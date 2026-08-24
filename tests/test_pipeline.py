from __future__ import annotations
import json
from pathlib import Path
import pytest
from enerco_analysis.cli import build_parser
from enerco_analysis.config import ProjectPaths
from enerco_analysis.pipeline import run_all


def _project(tmp_path: Path) -> ProjectPaths:
    (tmp_path / "config").mkdir()
    (tmp_path / "data" / "input").mkdir(parents=True)
    (tmp_path / "data" / "external").mkdir(parents=True)
    config = {
        "analysis": {
            "start_date": "2025-06-01",
            "end_date": "2026-06-30",
            "profile_start_date": "2025-07-01",
            "profile_end_date": "2026-06-30",
            "peak_hours": [7, 18],
            "weekday_numbers": [0, 1, 2, 3, 4],
            "weekend_numbers": [5, 6],
        },
        "profile_metrics": {},
        "external_factors": {"weather_raw_filename": "kosovo_district_weather_raw.json"},
        "quality": {"unusable_missing_share": 0.1, "zero_run_hours": 48},
        "outliers": {},
        "clustering": {"k_min": 2, "k_max": 3},
        "inputs": {
            "hourly_workbook": "source.xlsx",
            "confidential_key_workbook": "key.xlsx",
        },
        "privacy": {"allow_real_company_names_in_outputs": False},
    }
    (tmp_path / "config" / "project.json").write_text(json.dumps(config), encoding="utf-8")
    (tmp_path / "data" / "input" / "source.xlsx").write_bytes(b"source")
    (tmp_path / "data" / "external" / "kosovo_district_weather_raw.json").write_text(
        "{}", encoding="utf-8"
    )
    (tmp_path / "data" / "external" / "kosovo_official_holidays.csv").write_text(
        "date,name\n", encoding="utf-8"
    )
    return ProjectPaths.from_root(tmp_path)


def _write(path: Path, value: str = "new") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _install_success_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    import enerco_analysis.pipeline as module

    def quality(_workbook, report, _config, table):
        _write(report)
        _write(table)
        return {"series_count": 1}

    def transform(_workbook, _quality, output, validation, _config):
        _write(output)
        _write(validation)
        return {"rows": 1}

    def profiles(_long, processed, report, _config):
        _write(report)
        _write(processed / "company_profile_metrics.parquet")
        _write(processed / "meter_profile_metrics.parquet")
        return {"companies": 1}

    def outliers(_long, processed, report, _config):
        _write(report)
        _write(processed / "company_hourly_outliers.parquet")
        return {"outliers": 0}

    def prepare(_weather, _holidays, external, report, _config):
        _write(report)
        _write(external / "kosovo_district_weather_hourly.parquet")
        return {"weather_rows": 1}

    def enrich(_long, _external, output, _config):
        _write(output)
        return {"rows": 1}

    def analyze(_enriched, processed, _external, report, outlier_report, _config):
        _write(report)
        _write(outlier_report)
        _write(processed / "company_weather_sensitivity.parquet")
        return {"companies": 1}

    def clusters(_metrics, processed, report, _config):
        _write(report)
        _write(processed / "company_clusters.parquet")
        return {"companies_clustered": 1, "report_file": report.name}

    monkeypatch.setattr(module, "build_quality_report", quality)
    monkeypatch.setattr(module, "transform_to_long", transform)
    monkeypatch.setattr(module, "build_profile_metrics", profiles)
    monkeypatch.setattr(module, "build_outlier_report", outliers)
    monkeypatch.setattr(module, "prepare_external_factors", prepare)
    monkeypatch.setattr(module, "enrich_hourly_consumption", enrich)
    monkeypatch.setattr(module, "analyze_external_factors", analyze)
    monkeypatch.setattr(module, "build_company_clusters", clusters)


def test_run_all_promotes_only_after_all_steps_succeed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _project(tmp_path)
    old_report = paths.output_dir / "profile_metrics.xlsx"
    _write(old_report, "old")
    _install_success_stubs(monkeypatch)

    result = run_all(paths, progress=lambda _message: None)

    assert result["status"] == "success"
    assert len(result["steps"]) == 6
    assert old_report.read_text(encoding="utf-8") == "new"
    assert (paths.processed_dir / "hourly_consumption_enriched.parquet").is_file()
    summary = json.loads((paths.output_dir / "pipeline_run_summary.json").read_text(encoding="utf-8"))
    assert summary["source_workbook"]["sha256"]
    assert summary["status"] == "success"
    assert not (paths.root / ".pipeline_staging").exists()


def test_run_all_preserves_previous_outputs_when_a_step_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _project(tmp_path)
    old_report = paths.output_dir / "profile_metrics.xlsx"
    _write(old_report, "old")
    _install_success_stubs(monkeypatch)

    def fail_profiles(*_args, **_kwargs):
        raise RuntimeError("profile failure")

    monkeypatch.setattr("enerco_analysis.pipeline.build_profile_metrics", fail_profiles)
    with pytest.raises(RuntimeError, match="profile failure"):
        run_all(paths, progress=lambda _message: None)

    assert old_report.read_text(encoding="utf-8") == "old"
    summary = json.loads((paths.output_dir / "pipeline_run_summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "failed"
    assert summary["previous_outputs_preserved"] is True
    assert summary["steps"][-1]["status"] == "failed"


def test_parser_accepts_run_all() -> None:
    args = build_parser().parse_args(["run-all"])
    assert args.command == "run-all"
