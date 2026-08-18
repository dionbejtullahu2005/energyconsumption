from __future__ import annotations

import os
import warnings
from datetime import datetime
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

os.environ.setdefault("LOKY_MAX_CPU_COUNT", str(max(1, os.cpu_count() or 1)))
warnings.filterwarnings(
    "ignore",
    message="Could not find the number of physical cores.*",
    category=UserWarning,
)

from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import MinMaxScaler, RobustScaler, StandardScaler


def _make_scaler(name: str) -> Any:
    normalised = name.lower()
    if normalised == "standard":
        return StandardScaler()
    if normalised == "minmax":
        return MinMaxScaler()
    if normalised == "robust":
        return RobustScaler()
    raise ValueError(f"Scaler i panjohur: {name}")


def _elbow_k(k_values: list[int], inertias: list[float]) -> int:
    if len(k_values) < 3:
        return k_values[0]
    x = np.asarray(k_values, dtype=float)
    y = np.asarray(inertias, dtype=float)
    x = (x - x.min()) / (x.max() - x.min())
    y_range = y.max() - y.min()
    y = (y - y.min()) / y_range if y_range > 0 else np.zeros_like(y)
    start = np.array([x[0], y[0]])
    end = np.array([x[-1], y[-1]])
    line = end - start
    line_norm = np.linalg.norm(line)
    if line_norm == 0:
        return k_values[0]
    points = np.column_stack([x, y])
    offsets = points - start
    distances = np.abs(line[0] * offsets[:, 1] - line[1] * offsets[:, 0]) / line_norm
    return k_values[int(np.argmax(distances))]


def _cluster_description(
    center: pd.Series,
    overall_mean: pd.Series,
    overall_std: pd.Series,
) -> str:
    labels: list[str] = []
    if center["summer_index"] - center["winter_index"] >= 0.20 and center["summer_index"] >= 1.10:
        labels.append("pik veror")
    elif center["winter_index"] - center["summer_index"] >= 0.20 and center["winter_index"] >= 1.10:
        labels.append("pik dimëror")
    if (
        center["load_factor"] >= overall_mean["load_factor"]
        and center["coefficient_of_variation"]
        <= overall_mean["coefficient_of_variation"]
    ):
        labels.append("profil relativisht i qëndrueshëm")
    if (
        center["peak_offpeak_ratio"] >= overall_mean["peak_offpeak_ratio"]
        and center["weekday_weekend_ratio"] >= overall_mean["weekday_weekend_ratio"]
    ):
        labels.append("konsum më ditor dhe i orientuar nga java e punës")
    if (
        center["coefficient_of_variation"] > overall_mean["coefficient_of_variation"]
        and center["load_factor"] < overall_mean["load_factor"]
    ):
        labels.append("ngarkesë më kulmore dhe e luhatshme")
    return " – ".join(labels).capitalize() if labels else "Profil i përzier"


def _save_evaluation_chart(evaluation: pd.DataFrame, output_path: Path) -> None:
    figure, axes = plt.subplots(1, 2, figsize=(12, 4.5))
    axes[0].plot(evaluation["k"], evaluation["inertia"], marker="o")
    axes[0].axvline(
        evaluation.loc[evaluation["is_elbow_choice"], "k"].iloc[0],
        color="tab:orange",
        linestyle="--",
        label="Elbow",
    )
    axes[0].set(title="Elbow Method", xlabel="Numri i klasterëve (k)", ylabel="Inertia")
    axes[0].legend()
    axes[1].plot(evaluation["k"], evaluation["silhouette_score"], marker="o", color="tab:green")
    axes[1].axvline(
        evaluation.loc[evaluation["is_silhouette_choice"], "k"].iloc[0],
        color="tab:red",
        linestyle="--",
        label="Silhouette maksimum",
    )
    axes[1].set(
        title="Silhouette Score",
        xlabel="Numri i klasterëve (k)",
        ylabel="Silhouette mesatare",
    )
    axes[1].legend()
    figure.tight_layout()
    figure.savefig(output_path, dpi=160, bbox_inches="tight")
    plt.close(figure)


def _save_pca_chart(assignments: pd.DataFrame, output_path: Path) -> None:
    figure, axis = plt.subplots(figsize=(8, 6))
    for cluster_id, group in assignments.groupby("cluster_id"):
        axis.scatter(group["pca_1"], group["pca_2"], label=cluster_id, alpha=0.8, s=42)
    axis.set(
        title="Klasterët e kompanive – projeksion PCA",
        xlabel="PCA 1",
        ylabel="PCA 2",
    )
    axis.legend(title="Klasteri", fontsize=8)
    figure.tight_layout()
    figure.savefig(output_path, dpi=160, bbox_inches="tight")
    plt.close(figure)


def build_company_clusters(
    company_metrics_path: Path,
    processed_dir: Path,
    report_path: Path,
    config: dict[str, Any],
) -> dict[str, Any]:
    metrics = pd.read_parquet(company_metrics_path)
    clustering_config = config["clustering"]
    features = list(clustering_config["features"])
    missing_columns = sorted(set(features).difference(metrics.columns))
    if missing_columns:
        raise ValueError(f"Mungojnë features për klasterizim: {missing_columns}")

    complete_mask = metrics[features].replace([np.inf, -np.inf], np.nan).notna().all(axis=1)
    eligible = metrics.loc[complete_mask].copy()
    excluded = metrics.loc[~complete_mask, ["company_id"] + features].copy()
    if len(eligible) < 4:
        raise ValueError("Nuk ka mjaft kompani me metrika të plota për klasterizim")

    scaler = _make_scaler(str(clustering_config["scaler"]))
    scaled = scaler.fit_transform(eligible[features])
    k_min = max(2, int(clustering_config["k_min"]))
    k_max = min(int(clustering_config["k_max"]), len(eligible) - 1)
    k_values = list(range(k_min, k_max + 1))
    random_state = int(clustering_config["random_state"])
    n_init = int(clustering_config["n_init"])
    sensitivity_rows: list[dict[str, Any]] = []
    for scaler_name in ("standard", "minmax", "robust"):
        candidate_scaler = _make_scaler(scaler_name)
        candidate_scaled = candidate_scaler.fit_transform(eligible[features])
        for k in k_values:
            candidate_model = KMeans(
                n_clusters=k, random_state=random_state, n_init=n_init
            )
            candidate_labels = candidate_model.fit_predict(candidate_scaled)
            sensitivity_rows.append(
                {
                    "scaler": scaler_name,
                    "k": k,
                    "silhouette_score": float(
                        silhouette_score(candidate_scaled, candidate_labels)
                    ),
                    "minimum_cluster_size": int(
                        pd.Series(candidate_labels).value_counts().min()
                    ),
                    "maximum_cluster_size": int(
                        pd.Series(candidate_labels).value_counts().max()
                    ),
                }
            )
    scaler_sensitivity = pd.DataFrame(sensitivity_rows)
    inertias: list[float] = []
    silhouettes: list[float] = []
    for k in k_values:
        model = KMeans(n_clusters=k, random_state=random_state, n_init=n_init)
        labels = model.fit_predict(scaled)
        inertias.append(float(model.inertia_))
        silhouettes.append(float(silhouette_score(scaled, labels)))

    elbow_choice = _elbow_k(k_values, inertias)
    silhouette_choice = k_values[int(np.argmax(silhouettes))]
    selected_k = silhouette_choice
    final_model = KMeans(n_clusters=selected_k, random_state=random_state, n_init=n_init)
    raw_labels = final_model.fit_predict(scaled)
    original_centers = pd.DataFrame(scaler.inverse_transform(final_model.cluster_centers_), columns=features)

    # Rinumërimi sipas raportit pik/jo-pik e bën Cluster ID të qëndrueshëm dhe të lexueshëm.
    ordered_raw = original_centers.sort_values(
        ["peak_offpeak_ratio", "weekday_weekend_ratio"]
    ).index.tolist()
    label_map = {raw: index + 1 for index, raw in enumerate(ordered_raw)}
    stable_numbers = np.array([label_map[label] for label in raw_labels])
    centers = original_centers.assign(raw_cluster=range(selected_k))
    centers["cluster_number"] = centers["raw_cluster"].map(label_map)
    centers = centers.sort_values("cluster_number").drop(columns="raw_cluster")
    overall_mean = eligible[features].mean()
    overall_std = eligible[features].std()
    centers["cluster_description"] = [
        _cluster_description(row, overall_mean, overall_std) for _, row in centers.iterrows()
    ]
    centers["cluster_id"] = centers["cluster_number"].map(lambda value: f"Klaster {value}")
    cluster_descriptions = centers.set_index("cluster_number")["cluster_description"].to_dict()

    pca = PCA(n_components=2, random_state=random_state)
    coordinates = pca.fit_transform(scaled)
    assignments = eligible.copy()
    assignments["cluster_number"] = stable_numbers
    assignments["cluster_id"] = assignments["cluster_number"].map(lambda value: f"Klaster {value}")
    assignments["cluster_description"] = assignments["cluster_number"].map(cluster_descriptions)
    assignments["pca_1"] = coordinates[:, 0]
    assignments["pca_2"] = coordinates[:, 1]
    assignments["seasonality_input_reliable"] = assignments["seasonality_reliable"]
    assignments["sector_comparison_status"] = "Nuk disponohet metadata e sektorit"

    cluster_sizes = assignments.groupby(
        ["cluster_number", "cluster_id", "cluster_description"], observed=True
    ).size().rename("company_count").reset_index()
    centers = centers.merge(cluster_sizes, on=["cluster_number", "cluster_id", "cluster_description"])
    evaluation = pd.DataFrame(
        {"k": k_values, "inertia": inertias, "silhouette_score": silhouettes}
    )
    evaluation["is_elbow_choice"] = evaluation["k"].eq(elbow_choice)
    evaluation["is_silhouette_choice"] = evaluation["k"].eq(silhouette_choice)
    evaluation["is_selected"] = evaluation["k"].eq(selected_k)
    feature_stats = pd.DataFrame(
        {
            "feature": features,
            "original_mean": eligible[features].mean().values,
            "original_std": eligible[features].std().values,
            "scaled_mean": scaled.mean(axis=0),
            "scaled_std_population": scaled.std(axis=0),
        }
    )

    processed_dir.mkdir(parents=True, exist_ok=True)
    assignments.to_parquet(processed_dir / "company_clusters.parquet", index=False)
    centers.to_parquet(processed_dir / "cluster_centers.parquet", index=False)
    evaluation.to_parquet(processed_dir / "cluster_evaluation.parquet", index=False)
    scaler_sensitivity.to_parquet(
        processed_dir / "cluster_scaler_sensitivity.parquet", index=False
    )
    excluded.to_parquet(processed_dir / "cluster_excluded_companies.parquet", index=False)

    chart_dir = report_path.parent / "charts"
    chart_dir.mkdir(parents=True, exist_ok=True)
    evaluation_chart = chart_dir / "clustering_elbow_silhouette.png"
    pca_chart = chart_dir / "clustering_pca.png"
    _save_evaluation_chart(evaluation, evaluation_chart)
    _save_pca_chart(assignments, pca_chart)

    summary = {
        "companies_total": len(metrics),
        "companies_clustered": len(assignments),
        "companies_excluded": len(excluded),
        "feature_count": len(features),
        "scaler": str(clustering_config["scaler"]),
        "k_tested_min": k_min,
        "k_tested_max": k_max,
        "elbow_choice": elbow_choice,
        "silhouette_choice": silhouette_choice,
        "selected_k": selected_k,
        "selected_silhouette_score": float(max(silhouettes)),
        "minimum_cluster_size": int(cluster_sizes["company_count"].min()),
        "maximum_cluster_size": int(cluster_sizes["company_count"].max()),
        "pca_explained_variance_2d": float(pca.explained_variance_ratio_.sum()),
        "sector_comparison_completed": False,
        "sector_comparison_blocker": "Metadata e sektorit nuk disponohet",
        "scaler_selection_reason": (
            "MinMax u zgjodh sepse StandardScaler krijonte klaster singleton nga një vlerë ekstreme"
            if str(clustering_config["scaler"]).lower() == "minmax"
            else "Sipas konfigurimit"
        ),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)

    def write_report(target: Path) -> None:
        with pd.ExcelWriter(target, engine="openpyxl") as writer:
            pd.DataFrame({"metric": summary.keys(), "value": summary.values()}).to_excel(
                writer, sheet_name="Summary", index=False
            )
            assignments.to_excel(writer, sheet_name="Company clusters", index=False)
            centers.to_excel(writer, sheet_name="Cluster centers", index=False)
            evaluation.to_excel(writer, sheet_name="K evaluation", index=False)
            scaler_sensitivity.to_excel(writer, sheet_name="Scaler sensitivity", index=False)
            feature_stats.to_excel(writer, sheet_name="Feature scaling", index=False)
            excluded.to_excel(writer, sheet_name="Excluded companies", index=False)

    actual_report_path = report_path
    try:
        write_report(actual_report_path)
    except PermissionError:
        actual_report_path = report_path.with_name(f"{report_path.stem}_refresh{report_path.suffix}")
        try:
            write_report(actual_report_path)
        except PermissionError:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            actual_report_path = report_path.with_name(
                f"{report_path.stem}_{timestamp}{report_path.suffix}"
            )
            write_report(actual_report_path)
    summary["report_file"] = actual_report_path.name
    return summary
