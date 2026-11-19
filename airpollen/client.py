"""Thin client for Google Maps Platform environment APIs.

* Geocoding API      - city name -> coordinates
* Air Quality API    - current conditions, hourly history (<=30 days), forecast (<=96 h), heatmap tiles
* Pollen API         - 5-day pollen forecast (grass / tree / weed + plants), heatmap tiles

Every JSON response can be cached on disk (``cache_dir``) so the demo can be
replayed without spending quota; replay mode is used only when no API key is set.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

AQ_BASE = "https://airquality.googleapis.com/v1"
POLLEN_BASE = "https://pollen.googleapis.com/v1"
GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"

AQ_MAP_TYPES = ["UAQI_RED_GREEN", "UAQI_INDIGO_PERSIAN", "PM25_INDIGO_PERSIAN", "US_AQI"]
POLLEN_MAP_TYPES = ["TREE_UPI", "GRASS_UPI", "WEED_UPI"]

EXTRA = [
    "HEALTH_RECOMMENDATIONS",
    "DOMINANT_POLLUTANT_CONCENTRATION",
    "POLLUTANT_CONCENTRATION",
    "LOCAL_AQI",
    "POLLUTANT_ADDITIONAL_INFO",
]


class ApiError(RuntimeError):
    pass


class EnvClient:
    def __init__(self, api_key: str | None = None, language: str = "uk",
                 cache_dir: str | os.PathLike | None = "data/cache",
                 session: requests.Session | None = None, timeout: float = 20):
        self.api_key = api_key or os.getenv("GOOGLE_MAPS_API_KEY") or None
        self.language = language
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.session = session or requests.Session()
        self.timeout = timeout
        self.calls = 0  # real HTTP calls made (for the "API usage" counter in the UI)

    # ------------------------------------------------------------------ infra
    @property
    def replay(self) -> bool:
        """No key -> serve previously recorded responses from the cache."""
        return not self.api_key

    def _cache_path(self, name: str, payload: dict) -> Path | None:
        if not self.cache_dir:
            return None
        h = hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]
        return self.cache_dir / f"{name}_{h}.json"

    def _request(self, name: str, method: str, url: str, *, params=None, body=None) -> dict:
        key_payload = {"url": url, "params": params, "body": _strip_times(body)}
        path = self._cache_path(name, key_payload)
        if self.replay:
            if path and path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
            raise ApiError("No GOOGLE_MAPS_API_KEY and no recorded response for this request.")

        params = dict(params or {}, key=self.api_key)
        r = self.session.request(method, url, params=params, json=body, timeout=self.timeout)
        self.calls += 1
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code != 200:
            msg = (data.get("error") or {}).get("message") or r.text[:300]
            raise ApiError(f"{name}: HTTP {r.status_code}: {msg}")
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        return data

    # -------------------------------------------------------------- Geocoding
    def geocode(self, address: str) -> dict:
        data = self._request("geocode", "GET", GEOCODE_URL,
                             params={"address": address, "language": self.language})
        status = data.get("status")
        if status != "OK" or not data.get("results"):
            raise ApiError(f"Geocoding: {status or 'no results'} for '{address}'")
        res = data["results"][0]
        loc = res["geometry"]["location"]
        return {"name": res.get("formatted_address", address), "lat": loc["lat"], "lng": loc["lng"]}

    # ------------------------------------------------------------ Air Quality
    def current(self, lat: float, lng: float) -> dict:
        body = {"location": {"latitude": lat, "longitude": lng},
                "universalAqi": True, "extraComputations": EXTRA,
                "languageCode": self.language}
        return self._request("aq_current", "POST", f"{AQ_BASE}/currentConditions:lookup", body=body)

    def _paged(self, name: str, url: str, body: dict, list_key: str, max_pages: int = 10) -> list:
        items, token = [], None
        for page in range(max_pages):
            b = dict(body, **({"pageToken": token} if token else {}))
            # page number goes into the cache name: the token itself is not part of the key
            data = self._request(f"{name}_p{page}", "POST", url, body=b)
            items.extend(data.get(list_key, []))
            token = data.get("nextPageToken")
            if not token:
                break
        return items

    def history(self, lat: float, lng: float, hours: int = 48) -> list:
        body = {"location": {"latitude": lat, "longitude": lng}, "hours": hours,
                "pageSize": 72, "universalAqi": True, "extraComputations": ["LOCAL_AQI"],
                "languageCode": self.language}
        return self._paged("aq_history", f"{AQ_BASE}/history:lookup", body, "hoursInfo")

    def forecast(self, lat: float, lng: float, hours: int = 72) -> list:
        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        start, end = now + timedelta(hours=1), now + timedelta(hours=min(hours, 96))
        body = {"location": {"latitude": lat, "longitude": lng},
                "period": {"startTime": _iso(start), "endTime": _iso(end)},
                "pageSize": 96, "universalAqi": True, "extraComputations": ["LOCAL_AQI"],
                "languageCode": self.language}
        return self._paged("aq_forecast", f"{AQ_BASE}/forecast:lookup", body, "hourlyForecasts")

    # ----------------------------------------------------------------- Pollen
    def pollen(self, lat: float, lng: float, days: int = 5) -> dict:
        params = {"location.latitude": lat, "location.longitude": lng, "days": days,
                  "languageCode": self.language, "plantsDescription": "false"}
        return self._request("pollen", "GET", f"{POLLEN_BASE}/forecast:lookup", params=params)

    # ------------------------------------------------------------------ Tiles
    def tile_url(self, kind: str, map_type: str) -> str | None:
        """XYZ tile template for Leaflet/folium (needs a real key)."""
        if not self.api_key:
            return None
        base = AQ_BASE if kind == "aq" else POLLEN_BASE
        return f"{base}/mapTypes/{map_type}/heatmapTiles/{{z}}/{{x}}/{{y}}?key={self.api_key}"


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _strip_times(body):
    """Cache key must not depend on 'now' so forecasts can be replayed."""
    if not body:
        return body
    b = dict(body)
    b.pop("period", None)
    b.pop("pageToken", None)
    return b
