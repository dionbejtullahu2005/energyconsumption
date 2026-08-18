import pandas as pd

from enerco_analysis.outliers import _zscore_outliers


def test_hourly_zscore_outlier_detection() -> None:
    frame = pd.DataFrame(
        {
            "company_id": ["Kompania 1", "Kompania 1"],
            "energy_kwh": [10.0, 50.0],
        }
    )
    metrics = pd.DataFrame(
        {"company_id": ["Kompania 1"], "energy_mean_kwh": [10.0], "energy_std_kwh": [5.0]}
    )
    result = _zscore_outliers(frame, metrics, ["company_id"], 3.0, 50.0)
    assert len(result) == 1
    assert result.iloc[0]["z_score"] == 8.0
    assert result.iloc[0]["outlier_direction"] == "I lartë"
