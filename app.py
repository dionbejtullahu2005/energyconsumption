from __future__ import annotations
from pathlib import Path
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import pyarrow.parquet as pq
import streamlit as st


ROOT = Path(__file__).resolve().parent
PROCESSED = ROOT / "data" / "processed"
INTERIM = ROOT / "data" / "interim"

st.set_page_config(
    page_title="Enerco — Analiza e Konsumit",
    layout="wide",
)

st.markdown(
    """
    <style>
    [data-testid="stMetricValue"] {
        font-size: clamp(1.35rem, 2.1vw, 2rem);
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def company_sort_key(value: str) -> tuple[int, str]:
    try:
        return int(value.rsplit(" ", 1)[1]), value
    except (IndexError, ValueError):
        return 10**9, value


def fmt_number(value: float | int | None, digits: int = 2) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"{value:,.{digits}f}"


def fmt_pct(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"{value:.1%}"


@st.cache_data(show_spinner=False)
def load_small_tables() -> dict[str, pd.DataFrame]:
    files = {
        "company_metrics": PROCESSED / "company_profile_metrics.parquet",
        "meter_metrics": PROCESSED / "meter_profile_metrics.parquet",
        "quality": INTERIM / "meter_quality.parquet",
        "clusters": PROCESSED / "company_clusters.parquet",
        "cluster_centers": PROCESSED / "cluster_centers.parquet",
        "company_monthly": PROCESSED / "company_monthly_profiles.parquet",
        "meter_hourly": PROCESSED / "meter_hourly_profiles.parquet",
        "company_weather": PROCESSED / "company_weather_sensitivity.parquet",
        "holiday_summary": PROCESSED / "company_holiday_effect_summary.parquet",
        "heterogeneity": PROCESSED / "company_meter_heterogeneity.parquet",
    }
    missing = [str(path) for path in files.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Mungojnë output-et e pipeline-it: {missing}")
    return {name: pd.read_parquet(path) for name, path in files.items()}


@st.cache_data(show_spinner=False)
def load_company_hourly(company_id: str) -> pd.DataFrame:
    path = PROCESSED / "hourly_consumption_enriched.parquet"
    columns = [
        "company_id", "meter_id", "energy_flow", "interval_start", "date", "hour_1_24",
        "is_weekend", "is_peak_07_18", "tariff", "energy_kwh", "is_missing",
        "temperature_2m", "temperature_2m_mean", "hdd_18", "cdd_18",
        "is_holiday_or_day_off", "holiday_name",
    ]
    frame = pq.read_table(path, columns=columns, filters=[("company_id", "=", company_id)]).to_pandas()
    frame["interval_start"] = pd.to_datetime(frame["interval_start"])
    frame["date"] = pd.to_datetime(frame["date"])
    return frame


@st.cache_data(show_spinner=False)
def load_company_outliers(company_id: str) -> pd.DataFrame:
    path = PROCESSED / "company_hourly_outliers_enriched.parquet"
    frame = pq.read_table(path, filters=[("company_id", "=", company_id)]).to_pandas()
    frame["interval_start"] = pd.to_datetime(frame["interval_start"])
    frame["date"] = pd.to_datetime(frame["date"])
    return frame


@st.cache_data(show_spinner=False)
def load_company_daily(company_id: str) -> pd.DataFrame:
    path = PROCESSED / "company_daily_energy_enriched.parquet"
    frame = pq.read_table(path, filters=[("company_id", "=", company_id)]).to_pandas()
    frame["date"] = pd.to_datetime(frame["date"])
    return frame


try:
    tables = load_small_tables()
except FileNotFoundError as error:
    st.error(str(error))
    st.stop()

company_metrics = tables["company_metrics"]
companies = sorted(company_metrics["company_id"].unique().tolist(), key=company_sort_key)

st.title("Analiza e profileve të konsumit — Enerco")
st.caption(
    "Rezultate anonime për periudhën Qershor 2025 – Qershor 2026. "
    "Konsumi dhe injektimi paraqiten në kWh."
)

with st.sidebar:
    st.header("Filtrat")
    selected_company = st.selectbox("Kompania", companies, index=0)
    hourly_all = load_company_hourly(selected_company)
    min_date = hourly_all["date"].min().date()
    max_date = hourly_all["date"].max().date()
    selected_dates = st.date_input(
        "Periudha",
        value=(min_date, max_date),
        min_value=min_date,
        max_value=max_date,
    )
    if isinstance(selected_dates, tuple) and len(selected_dates) == 2:
        date_start, date_end = selected_dates
    else:
        date_start = date_end = selected_dates
    st.divider()
    st.caption("Temperatura e Prishtinës përdoret si proxy për të gjitha kompanitë.")

start_ts = pd.Timestamp(date_start)
end_ts = pd.Timestamp(date_end)
hourly = hourly_all.loc[hourly_all["date"].between(start_ts, end_ts)].copy()
company_row = company_metrics.loc[company_metrics["company_id"].eq(selected_company)].iloc[0]
cluster_rows = tables["clusters"].loc[tables["clusters"]["company_id"].eq(selected_company)]
heterogeneity_rows = tables["heterogeneity"].loc[
    tables["heterogeneity"]["company_id"].eq(selected_company)
]

if cluster_rows.empty:
    cluster_id = "Pa grup"
    cluster_description = "Mungojnë një ose më shumë metrika për grupim"
else:
    cluster_id = cluster_rows.iloc[0]["cluster_id"].replace("Klaster", "Grupi")
    cluster_description = cluster_rows.iloc[0]["cluster_description"]

tabs = st.tabs(
    [
        "Përmbledhje",
        "Profili i konsumit",
        "Moti dhe festat",
        "Vlerat e pazakonta",
        "Njehsorët dhe prosumerët",
        "Grupet",
        "Cilësia e të dhënave",
    ]
)

with tabs[0]:
    st.subheader(f"Karta analitike — {selected_company}")
    cols = st.columns(5)
    cols[0].metric("Energjia totale", f"{fmt_number(company_row['energy_total_kwh'] / 1000)} MWh")
    cols[1].metric("Raporti pik/jo-pik", fmt_number(company_row["peak_offpeak_ratio"]))
    cols[2].metric("Java/fundjava", fmt_number(company_row["weekday_weekend_ratio"]))
    cols[3].metric("Load factor", fmt_pct(company_row["load_factor"]))
    cols[4].metric("Mbulimi", fmt_pct(company_row["coverage_share"]))

    left, right = st.columns([1.2, 1])
    with left:
        st.markdown("#### Interpretimi")
        st.write(f"**{cluster_id}:** {cluster_description}")
        st.write(f"**Sezonaliteti:** {company_row['seasonality_label']}")
        st.write(f"**Trendi mujor:** {fmt_pct(company_row['monthly_trend_pct'])}")
        if not bool(company_row["seasonality_reliable"]):
            st.warning("Sezonaliteti ka mbulim më të ulët se pragu i besueshmërisë.")
    with right:
        st.markdown("#### Njehsorët")
        st.metric("Numri i njehsorëve", int(company_row["meter_count"]))
        if not heterogeneity_rows.empty:
            st.write(heterogeneity_rows.iloc[0]["meter_profile_similarity"])

    company_series = (
        hourly.loc[hourly["energy_flow"].eq("consumption_import")]
        .groupby("interval_start", as_index=False)["energy_kwh"]
        .sum(min_count=1)
    )
    fig = px.line(
        company_series,
        x="interval_start",
        y="energy_kwh",
        labels={"interval_start": "Data dhe ora", "energy_kwh": "Konsum (kWh)"},
        title="Konsumi orar në periudhën e zgjedhur",
    )
    st.plotly_chart(fig, use_container_width=True)

with tabs[1]:
    consumption = hourly.loc[hourly["energy_flow"].eq("consumption_import")].copy()
    hourly_company = consumption.groupby(
        ["interval_start", "hour_1_24", "is_weekend"], as_index=False
    )["energy_kwh"].sum(min_count=1)
    profile = hourly_company.groupby(["hour_1_24", "is_weekend"], as_index=False)["energy_kwh"].mean()
    profile["Lloji i ditës"] = profile["is_weekend"].map(
        {False: "Ditë pune", True: "Fundjavë"}
    )
    fig = px.line(
        profile,
        x="hour_1_24",
        y="energy_kwh",
        color="Lloji i ditës",
        markers=True,
        labels={"hour_1_24": "Ora 1–24", "energy_kwh": "Konsum mesatar (kWh)"},
        title="Profili mesatar 24-orësh",
    )
    fig.update_xaxes(dtick=1)
    st.plotly_chart(fig, use_container_width=True)

    monthly = tables["company_monthly"].loc[
        tables["company_monthly"]["company_id"].eq(selected_company)
    ].copy()
    fig = px.bar(
        monthly,
        x="month_start",
        y="month_total_kwh",
        labels={"month_start": "Muaji", "month_total_kwh": "Konsum (kWh)"},
        title="Konsumi mujor — korrik 2025 deri qershor 2026",
    )
    st.plotly_chart(fig, use_container_width=True)

with tabs[2]:
    daily = load_company_daily(selected_company)
    daily = daily.loc[daily["date"].between(start_ts, end_ts)]
    sensitivity = tables["company_weather"].loc[
        tables["company_weather"]["company_id"].eq(selected_company)
    ].iloc[0]
    holiday = tables["holiday_summary"].loc[
        tables["holiday_summary"]["company_id"].eq(selected_company)
    ]
    cols = st.columns(4)
    cols[0].metric("Temperaturë–konsum", fmt_number(sensitivity["temperature_correlation"]))
    cols[1].metric("HDD–konsum", fmt_number(sensitivity["hdd_correlation"]))
    cols[2].metric("CDD–konsum", fmt_number(sensitivity["cdd_correlation"]))
    cols[3].metric(
        "Efekti mesatar i festave",
        fmt_pct(holiday.iloc[0]["mean_holiday_effect_pct"]) if not holiday.empty else "—",
    )
    st.info(str(sensitivity["weather_sensitivity_label"]))
    fig = px.scatter(
        daily,
        x="temperature_2m_mean",
        y="energy_total_kwh",
        color="is_holiday_or_day_off",
        hover_data=["date", "holiday_name"],
        labels={
            "temperature_2m_mean": "Temperatura mesatare ditore (°C)",
            "energy_total_kwh": "Konsum ditor (kWh)",
            "is_holiday_or_day_off": "Festë/pushim",
        },
        title="Konsumi ditor kundrejt temperaturës së Prishtinës",
    )
    st.plotly_chart(fig, use_container_width=True)
    holiday_days = daily.loc[daily["is_holiday_or_day_off"]].copy()
    st.markdown("#### Ditët e festave në periudhën e zgjedhur")
    st.dataframe(
        holiday_days[["date", "holiday_name", "energy_total_kwh", "temperature_2m_mean"]],
        use_container_width=True,
        hide_index=True,
    )

with tabs[3]:
    outliers = load_company_outliers(selected_company)
    outliers = outliers.loc[outliers["date"].between(start_ts, end_ts)]
    cols = st.columns(4)
    cols[0].metric("Gjithsej", len(outliers))
    cols[1].metric("Të larta", int(outliers["outlier_direction"].eq("I lartë").sum()))
    cols[2].metric("Të ulëta", int(outliers["outlier_direction"].eq("I ulët").sum()))
    cols[3].metric("Në festa/pushim", int(outliers["is_holiday_or_day_off"].fillna(False).sum()))
    if outliers.empty:
        st.success("Nuk ka vlera të pazakonta në periudhën e zgjedhur.")
    else:
        outliers = outliers.assign(absolute_z=outliers["z_score"].abs())
        fig = px.scatter(
            outliers,
            x="interval_start",
            y="energy_kwh",
            color="external_context",
            size="absolute_z",
            hover_data=["z_score", "reason", "recommendation", "holiday_name", "temperature_2m"],
            labels={"interval_start": "Data dhe ora", "energy_kwh": "Konsum (kWh)"},
            title="Vlerat e pazakonta dhe konteksti i jashtëm",
        )
        st.plotly_chart(fig, use_container_width=True)
        visible_columns = [
            "interval_start", "energy_kwh", "z_score", "outlier_direction", "external_context",
            "holiday_name", "temperature_2m", "recommendation"
        ]
        st.dataframe(outliers[visible_columns], use_container_width=True, hide_index=True)

with tabs[4]:
    company_meter_metrics = tables["meter_metrics"].loc[
        tables["meter_metrics"]["company_id"].eq(selected_company)
    ].copy()
    meter_ids = sorted(company_meter_metrics["meter_id"].unique().tolist())
    selected_meter = st.selectbox("Njehsori", meter_ids, key="meter_select")
    available_flows = company_meter_metrics.loc[
        company_meter_metrics["meter_id"].eq(selected_meter), "energy_flow"
    ].tolist()
    flow_labels = {
        "consumption_import": "Konsum / import (A+)",
        "injection_export": "Injektim / eksport (A−)",
    }
    selected_flow = st.selectbox(
        "Drejtimi i energjisë",
        available_flows,
        format_func=lambda value: flow_labels.get(value, value),
    )
    meter_row = company_meter_metrics.loc[
        company_meter_metrics["meter_id"].eq(selected_meter)
        & company_meter_metrics["energy_flow"].eq(selected_flow)
    ].iloc[0]
    cols = st.columns(5)
    cols[0].metric("Energjia", f"{fmt_number(meter_row['energy_total_kwh'] / 1000)} MWh")
    cols[1].metric("Pik/jo-pik", fmt_number(meter_row["peak_offpeak_ratio"]))
    cols[2].metric("Java/fundjava", fmt_number(meter_row["weekday_weekend_ratio"]))
    cols[3].metric("CV", fmt_number(meter_row["coefficient_of_variation"]))
    cols[4].metric("Load factor", fmt_pct(meter_row["load_factor"]))
    meter_profile = tables["meter_hourly"].loc[
        tables["meter_hourly"]["meter_id"].eq(selected_meter)
        & tables["meter_hourly"]["energy_flow"].eq(selected_flow)
    ]
    fig = px.line(
        meter_profile,
        x="hour_1_24",
        y="mean_kwh",
        markers=True,
        labels={"hour_1_24": "Ora 1–24", "mean_kwh": "Energji mesatare (kWh)"},
        title=f"Profili 24-orësh — {selected_meter}",
    )
    fig.update_xaxes(dtick=1)
    st.plotly_chart(fig, use_container_width=True)

    prosumer = hourly.loc[hourly["meter_id"].eq(selected_meter)].copy()
    if prosumer["energy_flow"].nunique() == 2:
        monthly_prosumer = (
            prosumer.assign(month=prosumer["interval_start"].dt.to_period("M").dt.to_timestamp())
            .groupby(["month", "energy_flow"], as_index=False)["energy_kwh"]
            .sum(min_count=1)
        )
        monthly_prosumer["Drejtimi"] = monthly_prosumer["energy_flow"].map(flow_labels)
        fig = px.bar(
            monthly_prosumer,
            x="month",
            y="energy_kwh",
            color="Drejtimi",
            barmode="group",
            labels={"month": "Muaji", "energy_kwh": "Energji (kWh)"},
            title="Prosumer — konsum dhe injektim mujor",
        )
        st.plotly_chart(fig, use_container_width=True)

with tabs[5]:
    clusters = tables["clusters"].copy()
    clusters["Grupi"] = clusters["cluster_id"].str.replace("Klaster", "Grupi", regex=False)
    fig = px.scatter(
        clusters,
        x="pca_1",
        y="pca_2",
        color="Grupi",
        hover_name="company_id",
        hover_data=[
            "cluster_description", "peak_offpeak_ratio", "weekday_weekend_ratio",
            "coefficient_of_variation", "load_factor"
        ],
        labels={"pca_1": "PCA 1", "pca_2": "PCA 2"},
        title="Grupet e kompanive — projeksion PCA",
    )
    selected = clusters.loc[clusters["company_id"].eq(selected_company)]
    if not selected.empty:
        fig.add_trace(
            go.Scatter(
                x=selected["pca_1"],
                y=selected["pca_2"],
                mode="markers",
                marker={"size": 18, "symbol": "circle-open", "color": "black", "line": {"width": 3}},
                name=selected_company,
            )
        )
    st.plotly_chart(fig, use_container_width=True)
    centers = tables["cluster_centers"].copy()
    centers["cluster_id"] = centers["cluster_id"].str.replace("Klaster", "Grupi", regex=False)
    st.dataframe(
        centers[["cluster_id", "cluster_description", "company_count"]],
        use_container_width=True,
        hide_index=True,
    )

with tabs[6]:
    quality = tables["quality"].loc[tables["quality"]["company_id"].eq(selected_company)].copy()
    status_counts = quality["quality_status"].value_counts()
    cols = st.columns(4)
    cols[0].metric("Seri energjie", len(quality))
    cols[1].metric("Të pastra", int(status_counts.get("I pastër", 0)))
    cols[2].metric("Për shqyrtim", int(status_counts.get("Për shqyrtim", 0)))
    cols[3].metric("Të papërdorshme", int(status_counts.get("I papërdorshëm", 0)))
    visible = [
        "meter_id", "energy_flow", "missing_share", "internal_missing_share_active_span",
        "zero_run_events_48h_plus", "extreme_values_50x_mean", "quality_status",
        "active_period_quality_status",
    ]
    st.dataframe(quality[visible], use_container_width=True, hide_index=True)
    st.caption(
        "Statusi për gjithë periudhën dhe statusi për periudhën aktive paraqiten veçmas. "
        "‘Për shqyrtim’ nuk do të thotë automatikisht defekt."
    )
