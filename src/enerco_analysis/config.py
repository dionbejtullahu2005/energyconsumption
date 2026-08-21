from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

@dataclass(frozen=True)
class ProjectPaths:
    root: Path
    config_file: Path
    input_dir: Path
    external_dir: Path
    interim_dir: Path
    processed_dir: Path
    output_dir: Path

    @classmethod
    def from_root(cls, root: Path) -> "ProjectPaths":
        root = root.resolve()
        return cls(
            root=root,
            config_file=root / "config" / "project.json",
            input_dir=root / "data" / "input",
            external_dir=root / "data" / "external",
            interim_dir=root / "data" / "interim",
            processed_dir=root / "data" / "processed",
            output_dir=root / "outputs",
        )


def load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)
    validate_config(config)
    return config


def validate_config(config: dict[str, Any]) -> None:
    required_sections = {
        "analysis", "quality", "outliers", "clustering", "inputs", "privacy",
        "profile_metrics", "external_factors"
    }
    missing = required_sections.difference(config)
    if missing:
        raise ValueError(f"Mungojnë seksionet e konfigurimit: {sorted(missing)}")

    analysis = config["analysis"]
    start = date.fromisoformat(analysis["start_date"])
    end = date.fromisoformat(analysis["end_date"])
    if start > end:
        raise ValueError("analysis.start_date duhet të jetë para analysis.end_date")

    peak_start, peak_end = analysis["peak_hours"]
    if not (0 <= peak_start <= peak_end <= 23):
        raise ValueError("analysis.peak_hours duhet të jetë brenda intervalit 0–23")

    quality = config["quality"]
    if not 0 <= quality["unusable_missing_share"] <= 1:
        raise ValueError("quality.unusable_missing_share duhet të jetë ndërmjet 0 dhe 1")
    if quality["zero_run_hours"] <= 0:
        raise ValueError("quality.zero_run_hours duhet të jetë pozitiv")

    clustering = config["clustering"]
    if clustering["k_min"] < 2 or clustering["k_max"] < clustering["k_min"]:
        raise ValueError("Intervali clustering.k_min/k_max nuk është valid")


def ensure_project_directories(paths: ProjectPaths) -> None:
    for directory in (
        paths.input_dir,
        paths.external_dir,
        paths.interim_dir,
        paths.processed_dir,
        paths.output_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)
