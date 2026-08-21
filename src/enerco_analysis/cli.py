from __future__ import annotations

import argparse
import sys
from pathlib import Path

from enerco_analysis.config import ProjectPaths, ensure_project_directories, load_config
from enerco_analysis.quality import build_quality_report
from enerco_analysis.transform import transform_to_long
from enerco_analysis.profiles import build_profile_metrics
from enerco_analysis.outliers import build_outlier_report
from enerco_analysis.pipeline import run_all
from enerco_analysis.clustering import build_company_clusters
from enerco_analysis.external_factors import (
    analyze_external_factors,
    enrich_hourly_consumption,
    prepare_external_factors,
)

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="enerco-analysis",
        description="Pipeline analitik për konsumin orar të EnerCo.",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path.cwd(),
        help="Dosja rrënjë e projektit (parazgjedhje: dosja aktuale).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("check-setup", help="Verifikon konfigurimin dhe inputet.")
    subparsers.add_parser("quality-report", help="Gjeneron raportin e Hapit 1.")
    subparsers.add_parser("transform-long", help="Gjeneron tabelën e gjatë të Hapit 2.")
    subparsers.add_parser("profile-metrics", help="Gjeneron metrikat e profilit të Hapit 3.")
    subparsers.add_parser("outlier-report", help="Gjeneron outlierët e Hapit 4.")
    subparsers.add_parser("cluster-companies", help="Gjeneron klasterët e Hapit 6.")
    subparsers.add_parser("external-factors", help="Përgatit dhe analizon motin/festat e Hapit 5.")
    subparsers.add_parser(
        "run-all",
        help="Ekzekuton në mënyrë të sigurt të gjithë pipeline-in, nga Hapi 1 te Hapi 6.",
    )
    return parser


def check_setup(paths: ProjectPaths) -> int:
    ensure_project_directories(paths)
    config = load_config(paths.config_file)
    hourly = paths.input_dir / config["inputs"]["hourly_workbook"]
    key = paths.input_dir / config["inputs"]["confidential_key_workbook"]

    print(f"Konfigurimi: OK ({paths.config_file})")
    print(f"Periudha: {config['analysis']['start_date']} – {config['analysis']['end_date']}")
    print(f"Workbook orar: {'GJETUR' if hourly.is_file() else 'MUNGON'} ({hourly})")
    print(f"Çelësi konfidencial: {'GJETUR' if key.is_file() else 'MUNGON'} ({key})")
    print("Emrat realë në output: JO")
    return 0 if hourly.is_file() else 2


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args()
    paths = ProjectPaths.from_root(args.project_root)
    if args.command == "check-setup":
        return check_setup(paths)
    if args.command == "run-all":
        try:
            run_all(paths)
        except Exception as error:
            print(f"GABIM: {type(error).__name__}: {error}")
            print("Pipeline-i u ndërpre; rezultatet e mëparshme janë ruajtur.")
            return 1
        return 0
    if args.command == "quality-report":
        ensure_project_directories(paths)
        config = load_config(paths.config_file)
        workbook = paths.input_dir / config["inputs"]["hourly_workbook"]
        if not workbook.is_file():
            print(f"Workbook-u mungon: {workbook}")
            return 2
        output = paths.output_dir / "data_quality_report.xlsx"
        print(f"Duke analizuar: {workbook.name}")
        machine_table = paths.interim_dir / "meter_quality.parquet"
        summary = build_quality_report(workbook, output, config, machine_table)
        print(f"Raporti u krijua: {output}")
        for key, value in summary.items():
            print(f"{key}: {value}")
        return 0
    if args.command == "transform-long":
        ensure_project_directories(paths)
        config = load_config(paths.config_file)
        workbook = paths.input_dir / config["inputs"]["hourly_workbook"]
        quality_report = paths.interim_dir / "meter_quality.parquet"
        if not workbook.is_file():
            print(f"Workbook-u mungon: {workbook}")
            return 2
        if not quality_report.is_file():
            print(f"Tabela teknike e Hapit 1 mungon: {quality_report}")
            return 2
        output = paths.processed_dir / "hourly_consumption_long.parquet"
        validation = paths.output_dir / "long_format_validation.xlsx"
        print(f"Duke transformuar: {workbook.name}")
        summary = transform_to_long(workbook, quality_report, output, validation, config)
        print(f"Tabela e gjatë u krijua: {output}")
        print(f"Raporti i validimit: {validation}")
        for key, value in summary.items():
            print(f"{key}: {value}")
        return 0
    if args.command == "profile-metrics":
        ensure_project_directories(paths)
        config = load_config(paths.config_file)
        long_path = paths.processed_dir / "hourly_consumption_long.parquet"
        if not long_path.is_file():
            print(f"Tabela e Hapit 2 mungon: {long_path}")
            return 2
        report = paths.output_dir / "profile_metrics.xlsx"
        print(f"Duke llogaritur metrikat nga: {long_path.name}")
        summary = build_profile_metrics(long_path, paths.processed_dir, report, config)
        print(f"Raporti i metrikave u krijua: {report}")
        for key, value in summary.items():
            print(f"{key}: {value}")
        return 0
    if args.command == "outlier-report":
        ensure_project_directories(paths)
        config = load_config(paths.config_file)
        long_path = paths.processed_dir / "hourly_consumption_long.parquet"
        required = [
            long_path,
            paths.processed_dir / "meter_profile_metrics.parquet",
            paths.processed_dir / "company_profile_metrics.parquet",
        ]
        missing = [path for path in required if not path.is_file()]
        if missing:
            print(f"Mungojnë output-et paraprake: {missing}")
            return 2
        report = paths.output_dir / "outlier_report.xlsx"
        print(f"Duke llogaritur outlierët nga: {long_path.name}")
        summary = build_outlier_report(long_path, paths.processed_dir, report, config)
        print(f"Raporti i outlierëve u krijua: {report}")
        for key, value in summary.items():
            print(f"{key}: {value}")
        return 0
    if args.command == "cluster-companies":
        ensure_project_directories(paths)
        config = load_config(paths.config_file)
        company_metrics = paths.processed_dir / "company_profile_metrics.parquet"
        if not company_metrics.is_file():
            print(f"Metrikat e kompanive mungojnë: {company_metrics}")
            return 2
        report = paths.output_dir / "company_clustering.xlsx"
        print(f"Duke klasterizuar kompanitë nga: {company_metrics.name}")
        summary = build_company_clusters(company_metrics, paths.processed_dir, report, config)
        actual_report = report.with_name(str(summary.get("report_file", report.name)))
        print(f"Raporti i klasterizimit u krijua: {actual_report}")
        for key, value in summary.items():
            print(f"{key}: {value}")
        return 0
    if args.command == "external-factors":
        ensure_project_directories(paths)
        config = load_config(paths.config_file)
        raw_weather = paths.external_dir / "prishtina_open_meteo_raw.json"
        holidays_csv = paths.external_dir / "kosovo_official_holidays.csv"
        long_path = paths.processed_dir / "hourly_consumption_long.parquet"
        required = [raw_weather, holidays_csv, long_path, paths.processed_dir / "company_hourly_outliers.parquet"]
        missing = [path for path in required if not path.is_file()]
        if missing:
            print(f"Mungojnë burimet/output-et për Hapin 5: {missing}")
            return 2
        quality_report = paths.output_dir / "weather_data_quality.xlsx"
        print("Duke përgatitur temperaturat, HDD/CDD dhe kalendarin e festave...")
        source_summary = prepare_external_factors(
            raw_weather, holidays_csv, paths.external_dir, quality_report, config
        )
        enriched_path = paths.processed_dir / "hourly_consumption_enriched.parquet"
        print("Duke pasuruar konsumin orar me motin dhe festat...")
        enrichment_summary = enrich_hourly_consumption(
            long_path, paths.external_dir, enriched_path
        )
        analysis_report = paths.output_dir / "weather_holiday_analysis.xlsx"
        outlier_report = paths.output_dir / "outlier_report_enriched.xlsx"
        print("Duke analizuar ndjeshmërinë ndaj motit dhe efektin e festave...")
        analysis_summary = analyze_external_factors(
            enriched_path,
            paths.processed_dir,
            paths.external_dir,
            analysis_report,
            outlier_report,
            config,
        )
        print(f"Raporti i cilësisë së burimeve: {quality_report}")
        print(f"Raporti mot/festa: {analysis_report}")
        print(f"Outlierët e pasuruar: {outlier_report}")
        for section, values in (
            ("external_sources", source_summary),
            ("enrichment", enrichment_summary),
            ("analysis", analysis_summary),
        ):
            print(f"[{section}]")
            for key, value in values.items():
                print(f"{key}: {value}")
        return 0
    raise RuntimeError(f"Komandë e panjohur: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
