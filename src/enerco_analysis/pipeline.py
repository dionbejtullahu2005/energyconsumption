from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from enerco_analysis.clustering import build_company_clusters
from enerco_analysis.config import ProjectPaths, ensure_project_directories, load_config
from enerco_analysis.external_factors import (
    analyze_external_factors,
    enrich_hourly_consumption,
    prepare_external_factors,
)
from enerco_analysis.outliers import build_outlier_report
from enerco_analysis.profiles import build_profile_metrics
from enerco_analysis.quality import build_quality_report
from enerco_analysis.transform import transform_to_long


SUMMARY_FILE = "pipeline_run_summary.json"

def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _source_info(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "name": path.name,
        "path": str(path.resolve()),
        "size_bytes": stat.st_size,
        "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(
            timespec="seconds"
        ),
        "sha256": _sha256(path),
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _staging_paths(paths: ProjectPaths, run_id: str) -> ProjectPaths:
    root = paths.root / ".pipeline_staging" / run_id
    return ProjectPaths(
        root=root,
        config_file=paths.config_file,
        input_dir=paths.input_dir,
        external_dir=root / "external",
        interim_dir=root / "interim",
        processed_dir=root / "processed",
        output_dir=root / "outputs",
    )


def _copy_external_inputs(
    paths: ProjectPaths, staging: ProjectPaths, config: dict[str, Any]
) -> tuple[Path, Path]:
    raw_weather = paths.external_dir / config["external_factors"]["weather_raw_filename"]
    holidays = paths.external_dir / "kosovo_official_holidays.csv"
    missing = [path for path in (raw_weather, holidays) if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Mungojnë burimet e Hapit 5: {missing}")
    staging.external_dir.mkdir(parents=True, exist_ok=True)
    staged_weather = staging.external_dir / raw_weather.name
    staged_holidays = staging.external_dir / holidays.name
    shutil.copy2(raw_weather, staged_weather)
    shutil.copy2(holidays, staged_holidays)
    return staged_weather, staged_holidays


def _record_step(
    steps: list[dict[str, Any]],
    name: str,
    action: Callable[[], dict[str, Any]],
    progress: Callable[[str], None],
) -> dict[str, Any]:
    number = len(steps) + 1
    progress(f"[{number}/6] {name}...")
    started = time.perf_counter()
    try:
        details = action()
    except Exception as error:
        steps.append(
            {
                "step": number,
                "name": name,
                "status": "failed",
                "duration_seconds": round(time.perf_counter() - started, 3),
                "error_type": type(error).__name__,
                "error": str(error),
            }
        )
        raise
    steps.append(
        {
            "step": number,
            "name": name,
            "status": "success",
            "duration_seconds": round(time.perf_counter() - started, 3),
            "summary": details,
        }
    )
    progress(f"      Përfunduar ({steps[-1]['duration_seconds']:.3f} sekonda)")
    return details


def _staged_outputs(staging: ProjectPaths) -> list[tuple[Path, Path]]:
    mappings: list[tuple[Path, Path]] = []
    destinations = {
        staging.interim_dir: "data/interim",
        staging.processed_dir: "data/processed",
        staging.output_dir: "outputs",
        staging.external_dir: "data/external",
    }
    for source_root, destination_root in destinations.items():
        if not source_root.exists():
            continue
        for source in sorted(path for path in source_root.rglob("*") if path.is_file()):
            if source_root == staging.external_dir and (
                source.name.endswith("_raw.json") or source.name == "kosovo_official_holidays.csv"
            ):
                continue
            relative = source.relative_to(source_root)
            mappings.append((source, staging.root.parent.parent / destination_root / relative))
    return mappings


def _promote_with_rollback(
    mappings: list[tuple[Path, Path]], staging_root: Path
) -> list[str]:
    backup_root = staging_root / "backup"
    promoted: list[tuple[Path, Path | None]] = []
    try:
        for index, (source, target) in enumerate(mappings):
            target.parent.mkdir(parents=True, exist_ok=True)
            backup: Path | None = None
            if target.exists():
                backup = backup_root / f"{index:04d}" / target.name
                backup.parent.mkdir(parents=True, exist_ok=True)
                os.replace(target, backup)
            try:
                os.replace(source, target)
            except Exception:
                if backup is not None and backup.exists():
                    os.replace(backup, target)
                raise
            promoted.append((target, backup))
    except Exception:
        for target, backup in reversed(promoted):
            if target.exists():
                target.unlink()
            if backup is not None and backup.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(backup, target)
        raise
    return [str(target) for target, _ in promoted]


def run_all(paths: ProjectPaths, progress: Callable[[str], None] = print) -> dict[str, Any]:
    """Ekzekuton pipeline-in në staging dhe promovon rezultatet vetëm pas suksesit."""
    ensure_project_directories(paths)
    config = load_config(paths.config_file)
    workbook = paths.input_dir / config["inputs"]["hourly_workbook"]
    if not workbook.is_file():
        raise FileNotFoundError(f"Workbook-u mungon: {workbook}")

    run_id = f"{datetime.now():%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}"
    staging = _staging_paths(paths, run_id)
    for directory in (
        staging.external_dir,
        staging.interim_dir,
        staging.processed_dir,
        staging.output_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    started_at = _utc_now()
    started_clock = time.perf_counter()
    steps: list[dict[str, Any]] = []
    report: dict[str, Any] = {
        "run_id": run_id,
        "status": "running",
        "started_at": started_at,
        "project_root": str(paths.root),
        "source_workbook": _source_info(workbook),
        "analysis_period": {
            "start": config["analysis"]["start_date"],
            "end": config["analysis"]["end_date"],
            "profile_start": config["analysis"]["profile_start_date"],
            "profile_end": config["analysis"]["profile_end_date"],
        },
        "steps": steps,
    }
    summary_path = paths.output_dir / SUMMARY_FILE

    try:
        quality_path = staging.output_dir / "data_quality_report.xlsx"
        quality_table = staging.interim_dir / "meter_quality.parquet"
        _record_step(
            steps,
            "Hapi 1 — Kontrolli i cilësisë",
            lambda: build_quality_report(workbook, quality_path, config, quality_table),
            progress,
        )

        long_path = staging.processed_dir / "hourly_consumption_long.parquet"
        _record_step(
            steps,
            "Hapi 2 — Transformimi në format të gjatë",
            lambda: transform_to_long(
                workbook,
                quality_table,
                long_path,
                staging.output_dir / "long_format_validation.xlsx",
                config,
            ),
            progress,
        )

        _record_step(
            steps,
            "Hapi 3 — Metrikat e profilit",
            lambda: build_profile_metrics(
                long_path,
                staging.processed_dir,
                staging.output_dir / "profile_metrics.xlsx",
                config,
            ),
            progress,
        )

        _record_step(
            steps,
            "Hapi 4 — Outlier-ët",
            lambda: build_outlier_report(
                long_path,
                staging.processed_dir,
                staging.output_dir / "outlier_report.xlsx",
                config,
            ),
            progress,
        )

        def external_step() -> dict[str, Any]:
            raw_weather, holidays = _copy_external_inputs(paths, staging, config)
            source = prepare_external_factors(
                raw_weather,
                holidays,
                staging.external_dir,
                staging.output_dir / "weather_data_quality.xlsx",
                config,
            )
            enriched_path = staging.processed_dir / "hourly_consumption_enriched.parquet"
            enrichment = enrich_hourly_consumption(
                long_path, staging.external_dir, enriched_path, config
            )
            analysis = analyze_external_factors(
                enriched_path,
                staging.processed_dir,
                staging.external_dir,
                staging.output_dir / "weather_holiday_analysis.xlsx",
                staging.output_dir / "outlier_report_enriched.xlsx",
                config,
            )
            return {"external_sources": source, "enrichment": enrichment, "analysis": analysis}

        _record_step(
            steps,
            "Hapi 5 — Moti dhe festat",
            external_step,
            progress,
        )

        _record_step(
            steps,
            "Hapi 6 — Grupimi i kompanive",
            lambda: build_company_clusters(
                staging.processed_dir / "company_profile_metrics.parquet",
                staging.processed_dir,
                staging.output_dir / "company_clustering.xlsx",
                config,
            ),
            progress,
        )

        mappings = _staged_outputs(staging)
        required_names = {
            "meter_quality.parquet",
            "hourly_consumption_long.parquet",
            "company_profile_metrics.parquet",
            "company_hourly_outliers.parquet",
            "hourly_consumption_enriched.parquet",
            "company_clusters.parquet",
            "data_quality_report.xlsx",
            "profile_metrics.xlsx",
            "outlier_report_enriched.xlsx",
            "weather_holiday_analysis.xlsx",
            "company_clustering.xlsx",
        }
        staged_names = {source.name for source, _ in mappings}
        missing_outputs = sorted(required_names - staged_names)
        if missing_outputs:
            raise RuntimeError(f"Mungojnë output-et e detyrueshme: {missing_outputs}")

        promoted_targets = [str(target) for _, target in mappings]
        report.update(
            {
                "status": "success",
                "completed_at": _utc_now(),
                "duration_seconds": round(time.perf_counter() - started_clock, 3),
                "promoted_output_count": len(mappings) + 1,
                "promoted_outputs": promoted_targets + [str(summary_path)],
            }
        )
        _write_json(staging.output_dir / SUMMARY_FILE, report)
        mappings = _staged_outputs(staging)
        progress("Duke verifikuar dhe promovuar rezultatet...")
        _promote_with_rollback(mappings, staging.root)
        progress(f"Pipeline-i përfundoi me sukses. Raporti: {summary_path}")
        return report
    except Exception as error:
        report.update(
            {
                "status": "failed",
                "completed_at": _utc_now(),
                "duration_seconds": round(time.perf_counter() - started_clock, 3),
                "error_type": type(error).__name__,
                "error": str(error),
                "previous_outputs_preserved": True,
            }
        )
        _write_json(summary_path, report)
        raise
    finally:
        shutil.rmtree(staging.root, ignore_errors=True)
        staging_parent = staging.root.parent
        if staging_parent.exists() and not any(staging_parent.iterdir()):
            staging_parent.rmdir()
