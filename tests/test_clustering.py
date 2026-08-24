import pandas as pd

from enerco_analysis.clustering import (
    _cluster_totals,
    _elbow_k,
    _make_scaler,
    ensure_cluster_energy_totals,
)

def test_elbow_choice_is_internal_for_curved_inertia() -> None:
    assert _elbow_k([2, 3, 4, 5, 6], [100.0, 55.0, 35.0, 28.0, 24.0]) in {3, 4}

def test_scaler_factory() -> None:
    assert _make_scaler("standard").__class__.__name__ == "StandardScaler"


def test_cluster_totals_sum_company_energy() -> None:
    assignments = pd.DataFrame(
        {
            "company_id": ["Kompania 1", "Kompania 2", "Kompania 3"],
            "cluster_number": [1, 1, 2],
            "cluster_id": ["Klaster 1", "Klaster 1", "Klaster 2"],
            "cluster_description": ["Profili A", "Profili A", "Profili B"],
            "energy_total_kwh": [1000.0, 2500.0, 4000.0],
        }
    )
    result = _cluster_totals(assignments).set_index("cluster_number")
    assert result.loc[1, "company_count"] == 2
    assert result.loc[1, "total_energy_kwh"] == 3500.0
    assert result.loc[1, "total_energy_mwh"] == 3.5


def test_old_cluster_centers_are_upgraded_for_ui() -> None:
    old_centers = pd.DataFrame(
        {
            "cluster_number": [1, 2],
            "cluster_id": ["Klaster 1", "Klaster 2"],
            "cluster_description": ["Profili A", "Profili B"],
            "company_count": [2, 1],
        }
    )
    assignments = pd.DataFrame(
        {
            "company_id": ["Kompania 1", "Kompania 2", "Kompania 3"],
            "cluster_number": [1, 1, 2],
            "cluster_id": ["Klaster 1", "Klaster 1", "Klaster 2"],
            "cluster_description": ["Profili A", "Profili A", "Profili B"],
            "energy_total_kwh": [1000.0, 2500.0, 4000.0],
        }
    )
    upgraded = ensure_cluster_energy_totals(old_centers, assignments).set_index("cluster_number")
    assert upgraded.loc[1, "total_energy_mwh"] == 3.5
    assert upgraded.loc[2, "total_energy_mwh"] == 4.0
