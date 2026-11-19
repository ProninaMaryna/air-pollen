"""Turn raw API JSON into tables and a simple 'when is it safe to go outside' advisor."""
from __future__ import annotations

import pandas as pd

UAQI_BANDS = [  # Universal AQI: 100 = best air, 0 = worst
    (80, "Відмінна", "#009e3a"),
    (60, "Добра", "#84cf33"),
    (40, "Помірна", "#ffd400"),
    (20, "Погана", "#ff8c00"),
    (1, "Дуже погана", "#ff0000"),
    (0, "Надзвичайно погана", "#800000"),
]

POLLEN_LABELS = {0: "Немає", 1: "Дуже низький", 2: "Низький", 3: "Помірний", 4: "Високий", 5: "Дуже високий"}

PERSONAS = {
    "generalPopulation": "Усі",
    "children": "Діти",
    "elderly": "Літні люди",
    "athletes": "Спортсмени",
    "pregnantWomen": "Вагітні",
    "lungDiseasePopulation": "Хвороби легень / астма",
    "heartDiseasePopulation": "Серцево-судинні хвороби",
}


def color_hex(c: dict | None) -> str:
    """API colours come either as 0..1 floats or 0..255 ints; missing channel = 0."""
    if not c:
        return "#999999"
    vals = [c.get(k, 0) for k in ("red", "green", "blue")]
    if all(v <= 1 for v in vals):
        vals = [round(v * 255) for v in vals]
    return "#%02x%02x%02x" % tuple(int(v) for v in vals)


def uaqi_band(aqi: float) -> tuple[str, str]:
    for lo, name, col in UAQI_BANDS:
        if aqi >= lo:
            return name, col
    return UAQI_BANDS[-1][1], UAQI_BANDS[-1][2]


def pick_index(indexes: list, code: str = "uaqi") -> dict | None:
    for ix in indexes or []:
        if ix.get("code") == code:
            return ix
    return None


def local_index(indexes: list) -> dict | None:
    for ix in indexes or []:
        if ix.get("code") != "uaqi":
            return ix
    return None


def current_summary(cur: dict) -> dict:
    u = pick_index(cur.get("indexes")) or {}
    loc = local_index(cur.get("indexes")) or {}
    return {
        "time": cur.get("dateTime"),
        "region": cur.get("regionCode"),
        "uaqi": u.get("aqi"),
        "uaqi_category": u.get("category"),
        "uaqi_color": color_hex(u.get("color")),
        "dominant": u.get("dominantPollutant"),
        "local_name": loc.get("displayName"),
        "local_aqi": loc.get("aqiDisplay"),
        "local_category": loc.get("category"),
        "health": cur.get("healthRecommendations", {}),
    }


def pollutants_df(cur: dict) -> pd.DataFrame:
    rows = []
    for p in cur.get("pollutants", []):
        conc = p.get("concentration") or {}
        rows.append({
            "code": p.get("code"),
            "Забруднювач": p.get("displayName"),
            "Повна назва": p.get("fullName"),
            "Концентрація": conc.get("value"),
            "Одиниці": {"PARTS_PER_BILLION": "ppb", "MICROGRAMS_PER_CUBIC_METER": "мкг/м³"}
            .get(conc.get("units"), conc.get("units")),
            "Джерела": (p.get("additionalInfo") or {}).get("sources"),
            "Вплив": (p.get("additionalInfo") or {}).get("effects"),
        })
    return pd.DataFrame(rows)


def hourly_df(history: list, forecast: list) -> pd.DataFrame:
    rows = []
    for kind, items in (("Історія", history), ("Прогноз", forecast)):
        for h in items or []:
            u = pick_index(h.get("indexes"))
            if not u or u.get("aqi") is None:
                continue
            rows.append({"time": pd.to_datetime(h["dateTime"]), "uaqi": u["aqi"],
                         "category": u.get("category"), "kind": kind})
    df = pd.DataFrame(rows, columns=["time", "uaqi", "category", "kind"])
    return df.sort_values("time").drop_duplicates("time", keep="last").reset_index(drop=True)


def pollen_df(pollen: dict) -> pd.DataFrame:
    rows = []
    for day in pollen.get("dailyInfo", []):
        d = day["date"]
        date = pd.Timestamp(year=d["year"], month=d["month"], day=d["day"])
        for t in day.get("pollenTypeInfo", []):
            ii = t.get("indexInfo") or {}
            rows.append({"date": date, "type": t.get("code"), "Тип": t.get("displayName"),
                         "group": "type", "value": ii.get("value", 0),
                         "category": ii.get("category") or POLLEN_LABELS[ii.get("value", 0)],
                         "inSeason": t.get("inSeason", False),
                         "advice": "; ".join(t.get("healthRecommendations") or [])})
        for p in day.get("plantInfo", []):
            ii = p.get("indexInfo")
            if not ii:
                continue
            rows.append({"date": date, "type": p.get("code"), "Тип": p.get("displayName"),
                         "group": "plant", "value": ii.get("value", 0),
                         "category": ii.get("category"), "inSeason": p.get("inSeason", False),
                         "advice": ""})
    return pd.DataFrame(rows, columns=["date", "type", "Тип", "group", "value", "category", "inSeason", "advice"])


def best_windows(hourly: pd.DataFrame, pollen: pd.DataFrame, hours: int = 2, top: int = 3,
                 day_from: int = 7, day_to: int = 22) -> pd.DataFrame:
    """Best `hours`-long windows for outdoor activity in the forecast.

    Score = mean UAQI of the window (higher is better) - 8 * max pollen UPI of that day.
    Only daytime hours (local time of the timestamps given) are considered.
    """
    f = hourly[hourly["kind"] == "Прогноз"].copy()
    if f.empty:
        return pd.DataFrame(columns=["start", "end", "uaqi", "pollen", "score"])
    f = f.set_index("time").sort_index()
    roll = f["uaqi"].rolling(hours).mean().dropna()
    pmax = {}
    if not pollen.empty:
        types = pollen[pollen["group"] == "type"]
        pmax = types.groupby(types["date"].dt.date)["value"].max().to_dict()
    rows = []
    for end, aqi in roll.items():
        start = end - pd.Timedelta(hours=hours - 1)
        local = start.tz_convert("Europe/Kyiv") if start.tzinfo else start
        if not (day_from <= local.hour <= day_to - hours):
            continue
        p = pmax.get(local.date(), 0)
        rows.append({"start": local, "end": local + pd.Timedelta(hours=hours),
                     "uaqi": round(aqi, 1), "pollen": p, "score": aqi - 8 * p})
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.sort_values("score", ascending=False)
    picked, used = [], []
    for _, r in df.iterrows():  # non-overlapping windows
        if all(abs((r["start"] - u).total_seconds()) >= hours * 3600 for u in used):
            picked.append(r)
            used.append(r["start"])
        if len(picked) == top:
            break
    return pd.DataFrame(picked).reset_index(drop=True)
