from __future__ import annotations

import json
import shutil
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_treport.signal_report import build_signal_report_payload
from agent_treport.signal_report.adapters.errors import SignalReportInputError
from agent_treport.signal_report.adapters.operational_holdings import (
    OperationalHoldingsInputError,
    OperationalSignalReportInputProvider,
    collect_holdings_fixture,
    compute_operational_export_fingerprint,
    export_latest_holdings_comparison,
    import_operational_holdings_export_to_history,
    load_operational_signal_report_inputs,
    merge_security_mapping_patch,
    sync_operational_holdings,
    update_holdings_history_fixture,
)
from agent_treport.signal_report.adapters.operational_universe import (
    collect_universe_fixture,
)
from agent_treport.signal_report.domain.security_resolution import (
    SecurityClassificationPolicy,
    validate_security_resolution_export,
)

FIXTURE_ROOT = Path(__file__).parent / "fixtures"
SOURCE_MANIFEST = FIXTURE_ROOT / "operational_holdings_source" / "url_holdings_cumulative.json"
NORMALIZED_MANIFEST = FIXTURE_ROOT / "operational_holdings" / "url_holdings_cumulative.json"
SECURITY_MAPPING = FIXTURE_ROOT / "security_mapping" / "security_mapping.json"
FIXTURE_EVIDENCE = (
    Path(__file__).parents[1]
    / "src"
    / "agent_treport"
    / "fixtures"
    / "signal_report"
    / "evidence.json"
)


def _jsonl_rows(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _write_source_export(
    tmp_path: Path,
    rows_by_date: Mapping[str, list[dict[str, object]]],
) -> Path:
    source_dir = tmp_path / "source"
    parts_dir = source_dir / "url_holdings_cumulative.json.parts"
    parts_dir.mkdir(parents=True)
    manifest_path = source_dir / "url_holdings_cumulative.json"
    partitions: dict[str, dict[str, object]] = {}
    for source_date, rows in rows_by_date.items():
        partition_file = parts_dir / f"{source_date}.jsonl"
        partition_file.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )
        partitions[source_date] = {
            "file": str(partition_file),
            "record_count": len(rows),
        }
    manifest_path.write_text(
        json.dumps(
            {
                "storage_format": "partitioned_jsonl_v2",
                "updated_at": "2026-05-11 21:33:33",
                "dates": list(rows_by_date),
                "record_count": sum(len(rows) for rows in rows_by_date.values()),
                "partitions": partitions,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return manifest_path


def _write_security_mapping(tmp_path: Path, mappings: Mapping[str, str]) -> Path:
    mapping_path = tmp_path / "security_mapping.json"
    mapping_path.write_text(
        json.dumps(
            {
                "schema_version": "agent_treport.security_mapping.v1",
                "mappings": [
                    {"security_id": security_id, "ticker": ticker}
                    for security_id, ticker in mappings.items()
                ],
            }
        ),
        encoding="utf-8",
    )
    return mapping_path


def _write_security_resolution(
    tmp_path: Path,
    *,
    mappings: list[dict[str, object]],
    exclusions: list[dict[str, object]],
) -> Path:
    resolution_path = tmp_path / "security_resolution.json"
    resolution_path.write_text(
        json.dumps(
            {
                "schema_version": "agent_treport.security_resolution_export.v1",
                "mappings": mappings,
                "exclusions": exclusions,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return resolution_path


def _write_native_collection_fixture(
    tmp_path: Path,
    *,
    quality_warnings: list[dict[str, object]] | None = None,
    limitations: list[dict[str, object]] | None = None,
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
                "limitations": limitations or [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return fixture_path


def _write_native_universe_fixture(
    tmp_path: Path,
    *,
    brands: list[dict[str, object]],
    etfs: list[dict[str, object]],
    filename: str = "native_universe_fixture.json",
) -> Path:
    fixture_path = tmp_path / filename
    fixture_path.write_text(
        json.dumps(
            {
                "schema_version": "agent_treport.native_universe.fixture.v1",
                "complete": True,
                "brands": brands,
                "etfs": etfs,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return fixture_path


def _source_holding(
    *,
    security_id: str = "sec_nvda",
    weight_percent: float = 7.5,
) -> dict[str, object]:
    return {
        "security_id": security_id,
        "ticker": "NVDA",
        "name": "NVIDIA Corp.",
        "market": "US",
        "sector": "Information Technology",
        "theme": "AI infrastructure",
        "country": "US",
        "weight_percent": weight_percent,
        "shares": 1500,
        "market_value_krw": 240000000,
        "price_krw": 160000,
        "is_cash": False,
        "security_classification": "ticker_candidate",
    }


def _write_fake_source_provider_fixture(
    tmp_path: Path,
    *,
    entries: list[dict[str, object]] | None = None,
    holdings_results: list[dict[str, object]] | None = None,
    filename: str = "source_provider_fixture.json",
) -> Path:
    fixture_path = tmp_path / filename
    fixture_path.write_text(
        json.dumps(
            {
                "schema_version": "agent_treport.source_provider.fake.v1",
                "source_provider_id": "provider_kodex_fake",
                "catalog": {
                    "complete": True,
                    "entries": entries
                    or [
                        {
                            "source_provider_id": "provider_kodex_fake",
                            "provider_etf_id": "2ETF35",
                            "etf_id": "etf_kodex_ai",
                            "etf_name": "KODEX AI ETF",
                            "brand_id": "brand_samsung",
                            "brand_name": "Samsung Asset Management",
                            "strategy_label": "active",
                            "locator": "https://provider.example/internal/2ETF35",
                        }
                    ],
                },
                "holdings_results": holdings_results
                or [
                    {
                        "source_provider_id": "provider_kodex_fake",
                        "provider_etf_id": "2ETF35",
                        "requested_date": "2026-05-11",
                        "observed_date": "2026-05-10",
                        "outcome": "fetched",
                        "retry_attempt_count": 0,
                        "holdings": [_source_holding()],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return fixture_path


def _write_holdings_snapshot_fixture(
    tmp_path: Path,
    *,
    etf_id: str = "etf_focus_ai",
    filename: str = "native_holdings_fixture.json",
) -> Path:
    fixture_path = tmp_path / filename
    fixture_path.write_text(
        json.dumps(
            {
                "schema_version": "agent_treport.native_holdings.fixture.v1",
                "snapshots": [
                    {
                        "as_of_date": "2026-05-11",
                        "holdings": [
                            {
                                "etf_id": etf_id,
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
                            }
                        ],
                    }
                ],
                "quality_warnings": [],
                "limitations": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return fixture_path


def _write_two_etf_holdings_fixture(
    tmp_path: Path,
    *,
    focus_current_weight: float = 7.5,
    peer_current_weight: float = 5.0,
    filename: str = "two_etf_holdings_fixture.json",
) -> Path:
    fixture_path = tmp_path / filename
    fixture_path.write_text(
        json.dumps(
            {
                "schema_version": "agent_treport.native_holdings.fixture.v1",
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
                                "weight_percent": focus_current_weight,
                                "shares": 1500,
                                "market_value_krw": 240000000,
                                "price_krw": 160000,
                                "is_cash": False,
                                "security_classification": "ticker_candidate",
                            },
                            {
                                "etf_id": "etf_peer_ai",
                                "security_id": "sec_msft",
                                "ticker": "MSFT",
                                "name": "Microsoft Corp.",
                                "market": "US",
                                "sector": "Information Technology",
                                "theme": "AI infrastructure",
                                "country": "US",
                                "weight_percent": peer_current_weight,
                                "shares": 1000,
                                "market_value_krw": 180000000,
                                "price_krw": 180000,
                                "is_cash": False,
                                "security_classification": "ticker_candidate",
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
                            },
                            {
                                "etf_id": "etf_peer_ai",
                                "security_id": "sec_msft",
                                "ticker": "MSFT",
                                "name": "Microsoft Corp.",
                                "market": "US",
                                "sector": "Information Technology",
                                "theme": "AI infrastructure",
                                "country": "US",
                                "weight_percent": 4.0,
                                "shares": 800,
                                "market_value_krw": 140000000,
                                "price_krw": 175000,
                                "is_cash": False,
                                "security_classification": "ticker_candidate",
                            },
                        ],
                    },
                ],
                "quality_warnings": [],
                "limitations": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return fixture_path


def _write_mixed_window_holdings_fixture(tmp_path: Path) -> Path:
    def holding(etf_id: str, security_id: str, weight: float) -> dict[str, object]:
        return {
            "etf_id": etf_id,
            "security_id": security_id,
            "ticker": security_id.removeprefix("sec_").upper(),
            "name": f"{security_id} Corp.",
            "market": "US",
            "sector": "Information Technology",
            "theme": "AI infrastructure",
            "country": "US",
            "weight_percent": weight,
            "shares": 1000,
            "market_value_krw": 100000000,
            "price_krw": 100000,
            "is_cash": False,
            "security_classification": "ticker_candidate",
        }

    fixture_path = tmp_path / "mixed_window_holdings_fixture.json"
    fixture_path.write_text(
        json.dumps(
            {
                "schema_version": "agent_treport.native_holdings.fixture.v1",
                "snapshots": [
                    {
                        "as_of_date": "2026-05-15",
                        "holdings": [
                            holding("etf_alpha", "sec_alpha", 7.5),
                            holding("etf_beta", "sec_beta", 6.0),
                        ],
                    },
                    {
                        "as_of_date": "2026-05-14",
                        "holdings": [
                            holding("etf_alpha", "sec_alpha", 7.0),
                            holding("etf_gamma", "sec_gamma", 4.0),
                        ],
                    },
                    {
                        "as_of_date": "2026-05-13",
                        "holdings": [
                            holding("etf_beta", "sec_beta", 5.5),
                            holding("etf_gamma", "sec_gamma", 3.5),
                        ],
                    },
                ],
                "quality_warnings": [],
                "limitations": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return fixture_path


def _write_security_coverage_holdings_fixture(tmp_path: Path) -> Path:
    rows = [
        {
            "security_id": "sec_mapped",
            "ticker": "MAP",
            "name": "Mapped Corp.",
            "is_cash": False,
            "security_classification": "ticker_candidate",
        },
        {
            "security_id": "sec_reviewed_mapping",
            "ticker": None,
            "name": "Reviewed Mapping Corp.",
            "is_cash": False,
            "security_classification": "ticker_candidate",
        },
        {
            "security_id": "sec_unmapped",
            "ticker": None,
            "name": "Unmapped Corp.",
            "is_cash": False,
            "security_classification": "ticker_candidate",
        },
        {
            "security_id": "sec_unknown",
            "ticker": None,
            "name": "Unknown Instrument",
            "is_cash": False,
            "security_classification": "unknown",
        },
        {
            "security_id": "sec_cash",
            "ticker": None,
            "name": "Cash",
            "is_cash": True,
            "security_classification": "cash_like",
        },
        {
            "security_id": "sec_reviewed_exclusion",
            "ticker": "BOND1",
            "name": "Reviewed Bond",
            "is_cash": False,
            "security_classification": "ticker_candidate",
        },
    ]
    fixture_path = tmp_path / "security_coverage_holdings_fixture.json"
    fixture_path.write_text(
        json.dumps(
            {
                "schema_version": "agent_treport.native_holdings.fixture.v1",
                "snapshots": [
                    {
                        "as_of_date": partition_date,
                        "holdings": [
                            {
                                "etf_id": "etf_focus_ai",
                                "market": "US",
                                "sector": None,
                                "theme": None,
                                "country": "US",
                                "weight_percent": 10.0,
                                "shares": 1.0,
                                "market_value_krw": 100.0,
                                "price_krw": 100.0,
                                **row,
                            }
                            for row in rows
                        ],
                    }
                    for partition_date in ("2026-05-11", "2026-05-08")
                ],
                "quality_warnings": [],
                "limitations": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return fixture_path


def _source_row(
    *,
    fund_id: str = "etf_cash_ops",
    as_of_date: str = "20260511",
    code: str | None,
    name: str,
    weight_pct: object,
    eval_amount_krw: object,
    quantity: object = "1",
    source_provider_id: str = "provider_fixture",
) -> dict[str, object]:
    return {
        "source_provider_id": source_provider_id,
        "brand_id": "brand_cash",
        "brand_name": "Cash Brand",
        "fund_id": fund_id,
        "fund_name": "Cash Operations ETF",
        "as_of_date": as_of_date,
        "code": code,
        "name": name,
        "quantity": quantity,
        "eval_amount_krw": eval_amount_krw,
        "weight_pct": weight_pct,
        "source_url": "https://example.com/source",
        "fetched_at": "2026-05-11T21:00:00+09:00",
    }


def _write_normalized_manifest(
    tmp_path: Path,
    rows_by_date: Mapping[str, list[dict[str, object]]],
) -> Path:
    export_dir = tmp_path / "normalized"
    parts_dir = export_dir / "url_holdings_cumulative.json.parts"
    parts_dir.mkdir(parents=True)
    manifest_path = export_dir / "url_holdings_cumulative.json"
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
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "agent_treport.operational_holdings.v1",
                "storage_format": "normalized_partitioned_jsonl_v1",
                "dates": list(rows_by_date),
                "record_count": sum(len(rows) for rows in rows_by_date.values()),
                "partitions": partitions,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return manifest_path


def _normalized_row(
    *,
    partition_date: str = "2026-05-11",
    security_id: str = "US67066G1040",
    ticker: str | None = "NVDA",
    is_cash: bool = False,
    security_classification: str | None = "ticker_candidate",
) -> dict[str, object]:
    row: dict[str, object] = {
        "etf_id": "etf_focus_ai",
        "etf_name": "AI Innovation Active ETF",
        "brand_id": "brand_alpha",
        "source_provider_id": "provider_operational_fixture",
        "as_of_date": partition_date,
        "security_id": security_id,
        "ticker": ticker,
        "name": "NVIDIA Corp.",
        "market": None,
        "sector": None,
        "theme": None,
        "country": None,
        "weight_percent": 100.0,
        "shares": 1.0,
        "market_value_krw": 100.0,
        "price_krw": None,
        "is_cash": is_cash,
    }
    if security_classification is not None:
        row["security_classification"] = security_classification
    return row


def _fingerprint_export_rows() -> dict[str, list[dict[str, object]]]:
    current = _normalized_row(
        partition_date="2026-05-11",
        security_id="SEC_A",
        ticker="AAA",
    )
    current.update(
        {
            "name": "Alpha Corp.",
            "weight_percent": 60.0,
            "shares": 3.0,
            "market_value_krw": 300.0,
        }
    )
    previous = _normalized_row(
        partition_date="2026-05-08",
        security_id="SEC_B",
        ticker="BBB",
    )
    previous.update(
        {
            "name": "Beta Corp.",
            "weight_percent": 40.0,
            "shares": 4.0,
            "market_value_krw": 200.0,
        }
    )
    return {
        "2026-05-11": [current],
        "2026-05-08": [previous],
    }


def _write_fingerprint_export(
    tmp_path: Path,
    *,
    rows_by_date: Mapping[str, list[dict[str, object]]] | None = None,
    reverse_manifest_key_order: bool = False,
    reverse_row_key_order: bool = False,
) -> Path:
    rows_by_date = rows_by_date or _fingerprint_export_rows()
    export_dir = tmp_path / "fingerprint_export"
    parts_dir = export_dir / "url_holdings_cumulative.json.parts"
    parts_dir.mkdir(parents=True)
    partitions: dict[str, dict[str, object]] = {}
    for partition_date, rows in rows_by_date.items():
        partition_path = parts_dir / f"{partition_date}.jsonl"
        output_rows = []
        for row in rows:
            if reverse_row_key_order:
                output_rows.append(dict(reversed(list(row.items()))))
            else:
                output_rows.append(row)
        partition_path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in output_rows),
            encoding="utf-8",
        )
        partitions[partition_date] = {
            "file": f"url_holdings_cumulative.json.parts/{partition_date}.jsonl",
            "record_count": len(rows),
        }
    manifest_items = [
        ("schema_version", "agent_treport.operational_holdings.v1"),
        ("storage_format", "normalized_partitioned_jsonl_v1"),
        ("source_storage_format", "partitioned_jsonl_v2"),
        ("source_updated_at", "2026-05-11 21:33:33"),
        ("synced_at", "2026-05-11T01:00:00+00:00"),
        ("dates", list(rows_by_date)),
        ("record_count", sum(len(rows) for rows in rows_by_date.values())),
        ("partitions", partitions),
    ]
    manifest = (
        dict(reversed(manifest_items))
        if reverse_manifest_key_order
        else dict(manifest_items)
    )
    manifest_path = export_dir / "url_holdings_cumulative.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest_path


def test_operational_export_fingerprint_returns_exact_sha256_for_copied_export(
    tmp_path: Path,
) -> None:
    manifest_path = _write_fingerprint_export(tmp_path)

    fingerprint = compute_operational_export_fingerprint(manifest_path)

    assert fingerprint == {
        "algorithm": "sha256",
        "scope": "copied_manifest_and_referenced_partitions_v1",
        "value": "6b146a89a0f050eda4ea80708d6197c5f0dfa373f81df68f61330dec2f40993d",
    }


def test_operational_export_fingerprint_canonicalizes_json_key_order_and_whitespace(
    tmp_path: Path,
) -> None:
    first = _write_fingerprint_export(tmp_path / "first")
    second = _write_fingerprint_export(
        tmp_path / "second",
        reverse_manifest_key_order=True,
        reverse_row_key_order=True,
    )

    assert compute_operational_export_fingerprint(first) == (
        compute_operational_export_fingerprint(second)
    )


def test_operational_export_fingerprint_changes_when_partition_content_changes(
    tmp_path: Path,
) -> None:
    manifest_path = _write_fingerprint_export(tmp_path)
    before = compute_operational_export_fingerprint(manifest_path)
    partition_path = (
        manifest_path.parent / "url_holdings_cumulative.json.parts" / "2026-05-11.jsonl"
    )
    row = json.loads(partition_path.read_text(encoding="utf-8").splitlines()[0])
    row["weight_percent"] = 61.0
    partition_path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")

    after = compute_operational_export_fingerprint(manifest_path)

    assert after["value"] != before["value"]


def test_operational_export_fingerprint_changes_when_manifest_metadata_changes(
    tmp_path: Path,
) -> None:
    manifest_path = _write_fingerprint_export(tmp_path)
    before = compute_operational_export_fingerprint(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["synced_at"] = "2026-05-11T02:00:00+00:00"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    after = compute_operational_export_fingerprint(manifest_path)

    assert after["value"] != before["value"]


def test_security_classification_policy_marks_short_maturity_cash_equivalent_cash_like() -> None:
    classification = SecurityClassificationPolicy().classify(
        security_id="CP20260731",
        name="Commercial Paper maturity 2026-07-31",
        as_of_date="2026-05-14",
        maturity_date="2026-07-31",
    )

    assert classification == "cash_like"


def test_security_classification_policy_marks_maturity_unknown_bond_non_equity() -> None:
    classification = SecurityClassificationPolicy().classify(
        security_id="BOND_UNKNOWN",
        name="Corporate Bond",
        as_of_date="2026-05-14",
    )

    assert classification == "non_equity"


def test_security_classification_policy_marks_futures_abbreviation_non_equity() -> None:
    classification = SecurityClassificationPolicy().classify(
        security_id="ESM6 Index",
        name="S&P500 EMINI FUT JUN 2026",
        as_of_date="2026-05-14",
    )

    assert classification == "non_equity"


def test_collect_holdings_fixture_writes_native_collection_export_without_source_manifest(
    tmp_path: Path,
) -> None:
    fixture_path = _write_native_collection_fixture(
        tmp_path,
        limitations=[
            {
                "code": "fixture_backed_collection",
                "message": "Native collection used fixture holdings only.",
            }
        ],
    )
    dest_dir = tmp_path / "native_collected"

    summary = collect_holdings_fixture(
        fixture_path=fixture_path,
        dest_dir=dest_dir,
        observed_partitions=2,
        now=lambda: datetime(2026, 5, 11, 1, 30, tzinfo=UTC),
    )

    manifest_path = dest_dir / "url_holdings_cumulative.json"
    summary_path = dest_dir / "collection_summary.json"
    latest_partition = dest_dir / "url_holdings_cumulative.json.parts" / "2026-05-11.jsonl"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    persisted_summary = json.loads(summary_path.read_text(encoding="utf-8"))
    latest_rows = _jsonl_rows(latest_partition)

    assert summary == persisted_summary
    assert not (dest_dir / "sync_metadata.json").exists()
    assert manifest["schema_version"] == "agent_treport.operational_holdings.v1"
    assert manifest["storage_format"] == "normalized_partitioned_jsonl_v1"
    assert manifest["dates"] == ["2026-05-11", "2026-05-08"]
    assert manifest["record_count"] == 3
    assert manifest["partitions"]["2026-05-11"] == {
        "file": "url_holdings_cumulative.json.parts/2026-05-11.jsonl",
        "record_count": 2,
    }
    assert latest_rows[0] == {
        "etf_id": "etf_focus_ai",
        "etf_name": "AI Native Collection ETF",
        "brand_id": "brand_alpha",
        "source_provider_id": "provider_native_fixture",
        "as_of_date": "2026-05-11",
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
    }
    assert summary["schema_version"] == "agent_treport.native_collection.summary.v1"
    assert summary["collection_source_type"] == "fixture"
    assert summary["collected_at"] == "2026-05-11T01:30:00+00:00"
    assert summary["requested_observed_partitions"] == 2
    assert summary["observed_dates"] == ["2026-05-11", "2026-05-08"]
    assert summary["etf_count"] == 1
    assert summary["brand_count"] == 1
    assert summary["partition_count"] == 2
    assert summary["row_count"] == 3
    assert summary["quality_warnings"] == []
    assert summary["limitations"] == [
        {
            "code": "fixture_backed_collection",
            "message": "Native collection used fixture holdings only.",
        }
    ]
    assert summary["normalized_output"] == {
        "manifest_path": "url_holdings_cumulative.json",
        "fingerprint": compute_operational_export_fingerprint(manifest_path),
    }
    rendered_summary = json.dumps(summary, ensure_ascii=False)
    assert str(fixture_path) not in rendered_summary
    assert "source_manifest" not in rendered_summary
    assert "http://" not in rendered_summary
    assert "https://" not in rendered_summary


def test_collect_holdings_fixture_consumes_active_universe_state(
    tmp_path: Path,
) -> None:
    universe_fixture_path = _write_native_universe_fixture(
        tmp_path,
        brands=[
            {
                "brand_id": "brand_alpha",
                "brand_name": "Alpha Asset Management",
                "source_provider_id": "provider_universe_fixture",
            }
        ],
        etfs=[
            {
                "etf_id": "etf_focus_ai",
                "etf_name": "Tracked AI ETF",
                "brand_id": "brand_alpha",
                "source_provider_id": "provider_universe_fixture",
            }
        ],
    )
    universe_dir = tmp_path / "native_universe"
    collect_universe_fixture(
        fixture_path=universe_fixture_path,
        dest_dir=universe_dir,
        now=lambda: datetime(2026, 5, 11, 0, 0, tzinfo=UTC),
    )
    holdings_fixture_path = _write_holdings_snapshot_fixture(tmp_path)
    dest_dir = tmp_path / "native_collected"

    collect_holdings_fixture(
        fixture_path=holdings_fixture_path,
        dest_dir=dest_dir,
        observed_partitions=1,
        universe_state_path=universe_dir / "universe_state.json",
        now=lambda: datetime(2026, 5, 11, 1, 30, tzinfo=UTC),
    )

    latest_rows = _jsonl_rows(
        dest_dir / "url_holdings_cumulative.json.parts" / "2026-05-11.jsonl"
    )
    assert latest_rows[0]["etf_id"] == "etf_focus_ai"
    assert latest_rows[0]["etf_name"] == "Tracked AI ETF"
    assert latest_rows[0]["brand_id"] == "brand_alpha"
    assert latest_rows[0]["source_provider_id"] == "provider_universe_fixture"


def test_collect_holdings_fixture_rejects_untracked_or_removed_universe_etf(
    tmp_path: Path,
) -> None:
    initial_universe_fixture_path = _write_native_universe_fixture(
        tmp_path,
        brands=[
            {
                "brand_id": "brand_alpha",
                "brand_name": "Alpha Asset Management",
                "source_provider_id": "provider_universe_fixture",
            },
            {
                "brand_id": "brand_beta",
                "brand_name": "Beta Asset Management",
                "source_provider_id": "provider_universe_fixture",
            },
        ],
        etfs=[
            {
                "etf_id": "etf_focus_ai",
                "etf_name": "Tracked AI ETF",
                "brand_id": "brand_alpha",
                "source_provider_id": "provider_universe_fixture",
            },
            {
                "etf_id": "etf_removed",
                "etf_name": "Removed Robotics ETF",
                "brand_id": "brand_beta",
                "source_provider_id": "provider_universe_fixture",
            },
        ],
    )
    removed_universe_fixture_path = _write_native_universe_fixture(
        tmp_path,
        filename="removed_universe_fixture.json",
        brands=[
            {
                "brand_id": "brand_alpha",
                "brand_name": "Alpha Asset Management",
                "source_provider_id": "provider_universe_fixture",
            }
        ],
        etfs=[
            {
                "etf_id": "etf_focus_ai",
                "etf_name": "Tracked AI ETF",
                "brand_id": "brand_alpha",
                "source_provider_id": "provider_universe_fixture",
            }
        ],
    )
    universe_dir = tmp_path / "native_universe"
    collect_universe_fixture(
        fixture_path=initial_universe_fixture_path,
        dest_dir=universe_dir,
        now=lambda: datetime(2026, 5, 11, 0, 0, tzinfo=UTC),
    )
    collect_universe_fixture(
        fixture_path=removed_universe_fixture_path,
        dest_dir=universe_dir,
        now=lambda: datetime(2026, 5, 12, 0, 0, tzinfo=UTC),
    )

    for etf_id in ("etf_missing", "etf_removed"):
        dest_dir = tmp_path / f"native_collected_{etf_id}"
        with pytest.raises(OperationalHoldingsInputError):
            collect_holdings_fixture(
                fixture_path=_write_holdings_snapshot_fixture(
                    tmp_path,
                    etf_id=etf_id,
                    filename=f"{etf_id}_holdings_fixture.json",
                ),
                dest_dir=dest_dir,
                observed_partitions=1,
                universe_state_path=universe_dir / "universe_state.json",
                now=lambda: datetime(2026, 5, 12, 1, 30, tzinfo=UTC),
            )

        assert not (dest_dir / "url_holdings_cumulative.json").exists()


def test_update_holdings_history_fixture_adds_empty_history_snapshots(
    tmp_path: Path,
) -> None:
    universe_fixture_path = _write_native_universe_fixture(
        tmp_path,
        brands=[
            {
                "brand_id": "brand_alpha",
                "brand_name": "Alpha Asset Management",
                "source_provider_id": "provider_universe_fixture",
            }
        ],
        etfs=[
            {
                "etf_id": "etf_focus_ai",
                "etf_name": "Tracked AI ETF",
                "brand_id": "brand_alpha",
                "source_provider_id": "provider_universe_fixture",
            }
        ],
    )
    universe_dir = tmp_path / "native_universe"
    collect_universe_fixture(
        fixture_path=universe_fixture_path,
        dest_dir=universe_dir,
        now=lambda: datetime(2026, 5, 11, 0, 0, tzinfo=UTC),
    )
    fixture_path = _write_native_collection_fixture(tmp_path)
    history_dir = tmp_path / "holdings_history"

    summary = update_holdings_history_fixture(
        fixture_path=fixture_path,
        universe_state_path=universe_dir / "universe_state.json",
        history_dir=history_dir,
        observed_partitions=2,
        now=lambda: datetime(2026, 5, 11, 1, 30, tzinfo=UTC),
    )

    manifest_path = history_dir / "holdings_history.json"
    latest_partition = history_dir / "holdings_history.json.parts" / "2026-05-11.jsonl"
    previous_partition = history_dir / "holdings_history.json.parts" / "2026-05-08.jsonl"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    latest_rows = _jsonl_rows(latest_partition)

    assert summary["schema_version"] == "agent_treport.native_holdings.history_update.v1"
    assert summary["history_store"] == {"manifest_path": "holdings_history.json"}
    assert summary["updated_at"] == "2026-05-11T01:30:00+00:00"
    assert summary["selected_active_etf_ids"] == ["etf_focus_ai"]
    assert summary["observed_dates"] == ["2026-05-11", "2026-05-08"]
    assert summary["added_snapshot_count"] == 2
    assert summary["skipped_snapshot_count"] == 0
    assert summary["refreshed_snapshot_count"] == 0
    assert summary["conflict_snapshot_count"] == 0
    assert summary["row_count"] == 3
    assert summary["added_snapshots"] == [
        {"etf_id": "etf_focus_ai", "observed_date": "2026-05-11", "row_count": 2},
        {"etf_id": "etf_focus_ai", "observed_date": "2026-05-08", "row_count": 1},
    ]
    assert manifest["schema_version"] == "agent_treport.native_holdings.history.v1"
    assert manifest["storage_format"] == "native_history_partitioned_jsonl_v1"
    assert manifest["dates"] == ["2026-05-11", "2026-05-08"]
    assert manifest["partitions"]["2026-05-11"] == {
        "file": "holdings_history.json.parts/2026-05-11.jsonl",
        "record_count": 2,
        "snapshot_count": 1,
    }
    assert previous_partition.is_file()
    assert latest_rows[0]["etf_id"] == "etf_focus_ai"
    assert latest_rows[0]["etf_name"] == "Tracked AI ETF"
    assert latest_rows[0]["source_provider_id"] == "provider_universe_fixture"
    rendered_summary = json.dumps(summary, ensure_ascii=False)
    assert str(tmp_path) not in rendered_summary
    assert str(fixture_path) not in rendered_summary
    assert "http://" not in rendered_summary
    assert "https://" not in rendered_summary


def test_update_holdings_history_source_writes_fake_fetched_snapshot(
    tmp_path: Path,
) -> None:
    source_module = __import__(
        "agent_treport.signal_report.adapters.source_acquisition",
        fromlist=[""],
    )
    provider = source_module.FakeSourceProvider.from_fixture_path(
        _write_fake_source_provider_fixture(tmp_path)
    )
    source_dir = tmp_path / "source_catalog"
    source_module.collect_source_catalog(
        provider=provider,
        dest_dir=source_dir,
        now=lambda: datetime(2026, 5, 11, 0, 45, tzinfo=UTC),
    )
    history_dir = tmp_path / "holdings_history"

    summary = source_module.update_holdings_history_source(
        provider=provider,
        source_catalog_path=source_dir / "source_catalog.json",
        universe_state_path=source_dir / "universe_state.json",
        history_dir=history_dir,
        requested_date="2026-05-11",
        now=lambda: datetime(2026, 5, 11, 1, 30, tzinfo=UTC),
    )

    manifest = json.loads((history_dir / "holdings_history.json").read_text(encoding="utf-8"))
    rows = _jsonl_rows(
        history_dir / "holdings_history.json.parts" / "2026-05-10.jsonl"
    )

    assert summary["schema_version"] == "agent_treport.source_acquisition.summary.v1"
    assert summary["source_provider_id"] == "provider_kodex_fake"
    assert summary["run_outcome"] == "succeeded"
    assert summary["history_store"] == {"manifest_path": "holdings_history.json"}
    assert summary["requested_dates"] == ["2026-05-11"]
    assert summary["observed_dates"] == ["2026-05-10"]
    assert summary["target_outcomes"] == [
        {
            "source_provider_id": "provider_kodex_fake",
            "brand_id": "brand_samsung",
            "etf_id": "etf_kodex_ai",
            "scope": "holdings_snapshot",
            "requested_date": "2026-05-11",
            "observed_date": "2026-05-10",
            "date_alignment": {
                "requested_date": "2026-05-11",
                "observed_date": "2026-05-10",
                "status": "observed_differs_from_provider_query",
            },
            "latest_upload_freshness": {
                "status": "fresh_latest",
                "observed_date": "2026-05-10",
                "latest_acceptable_observed_date": "2026-05-08",
            },
            "outcome": "fetched",
            "row_count": 1,
            "reason_code": None,
            "retry_attempt_count": 0,
        }
    ]
    assert summary["aggregate_counts"]["fetched"] == 1
    assert summary["aggregate_counts"]["failed"] == 0
    assert manifest["dates"] == ["2026-05-10"]
    assert rows == [
        {
            "as_of_date": "2026-05-10",
            "brand_id": "brand_samsung",
            "country": "US",
            "etf_id": "etf_kodex_ai",
            "etf_name": "KODEX AI ETF",
            "is_cash": False,
            "market": "US",
            "market_value_krw": 240000000.0,
            "name": "NVIDIA Corp.",
            "price_krw": 160000.0,
            "sector": "Information Technology",
            "security_classification": "ticker_candidate",
            "security_id": "sec_nvda",
            "shares": 1500.0,
            "source_provider_id": "provider_kodex_fake",
            "theme": "AI infrastructure",
            "ticker": "NVDA",
            "weight_percent": 7.5,
        }
    ]
    rendered_summary = json.dumps(summary, ensure_ascii=False)
    assert "sec_nvda" not in rendered_summary
    assert "NVIDIA" not in rendered_summary
    assert "https://" not in rendered_summary
    assert str(tmp_path) not in rendered_summary


def test_update_holdings_history_source_records_provider_query_date_alignment(
    tmp_path: Path,
) -> None:
    source_module = __import__(
        "agent_treport.signal_report.adapters.source_acquisition",
        fromlist=[""],
    )
    provider = source_module.FakeSourceProvider.from_fixture_path(
        _write_fake_source_provider_fixture(
            tmp_path,
            holdings_results=[
                {
                    "source_provider_id": "provider_kodex_fake",
                    "provider_etf_id": "2ETF35",
                    "requested_date": "2026-05-16",
                    "observed_date": "2026-05-15",
                    "outcome": "fetched",
                    "holdings": [_source_holding()],
                }
            ],
        )
    )
    source_dir = tmp_path / "source_catalog"
    source_module.collect_source_catalog(
        provider=provider,
        dest_dir=source_dir,
        now=lambda: datetime(2026, 5, 16, 0, 45, tzinfo=UTC),
    )

    summary = source_module.update_holdings_history_source(
        provider=provider,
        source_catalog_path=source_dir / "source_catalog.json",
        universe_state_path=source_dir / "universe_state.json",
        history_dir=tmp_path / "holdings_history",
        requested_date="2026-05-16",
        now=lambda: datetime(2026, 5, 16, 1, 30, tzinfo=UTC),
    )

    assert summary["requested_dates"] == ["2026-05-16"]
    assert summary["observed_dates"] == ["2026-05-15"]
    assert summary["target_outcomes"] == [
        {
            "source_provider_id": "provider_kodex_fake",
            "brand_id": "brand_samsung",
            "etf_id": "etf_kodex_ai",
            "scope": "holdings_snapshot",
            "requested_date": "2026-05-16",
            "observed_date": "2026-05-15",
            "date_alignment": {
                "requested_date": "2026-05-16",
                "observed_date": "2026-05-15",
                "status": "provider_query_adjusted",
            },
            "latest_upload_freshness": {
                "status": "fresh_latest",
                "observed_date": "2026-05-15",
                "latest_acceptable_observed_date": "2026-05-14",
            },
            "outcome": "fetched",
            "row_count": 1,
            "reason_code": None,
            "retry_attempt_count": 0,
        }
    ]
    rendered_summary = json.dumps(summary, ensure_ascii=False)
    assert "https://" not in rendered_summary
    assert str(tmp_path) not in rendered_summary


def test_update_holdings_history_source_accepts_observed_date_after_requested_date_as_fresh_latest(
    tmp_path: Path,
) -> None:
    source_module = __import__(
        "agent_treport.signal_report.adapters.source_acquisition",
        fromlist=[""],
    )
    provider = source_module.FakeSourceProvider.from_fixture_path(
        _write_fake_source_provider_fixture(
            tmp_path,
            holdings_results=[
                {
                    "source_provider_id": "provider_kodex_fake",
                    "provider_etf_id": "2ETF35",
                    "requested_date": "2026-05-11",
                    "observed_date": "2026-05-12",
                    "outcome": "fetched",
                    "holdings": [_source_holding()],
                }
            ],
        )
    )
    source_dir = tmp_path / "source_catalog"
    source_module.collect_source_catalog(
        provider=provider,
        dest_dir=source_dir,
        now=lambda: datetime(2026, 5, 11, 0, 45, tzinfo=UTC),
    )

    summary = source_module.update_holdings_history_source(
        provider=provider,
        source_catalog_path=source_dir / "source_catalog.json",
        universe_state_path=source_dir / "universe_state.json",
        history_dir=tmp_path / "holdings_history",
        requested_date="2026-05-11",
        now=lambda: datetime(2026, 5, 11, 1, 30, tzinfo=UTC),
    )

    assert summary["provider_rollout_status"] == "supported"
    assert summary["target_outcomes"][0]["latest_upload_freshness"] == {
        "status": "fresh_latest",
        "observed_date": "2026-05-12",
        "latest_acceptable_observed_date": "2026-05-08",
    }
    rendered_summary = json.dumps(summary, ensure_ascii=False)
    assert "https://" not in rendered_summary
    assert str(tmp_path) not in rendered_summary


def test_update_holdings_history_source_excludes_passive_and_unknown_targets(
    tmp_path: Path,
) -> None:
    source_module = __import__(
        "agent_treport.signal_report.adapters.source_acquisition",
        fromlist=[""],
    )
    provider = source_module.FakeSourceProvider.from_fixture_path(
        _write_fake_source_provider_fixture(
            tmp_path,
            entries=[
                {
                    "source_provider_id": "provider_kodex_fake",
                    "provider_etf_id": "2ETF35",
                    "etf_id": "etf_active_strategy",
                    "etf_name": "KODEX Active Strategy ETF",
                    "brand_id": "brand_samsung",
                    "brand_name": "Samsung Asset Management",
                    "strategy_label": "active",
                    "locator": "https://provider.example/internal/2ETF35",
                },
                {
                    "source_provider_id": "provider_kodex_fake",
                    "provider_etf_id": "2ETF36",
                    "etf_id": "etf_passive_strategy",
                    "etf_name": "KODEX Passive Strategy ETF",
                    "brand_id": "brand_samsung",
                    "brand_name": "Samsung Asset Management",
                    "strategy_label": "passive",
                    "locator": "https://provider.example/internal/2ETF36",
                },
                {
                    "source_provider_id": "provider_kodex_fake",
                    "provider_etf_id": "2ETF37",
                    "etf_id": "etf_unknown_strategy",
                    "etf_name": "KODEX Growth ETF",
                    "brand_id": "brand_samsung",
                    "brand_name": "Samsung Asset Management",
                    "locator": "https://provider.example/internal/2ETF37",
                },
            ],
            holdings_results=[
                {
                    "source_provider_id": "provider_kodex_fake",
                    "provider_etf_id": "2ETF35",
                    "requested_date": "2026-05-11",
                    "observed_date": "2026-05-10",
                    "outcome": "fetched",
                    "holdings": [_source_holding()],
                },
                {
                    "source_provider_id": "provider_kodex_fake",
                    "provider_etf_id": "2ETF36",
                    "requested_date": "2026-05-11",
                    "outcome": "failed",
                    "failure_code_class": "should_not_fetch_passive",
                },
                {
                    "source_provider_id": "provider_kodex_fake",
                    "provider_etf_id": "2ETF37",
                    "requested_date": "2026-05-11",
                    "outcome": "failed",
                    "failure_code_class": "should_not_fetch_unknown",
                },
            ],
        )
    )
    source_dir = tmp_path / "source_catalog"
    source_module.collect_source_catalog(
        provider=provider,
        dest_dir=source_dir,
        now=lambda: datetime(2026, 5, 11, 0, 45, tzinfo=UTC),
    )

    summary = source_module.update_holdings_history_source(
        provider=provider,
        source_catalog_path=source_dir / "source_catalog.json",
        universe_state_path=source_dir / "universe_state.json",
        history_dir=tmp_path / "holdings_history",
        requested_date="2026-05-11",
        now=lambda: datetime(2026, 5, 11, 1, 30, tzinfo=UTC),
    )

    assert summary["run_outcome"] == "succeeded"
    assert summary["aggregate_counts"]["target_count"] == 1
    assert summary["target_outcomes"][0]["etf_id"] == "etf_active_strategy"
    rendered_summary = json.dumps(summary, ensure_ascii=False)
    assert "should_not_fetch_passive" not in rendered_summary
    assert "should_not_fetch_unknown" not in rendered_summary
    assert "https://" not in rendered_summary
    assert str(tmp_path) not in rendered_summary


def test_update_holdings_history_source_rejects_unselected_refresh_snapshot(
    tmp_path: Path,
) -> None:
    source_module = __import__(
        "agent_treport.signal_report.adapters.source_acquisition",
        fromlist=[""],
    )
    provider = source_module.FakeSourceProvider.from_fixture_path(
        _write_fake_source_provider_fixture(tmp_path)
    )
    source_dir = tmp_path / "source_catalog"
    source_module.collect_source_catalog(
        provider=provider,
        dest_dir=source_dir,
        now=lambda: datetime(2026, 5, 11, 0, 45, tzinfo=UTC),
    )
    history_dir = tmp_path / "holdings_history"

    with pytest.raises(
        source_module.SourceAcquisitionInputError,
        match=(
            "refresh snapshot was not selected: "
            "etf_id=etf_kodex_ai observed_date=2026-05-09"
        ),
    ):
        source_module.update_holdings_history_source(
            provider=provider,
            source_catalog_path=source_dir / "source_catalog.json",
            universe_state_path=source_dir / "universe_state.json",
            history_dir=history_dir,
            requested_date="2026-05-11",
            refresh_snapshots={("etf_kodex_ai", "2026-05-09")},
            now=lambda: datetime(2026, 5, 11, 1, 30, tzinfo=UTC),
        )

    assert not (history_dir / "holdings_history.json").exists()


def test_update_holdings_history_source_skips_matching_existing_snapshot(
    tmp_path: Path,
) -> None:
    source_module = __import__(
        "agent_treport.signal_report.adapters.source_acquisition",
        fromlist=[""],
    )
    class CountingProvider(source_module.FakeSourceProvider):
        fetch_count = 0

        def fetch_holdings(self, target):  # type: ignore[no-untyped-def]
            self.fetch_count += 1
            return super().fetch_holdings(target)

    provider = CountingProvider.from_fixture_path(
        _write_fake_source_provider_fixture(
            tmp_path,
            holdings_results=[
                {
                    "source_provider_id": "provider_kodex_fake",
                    "provider_etf_id": "2ETF35",
                    "requested_date": "2026-05-11",
                    "observed_date": "2026-05-11",
                    "outcome": "fetched",
                    "retry_attempt_count": 0,
                    "holdings": [_source_holding()],
                }
            ],
        )
    )
    source_dir = tmp_path / "source_catalog"
    source_module.collect_source_catalog(
        provider=provider,
        dest_dir=source_dir,
        now=lambda: datetime(2026, 5, 11, 0, 45, tzinfo=UTC),
    )
    history_dir = tmp_path / "holdings_history"
    source_module.update_holdings_history_source(
        provider=provider,
        source_catalog_path=source_dir / "source_catalog.json",
        universe_state_path=source_dir / "universe_state.json",
        history_dir=history_dir,
        requested_date="2026-05-11",
        now=lambda: datetime(2026, 5, 11, 1, 30, tzinfo=UTC),
    )
    assert provider.fetch_count == 1
    history_before = (history_dir / "holdings_history.json").read_bytes()

    summary = source_module.update_holdings_history_source(
        provider=provider,
        source_catalog_path=source_dir / "source_catalog.json",
        universe_state_path=source_dir / "universe_state.json",
        history_dir=history_dir,
        requested_date="2026-05-11",
        now=lambda: datetime(2026, 5, 11, 2, 30, tzinfo=UTC),
    )

    assert provider.fetch_count == 1
    assert summary["run_outcome"] == "succeeded"
    assert summary["provider_rollout_status"] == "supported"
    assert summary["observed_dates"] == ["2026-05-11"]
    assert summary["aggregate_counts"] == {
        "target_count": 1,
        "fetched": 0,
        "skipped_existing": 1,
        "failed": 0,
        "rate_limited": 0,
        "unsupported": 0,
        "written_snapshot_count": 0,
        "row_count": 0,
    }
    assert summary["target_outcomes"] == [
        {
            "source_provider_id": "provider_kodex_fake",
            "brand_id": "brand_samsung",
            "etf_id": "etf_kodex_ai",
            "scope": "holdings_snapshot",
            "requested_date": "2026-05-11",
            "observed_date": "2026-05-11",
            "date_alignment": {
                "requested_date": "2026-05-11",
                "observed_date": "2026-05-11",
                "status": "matched",
            },
            "latest_upload_freshness": {
                "status": "fresh_latest",
                "observed_date": "2026-05-11",
                "latest_acceptable_observed_date": "2026-05-08",
            },
            "outcome": "skipped_existing",
            "row_count": 1,
            "reason_code": None,
            "retry_attempt_count": 0,
        }
    ]
    assert (history_dir / "holdings_history.json").read_bytes() == history_before


def test_update_holdings_history_source_partial_run_writes_success_and_records_failures(
    tmp_path: Path,
) -> None:
    source_module = __import__(
        "agent_treport.signal_report.adapters.source_acquisition",
        fromlist=[""],
    )
    entries: list[dict[str, object]] = [
        {
            "source_provider_id": "provider_kodex_fake",
            "provider_etf_id": provider_etf_id,
            "etf_id": etf_id,
            "etf_name": etf_name,
            "brand_id": "brand_samsung",
            "brand_name": "Samsung Asset Management",
            "strategy_label": "active",
            "locator": f"https://provider.example/internal/{provider_etf_id}",
        }
        for provider_etf_id, etf_id, etf_name in (
            ("2ETF35", "etf_kodex_ai", "KODEX AI ETF"),
            ("2ETF36", "etf_kodex_failed", "KODEX Failed ETF"),
            ("2ETF37", "etf_kodex_limited", "KODEX Limited ETF"),
            ("2ETF38", "etf_kodex_unsupported", "KODEX Unsupported ETF"),
        )
    ]
    holdings_results = [
        {
            "source_provider_id": "provider_kodex_fake",
            "provider_etf_id": "2ETF35",
            "requested_date": "2026-05-11",
            "observed_date": "2026-05-10",
            "outcome": "fetched",
            "holdings": [_source_holding()],
        },
        {
            "source_provider_id": "provider_kodex_fake",
            "provider_etf_id": "2ETF36",
            "requested_date": "2026-05-11",
            "outcome": "failed",
            "failure_code_class": "provider_response",
            "retry_attempt_count": 1,
        },
        {
            "source_provider_id": "provider_kodex_fake",
            "provider_etf_id": "2ETF37",
            "requested_date": "2026-05-11",
            "outcome": "rate_limited",
            "failure_code_class": "rate_limited",
            "retry_attempt_count": 1,
        },
        {
            "source_provider_id": "provider_kodex_fake",
            "provider_etf_id": "2ETF38",
            "requested_date": "2026-05-11",
            "outcome": "unsupported",
            "failure_code_class": "unsupported_target",
        },
    ]
    provider = source_module.FakeSourceProvider.from_fixture_path(
        _write_fake_source_provider_fixture(
            tmp_path,
            entries=entries,
            holdings_results=holdings_results,
        )
    )
    source_dir = tmp_path / "source_catalog"
    source_module.collect_source_catalog(
        provider=provider,
        dest_dir=source_dir,
        now=lambda: datetime(2026, 5, 11, 0, 45, tzinfo=UTC),
    )

    summary = source_module.update_holdings_history_source(
        provider=provider,
        source_catalog_path=source_dir / "source_catalog.json",
        universe_state_path=source_dir / "universe_state.json",
        history_dir=tmp_path / "holdings_history",
        requested_date="2026-05-11",
        now=lambda: datetime(2026, 5, 11, 1, 30, tzinfo=UTC),
    )

    outcomes = {
        item["etf_id"]: item
        for item in summary["target_outcomes"]
        if isinstance(item, dict)
    }
    assert summary["run_outcome"] == "partial"
    assert summary["observed_dates"] == ["2026-05-10"]
    assert summary["aggregate_counts"] == {
        "target_count": 4,
        "fetched": 1,
        "skipped_existing": 0,
        "failed": 1,
        "rate_limited": 1,
        "unsupported": 1,
        "written_snapshot_count": 1,
        "row_count": 1,
    }
    assert outcomes["etf_kodex_ai"]["outcome"] == "fetched"
    assert outcomes["etf_kodex_failed"]["reason_code"] == "provider_response"
    assert outcomes["etf_kodex_limited"]["outcome"] == "rate_limited"
    assert outcomes["etf_kodex_limited"]["retry_attempt_count"] == 1
    assert outcomes["etf_kodex_unsupported"]["outcome"] == "unsupported"
    assert (tmp_path / "holdings_history" / "holdings_history.json").is_file()

    rendered_summary = json.dumps(summary, ensure_ascii=False)
    assert "https://" not in rendered_summary
    assert "sec_nvda" not in rendered_summary
    assert "NVIDIA" not in rendered_summary


def test_update_holdings_history_source_failure_summary_is_path_safe(
    tmp_path: Path,
) -> None:
    from agent_treport.signal_report.adapters import source_acquisition as source_module

    fixture_path = _write_fake_source_provider_fixture(
        tmp_path,
        holdings_results=[
            {
                "source_provider_id": "provider_kodex_fake",
                "provider_etf_id": "2ETF35",
                "requested_date": "2026-05-11",
                "outcome": "failed",
                "failure_code_class": "provider_response",
                "retry_attempt_count": 2,
            }
        ],
    )
    source_dir = tmp_path / "source"
    history_dir = tmp_path / "history"
    provider = source_module.FakeSourceProvider.from_fixture_path(fixture_path)
    source_module.collect_source_catalog(
        provider=provider,
        dest_dir=source_dir,
        now=lambda: datetime(2026, 5, 11, 0, 45, tzinfo=UTC),
    )

    summary = source_module.update_holdings_history_source(
        provider=provider,
        source_catalog_path=source_dir / "source_catalog.json",
        universe_state_path=source_dir / "universe_state.json",
        history_dir=history_dir,
        requested_date="2026-05-11",
        now=lambda: datetime(2026, 5, 11, 1, 0, tzinfo=UTC),
    )

    assert summary["target_outcomes"] == [
        {
            "source_provider_id": "provider_kodex_fake",
            "brand_id": "brand_samsung",
            "etf_id": "etf_kodex_ai",
            "scope": "holdings_snapshot",
            "requested_date": "2026-05-11",
            "observed_date": None,
            "date_alignment": {
                "requested_date": "2026-05-11",
                "observed_date": None,
                "status": "missing_observed_date",
            },
            "latest_upload_freshness": {
                "status": "not_fetched",
                "observed_date": None,
                "latest_acceptable_observed_date": "2026-05-08",
            },
            "outcome": "failed",
            "row_count": 0,
            "reason_code": "provider_response",
            "retry_attempt_count": 2,
            "last_successful_observed_date": None,
            "observed_dates_missing": ["2026-05-11"],
            "next_backfill_date_count": 1,
            "blocked_until": "2026-05-12T01:00:00+00:00",
            "retry_after": "2026-05-12T01:00:00+00:00",
        }
    ]
    rendered = json.dumps(summary, ensure_ascii=False)
    for forbidden in (
        "provider_etf_id",
        "://",
        "endpoint",
        "headers",
        "credential",
        "response_body",
        "provider_envelope",
        "Traceback",
        str(tmp_path),
    ):
        assert forbidden not in rendered


def test_update_holdings_history_source_applies_retry_cooldown_and_backfill_plan(
    tmp_path: Path,
) -> None:
    from agent_treport.signal_report.adapters import source_acquisition as source_module

    failing_fixture = _write_fake_source_provider_fixture(
        tmp_path,
        holdings_results=[
            {
                "source_provider_id": "provider_kodex_fake",
                "provider_etf_id": "2ETF35",
                "requested_date": "2026-05-11",
                "outcome": "failed",
                "failure_code_class": "invalid_provider_payload",
                "retry_attempt_count": 3,
            }
        ],
        filename="failing_provider.json",
    )
    source_dir = tmp_path / "source"
    history_dir = tmp_path / "history"
    provider = source_module.FakeSourceProvider.from_fixture_path(failing_fixture)
    source_module.collect_source_catalog(
        provider=provider,
        dest_dir=source_dir,
        now=lambda: datetime(2026, 5, 11, 0, 45, tzinfo=UTC),
    )

    first_summary = source_module.update_holdings_history_source(
        provider=provider,
        source_catalog_path=source_dir / "source_catalog.json",
        universe_state_path=source_dir / "universe_state.json",
        history_dir=history_dir,
        requested_date="2026-05-11",
        now=lambda: datetime(2026, 5, 11, 1, 0, tzinfo=UTC),
    )

    target = first_summary["target_outcomes"][0]
    assert target["reason_code"] == "invalid_provider_payload"
    assert target["blocked_until"] == "2026-05-12T01:00:00+00:00"
    assert target["retry_after"] == "2026-05-12T01:00:00+00:00"
    assert target["observed_dates_missing"] == ["2026-05-11"]
    assert target["next_backfill_date_count"] == 1
    assert target["last_successful_observed_date"] is None

    recovered_fixture = _write_fake_source_provider_fixture(
        tmp_path,
        holdings_results=[
            {
                "source_provider_id": "provider_kodex_fake",
                "provider_etf_id": "2ETF35",
                "requested_date": "2026-05-11",
                "observed_date": "2026-05-11",
                "outcome": "fetched",
                "holdings": [_source_holding()],
            }
        ],
        filename="recovered_provider.json",
    )
    skipped_summary = source_module.update_holdings_history_source(
        provider=source_module.FakeSourceProvider.from_fixture_path(recovered_fixture),
        source_catalog_path=source_dir / "source_catalog.json",
        universe_state_path=source_dir / "universe_state.json",
        history_dir=history_dir,
        requested_date="2026-05-11",
        now=lambda: datetime(2026, 5, 11, 2, 0, tzinfo=UTC),
    )

    skipped = skipped_summary["target_outcomes"][0]
    assert skipped["outcome"] == "retry_cooldown"
    assert skipped["reason_code"] == "cooldown_active"
    assert skipped["retry_after"] == "2026-05-12T01:00:00+00:00"
    assert skipped["blocked_until"] == "2026-05-12T01:00:00+00:00"
    assert skipped["cooldown_remaining_seconds"] == 82800
    assert skipped["observed_dates_missing"] == ["2026-05-11"]
    assert skipped["next_backfill_date_count"] == 1

    rendered = json.dumps(skipped_summary, ensure_ascii=False)
    assert "provider_etf_id" not in rendered
    assert "://" not in rendered
    assert str(tmp_path) not in rendered

    recovered_summary = source_module.update_holdings_history_source(
        provider=source_module.FakeSourceProvider.from_fixture_path(recovered_fixture),
        source_catalog_path=source_dir / "source_catalog.json",
        universe_state_path=source_dir / "universe_state.json",
        history_dir=history_dir,
        requested_date="2026-05-11",
        now=lambda: datetime(2026, 5, 12, 2, 0, tzinfo=UTC),
    )

    recovered = recovered_summary["target_outcomes"][0]
    assert recovered["outcome"] == "fetched"
    assert "observed_dates_missing" not in recovered
    assert "next_backfill_date_count" not in recovered
    assert "blocked_until" not in recovered
    assert (history_dir / "holdings_history.json").is_file()


def test_update_holdings_history_source_processes_all_selected_provider_etfs(
    tmp_path: Path,
) -> None:
    source_module = __import__(
        "agent_treport.signal_report.adapters.source_acquisition",
        fromlist=[""],
    )
    provider = source_module.FakeSourceProvider.from_fixture_path(
        _write_fake_source_provider_fixture(
            tmp_path,
            entries=[
                {
                    "source_provider_id": "provider_kodex_fake",
                    "provider_etf_id": "2ETF35",
                    "etf_id": "etf_kodex_ai",
                    "etf_name": "KODEX AI ETF",
                    "brand_id": "brand_samsung",
                    "brand_name": "Samsung Asset Management",
                    "strategy_label": "active",
                    "locator": "https://provider.example/internal/2ETF35",
                },
                {
                    "source_provider_id": "provider_kodex_fake",
                    "provider_etf_id": "2ETF36",
                    "etf_id": "etf_kodex_robotics",
                    "etf_name": "KODEX Robotics ETF",
                    "brand_id": "brand_samsung",
                    "brand_name": "Samsung Asset Management",
                    "strategy_label": "active",
                    "locator": "https://provider.example/internal/2ETF36",
                },
            ],
            holdings_results=[
                {
                    "source_provider_id": "provider_kodex_fake",
                    "provider_etf_id": "2ETF35",
                    "requested_date": "2026-05-11",
                    "observed_date": "2026-05-11",
                    "outcome": "fetched",
                    "holdings": [_source_holding(security_id="sec_nvda")],
                },
                {
                    "source_provider_id": "provider_kodex_fake",
                    "provider_etf_id": "2ETF36",
                    "requested_date": "2026-05-11",
                    "observed_date": "2026-05-11",
                    "outcome": "fetched",
                    "holdings": [_source_holding(security_id="sec_msft")],
                },
            ],
        )
    )
    source_dir = tmp_path / "source_catalog"
    source_module.collect_source_catalog(
        provider=provider,
        dest_dir=source_dir,
        now=lambda: datetime(2026, 5, 11, 0, 45, tzinfo=UTC),
    )

    summary = source_module.update_holdings_history_source(
        provider=provider,
        source_catalog_path=source_dir / "source_catalog.json",
        universe_state_path=source_dir / "universe_state.json",
        history_dir=tmp_path / "holdings_history",
        requested_date="2026-05-11",
        provider_etf_ids={"2ETF35", "2ETF36"},
        now=lambda: datetime(2026, 5, 11, 1, 30, tzinfo=UTC),
    )

    assert [item["etf_id"] for item in summary["target_outcomes"]] == [
        "etf_kodex_ai",
        "etf_kodex_robotics",
    ]
    assert summary["run_outcome"] == "succeeded"
    assert summary["aggregate_counts"]["target_count"] == 2
    assert summary["aggregate_counts"]["fetched"] == 2
    assert summary["aggregate_counts"]["written_snapshot_count"] == 2
    rendered_summary = json.dumps(summary, ensure_ascii=False)
    assert "provider_etf_id" not in rendered_summary
    assert "https://" not in rendered_summary
    assert str(tmp_path) not in rendered_summary


def test_update_holdings_history_source_selected_provider_etf_failure_stays_bounded(
    tmp_path: Path,
) -> None:
    source_module = __import__(
        "agent_treport.signal_report.adapters.source_acquisition",
        fromlist=[""],
    )
    provider = source_module.FakeSourceProvider.from_fixture_path(
        _write_fake_source_provider_fixture(
            tmp_path,
            entries=[
                {
                    "source_provider_id": "provider_kodex_fake",
                    "provider_etf_id": "2ETF35",
                    "etf_id": "etf_first_candidate",
                    "etf_name": "KODEX First ETF",
                    "brand_id": "brand_samsung",
                    "brand_name": "Samsung Asset Management",
                    "strategy_label": "active",
                    "locator": "https://provider.example/internal/2ETF35",
                },
                {
                    "source_provider_id": "provider_kodex_fake",
                    "provider_etf_id": "2ETF36",
                    "etf_id": "etf_metadata_alternate",
                    "etf_name": "KODEX Metadata Alternate ETF",
                    "brand_id": "brand_samsung",
                    "brand_name": "Samsung Asset Management",
                    "strategy_label": "active",
                    "locator": "https://provider.example/internal/2ETF36",
                },
                {
                    "source_provider_id": "provider_kodex_fake",
                    "provider_etf_id": "2ETF37",
                    "etf_id": "etf_name_token_alternate",
                    "etf_name": "KODEX 액티브 Lower Priority ETF",
                    "brand_id": "brand_samsung",
                    "brand_name": "Samsung Asset Management",
                    "locator": "https://provider.example/internal/2ETF37",
                },
            ],
            holdings_results=[
                {
                    "source_provider_id": "provider_kodex_fake",
                    "provider_etf_id": "2ETF35",
                    "requested_date": "2026-05-11",
                    "outcome": "failed",
                    "failure_code_class": "provider_response",
                },
                {
                    "source_provider_id": "provider_kodex_fake",
                    "provider_etf_id": "2ETF36",
                    "requested_date": "2026-05-11",
                    "observed_date": "2026-05-10",
                    "outcome": "fetched",
                    "holdings": [_source_holding(security_id="sec_msft")],
                },
                {
                    "source_provider_id": "provider_kodex_fake",
                    "provider_etf_id": "2ETF37",
                    "requested_date": "2026-05-11",
                    "observed_date": "2026-05-10",
                    "outcome": "fetched",
                    "holdings": [_source_holding(security_id="sec_aapl")],
                },
            ],
        )
    )
    source_dir = tmp_path / "source_catalog"
    source_module.collect_source_catalog(
        provider=provider,
        dest_dir=source_dir,
        now=lambda: datetime(2026, 5, 11, 0, 45, tzinfo=UTC),
    )

    summary = source_module.update_holdings_history_source(
        provider=provider,
        source_catalog_path=source_dir / "source_catalog.json",
        universe_state_path=source_dir / "universe_state.json",
        history_dir=tmp_path / "holdings_history",
        requested_date="2026-05-11",
        provider_etf_ids={"2ETF35"},
        now=lambda: datetime(2026, 5, 11, 1, 30, tzinfo=UTC),
    )

    assert [item["etf_id"] for item in summary["target_outcomes"]] == [
        "etf_first_candidate"
    ]
    assert summary["target_outcomes"][0]["outcome"] == "failed"
    assert summary["provider_rollout_status"] == "active_holdings_failed"
    assert summary["aggregate_counts"]["target_count"] == 1
    assert summary["aggregate_counts"]["fetched"] == 0
    assert summary["aggregate_counts"]["failed"] == 1
    assert not (tmp_path / "holdings_history" / "holdings_history.json").exists()
    rendered_summary = json.dumps(summary, ensure_ascii=False)
    assert "etf_metadata_alternate" not in rendered_summary
    assert "2ETF37" not in rendered_summary
    assert "sec_msft" not in rendered_summary
    assert "https://" not in rendered_summary
    assert str(tmp_path) not in rendered_summary


def test_update_holdings_history_source_stores_stale_snapshot_without_supported_status(
    tmp_path: Path,
) -> None:
    source_module = __import__(
        "agent_treport.signal_report.adapters.source_acquisition",
        fromlist=[""],
    )
    provider = source_module.FakeSourceProvider.from_fixture_path(
        _write_fake_source_provider_fixture(
            tmp_path,
            holdings_results=[
                {
                    "source_provider_id": "provider_kodex_fake",
                    "provider_etf_id": "2ETF35",
                    "requested_date": "2026-05-11",
                    "observed_date": "2026-05-06",
                    "outcome": "fetched",
                    "holdings": [_source_holding()],
                }
            ],
        )
    )
    source_dir = tmp_path / "source_catalog"
    source_module.collect_source_catalog(
        provider=provider,
        dest_dir=source_dir,
        now=lambda: datetime(2026, 5, 11, 0, 45, tzinfo=UTC),
    )

    summary = source_module.update_holdings_history_source(
        provider=provider,
        source_catalog_path=source_dir / "source_catalog.json",
        universe_state_path=source_dir / "universe_state.json",
        history_dir=tmp_path / "holdings_history",
        requested_date="2026-05-11",
        now=lambda: datetime(2026, 5, 11, 1, 30, tzinfo=UTC),
    )

    rows = _jsonl_rows(
        tmp_path / "holdings_history" / "holdings_history.json.parts" / "2026-05-06.jsonl"
    )

    assert summary["run_outcome"] == "succeeded"
    assert summary["provider_rollout_status"] == "catalog_only"
    assert summary["observed_dates"] == ["2026-05-06"]
    assert summary["warnings"] == [
        {
            "code": "stale_latest_holdings",
            "severity": "warning",
            "source_provider_id": "provider_kodex_fake",
            "etf_id": "etf_kodex_ai",
            "scope": "holdings_snapshot",
            "observed_date": "2026-05-06",
            "latest_acceptable_observed_date": "2026-05-08",
        }
    ]
    assert summary["target_outcomes"][0]["latest_upload_freshness"] == {
        "status": "stale_latest",
        "observed_date": "2026-05-06",
        "latest_acceptable_observed_date": "2026-05-08",
    }
    assert rows[0]["as_of_date"] == "2026-05-06"
    rendered_summary = json.dumps(summary, ensure_ascii=False)
    assert "sec_nvda" not in rendered_summary
    assert "NVIDIA" not in rendered_summary
    assert "https://" not in rendered_summary
    assert str(tmp_path) not in rendered_summary


def test_update_holdings_history_source_marks_selected_active_holdings_failure(
    tmp_path: Path,
) -> None:
    source_module = __import__(
        "agent_treport.signal_report.adapters.source_acquisition",
        fromlist=[""],
    )
    provider = source_module.FakeSourceProvider.from_fixture_path(
        _write_fake_source_provider_fixture(
            tmp_path,
            entries=[
                {
                    "source_provider_id": "provider_kodex_fake",
                    "provider_etf_id": "2ETF35",
                    "etf_id": "etf_first_candidate",
                    "etf_name": "KODEX First ETF",
                    "brand_id": "brand_samsung",
                    "brand_name": "Samsung Asset Management",
                    "strategy_label": "active",
                    "locator": "https://provider.example/internal/2ETF35",
                },
                {
                    "source_provider_id": "provider_kodex_fake",
                    "provider_etf_id": "2ETF36",
                    "etf_id": "etf_second_candidate",
                    "etf_name": "KODEX Second ETF",
                    "brand_id": "brand_samsung",
                    "brand_name": "Samsung Asset Management",
                    "strategy_label": "active",
                    "locator": "https://provider.example/internal/2ETF36",
                },
            ],
            holdings_results=[
                {
                    "source_provider_id": "provider_kodex_fake",
                    "provider_etf_id": "2ETF35",
                    "requested_date": "2026-05-11",
                    "outcome": "failed",
                    "failure_code_class": "provider_response",
                },
                {
                    "source_provider_id": "provider_kodex_fake",
                    "provider_etf_id": "2ETF36",
                    "requested_date": "2026-05-11",
                    "outcome": "rate_limited",
                    "failure_code_class": "rate_limited",
                },
            ],
        )
    )
    source_dir = tmp_path / "source_catalog"
    source_module.collect_source_catalog(
        provider=provider,
        dest_dir=source_dir,
        now=lambda: datetime(2026, 5, 11, 0, 45, tzinfo=UTC),
    )

    summary = source_module.update_holdings_history_source(
        provider=provider,
        source_catalog_path=source_dir / "source_catalog.json",
        universe_state_path=source_dir / "universe_state.json",
        history_dir=tmp_path / "holdings_history",
        requested_date="2026-05-11",
        provider_etf_ids={"2ETF35"},
        now=lambda: datetime(2026, 5, 11, 1, 30, tzinfo=UTC),
    )

    assert summary["run_outcome"] == "failed"
    assert summary["provider_rollout_status"] == "active_holdings_failed"
    assert [item["etf_id"] for item in summary["target_outcomes"]] == [
        "etf_first_candidate"
    ]
    assert summary["aggregate_counts"]["failed"] == 1
    assert summary["aggregate_counts"]["rate_limited"] == 0
    assert not (tmp_path / "holdings_history" / "holdings_history.json").exists()
    rendered_summary = json.dumps(summary, ensure_ascii=False)
    assert "etf_second_candidate" not in rendered_summary
    assert "https://" not in rendered_summary
    assert str(tmp_path) not in rendered_summary


def test_update_holdings_history_source_changed_duplicate_is_refresh_required(
    tmp_path: Path,
) -> None:
    source_module = __import__(
        "agent_treport.signal_report.adapters.source_acquisition",
        fromlist=[""],
    )
    source_dir = tmp_path / "source_catalog"
    history_dir = tmp_path / "holdings_history"
    initial_provider = source_module.FakeSourceProvider.from_fixture_path(
        _write_fake_source_provider_fixture(tmp_path)
    )
    source_module.collect_source_catalog(
        provider=initial_provider,
        dest_dir=source_dir,
        now=lambda: datetime(2026, 5, 11, 0, 45, tzinfo=UTC),
    )
    source_module.update_holdings_history_source(
        provider=initial_provider,
        source_catalog_path=source_dir / "source_catalog.json",
        universe_state_path=source_dir / "universe_state.json",
        history_dir=history_dir,
        requested_date="2026-05-11",
        now=lambda: datetime(2026, 5, 11, 1, 30, tzinfo=UTC),
    )
    changed_provider = source_module.FakeSourceProvider.from_fixture_path(
        _write_fake_source_provider_fixture(
            tmp_path,
            filename="changed_source_provider_fixture.json",
            holdings_results=[
                {
                    "source_provider_id": "provider_kodex_fake",
                    "provider_etf_id": "2ETF35",
                    "requested_date": "2026-05-11",
                    "observed_date": "2026-05-10",
                    "outcome": "fetched",
                    "holdings": [_source_holding(weight_percent=8.25)],
                }
            ],
        )
    )
    history_before = (history_dir / "holdings_history.json").read_bytes()

    summary = source_module.update_holdings_history_source(
        provider=changed_provider,
        source_catalog_path=source_dir / "source_catalog.json",
        universe_state_path=source_dir / "universe_state.json",
        history_dir=history_dir,
        requested_date="2026-05-11",
        now=lambda: datetime(2026, 5, 11, 2, 30, tzinfo=UTC),
    )

    rows = _jsonl_rows(
        history_dir / "holdings_history.json.parts" / "2026-05-10.jsonl"
    )
    assert summary["run_outcome"] == "failed"
    assert summary["target_outcomes"] == [
        {
            "source_provider_id": "provider_kodex_fake",
            "brand_id": "brand_samsung",
            "etf_id": "etf_kodex_ai",
            "scope": "holdings_snapshot",
            "requested_date": "2026-05-11",
            "observed_date": "2026-05-10",
            "date_alignment": {
                "requested_date": "2026-05-11",
                "observed_date": "2026-05-10",
                "status": "observed_differs_from_provider_query",
            },
            "latest_upload_freshness": {
                "status": "not_fetched",
                "observed_date": "2026-05-10",
                "latest_acceptable_observed_date": "2026-05-08",
            },
            "outcome": "failed",
            "row_count": 1,
            "reason_code": "refresh_required",
            "retry_attempt_count": 0,
            "last_successful_observed_date": "2026-05-10",
            "observed_dates_missing": ["2026-05-11"],
            "next_backfill_date_count": 1,
            "blocked_until": "2026-05-12T02:30:00+00:00",
            "retry_after": "2026-05-12T02:30:00+00:00",
        }
    ]
    assert (history_dir / "holdings_history.json").read_bytes() == history_before
    assert rows[0]["weight_percent"] == 7.5


class _FakeKodexResponse:
    def __init__(self, payload: object, *, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        if isinstance(payload, str):
            self.content = payload.encode("utf-8")
            self.text = payload
        else:
            self.content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.text = self.content.decode("utf-8")

    def json(self) -> object:
        return self._payload


class _FakeKodexSession:
    def __init__(self) -> None:
        self.get_calls: list[tuple[str, dict[str, object]]] = []

    def get(self, url: str, **kwargs: object) -> _FakeKodexResponse:
        self.get_calls.append((url, kwargs))
        if url.endswith("/api/v1/kodex/product.do"):
            return _FakeKodexResponse(
                [
                    {
                        "fId": "2ETF99",
                        "fNm": "KODEX AI ETF",
                        "typeNm": "active",
                        "totalCnt": "1",
                    }
                ]
            )
        if url.endswith("/api/v1/kodex/product/2ETF99.do"):
            return _FakeKodexResponse(
                {
                    "info": {"product": {"listD": "2024.01.02"}},
                    "pdf": {"gijunYMD": "20260509", "list": []},
                }
            )
        if url.endswith("/api/v1/kodex/product-pdf/2ETF99.do"):
            return _FakeKodexResponse(
                {
                    "pdf": {
                        "gijunYMD": "2026.05.10",
                        "list": [
                            {
                                "itmNo": "KRD010010001",
                                "secNm": "KRW deposit",
                                "applyQ": "-15687283",
                                "evalA": "-15,687,283",
                                "ratio": None,
                            },
                            {
                                "itmNo": "US67066G1040",
                                "secNm": "NVIDIA Corp.",
                                "applyQ": "1,500",
                                "evalA": "240,000,000",
                                "ratio": "7.5",
                            }
                        ],
                    }
                }
            )
        raise AssertionError(f"unexpected KODEX URL: {url}")


class _BlockedKodexSession:
    def __init__(self, *, status_code: int) -> None:
        self.status_code = status_code
        self.get_calls: list[tuple[str, dict[str, object]]] = []

    def get(self, url: str, **kwargs: object) -> _FakeKodexResponse:
        self.get_calls.append((url, kwargs))
        return _FakeKodexResponse(
            {"message": "blocked"},
            status_code=self.status_code,
        )


class _RoutedSourceSession:
    def __init__(self, routes: Mapping[tuple[str, str], object]) -> None:
        self._routes = dict(routes)
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def get(self, url: str, **kwargs: object) -> _FakeKodexResponse:
        self.calls.append(("GET", url, kwargs))
        return self._response("GET", url)

    def post(self, url: str, **kwargs: object) -> _FakeKodexResponse:
        self.calls.append(("POST", url, kwargs))
        return self._response("POST", url)

    def _response(self, method: str, url: str) -> _FakeKodexResponse:
        routes = sorted(
            self._routes.items(),
            key=lambda item: len(item[0][1]),
            reverse=True,
        )
        for (route_method, route_suffix), payload in routes:
            if method == route_method and (url.endswith(route_suffix) or route_suffix in url):
                if isinstance(payload, _FakeKodexResponse):
                    return payload
                return _FakeKodexResponse(payload)
        raise AssertionError(f"unexpected {method} source provider URL: {url}")


def _ace_session(*, pdf_payload: object | None = None) -> _RoutedSourceSession:
    return _RoutedSourceSession(
        {
            ("GET", "/api/funds"): {
                "data": [
                    {
                        "fundCd": "453850",
                        "fundNm": "ACE AI ETF",
                        "badge": {"type": "Active"},
                    }
                ]
            },
            ("GET", "/api/funds/453850"): {"mm1ErnRt": "1.2"},
            ("GET", "/api/funds/453850/product"): {"listDt": "2024-01-02"},
            (
                "GET",
                "/api/funds/453850/pdf?page=1&size=1000&std_dt=20260511",
            ): pdf_payload
            or {
                "last_STD_DT": "2026-05-10",
                "pdfList": [
                    {
                        "std_DT": "2026-05-10",
                        "jm_KSC_CD": "US67066G1040",
                        "sec_NM": "NVIDIA Corp.",
                        "cu_ITEM_CNT": "1,500",
                        "val_AM": "240,000,000",
                        "wg": "7.5",
                    },
                    {
                        "std_DT": "2026-05-10",
                        "jm_KSC_CD": "KRD010010001",
                        "sec_NM": "KRW deposit",
                        "cu_ITEM_CNT": "-1",
                        "val_AM": "-1",
                        "wg": None,
                    },
                ],
            },
        }
    )


def test_ace_source_provider_parses_catalog_and_holdings_without_live_network(
    tmp_path: Path,
) -> None:
    source_module = __import__(
        "agent_treport.signal_report.adapters.source_acquisition",
        fromlist=[""],
    )
    provider = source_module.AceSourceProvider(session=_ace_session())
    source_dir = tmp_path / "source_catalog"

    catalog_summary = source_module.collect_source_catalog(
        provider=provider,
        dest_dir=source_dir,
        now=lambda: datetime(2026, 5, 11, 0, 45, tzinfo=UTC),
    )
    history_summary = source_module.update_holdings_history_source(
        provider=provider,
        source_catalog_path=source_dir / "source_catalog.json",
        universe_state_path=source_dir / "universe_state.json",
        history_dir=tmp_path / "holdings_history",
        requested_date="2026-05-11",
        now=lambda: datetime(2026, 5, 11, 1, 30, tzinfo=UTC),
    )

    rows = _jsonl_rows(
        tmp_path / "holdings_history" / "holdings_history.json.parts" / "2026-05-10.jsonl"
    )
    nvda = next(row for row in rows if row["security_id"] == "US67066G1040")
    cash = next(row for row in rows if row["security_id"] == "KRD010010001")

    assert catalog_summary["source_provider_id"] == "ace"
    assert catalog_summary["catalog_entries"] == [
        {
            "brand_id": "brand_korea_investment_asset_management",
            "etf_id": "etf_ace_453850",
            "source_provider_id": "ace",
            "is_active_strategy_etf": True,
            "active_strategy_source": "source_metadata",
            "active_strategy_confidence": "high",
        }
    ]
    assert history_summary["run_outcome"] == "succeeded"
    assert history_summary["observed_dates"] == ["2026-05-10"]
    assert "provider_query_date" not in history_summary["target_outcomes"][0]
    assert history_summary["target_outcomes"][0]["date_alignment"]["status"] == (
        "observed_differs_from_provider_query"
    )
    assert nvda["etf_id"] == "etf_ace_453850"
    assert nvda["etf_name"] == "ACE AI ETF"
    assert nvda["source_provider_id"] == "ace"
    assert nvda["weight_percent"] == 7.5
    assert nvda["shares"] == 1500.0
    assert nvda["market_value_krw"] == 240000000.0
    assert nvda["security_classification"] == "ticker_candidate"
    assert cash["is_cash"] is True
    assert cash["security_classification"] == "cash_like"
    assert cash["weight_percent"] == 0.0
    rendered = json.dumps(history_summary, ensure_ascii=False)
    assert "aceetf.co.kr" not in rendered
    assert "US67066G1040" not in rendered
    assert str(tmp_path) not in rendered


def test_ace_source_provider_rejects_mixed_observed_dates(
    tmp_path: Path,
) -> None:
    source_module = __import__(
        "agent_treport.signal_report.adapters.source_acquisition",
        fromlist=[""],
    )
    provider = source_module.AceSourceProvider(
        session=_ace_session(
            pdf_payload={
                "last_STD_DT": "2026-05-10",
                "pdfList": [
                    {
                        "std_DT": "2026-05-10",
                        "jm_KSC_CD": "US67066G1040",
                        "sec_NM": "NVIDIA Corp.",
                        "cu_ITEM_CNT": "1,500",
                        "val_AM": "240,000,000",
                        "wg": "7.5",
                    },
                    {
                        "std_DT": "2026-05-09",
                        "jm_KSC_CD": "US5949181045",
                        "sec_NM": "Microsoft Corp.",
                        "cu_ITEM_CNT": "900",
                        "val_AM": "180,000,000",
                        "wg": "5.0",
                    },
                ],
            }
        )
    )
    source_dir = tmp_path / "source_catalog"
    source_module.collect_source_catalog(
        provider=provider,
        dest_dir=source_dir,
        now=lambda: datetime(2026, 5, 11, 0, 45, tzinfo=UTC),
    )

    summary = source_module.update_holdings_history_source(
        provider=provider,
        source_catalog_path=source_dir / "source_catalog.json",
        universe_state_path=source_dir / "universe_state.json",
        history_dir=tmp_path / "holdings_history",
        requested_date="2026-05-11",
        now=lambda: datetime(2026, 5, 11, 1, 30, tzinfo=UTC),
    )

    assert summary["run_outcome"] == "failed"
    assert summary["target_outcomes"][0]["outcome"] == "failed"
    assert summary["target_outcomes"][0]["reason_code"] == (
        "invalid_provider_payload"
    )
    assert not (tmp_path / "holdings_history" / "holdings_history.json").exists()


def _hyundai_session(*, pdf_payload: object | None = None) -> _RoutedSourceSession:
    return _RoutedSourceSession(
        {
            ("GET", "/api/etfList"): [
                {
                    "fund": {
                        "id": "101",
                        "name": "UNICORN 액티브 AI ETF",
                    }
                }
            ],
            ("GET", "/api/funds/etf/101"): {
                "fund": {"펀드코드": "FUND101"},
                "종목코드": "ETF101",
                "상장일": "2024-01-02",
            },
            (
                "GET",
                "/api/etfPdf?fundCode=FUND101&etfCode=ETF101&ymd=20260511",
            ): pdf_payload
            or [
                {
                    "date": "2026-05-10",
                    "구성종목코드": "US67066G1040",
                    "구성종목명": "NVIDIA Corp.",
                    "구성종목수": "1,500",
                    "평가금액": "240,000,000",
                    "비중": "7.5",
                },
                {
                    "date": "2026-05-10",
                    "구성종목코드": "KRD010010001",
                    "구성종목명": "KRW deposit",
                    "구성종목수": "-1",
                    "평가금액": "-1",
                    "비중": None,
                },
            ],
        }
    )


def test_hyundai_source_provider_parses_catalog_and_holdings_without_live_network(
    tmp_path: Path,
) -> None:
    source_module = __import__(
        "agent_treport.signal_report.adapters.source_acquisition",
        fromlist=[""],
    )
    provider = source_module.HyundaiSourceProvider(session=_hyundai_session())
    source_dir = tmp_path / "source_catalog"

    catalog_summary = source_module.collect_source_catalog(
        provider=provider,
        dest_dir=source_dir,
        now=lambda: datetime(2026, 5, 11, 0, 45, tzinfo=UTC),
    )
    history_summary = source_module.update_holdings_history_source(
        provider=provider,
        source_catalog_path=source_dir / "source_catalog.json",
        universe_state_path=source_dir / "universe_state.json",
        history_dir=tmp_path / "holdings_history",
        requested_date="2026-05-11",
        now=lambda: datetime(2026, 5, 11, 1, 30, tzinfo=UTC),
    )

    rows = _jsonl_rows(
        tmp_path / "holdings_history" / "holdings_history.json.parts" / "2026-05-10.jsonl"
    )
    nvda = next(row for row in rows if row["security_id"] == "US67066G1040")
    cash = next(row for row in rows if row["security_id"] == "KRD010010001")

    assert catalog_summary["source_provider_id"] == "hyundai"
    assert catalog_summary["catalog_entries"] == [
        {
            "brand_id": "brand_hyundai_asset_management",
            "etf_id": "etf_hyundai_101",
            "source_provider_id": "hyundai",
            "is_active_strategy_etf": True,
            "active_strategy_source": "name_token",
            "active_strategy_confidence": "low",
        }
    ]
    assert history_summary["run_outcome"] == "succeeded"
    assert history_summary["observed_dates"] == ["2026-05-10"]
    assert "provider_query_date" not in history_summary["target_outcomes"][0]
    assert nvda["etf_id"] == "etf_hyundai_101"
    assert nvda["etf_name"] == "UNICORN 액티브 AI ETF"
    assert nvda["source_provider_id"] == "hyundai"
    assert nvda["weight_percent"] == 7.5
    assert nvda["shares"] == 1500.0
    assert nvda["market_value_krw"] == 240000000.0
    assert cash["is_cash"] is True
    assert cash["weight_percent"] == 0.0
    rendered = json.dumps(history_summary, ensure_ascii=False)
    assert "hyundaiam.com" not in rendered
    assert "US67066G1040" not in rendered
    assert str(tmp_path) not in rendered


def test_hyundai_source_provider_rejects_mixed_observed_dates(
    tmp_path: Path,
) -> None:
    source_module = __import__(
        "agent_treport.signal_report.adapters.source_acquisition",
        fromlist=[""],
    )
    provider = source_module.HyundaiSourceProvider(
        session=_hyundai_session(
            pdf_payload=[
                {
                    "date": "2026-05-10",
                    "구성종목코드": "US67066G1040",
                    "구성종목명": "NVIDIA Corp.",
                    "구성종목수": "1,500",
                    "평가금액": "240,000,000",
                    "비중": "7.5",
                },
                {
                    "date": "2026-05-09",
                    "구성종목코드": "US5949181045",
                    "구성종목명": "Microsoft Corp.",
                    "구성종목수": "900",
                    "평가금액": "180,000,000",
                    "비중": "5.0",
                },
            ]
        )
    )
    source_dir = tmp_path / "source_catalog"
    source_module.collect_source_catalog(
        provider=provider,
        dest_dir=source_dir,
        now=lambda: datetime(2026, 5, 11, 0, 45, tzinfo=UTC),
    )

    summary = source_module.update_holdings_history_source(
        provider=provider,
        source_catalog_path=source_dir / "source_catalog.json",
        universe_state_path=source_dir / "universe_state.json",
        history_dir=tmp_path / "holdings_history",
        requested_date="2026-05-11",
        now=lambda: datetime(2026, 5, 11, 1, 30, tzinfo=UTC),
    )

    assert summary["run_outcome"] == "failed"
    assert summary["target_outcomes"][0]["reason_code"] == (
        "invalid_provider_payload"
    )
    assert not (tmp_path / "holdings_history" / "holdings_history.json").exists()


def _timefolio_session(*, detail_html: str | None = None) -> _RoutedSourceSession:
    return _RoutedSourceSession(
        {
            ("GET", "/m11.php"): """
                <a href="./m11_view.php?idx=14"><div>AI ETF</div></a>
            """,
            ("GET", "/m11_view.php?idx=14&pdfDate=2026-05-11"): detail_html
            or """
                <input id="pdfDate" value="2026-05-10">
                <table class="moreList1">
                  <tbody>
                    <tr>
                      <td>US67066G1040</td>
                      <td>NVIDIA Corp.</td>
                      <td>1,500</td>
                      <td>240,000,000</td>
                      <td>7.5</td>
                    </tr>
                    <tr>
                      <td>KRD010010001</td>
                      <td>KRW deposit</td>
                      <td>-1</td>
                      <td>-1</td>
                      <td></td>
                    </tr>
                  </tbody>
                </table>
            """,
        }
    )


def test_timefolio_source_provider_parses_catalog_and_holdings_without_live_network(
    tmp_path: Path,
) -> None:
    source_module = __import__(
        "agent_treport.signal_report.adapters.source_acquisition",
        fromlist=[""],
    )
    provider = source_module.TimefolioSourceProvider(session=_timefolio_session())
    source_dir = tmp_path / "source_catalog"

    catalog_summary = source_module.collect_source_catalog(
        provider=provider,
        dest_dir=source_dir,
        now=lambda: datetime(2026, 5, 11, 0, 45, tzinfo=UTC),
    )
    history_summary = source_module.update_holdings_history_source(
        provider=provider,
        source_catalog_path=source_dir / "source_catalog.json",
        universe_state_path=source_dir / "universe_state.json",
        history_dir=tmp_path / "holdings_history",
        requested_date="2026-05-11",
        now=lambda: datetime(2026, 5, 11, 1, 30, tzinfo=UTC),
    )

    rows = _jsonl_rows(
        tmp_path / "holdings_history" / "holdings_history.json.parts" / "2026-05-10.jsonl"
    )
    nvda = next(row for row in rows if row["security_id"] == "US67066G1040")
    cash = next(row for row in rows if row["security_id"] == "KRD010010001")

    assert catalog_summary["source_provider_id"] == "timefolio"
    assert catalog_summary["catalog_entries"] == [
        {
            "brand_id": "brand_timefolio_asset_management",
            "etf_id": "etf_timefolio_14",
            "source_provider_id": "timefolio",
            "is_active_strategy_etf": True,
            "active_strategy_source": "timefolio_provider_default",
            "active_strategy_confidence": "medium",
        }
    ]
    assert history_summary["run_outcome"] == "succeeded"
    assert history_summary["observed_dates"] == ["2026-05-10"]
    assert "provider_query_date" not in history_summary["target_outcomes"][0]
    assert history_summary["target_outcomes"][0]["date_alignment"]["status"] == (
        "observed_differs_from_provider_query"
    )
    assert nvda["etf_id"] == "etf_timefolio_14"
    assert nvda["etf_name"] == "TIME AI ETF"
    assert nvda["source_provider_id"] == "timefolio"
    assert nvda["weight_percent"] == 7.5
    assert nvda["shares"] == 1500.0
    assert nvda["market_value_krw"] == 240000000.0
    assert cash["is_cash"] is True
    assert cash["weight_percent"] == 0.0
    rendered = json.dumps(history_summary, ensure_ascii=False)
    assert "timeetf.co.kr" not in rendered
    assert "US67066G1040" not in rendered
    assert str(tmp_path) not in rendered


def test_timefolio_source_provider_rejects_empty_holdings_table(
    tmp_path: Path,
) -> None:
    source_module = __import__(
        "agent_treport.signal_report.adapters.source_acquisition",
        fromlist=[""],
    )
    provider = source_module.TimefolioSourceProvider(
        session=_timefolio_session(
            detail_html='<input id="pdfDate" value="2026-05-10"><table></table>'
        )
    )
    source_dir = tmp_path / "source_catalog"
    source_module.collect_source_catalog(
        provider=provider,
        dest_dir=source_dir,
        now=lambda: datetime(2026, 5, 11, 0, 45, tzinfo=UTC),
    )

    summary = source_module.update_holdings_history_source(
        provider=provider,
        source_catalog_path=source_dir / "source_catalog.json",
        universe_state_path=source_dir / "universe_state.json",
        history_dir=tmp_path / "holdings_history",
        requested_date="2026-05-11",
        now=lambda: datetime(2026, 5, 11, 1, 30, tzinfo=UTC),
    )

    assert summary["run_outcome"] == "failed"
    assert summary["target_outcomes"][0]["reason_code"] == (
        "invalid_provider_payload"
    )
    assert not (tmp_path / "holdings_history" / "holdings_history.json").exists()


def test_timefolio_source_provider_keeps_uncoded_non_cash_rows_as_unknown(
    tmp_path: Path,
) -> None:
    source_module = __import__(
        "agent_treport.signal_report.adapters.source_acquisition",
        fromlist=[""],
    )
    provider = source_module.TimefolioSourceProvider(
        session=_timefolio_session(
            detail_html="""
                <input id="pdfDate" value="2026-05-10">
                <table class="moreList1">
                  <tbody>
                    <tr>
                      <td></td>
                      <td>Uncoded Synthetic Equity</td>
                      <td>10</td>
                      <td>20,000</td>
                      <td>1.5</td>
                    </tr>
                  </tbody>
                </table>
            """
        )
    )
    source_dir = tmp_path / "source_catalog"
    source_module.collect_source_catalog(
        provider=provider,
        dest_dir=source_dir,
        now=lambda: datetime(2026, 5, 11, 0, 45, tzinfo=UTC),
    )

    summary = source_module.update_holdings_history_source(
        provider=provider,
        source_catalog_path=source_dir / "source_catalog.json",
        universe_state_path=source_dir / "universe_state.json",
        history_dir=tmp_path / "holdings_history",
        requested_date="2026-05-11",
        now=lambda: datetime(2026, 5, 11, 1, 30, tzinfo=UTC),
    )

    assert summary["run_outcome"] == "succeeded"
    rows = _jsonl_rows(
        tmp_path / "holdings_history" / "holdings_history.json.parts" / "2026-05-10.jsonl"
    )

    security_id = rows[0]["security_id"]
    assert isinstance(security_id, str)
    assert security_id.startswith("UNCODED:timefolio:14:")
    assert rows[0]["security_classification"] == "unknown"
    assert rows[0]["is_cash"] is False


def test_timefolio_source_provider_classifies_uncoded_korean_cash_like(
    tmp_path: Path,
) -> None:
    source_module = __import__(
        "agent_treport.signal_report.adapters.source_acquisition",
        fromlist=[""],
    )
    provider = source_module.TimefolioSourceProvider(
        session=_timefolio_session(
            detail_html="""
                <input id="pdfDate" value="2026-05-10">
                <table class="moreList1">
                  <tbody>
                    <tr>
                      <td>US67066G1040</td>
                      <td>NVIDIA Corp.</td>
                      <td>10</td>
                      <td>900</td>
                      <td>90</td>
                    </tr>
                    <tr>
                      <td></td>
                      <td>현금</td>
                      <td>100</td>
                      <td>100</td>
                      <td>10</td>
                    </tr>
                  </tbody>
                </table>
            """
        )
    )
    source_dir = tmp_path / "source_catalog"
    source_module.collect_source_catalog(
        provider=provider,
        dest_dir=source_dir,
        now=lambda: datetime(2026, 5, 11, 0, 45, tzinfo=UTC),
    )

    source_module.update_holdings_history_source(
        provider=provider,
        source_catalog_path=source_dir / "source_catalog.json",
        universe_state_path=source_dir / "universe_state.json",
        history_dir=tmp_path / "holdings_history",
        requested_date="2026-05-11",
        now=lambda: datetime(2026, 5, 11, 1, 30, tzinfo=UTC),
    )

    rows = _jsonl_rows(
        tmp_path / "holdings_history" / "holdings_history.json.parts" / "2026-05-10.jsonl"
    )
    cash = next(row for row in rows if row["is_cash"])

    assert cash["security_id"] == "CASH_UNCODED:timefolio:14"
    assert cash["name"] == "Cash"
    assert cash["security_classification"] == "cash_like"
    assert cash["weight_percent"] == 10.0


def _tiger_session(*, holdings_payload: object | None = None) -> _RoutedSourceSession:
    return _RoutedSourceSession(
        {
            ("GET", "/tigeretf/ko/product/search/list.ajax"): """
                <div class="c-data-row" data-tot-cnt="1" data-ksd-fund="KR7TIGER001">
                  <div class="title"><a href="#">TIGER 액티브 AI ETF</a></div>
                </div>
            """,
            ("POST", "/tigeretf/ko/product/chart/prdct-item-list.ajax"): holdings_payload
            or {
                "rtnData": [
                    {
                        "wkdate": "2026-05-10",
                        "memItemcode": "US67066G1040",
                        "memItemname": "NVIDIA Corp.",
                        "stockQty": "1,500",
                        "stockPrc": "240,000,000",
                        "stockRate": "7.5",
                    },
                    {
                        "wkdate": "2026-05-10",
                        "memItemcode": "KRD010010001",
                        "memItemname": "KRW deposit",
                        "stockQty": "-1",
                        "stockPrc": "-1",
                        "stockRate": None,
                    },
                ]
            },
        }
    )


def test_tiger_source_provider_parses_catalog_and_holdings_without_live_network(
    tmp_path: Path,
) -> None:
    source_module = __import__(
        "agent_treport.signal_report.adapters.source_acquisition",
        fromlist=[""],
    )
    provider = source_module.TigerSourceProvider(session=_tiger_session())
    source_dir = tmp_path / "source_catalog"

    catalog_summary = source_module.collect_source_catalog(
        provider=provider,
        dest_dir=source_dir,
        now=lambda: datetime(2026, 5, 11, 0, 45, tzinfo=UTC),
    )
    history_summary = source_module.update_holdings_history_source(
        provider=provider,
        source_catalog_path=source_dir / "source_catalog.json",
        universe_state_path=source_dir / "universe_state.json",
        history_dir=tmp_path / "holdings_history",
        requested_date="2026-05-11",
        now=lambda: datetime(2026, 5, 11, 1, 30, tzinfo=UTC),
    )

    rows = _jsonl_rows(
        tmp_path / "holdings_history" / "holdings_history.json.parts" / "2026-05-10.jsonl"
    )
    nvda = next(row for row in rows if row["security_id"] == "US67066G1040")
    cash = next(row for row in rows if row["security_id"] == "KRD010010001")

    assert catalog_summary["source_provider_id"] == "tiger"
    assert catalog_summary["catalog_entries"] == [
        {
            "brand_id": "brand_mirae_asset_management",
            "etf_id": "etf_tiger_kr7tiger001",
            "source_provider_id": "tiger",
            "is_active_strategy_etf": True,
            "active_strategy_source": "name_token",
            "active_strategy_confidence": "low",
        }
    ]
    assert history_summary["run_outcome"] == "succeeded"
    assert history_summary["observed_dates"] == ["2026-05-10"]
    assert nvda["etf_id"] == "etf_tiger_kr7tiger001"
    assert nvda["etf_name"] == "TIGER 액티브 AI ETF"
    assert nvda["source_provider_id"] == "tiger"
    assert nvda["weight_percent"] == 7.5
    assert nvda["shares"] == 1500.0
    assert nvda["market_value_krw"] == 240000000.0
    assert cash["is_cash"] is True
    assert cash["weight_percent"] == 0.0
    rendered = json.dumps(history_summary, ensure_ascii=False)
    assert "miraeasset.com" not in rendered
    assert "US67066G1040" not in rendered
    assert str(tmp_path) not in rendered


def test_tiger_source_provider_rejects_mixed_observed_dates(
    tmp_path: Path,
) -> None:
    source_module = __import__(
        "agent_treport.signal_report.adapters.source_acquisition",
        fromlist=[""],
    )
    provider = source_module.TigerSourceProvider(
        session=_tiger_session(
            holdings_payload={
                "rtnData": [
                    {
                        "wkdate": "2026-05-10",
                        "memItemcode": "US67066G1040",
                        "memItemname": "NVIDIA Corp.",
                        "stockQty": "1,500",
                        "stockPrc": "240,000,000",
                        "stockRate": "7.5",
                    },
                    {
                        "wkdate": "2026-05-09",
                        "memItemcode": "US5949181045",
                        "memItemname": "Microsoft Corp.",
                        "stockQty": "900",
                        "stockPrc": "180,000,000",
                        "stockRate": "5.0",
                    },
                ]
            }
        )
    )
    source_dir = tmp_path / "source_catalog"
    source_module.collect_source_catalog(
        provider=provider,
        dest_dir=source_dir,
        now=lambda: datetime(2026, 5, 11, 0, 45, tzinfo=UTC),
    )

    summary = source_module.update_holdings_history_source(
        provider=provider,
        source_catalog_path=source_dir / "source_catalog.json",
        universe_state_path=source_dir / "universe_state.json",
        history_dir=tmp_path / "holdings_history",
        requested_date="2026-05-11",
        now=lambda: datetime(2026, 5, 11, 1, 30, tzinfo=UTC),
    )

    assert summary["run_outcome"] == "failed"
    assert summary["target_outcomes"][0]["reason_code"] == (
        "invalid_provider_payload"
    )
    assert not (tmp_path / "holdings_history" / "holdings_history.json").exists()


def _rise_session(*, holdings_html: str | None = None) -> _RoutedSourceSession:
    return _RoutedSourceSession(
        {
            ("GET", "/prod/finder"): """
                <button onclick="location.href='/prod/finderDetail/RF101'">
                  RISE 액티브 AI ETF
                </button>
            """,
            ("GET", "/prod/finderDetail/RF101?searchFlag=viewtab3"): """
                <html><body>RISE detail</body></html>
            """,
            ("POST", "/prod/finder/productViewSearchTabJquery3"): holdings_html
            or """
                <p>PDF reference 2026-05-10</p>
                <table>
                  <tbody>
                    <tr>
                      <td>1</td>
                      <td>NVIDIA Corp.</td>
                      <td>US67066G1040</td>
                      <td>1,500</td>
                      <td>7.5</td>
                      <td>240,000,000</td>
                    </tr>
                    <tr>
                      <td>2</td>
                      <td>KRW deposit</td>
                      <td>KRD010010001</td>
                      <td>-1</td>
                      <td></td>
                      <td>-1</td>
                    </tr>
                  </tbody>
                </table>
            """,
        }
    )


def test_rise_source_provider_parses_catalog_and_holdings_without_live_network(
    tmp_path: Path,
) -> None:
    source_module = __import__(
        "agent_treport.signal_report.adapters.source_acquisition",
        fromlist=[""],
    )
    provider = source_module.RiseSourceProvider(session=_rise_session())
    source_dir = tmp_path / "source_catalog"

    catalog_summary = source_module.collect_source_catalog(
        provider=provider,
        dest_dir=source_dir,
        now=lambda: datetime(2026, 5, 11, 0, 45, tzinfo=UTC),
    )
    history_summary = source_module.update_holdings_history_source(
        provider=provider,
        source_catalog_path=source_dir / "source_catalog.json",
        universe_state_path=source_dir / "universe_state.json",
        history_dir=tmp_path / "holdings_history",
        requested_date="2026-05-11",
        now=lambda: datetime(2026, 5, 11, 1, 30, tzinfo=UTC),
    )

    rows = _jsonl_rows(
        tmp_path / "holdings_history" / "holdings_history.json.parts" / "2026-05-10.jsonl"
    )
    nvda = next(row for row in rows if row["security_id"] == "US67066G1040")
    cash = next(row for row in rows if row["security_id"] == "KRD010010001")

    assert catalog_summary["source_provider_id"] == "rise"
    assert catalog_summary["catalog_entries"] == [
        {
            "brand_id": "brand_kb_asset_management",
            "etf_id": "etf_rise_rf101",
            "source_provider_id": "rise",
            "is_active_strategy_etf": True,
            "active_strategy_source": "name_token",
            "active_strategy_confidence": "low",
        }
    ]
    assert history_summary["run_outcome"] == "succeeded"
    assert history_summary["observed_dates"] == ["2026-05-10"]
    assert nvda["etf_id"] == "etf_rise_rf101"
    assert nvda["etf_name"] == "RISE 액티브 AI ETF"
    assert nvda["source_provider_id"] == "rise"
    assert nvda["weight_percent"] == 7.5
    assert nvda["shares"] == 1500.0
    assert nvda["market_value_krw"] == 240000000.0
    assert cash["is_cash"] is True
    assert cash["weight_percent"] == 0.0
    rendered = json.dumps(history_summary, ensure_ascii=False)
    assert "riseetf.co.kr" not in rendered
    assert "US67066G1040" not in rendered
    assert str(tmp_path) not in rendered


def test_rise_source_provider_rejects_empty_holdings_table(tmp_path: Path) -> None:
    source_module = __import__(
        "agent_treport.signal_report.adapters.source_acquisition",
        fromlist=[""],
    )
    provider = source_module.RiseSourceProvider(
        session=_rise_session(holdings_html="<table></table>")
    )
    source_dir = tmp_path / "source_catalog"
    source_module.collect_source_catalog(
        provider=provider,
        dest_dir=source_dir,
        now=lambda: datetime(2026, 5, 11, 0, 45, tzinfo=UTC),
    )

    summary = source_module.update_holdings_history_source(
        provider=provider,
        source_catalog_path=source_dir / "source_catalog.json",
        universe_state_path=source_dir / "universe_state.json",
        history_dir=tmp_path / "holdings_history",
        requested_date="2026-05-11",
        now=lambda: datetime(2026, 5, 11, 1, 30, tzinfo=UTC),
    )

    assert summary["run_outcome"] == "failed"
    assert summary["target_outcomes"][0]["reason_code"] == (
        "invalid_provider_payload"
    )
    assert not (tmp_path / "holdings_history" / "holdings_history.json").exists()


def test_rise_source_provider_parses_holdings_row_fragment_without_table(
    tmp_path: Path,
) -> None:
    source_module = __import__(
        "agent_treport.signal_report.adapters.source_acquisition",
        fromlist=[""],
    )
    provider = source_module.RiseSourceProvider(
        session=_rise_session(
            holdings_html="""
                <tr>
                  <td>1</td>
                  <td>Synthetic Row Fragment Equity</td>
                  <td>SYNTHETIC-RISE-001</td>
                  <td>1,500</td>
                  <td>7.5</td>
                  <td>240,000,000</td>
                </tr>
            """
        )
    )
    source_dir = tmp_path / "source_catalog"
    source_module.collect_source_catalog(
        provider=provider,
        dest_dir=source_dir,
        now=lambda: datetime(2026, 5, 11, 0, 45, tzinfo=UTC),
    )

    summary = source_module.update_holdings_history_source(
        provider=provider,
        source_catalog_path=source_dir / "source_catalog.json",
        universe_state_path=source_dir / "universe_state.json",
        history_dir=tmp_path / "holdings_history",
        requested_date="2026-05-11",
        now=lambda: datetime(2026, 5, 11, 1, 30, tzinfo=UTC),
    )

    assert summary["run_outcome"] == "succeeded"
    rows = _jsonl_rows(
        tmp_path / "holdings_history" / "holdings_history.json.parts" / "2026-05-11.jsonl"
    )

    assert rows[0]["security_id"] == "SYNTHETIC-RISE-001"
    assert rows[0]["source_provider_id"] == "rise"


def test_rise_source_provider_preserves_coded_rows_with_blank_names(
    tmp_path: Path,
) -> None:
    source_module = __import__(
        "agent_treport.signal_report.adapters.source_acquisition",
        fromlist=[""],
    )
    provider = source_module.RiseSourceProvider(
        session=_rise_session(
            holdings_html="""
                <tr>
                  <td>1</td>
                  <td></td>
                  <td>KRZF00354YFB</td>
                  <td>1,311,992</td>
                  <td>1.28</td>
                  <td>1,290,666</td>
                </tr>
                <tr>
                  <td>2</td>
                  <td>현금성자산</td>
                  <td>KRD010010001</td>
                  <td>201,638</td>
                  <td>0.2</td>
                  <td>201,638</td>
                </tr>
            """
        )
    )
    source_dir = tmp_path / "source_catalog"
    source_module.collect_source_catalog(
        provider=provider,
        dest_dir=source_dir,
        now=lambda: datetime(2026, 5, 11, 0, 45, tzinfo=UTC),
    )

    summary = source_module.update_holdings_history_source(
        provider=provider,
        source_catalog_path=source_dir / "source_catalog.json",
        universe_state_path=source_dir / "universe_state.json",
        history_dir=tmp_path / "holdings_history",
        requested_date="2026-05-11",
        now=lambda: datetime(2026, 5, 11, 1, 30, tzinfo=UTC),
    )

    rows = _jsonl_rows(
        tmp_path / "holdings_history" / "holdings_history.json.parts" / "2026-05-11.jsonl"
    )
    coded = next(row for row in rows if row["security_id"] == "KRZF00354YFB")

    assert summary["run_outcome"] == "succeeded"
    assert coded["name"] == "KRZF00354YFB"
    assert coded["source_provider_id"] == "rise"


def _sol_session(*, pdf_payload: object | None = None) -> _RoutedSourceSession:
    return _RoutedSourceSession(
        {
            ("GET", "/ko/fund"): """
                <a href="/ko/fund/etf/211099" class="fd-link">
                  <span class="fd-name">SOL 액티브 AI ETF</span>
                </a>
            """,
            ("POST", "/api/common/searchByEtfNameOrFilter"): {
                "items": [{"FUND_CD": "211099", "Name": "SOL 액티브 AI ETF"}],
                "toalPage": 1,
            },
            ("GET", "/ko/fund/etf/211099?tabIndex=3"): """
                <html><body>SOL holdings detail</body></html>
            """,
            ("GET", "/ko/fund/etf/211099?tabIndex=1"): """
                <html><body>SOL product detail</body></html>
            """,
            ("GET", "/api/fund/pdfList"): pdf_payload
            if pdf_payload is not None
            else [
                {
                    "WORK_DT": "20260510",
                    "STOCK_CODE": "US67066G1040",
                    "SEC_NM": "NVIDIA Corp.",
                    "QTY": "1,500",
                    "PRICE": "240,000,000",
                    "WT_DISP": "7.5%",
                },
                {
                    "WORK_DT": "20260510",
                    "STOCK_CODE": "KRD010010001",
                    "SEC_NM": "KRW deposit",
                    "QTY": "-1",
                    "PRICE": "-1",
                    "WT_DISP": "",
                },
            ],
        }
    )


def test_sol_source_provider_parses_catalog_and_holdings_without_live_network(
    tmp_path: Path,
) -> None:
    source_module = __import__(
        "agent_treport.signal_report.adapters.source_acquisition",
        fromlist=[""],
    )
    provider = source_module.SolSourceProvider(session=_sol_session())
    source_dir = tmp_path / "source_catalog"

    catalog_summary = source_module.collect_source_catalog(
        provider=provider,
        dest_dir=source_dir,
        now=lambda: datetime(2026, 5, 11, 0, 45, tzinfo=UTC),
    )
    history_summary = source_module.update_holdings_history_source(
        provider=provider,
        source_catalog_path=source_dir / "source_catalog.json",
        universe_state_path=source_dir / "universe_state.json",
        history_dir=tmp_path / "holdings_history",
        requested_date="2026-05-11",
        now=lambda: datetime(2026, 5, 11, 1, 30, tzinfo=UTC),
    )

    rows = _jsonl_rows(
        tmp_path / "holdings_history" / "holdings_history.json.parts" / "2026-05-10.jsonl"
    )
    nvda = next(row for row in rows if row["security_id"] == "US67066G1040")
    cash = next(row for row in rows if row["security_id"] == "KRD010010001")

    assert catalog_summary["source_provider_id"] == "sol"
    assert catalog_summary["catalog_entries"] == [
        {
            "brand_id": "brand_shinhan_asset_management",
            "etf_id": "etf_sol_211099",
            "source_provider_id": "sol",
            "is_active_strategy_etf": True,
            "active_strategy_source": "reference_seed",
            "active_strategy_confidence": "high",
        }
    ]
    assert history_summary["run_outcome"] == "succeeded"
    assert history_summary["observed_dates"] == ["2026-05-10"]
    assert history_summary["target_outcomes"][0]["date_alignment"] == {
        "requested_date": "2026-05-11",
        "observed_date": "2026-05-10",
        "status": "observed_differs_from_provider_query",
    }
    assert nvda["etf_id"] == "etf_sol_211099"
    assert nvda["etf_name"] == "SOL 액티브 AI ETF"
    assert nvda["source_provider_id"] == "sol"
    assert nvda["weight_percent"] == 7.5
    assert nvda["shares"] == 1500.0
    assert nvda["market_value_krw"] == 240000000.0
    assert cash["is_cash"] is True
    assert cash["weight_percent"] == 0.0
    rendered = json.dumps(history_summary, ensure_ascii=False)
    assert "soletf.com" not in rendered
    assert "US67066G1040" not in rendered
    assert str(tmp_path) not in rendered


def test_sol_source_provider_drops_zero_weight_cash_total_row(
    tmp_path: Path,
) -> None:
    source_module = __import__(
        "agent_treport.signal_report.adapters.source_acquisition",
        fromlist=[""],
    )
    provider = source_module.SolSourceProvider(
        session=_sol_session(
            pdf_payload=[
                {
                    "WORK_DT": "20260510",
                    "STOCK_CODE": "CASH00000001",
                    "SEC_NM": "Cash",
                    "QTY": "1,000",
                    "PRICE": "1,000",
                    "WT_DISP": "0%",
                },
                {
                    "WORK_DT": "20260510",
                    "STOCK_CODE": "US67066G1040",
                    "SEC_NM": "NVIDIA Corp.",
                    "QTY": "10",
                    "PRICE": "900",
                    "WT_DISP": "90%",
                },
                {
                    "WORK_DT": "20260510",
                    "STOCK_CODE": "KRD010010001",
                    "SEC_NM": "KRW deposit",
                    "QTY": "100",
                    "PRICE": "100",
                    "WT_DISP": "10%",
                },
            ]
        )
    )
    source_dir = tmp_path / "source_catalog"

    source_module.collect_source_catalog(
        provider=provider,
        dest_dir=source_dir,
        now=lambda: datetime(2026, 5, 11, 0, 45, tzinfo=UTC),
    )
    source_module.update_holdings_history_source(
        provider=provider,
        source_catalog_path=source_dir / "source_catalog.json",
        universe_state_path=source_dir / "universe_state.json",
        history_dir=tmp_path / "holdings_history",
        requested_date="2026-05-11",
        now=lambda: datetime(2026, 5, 11, 1, 30, tzinfo=UTC),
    )

    rows = _jsonl_rows(
        tmp_path / "holdings_history" / "holdings_history.json.parts" / "2026-05-10.jsonl"
    )

    assert {row["security_id"] for row in rows} == {"US67066G1040", "KRD010010001"}


def test_sol_source_provider_rejects_empty_holdings_payload(tmp_path: Path) -> None:
    source_module = __import__(
        "agent_treport.signal_report.adapters.source_acquisition",
        fromlist=[""],
    )
    provider = source_module.SolSourceProvider(session=_sol_session(pdf_payload=[]))
    source_dir = tmp_path / "source_catalog"
    source_module.collect_source_catalog(
        provider=provider,
        dest_dir=source_dir,
        now=lambda: datetime(2026, 5, 11, 0, 45, tzinfo=UTC),
    )

    summary = source_module.update_holdings_history_source(
        provider=provider,
        source_catalog_path=source_dir / "source_catalog.json",
        universe_state_path=source_dir / "universe_state.json",
        history_dir=tmp_path / "holdings_history",
        requested_date="2026-05-11",
        now=lambda: datetime(2026, 5, 11, 1, 30, tzinfo=UTC),
    )

    assert summary["run_outcome"] == "failed"
    assert summary["target_outcomes"][0]["reason_code"] == (
        "invalid_provider_payload"
    )
    assert not (tmp_path / "holdings_history" / "holdings_history.json").exists()


def test_live_source_provider_registry_enumerates_all_rollout_ids() -> None:
    source_module = __import__(
        "agent_treport.signal_report.adapters.source_acquisition",
        fromlist=[""],
    )

    assert source_module.LIVE_SOURCE_PROVIDER_IDS == (
        "kodex",
        "ace",
        "hyundai",
        "timefolio",
        "tiger",
        "rise",
        "sol",
    )
    providers = [
        source_module.create_live_source_provider(provider_id, session=object())
        for provider_id in source_module.LIVE_SOURCE_PROVIDER_IDS
    ]

    assert [provider.source_provider_id for provider in providers] == list(
        source_module.LIVE_SOURCE_PROVIDER_IDS
    )
    with pytest.raises(source_module.SourceAcquisitionInputError):
        source_module.create_live_source_provider("fake", session=object())


@pytest.mark.parametrize(
    ("provider_class_name", "session_factory", "source_provider_id"),
    [
        ("KodexSourceProvider", _FakeKodexSession, "kodex"),
        ("AceSourceProvider", _ace_session, "ace"),
        ("HyundaiSourceProvider", _hyundai_session, "hyundai"),
        ("TimefolioSourceProvider", _timefolio_session, "timefolio"),
        ("TigerSourceProvider", _tiger_session, "tiger"),
        ("RiseSourceProvider", _rise_session, "rise"),
        ("SolSourceProvider", _sol_session, "sol"),
    ],
)
def test_each_live_source_provider_fixture_feeds_native_history_contract(
    tmp_path: Path,
    provider_class_name: str,
    session_factory: Callable[[], object],
    source_provider_id: str,
) -> None:
    source_module = __import__(
        "agent_treport.signal_report.adapters.source_acquisition",
        fromlist=[""],
    )
    provider_class = getattr(source_module, provider_class_name)
    provider = provider_class(session=session_factory())
    source_dir = tmp_path / source_provider_id / "source_catalog"
    history_dir = tmp_path / source_provider_id / "holdings_history"

    source_module.collect_source_catalog(
        provider=provider,
        dest_dir=source_dir,
        now=lambda: datetime(2026, 5, 11, 0, 45, tzinfo=UTC),
    )
    summary = source_module.update_holdings_history_source(
        provider=provider,
        source_catalog_path=source_dir / "source_catalog.json",
        universe_state_path=source_dir / "universe_state.json",
        history_dir=history_dir,
        requested_date="2026-05-11",
        now=lambda: datetime(2026, 5, 11, 1, 30, tzinfo=UTC),
    )

    rows = _jsonl_rows(
        history_dir / "holdings_history.json.parts" / "2026-05-10.jsonl"
    )

    assert summary["run_outcome"] == "succeeded"
    assert summary["source_provider_id"] == source_provider_id
    assert summary["observed_dates"] == ["2026-05-10"]
    assert rows
    assert {row["source_provider_id"] for row in rows} == {source_provider_id}
    assert {
        "etf_id",
        "etf_name",
        "brand_id",
        "source_provider_id",
        "as_of_date",
        "security_id",
        "ticker",
        "name",
        "market",
        "sector",
        "theme",
        "country",
        "weight_percent",
        "shares",
        "market_value_krw",
        "price_krw",
        "is_cash",
        "security_classification",
    }.issubset(rows[0])


@pytest.mark.parametrize(
    ("provider_class_name", "session_factory", "source_provider_id", "forbidden"),
    [
        (
            "KodexSourceProvider",
            _FakeKodexSession,
            "kodex",
            ("samsungfund.com", "US67066G1040", "/api/", "NVIDIA Corp."),
        ),
        (
            "AceSourceProvider",
            _ace_session,
            "ace",
            ("aceetf.co.kr", "US67066G1040", "/api/", "NVIDIA Corp."),
        ),
        (
            "HyundaiSourceProvider",
            _hyundai_session,
            "hyundai",
            ("hyundaiam.com", "US67066G1040", "/api/", "NVIDIA Corp."),
        ),
        (
            "TimefolioSourceProvider",
            _timefolio_session,
            "timefolio",
            ("timeetf.co.kr", "US67066G1040", "m11_view", "NVIDIA Corp."),
        ),
        (
            "TigerSourceProvider",
            _tiger_session,
            "tiger",
            ("miraeasset.com", "US67066G1040", "ajax", "NVIDIA Corp."),
        ),
        (
            "RiseSourceProvider",
            _rise_session,
            "rise",
            ("riseetf.co.kr", "US67066G1040", "productView", "NVIDIA Corp."),
        ),
        (
            "SolSourceProvider",
            _sol_session,
            "sol",
            ("soletf.com", "US67066G1040", "pdfList", "NVIDIA Corp."),
        ),
    ],
)
def test_live_source_provider_summaries_remain_path_safe(
    tmp_path: Path,
    provider_class_name: str,
    session_factory: Callable[[], object],
    source_provider_id: str,
    forbidden: tuple[str, ...],
) -> None:
    source_module = __import__(
        "agent_treport.signal_report.adapters.source_acquisition",
        fromlist=[""],
    )
    provider_class = getattr(source_module, provider_class_name)
    provider = provider_class(session=session_factory())
    source_dir = tmp_path / source_provider_id / "source_catalog"
    history_dir = tmp_path / source_provider_id / "holdings_history"

    catalog_summary = source_module.collect_source_catalog(
        provider=provider,
        dest_dir=source_dir,
        now=lambda: datetime(2026, 5, 11, 0, 45, tzinfo=UTC),
    )
    history_summary = source_module.update_holdings_history_source(
        provider=provider,
        source_catalog_path=source_dir / "source_catalog.json",
        universe_state_path=source_dir / "universe_state.json",
        history_dir=history_dir,
        requested_date="2026-05-11",
        now=lambda: datetime(2026, 5, 11, 1, 30, tzinfo=UTC),
    )

    rendered = json.dumps(
        {"catalog": catalog_summary, "history": history_summary},
        ensure_ascii=False,
    )

    assert "https://" not in rendered
    assert str(tmp_path) not in rendered
    for value in forbidden:
        assert value not in rendered


def test_source_provider_stops_reusing_host_after_blocked_response() -> None:
    source_module = __import__(
        "agent_treport.signal_report.adapters.source_acquisition",
        fromlist=[""],
    )
    session = _BlockedKodexSession(status_code=403)
    provider = source_module.KodexSourceProvider(session=session)
    target = source_module.HoldingsFetchTarget(
        source_provider_id="kodex",
        provider_etf_id="2ETF35",
        etf_id="etf_kodex_2etf35",
        requested_date="2026-05-11",
        provider_query_date="2026-05-11",
    )

    first = provider.fetch_holdings(target)
    second = provider.fetch_holdings(target)

    assert first.outcome == "rate_limited"
    assert first.failure_code_class == "blocked"
    assert second.outcome == "rate_limited"
    assert second.failure_code_class == "blocked"
    assert len(session.get_calls) == 1


def test_source_provider_classifies_rate_limited_response() -> None:
    source_module = __import__(
        "agent_treport.signal_report.adapters.source_acquisition",
        fromlist=[""],
    )
    session = _BlockedKodexSession(status_code=429)
    provider = source_module.KodexSourceProvider(session=session)
    target = source_module.HoldingsFetchTarget(
        source_provider_id="kodex",
        provider_etf_id="2ETF35",
        etf_id="etf_kodex_2etf35",
        requested_date="2026-05-11",
        provider_query_date="2026-05-11",
    )

    result = provider.fetch_holdings(target)

    assert result.outcome == "rate_limited"
    assert result.failure_code_class == "rate_limited"


def test_kodex_source_provider_parses_catalog_and_holdings_without_live_network(
    tmp_path: Path,
) -> None:
    source_module = __import__(
        "agent_treport.signal_report.adapters.source_acquisition",
        fromlist=[""],
    )
    session = _FakeKodexSession()
    provider = source_module.KodexSourceProvider(session=session)
    source_dir = tmp_path / "source_catalog"

    catalog_summary = source_module.collect_source_catalog(
        provider=provider,
        dest_dir=source_dir,
        now=lambda: datetime(2026, 5, 11, 0, 45, tzinfo=UTC),
    )
    history_summary = source_module.update_holdings_history_source(
        provider=provider,
        source_catalog_path=source_dir / "source_catalog.json",
        universe_state_path=source_dir / "universe_state.json",
        history_dir=tmp_path / "holdings_history",
        requested_date="2026-05-11",
        now=lambda: datetime(2026, 5, 11, 1, 30, tzinfo=UTC),
    )

    rows = _jsonl_rows(
        tmp_path / "holdings_history" / "holdings_history.json.parts" / "2026-05-10.jsonl"
    )
    nvda = next(row for row in rows if row["security_id"] == "US67066G1040")

    assert catalog_summary["source_provider_id"] == "kodex"
    assert catalog_summary["catalog_entries"] == [
        {
            "brand_id": "brand_samsung_asset_management",
            "etf_id": "etf_kodex_2etf99",
            "source_provider_id": "kodex",
            "is_active_strategy_etf": True,
            "active_strategy_source": "source_metadata",
            "active_strategy_confidence": "high",
        }
    ]
    assert history_summary["run_outcome"] == "succeeded"
    assert history_summary["requested_dates"] == ["2026-05-11"]
    assert history_summary["observed_dates"] == ["2026-05-10"]
    assert nvda["etf_id"] == "etf_kodex_2etf99"
    assert nvda["name"] == "NVIDIA Corp."
    assert nvda["weight_percent"] == 7.5
    assert nvda["shares"] == 1500.0
    assert nvda["market_value_krw"] == 240000000.0
    rendered = json.dumps(history_summary, ensure_ascii=False)
    assert "www.samsungfund.com" not in rendered
    assert "US67066G1040" not in rendered


def test_kodex_source_provider_accepts_cash_deposit_rows_without_ratio(
    tmp_path: Path,
) -> None:
    source_module = __import__(
        "agent_treport.signal_report.adapters.source_acquisition",
        fromlist=[""],
    )
    session = _FakeKodexSession()
    provider = source_module.KodexSourceProvider(session=session)
    source_dir = tmp_path / "source_catalog"

    source_module.collect_source_catalog(
        provider=provider,
        dest_dir=source_dir,
        now=lambda: datetime(2026, 5, 11, 0, 45, tzinfo=UTC),
    )
    source_module.update_holdings_history_source(
        provider=provider,
        source_catalog_path=source_dir / "source_catalog.json",
        universe_state_path=source_dir / "universe_state.json",
        history_dir=tmp_path / "holdings_history",
        requested_date="2026-05-11",
        now=lambda: datetime(2026, 5, 11, 1, 30, tzinfo=UTC),
    )

    rows = _jsonl_rows(
        tmp_path / "holdings_history" / "holdings_history.json.parts" / "2026-05-10.jsonl"
    )
    cash = next(row for row in rows if row["security_id"] == "KRD010010001")

    assert cash["is_cash"] is True
    assert cash["security_classification"] == "cash_like"
    assert cash["weight_percent"] == 0.0


def test_kodex_source_provider_derives_missing_cash_weight_when_market_fit_is_valid(
    tmp_path: Path,
) -> None:
    source_module = __import__(
        "agent_treport.signal_report.adapters.source_acquisition",
        fromlist=[""],
    )
    session = _RoutedSourceSession(
        {
            ("GET", "/api/v1/kodex/product.do"): [
                {
                    "fId": "2ETF99",
                    "fNm": "KODEX AI ETF",
                    "typeNm": "active",
                    "totalCnt": "1",
                }
            ],
            ("GET", "/api/v1/kodex/product/2ETF99.do"): {
                "info": {"product": {"listD": "2024.01.02"}},
                "pdf": {"gijunYMD": "20260509", "list": []},
            },
            ("GET", "/api/v1/kodex/product-pdf/2ETF99.do"): {
                "pdf": {
                    "gijunYMD": "2026.05.10",
                    "list": [
                        {
                            "itmNo": "US67066G1040",
                            "secNm": "NVIDIA Corp.",
                            "applyQ": "10",
                            "evalA": "900",
                            "ratio": "90",
                        },
                        {
                            "itmNo": "KRD010010001",
                            "secNm": "KRW deposit",
                            "applyQ": "100",
                            "evalA": "100",
                            "ratio": None,
                        },
                    ],
                }
            },
        }
    )
    provider = source_module.KodexSourceProvider(session=session)
    source_dir = tmp_path / "source_catalog"

    source_module.collect_source_catalog(
        provider=provider,
        dest_dir=source_dir,
        now=lambda: datetime(2026, 5, 11, 0, 45, tzinfo=UTC),
    )
    source_module.update_holdings_history_source(
        provider=provider,
        source_catalog_path=source_dir / "source_catalog.json",
        universe_state_path=source_dir / "universe_state.json",
        history_dir=tmp_path / "holdings_history",
        requested_date="2026-05-11",
        now=lambda: datetime(2026, 5, 11, 1, 30, tzinfo=UTC),
    )

    rows = _jsonl_rows(
        tmp_path / "holdings_history" / "holdings_history.json.parts" / "2026-05-10.jsonl"
    )
    cash = next(row for row in rows if row["security_id"] == "KRD010010001")

    assert cash["is_cash"] is True
    assert cash["weight_percent"] == 10.0


def test_kodex_source_provider_uses_provider_query_date_for_weekend_request(
    tmp_path: Path,
) -> None:
    source_module = __import__(
        "agent_treport.signal_report.adapters.source_acquisition",
        fromlist=[""],
    )
    session = _FakeKodexSession()
    provider = source_module.KodexSourceProvider(session=session)
    source_dir = tmp_path / "source_catalog"

    source_module.collect_source_catalog(
        provider=provider,
        dest_dir=source_dir,
        now=lambda: datetime(2026, 5, 16, 0, 45, tzinfo=UTC),
    )
    summary = source_module.update_holdings_history_source(
        provider=provider,
        source_catalog_path=source_dir / "source_catalog.json",
        universe_state_path=source_dir / "universe_state.json",
        history_dir=tmp_path / "holdings_history",
        requested_date="2026-05-16",
        now=lambda: datetime(2026, 5, 16, 1, 30, tzinfo=UTC),
    )

    pdf_call = next(
        kwargs for url, kwargs in session.get_calls if url.endswith("/product-pdf/2ETF99.do")
    )
    assert pdf_call["params"] == {"gijunYMD": "2026.05.15"}
    assert "provider_query_date" not in summary["target_outcomes"][0]
    assert summary["target_outcomes"][0]["date_alignment"]["status"] == (
        "observed_differs_from_provider_query"
    )


def test_update_holdings_history_fixture_skips_matching_existing_snapshots(
    tmp_path: Path,
) -> None:
    universe_fixture_path = _write_native_universe_fixture(
        tmp_path,
        brands=[
            {
                "brand_id": "brand_alpha",
                "brand_name": "Alpha Asset Management",
                "source_provider_id": "provider_universe_fixture",
            }
        ],
        etfs=[
            {
                "etf_id": "etf_focus_ai",
                "etf_name": "Tracked AI ETF",
                "brand_id": "brand_alpha",
                "source_provider_id": "provider_universe_fixture",
            }
        ],
    )
    universe_dir = tmp_path / "native_universe"
    collect_universe_fixture(
        fixture_path=universe_fixture_path,
        dest_dir=universe_dir,
        now=lambda: datetime(2026, 5, 11, 0, 0, tzinfo=UTC),
    )
    fixture_path = _write_native_collection_fixture(tmp_path)
    history_dir = tmp_path / "holdings_history"
    update_holdings_history_fixture(
        fixture_path=fixture_path,
        universe_state_path=universe_dir / "universe_state.json",
        history_dir=history_dir,
        observed_partitions=2,
        now=lambda: datetime(2026, 5, 11, 1, 30, tzinfo=UTC),
    )
    manifest_path = history_dir / "holdings_history.json"
    stored_before = json.loads(manifest_path.read_text(encoding="utf-8"))

    summary = update_holdings_history_fixture(
        fixture_path=fixture_path,
        universe_state_path=universe_dir / "universe_state.json",
        history_dir=history_dir,
        observed_partitions=2,
        now=lambda: datetime(2026, 5, 11, 2, 30, tzinfo=UTC),
    )

    stored_after = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert summary["added_snapshot_count"] == 0
    assert summary["skipped_snapshot_count"] == 2
    assert summary["refreshed_snapshot_count"] == 0
    assert summary["conflict_snapshot_count"] == 0
    assert summary["row_count"] == 0
    assert summary["skipped_snapshots"] == [
        {"etf_id": "etf_focus_ai", "observed_date": "2026-05-11", "row_count": 2},
        {"etf_id": "etf_focus_ai", "observed_date": "2026-05-08", "row_count": 1},
    ]
    assert stored_after == stored_before


def test_update_holdings_history_fixture_rejects_changed_duplicate_without_refresh(
    tmp_path: Path,
) -> None:
    universe_fixture_path = _write_native_universe_fixture(
        tmp_path,
        brands=[
            {
                "brand_id": "brand_alpha",
                "brand_name": "Alpha Asset Management",
                "source_provider_id": "provider_universe_fixture",
            }
        ],
        etfs=[
            {
                "etf_id": "etf_focus_ai",
                "etf_name": "Tracked AI ETF",
                "brand_id": "brand_alpha",
                "source_provider_id": "provider_universe_fixture",
            }
        ],
    )
    universe_dir = tmp_path / "native_universe"
    collect_universe_fixture(
        fixture_path=universe_fixture_path,
        dest_dir=universe_dir,
        now=lambda: datetime(2026, 5, 11, 0, 0, tzinfo=UTC),
    )
    fixture_path = _write_native_collection_fixture(tmp_path)
    history_dir = tmp_path / "holdings_history"
    update_holdings_history_fixture(
        fixture_path=fixture_path,
        universe_state_path=universe_dir / "universe_state.json",
        history_dir=history_dir,
        observed_partitions=2,
        now=lambda: datetime(2026, 5, 11, 1, 30, tzinfo=UTC),
    )
    manifest_path = history_dir / "holdings_history.json"
    stored_before = json.loads(manifest_path.read_text(encoding="utf-8"))
    changed_fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    changed_fixture["snapshots"][0]["holdings"][0]["weight_percent"] = 8.25
    fixture_path.write_text(
        json.dumps(changed_fixture, ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(
        OperationalHoldingsInputError,
        match=(
            "refresh required for holdings history snapshot: "
            "etf_id=etf_focus_ai observed_date=2026-05-11 row_count=2"
        ),
    ):
        update_holdings_history_fixture(
            fixture_path=fixture_path,
            universe_state_path=universe_dir / "universe_state.json",
            history_dir=history_dir,
            observed_partitions=2,
            now=lambda: datetime(2026, 5, 11, 2, 30, tzinfo=UTC),
        )

    stored_after = json.loads(manifest_path.read_text(encoding="utf-8"))
    latest_rows = _jsonl_rows(
        history_dir / "holdings_history.json.parts" / "2026-05-11.jsonl"
    )
    nvda = next(row for row in latest_rows if row["security_id"] == "sec_nvda")
    assert stored_after == stored_before
    assert nvda["weight_percent"] == 7.5


def test_update_holdings_history_fixture_refreshes_only_selected_snapshot(
    tmp_path: Path,
) -> None:
    universe_fixture_path = _write_native_universe_fixture(
        tmp_path,
        brands=[
            {
                "brand_id": "brand_alpha",
                "brand_name": "Alpha Asset Management",
                "source_provider_id": "provider_universe_fixture",
            }
        ],
        etfs=[
            {
                "etf_id": "etf_focus_ai",
                "etf_name": "Tracked AI ETF",
                "brand_id": "brand_alpha",
                "source_provider_id": "provider_universe_fixture",
            },
            {
                "etf_id": "etf_peer_ai",
                "etf_name": "Tracked Peer ETF",
                "brand_id": "brand_alpha",
                "source_provider_id": "provider_universe_fixture",
            },
        ],
    )
    universe_dir = tmp_path / "native_universe"
    collect_universe_fixture(
        fixture_path=universe_fixture_path,
        dest_dir=universe_dir,
        now=lambda: datetime(2026, 5, 11, 0, 0, tzinfo=UTC),
    )
    history_dir = tmp_path / "holdings_history"
    initial_fixture = _write_two_etf_holdings_fixture(tmp_path)
    update_holdings_history_fixture(
        fixture_path=initial_fixture,
        universe_state_path=universe_dir / "universe_state.json",
        history_dir=history_dir,
        observed_partitions=2,
        now=lambda: datetime(2026, 5, 11, 1, 30, tzinfo=UTC),
    )
    changed_fixture = _write_two_etf_holdings_fixture(
        tmp_path,
        focus_current_weight=8.25,
        filename="changed_two_etf_holdings_fixture.json",
    )

    summary = update_holdings_history_fixture(
        fixture_path=changed_fixture,
        universe_state_path=universe_dir / "universe_state.json",
        history_dir=history_dir,
        observed_partitions=2,
        refresh_snapshots=(("etf_focus_ai", "2026-05-11"),),
        now=lambda: datetime(2026, 5, 11, 2, 30, tzinfo=UTC),
    )

    current_rows = _jsonl_rows(
        history_dir / "holdings_history.json.parts" / "2026-05-11.jsonl"
    )
    previous_rows = _jsonl_rows(
        history_dir / "holdings_history.json.parts" / "2026-05-08.jsonl"
    )
    current_by_etf = {row["etf_id"]: row for row in current_rows}
    previous_by_etf = {row["etf_id"]: row for row in previous_rows}
    assert summary["added_snapshot_count"] == 0
    assert summary["skipped_snapshot_count"] == 3
    assert summary["refreshed_snapshot_count"] == 1
    assert summary["conflict_snapshot_count"] == 0
    assert summary["row_count"] == 1
    assert summary["refreshed_snapshots"] == [
        {"etf_id": "etf_focus_ai", "observed_date": "2026-05-11", "row_count": 1}
    ]
    assert current_by_etf["etf_focus_ai"]["weight_percent"] == 8.25
    assert current_by_etf["etf_peer_ai"]["weight_percent"] == 5.0
    assert previous_by_etf["etf_focus_ai"]["weight_percent"] == 6.0
    assert previous_by_etf["etf_peer_ai"]["weight_percent"] == 4.0


def test_update_holdings_history_fixture_rejects_unselected_refresh_snapshot(
    tmp_path: Path,
) -> None:
    universe_fixture_path = _write_native_universe_fixture(
        tmp_path,
        brands=[
            {
                "brand_id": "brand_alpha",
                "brand_name": "Alpha Asset Management",
                "source_provider_id": "provider_universe_fixture",
            }
        ],
        etfs=[
            {
                "etf_id": "etf_focus_ai",
                "etf_name": "Tracked AI ETF",
                "brand_id": "brand_alpha",
                "source_provider_id": "provider_universe_fixture",
            }
        ],
    )
    universe_dir = tmp_path / "native_universe"
    collect_universe_fixture(
        fixture_path=universe_fixture_path,
        dest_dir=universe_dir,
        now=lambda: datetime(2026, 5, 11, 0, 0, tzinfo=UTC),
    )
    fixture_path = _write_native_collection_fixture(tmp_path)

    with pytest.raises(
        OperationalHoldingsInputError,
        match=(
            "refresh snapshot was not selected: "
            "etf_id=etf_focus_ai observed_date=2026-05-07"
        ),
    ):
        update_holdings_history_fixture(
            fixture_path=fixture_path,
            universe_state_path=universe_dir / "universe_state.json",
            history_dir=tmp_path / "holdings_history",
            observed_partitions=2,
            refresh_snapshots=(("etf_focus_ai", "2026-05-07"),),
            now=lambda: datetime(2026, 5, 11, 1, 30, tzinfo=UTC),
        )

    assert not (tmp_path / "holdings_history" / "holdings_history.json").exists()


def test_export_latest_holdings_comparison_excludes_removed_etfs_from_default_export(
    tmp_path: Path,
) -> None:
    initial_universe_fixture_path = _write_native_universe_fixture(
        tmp_path,
        brands=[
            {
                "brand_id": "brand_alpha",
                "brand_name": "Alpha Asset Management",
                "source_provider_id": "provider_universe_fixture",
            }
        ],
        etfs=[
            {
                "etf_id": "etf_focus_ai",
                "etf_name": "Tracked AI ETF",
                "brand_id": "brand_alpha",
                "source_provider_id": "provider_universe_fixture",
            },
            {
                "etf_id": "etf_peer_ai",
                "etf_name": "Removed Peer ETF",
                "brand_id": "brand_alpha",
                "source_provider_id": "provider_universe_fixture",
            },
        ],
    )
    active_only_universe_fixture_path = _write_native_universe_fixture(
        tmp_path,
        filename="active_only_universe_fixture.json",
        brands=[
            {
                "brand_id": "brand_alpha",
                "brand_name": "Alpha Asset Management",
                "source_provider_id": "provider_universe_fixture",
            }
        ],
        etfs=[
            {
                "etf_id": "etf_focus_ai",
                "etf_name": "Tracked AI ETF",
                "brand_id": "brand_alpha",
                "source_provider_id": "provider_universe_fixture",
            }
        ],
    )
    universe_dir = tmp_path / "native_universe"
    collect_universe_fixture(
        fixture_path=initial_universe_fixture_path,
        dest_dir=universe_dir,
        now=lambda: datetime(2026, 5, 11, 0, 0, tzinfo=UTC),
    )
    history_dir = tmp_path / "holdings_history"
    update_holdings_history_fixture(
        fixture_path=_write_two_etf_holdings_fixture(tmp_path),
        universe_state_path=universe_dir / "universe_state.json",
        history_dir=history_dir,
        observed_partitions=2,
        now=lambda: datetime(2026, 5, 11, 1, 30, tzinfo=UTC),
    )
    collect_universe_fixture(
        fixture_path=active_only_universe_fixture_path,
        dest_dir=universe_dir,
        now=lambda: datetime(2026, 5, 12, 0, 0, tzinfo=UTC),
    )
    export_dir = tmp_path / "latest_export"

    summary = export_latest_holdings_comparison(
        history_dir=history_dir,
        universe_state_path=universe_dir / "universe_state.json",
        dest_dir=export_dir,
        now=lambda: datetime(2026, 5, 12, 1, 30, tzinfo=UTC),
    )

    manifest_path = export_dir / "url_holdings_cumulative.json"
    summary_path = export_dir / "collection_summary.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    current_rows = _jsonl_rows(
        export_dir / "url_holdings_cumulative.json.parts" / "2026-05-11.jsonl"
    )
    previous_rows = _jsonl_rows(
        export_dir / "url_holdings_cumulative.json.parts" / "2026-05-08.jsonl"
    )
    stored_history_rows = _jsonl_rows(
        history_dir / "holdings_history.json.parts" / "2026-05-11.jsonl"
    )

    assert summary == json.loads(summary_path.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "agent_treport.operational_holdings.v1"
    assert manifest["storage_format"] == "normalized_partitioned_jsonl_v1"
    assert manifest["collection_source_type"] == "native_history"
    assert manifest["dates"] == ["2026-05-11", "2026-05-08"]
    assert manifest["record_count"] == 2
    assert {row["etf_id"] for row in current_rows} == {"etf_focus_ai"}
    assert {row["etf_id"] for row in previous_rows} == {"etf_focus_ai"}
    assert {row["etf_id"] for row in stored_history_rows} == {
        "etf_focus_ai",
        "etf_peer_ai",
    }
    assert summary["schema_version"] == "agent_treport.native_collection.summary.v1"
    assert summary["collection_source_type"] == "native_history"
    assert summary["observed_dates"] == ["2026-05-11", "2026-05-08"]
    assert summary["active_etf_coverage"] == {
        "selected_current_date": "2026-05-11",
        "selected_previous_date": "2026-05-08",
        "active_etf_count": 1,
        "complete_active_etf_count": 1,
        "missing_active_etf_ids": [],
        "coverage_ratio": 1.0,
    }
    assert summary["normalized_output"] == {
        "manifest_path": "url_holdings_cumulative.json",
        "fingerprint": compute_operational_export_fingerprint(manifest_path),
    }
    rendered_summary = json.dumps(summary, ensure_ascii=False)
    assert str(tmp_path) not in rendered_summary
    assert "etf_peer_ai" not in json.dumps(manifest, ensure_ascii=False)


def test_export_latest_holdings_comparison_uses_per_etf_windows(
    tmp_path: Path,
) -> None:
    universe_fixture_path = _write_native_universe_fixture(
        tmp_path,
        brands=[
            {
                "brand_id": "brand_alpha",
                "brand_name": "Alpha Asset Management",
                "source_provider_id": "provider_universe_fixture",
            }
        ],
        etfs=[
            {
                "etf_id": "etf_alpha",
                "etf_name": "Alpha Active ETF",
                "brand_id": "brand_alpha",
                "source_provider_id": "provider_universe_fixture",
            },
            {
                "etf_id": "etf_beta",
                "etf_name": "Beta Active ETF",
                "brand_id": "brand_alpha",
                "source_provider_id": "provider_universe_fixture",
            },
            {
                "etf_id": "etf_gamma",
                "etf_name": "Gamma Active ETF",
                "brand_id": "brand_alpha",
                "source_provider_id": "provider_universe_fixture",
            },
        ],
    )
    universe_dir = tmp_path / "native_universe"
    collect_universe_fixture(
        fixture_path=universe_fixture_path,
        dest_dir=universe_dir,
        now=lambda: datetime(2026, 5, 15, 0, 0, tzinfo=UTC),
    )
    history_dir = tmp_path / "holdings_history"
    update_holdings_history_fixture(
        fixture_path=_write_mixed_window_holdings_fixture(tmp_path),
        universe_state_path=universe_dir / "universe_state.json",
        history_dir=history_dir,
        observed_partitions=3,
        now=lambda: datetime(2026, 5, 15, 1, 30, tzinfo=UTC),
    )
    export_dir = tmp_path / "latest_export"

    summary = export_latest_holdings_comparison(
        history_dir=history_dir,
        universe_state_path=universe_dir / "universe_state.json",
        dest_dir=export_dir,
        now=lambda: datetime(2026, 5, 15, 2, 30, tzinfo=UTC),
    )

    manifest = json.loads(
        (export_dir / "url_holdings_cumulative.json").read_text(encoding="utf-8")
    )
    assert manifest["dates"] == ["2026-05-15", "2026-05-14", "2026-05-13"]
    assert summary["active_etf_coverage"] == {
        "selected_current_date": "2026-05-15",
        "selected_previous_date": "2026-05-14",
        "active_etf_count": 3,
        "complete_active_etf_count": 3,
        "missing_active_etf_ids": [],
        "coverage_ratio": 1.0,
        "mixed_comparison_windows": True,
        "comparison_windows": [
            {
                "etf_id": "etf_alpha",
                "selected_current_date": "2026-05-15",
                "selected_previous_date": "2026-05-14",
            },
            {
                "etf_id": "etf_beta",
                "selected_current_date": "2026-05-15",
                "selected_previous_date": "2026-05-13",
            },
            {
                "etf_id": "etf_gamma",
                "selected_current_date": "2026-05-14",
                "selected_previous_date": "2026-05-13",
            },
        ],
    }


def test_export_latest_holdings_comparison_applies_reviewed_mapping_without_changing_history(
    tmp_path: Path,
) -> None:
    universe_fixture_path = _write_native_universe_fixture(
        tmp_path,
        brands=[
            {
                "brand_id": "brand_alpha",
                "brand_name": "Alpha Asset Management",
                "source_provider_id": "provider_universe_fixture",
            }
        ],
        etfs=[
            {
                "etf_id": "etf_focus_ai",
                "etf_name": "Tracked AI ETF",
                "brand_id": "brand_alpha",
                "source_provider_id": "provider_universe_fixture",
            },
            {
                "etf_id": "etf_peer_ai",
                "etf_name": "Peer AI ETF",
                "brand_id": "brand_alpha",
                "source_provider_id": "provider_universe_fixture",
            },
        ],
    )
    universe_dir = tmp_path / "native_universe"
    collect_universe_fixture(
        fixture_path=universe_fixture_path,
        dest_dir=universe_dir,
        now=lambda: datetime(2026, 5, 11, 0, 0, tzinfo=UTC),
    )
    history_dir = tmp_path / "holdings_history"
    update_holdings_history_fixture(
        fixture_path=_write_two_etf_holdings_fixture(tmp_path),
        universe_state_path=universe_dir / "universe_state.json",
        history_dir=history_dir,
        observed_partitions=2,
        now=lambda: datetime(2026, 5, 11, 1, 30, tzinfo=UTC),
    )
    resolution_path = _write_security_resolution(
        tmp_path,
        mappings=[
            {
                "security_id": "sec_nvda",
                "ticker": "NVDA_REVIEWED",
                "name": "NVIDIA Corp.",
                "exchange": "NASDAQ",
                "security_classification": "ticker_candidate",
            }
        ],
        exclusions=[],
    )
    export_dir = tmp_path / "latest_export"

    export_latest_holdings_comparison(
        history_dir=history_dir,
        universe_state_path=universe_dir / "universe_state.json",
        dest_dir=export_dir,
        security_resolution_path=resolution_path,
        now=lambda: datetime(2026, 5, 12, 1, 30, tzinfo=UTC),
    )

    current_rows = _jsonl_rows(
        export_dir / "url_holdings_cumulative.json.parts" / "2026-05-11.jsonl"
    )
    history_rows = _jsonl_rows(
        history_dir / "holdings_history.json.parts" / "2026-05-11.jsonl"
    )
    exported_nvda = next(row for row in current_rows if row["security_id"] == "sec_nvda")
    stored_nvda = next(row for row in history_rows if row["security_id"] == "sec_nvda")

    assert exported_nvda["security_id"] == "sec_nvda"
    assert exported_nvda["ticker"] == "NVDA_REVIEWED"
    assert exported_nvda["security_classification"] == "ticker_candidate"
    assert stored_nvda["ticker"] == "NVDA"
    assert stored_nvda["security_classification"] == "ticker_candidate"


def test_export_latest_holdings_comparison_applies_reviewed_exclusion_to_export_coverage(
    tmp_path: Path,
) -> None:
    universe_fixture_path = _write_native_universe_fixture(
        tmp_path,
        brands=[
            {
                "brand_id": "brand_alpha",
                "brand_name": "Alpha Asset Management",
                "source_provider_id": "provider_universe_fixture",
            }
        ],
        etfs=[
            {
                "etf_id": "etf_focus_ai",
                "etf_name": "Tracked AI ETF",
                "brand_id": "brand_alpha",
                "source_provider_id": "provider_universe_fixture",
            },
            {
                "etf_id": "etf_peer_ai",
                "etf_name": "Peer AI ETF",
                "brand_id": "brand_alpha",
                "source_provider_id": "provider_universe_fixture",
            },
        ],
    )
    universe_dir = tmp_path / "native_universe"
    collect_universe_fixture(
        fixture_path=universe_fixture_path,
        dest_dir=universe_dir,
        now=lambda: datetime(2026, 5, 11, 0, 0, tzinfo=UTC),
    )
    history_dir = tmp_path / "holdings_history"
    update_holdings_history_fixture(
        fixture_path=_write_two_etf_holdings_fixture(tmp_path),
        universe_state_path=universe_dir / "universe_state.json",
        history_dir=history_dir,
        observed_partitions=2,
        now=lambda: datetime(2026, 5, 11, 1, 30, tzinfo=UTC),
    )
    resolution_path = _write_security_resolution(
        tmp_path,
        mappings=[],
        exclusions=[
            {
                "security_id": "sec_msft",
                "name": "Microsoft Corp.",
                "security_classification": "non_equity",
                "reason": "reviewed_fund_holding_exclusion",
            }
        ],
    )
    export_dir = tmp_path / "latest_export"

    summary = export_latest_holdings_comparison(
        history_dir=history_dir,
        universe_state_path=universe_dir / "universe_state.json",
        dest_dir=export_dir,
        security_resolution_path=resolution_path,
        now=lambda: datetime(2026, 5, 12, 1, 30, tzinfo=UTC),
    )

    current_rows = _jsonl_rows(
        export_dir / "url_holdings_cumulative.json.parts" / "2026-05-11.jsonl"
    )
    history_rows = _jsonl_rows(
        history_dir / "holdings_history.json.parts" / "2026-05-11.jsonl"
    )
    exported_msft = next(row for row in current_rows if row["security_id"] == "sec_msft")
    stored_msft = next(row for row in history_rows if row["security_id"] == "sec_msft")

    assert exported_msft["security_classification"] == "non_equity"
    assert exported_msft["ticker"] is None
    assert stored_msft["ticker"] == "MSFT"
    assert stored_msft["security_classification"] == "ticker_candidate"
    assert summary["security_coverage"] == {
        "security_resolution_available": True,
        "mapped_ticker_candidate_count": 2,
        "unresolved_ticker_candidate_count": 0,
        "unknown_count": 0,
        "non_ticker_excluded_count": 2,
        "reviewed_mapping_applied_count": 0,
        "reviewed_exclusion_applied_count": 2,
        "ticker_mapping_coverage_ratio": 1.0,
        "ticker_collision_review_count": 0,
        "ticker_collision_review_samples": [],
        "recovery_sample_count": 0,
        "recovery_samples": [],
    }


def test_export_latest_holdings_comparison_writes_path_safe_security_coverage_samples(
    tmp_path: Path,
) -> None:
    universe_fixture_path = _write_native_universe_fixture(
        tmp_path,
        brands=[
            {
                "brand_id": "brand_alpha",
                "brand_name": "Alpha Asset Management",
                "source_provider_id": "provider_universe_fixture",
            }
        ],
        etfs=[
            {
                "etf_id": "etf_focus_ai",
                "etf_name": "Tracked AI ETF",
                "brand_id": "brand_alpha",
                "source_provider_id": "provider_universe_fixture",
            }
        ],
    )
    universe_dir = tmp_path / "native_universe"
    collect_universe_fixture(
        fixture_path=universe_fixture_path,
        dest_dir=universe_dir,
        now=lambda: datetime(2026, 5, 11, 0, 0, tzinfo=UTC),
    )
    history_dir = tmp_path / "holdings_history"
    update_holdings_history_fixture(
        fixture_path=_write_security_coverage_holdings_fixture(tmp_path),
        universe_state_path=universe_dir / "universe_state.json",
        history_dir=history_dir,
        observed_partitions=2,
        now=lambda: datetime(2026, 5, 11, 1, 30, tzinfo=UTC),
    )
    resolution_path = _write_security_resolution(
        tmp_path,
        mappings=[
            {
                "security_id": "sec_reviewed_mapping",
                "ticker": "RMAP",
                "name": "Reviewed Mapping Corp.",
                "exchange": "NASDAQ",
                "security_classification": "ticker_candidate",
            }
        ],
        exclusions=[
            {
                "security_id": "sec_reviewed_exclusion",
                "name": "Reviewed Bond",
                "security_classification": "non_equity",
                "reason": "reviewed_fund_holding_exclusion",
            }
        ],
    )
    export_dir = tmp_path / "latest_export"

    summary = export_latest_holdings_comparison(
        history_dir=history_dir,
        universe_state_path=universe_dir / "universe_state.json",
        dest_dir=export_dir,
        security_resolution_path=resolution_path,
        now=lambda: datetime(2026, 5, 12, 1, 30, tzinfo=UTC),
    )

    security_coverage = summary["security_coverage"]
    assert security_coverage == {
        "security_resolution_available": True,
        "mapped_ticker_candidate_count": 4,
        "unresolved_ticker_candidate_count": 2,
        "unknown_count": 2,
        "non_ticker_excluded_count": 4,
        "reviewed_mapping_applied_count": 2,
        "reviewed_exclusion_applied_count": 2,
        "ticker_mapping_coverage_ratio": 0.666667,
        "ticker_collision_review_count": 0,
        "ticker_collision_review_samples": [],
        "recovery_sample_count": 2,
        "recovery_samples": [
            {
                "security_id": "sec_unknown",
                "name": "Unknown Instrument",
                "observed_row_count": 2,
                "observed_etf_count": 1,
                "observed_date_count": 2,
                "name_alias_count": 0,
                "security_classification": "unknown",
            },
            {
                "security_id": "sec_unmapped",
                "name": "Unmapped Corp.",
                "observed_row_count": 2,
                "observed_etf_count": 1,
                "observed_date_count": 2,
                "name_alias_count": 0,
            },
        ],
    }
    rendered_samples = json.dumps(
        security_coverage["recovery_samples"],
        ensure_ascii=False,
    )
    assert str(tmp_path) not in rendered_samples
    assert "etf_focus_ai" not in rendered_samples
    assert "2026-05-11" not in rendered_samples
    assert "2026-05-08" not in rendered_samples
    assert "source_url" not in rendered_samples
    assert "credential" not in rendered_samples


def test_import_operational_holdings_export_adds_history_snapshots(
    tmp_path: Path,
) -> None:
    manifest_path = _write_normalized_manifest(
        tmp_path,
        {
            "2026-05-11": [
                _normalized_row(partition_date="2026-05-11", security_id="SEC_A"),
            ],
            "2026-05-08": [
                _normalized_row(partition_date="2026-05-08", security_id="SEC_A"),
            ],
        },
    )
    history_dir = tmp_path / "holdings_history"

    summary = import_operational_holdings_export_to_history(
        manifest_path=manifest_path,
        history_dir=history_dir,
        now=lambda: datetime(2026, 5, 11, 3, 0, tzinfo=UTC),
    )

    history_manifest = json.loads(
        (history_dir / "holdings_history.json").read_text(encoding="utf-8")
    )
    latest_rows = _jsonl_rows(
        history_dir / "holdings_history.json.parts" / "2026-05-11.jsonl"
    )
    assert summary["schema_version"] == "agent_treport.native_holdings.history_update.v1"
    assert summary["source_type"] == "normalized_operational_export"
    assert summary["observed_dates"] == ["2026-05-11", "2026-05-08"]
    assert summary["added_snapshot_count"] == 2
    assert summary["skipped_snapshot_count"] == 0
    assert summary["refreshed_snapshot_count"] == 0
    assert summary["conflict_snapshot_count"] == 0
    assert summary["row_count"] == 2
    assert history_manifest["schema_version"] == "agent_treport.native_holdings.history.v1"
    assert history_manifest["dates"] == ["2026-05-11", "2026-05-08"]
    assert latest_rows[0]["etf_id"] == "etf_focus_ai"
    assert latest_rows[0]["security_id"] == "SEC_A"
    rendered_summary = json.dumps(summary, ensure_ascii=False)
    assert str(tmp_path) not in rendered_summary
    assert str(manifest_path) not in rendered_summary


def test_import_operational_holdings_export_uses_duplicate_conflict_refresh_rules(
    tmp_path: Path,
) -> None:
    manifest_path = _write_normalized_manifest(
        tmp_path / "first",
        {
            "2026-05-11": [
                _normalized_row(partition_date="2026-05-11", security_id="SEC_A"),
            ],
        },
    )
    history_dir = tmp_path / "holdings_history"
    import_operational_holdings_export_to_history(
        manifest_path=manifest_path,
        history_dir=history_dir,
        now=lambda: datetime(2026, 5, 11, 3, 0, tzinfo=UTC),
    )

    skipped = import_operational_holdings_export_to_history(
        manifest_path=manifest_path,
        history_dir=history_dir,
        now=lambda: datetime(2026, 5, 11, 4, 0, tzinfo=UTC),
    )

    changed_row = _normalized_row(partition_date="2026-05-11", security_id="SEC_A")
    changed_row["weight_percent"] = 95.0
    changed_manifest_path = _write_normalized_manifest(
        tmp_path / "changed",
        {"2026-05-11": [changed_row]},
    )
    with pytest.raises(
        OperationalHoldingsInputError,
        match=(
            "refresh required for holdings history snapshot: "
            "etf_id=etf_focus_ai observed_date=2026-05-11 row_count=1"
        ),
    ):
        import_operational_holdings_export_to_history(
            manifest_path=changed_manifest_path,
            history_dir=history_dir,
            now=lambda: datetime(2026, 5, 11, 5, 0, tzinfo=UTC),
        )

    refreshed = import_operational_holdings_export_to_history(
        manifest_path=changed_manifest_path,
        history_dir=history_dir,
        refresh_snapshots=(("etf_focus_ai", "2026-05-11"),),
        now=lambda: datetime(2026, 5, 11, 6, 0, tzinfo=UTC),
    )

    rows = _jsonl_rows(
        history_dir / "holdings_history.json.parts" / "2026-05-11.jsonl"
    )
    assert skipped["added_snapshot_count"] == 0
    assert skipped["skipped_snapshot_count"] == 1
    assert skipped["row_count"] == 0
    assert refreshed["refreshed_snapshot_count"] == 1
    assert refreshed["refreshed_snapshots"] == [
        {"etf_id": "etf_focus_ai", "observed_date": "2026-05-11", "row_count": 1}
    ]
    assert rows[0]["weight_percent"] == 95.0


def test_sync_operational_holdings_copies_latest_observed_partitions_as_normalized_export(
    tmp_path: Path,
) -> None:
    dest_dir = tmp_path / "copied"

    metadata = sync_operational_holdings(
        source_manifest_path=SOURCE_MANIFEST,
        dest_dir=dest_dir,
        observed_partitions=2,
        now=lambda: datetime(2026, 5, 11, 13, 0, tzinfo=UTC),
    )

    copied_manifest_path = dest_dir / "url_holdings_cumulative.json"
    copied_manifest = json.loads(copied_manifest_path.read_text(encoding="utf-8"))
    latest_rows = _jsonl_rows(
        dest_dir / "url_holdings_cumulative.json.parts" / "2026-05-11.jsonl"
    )
    previous_rows = _jsonl_rows(
        dest_dir / "url_holdings_cumulative.json.parts" / "2026-05-08.jsonl"
    )
    focus_nvidia = next(
        row
        for row in latest_rows
        if row["etf_id"] == "etf_focus_ai" and row["security_id"] == "US67066G1040"
    )
    focus_cash = next(row for row in latest_rows if row["security_id"] == "CASH00000001")
    peer_palantir_previous = next(
        row
        for row in previous_rows
        if row["etf_id"] == "etf_peer_ai" and row["security_id"] == "US69608A1088"
    )

    assert metadata["schema_version"] == "agent_treport.operational_holdings.sync_metadata.v1"
    assert metadata["requested_observed_partitions"] == 2
    assert metadata["source_dates"] == ["20260511", "20260508"]
    assert metadata["copied_dates"] == ["2026-05-11", "2026-05-08"]
    assert metadata["copied_partition_count"] == 2
    assert metadata["copied_record_count"] == 16
    assert metadata["skipped_missing_security_id_count"] == 1
    assert metadata["derived_cash_weight_count"] == 3
    assert metadata["derived_cash_weight_fit_failed_count"] == 0
    assert metadata["skipped_unusable_cash_weight_count"] == 0
    assert metadata["uncoded_cash_holding_count"] == 1
    assert metadata["cash_identification_counts"] == {
        "code_exact_cash": 2,
        "code_prefix_cash": 2,
        "code_prefix_currency": 1,
        "uncoded_cash_keyword": 1,
        "name_cash_keyword": 0,
    }
    assert metadata["source_quality_samples"][0]["reason"] == "missing_security_id"
    assert metadata["numeric_null_normalized_count"] == 1
    assert metadata["duplicate_aggregated_count"] == 1
    assert metadata["source_file_strategy_counts"] == {"sibling": 2, "manifest_file": 0}
    assert metadata["security_mapping_available"] is False
    assert metadata["security_mapping_path"] is None
    assert metadata["mapped_security_count"] == 0
    assert metadata["unmapped_security_count"] == 10
    assert metadata["sync_quality"]["metrics"]["ticker_mapping_coverage_ratio"] == 0.0
    assert metadata["sync_quality"]["status"] == "risk_failed"
    assert metadata["synced_at"] == "2026-05-11T13:00:00+00:00"
    assert (dest_dir / "sync_metadata.json").is_file()

    assert copied_manifest["schema_version"] == "agent_treport.operational_holdings.v1"
    assert copied_manifest["storage_format"] == "normalized_partitioned_jsonl_v1"
    assert copied_manifest["source_storage_format"] == "partitioned_jsonl_v2"
    assert copied_manifest["dates"] == ["2026-05-11", "2026-05-08"]
    assert copied_manifest["record_count"] == 16
    assert copied_manifest["partitions"]["2026-05-11"]["file"] == (
        "url_holdings_cumulative.json.parts/2026-05-11.jsonl"
    )
    assert copied_manifest["partitions"]["2026-05-11"]["record_count"] == 12
    assert copied_manifest["partitions"]["2026-05-08"]["record_count"] == 4

    assert set(focus_nvidia) == {
        "etf_id",
        "etf_name",
        "brand_id",
        "source_provider_id",
        "as_of_date",
        "security_id",
        "ticker",
        "name",
        "market",
        "sector",
        "theme",
        "country",
        "weight_percent",
        "shares",
        "market_value_krw",
        "price_krw",
        "is_cash",
        "security_classification",
    }
    assert focus_nvidia["ticker"] is None
    assert focus_nvidia["weight_percent"] == 7.5
    assert focus_nvidia["shares"] == 1500
    assert focus_nvidia["market_value_krw"] == 240000000
    assert focus_nvidia["market"] is None
    assert focus_cash["name"] == "Cash"
    assert focus_cash["is_cash"] is True
    assert peer_palantir_previous["shares"] is None


def test_sync_operational_holdings_writes_security_classification_schema(
    tmp_path: Path,
) -> None:
    manifest_path = _write_source_export(
        tmp_path,
        {
            "20260511": [
                _source_row(
                    code="US67066G1040",
                    name="NVIDIA Corp.",
                    weight_pct="90",
                    eval_amount_krw="90",
                ),
                _source_row(
                    code="CASH_SWEEP",
                    name="Cash sweep",
                    weight_pct="10",
                    eval_amount_krw="10",
                    quantity=None,
                ),
            ],
        },
    )

    sync_operational_holdings(
        source_manifest_path=manifest_path,
        dest_dir=tmp_path / "dest",
    )

    rows = _jsonl_rows(
        tmp_path / "dest" / "url_holdings_cumulative.json.parts" / "2026-05-11.jsonl"
    )
    rows_by_security_id = {str(row["security_id"]): row for row in rows}
    assert rows_by_security_id["US67066G1040"]["security_classification"] == (
        "ticker_candidate"
    )
    assert rows_by_security_id["CASH_SWEEP"]["security_classification"] == "cash_like"


def test_load_operational_signal_report_inputs_rejects_old_normalized_row_without_classification(
    tmp_path: Path,
) -> None:
    manifest_path = _write_normalized_manifest(
        tmp_path,
        {
            "2026-05-11": [
                _normalized_row(security_classification=None),
            ],
        },
    )

    with pytest.raises(
        OperationalHoldingsInputError,
        match="security_classification",
    ):
        load_operational_signal_report_inputs(
            manifest_path=manifest_path,
            focus_etf_id="etf_focus_ai",
        )


def test_sync_operational_holdings_derives_null_weight_cash_after_duplicate_aggregation(
    tmp_path: Path,
) -> None:
    manifest_path = _write_source_export(
        tmp_path,
        {
            "20260511": [
                _source_row(
                    code="KR7000000001",
                    name="Equity A",
                    weight_pct="60",
                    eval_amount_krw="600",
                ),
                _source_row(
                    code="KR7000000002",
                    name="Equity B",
                    weight_pct="30",
                    eval_amount_krw="300",
                ),
                _source_row(
                    code="CASH_SWEEP",
                    name="Cash sweep",
                    weight_pct=None,
                    eval_amount_krw="40",
                    quantity=None,
                ),
                _source_row(
                    code="CASH_SWEEP",
                    name="Cash sweep",
                    weight_pct=None,
                    eval_amount_krw="60",
                    quantity=None,
                ),
            ],
        },
    )
    metadata = sync_operational_holdings(
        source_manifest_path=manifest_path,
        dest_dir=tmp_path / "dest",
    )

    rows = _jsonl_rows(
        tmp_path / "dest" / "url_holdings_cumulative.json.parts" / "2026-05-11.jsonl"
    )
    cash = next(row for row in rows if row["security_id"] == "CASH_SWEEP")
    assert cash["name"] == "Cash"
    assert cash["weight_percent"] == 10.0
    assert cash["market_value_krw"] == 100.0
    assert metadata["derived_cash_weight_count"] == 1
    assert metadata["duplicate_aggregated_count"] == 1
    assert metadata["derived_cash_weight_fit_failed_count"] == 0
    assert metadata["skipped_unusable_cash_weight_count"] == 0


def test_sync_operational_holdings_maps_non_cash_ticker_from_security_mapping(
    tmp_path: Path,
) -> None:
    manifest_path = _write_source_export(
        tmp_path,
        {
            "20260511": [
                _source_row(
                    code="US67066G1040",
                    name="NVIDIA Corp.",
                    weight_pct="100",
                    eval_amount_krw="100",
                ),
            ],
        },
    )

    metadata = sync_operational_holdings(
        source_manifest_path=manifest_path,
        dest_dir=tmp_path / "dest",
        security_mapping_path=SECURITY_MAPPING,
    )

    rows = _jsonl_rows(
        tmp_path / "dest" / "url_holdings_cumulative.json.parts" / "2026-05-11.jsonl"
    )
    assert rows[0]["security_id"] == "US67066G1040"
    assert rows[0]["ticker"] == "NVDA"
    assert metadata["security_mapping_available"] is True
    assert metadata["security_mapping_path"] == str(SECURITY_MAPPING)
    assert metadata["mapped_security_count"] == 1
    assert metadata["unmapped_security_count"] == 0
    assert metadata["unmapped_security_samples"] == []
    assert metadata["sync_quality"]["metrics"]["security_mapping_available"] is True
    assert metadata["sync_quality"]["metrics"]["mapped_security_count"] == 1
    assert metadata["sync_quality"]["metrics"]["unmapped_security_count"] == 0
    assert metadata["sync_quality"]["metrics"]["ticker_mapping_coverage_ratio"] == 1.0


def test_sync_operational_holdings_uses_security_resolution_for_mapping_and_exclusion(
    tmp_path: Path,
) -> None:
    manifest_path = _write_source_export(
        tmp_path,
        {
            "20260511": [
                _source_row(
                    code="US67066G1040",
                    name="NVIDIA Corp.",
                    weight_pct="60",
                    eval_amount_krw="60",
                ),
                _source_row(
                    code="BOND00000001",
                    name="Corporate Bond",
                    weight_pct="40",
                    eval_amount_krw="40",
                ),
            ],
        },
    )
    security_resolution_path = _write_security_resolution(
        tmp_path,
        mappings=[
            {
                "security_id": "US67066G1040",
                "ticker": "NVDA",
                "name": "NVIDIA Corp",
                "exchange": "NMS",
                "security_classification": "ticker_candidate",
            }
        ],
        exclusions=[
            {
                "security_id": "BOND00000001",
                "name": "Corporate Bond",
                "security_classification": "non_equity",
                "reason": "excluded",
            }
        ],
    )

    metadata = sync_operational_holdings(
        source_manifest_path=manifest_path,
        dest_dir=tmp_path / "dest",
        security_resolution_path=security_resolution_path,
    )

    rows = _jsonl_rows(
        tmp_path / "dest" / "url_holdings_cumulative.json.parts" / "2026-05-11.jsonl"
    )
    rows_by_security_id = {str(row["security_id"]): row for row in rows}
    assert rows_by_security_id["US67066G1040"]["ticker"] == "NVDA"
    assert rows_by_security_id["US67066G1040"]["security_classification"] == (
        "ticker_candidate"
    )
    assert rows_by_security_id["BOND00000001"]["ticker"] is None
    assert rows_by_security_id["BOND00000001"]["security_classification"] == "non_equity"
    assert rows_by_security_id["BOND00000001"]["is_cash"] is False
    assert metadata["security_resolution_available"] is True
    assert metadata["security_resolution_path"] == str(security_resolution_path)
    assert metadata["mapped_security_count"] == 1
    assert metadata["unmapped_security_count"] == 0
    assert metadata["non_ticker_excluded_security_count"] == 1
    assert metadata["sync_quality"]["metrics"]["ticker_mapping_coverage_ratio"] == 1.0
    assert metadata["sync_quality"]["metrics"]["non_ticker_excluded_security_count"] == 1


def test_security_resolution_identity_metadata_reaches_normalized_holdings(
    tmp_path: Path,
) -> None:
    validated = validate_security_resolution_export(
        {
            "schema_version": "agent_treport.security_resolution_export.v1",
            "mappings": [
                {
                    "security_id": "US02079K1079",
                    "ticker": "GOOG",
                    "name": "Alphabet Inc. Class C",
                    "exchange": "NASDAQ",
                    "security_classification": "ticker_candidate",
                    "security_group_id": "alphabet_class_c",
                    "listing_key": "XNAS:GOOG",
                    "security_group_name": "Alphabet Class C",
                    "security_group_ticker": "GOOG",
                }
            ],
            "exclusions": [],
        }
    )
    manifest_path = _write_source_export(
        tmp_path,
        {
            "20260511": [
                _source_row(
                    code="US02079K1079",
                    name="Alphabet Inc. Class C",
                    weight_pct="100",
                    eval_amount_krw="100",
                ),
            ],
        },
    )
    security_resolution_path = _write_security_resolution(
        tmp_path,
        mappings=list(validated["mappings"]),
        exclusions=[],
    )

    sync_operational_holdings(
        source_manifest_path=manifest_path,
        dest_dir=tmp_path / "dest",
        security_resolution_path=security_resolution_path,
    )

    rows = _jsonl_rows(
        tmp_path / "dest" / "url_holdings_cumulative.json.parts" / "2026-05-11.jsonl"
    )

    assert rows[0]["security_id"] == "US02079K1079"
    assert rows[0]["ticker"] == "GOOG"
    assert rows[0]["security_group_id"] == "alphabet_class_c"
    assert rows[0]["listing_key"] == "XNAS:GOOG"
    assert rows[0]["security_group_name"] == "Alphabet Class C"
    assert rows[0]["security_group_ticker"] == "GOOG"


def test_sync_operational_holdings_records_ticker_collision_review_evidence(
    tmp_path: Path,
) -> None:
    manifest_path = _write_source_export(
        tmp_path,
        {
            "20260511": [
                _source_row(
                    code="US02079K1079",
                    name="Alphabet Inc. Class C",
                    weight_pct="50",
                    eval_amount_krw="50",
                ),
                _source_row(
                    code="US02079K3059",
                    name="Alphabet Inc. Class A",
                    weight_pct="50",
                    eval_amount_krw="50",
                ),
            ],
        },
    )
    security_resolution_path = _write_security_resolution(
        tmp_path,
        mappings=[
            {
                "security_id": "US02079K1079",
                "ticker": "GOOG",
                "name": "Alphabet Inc. Class C",
                "exchange": "NASDAQ",
                "security_classification": "ticker_candidate",
            },
            {
                "security_id": "US02079K3059",
                "ticker": "GOOG",
                "name": "Alphabet Inc. Class A",
                "exchange": "NASDAQ",
                "security_classification": "ticker_candidate",
            },
        ],
        exclusions=[],
    )

    metadata = sync_operational_holdings(
        source_manifest_path=manifest_path,
        dest_dir=tmp_path / "dest",
        security_resolution_path=security_resolution_path,
    )

    assert metadata["ticker_collision_review_count"] == 1
    assert metadata["ticker_collision_review_samples"] == [
        {
            "etf_id": "etf_cash_ops",
            "as_of_date": "2026-05-11",
            "ticker": "GOOG",
            "security_ids": ["US02079K1079", "US02079K3059"],
        }
    ]
    assert any(
        item["code"] == "ticker_collision_review_required"
        for item in metadata["sync_quality"]["warnings"]
    )


def test_old_v1_security_resolution_export_stays_parseable_without_grouping() -> None:
    validated = validate_security_resolution_export(
        {
            "schema_version": "agent_treport.security_resolution_export.v1",
            "mappings": [
                {
                    "security_id": "US02079K1079",
                    "ticker": "GOOG",
                    "name": "Alphabet Inc. Class C",
                    "exchange": "NASDAQ",
                    "security_classification": "ticker_candidate",
                }
            ],
            "exclusions": [],
        }
    )

    mapping = validated["mappings"][0]

    assert mapping["security_id"] == "US02079K1079"
    assert "security_group_id" not in mapping
    assert "listing_key" not in mapping
    assert "security_group_name" not in mapping
    assert "security_group_ticker" not in mapping


def test_merge_security_mapping_patch_returns_sorted_security_mapping_schema() -> None:
    merged, summary = merge_security_mapping_patch(
        {"SEC_B": "Beta"},
        {
            "schema_version": "agent_treport.security_mapping.patch.v1",
            "mappings": [
                {"security_id": " SEC_A ", "ticker": " nVdA "},
            ],
        },
    )

    assert merged == {
        "schema_version": "agent_treport.security_mapping.v1",
        "mappings": [
            {"security_id": "SEC_A", "ticker": "nVdA"},
            {"security_id": "SEC_B", "ticker": "Beta"},
        ],
    }
    assert summary == {
        "added_mapping_count": 1,
        "replaced_mapping_count": 0,
        "unchanged_mapping_count": 0,
        "total_mapping_count": 2,
    }


def test_merge_security_mapping_patch_rejects_duplicate_security_id() -> None:
    with pytest.raises(
        OperationalHoldingsInputError,
        match="duplicate security mapping patch security_id: SEC_A",
    ):
        merge_security_mapping_patch(
            {},
            {
                "schema_version": "agent_treport.security_mapping.patch.v1",
                "mappings": [
                    {"security_id": "SEC_A", "ticker": "AAA"},
                    {"security_id": "SEC_A", "ticker": "BBB"},
                ],
            },
        )


def test_merge_security_mapping_patch_allows_duplicate_ticker_values() -> None:
    merged, summary = merge_security_mapping_patch(
        {},
        {
            "schema_version": "agent_treport.security_mapping.patch.v1",
            "mappings": [
                {"security_id": "SEC_A", "ticker": "DUP"},
                {"security_id": "SEC_B", "ticker": "DUP"},
            ],
        },
    )

    assert merged["mappings"] == [
        {"security_id": "SEC_A", "ticker": "DUP"},
        {"security_id": "SEC_B", "ticker": "DUP"},
    ]
    assert summary["added_mapping_count"] == 2


def test_merge_security_mapping_patch_blocks_replacement_by_default_without_ticker_leak() -> None:
    with pytest.raises(OperationalHoldingsInputError) as exc_info:
        merge_security_mapping_patch(
            {"SEC_A": "OLD"},
            {
                "schema_version": "agent_treport.security_mapping.patch.v1",
                "mappings": [
                    {"security_id": "SEC_A", "ticker": "NEW"},
                ],
            },
        )

    message = str(exc_info.value)
    assert "SEC_A" in message
    assert "existing mapping conflict" in message
    assert "OLD" not in message
    assert "NEW" not in message


def test_merge_security_mapping_patch_replaces_only_when_allowed_and_counts_no_ops() -> None:
    merged, summary = merge_security_mapping_patch(
        {"SEC_A": "OLD", "SEC_B": "Same"},
        {
            "schema_version": "agent_treport.security_mapping.patch.v1",
            "mappings": [
                {"security_id": "SEC_A", "ticker": "NEW"},
                {"security_id": "SEC_B", "ticker": "Same"},
            ],
        },
        allow_replacements=True,
    )

    assert merged["mappings"] == [
        {"security_id": "SEC_A", "ticker": "NEW"},
        {"security_id": "SEC_B", "ticker": "Same"},
    ]
    assert summary == {
        "added_mapping_count": 0,
        "replaced_mapping_count": 1,
        "unchanged_mapping_count": 1,
        "total_mapping_count": 2,
    }


def test_merge_security_mapping_patch_rejects_empty_patch() -> None:
    with pytest.raises(
        OperationalHoldingsInputError,
        match="security mapping patch mappings must not be empty",
    ):
        merge_security_mapping_patch(
            {},
            {
                "schema_version": "agent_treport.security_mapping.patch.v1",
                "mappings": [],
            },
        )


def test_sync_operational_holdings_without_security_mapping_leaves_non_cash_tickers_null(
    tmp_path: Path,
) -> None:
    manifest_path = _write_source_export(
        tmp_path,
        {
            "20260511": [
                _source_row(
                    code="US67066G1040",
                    name="NVIDIA Corp.",
                    weight_pct="60",
                    eval_amount_krw="60",
                ),
                _source_row(
                    code="US88160R1014",
                    name="Tesla Inc.",
                    weight_pct="40",
                    eval_amount_krw="40",
                ),
            ],
        },
    )
    metadata = sync_operational_holdings(
        source_manifest_path=manifest_path,
        dest_dir=tmp_path / "dest",
    )

    rows = _jsonl_rows(
        tmp_path / "dest" / "url_holdings_cumulative.json.parts" / "2026-05-11.jsonl"
    )
    assert {row["security_id"]: row["ticker"] for row in rows} == {
        "US67066G1040": None,
        "US88160R1014": None,
    }
    assert metadata["security_mapping_available"] is False
    assert metadata["security_mapping_path"] is None
    assert metadata["mapped_security_count"] == 0
    assert metadata["unmapped_security_count"] == 2
    assert metadata["sync_quality"]["metrics"]["security_mapping_available"] is False
    assert metadata["sync_quality"]["metrics"]["mapped_security_count"] == 0
    assert metadata["sync_quality"]["metrics"]["unmapped_security_count"] == 2
    assert metadata["sync_quality"]["metrics"]["ticker_mapping_coverage_ratio"] == 0.0
    assert metadata["sync_quality"]["status"] == "risk_failed"
    assert metadata["sync_quality"]["risk_failures"] == [
        {
            "code": "low_ticker_mapping_coverage",
            "message": "Ticker mapping coverage fell below the operational review threshold.",
            "metric": "ticker_mapping_coverage_ratio",
            "value": 0.0,
            "threshold": 0.5,
        }
    ]


def test_sync_operational_holdings_never_maps_cash_rows_to_tickers(
    tmp_path: Path,
) -> None:
    mapping_path = tmp_path / "security_mapping.json"
    mapping_path.write_text(
        json.dumps(
            {
                "schema_version": "agent_treport.security_mapping.v1",
                "mappings": [
                    {"security_id": "CASH00000001", "ticker": "SHOULD_NOT_USE"},
                    {"security_id": "US67066G1040", "ticker": "NVDA"},
                ],
            }
        ),
        encoding="utf-8",
    )
    manifest_path = _write_source_export(
        tmp_path,
        {
            "20260511": [
                _source_row(
                    code="US67066G1040",
                    name="NVIDIA Corp.",
                    weight_pct="90",
                    eval_amount_krw="90",
                ),
                _source_row(
                    code="CASH00000001",
                    name="Cash",
                    weight_pct="10",
                    eval_amount_krw="10",
                    quantity=None,
                ),
            ],
        },
    )

    metadata = sync_operational_holdings(
        source_manifest_path=manifest_path,
        dest_dir=tmp_path / "dest",
        security_mapping_path=mapping_path,
    )

    rows = _jsonl_rows(
        tmp_path / "dest" / "url_holdings_cumulative.json.parts" / "2026-05-11.jsonl"
    )
    cash = next(row for row in rows if row["is_cash"])
    equity = next(row for row in rows if not row["is_cash"])
    assert equity["ticker"] == "NVDA"
    assert cash["security_id"] == "CASH00000001"
    assert cash["ticker"] is None
    assert metadata["mapped_security_count"] == 1
    assert metadata["unmapped_security_count"] == 0
    assert metadata["sync_quality"]["metrics"]["ticker_mapping_coverage_ratio"] == 1.0


def test_sync_operational_holdings_warns_for_low_ticker_mapping_coverage(
    tmp_path: Path,
) -> None:
    manifest_path = _write_source_export(
        tmp_path,
        {
            "20260511": [
                _source_row(
                    code="US67066G1040",
                    name="NVIDIA Corp.",
                    weight_pct="50",
                    eval_amount_krw="50",
                ),
                _source_row(
                    code="US9999999999",
                    name="Unmapped Equity",
                    weight_pct="50",
                    eval_amount_krw="50",
                ),
            ],
        },
    )

    metadata = sync_operational_holdings(
        source_manifest_path=manifest_path,
        dest_dir=tmp_path / "dest",
        security_mapping_path=SECURITY_MAPPING,
    )

    assert metadata["mapped_security_count"] == 1
    assert metadata["unmapped_security_count"] == 1
    assert metadata["sync_quality"]["metrics"]["ticker_mapping_coverage_ratio"] == 0.5
    assert metadata["sync_quality"]["status"] == "warning"
    assert metadata["sync_quality"]["risk_failures"] == []
    assert metadata["sync_quality"]["warnings"] == [
        {
            "code": "low_ticker_mapping_coverage",
            "message": "Ticker mapping coverage fell below the operational warning threshold.",
            "metric": "ticker_mapping_coverage_ratio",
            "value": 0.5,
            "threshold": 0.8,
        }
    ]


def test_sync_operational_holdings_reports_sorted_unmapped_security_samples(
    tmp_path: Path,
) -> None:
    mapping_path = _write_security_mapping(tmp_path, {"SEC_MAPPED": "MAP"})
    manifest_path = _write_source_export(
        tmp_path,
        {
            "20260512": [
                _source_row(
                    fund_id="etf_alpha",
                    as_of_date="20260512",
                    code="SEC_TOP",
                    name="Alpha Canonical",
                    weight_pct="10",
                    eval_amount_krw="10",
                ),
                _source_row(
                    fund_id="etf_alpha",
                    as_of_date="20260512",
                    code="SEC_TOP",
                    name="Alpha Duplicate Source",
                    weight_pct="5",
                    eval_amount_krw="5",
                ),
                _source_row(
                    fund_id="etf_beta",
                    as_of_date="20260512",
                    code="SEC_TOP",
                    name="Alpha Basket",
                    weight_pct="15",
                    eval_amount_krw="15",
                ),
                _source_row(
                    fund_id="etf_alpha",
                    as_of_date="20260512",
                    code="SEC_WIDE",
                    name="Wide Alpha",
                    weight_pct="20",
                    eval_amount_krw="20",
                ),
                _source_row(
                    fund_id="etf_beta",
                    as_of_date="20260512",
                    code="SEC_WIDE",
                    name="Wide Beta",
                    weight_pct="20",
                    eval_amount_krw="20",
                ),
                _source_row(
                    fund_id="etf_alpha",
                    as_of_date="20260512",
                    code="SEC_NARROW",
                    name="Narrow",
                    weight_pct="20",
                    eval_amount_krw="20",
                ),
                _source_row(
                    fund_id="etf_alpha",
                    as_of_date="20260512",
                    code="SEC_ZETA",
                    name="Zeta",
                    weight_pct="5",
                    eval_amount_krw="5",
                ),
                _source_row(
                    fund_id="etf_alpha",
                    as_of_date="20260512",
                    code="SEC_DELTA",
                    name="Delta",
                    weight_pct="5",
                    eval_amount_krw="5",
                ),
                _source_row(
                    fund_id="etf_alpha",
                    as_of_date="20260512",
                    code="CASH00000001",
                    name="Cash",
                    weight_pct="1",
                    eval_amount_krw="1",
                    quantity=None,
                ),
                _source_row(
                    fund_id="etf_alpha",
                    as_of_date="20260512",
                    code="SEC_MAPPED",
                    name="Mapped Security",
                    weight_pct="4",
                    eval_amount_krw="4",
                ),
            ],
            "20260511": [
                _source_row(
                    fund_id="etf_alpha",
                    as_of_date="20260511",
                    code="SEC_TOP",
                    name="Alpha Prior",
                    weight_pct="10",
                    eval_amount_krw="10",
                ),
                _source_row(
                    fund_id="etf_alpha",
                    as_of_date="20260511",
                    code="SEC_NARROW",
                    name="Narrow",
                    weight_pct="20",
                    eval_amount_krw="20",
                ),
            ],
        },
    )

    metadata = sync_operational_holdings(
        source_manifest_path=manifest_path,
        dest_dir=tmp_path / "dest",
        security_mapping_path=mapping_path,
    )

    samples = metadata["unmapped_security_samples"]

    assert samples == [
        {
            "security_id": "SEC_TOP",
            "name": "Alpha Canonical",
            "observed_row_count": 3,
            "observed_etf_count": 2,
            "observed_date_count": 2,
            "name_alias_count": 2,
        },
        {
            "security_id": "SEC_WIDE",
            "name": "Wide Alpha",
            "observed_row_count": 2,
            "observed_etf_count": 2,
            "observed_date_count": 1,
            "name_alias_count": 1,
        },
        {
            "security_id": "SEC_NARROW",
            "name": "Narrow",
            "observed_row_count": 2,
            "observed_etf_count": 1,
            "observed_date_count": 2,
            "name_alias_count": 0,
        },
        {
            "security_id": "SEC_DELTA",
            "name": "Delta",
            "observed_row_count": 1,
            "observed_etf_count": 1,
            "observed_date_count": 1,
            "name_alias_count": 0,
        },
        {
            "security_id": "SEC_ZETA",
            "name": "Zeta",
            "observed_row_count": 1,
            "observed_etf_count": 1,
            "observed_date_count": 1,
            "name_alias_count": 0,
        },
    ]
    assert all(
        set(sample) == {
            "security_id",
            "name",
            "observed_row_count",
            "observed_etf_count",
            "observed_date_count",
            "name_alias_count",
        }
        for sample in samples
    )
    assert "CASH00000001" not in {sample["security_id"] for sample in samples}
    assert "SEC_MAPPED" not in {sample["security_id"] for sample in samples}
    serialized_samples = json.dumps(samples, ensure_ascii=False)
    assert "etf_alpha" not in serialized_samples
    assert "etf_beta" not in serialized_samples
    assert "20260512" not in serialized_samples
    assert "2026-05-12" not in serialized_samples
    assert "provider_fixture" not in serialized_samples
    assert "source_url" not in serialized_samples
    assert "https://example.com/source" not in serialized_samples


def test_sync_operational_holdings_caps_unmapped_security_samples_at_twenty(
    tmp_path: Path,
) -> None:
    manifest_path = _write_source_export(
        tmp_path,
        {
            "20260511": [
                _source_row(
                    code=f"SEC_{index:02d}",
                    name=f"Security {index:02d}",
                    weight_pct="1",
                    eval_amount_krw="1",
                )
                for index in range(25)
            ],
        },
    )

    metadata = sync_operational_holdings(
        source_manifest_path=manifest_path,
        dest_dir=tmp_path / "dest",
    )

    samples = metadata["unmapped_security_samples"]

    assert len(samples) == 20
    assert [sample["security_id"] for sample in samples] == [
        f"SEC_{index:02d}" for index in range(20)
    ]


def test_sync_operational_holdings_trims_mapping_values_and_allows_duplicate_tickers(
    tmp_path: Path,
) -> None:
    mapping_path = tmp_path / "security_mapping.json"
    mapping_path.write_text(
        json.dumps(
            {
                "schema_version": "agent_treport.security_mapping.v1",
                "mappings": [
                    {"security_id": " US67066G1040 ", "ticker": " nvda "},
                    {"security_id": " US88160R1014 ", "ticker": " nvda "},
                ],
            }
        ),
        encoding="utf-8",
    )
    manifest_path = _write_source_export(
        tmp_path,
        {
            "20260511": [
                _source_row(
                    code="US67066G1040",
                    name="NVIDIA Corp.",
                    weight_pct="60",
                    eval_amount_krw="60",
                ),
                _source_row(
                    code="US88160R1014",
                    name="Tesla Inc.",
                    weight_pct="40",
                    eval_amount_krw="40",
                ),
            ],
        },
    )

    sync_operational_holdings(
        source_manifest_path=manifest_path,
        dest_dir=tmp_path / "dest",
        security_mapping_path=mapping_path,
    )

    rows = _jsonl_rows(
        tmp_path / "dest" / "url_holdings_cumulative.json.parts" / "2026-05-11.jsonl"
    )
    assert {row["security_id"]: row["ticker"] for row in rows} == {
        "US67066G1040": "nvda",
        "US88160R1014": "nvda",
    }


def test_sync_operational_holdings_rejects_missing_security_mapping_file(
    tmp_path: Path,
) -> None:
    manifest_path = _write_source_export(
        tmp_path,
        {
            "20260511": [
                _source_row(
                    code="US67066G1040",
                    name="NVIDIA Corp.",
                    weight_pct="100",
                    eval_amount_krw="100",
                ),
            ],
        },
    )

    with pytest.raises(
        OperationalHoldingsInputError,
        match="security mapping file not found",
    ):
        sync_operational_holdings(
            source_manifest_path=manifest_path,
            dest_dir=tmp_path / "dest",
            security_mapping_path=tmp_path / "missing-security-mapping.json",
        )


@pytest.mark.parametrize(
    ("mapping_payload", "message"),
    [
        (
            {"schema_version": "wrong", "mappings": []},
            "invalid security mapping schema",
        ),
        (
            {
                "schema_version": "agent_treport.security_mapping.v1",
                "mappings": [{"security_id": " ", "ticker": "NVDA"}],
            },
            "security mapping security_id must be a non-empty string",
        ),
        (
            {
                "schema_version": "agent_treport.security_mapping.v1",
                "mappings": [{"security_id": "US67066G1040", "ticker": " "}],
            },
            "security mapping ticker must be a non-empty string",
        ),
        (
            {
                "schema_version": "agent_treport.security_mapping.v1",
                "mappings": [
                    {"security_id": "US67066G1040", "ticker": "NVDA"},
                    {"security_id": "US67066G1040", "ticker": "NVDA2"},
                ],
            },
            "duplicate security mapping security_id: US67066G1040",
        ),
    ],
)
def test_sync_operational_holdings_rejects_invalid_security_mapping(
    tmp_path: Path,
    mapping_payload: dict[str, object],
    message: str,
) -> None:
    manifest_path = _write_source_export(
        tmp_path,
        {
            "20260511": [
                _source_row(
                    code="US67066G1040",
                    name="NVIDIA Corp.",
                    weight_pct="100",
                    eval_amount_krw="100",
                ),
            ],
        },
    )
    mapping_path = tmp_path / "security_mapping.json"
    mapping_path.write_text(json.dumps(mapping_payload), encoding="utf-8")

    with pytest.raises(OperationalHoldingsInputError, match=message):
        sync_operational_holdings(
            source_manifest_path=manifest_path,
            dest_dir=tmp_path / "dest",
            security_mapping_path=mapping_path,
        )


def test_sync_operational_holdings_preserves_uncoded_cash_holding_with_derived_weight(
    tmp_path: Path,
) -> None:
    manifest_path = _write_source_export(
        tmp_path,
        {
            "20260511": [
                _source_row(
                    code="KR7000000001",
                    name="Equity A",
                    weight_pct="80",
                    eval_amount_krw="800",
                ),
                _source_row(
                    code="KR7000000002",
                    name="Equity B",
                    weight_pct="10",
                    eval_amount_krw="100",
                ),
                _source_row(
                    code="",
                    name="MMDA deposit cash",
                    weight_pct=None,
                    eval_amount_krw="100",
                    quantity=None,
                ),
            ],
        },
    )
    metadata = sync_operational_holdings(
        source_manifest_path=manifest_path,
        dest_dir=tmp_path / "dest",
    )

    rows = _jsonl_rows(
        tmp_path / "dest" / "url_holdings_cumulative.json.parts" / "2026-05-11.jsonl"
    )
    cash = next(
        row
        for row in rows
        if row["security_id"] == "CASH_UNCODED:provider_fixture:etf_cash_ops"
    )
    assert cash["ticker"] is None
    assert cash["name"] == "Cash"
    assert cash["is_cash"] is True
    assert cash["weight_percent"] == 10.0
    assert metadata["skipped_missing_security_id_count"] == 0
    assert metadata["uncoded_cash_holding_count"] == 1
    assert metadata["cash_identification_counts"]["uncoded_cash_keyword"] == 1


def test_sync_operational_holdings_classifies_korean_uncoded_cash_as_cash_like(
    tmp_path: Path,
) -> None:
    manifest_path = _write_source_export(
        tmp_path,
        {
            "20260511": [
                _source_row(
                    code="",
                    name="현금",
                    weight_pct="1.5",
                    eval_amount_krw="150",
                    quantity="150",
                ),
            ],
        },
    )

    sync_operational_holdings(
        source_manifest_path=manifest_path,
        dest_dir=tmp_path / "dest",
    )

    rows = _jsonl_rows(
        tmp_path / "dest" / "url_holdings_cumulative.json.parts" / "2026-05-11.jsonl"
    )

    assert rows[0]["security_id"] == "CASH_UNCODED:provider_fixture:etf_cash_ops"
    assert rows[0]["ticker"] is None
    assert rows[0]["name"] == "Cash"
    assert rows[0]["is_cash"] is True
    assert rows[0]["security_classification"] == "cash_like"


def test_sync_operational_holdings_counts_cash_identification_rules_by_priority(
    tmp_path: Path,
) -> None:
    manifest_path = _write_source_export(
        tmp_path,
        {
            "20260511": [
                _source_row(
                    code="CASH00000001",
                    name="Cash",
                    weight_pct="10",
                    eval_amount_krw="10",
                ),
                _source_row(
                    code="KRD010010001",
                    name="KRD cash",
                    weight_pct="20",
                    eval_amount_krw="20",
                ),
                _source_row(
                    code="KRWDEPOSIT01",
                    name="KRW deposit",
                    weight_pct="30",
                    eval_amount_krw="30",
                ),
                _source_row(
                    code="KRSTOCK0001",
                    name="Customer MMDA deposit",
                    weight_pct="40",
                    eval_amount_krw="40",
                ),
                _source_row(
                    code="",
                    name="Cash balance",
                    weight_pct="0",
                    eval_amount_krw="0",
                ),
            ],
        },
    )
    metadata = sync_operational_holdings(
        source_manifest_path=manifest_path,
        dest_dir=tmp_path / "dest",
    )

    assert metadata["cash_identification_counts"] == {
        "code_exact_cash": 1,
        "code_prefix_cash": 1,
        "code_prefix_currency": 1,
        "uncoded_cash_keyword": 1,
        "name_cash_keyword": 1,
    }
    rows = _jsonl_rows(
        tmp_path / "dest" / "url_holdings_cumulative.json.parts" / "2026-05-11.jsonl"
    )
    assert all(row["is_cash"] for row in rows)


def test_sync_operational_holdings_skips_null_weight_cash_when_fit_sample_missing(
    tmp_path: Path,
) -> None:
    manifest_path = _write_source_export(
        tmp_path,
        {
            "20260511": [
                _source_row(
                    code="KR7000000001",
                    name="Equity A",
                    weight_pct="90",
                    eval_amount_krw=None,
                ),
                _source_row(
                    code="CASH00000001",
                    name="Cash",
                    weight_pct=None,
                    eval_amount_krw="100",
                    quantity=None,
                ),
            ],
        },
    )

    metadata = sync_operational_holdings(
        source_manifest_path=manifest_path,
        dest_dir=tmp_path / "dest",
    )

    rows = _jsonl_rows(
        tmp_path / "dest" / "url_holdings_cumulative.json.parts" / "2026-05-11.jsonl"
    )
    assert [row["security_id"] for row in rows] == ["KR7000000001"]
    assert metadata["derived_cash_weight_count"] == 0
    assert metadata["derived_cash_weight_fit_failed_count"] == 1
    assert metadata["skipped_unusable_cash_weight_count"] == 0
    assert metadata["source_quality_samples"][0]["reason"] == "no_weight_fit_sample"


def test_sync_operational_holdings_skips_null_weight_cash_when_fit_tolerance_exceeded(
    tmp_path: Path,
) -> None:
    manifest_path = _write_source_export(
        tmp_path,
        {
            "20260511": [
                _source_row(
                    code="KR7000000001",
                    name="Equity A",
                    weight_pct="10",
                    eval_amount_krw="600",
                ),
                _source_row(
                    code="KR7000000002",
                    name="Equity B",
                    weight_pct="20",
                    eval_amount_krw="300",
                ),
                _source_row(
                    code="CASH00000001",
                    name="Cash",
                    weight_pct=None,
                    eval_amount_krw="100",
                    quantity=None,
                ),
            ],
        },
    )

    metadata = sync_operational_holdings(
        source_manifest_path=manifest_path,
        dest_dir=tmp_path / "dest",
    )

    rows = _jsonl_rows(
        tmp_path / "dest" / "url_holdings_cumulative.json.parts" / "2026-05-11.jsonl"
    )
    assert {row["security_id"] for row in rows} == {"KR7000000001", "KR7000000002"}
    assert metadata["derived_cash_weight_fit_failed_count"] == 1
    assert metadata["skipped_unusable_cash_weight_count"] == 0
    assert metadata["source_quality_samples"][0]["reason"] == "weight_fit_tolerance_exceeded"


def test_sync_operational_holdings_marks_high_cash_derivation_failure_ratio_as_risk(
    tmp_path: Path,
) -> None:
    manifest_path = _write_source_export(
        tmp_path,
        {
            "20260511": [
                _source_row(
                    code="KR7000000001",
                    name="Equity A",
                    weight_pct="10",
                    eval_amount_krw="600",
                ),
                _source_row(
                    code="KR7000000002",
                    name="Equity B",
                    weight_pct="20",
                    eval_amount_krw="300",
                ),
                _source_row(
                    code="CASH00000001",
                    name="Cash",
                    weight_pct=None,
                    eval_amount_krw="100",
                    quantity=None,
                ),
            ],
        },
    )
    mapping_path = _write_security_mapping(
        tmp_path,
        {
            "KR7000000001": "Equity A",
            "KR7000000002": "Equity B",
        },
    )

    metadata = sync_operational_holdings(
        source_manifest_path=manifest_path,
        dest_dir=tmp_path / "dest",
        security_mapping_path=mapping_path,
    )

    sync_quality = metadata["sync_quality"]
    assert sync_quality == {
        "schema_version": "agent_treport.operational_holdings.sync_quality.v1",
        "status": "risk_failed",
        "metrics": {
            "cash_derivation_attempt_count": 1,
            "cash_derivation_failure_count": 1,
            "cash_derivation_failure_ratio": 1.0,
            "fit_failure_ratio": 1.0,
            "unusable_cash_weight_ratio": 0.0,
            "cash_like_row_count": 1,
            "missing_source_date_count": 0,
            "missing_source_dates": [],
            "skipped_missing_security_id_count": 0,
            "security_mapping_available": True,
            "security_resolution_available": False,
            "mapped_security_count": 2,
            "unmapped_security_count": 0,
            "non_ticker_excluded_security_count": 0,
            "ticker_mapping_coverage_ratio": 1.0,
            "ticker_collision_review_count": 0,
            "cash_derivation_failure_distribution": {
                "by_reason": {
                    "no_weight_fit_sample": 0,
                    "weight_fit_tolerance_exceeded": 1,
                    "invalid_cash_market_value": 0,
                    "invalid_snapshot_market_value_total": 0,
                },
                "by_date": {
                    "2026-05-11": 1,
                },
            },
        },
        "warnings": [],
        "risk_failures": [
            {
                "code": "cash_derivation_failure_ratio",
                "message": (
                    "Cash weight derivation failures exceeded the operational "
                    "review threshold."
                ),
                "metric": "cash_derivation_failure_ratio",
                "value": 1.0,
                "threshold": 0.2,
            }
        ],
    }


def test_sync_operational_holdings_marks_warning_cash_derivation_failure_ratio(
    tmp_path: Path,
) -> None:
    rows: list[dict[str, object]] = []
    for index in range(19):
        fund_id = f"etf_derives_cash_{index:02d}"
        rows.extend(
            [
                _source_row(
                    fund_id=fund_id,
                    code=f"KR7{index:09d}",
                    name="Equity A",
                    weight_pct="90",
                    eval_amount_krw="900",
                ),
                _source_row(
                    fund_id=fund_id,
                    code="CASH00000001",
                    name="Cash",
                    weight_pct=None,
                    eval_amount_krw="100",
                    quantity=None,
                ),
            ]
        )
    rows.extend(
        [
            _source_row(
                fund_id="etf_fit_failed",
                code="KR7000001001",
                name="Equity A",
                weight_pct="10",
                eval_amount_krw="600",
            ),
            _source_row(
                fund_id="etf_fit_failed",
                code="KR7000001002",
                name="Equity B",
                weight_pct="20",
                eval_amount_krw="300",
            ),
            _source_row(
                fund_id="etf_fit_failed",
                code="CASH00000001",
                name="Cash",
                weight_pct=None,
                eval_amount_krw="100",
                quantity=None,
            ),
        ]
    )
    manifest_path = _write_source_export(tmp_path, {"20260511": rows})
    mapping_path = _write_security_mapping(
        tmp_path,
        {
            str(row["code"]): str(row["code"])
            for row in rows
            if isinstance(row.get("code"), str) and str(row["code"]).startswith("KR7")
        },
    )

    metadata = sync_operational_holdings(
        source_manifest_path=manifest_path,
        dest_dir=tmp_path / "dest",
        security_mapping_path=mapping_path,
    )

    sync_quality = metadata["sync_quality"]
    assert sync_quality["status"] == "warning"
    assert sync_quality["metrics"]["cash_derivation_attempt_count"] == 20
    assert sync_quality["metrics"]["cash_derivation_failure_count"] == 1
    assert sync_quality["metrics"]["cash_derivation_failure_ratio"] == 0.05
    assert sync_quality["risk_failures"] == []
    assert sync_quality["warnings"] == [
        {
            "code": "cash_derivation_failure_ratio",
            "message": (
                "Cash weight derivation failures reached the operational "
                "warning threshold."
            ),
            "metric": "cash_derivation_failure_ratio",
            "value": 0.05,
            "threshold": 0.05,
        }
    ]


def test_sync_operational_holdings_warns_when_non_cash_rows_lack_security_id(
    tmp_path: Path,
) -> None:
    manifest_path = _write_source_export(
        tmp_path,
        {
            "20260511": [
                _source_row(
                    code="KR7000000001",
                    name="Equity A",
                    weight_pct="100",
                    eval_amount_krw="100",
                ),
                _source_row(
                    code="",
                    name="Unmapped operating row",
                    weight_pct="0",
                    eval_amount_krw="0",
                ),
            ],
        },
    )

    metadata = sync_operational_holdings(
        source_manifest_path=manifest_path,
        dest_dir=tmp_path / "dest",
        security_mapping_path=SECURITY_MAPPING,
    )

    sync_quality = metadata["sync_quality"]
    assert sync_quality["status"] == "warning"
    assert sync_quality["metrics"]["cash_derivation_failure_ratio"] is None
    assert sync_quality["metrics"]["skipped_missing_security_id_count"] == 1
    assert sync_quality["risk_failures"] == []
    assert sync_quality["warnings"] == [
        {
            "code": "skipped_missing_security_id",
            "message": "Non-cash source rows without a stable security id were skipped.",
            "metric": "skipped_missing_security_id_count",
            "value": 1,
            "threshold": 0,
        }
    ]


def test_sync_operational_holdings_warns_when_requested_source_dates_are_missing(
    tmp_path: Path,
) -> None:
    manifest_path = _write_source_export(
        tmp_path,
        {
            "20260511": [
                _source_row(
                    code="KR7000000001",
                    name="Equity A",
                    weight_pct="100",
                    eval_amount_krw="100",
                ),
            ],
        },
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["dates"] = ["20260512", "20260511"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    metadata = sync_operational_holdings(
        source_manifest_path=manifest_path,
        dest_dir=tmp_path / "dest",
        observed_partitions=2,
        security_mapping_path=SECURITY_MAPPING,
    )

    sync_quality = metadata["sync_quality"]
    assert sync_quality["status"] == "warning"
    assert sync_quality["metrics"]["missing_source_date_count"] == 1
    assert sync_quality["metrics"]["missing_source_dates"] == ["2026-05-12"]
    assert sync_quality["risk_failures"] == []
    assert sync_quality["warnings"] == [
        {
            "code": "missing_source_dates",
            "message": "Some requested observed source dates were unavailable during sync.",
            "metric": "missing_source_date_count",
            "value": 1,
            "threshold": 0,
        }
    ]


def test_sync_operational_holdings_keeps_sync_quality_path_safe(
    tmp_path: Path,
) -> None:
    manifest_path = _write_source_export(
        tmp_path,
        {
            "20260511": [
                _source_row(
                    code="KR7000000001",
                    name="Equity A",
                    weight_pct="100",
                    eval_amount_krw="100",
                ),
            ],
        },
    )
    dest_dir = tmp_path / "dest"

    metadata = sync_operational_holdings(
        source_manifest_path=manifest_path,
        dest_dir=dest_dir,
        security_mapping_path=SECURITY_MAPPING,
    )

    manifest_metadata = json.loads((dest_dir / "sync_metadata.json").read_text(encoding="utf-8"))
    assert metadata["source_manifest_path"] == str(manifest_path)
    assert metadata["copied_manifest_path"] == str(dest_dir / manifest_path.name)
    assert metadata["security_mapping_path"] == str(SECURITY_MAPPING)
    sync_quality = metadata["sync_quality"]
    assert manifest_metadata["sync_quality"] == sync_quality
    serialized = json.dumps(sync_quality, ensure_ascii=False)
    assert str(manifest_path) not in serialized
    assert str(dest_dir) not in serialized
    assert str(SECURITY_MAPPING) not in serialized
    assert "source_manifest_path" not in serialized
    assert "copied_manifest_path" not in serialized
    assert "security_mapping_path" not in serialized
    assert "source_file_used" not in serialized
    assert "https://example.com/source" not in serialized
    assert "2026-05-11T21:00:00+09:00" not in serialized


def test_sync_operational_holdings_reports_cash_derivation_failure_distribution(
    tmp_path: Path,
) -> None:
    manifest_path = _write_source_export(
        tmp_path,
        {
            "20260511": [
                _source_row(
                    fund_id="etf_no_fit",
                    code="KR7000000001",
                    name="Equity A",
                    weight_pct="90",
                    eval_amount_krw=None,
                ),
                _source_row(
                    fund_id="etf_no_fit",
                    code="CASH00000001",
                    name="Cash",
                    weight_pct=None,
                    eval_amount_krw="100",
                    quantity=None,
                ),
                _source_row(
                    fund_id="etf_bad_fit",
                    code="KR7000000002",
                    name="Equity B",
                    weight_pct="10",
                    eval_amount_krw="600",
                ),
                _source_row(
                    fund_id="etf_bad_fit",
                    code="KR7000000003",
                    name="Equity C",
                    weight_pct="20",
                    eval_amount_krw="300",
                ),
                _source_row(
                    fund_id="etf_bad_fit",
                    code="CASH00000001",
                    name="Cash",
                    weight_pct=None,
                    eval_amount_krw="100",
                    quantity=None,
                ),
                _source_row(
                    fund_id="etf_invalid_cash_value",
                    code="KR7000000004",
                    name="Equity D",
                    weight_pct="100",
                    eval_amount_krw="100",
                ),
                _source_row(
                    fund_id="etf_invalid_cash_value",
                    code="CASH00000001",
                    name="Cash",
                    weight_pct=None,
                    eval_amount_krw=None,
                    quantity=None,
                ),
                _source_row(
                    fund_id="etf_invalid_total",
                    code="KR7000000005",
                    name="Equity E",
                    weight_pct="100",
                    eval_amount_krw="-100",
                ),
                _source_row(
                    fund_id="etf_invalid_total",
                    code="CASH00000001",
                    name="Cash",
                    weight_pct=None,
                    eval_amount_krw="100",
                    quantity=None,
                ),
            ],
        },
    )

    metadata = sync_operational_holdings(
        source_manifest_path=manifest_path,
        dest_dir=tmp_path / "dest",
    )

    assert metadata["sync_quality"]["metrics"]["cash_derivation_failure_distribution"] == {
        "by_reason": {
            "no_weight_fit_sample": 1,
            "weight_fit_tolerance_exceeded": 1,
            "invalid_cash_market_value": 1,
            "invalid_snapshot_market_value_total": 1,
        },
        "by_date": {
            "2026-05-11": 4,
        },
    }


def test_sync_operational_holdings_skips_unusable_cash_derivation_inputs(
    tmp_path: Path,
) -> None:
    manifest_path = _write_source_export(
        tmp_path,
        {
            "20260511": [
                _source_row(
                    fund_id="etf_zero_total",
                    code="KR7000000001",
                    name="Offsetting Equity",
                    weight_pct="100",
                    eval_amount_krw="-100",
                ),
                _source_row(
                    fund_id="etf_zero_total",
                    code="CASH00000001",
                    name="Cash",
                    weight_pct=None,
                    eval_amount_krw="100",
                    quantity=None,
                ),
                _source_row(
                    fund_id="etf_missing_cash_value",
                    code="KR7000000002",
                    name="Equity B",
                    weight_pct="100",
                    eval_amount_krw="100",
                ),
                _source_row(
                    fund_id="etf_missing_cash_value",
                    code="KRD010010001",
                    name="Cash",
                    weight_pct=None,
                    eval_amount_krw=None,
                    quantity=None,
                ),
            ],
        },
    )

    metadata = sync_operational_holdings(
        source_manifest_path=manifest_path,
        dest_dir=tmp_path / "dest",
    )

    rows = _jsonl_rows(
        tmp_path / "dest" / "url_holdings_cumulative.json.parts" / "2026-05-11.jsonl"
    )
    assert {row["security_id"] for row in rows} == {"KR7000000001", "KR7000000002"}
    assert metadata["derived_cash_weight_fit_failed_count"] == 0
    assert metadata["skipped_unusable_cash_weight_count"] == 2
    assert [sample["reason"] for sample in metadata["source_quality_samples"]] == [
        "invalid_snapshot_market_value_total",
        "invalid_cash_market_value",
    ]


def test_sync_operational_holdings_rejects_non_cash_null_weight(tmp_path: Path) -> None:
    manifest_path = _write_source_export(
        tmp_path,
        {
            "20260511": [
                _source_row(
                    code="KR7000000001",
                    name="Equity A",
                    weight_pct=None,
                    eval_amount_krw="100",
                ),
            ],
        },
    )

    with pytest.raises(
        OperationalHoldingsInputError,
        match="non-cash source row weight_pct must not be null",
    ):
        sync_operational_holdings(
            source_manifest_path=manifest_path,
            dest_dir=tmp_path / "dest",
        )


def test_sync_operational_holdings_caps_source_quality_samples(tmp_path: Path) -> None:
    missing_code_rows = [
        _source_row(code="", name=f"Missing Code {index}", weight_pct="0", eval_amount_krw="0")
        for index in range(25)
    ]
    manifest_path = _write_source_export(
        tmp_path,
        {
            "20260511": [
                _source_row(
                    code="KR7000000001",
                    name="Equity A",
                    weight_pct="100",
                    eval_amount_krw="100",
                ),
                *missing_code_rows,
            ],
        },
    )

    metadata = sync_operational_holdings(
        source_manifest_path=manifest_path,
        dest_dir=tmp_path / "dest",
    )

    assert metadata["skipped_missing_security_id_count"] == 25
    assert len(metadata["source_quality_samples"]) == 20
    assert {sample["reason"] for sample in metadata["source_quality_samples"]} == {
        "missing_security_id"
    }
    assert "source_url" not in metadata["source_quality_samples"][0]
    assert "fetched_at" not in metadata["source_quality_samples"][0]


def test_sync_operational_holdings_reports_malformed_jsonl_date_and_line(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "source"
    parts_dir = source_dir / "url_holdings_cumulative.json.parts"
    parts_dir.mkdir(parents=True)
    manifest_path = source_dir / "url_holdings_cumulative.json"
    manifest_path.write_text(
        json.dumps(
            {
                "storage_format": "partitioned_jsonl_v2",
                "updated_at": "2026-05-11 21:33:33",
                "dates": ["20260511"],
                "record_count": 1,
                "partitions": {"20260511": {"file": "ignored.jsonl", "record_count": 1}},
            }
        ),
        encoding="utf-8",
    )
    (parts_dir / "20260511.jsonl").write_text('{"fund_id": "ok"}\n{bad json}\n', encoding="utf-8")

    with pytest.raises(
        OperationalHoldingsInputError,
        match=r"malformed JSONL for source date 20260511 line 2",
    ):
        sync_operational_holdings(source_manifest_path=manifest_path, dest_dir=tmp_path / "dest")


def test_load_operational_signal_report_inputs_selects_focus_current_and_previous() -> None:
    inputs, provenance = load_operational_signal_report_inputs(
        manifest_path=NORMALIZED_MANIFEST,
        focus_etf_id="etf_focus_ai",
        observed_partitions=3,
    )

    assert inputs.focus_etf_id == "etf_focus_ai"
    assert inputs.evidence == ()
    assert inputs.snapshots.current_date == "2026-05-11"
    assert inputs.snapshots.previous_date == "2026-05-08"
    assert inputs.snapshots.as_of_date == "2026-05-11"
    assert inputs.snapshots.lookback_days == 3
    assert inputs.snapshots.universe == "operational_holdings:2026-05-11:2026-05-08"
    assert {etf.etf_id for etf in inputs.snapshots.etfs} == {
        "etf_focus_ai",
        "etf_peer_ai",
    }
    focus = next(etf for etf in inputs.snapshots.etfs if etf.etf_id == "etf_focus_ai")
    assert {holding.security_id for holding in focus.current} == {
        "US67066G1040",
        "CASH00000001",
        "US88160R1014",
    }
    assert {holding.security_id for holding in focus.previous} == {
        "US67066G1040",
        "CASH00000001",
    }
    assert provenance["schema_version"] == "agent_treport.operational_holdings.provenance.v1"
    assert provenance["selected_current_date"] == "2026-05-11"
    assert provenance["selected_previous_date"] == "2026-05-08"
    assert provenance["missing_partition_dates"] == []
    assert provenance["included_etf_ids"] == ["etf_focus_ai", "etf_peer_ai"]
    assert provenance["sync_metadata_available"] is True
    assert provenance["sync_quality_counts"] == {
        "derived_cash_weight_count": 3,
        "derived_cash_weight_fit_failed_count": 0,
        "skipped_unusable_cash_weight_count": 0,
        "uncoded_cash_holding_count": 1,
        "skipped_missing_security_id_count": 1,
        "numeric_null_normalized_count": 1,
        "duplicate_aggregated_count": 1,
        "renamed_security_count": 1,
        "cash_identification_counts": {
            "code_exact_cash": 2,
            "code_prefix_cash": 2,
            "code_prefix_currency": 1,
            "name_cash_keyword": 0,
            "uncoded_cash_keyword": 1,
        },
    }
    assert provenance["source_quality_samples"] == [
        {
            "code": "",
            "eval_amount_krw": "1",
            "fund_id": "etf_peer_ai",
            "line_number": 14,
            "source_provider_id": "provider_operational_fixture",
            "reason": "missing_security_id",
            "source_date": "20260511",
            "weight_pct": "0.1",
        }
    ]
    assert "source_manifest_path" not in provenance
    assert "field_mappings" not in provenance
    assert "sync_quality" not in provenance


def test_load_operational_signal_report_inputs_includes_new_sync_quality_subset(
    tmp_path: Path,
) -> None:
    source_manifest = _write_source_export(
        tmp_path,
        {
            "20260511": [
                _source_row(
                    fund_id="etf_focus_cash",
                    as_of_date="20260511",
                    code="KR7000000001",
                    name="Equity A",
                    weight_pct="80",
                    eval_amount_krw="800",
                ),
                _source_row(
                    fund_id="etf_focus_cash",
                    as_of_date="20260511",
                    code="CASH00000001",
                    name="Cash",
                    weight_pct=None,
                    eval_amount_krw="200",
                    quantity=None,
                ),
            ],
            "20260508": [
                _source_row(
                    fund_id="etf_focus_cash",
                    as_of_date="20260508",
                    code="KR7000000001",
                    name="Equity A",
                    weight_pct="90",
                    eval_amount_krw="900",
                ),
                _source_row(
                    fund_id="etf_focus_cash",
                    as_of_date="20260508",
                    code="CASH00000001",
                    name="Cash",
                    weight_pct="10",
                    eval_amount_krw="100",
                    quantity=None,
                ),
            ],
        },
    )
    dest_dir = tmp_path / "copied"
    metadata = sync_operational_holdings(
        source_manifest_path=source_manifest,
        dest_dir=dest_dir,
        observed_partitions=2,
        security_mapping_path=SECURITY_MAPPING,
    )

    _, provenance = load_operational_signal_report_inputs(
        manifest_path=dest_dir / "url_holdings_cumulative.json",
        focus_etf_id="etf_focus_cash",
        observed_partitions=2,
    )

    assert provenance["sync_metadata_available"] is True
    assert provenance["sync_quality"] == metadata["sync_quality"]
    assert provenance["sync_quality"]["metrics"]["security_mapping_available"] is True
    assert provenance["sync_quality"]["metrics"]["mapped_security_count"] == 2
    assert provenance["sync_quality"]["metrics"]["unmapped_security_count"] == 0
    assert provenance["sync_quality"]["metrics"]["ticker_mapping_coverage_ratio"] == 1.0
    serialized = json.dumps(provenance["sync_quality"], ensure_ascii=False)
    assert str(source_manifest) not in serialized
    assert str(dest_dir) not in serialized
    assert str(SECURITY_MAPPING) not in serialized
    assert "source_manifest_path" not in serialized
    assert "copied_manifest_path" not in serialized
    assert "security_mapping_path" not in serialized


def test_load_operational_signal_report_inputs_rebuilds_path_safe_sync_quality_subset(
    tmp_path: Path,
) -> None:
    source_manifest = _write_source_export(
        tmp_path,
        {
            "20260511": [
                _source_row(
                    fund_id="etf_focus_cash",
                    as_of_date="20260511",
                    code="KR7000000001",
                    name="Equity A",
                    weight_pct="80",
                    eval_amount_krw="800",
                ),
                _source_row(
                    fund_id="etf_focus_cash",
                    as_of_date="20260511",
                    code="CASH00000001",
                    name="Cash",
                    weight_pct=None,
                    eval_amount_krw="200",
                    quantity=None,
                ),
            ],
            "20260508": [
                _source_row(
                    fund_id="etf_focus_cash",
                    as_of_date="20260508",
                    code="KR7000000001",
                    name="Equity A",
                    weight_pct="90",
                    eval_amount_krw="900",
                ),
                _source_row(
                    fund_id="etf_focus_cash",
                    as_of_date="20260508",
                    code="CASH00000001",
                    name="Cash",
                    weight_pct="10",
                    eval_amount_krw="100",
                    quantity=None,
                ),
            ],
        },
    )
    dest_dir = tmp_path / "copied"
    sync_operational_holdings(
        source_manifest_path=source_manifest,
        dest_dir=dest_dir,
        observed_partitions=2,
    )
    sync_metadata_path = dest_dir / "sync_metadata.json"
    sync_metadata = json.loads(sync_metadata_path.read_text(encoding="utf-8"))
    sync_metadata["sync_quality"]["source_manifest_path"] = str(source_manifest)
    sync_metadata["sync_quality"]["metrics"]["source_file_used"] = str(source_manifest)
    sync_metadata_path.write_text(json.dumps(sync_metadata), encoding="utf-8")

    _, provenance = load_operational_signal_report_inputs(
        manifest_path=dest_dir / "url_holdings_cumulative.json",
        focus_etf_id="etf_focus_cash",
        observed_partitions=2,
    )

    serialized = json.dumps(provenance["sync_quality"], ensure_ascii=False)
    assert str(source_manifest) not in serialized
    assert "source_manifest_path" not in serialized
    assert "source_file_used" not in serialized


def test_load_operational_signal_report_inputs_projects_native_security_coverage(
    tmp_path: Path,
) -> None:
    universe_fixture_path = _write_native_universe_fixture(
        tmp_path,
        brands=[
            {
                "brand_id": "brand_alpha",
                "brand_name": "Alpha Asset Management",
                "source_provider_id": "provider_universe_fixture",
            }
        ],
        etfs=[
            {
                "etf_id": "etf_focus_ai",
                "etf_name": "Tracked AI ETF",
                "brand_id": "brand_alpha",
                "source_provider_id": "provider_universe_fixture",
            }
        ],
    )
    universe_dir = tmp_path / "native_universe"
    collect_universe_fixture(
        fixture_path=universe_fixture_path,
        dest_dir=universe_dir,
        now=lambda: datetime(2026, 5, 11, 0, 0, tzinfo=UTC),
    )
    history_dir = tmp_path / "holdings_history"
    update_holdings_history_fixture(
        fixture_path=_write_security_coverage_holdings_fixture(tmp_path),
        universe_state_path=universe_dir / "universe_state.json",
        history_dir=history_dir,
        observed_partitions=2,
        now=lambda: datetime(2026, 5, 11, 1, 30, tzinfo=UTC),
    )
    export_dir = tmp_path / "latest_export"
    export_latest_holdings_comparison(
        history_dir=history_dir,
        universe_state_path=universe_dir / "universe_state.json",
        dest_dir=export_dir,
        now=lambda: datetime(2026, 5, 12, 1, 30, tzinfo=UTC),
    )

    _, provenance = load_operational_signal_report_inputs(
        manifest_path=export_dir / "url_holdings_cumulative.json",
        focus_etf_id="etf_focus_ai",
        observed_partitions=2,
    )

    assert provenance["collection_summary_available"] is True
    assert provenance["collection_summary"]["security_coverage"][
        "security_resolution_available"
    ] is False
    assert provenance["collection_summary"]["security_coverage"][
        "unknown_count"
    ] == 2
    rendered = json.dumps(
        provenance["collection_summary"]["security_coverage"],
        ensure_ascii=False,
    )
    assert str(tmp_path) not in rendered
    assert "etf_focus_ai" not in rendered
    assert "2026-05-11" not in rendered


def test_load_operational_signal_report_inputs_marks_missing_sync_metadata(
    tmp_path: Path,
) -> None:
    copied_root = tmp_path / "operational_holdings"
    shutil.copytree(NORMALIZED_MANIFEST.parent, copied_root)
    (copied_root / "sync_metadata.json").unlink()

    _, provenance = load_operational_signal_report_inputs(
        manifest_path=copied_root / "url_holdings_cumulative.json",
        focus_etf_id="etf_focus_ai",
        observed_partitions=3,
    )

    assert provenance["sync_metadata_available"] is False
    assert "sync_quality_counts" not in provenance
    assert "source_quality_samples" not in provenance
    assert "sync_quality" not in provenance


def test_load_operational_signal_report_inputs_omits_old_metadata_without_sync_quality(
    tmp_path: Path,
) -> None:
    copied_root = tmp_path / "operational_holdings"
    shutil.copytree(NORMALIZED_MANIFEST.parent, copied_root)
    sync_metadata_path = copied_root / "sync_metadata.json"
    sync_metadata = json.loads(sync_metadata_path.read_text(encoding="utf-8"))
    sync_metadata.pop("sync_quality", None)
    sync_metadata_path.write_text(json.dumps(sync_metadata), encoding="utf-8")

    _, provenance = load_operational_signal_report_inputs(
        manifest_path=copied_root / "url_holdings_cumulative.json",
        focus_etf_id="etf_focus_ai",
        observed_partitions=3,
    )

    assert provenance["sync_metadata_available"] is True
    assert "sync_quality_counts" in provenance
    assert "sync_quality" not in provenance


def test_synced_operational_holdings_load_to_payload_includes_derived_cash_summary(
    tmp_path: Path,
) -> None:
    source_manifest = _write_source_export(
        tmp_path,
        {
            "20260511": [
                _source_row(
                    fund_id="etf_focus_cash",
                    as_of_date="20260511",
                    code="KR7000000001",
                    name="Equity A",
                    weight_pct="80",
                    eval_amount_krw="800",
                ),
                _source_row(
                    fund_id="etf_focus_cash",
                    as_of_date="20260511",
                    code="CASH00000001",
                    name="Cash",
                    weight_pct=None,
                    eval_amount_krw="200",
                    quantity=None,
                ),
            ],
            "20260508": [
                _source_row(
                    fund_id="etf_focus_cash",
                    as_of_date="20260508",
                    code="KR7000000001",
                    name="Equity A",
                    weight_pct="90",
                    eval_amount_krw="900",
                ),
                _source_row(
                    fund_id="etf_focus_cash",
                    as_of_date="20260508",
                    code="CASH00000001",
                    name="Cash",
                    weight_pct="10",
                    eval_amount_krw="100",
                    quantity=None,
                ),
            ],
        },
    )
    dest_dir = tmp_path / "copied"
    sync_operational_holdings(
        source_manifest_path=source_manifest,
        dest_dir=dest_dir,
        observed_partitions=2,
    )

    inputs, provenance = load_operational_signal_report_inputs(
        manifest_path=dest_dir / "url_holdings_cumulative.json",
        focus_etf_id="etf_focus_cash",
        observed_partitions=2,
    )
    payload = build_signal_report_payload(
        snapshots=inputs.snapshots,
        focus_etf_id=inputs.focus_etf_id,
        evidence=(),
    )

    current_focus = inputs.snapshots.etfs[0].current
    cash = next(holding for holding in current_focus if holding.security_id == "CASH00000001")
    assert cash.weight_percent == 20.0
    assert provenance["sync_quality_counts"]["derived_cash_weight_count"] == 1
    assert payload.market_map.cash_position["weight_delta_pp"] == 10.0
    assert payload.etf_follow_sheets[0].cash_change_pp == 10.0


def test_load_operational_signal_report_inputs_rejects_raw_tracker_manifest() -> None:
    with pytest.raises(
        OperationalHoldingsInputError,
        match="run sync-operational-holdings first",
    ):
        load_operational_signal_report_inputs(
            manifest_path=SOURCE_MANIFEST,
            focus_etf_id="etf_focus_ai",
        )


def test_operational_provider_skips_missing_partitions_during_scan(
    tmp_path: Path,
) -> None:
    copied_root = tmp_path / "operational_holdings"
    shutil.copytree(NORMALIZED_MANIFEST.parent, copied_root)
    (copied_root / "url_holdings_cumulative.json.parts" / "2026-05-11.jsonl").unlink()
    provider = OperationalSignalReportInputProvider(
        manifest_path=copied_root / "url_holdings_cumulative.json",
        focus_etf_id="etf_focus_ai",
        observed_partitions=3,
    )

    inputs = provider.load()

    assert inputs.snapshots.current_date == "2026-05-08"
    assert inputs.snapshots.previous_date == "2026-05-07"
    assert inputs.snapshots.lookback_days == 1
    assert provider.provenance["missing_partition_dates"] == ["2026-05-11"]


def test_load_operational_signal_report_inputs_rejects_duplicate_normalized_rows(
    tmp_path: Path,
) -> None:
    copied_root = tmp_path / "operational_holdings"
    shutil.copytree(NORMALIZED_MANIFEST.parent, copied_root)
    manifest_path = copied_root / "url_holdings_cumulative.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    partition = copied_root / "url_holdings_cumulative.json.parts" / "2026-05-11.jsonl"
    first_row = partition.read_text(encoding="utf-8").splitlines()[0]
    with partition.open("a", encoding="utf-8") as stream:
        stream.write(first_row + "\n")
    manifest["partitions"]["2026-05-11"]["record_count"] += 1
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(
        OperationalHoldingsInputError,
        match=(
            "duplicate normalized holding: etf_id=etf_focus_ai "
            "date=2026-05-11 security_id=US67066G1040"
        ),
    ):
        load_operational_signal_report_inputs(
            manifest_path=manifest_path,
            focus_etf_id="etf_focus_ai",
        )


def test_load_operational_signal_report_inputs_requires_safe_relative_partition_paths(
    tmp_path: Path,
) -> None:
    copied_root = tmp_path / "operational_holdings"
    shutil.copytree(NORMALIZED_MANIFEST.parent, copied_root)
    manifest_path = copied_root / "url_holdings_cumulative.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["partitions"]["2026-05-11"]["file"] = str((tmp_path / "escaped.jsonl").resolve())
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(
        OperationalHoldingsInputError,
        match="partition file must be manifest-relative",
    ):
        load_operational_signal_report_inputs(
            manifest_path=manifest_path,
            focus_etf_id="etf_focus_ai",
        )


def test_load_operational_signal_report_inputs_loads_optional_evidence() -> None:
    inputs, provenance = load_operational_signal_report_inputs(
        manifest_path=NORMALIZED_MANIFEST,
        focus_etf_id="etf_focus_ai",
        evidence_path=FIXTURE_EVIDENCE,
    )

    assert inputs.evidence
    assert inputs.evidence[0].evidence_id
    assert provenance["evidence_path"] == str(FIXTURE_EVIDENCE)


def test_load_operational_signal_report_inputs_wraps_invalid_evidence_errors(
    tmp_path: Path,
) -> None:
    evidence_path = tmp_path / "bad-evidence.json"
    evidence_path.write_text('{"not": "a list"}', encoding="utf-8")

    with pytest.raises(
        SignalReportInputError,
        match=r"invalid evidence input: .*bad-evidence\.json:",
    ):
        load_operational_signal_report_inputs(
            manifest_path=NORMALIZED_MANIFEST,
            focus_etf_id="etf_focus_ai",
            evidence_path=evidence_path,
        )
