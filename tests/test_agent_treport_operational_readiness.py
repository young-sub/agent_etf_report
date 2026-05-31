from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_treport.signal_report.adapters.operational_holdings import (
    collect_holdings_fixture,
    compute_operational_export_fingerprint,
)
from agent_treport.signal_report.adapters.operational_readiness import (
    OperationalReadinessInputError,
    check_operational_run_readiness,
)

FIXTURE_ROOT = Path(__file__).parent / "fixtures"
OPERATIONAL_SOURCE_MANIFEST = (
    FIXTURE_ROOT / "operational_holdings_source" / "url_holdings_cumulative.json"
)
OPERATIONAL_NORMALIZED_ROOT = FIXTURE_ROOT / "operational_holdings"


def fixed_now() -> datetime:
    return datetime(2026, 5, 11, 15, 0, tzinfo=UTC)


def same_day_now() -> datetime:
    return datetime(2026, 5, 11, 1, 0, tzinfo=UTC)


def one_day_lag_now() -> datetime:
    return datetime(2026, 5, 11, 15, 0, tzinfo=UTC)


def stale_observed_now() -> datetime:
    return datetime(2026, 5, 14, 15, 0, tzinfo=UTC)


def _copy_ready_export(
    tmp_path: Path,
    *,
    synced_at: str = "2026-05-11T01:00:00+00:00",
    ticker_mapping_coverage_ratio: float | None = 0.9,
    sync_quality_status: str = "ok",
    sync_quality_warnings: list[dict[str, object]] | None = None,
    sync_quality_risk_failures: list[dict[str, object]] | None = None,
) -> Path:
    destination = tmp_path / "operational_holdings"
    shutil.copytree(OPERATIONAL_NORMALIZED_ROOT, destination)
    manifest_path = destination / "url_holdings_cumulative.json"
    manifest = _read_json(manifest_path)
    manifest["synced_at"] = synced_at
    manifest_dates = manifest["dates"]
    manifest_partitions = manifest["partitions"]
    manifest_record_count = manifest["record_count"]
    assert isinstance(manifest_dates, list)
    assert isinstance(manifest_partitions, dict)
    assert isinstance(manifest_record_count, int)
    _write_json(manifest_path, manifest)
    metadata_path = destination / "sync_metadata.json"
    metadata = _read_json(metadata_path)
    metadata.update(
        {
            "copied_dates": manifest_dates,
            "copied_partition_count": len(manifest_partitions),
            "copied_record_count": manifest_record_count,
            "synced_at": synced_at,
            "mapped_security_count": 9 if ticker_mapping_coverage_ratio is not None else 0,
            "unmapped_security_count": 1 if ticker_mapping_coverage_ratio is not None else 0,
            "unmapped_security_samples": [
                {
                    "security_id": "SEC_A",
                    "name": "Alpha Corp.",
                    "observed_row_count": 5,
                    "observed_etf_count": 3,
                    "observed_date_count": 2,
                    "name_alias_count": 0,
                    "source_file_used": "must-not-leak.jsonl",
                },
                {
                    "security_id": "SEC_B",
                    "name": "Beta Corp.",
                    "observed_row_count": 4,
                    "observed_etf_count": 2,
                    "observed_date_count": 2,
                    "name_alias_count": 1,
                },
                {
                    "security_id": "SEC_C",
                    "name": "Gamma Corp.",
                    "observed_row_count": 3,
                    "observed_etf_count": 2,
                    "observed_date_count": 1,
                    "name_alias_count": 0,
                },
                {
                    "security_id": "SEC_D",
                    "name": "Delta Corp.",
                    "observed_row_count": 2,
                    "observed_etf_count": 1,
                    "observed_date_count": 1,
                    "name_alias_count": 0,
                },
            ],
            "sync_quality": {
                "schema_version": "agent_treport.operational_holdings.sync_quality.v1",
                "status": sync_quality_status,
                "metrics": {
                    "ticker_mapping_coverage_ratio": ticker_mapping_coverage_ratio,
                    "mapped_security_count": (
                        9 if ticker_mapping_coverage_ratio is not None else 0
                    ),
                    "unmapped_security_count": (
                        1 if ticker_mapping_coverage_ratio is not None else 0
                    ),
                    "missing_source_date_count": 0,
                },
                "warnings": sync_quality_warnings or [],
                "risk_failures": sync_quality_risk_failures or [],
            },
        }
    )
    _write_json(metadata_path, metadata)
    return manifest_path


def _read_json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_native_collection_fixture(
    tmp_path: Path,
    *,
    quality_warnings: list[dict[str, object]] | None = None,
) -> Path:
    fixture_path = tmp_path / "native_fixture.json"
    fixture_path.write_text(
        json.dumps(
            {
                "schema_version": "agent_treport.native_holdings.fixture.v1",
                "brands": [
                    {
                        "brand_id": "brand_alpha",
                        "name": "Alpha Asset Management",
                    }
                ],
                "etf_universe": [
                    {
                        "etf_id": "etf_focus_ai",
                        "etf_name": "AI Native Collection ETF",
                        "brand_id": "brand_alpha",
                        "source_provider_id": "provider_native_fixture",
                    }
                ],
                "snapshots": [
                    {
                        "as_of_date": "2026-05-11",
                        "holdings": [
                            {
                                "etf_id": "etf_focus_ai",
                                "security_id": "sec_nvda",
                                "ticker": "NVDA",
                                "name": "NVIDIA Corp.",
                                "market": "US",
                                "sector": "Information Technology",
                                "theme": "AI infrastructure",
                                "country": "US",
                                "weight_percent": 7.5,
                                "shares": 1500,
                                "market_value_krw": 240000000,
                                "price_krw": 160000,
                                "is_cash": False,
                                "security_classification": "ticker_candidate",
                            },
                            {
                                "etf_id": "etf_focus_ai",
                                "security_id": "sec_cash",
                                "ticker": None,
                                "name": "Cash",
                                "market": None,
                                "sector": "Cash",
                                "theme": "Cash",
                                "country": None,
                                "weight_percent": 2.0,
                                "shares": None,
                                "market_value_krw": 32000000,
                                "price_krw": None,
                                "is_cash": True,
                                "security_classification": "cash_like",
                            },
                        ],
                    },
                    {
                        "as_of_date": "2026-05-08",
                        "holdings": [
                            {
                                "etf_id": "etf_focus_ai",
                                "security_id": "sec_nvda",
                                "ticker": "NVDA",
                                "name": "NVIDIA Corp.",
                                "market": "US",
                                "sector": "Information Technology",
                                "theme": "AI infrastructure",
                                "country": "US",
                                "weight_percent": 6.0,
                                "shares": 1200,
                                "market_value_krw": 180000000,
                                "price_krw": 150000,
                                "is_cash": False,
                                "security_classification": "ticker_candidate",
                            }
                        ],
                    },
                ],
                "quality_warnings": quality_warnings or [],
                "limitations": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return fixture_path


def _write_native_history_comparison_export(
    tmp_path: Path,
    *,
    active_etf_ids: list[str],
    current_etf_ids: list[str],
    previous_etf_ids: list[str],
) -> Path:
    export_dir = tmp_path / "native_history_export"
    parts_dir = export_dir / "url_holdings_cumulative.json.parts"
    parts_dir.mkdir(parents=True)
    dates = ["2026-05-11", "2026-05-08"]
    rows_by_date = {
        "2026-05-11": [
            _native_history_row(etf_id=etf_id, partition_date="2026-05-11")
            for etf_id in current_etf_ids
        ],
        "2026-05-08": [
            _native_history_row(etf_id=etf_id, partition_date="2026-05-08")
            for etf_id in previous_etf_ids
        ],
    }
    partitions: dict[str, dict[str, object]] = {}
    for partition_date, rows in rows_by_date.items():
        partition_path = parts_dir / f"{partition_date}.jsonl"
        partition_path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )
        partitions[partition_date] = {
            "file": f"url_holdings_cumulative.json.parts/{partition_date}.jsonl",
            "record_count": len(rows),
        }
    manifest_path = export_dir / "url_holdings_cumulative.json"
    manifest = {
        "schema_version": "agent_treport.operational_holdings.v1",
        "storage_format": "normalized_partitioned_jsonl_v1",
        "collection_source_type": "native_history",
        "collected_at": "2026-05-11T01:00:00+00:00",
        "dates": dates,
        "record_count": sum(len(rows) for rows in rows_by_date.values()),
        "partitions": partitions,
    }
    _write_json(manifest_path, manifest)
    complete_active_ids = sorted(
        set(active_etf_ids) & set(current_etf_ids) & set(previous_etf_ids)
    )
    missing_active_ids = sorted(set(active_etf_ids) - set(complete_active_ids))
    coverage_ratio = round(len(complete_active_ids) / len(active_etf_ids), 6)
    summary = {
        "schema_version": "agent_treport.native_collection.summary.v1",
        "collection_source_type": "native_history",
        "collected_at": "2026-05-11T01:00:00+00:00",
        "requested_observed_partitions": 2,
        "observed_dates": dates,
        "etf_count": len(active_etf_ids),
        "brand_count": 1,
        "partition_count": len(partitions),
        "row_count": manifest["record_count"],
        "quality_warnings": [],
        "limitations": [],
        "active_etf_coverage": {
            "selected_current_date": "2026-05-11",
            "selected_previous_date": "2026-05-08",
            "active_etf_count": len(active_etf_ids),
            "complete_active_etf_count": len(complete_active_ids),
            "missing_active_etf_ids": missing_active_ids,
            "coverage_ratio": coverage_ratio,
        },
        "security_coverage": {
            "security_resolution_available": True,
            "mapped_ticker_candidate_count": 9,
            "unresolved_ticker_candidate_count": 1,
            "unknown_count": 0,
            "non_ticker_excluded_count": 0,
            "reviewed_mapping_applied_count": 0,
            "reviewed_exclusion_applied_count": 0,
            "ticker_mapping_coverage_ratio": 0.9,
            "recovery_sample_count": 1,
            "recovery_samples": [],
        },
        "normalized_output": {
            "manifest_path": "url_holdings_cumulative.json",
            "fingerprint": compute_operational_export_fingerprint(manifest_path),
        },
    }
    _write_json(export_dir / "collection_summary.json", summary)
    return manifest_path


def _set_native_history_security_coverage(
    manifest_path: Path,
    *,
    security_resolution_available: bool = True,
    mapped_ticker_candidate_count: int = 9,
    unresolved_ticker_candidate_count: int = 1,
    unknown_count: int = 0,
    non_ticker_excluded_count: int = 0,
    reviewed_mapping_applied_count: int = 0,
    reviewed_exclusion_applied_count: int = 0,
) -> None:
    denominator = mapped_ticker_candidate_count + unresolved_ticker_candidate_count
    ratio = round(mapped_ticker_candidate_count / denominator, 6) if denominator else None
    summary_path = manifest_path.parent / "collection_summary.json"
    summary = _read_json(summary_path)
    summary["security_coverage"] = {
        "security_resolution_available": security_resolution_available,
        "mapped_ticker_candidate_count": mapped_ticker_candidate_count,
        "unresolved_ticker_candidate_count": unresolved_ticker_candidate_count,
        "unknown_count": unknown_count,
        "non_ticker_excluded_count": non_ticker_excluded_count,
        "reviewed_mapping_applied_count": reviewed_mapping_applied_count,
        "reviewed_exclusion_applied_count": reviewed_exclusion_applied_count,
        "ticker_mapping_coverage_ratio": ratio,
        "recovery_sample_count": unresolved_ticker_candidate_count + unknown_count,
        "recovery_samples": [],
    }
    _write_json(summary_path, summary)


def _native_history_row(*, etf_id: str, partition_date: str) -> dict[str, object]:
    suffix = etf_id.rsplit("_", 1)[-1]
    return {
        "etf_id": etf_id,
        "etf_name": f"Tracked ETF {suffix}",
        "brand_id": "brand_alpha",
        "source_provider_id": "provider_native_history",
        "as_of_date": partition_date,
        "security_id": f"sec_{suffix}",
        "ticker": f"T{suffix}".upper(),
        "name": f"Security {suffix}",
        "market": "US",
        "sector": "Information Technology",
        "theme": "AI infrastructure",
        "country": "US",
        "weight_percent": 10.0,
        "shares": 1.0,
        "market_value_krw": 100.0,
        "price_krw": 100.0,
        "is_cash": False,
        "security_classification": "ticker_candidate",
    }


def _codes(items: object) -> list[str]:
    assert isinstance(items, list)
    return [str(item["code"]) for item in items if isinstance(item, dict)]


def test_missing_holdings_manifest_fails_before_readiness_json(tmp_path: Path) -> None:
    holdings_path = tmp_path / "missing" / "url_holdings_cumulative.json"

    with pytest.raises(
        OperationalReadinessInputError,
        match=(
            "operational export fingerprint could not be computed: "
            "manifest file could not be read"
        ),
    ):
        check_operational_run_readiness(
            holdings_path=holdings_path,
            focus_etf_id="etf_focus_ai",
            now=fixed_now,
        )


def test_not_normalized_source_manifest_fails_before_readiness_json() -> None:
    with pytest.raises(
        OperationalReadinessInputError,
        match=(
            "operational export fingerprint could not be computed: "
            "invalid copied export schema"
        ),
    ):
        check_operational_run_readiness(
            holdings_path=OPERATIONAL_SOURCE_MANIFEST,
            focus_etf_id="etf_focus_ai",
            now=same_day_now,
        )


def test_valid_json_with_invalid_manifest_contract_returns_failed_code(
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "url_holdings_cumulative.json"
    manifest_path.write_text("[]", encoding="utf-8")

    with pytest.raises(
        OperationalReadinessInputError,
        match=(
            "operational export fingerprint could not be computed: "
            "manifest input must be a JSON object"
        ),
    ):
        check_operational_run_readiness(
            holdings_path=manifest_path,
            focus_etf_id="etf_focus_ai",
            now=same_day_now,
        )


def test_fresh_same_day_export_with_focus_current_and_previous_is_ready(
    tmp_path: Path,
) -> None:
    manifest_path = _copy_ready_export(tmp_path)

    result = check_operational_run_readiness(
        holdings_path=manifest_path,
        focus_etf_id="etf_focus_ai",
        now=same_day_now,
    )

    assert result["status"] == "ready"
    assert result["user_ready_allowed"] is True
    assert result["latest_observed_date"] == "2026-05-11"
    assert result["latest_observed_age_days"] == 0
    assert result["synced_at"] == "2026-05-11T01:00:00+00:00"
    assert result["current_date"] == "2026-05-11"
    assert result["previous_date"] == "2026-05-08"
    assert result["export_fingerprint"] == compute_operational_export_fingerprint(
        manifest_path
    )
    assert result["scanned_dates"] == ["2026-05-11", "2026-05-08"]
    assert result["missing_partition_dates"] == []
    assert result["reasons"] == []
    assert result["warnings"] == []
    assert _codes(result["next_actions"]) == ["run_report"]
    assert result["summary"] == {
        "copied_record_count": 18,
        "copied_partition_count": 3,
        "source_record_count": 20,
        "mapped_security_count": 9,
        "unmapped_security_count": 1,
        "ticker_mapping_coverage_ratio": 0.9,
        "unmapped_security_sample_count": 4,
        "missing_source_date_count": 0,
        "missing_partition_date_count": 0,
        "sync_quality_status": "ok",
        "current_record_count": 12,
        "previous_record_count": 4,
        "current_focus_etf_record_count": 3,
        "previous_focus_etf_record_count": 2,
        "included_etf_count": 2,
    }


def test_native_collection_summary_can_satisfy_readiness_with_disclosure_warning(
    tmp_path: Path,
) -> None:
    fixture_path = _write_native_collection_fixture(
        tmp_path,
        quality_warnings=[
            {
                "code": "fixture_backed_collection",
                "message": "Native collection used fixture holdings only.",
                "metric": "collection_source_type",
                "value": "fixture",
                "threshold": "live_provider",
            }
        ],
    )
    summary = collect_holdings_fixture(
        fixture_path=fixture_path,
        dest_dir=tmp_path / "native_collected",
        observed_partitions=2,
        now=lambda: datetime(2026, 5, 11, 1, 0, tzinfo=UTC),
    )
    manifest_path = tmp_path / "native_collected" / "url_holdings_cumulative.json"

    result = check_operational_run_readiness(
        holdings_path=manifest_path,
        focus_etf_id="etf_focus_ai",
        observed_partitions=2,
        now=same_day_now,
    )

    assert not (manifest_path.parent / "sync_metadata.json").exists()
    assert result["status"] == "ready_with_warnings"
    assert result["user_ready_allowed"] is True
    assert result["readiness_evidence_type"] == "native_collection"
    assert result["collection_summary_path"] == "collection_summary.json"
    assert result["collected_at"] == "2026-05-11T01:00:00+00:00"
    assert result["current_date"] == "2026-05-11"
    assert result["previous_date"] == "2026-05-08"
    assert result["export_fingerprint"] == compute_operational_export_fingerprint(
        manifest_path
    )
    assert result["collection_summary"] == {
        "schema_version": "agent_treport.native_collection.summary.v1",
        "collection_source_type": "fixture",
        "requested_observed_partitions": 2,
        "observed_dates": ["2026-05-11", "2026-05-08"],
        "etf_count": 1,
        "brand_count": 1,
        "partition_count": 2,
        "row_count": 3,
        "normalized_output": summary["normalized_output"],
    }
    assert _codes(result["warnings"]) == ["fixture_backed_collection"]
    assert result["warnings"][0] == {
        "code": "fixture_backed_collection",
        "severity": "warning",
        "message": "Native collection used fixture holdings only.",
        "metric": "collection_source_type",
        "value": "fixture",
        "threshold": "live_provider",
    }
    assert _codes(result["next_actions"]) == ["review_warnings", "run_report"]


def test_native_collection_summary_without_warnings_is_ready(tmp_path: Path) -> None:
    fixture_path = _write_native_collection_fixture(tmp_path)
    collect_holdings_fixture(
        fixture_path=fixture_path,
        dest_dir=tmp_path / "native_collected",
        observed_partitions=2,
        now=lambda: datetime(2026, 5, 11, 1, 0, tzinfo=UTC),
    )
    manifest_path = tmp_path / "native_collected" / "url_holdings_cumulative.json"

    result = check_operational_run_readiness(
        holdings_path=manifest_path,
        focus_etf_id="etf_focus_ai",
        observed_partitions=2,
        now=same_day_now,
    )

    assert result["status"] == "ready"
    assert result["user_ready_allowed"] is True
    assert result["readiness_evidence_type"] == "native_collection"
    assert result["warnings"] == []
    assert result["reasons"] == []


def test_native_collection_missing_summary_is_hold(tmp_path: Path) -> None:
    fixture_path = _write_native_collection_fixture(tmp_path)
    collect_holdings_fixture(
        fixture_path=fixture_path,
        dest_dir=tmp_path / "native_collected",
        observed_partitions=2,
        now=lambda: datetime(2026, 5, 11, 1, 0, tzinfo=UTC),
    )
    manifest_path = tmp_path / "native_collected" / "url_holdings_cumulative.json"
    (manifest_path.parent / "collection_summary.json").unlink()

    result = check_operational_run_readiness(
        holdings_path=manifest_path,
        focus_etf_id="etf_focus_ai",
        observed_partitions=2,
        now=same_day_now,
    )

    assert result["status"] == "hold"
    assert result["user_ready_allowed"] is False
    assert _codes(result["reasons"]) == ["collection_summary_missing"]


def test_native_collection_summary_mismatch_is_failed(tmp_path: Path) -> None:
    fixture_path = _write_native_collection_fixture(tmp_path)
    collect_holdings_fixture(
        fixture_path=fixture_path,
        dest_dir=tmp_path / "native_collected",
        observed_partitions=2,
        now=lambda: datetime(2026, 5, 11, 1, 0, tzinfo=UTC),
    )
    manifest_path = tmp_path / "native_collected" / "url_holdings_cumulative.json"
    summary_path = manifest_path.parent / "collection_summary.json"
    summary = _read_json(summary_path)
    summary["row_count"] = 999
    _write_json(summary_path, summary)

    result = check_operational_run_readiness(
        holdings_path=manifest_path,
        focus_etf_id="etf_focus_ai",
        observed_partitions=2,
        now=same_day_now,
    )

    assert result["status"] == "failed"
    assert result["user_ready_allowed"] is False
    assert _codes(result["reasons"]) == ["collection_summary_mismatch"]


def test_native_history_non_focus_coverage_at_threshold_is_ready_with_warning(
    tmp_path: Path,
) -> None:
    active_etf_ids = [
        "etf_focus_ai",
        "etf_peer_1",
        "etf_peer_2",
        "etf_peer_3",
        "etf_peer_4",
        "etf_peer_5",
    ]
    manifest_path = _write_native_history_comparison_export(
        tmp_path,
        active_etf_ids=active_etf_ids,
        current_etf_ids=active_etf_ids,
        previous_etf_ids=[
            "etf_focus_ai",
            "etf_peer_1",
            "etf_peer_2",
            "etf_peer_3",
            "etf_peer_4",
        ],
    )

    result = check_operational_run_readiness(
        holdings_path=manifest_path,
        focus_etf_id="etf_focus_ai",
        observed_partitions=2,
        now=same_day_now,
    )

    assert result["status"] == "ready_with_warnings"
    assert result["user_ready_allowed"] is True
    assert result["readiness_evidence_type"] == "native_history"
    assert result["active_etf_coverage"] == {
        "selected_current_date": "2026-05-11",
        "selected_previous_date": "2026-05-08",
        "active_etf_count": 6,
        "complete_active_etf_count": 5,
        "missing_active_etf_ids": ["etf_peer_5"],
        "coverage_ratio": 0.833333,
        "non_focus_active_etf_count": 5,
        "non_focus_complete_etf_count": 4,
        "non_focus_coverage_ratio": 0.8,
    }
    assert _codes(result["warnings"]) == ["active_etf_coverage_gap"]
    assert result["warnings"][0]["metric"] == "non_focus_active_etf_coverage_ratio"
    assert result["warnings"][0]["value"] == 0.8
    assert result["warnings"][0]["threshold"] == 0.8
    assert result["warnings"][0]["details"] == {
        "selected_current_date": "2026-05-11",
        "selected_previous_date": "2026-05-08",
        "missing_active_etf_ids": ["etf_peer_5"],
    }
    assert result["reasons"] == []


def test_native_history_non_focus_coverage_below_threshold_is_warning(
    tmp_path: Path,
) -> None:
    active_etf_ids = [
        "etf_focus_ai",
        "etf_peer_1",
        "etf_peer_2",
        "etf_peer_3",
        "etf_peer_4",
        "etf_peer_5",
    ]
    manifest_path = _write_native_history_comparison_export(
        tmp_path,
        active_etf_ids=active_etf_ids,
        current_etf_ids=active_etf_ids,
        previous_etf_ids=[
            "etf_focus_ai",
            "etf_peer_1",
            "etf_peer_2",
            "etf_peer_3",
        ],
    )

    result = check_operational_run_readiness(
        holdings_path=manifest_path,
        focus_etf_id="etf_focus_ai",
        observed_partitions=2,
        now=same_day_now,
    )

    assert result["status"] == "ready_with_warnings"
    assert result["user_ready_allowed"] is True
    assert result["readiness_evidence_type"] == "native_history"
    assert result["reasons"] == []
    assert _codes(result["warnings"]) == ["active_etf_coverage_gap"]
    assert result["warnings"][0]["metric"] == "non_focus_active_etf_coverage_ratio"
    assert result["warnings"][0]["value"] == 0.6
    assert result["warnings"][0]["threshold"] == 0.8
    assert result["warnings"][0]["details"] == {
        "selected_current_date": "2026-05-11",
        "selected_previous_date": "2026-05-08",
        "missing_active_etf_ids": ["etf_peer_4", "etf_peer_5"],
    }


def test_native_history_ticker_coverage_below_threshold_is_hold(
    tmp_path: Path,
) -> None:
    manifest_path = _write_native_history_comparison_export(
        tmp_path,
        active_etf_ids=["etf_focus_ai"],
        current_etf_ids=["etf_focus_ai"],
        previous_etf_ids=["etf_focus_ai"],
    )
    _set_native_history_security_coverage(
        manifest_path,
        mapped_ticker_candidate_count=1,
        unresolved_ticker_candidate_count=3,
    )

    result = check_operational_run_readiness(
        holdings_path=manifest_path,
        focus_etf_id="etf_focus_ai",
        observed_partitions=2,
        now=same_day_now,
    )

    assert result["status"] == "hold"
    assert result["user_ready_allowed"] is False
    assert result["security_coverage"]["ticker_mapping_coverage_ratio"] == 0.25
    assert _codes(result["reasons"]) == ["low_ticker_mapping_coverage"]
    assert result["reasons"][0]["metric"] == "ticker_mapping_coverage_ratio"
    assert result["reasons"][0]["value"] == 0.25
    assert result["reasons"][0]["threshold"] == 0.5
    recovery_action = next(
        action for action in result["next_actions"] if action["code"] == "recover_ticker_mapping"
    )
    assert recovery_action["required"] is True
    assert "--collection-summary-path <collection_summary.json>" in recovery_action[
        "command_hint"
    ]


def test_native_history_ticker_coverage_warning_allows_run(
    tmp_path: Path,
) -> None:
    manifest_path = _write_native_history_comparison_export(
        tmp_path,
        active_etf_ids=["etf_focus_ai"],
        current_etf_ids=["etf_focus_ai"],
        previous_etf_ids=["etf_focus_ai"],
    )
    _set_native_history_security_coverage(
        manifest_path,
        mapped_ticker_candidate_count=3,
        unresolved_ticker_candidate_count=2,
    )

    result = check_operational_run_readiness(
        holdings_path=manifest_path,
        focus_etf_id="etf_focus_ai",
        observed_partitions=2,
        now=same_day_now,
    )

    assert result["status"] == "ready_with_warnings"
    assert result["user_ready_allowed"] is True
    assert _codes(result["warnings"]) == ["ticker_mapping_coverage_warning"]
    assert result["warnings"][0]["value"] == 0.6
    assert result["warnings"][0]["threshold"] == 0.8
    assert _codes(result["reasons"]) == []


def test_native_history_ticker_coverage_ok_without_other_security_warnings(
    tmp_path: Path,
) -> None:
    manifest_path = _write_native_history_comparison_export(
        tmp_path,
        active_etf_ids=["etf_focus_ai"],
        current_etf_ids=["etf_focus_ai"],
        previous_etf_ids=["etf_focus_ai"],
    )
    _set_native_history_security_coverage(
        manifest_path,
        mapped_ticker_candidate_count=8,
        unresolved_ticker_candidate_count=2,
    )

    result = check_operational_run_readiness(
        holdings_path=manifest_path,
        focus_etf_id="etf_focus_ai",
        observed_partitions=2,
        now=same_day_now,
    )

    assert result["status"] == "ready"
    assert result["warnings"] == []
    assert result["reasons"] == []


def test_focus_etf_set_with_three_eligible_focus_etfs_is_user_ready(
    tmp_path: Path,
) -> None:
    manifest_path = _write_native_history_comparison_export(
        tmp_path,
        active_etf_ids=[
            "etf_focus_ai",
            "etf_peer_1",
            "etf_peer_2",
            "etf_peer_3",
        ],
        current_etf_ids=[
            "etf_focus_ai",
            "etf_peer_1",
            "etf_peer_2",
            "etf_peer_3",
        ],
        previous_etf_ids=[
            "etf_focus_ai",
            "etf_peer_1",
            "etf_peer_2",
            "etf_peer_3",
        ],
    )

    result = check_operational_run_readiness(
        holdings_path=manifest_path,
        focus_etf_ids=["etf_focus_ai", "etf_peer_1", "etf_peer_2"],
        observed_partitions=2,
        now=same_day_now,
    )

    assert result["status"] == "ready"
    assert result["user_ready_allowed"] is True
    assert result["focus_etf_ids"] == ["etf_focus_ai", "etf_peer_1", "etf_peer_2"]
    assert result["focus_eligibility"] == {
        "minimum_eligible_focus_etf_count": 3,
        "eligible_focus_etf_count": 3,
        "eligible_focus_etf_ids": ["etf_focus_ai", "etf_peer_1", "etf_peer_2"],
        "ineligible_focus_etf_ids": [],
        "mixed_comparison_windows": False,
        "comparison_windows": [
            {
                "etf_id": "etf_focus_ai",
                "selected_current_date": "2026-05-11",
                "selected_previous_date": "2026-05-08",
            },
            {
                "etf_id": "etf_peer_1",
                "selected_current_date": "2026-05-11",
                "selected_previous_date": "2026-05-08",
            },
            {
                "etf_id": "etf_peer_2",
                "selected_current_date": "2026-05-11",
                "selected_previous_date": "2026-05-08",
            },
        ],
        "handoff_exclusions": [],
    }
    assert result["summary"]["eligible_focus_etf_count"] == 3
    assert result["summary"]["included_etf_count"] == 3
    assert result["reasons"] == []
    assert result["warnings"] == []


def test_focus_etf_set_with_fewer_than_three_eligible_focus_etfs_is_hold(
    tmp_path: Path,
) -> None:
    manifest_path = _write_native_history_comparison_export(
        tmp_path,
        active_etf_ids=["etf_focus_ai", "etf_peer_1", "etf_peer_2"],
        current_etf_ids=["etf_focus_ai", "etf_peer_1", "etf_peer_2"],
        previous_etf_ids=["etf_focus_ai", "etf_peer_1"],
    )

    result = check_operational_run_readiness(
        holdings_path=manifest_path,
        focus_etf_ids=["etf_focus_ai", "etf_peer_1", "etf_peer_2"],
        observed_partitions=2,
        now=same_day_now,
    )

    assert result["status"] == "hold"
    assert result["user_ready_allowed"] is False
    assert _codes(result["reasons"]) == ["insufficient_focus_etf_eligibility"]
    assert result["reasons"][0]["severity"] == "hold"
    assert result["reasons"][0]["metric"] == "eligible_focus_etf_count"
    assert result["reasons"][0]["value"] == 2
    assert result["reasons"][0]["threshold"] == 3
    assert result["focus_eligibility"]["eligible_focus_etf_ids"] == [
        "etf_focus_ai",
        "etf_peer_1",
    ]
    assert result["focus_eligibility"]["ineligible_focus_etf_ids"] == ["etf_peer_2"]
    rendered = json.dumps(result, ensure_ascii=False)
    assert "provider_etf_id" not in rendered
    assert "://" not in rendered
    assert str(tmp_path) not in rendered


def test_focus_etf_set_source_exclusion_evidence_is_path_safe_and_non_blocking(
    tmp_path: Path,
) -> None:
    manifest_path = _write_native_history_comparison_export(
        tmp_path,
        active_etf_ids=[
            "etf_focus_ai",
            "etf_peer_1",
            "etf_peer_2",
            "etf_hyundai_2912753",
        ],
        current_etf_ids=["etf_focus_ai", "etf_peer_1", "etf_peer_2"],
        previous_etf_ids=["etf_focus_ai", "etf_peer_1", "etf_peer_2"],
    )
    _write_json(
        manifest_path.parent / "source_acquisition_summary.json",
        {
            "schema_version": "agent_treport.source_acquisition.summary.v1",
            "source_provider_id": "hyundai",
            "target_outcomes": [
                {
                    "source_provider_id": "hyundai",
                    "provider_etf_id": "2912753",
                    "etf_id": "etf_hyundai_2912753",
                    "requested_date": "2026-05-11",
                    "observed_date": None,
                    "outcome": "failed",
                    "failure_code_class": "invalid_provider_payload",
                    "raw_url": "https://provider.example/raw",
                    "local_path": str(tmp_path / "raw.json"),
                }
            ],
        },
    )

    result = check_operational_run_readiness(
        holdings_path=manifest_path,
        focus_etf_ids=["etf_focus_ai", "etf_peer_1", "etf_peer_2"],
        observed_partitions=2,
        now=same_day_now,
    )

    assert result["status"] == "ready_with_warnings"
    assert result["user_ready_allowed"] is True
    assert result["focus_eligibility"]["handoff_exclusions"] == [
        {
            "source_provider_id": "hyundai",
            "etf_id": "etf_hyundai_2912753",
            "scope": "holdings_snapshot",
            "reason_code": "invalid_provider_payload",
            "observed_dates_missing": ["2026-05-11"],
        }
    ]
    assert _codes(result["warnings"]) == [
        "active_etf_coverage_gap",
        "handoff_exclusions_present",
    ]
    rendered = json.dumps(result, ensure_ascii=False)
    assert "provider_etf_id" not in rendered
    assert "https://provider.example/raw" not in rendered
    assert str(tmp_path) not in rendered


def test_native_history_unknown_security_count_warns(
    tmp_path: Path,
) -> None:
    manifest_path = _write_native_history_comparison_export(
        tmp_path,
        active_etf_ids=["etf_focus_ai"],
        current_etf_ids=["etf_focus_ai"],
        previous_etf_ids=["etf_focus_ai"],
    )
    _set_native_history_security_coverage(
        manifest_path,
        mapped_ticker_candidate_count=8,
        unresolved_ticker_candidate_count=0,
        unknown_count=1,
    )

    result = check_operational_run_readiness(
        holdings_path=manifest_path,
        focus_etf_id="etf_focus_ai",
        observed_partitions=2,
        now=same_day_now,
    )

    assert result["status"] == "ready_with_warnings"
    assert result["user_ready_allowed"] is True
    assert _codes(result["warnings"]) == ["unknown_security_classification"]
    assert result["warnings"][0]["metric"] == "unknown_count"
    assert result["warnings"][0]["value"] == 1
    assert result["warnings"][0]["threshold"] == 0


def test_native_history_missing_reviewed_security_resolution_warns(
    tmp_path: Path,
) -> None:
    manifest_path = _write_native_history_comparison_export(
        tmp_path,
        active_etf_ids=["etf_focus_ai"],
        current_etf_ids=["etf_focus_ai"],
        previous_etf_ids=["etf_focus_ai"],
    )
    _set_native_history_security_coverage(
        manifest_path,
        security_resolution_available=False,
        mapped_ticker_candidate_count=8,
        unresolved_ticker_candidate_count=0,
    )

    result = check_operational_run_readiness(
        holdings_path=manifest_path,
        focus_etf_id="etf_focus_ai",
        observed_partitions=2,
        now=same_day_now,
    )

    assert result["status"] == "ready_with_warnings"
    assert result["user_ready_allowed"] is True
    assert _codes(result["warnings"]) == ["security_resolution_missing"]
    assert result["warnings"][0]["metric"] == "security_resolution_available"
    assert result["warnings"][0]["value"] is False
    assert result["warnings"][0]["threshold"] is True


def test_native_history_missing_focus_current_snapshot_is_failed(
    tmp_path: Path,
) -> None:
    manifest_path = _write_native_history_comparison_export(
        tmp_path,
        active_etf_ids=["etf_focus_ai", "etf_peer_1"],
        current_etf_ids=["etf_peer_1"],
        previous_etf_ids=["etf_focus_ai", "etf_peer_1"],
    )

    result = check_operational_run_readiness(
        holdings_path=manifest_path,
        focus_etf_id="etf_focus_ai",
        observed_partitions=2,
        now=same_day_now,
    )

    assert result["status"] == "failed"
    assert result["user_ready_allowed"] is False
    assert result["current_date"] == "2026-05-11"
    assert result["previous_date"] == "2026-05-08"
    assert _codes(result["reasons"]) == ["focus_current_snapshot_not_found"]
    assert result["summary"]["current_focus_etf_record_count"] == 0
    assert result["summary"]["previous_focus_etf_record_count"] == 1


def test_native_history_readiness_ignores_source_acquisition_summary_operator_evidence(
    tmp_path: Path,
) -> None:
    manifest_path = _write_native_history_comparison_export(
        tmp_path,
        active_etf_ids=["etf_focus_ai"],
        current_etf_ids=["etf_focus_ai"],
        previous_etf_ids=["etf_focus_ai"],
    )
    _write_json(
        manifest_path.parent / "source_acquisition_summary.json",
        {
            "schema_version": "agent_treport.source_acquisition.summary.v1",
            "source_provider_id": "provider_kodex_fake",
            "target_outcomes": [
                {
                    "provider_etf_id": "2ETF35",
                    "outcome": "failed",
                    "failure_code_class": "provider_response",
                    "retry_attempt_count": 3,
                    "raw_url": "https://provider.example/raw",
                    "local_path": str(tmp_path / "raw.json"),
                }
            ],
        },
    )

    result = check_operational_run_readiness(
        holdings_path=manifest_path,
        focus_etf_id="etf_focus_ai",
        observed_partitions=2,
        now=same_day_now,
    )

    rendered = json.dumps(result, ensure_ascii=False)
    assert result["status"] == "ready"
    assert result["readiness_evidence_type"] == "native_history"
    assert result["collection_summary_path"] == "collection_summary.json"
    assert result["collection_summary"]["normalized_output"]["fingerprint"] == (
        result["export_fingerprint"]
    )
    assert "source_acquisition_summary" not in rendered
    assert "provider_etf_id" not in rendered
    assert "2ETF35" not in rendered
    assert "failure_code_class" not in rendered
    assert "retry_attempt_count" not in rendered
    assert "https://provider.example/raw" not in rendered
    assert str(tmp_path) not in rendered


def test_latest_observed_age_within_limit_is_ready_with_warning(
    tmp_path: Path,
) -> None:
    manifest_path = _copy_ready_export(
        tmp_path,
        synced_at="2026-05-11T15:00:00+00:00",
    )

    result = check_operational_run_readiness(
        holdings_path=manifest_path,
        focus_etf_id="etf_focus_ai",
        now=one_day_lag_now,
    )

    assert result["status"] == "ready_with_warnings"
    assert result["user_ready_allowed"] is True
    assert result["latest_observed_age_days"] == 1
    assert _codes(result["warnings"]) == ["observed_date_lag"]
    assert _codes(result["next_actions"]) == ["review_warnings", "run_report"]


def test_latest_observed_age_above_limit_is_hold(tmp_path: Path) -> None:
    manifest_path = _copy_ready_export(
        tmp_path,
        synced_at="2026-05-14T15:00:00+00:00",
    )

    result = check_operational_run_readiness(
        holdings_path=manifest_path,
        focus_etf_id="etf_focus_ai",
        now=stale_observed_now,
    )

    assert result["status"] == "hold"
    assert result["user_ready_allowed"] is False
    assert result["latest_observed_age_days"] == 4
    assert _codes(result["reasons"]) == ["observed_date_stale"]
    assert "run_report" not in _codes(result["next_actions"])


def test_missing_auto_discovered_sync_metadata_is_hold(tmp_path: Path) -> None:
    manifest_path = _copy_ready_export(tmp_path)
    (manifest_path.parent / "sync_metadata.json").unlink()

    result = check_operational_run_readiness(
        holdings_path=manifest_path,
        focus_etf_id="etf_focus_ai",
        now=same_day_now,
    )

    assert result["status"] == "hold"
    assert _codes(result["reasons"]) == ["sync_metadata_missing"]
    assert result["summary"]["sync_quality_status"] is None


def test_stale_sync_date_is_hold_even_when_observed_date_is_recent(
    tmp_path: Path,
) -> None:
    manifest_path = _copy_ready_export(
        tmp_path,
        synced_at="2026-05-11T01:00:00+00:00",
    )

    result = check_operational_run_readiness(
        holdings_path=manifest_path,
        focus_etf_id="etf_focus_ai",
        now=one_day_lag_now,
    )

    assert result["status"] == "hold"
    assert _codes(result["reasons"]) == ["sync_not_run_today"]
    assert _codes(result["warnings"]) == ["observed_date_lag"]


def test_manifest_metadata_mismatch_returns_failed(tmp_path: Path) -> None:
    manifest_path = _copy_ready_export(tmp_path)
    metadata_path = manifest_path.parent / "sync_metadata.json"
    metadata = _read_json(metadata_path)
    metadata["copied_record_count"] = 999
    _write_json(metadata_path, metadata)

    result = check_operational_run_readiness(
        holdings_path=manifest_path,
        focus_etf_id="etf_focus_ai",
        now=same_day_now,
    )

    assert result["status"] == "failed"
    assert _codes(result["reasons"]) == ["manifest_metadata_mismatch"]


def test_focus_etf_missing_returns_failed(tmp_path: Path) -> None:
    manifest_path = _copy_ready_export(tmp_path)

    result = check_operational_run_readiness(
        holdings_path=manifest_path,
        focus_etf_id="missing_etf",
        now=same_day_now,
    )

    assert result["status"] == "failed"
    assert _codes(result["reasons"]) == ["focus_etf_not_found"]


def test_previous_snapshot_missing_returns_failed(tmp_path: Path) -> None:
    manifest_path = _copy_ready_export(tmp_path)

    result = check_operational_run_readiness(
        holdings_path=manifest_path,
        focus_etf_id="etf_current_only",
        now=same_day_now,
    )

    assert result["status"] == "failed"
    assert _codes(result["reasons"]) == ["previous_snapshot_not_found"]


def test_missing_referenced_partition_fails_before_readiness_json(tmp_path: Path) -> None:
    manifest_path = _copy_ready_export(tmp_path)
    (manifest_path.parent / "url_holdings_cumulative.json.parts" / "2026-05-08.jsonl").unlink()

    with pytest.raises(
        OperationalReadinessInputError,
        match=(
            "operational export fingerprint could not be computed: "
            "partition file could not be read for date 2026-05-08"
        ),
    ):
        check_operational_run_readiness(
            holdings_path=manifest_path,
            focus_etf_id="etf_focus_ai",
            now=same_day_now,
        )


def test_low_mapping_coverage_is_hold_with_recovery_action(tmp_path: Path) -> None:
    manifest_path = _copy_ready_export(
        tmp_path,
        ticker_mapping_coverage_ratio=0.4,
        sync_quality_status="risk_failed",
        sync_quality_risk_failures=[
            {
                "code": "low_ticker_mapping_coverage",
                "message": "Ticker mapping coverage fell below threshold.",
                "metric": "ticker_mapping_coverage_ratio",
                "value": 0.4,
                "threshold": 0.5,
            }
        ],
    )

    result = check_operational_run_readiness(
        holdings_path=manifest_path,
        focus_etf_id="etf_focus_ai",
        now=same_day_now,
    )

    assert result["status"] == "hold"
    assert _codes(result["reasons"]) == [
        "sync_quality_risk_failed",
        "low_ticker_mapping_coverage",
    ]
    assert _codes(result["next_actions"]) == ["recover_ticker_mapping"]
    assert result["top_unmapped_security_samples"] == [
        {
            "security_id": "SEC_A",
            "name": "Alpha Corp.",
            "observed_row_count": 5,
            "observed_etf_count": 3,
            "observed_date_count": 2,
            "name_alias_count": 0,
        },
        {
            "security_id": "SEC_B",
            "name": "Beta Corp.",
            "observed_row_count": 4,
            "observed_etf_count": 2,
            "observed_date_count": 2,
            "name_alias_count": 1,
        },
        {
            "security_id": "SEC_C",
            "name": "Gamma Corp.",
            "observed_row_count": 3,
            "observed_etf_count": 2,
            "observed_date_count": 1,
            "name_alias_count": 0,
        },
    ]


def test_cash_derivation_risk_is_hold_with_cash_review_action(tmp_path: Path) -> None:
    manifest_path = _copy_ready_export(
        tmp_path,
        sync_quality_status="risk_failed",
        sync_quality_risk_failures=[
            {
                "code": "cash_derivation_failure_ratio",
                "message": "Cash derivation failure ratio crossed the risk threshold.",
                "metric": "cash_derivation_failure_ratio",
                "value": 0.25,
                "threshold": 0.2,
            }
        ],
    )

    result = check_operational_run_readiness(
        holdings_path=manifest_path,
        focus_etf_id="etf_focus_ai",
        now=same_day_now,
    )

    action_codes = _codes(result["next_actions"])
    assert result["status"] == "hold"
    assert action_codes == ["review_cash_derivation_risk"]
    assert "recover_ticker_mapping" not in action_codes
    assert result["next_actions"][0]["required"] is True
    assert result["next_actions"][0]["for_codes"] == ["cash_derivation_failure_ratio"]
    assert result["reasons"][0]["details"]["source_items"] == [
        {
            "code": "cash_derivation_failure_ratio",
            "message": "Cash derivation failure ratio crossed the risk threshold.",
            "metric": "cash_derivation_failure_ratio",
            "value": 0.25,
            "threshold": 0.2,
        }
    ]


def test_warning_mapping_coverage_allows_run_with_recommended_recovery(
    tmp_path: Path,
) -> None:
    manifest_path = _copy_ready_export(
        tmp_path,
        ticker_mapping_coverage_ratio=0.6,
        sync_quality_status="warning",
        sync_quality_warnings=[
            {
                "code": "low_ticker_mapping_coverage",
                "message": "Ticker mapping coverage fell below warning threshold.",
                "metric": "ticker_mapping_coverage_ratio",
                "value": 0.6,
                "threshold": 0.8,
            }
        ],
    )

    result = check_operational_run_readiness(
        holdings_path=manifest_path,
        focus_etf_id="etf_focus_ai",
        now=same_day_now,
    )

    assert result["status"] == "ready_with_warnings"
    assert _codes(result["warnings"]) == [
        "sync_quality_warning",
        "ticker_mapping_coverage_warning",
    ]
    assert _codes(result["next_actions"]) == [
        "review_warnings",
        "improve_ticker_mapping",
        "run_report",
    ]


def test_cash_derivation_warning_allows_run_with_cash_review_recommendation(
    tmp_path: Path,
) -> None:
    manifest_path = _copy_ready_export(
        tmp_path,
        sync_quality_status="warning",
        sync_quality_warnings=[
            {
                "code": "cash_derivation_failure_ratio",
                "message": "Cash derivation failure ratio reached the warning threshold.",
                "metric": "cash_derivation_failure_ratio",
                "value": 0.08,
                "threshold": 0.05,
            }
        ],
    )

    result = check_operational_run_readiness(
        holdings_path=manifest_path,
        focus_etf_id="etf_focus_ai",
        now=same_day_now,
    )

    action_codes = _codes(result["next_actions"])
    assert result["status"] == "ready_with_warnings"
    assert "review_cash_derivation_warning" in action_codes
    assert "recover_ticker_mapping" not in action_codes
    cash_action = next(
        action
        for action in result["next_actions"]
        if action["code"] == "review_cash_derivation_warning"
    )
    assert cash_action["required"] is False
    assert cash_action["for_codes"] == ["cash_derivation_failure_ratio"]
    assert result["warnings"][0]["details"]["source_items"] == [
        {
            "code": "cash_derivation_failure_ratio",
            "message": "Cash derivation failure ratio reached the warning threshold.",
            "metric": "cash_derivation_failure_ratio",
            "value": 0.08,
            "threshold": 0.05,
        }
    ]


def test_null_mapping_coverage_is_ready_with_warning(tmp_path: Path) -> None:
    manifest_path = _copy_ready_export(
        tmp_path,
        ticker_mapping_coverage_ratio=None,
    )

    result = check_operational_run_readiness(
        holdings_path=manifest_path,
        focus_etf_id="etf_focus_ai",
        now=same_day_now,
    )

    assert result["status"] == "ready_with_warnings"
    assert _codes(result["warnings"]) == ["ticker_mapping_coverage_unavailable"]
    assert result["summary"]["ticker_mapping_coverage_ratio"] is None


def test_invalid_partition_contract_returns_stable_failed_code(tmp_path: Path) -> None:
    manifest_path = _copy_ready_export(tmp_path)
    partition_path = (
        manifest_path.parent / "url_holdings_cumulative.json.parts" / "2026-05-11.jsonl"
    )
    rows = partition_path.read_text(encoding="utf-8").splitlines()
    first_row = json.loads(rows[0])
    del first_row["etf_id"]
    rows[0] = json.dumps(first_row, ensure_ascii=False)
    partition_path.write_text("\n".join(rows) + "\n", encoding="utf-8")

    result = check_operational_run_readiness(
        holdings_path=manifest_path,
        focus_etf_id="etf_focus_ai",
        now=same_day_now,
    )

    assert result["status"] == "failed"
    assert _codes(result["reasons"]) == ["invalid_partition_contract"]
