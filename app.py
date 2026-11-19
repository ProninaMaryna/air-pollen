"""Streamlit PoC: air quality + pollen monitor for Ukrainian cities.

    streamlit run app.py
"""
from __future__ import annotations

import os

import folium
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv
from streamlit_folium import st_folium

from airpollen import analysis as an
from airpollen.client import AQ_MAP_TYPES, POLLEN_MAP_TYPES, ApiError, EnvClient

load_dotenv()
st.set_page_config(page_title="Повітря і пилок", page_icon="🌿", layout="wide")

DAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Нд"]


def day_label(ts) -> str:
    return f"{DAYS[ts.weekday()]} {ts:%d.%m}"


CITIES = ["Київ", "Львів", "Харків", "Одеса", "Дніпро", "Ужгород"]


@st.cache_resource
def get_client() -> EnvClient:
    return EnvClient(os.getenv("GOOGLE_MAPS_API_KEY"), language="uk")


client = get_client()


@st.cache_data(ttl=900, show_spinner=False)
def load_city(name: str) -> dict:
    g = client.geocode(name + ", Україна")
    lat, lng = g["lat"], g["lng"]
    return {
        "geo": g,
        "current": client.current(lat, lng),
        "history": client.history(lat, lng, hours=48),
        "forecast": client.forecast(lat, lng, hours=72),
        "pollen": client.pollen(lat, lng, days=5),
    }


# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.header("Налаштування")
    preset = st.radio("Місто", CITIES, horizontal=True)
    custom = st.text_input("…або будь-яка адреса", placeholder="напр. Чернівці")
    city = custom.strip() or preset
    persona = st.selectbox("Для кого рекомендації", list(an.PERSONAS), format_func=an.PERSONAS.get)
    st.divider()
    if client.replay:
        st.warning("Немає GOOGLE_MAPS_API_KEY: показую записані відповіді API.")
    else:
        st.success("Google Maps Platform API: підключено")
    st.caption(f"HTTP-запитів до API за сесію: {client.calls}")

st.title("🌿 Повітря і пилок")
st.caption("Google Maps Platform: Air Quality API · Pollen API · Geocoding API")

try:
    with st.spinner(f"Запитую API для «{city}»…"):
        data = load_city(city)
except ApiError as e:
    st.error(str(e))
    st.stop()

geo, cur = data["geo"], data["current"]
s = an.current_summary(cur)
hourly = an.hourly_df(data["history"], data["forecast"])
pol = an.pollen_df(data["pollen"])
pol_types = pol[pol["group"] == "type"]
today_pollen = pol_types[pol_types["date"] == pol_types["date"].min()] if not pol_types.empty else pol_types

st.subheader(geo["name"])

# ------------------------------------------------------------------ KPI row
c1, c2, c3, c4 = st.columns(4)
c1.markdown(
    f"<div style='background:{s['uaqi_color']};padding:14px;border-radius:10px;color:#111'>"
    f"<div style='font-size:13px'>Universal AQI (0 - погано, 100 - чудово)</div>"
    f"<div style='font-size:40px;font-weight:700'>{s['uaqi']}</div>"
    f"<div>{s['uaqi_category'] or ''}</div></div>", unsafe_allow_html=True)
if s["local_aqi"]:  # numeric local index
    c2.metric(s["local_name"] or "Локальний індекс", s["local_aqi"], s["local_category"] or "", delta_color="off")
else:  # some indexes (e.g. EAQI) come back as a category only; long text would be cut in st.metric
    c2.markdown(
        f"<div style='font-size:14px'>{s['local_name'] or 'Локальний індекс'}</div>"
        f"<div style='font-size:24px;font-weight:600;line-height:1.25;margin-top:6px'>{s['local_category'] or '—'}</div>",
        unsafe_allow_html=True)
dom = cur.get("pollutants", [])
dom_name = next((p.get("displayName") for p in dom if p.get("code") == s["dominant"]), s["dominant"])
c3.metric("Домінуючий забруднювач", dom_name or "—")
if not today_pollen.empty:
    top = today_pollen.sort_values("value", ascending=False).iloc[0]
    c4.metric("Пилок сьогодні (макс.)", f"{an.POLLEN_LABELS[int(top['value'])]}", f"{top['Тип']}",
              delta_color="off")
else:
    c4.metric("Пилок сьогодні", "немає даних")

advice = s["health"].get(persona) or s["health"].get("generalPopulation")
if advice:
    st.info(f"**{an.PERSONAS[persona]}:** {advice}")

tab_now, tab_trend, tab_pollen, tab_map, tab_cmp = st.tabs(
    ["Забруднювачі", "Динаміка й прогноз", "Пилок", "Карта", "Порівняння міст"])

# ------------------------------------------------------------- pollutants
with tab_now:
    pdf = an.pollutants_df(cur)
    if not pdf.empty:
        st.dataframe(pdf[["Забруднювач", "Повна назва", "Концентрація", "Одиниці"]],
                     hide_index=True, width="stretch")
        with st.expander("Джерела та вплив на здоров'я (від API)"):
            for _, r in pdf.iterrows():
                if r["Джерела"] or r["Вплив"]:
                    st.markdown(f"**{r['Повна назва']}**: {r['Джерела'] or ''} {r['Вплив'] or ''}")

# ------------------------------------------------------------------ trend
with tab_trend:
    if hourly.empty:
        st.write("Немає погодинних даних")
    else:
        fig = go.Figure()
        hi = 100
        for lo, name, col in an.UAQI_BANDS:  # bands are sorted from best to worst
            fig.add_hrect(y0=lo, y1=hi, fillcolor=col, opacity=0.08, line_width=0)
            hi = lo
        for kind, dash in (("Історія", "solid"), ("Прогноз", "dot")):
            d = hourly[hourly["kind"] == kind]
            fig.add_scatter(x=d["time"], y=d["uaqi"], name=kind, mode="lines",
                            line=dict(dash=dash, width=2.5, color="#2a6fdb"))
        fig.add_vline(x=pd.Timestamp.now(tz="UTC"), line_dash="dash", line_color="gray")
        fig.update_layout(height=380, yaxis=dict(range=[0, 100], title="Universal AQI"),
                          xaxis_title=None, margin=dict(l=10, r=10, t=40, b=10),
                          legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0))
        st.plotly_chart(fig, width="stretch")

        st.markdown("#### Коли краще бути надворі (наступні 72 год)")
        wins = an.best_windows(hourly, pol)
        if wins.empty:
            st.write("Немає денних вікон у прогнозі.")
        for _, w in wins.iterrows():
            band, _ = an.uaqi_band(w["uaqi"])
            st.markdown(f"- **{day_label(w['start'])}, {w['start']:%H:%M}–{w['end']:%H:%M}**: AQI ≈ {w['uaqi']} ({band}), "
                        f"пилок: {an.POLLEN_LABELS[int(w['pollen'])].lower()}")

# ----------------------------------------------------------------- pollen
with tab_pollen:
    if pol.empty:
        st.write("Pollen API не повернув даних для цієї точки.")
    else:
        hm = pol.pivot_table(index="Тип", columns=pol["date"].map(day_label), values="value",
                             aggfunc="max", sort=False).fillna(0)
        fig = px.imshow(hm, color_continuous_scale=["#e8f5e9", "#ffeb3b", "#ff9800", "#d32f2f"],
                        zmin=0, zmax=5, text_auto=True, aspect="auto")
        fig.update_layout(height=max(280, 60 + 40 * len(hm)), margin=dict(l=10, r=10, t=10, b=10), xaxis_title=None,
                          coloraxis_colorbar=dict(title="UPI"))
        st.plotly_chart(fig, width="stretch")
        st.caption("Universal Pollen Index: 0 немає · 1 дуже низький · 2 низький · 3 помірний · 4 високий · 5 дуже високий")
        for _, r in today_pollen.iterrows():
            if r["advice"]:
                st.markdown(f"**{r['Тип']}**: {r['advice']}")

# -------------------------------------------------------------------- map
with tab_map:
    layer = st.selectbox("Шар теплової карти", AQ_MAP_TYPES + POLLEN_MAP_TYPES)
    m = folium.Map(location=[geo["lat"], geo["lng"]], zoom_start=7, tiles="OpenStreetMap")
    kind = "pollen" if layer in POLLEN_MAP_TYPES else "aq"
    url = client.tile_url(kind, layer)
    if url:
        folium.TileLayer(tiles=url, attr="Google Air Quality / Pollen API", name=layer,
                         overlay=True, opacity=0.6).add_to(m)
    else:
        st.caption("Теплові тайли потребують API-ключа.")
    folium.Marker([geo["lat"], geo["lng"]], tooltip=f"{geo['name']}: AQI {s['uaqi']}").add_to(m)
    st_folium(m, height=480, use_container_width=True, returned_objects=[])

# ---------------------------------------------------------------- compare
with tab_cmp:
    rows = []
    for c in CITIES:
        try:
            d = load_city(c)
        except ApiError:
            continue
        ss = an.current_summary(d["current"])
        pp = an.pollen_df(d["pollen"])
        pt = pp[(pp["group"] == "type")]
        pmax = int(pt[pt["date"] == pt["date"].min()]["value"].max()) if not pt.empty else 0
        rows.append({"Місто": c, "UAQI": ss["uaqi"], "Категорія": ss["uaqi_category"],
                     "Пилок (макс. UPI)": pmax, "color": ss["uaqi_color"]})
    if rows:
        cdf = pd.DataFrame(rows).sort_values("UAQI", ascending=False)
        fig = px.bar(cdf, x="Місто", y="UAQI", text="UAQI", color="Місто",
                     color_discrete_sequence=list(cdf["color"]))
        fig.update_layout(height=340, showlegend=False, yaxis_range=[0, 100],
                          margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(fig, width="stretch")
        st.dataframe(cdf.drop(columns="color"), hide_index=True, width="stretch")

st.caption(f"Дані: {s['time']} · регіон {s['region']} · запитів до API за сесію: {client.calls}")
