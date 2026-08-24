from __future__ import annotations
from pathlib import Path
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import pyarrow.parquet as pq
import streamlit as st

from enerco_analysis.clustering import ensure_cluster_energy_totals
from enerco_analysis.exports import build_cluster_company_export
from enerco_analysis.ui_metrics import (
    calculate_holiday_effect,
    calculate_period_metrics,
    calculate_weather_metrics,
)


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
        "company_weather_proxy": PROCESSED / "company_weather_sensitivity_prishtina_proxy.parquet",
        "district_membership": PROCESSED / "company_district_membership.parquet",
        "holiday_summary": PROCESSED / "company_holiday_effect_summary.parquet",
        "heterogeneity": PROCESSED / "company_meter_heterogeneity.parquet",
    }
    missing = [str(path) for path in files.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Mungojnë output-et e pipeline-it: {missing}")
    tables = {name: pd.read_parquet(path) for name, path in files.items()}
    # Kompatibilitet me output-et e klasterizimit të krijuara para kolonave të energjisë.
    tables["cluster_centers"] = ensure_cluster_energy_totals(
        tables["cluster_centers"], tables["clusters"]
    )
    return tables


@st.cache_data(show_spinner=False)
def load_company_hourly(company_id: str) -> pd.DataFrame:
    path = PROCESSED / "hourly_consumption_enriched.parquet"
    columns = [
        "company_id", "meter_id", "meter_prefix", "district", "energy_flow", "interval_start", "date", "hour_1_24",
        "is_weekend", "is_peak_07_18", "tariff", "energy_kwh", "is_missing",
        "temperature_2m", "temperature_2m_mean", "hdd_18", "cdd_18",
        "is_holiday_or_day_off", "holiday_name",
    ]
    frame = pq.read_table(path, columns=columns, filters=[("company_id", "=", company_id)]).to_pandas()
    frame["interval_start"] = pd.to_datetime(frame["interval_start"])
    frame["date"] = pd.to_datetime(frame["date"])
    return frame


@st.cache_data(show_spinner=False)
def load_company_outliers(company_id: str, weather_by_district: bool) -> pd.DataFrame:
    filename = (
        "company_hourly_outliers_enriched.parquet"
        if weather_by_district else "company_hourly_outliers_prishtina_proxy.parquet"
    )
    path = PROCESSED / filename
    frame = pq.read_table(path, filters=[("company_id", "=", company_id)]).to_pandas()
    frame["interval_start"] = pd.to_datetime(frame["interval_start"])
    frame["date"] = pd.to_datetime(frame["date"])
    return frame


@st.cache_data(show_spinner=False)
def load_company_daily(company_id: str, weather_by_district: bool) -> pd.DataFrame:
    filename = (
        "company_daily_energy_enriched.parquet"
        if weather_by_district else "company_daily_energy_prishtina_proxy.parquet"
    )
    path = PROCESSED / filename
    frame = pq.read_table(path, filters=[("company_id", "=", company_id)]).to_pandas()
    frame["date"] = pd.to_datetime(frame["date"])
    return frame


@st.cache_data(show_spinner=False)
def load_all_company_daily(weather_by_district: bool) -> pd.DataFrame:
    filename = (
        "company_daily_energy_enriched.parquet"
        if weather_by_district else "company_daily_energy_prishtina_proxy.parquet"
    )
    frame = pd.read_parquet(PROCESSED / filename)
    frame["date"] = pd.to_datetime(frame["date"])
    return frame


try:
    tables = load_small_tables()
except FileNotFoundError as error:
    st.error(str(error))
    st.stop()

company_metrics = tables["company_metrics"]
membership = tables["district_membership"]
districts = sorted(membership["district"].dropna().unique().tolist())

st.title("Analiza e profileve të konsumit — Enerco")
st.caption(
    "Rezultate anonime për periudhën Qershor 2025 – Qershor 2026. "
    "Konsumi dhe injektimi paraqiten në kWh."
)

with st.sidebar:
    st.header("Filtrat")
    weather_by_district = st.toggle(
        "Temperatura sipas distriktit",
        value=False,
        help="OFF: Prishtina përdoret si proxy për të gjithë njehsorët. ON: temperatura lidhet sipas prefiksit të njehsorit.",
    )
    selected_district = st.selectbox("Distrikti", ["Të gjitha"] + districts)
    if selected_district == "Të gjitha":
        companies = company_metrics["company_id"].unique().tolist()
    else:
        companies = membership.loc[membership["district"].eq(selected_district), "company_id"].unique().tolist()
    companies = sorted(companies, key=company_sort_key)
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
    selected_membership = membership.loc[membership["company_id"].eq(selected_company)]
    if weather_by_district:
        st.caption("ON — moti lidhet sipas prefiksit të njehsorit dhe distriktit përkatës.")
    else:
        st.caption("OFF — temperatura e Prishtinës përdoret si proxy për të gjithë njehsorët.")

start_ts = pd.Timestamp(date_start)
end_ts = pd.Timestamp(date_end)
hourly = hourly_all.loc[hourly_all["date"].between(start_ts, end_ts)].copy()
if hourly.empty:
    st.error("Nuk ka të dhëna në periudhën e zgjedhur.")
    st.stop()
st.caption(f"Periudha aktive e rezultateve: {start_ts:%d.%m.%Y} – {end_ts:%d.%m.%Y}")
company_row = company_metrics.loc[company_metrics["company_id"].eq(selected_company)].iloc[0]
period_metrics = calculate_period_metrics(hourly)
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
    cols[0].metric("Energjia totale", f"{fmt_number(period_metrics['energy_total_kwh'] / 1000)} MWh")
    cols[1].metric("Raporti pik/jo-pik", fmt_number(period_metrics["peak_offpeak_ratio"]))
    cols[2].metric("Java/fundjava", fmt_number(period_metrics["weekday_weekend_ratio"]))
    cols[3].metric("Load factor", fmt_pct(period_metrics["load_factor"]))
    cols[4].metric("Mbulimi", fmt_pct(period_metrics["coverage_share"]))

    left, right = st.columns([1.2, 1])
    with left:
        st.markdown("#### Interpretimi")
        st.write(f"**{cluster_id}:** {cluster_description}")
        st.caption("Grupi dhe interpretimi i sezonalitetit janë klasifikime të periudhës bazë të pipeline-it.")
    with right:
        st.markdown("#### Njehsorët")
        st.metric("Numri i njehsorëve", int(company_row["meter_count"]))
        st.write(f"**Distrikti/et:** {'; '.join(selected_membership['district'].tolist())}")
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

    monthly = (
        consumption.assign(month_start=consumption["interval_start"].dt.to_period("M").dt.to_timestamp())
        .groupby("month_start", as_index=False)["energy_kwh"].sum(min_count=1)
        .rename(columns={"energy_kwh": "month_total_kwh"})
    )
    fig = px.bar(
        monthly,
        x="month_start",
        y="month_total_kwh",
        labels={"month_start": "Muaji", "month_total_kwh": "Konsum (kWh)"},
        title="Konsumi mujor në periudhën e zgjedhur",
    )
    st.plotly_chart(fig, use_container_width=True)

with tabs[2]:
    daily = load_company_daily(selected_company, weather_by_district)
    daily = daily.loc[daily["date"].between(start_ts, end_ts)]
    weather_table = tables["company_weather"] if weather_by_district else tables["company_weather_proxy"]
    sensitivity_reference = weather_table.loc[
        weather_table["company_id"].eq(selected_company)
    ].iloc[0]
    sensitivity = calculate_weather_metrics(daily)
    holiday_effect = calculate_holiday_effect(daily)
    cols = st.columns(4)
    cols[0].metric("Temperaturë–konsum", fmt_number(sensitivity["temperature_correlation"]))
    cols[1].metric("HDD–konsum", fmt_number(sensitivity["hdd_correlation"]))
    cols[2].metric("CDD–konsum", fmt_number(sensitivity["cdd_correlation"]))
    cols[3].metric(
        "Efekti mesatar i festave",
        fmt_pct(holiday_effect),
    )
    st.info(str(sensitivity["weather_sensitivity_label"]))
    st.caption(
        f"Burimi territorial: {sensitivity_reference['districts']}. "
        f"Metoda: {sensitivity_reference['temperature_method']}. "
        f"Ditë të vlefshme në filtër: {sensitivity['days_with_consumption']}."
    )
    if not sensitivity["weather_analysis_reliable"]:
        st.warning("Periudha e zgjedhur ka më pak se 90 ditë të vlefshme; korelacionet duhen interpretuar me kujdes.")
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
        title=(
            "Konsumi ditor kundrejt temperaturës lokale të distriktit"
            if weather_by_district else
            "Konsumi ditor kundrejt temperaturës së Prishtinës (proxy)"
        ),
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
    outliers = load_company_outliers(selected_company, weather_by_district)
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
    meter_location = selected_membership.loc[
        selected_membership["meter_prefix"].eq(str(selected_meter)[:3].upper())
    ]
    if not meter_location.empty:
        st.caption(f"Prefiksi: {meter_location.iloc[0]['meter_prefix']} · Distrikti: {meter_location.iloc[0]['district']}")
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
    selected_meter_period = hourly.loc[
        hourly["meter_id"].eq(selected_meter) & hourly["energy_flow"].eq(selected_flow)
    ]
    meter_period_metrics = calculate_period_metrics(
        selected_meter_period.assign(energy_flow="consumption_import")
    )
    cols = st.columns(5)
    cols[0].metric("Energjia", f"{fmt_number(meter_period_metrics['energy_total_kwh'] / 1000)} MWh")
    cols[1].metric("Pik/jo-pik", fmt_number(meter_period_metrics["peak_offpeak_ratio"]))
    cols[2].metric("Java/fundjava", fmt_number(meter_period_metrics["weekday_weekend_ratio"]))
    cols[3].metric("CV", fmt_number(meter_period_metrics["coefficient_of_variation"]))
    cols[4].metric("Load factor", fmt_pct(meter_period_metrics["load_factor"]))
    meter_profile = selected_meter_period.groupby("hour_1_24", as_index=False)["energy_kwh"].mean().rename(
        columns={"energy_kwh": "mean_kwh"}
    )
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
    all_daily = load_all_company_daily(weather_by_district)
    period_company_energy = all_daily.loc[
        all_daily["date"].between(start_ts, end_ts)
    ].groupby("company_id", as_index=False)["energy_total_kwh"].sum()
    period_cluster_energy = tables["clusters"][["company_id", "cluster_number"]].merge(
        period_company_energy, on="company_id", how="left", validate="one_to_one"
    ).groupby("cluster_number", as_index=False)["energy_total_kwh"].sum()
    centers = centers.drop(columns=["total_energy_kwh", "total_energy_mwh"], errors="ignore").merge(
        period_cluster_energy, on="cluster_number", how="left", validate="one_to_one"
    )
    centers["total_energy_mwh"] = centers["energy_total_kwh"] / 1000
    centers["Energjia totale (MWh)"] = centers["total_energy_mwh"].map(
        lambda value: f"{value:,.2f}"
    )
    st.caption("ID-të e grupeve mbeten nga periudha bazë; energjia në tabelë rillogaritet për datat e zgjedhura.")
    st.dataframe(
        centers[["cluster_id", "cluster_description", "company_count", "Energjia totale (MWh)"]],
        use_container_width=True,
        hide_index=True,
        column_config={
            "cluster_id": "Grupi",
            "cluster_description": "Përshkrimi i grupit",
            "company_count": "Numri i kompanive",
        },
    )
    export_bytes = build_cluster_company_export(
        tables["clusters"], period_company_energy, date_start, date_end
    )
    st.download_button(
        "Eksporto listat e grupeve në Excel",
        data=export_bytes,
        file_name=f"kompanite_sipas_grupeve_{date_start}_{date_end}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
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
        "‘Për shqyrtim’ nuk do të thotë automatikisht defekt. Raporti i cilësisë është "
        "output bazë i pipeline-it dhe nuk rillogaritet nga filtri i datave në UI."
    )
