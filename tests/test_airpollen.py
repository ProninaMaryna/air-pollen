import json
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from airpollen import analysis as an
from airpollen.client import ApiError, EnvClient


class FakeResp:
    def __init__(self, status, data):
        self.status_code, self._data, self.text = status, data, json.dumps(data)

    def json(self):
        return self._data


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def request(self, method, url, params=None, json=None, timeout=None):
        self.requests.append((method, url, params, json))
        return self.responses.pop(0)


def _hour(dt, aqi):
    return {"dateTime": dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "indexes": [{"code": "uaqi", "aqi": aqi, "category": "x"}]}


def test_geocode_and_key_passed(tmp_path):
    s = FakeSession([FakeResp(200, {"status": "OK", "results": [
        {"formatted_address": "Kyiv, Ukraine", "geometry": {"location": {"lat": 50.45, "lng": 30.52}}}]})])
    c = EnvClient("KEY", cache_dir=tmp_path, session=s)
    g = c.geocode("Київ")
    assert g == {"name": "Kyiv, Ukraine", "lat": 50.45, "lng": 30.52}
    assert s.requests[0][2]["key"] == "KEY" and c.calls == 1


def test_error_message_and_replay(tmp_path):
    s = FakeSession([FakeResp(403, {"error": {"message": "API not enabled"}})])
    c = EnvClient("KEY", cache_dir=tmp_path, session=s)
    with pytest.raises(ApiError, match="API not enabled"):
        c.current(1, 2)
    with pytest.raises(ApiError):
        EnvClient(None, cache_dir=tmp_path).current(1, 2)


def test_cache_then_replay(tmp_path):
    data = {"dateTime": "t", "indexes": [{"code": "uaqi", "aqi": 70}]}
    EnvClient("KEY", cache_dir=tmp_path, session=FakeSession([FakeResp(200, data)])).current(1, 2)
    assert EnvClient(None, cache_dir=tmp_path).current(1, 2) == data


def test_forecast_pagination(tmp_path):
    t0 = datetime(2026, 11, 18, tzinfo=timezone.utc)
    s = FakeSession([FakeResp(200, {"hourlyForecasts": [_hour(t0, 50)], "nextPageToken": "p2"}),
                     FakeResp(200, {"hourlyForecasts": [_hour(t0 + timedelta(hours=1), 60)]})])
    c = EnvClient("KEY", cache_dir=None, session=s)
    assert len(c.forecast(1, 2)) == 2
    assert s.requests[1][3]["pageToken"] == "p2"


def test_color_hex_both_formats():
    assert an.color_hex({"red": 1, "green": 0.5}) == "#ff8000"
    assert an.color_hex({"red": 255, "green": 128, "blue": 0, "alpha": 255}) == "#ff8000"
    assert an.color_hex(None) == "#999999"


def test_pollen_df_and_missing_index():
    pollen = {"dailyInfo": [{"date": {"year": 2026, "month": 11, "day": 18}, "pollenTypeInfo": [
        {"code": "GRASS", "displayName": "Трава", "inSeason": True,
         "indexInfo": {"value": 3, "category": "Moderate"}, "healthRecommendations": ["a"]},
        {"code": "TREE", "displayName": "Дерева", "inSeason": False}]}]}
    df = an.pollen_df(pollen)
    assert list(df["value"]) == [3, 0]
    assert df.iloc[1]["category"] == "Немає"


def test_best_windows_prefers_clean_daytime_air():
    base = datetime.now(timezone.utc).replace(hour=6, minute=0, second=0, microsecond=0) + timedelta(days=1)
    fc = [_hour(base + timedelta(hours=i), 80 if 8 <= i <= 9 else 30) for i in range(16)]
    hourly = an.hourly_df([], fc)
    w = an.best_windows(hourly, pd.DataFrame(columns=["date", "group", "value"]), hours=2, top=1)
    assert w.iloc[0]["uaqi"] == 80


def test_paged_cache_replay_keeps_pages(tmp_path):
    t0 = datetime(2026, 11, 18, tzinfo=timezone.utc)
    s = FakeSession([FakeResp(200, {"hoursInfo": [_hour(t0, 50)], "nextPageToken": "p2"}),
                     FakeResp(200, {"hoursInfo": [_hour(t0 + timedelta(hours=1), 60)]})])
    live = EnvClient("KEY", cache_dir=tmp_path, session=s).history(1, 2)
    replay = EnvClient(None, cache_dir=tmp_path).history(1, 2)
    assert [h["dateTime"] for h in replay] == [h["dateTime"] for h in live]
