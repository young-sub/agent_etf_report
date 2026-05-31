from __future__ import annotations

import asyncio
import json
from io import StringIO
from pathlib import Path

import pytest

from agent_treport.cli import run_cli_async
from agent_treport.signal_report.adapters.operational_holdings import (
    HOLDINGS_HISTORY_SCHEMA_VERSION,
    HOLDINGS_HISTORY_STORAGE_FORMAT,
    OperationalHoldingsInputError,
)
from agent_treport.signal_report.adapters.provider_history_reconciliation import (
    ProviderExpectedSnapshot,
    reconcile_provider_holdings_histories,
)
from agent_treport.signal_report.adapters.source_acquisition import (
    LIVE_SOURCE_PROVIDER_IDS,
)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")



def _holding_row(
    *,
    source_provider_id: str,
    etf_id: str,
    observed_date: str,
    security_id: str,
) -> dict[str, object]:
    return {
        "etf_id": etf_id,
        "etf_name": etf_id,
        "brand_id": f"brand_{source_provider_id}",
        "source_provider_id": source_provider_id,
        "as_of_date": observed_date,
        "security_id": security_id,
        "ticker": security_id,
        "name": security_id,
        "market": "US",
        "sector": "Technology",
        "theme": "AI",
        "country": "US",
        "weight_percent": 1.0,
        "shares": 1.0,
        "market_value_krw": 100.0,
        "price_krw": 100.0,
        "is_cash": False,
        "security_classification": "ticker_candidate",
    }


def _write_history(path: Path, rows_by_date: dict[str, list[dict[str, object]]]) -> None:
    parts_dir = path.parent / f"{path.name}.parts"
    parts_dir.mkdir(parents=True, exist_ok=True)
    partitions: dict[str, dict[str, object]] = {}
    for observed_date, rows in rows_by_date.items():
        part = parts_dir / f"{observed_date}.jsonl"
        part.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )
        partitions[observed_date] = {
            "file": f"{path.name}.parts/{observed_date}.jsonl",
            "record_count": len(rows),
            "snapshot_count": len({row["etf_id"] for row in rows}),
        }
    _write_json(
        path,
        {
            "schema_version": HOLDINGS_HISTORY_SCHEMA_VERSION,
            "storage_format": HOLDINGS_HISTORY_STORAGE_FORMAT,
            "updated_at": "2026-05-31T00:00:00+00:00",
            "dates": sorted(rows_by_date, reverse=True),
            "record_count": sum(len(rows) for rows in rows_by_date.values()),
            "snapshot_count": sum(
                len({row["etf_id"] for row in rows})
                for rows in rows_by_date.values()
            ),
            "partitions": partitions,
        },
    )
def _run_async(awaitable):
    return asyncio.run(awaitable)


def test_inspect_operational_source_cache_reports_registered_provider_layouts(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        cache_root = tmp_path / "source-provider-operational"
        stdout = StringIO()

        exit_code = await run_cli_async(
            [
                "inspect-operational-source-cache",
                "--cache-root",
                str(cache_root),
            ],
            stdout=stdout,
        )

        payload = json.loads(stdout.getvalue())
        assert exit_code == 0
        assert payload["schema_version"] == (
            "agent_treport.source_provider_operational_cache.layout.v1"
        )
        assert payload["status"] == "missing_artifacts"
        assert payload["registered_provider_ids"] == list(LIVE_SOURCE_PROVIDER_IDS)
        assert payload["provider_count"] == len(LIVE_SOURCE_PROVIDER_IDS)
        assert payload["expected_artifacts"] == [
            "catalog/source_catalog.json",
            "catalog/universe_state.json",
            "catalog/source_acquisition_summary.json",
            "focus_etf_set.json",
            "holdings-history/",
            "security-master/",
        ]
        assert [item["source_provider_id"] for item in payload["providers"]] == list(
            LIVE_SOURCE_PROVIDER_IDS
        )
        first_provider = payload["providers"][0]
        assert first_provider["cache_root"] == f"{LIVE_SOURCE_PROVIDER_IDS[0]}/"
        assert first_provider["status"] == "missing_artifacts"
        assert first_provider["artifacts"][0] == {
            "artifact": "catalog/source_catalog.json",
            "path": f"{LIVE_SOURCE_PROVIDER_IDS[0]}/catalog/source_catalog.json",
            "kind": "file",
            "status": "missing",
        }
        assert payload["missing_artifact_count"] == (
            len(LIVE_SOURCE_PROVIDER_IDS) * len(payload["expected_artifacts"])
        )
        assert {
            item["path"] for item in payload["missing_artifacts"][:2]
        } == {
            f"{LIVE_SOURCE_PROVIDER_IDS[0]}/catalog/source_catalog.json",
            f"{LIVE_SOURCE_PROVIDER_IDS[0]}/catalog/universe_state.json",
        }

    _run_async(scenario())


def test_inspect_operational_source_cache_rejects_unsafe_focus_etf_ids(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        cache_root = tmp_path / "source-provider-operational"
        ace_root = cache_root / "ace"
        _write_json(
            ace_root / "focus_etf_set.json",
            {
                "schema_version": "agent_treport.focus_etf_set.v1",
                "focus_etf_ids": ["../escape"],
            },
        )
        stdout = StringIO()

        exit_code = await run_cli_async(
            [
                "inspect-operational-source-cache",
                "--cache-root",
                str(cache_root),
            ],
            stdout=stdout,
        )

        payload = json.loads(stdout.getvalue())
        assert exit_code == 0
        assert payload["status"] == "invalid_artifacts"
        assert payload["invalid_artifact_count"] == 1
        assert payload["invalid_artifacts"] == [
            {
                "source_provider_id": "ace",
                "artifact": "focus_etf_set.json",
                "path": "ace/focus_etf_set.json",
                "message": "focus_etf_id is not path-safe",
            }
        ]

    _run_async(scenario())


def test_reconcile_provider_holdings_histories_writes_missing_provider_histories(
    tmp_path: Path,
) -> None:
    canonical_dir = tmp_path / "canonical"
    cache_root = tmp_path / "source-provider-operational"
    _write_history(
        canonical_dir / "holdings_history.json",
        {
            "2026-05-12": [
                _holding_row(
                    source_provider_id="ace",
                    etf_id="etf_ace_a",
                    observed_date="2026-05-12",
                    security_id="US0001",
                ),
                _holding_row(
                    source_provider_id="kodex",
                    etf_id="etf_kodex_k",
                    observed_date="2026-05-12",
                    security_id="US0002",
                ),
            ],
            "2026-05-11": [
                _holding_row(
                    source_provider_id="ace",
                    etf_id="etf_ace_a",
                    observed_date="2026-05-11",
                    security_id="US0003",
                ),
            ],
        },
    )

    summary = reconcile_provider_holdings_histories(
        canonical_history_dir=canonical_dir,
        cache_root=cache_root,
        provider_ids=("ace", "kodex"),
        write_missing_provider_histories=True,
        now=lambda: "2026-05-31T00:00:00+00:00",
    )

    providers = {item["source_provider_id"]: item for item in summary["providers"]}
    assert summary["status"] == "ready"
    assert summary["canonical"]["record_count"] == 3
    assert summary["provider_totals"] == {
        "record_count": 3,
        "snapshot_count": 3,
        "etf_count": 2,
    }
    assert providers["ace"]["history_status"] == "written"
    assert providers["ace"]["record_count"] == 2
    assert providers["ace"]["snapshot_count"] == 2
    assert providers["ace"]["dates"] == ["2026-05-12", "2026-05-11"]
    assert providers["kodex"]["history_status"] == "written"
    assert providers["kodex"]["record_count"] == 1
    assert providers["kodex"]["missing_canonical_dates"] == ["2026-05-11"]
    assert (
        cache_root
        / "ace"
        / "holdings-history"
        / "holdings_history.json.parts"
        / "2026-05-12.jsonl"
    ).is_file()
    assert (
        cache_root / "kodex" / "holdings-history" / "holdings_history.json"
    ).is_file()


def test_reconcile_provider_holdings_histories_reports_mismatches_and_expected_gaps(
    tmp_path: Path,
) -> None:
    canonical_dir = tmp_path / "canonical"
    cache_root = tmp_path / "source-provider-operational"
    _write_history(
        canonical_dir / "holdings_history.json",
        {
            "2026-05-12": [
                _holding_row(
                    source_provider_id="kodex",
                    etf_id="etf_kodex_k",
                    observed_date="2026-05-12",
                    security_id="US0002",
                ),
                _holding_row(
                    source_provider_id="hyundai",
                    etf_id="etf_hyundai_other",
                    observed_date="2026-05-12",
                    security_id="US0004",
                ),
            ],
            "2026-05-11": [
                _holding_row(
                    source_provider_id="ace",
                    etf_id="etf_ace_a",
                    observed_date="2026-05-11",
                    security_id="US0003",
                ),
            ],
        },
    )
    _write_history(
        cache_root / "kodex" / "holdings-history" / "holdings_history.json",
        {
            "2026-05-12": [
                _holding_row(
                    source_provider_id="kodex",
                    etf_id="etf_kodex_k",
                    observed_date="2026-05-12",
                    security_id="US9999",
                ),
            ],
        },
    )

    summary = reconcile_provider_holdings_histories(
        canonical_history_dir=canonical_dir,
        cache_root=cache_root,
        provider_ids=("kodex", "hyundai"),
        expected_snapshots=(
            ProviderExpectedSnapshot(
                source_provider_id="hyundai",
                etf_id="etf_hyundai_2912753",
                observed_date="2026-05-11",
            ),
        ),
    )

    providers = {item["source_provider_id"]: item for item in summary["providers"]}
    assert summary["status"] == "mismatched_provider_histories"
    assert providers["kodex"]["history_status"] == "mismatched"
    assert providers["kodex"]["changed_snapshot_count"] == 1
    assert providers["kodex"]["missing_canonical_dates"] == ["2026-05-11"]
    assert summary["expected_snapshot_gaps"] == [
        {
            "source_provider_id": "hyundai",
            "etf_id": "etf_hyundai_2912753",
            "observed_date": "2026-05-11",
            "status": "missing_from_canonical_history",
        }
    ]


def test_reconcile_provider_holdings_histories_rejects_unsafe_expected_snapshot(
    tmp_path: Path,
) -> None:
    canonical_dir = tmp_path / "canonical"
    _write_history(
        canonical_dir / "holdings_history.json",
        {
            "2026-05-12": [
                _holding_row(
                    source_provider_id="ace",
                    etf_id="etf_ace_a",
                    observed_date="2026-05-12",
                    security_id="US0001",
                ),
            ],
        },
    )

    with pytest.raises(
        OperationalHoldingsInputError,
        match="expected snapshot etf_id is not path-safe",
    ):
        reconcile_provider_holdings_histories(
            canonical_history_dir=canonical_dir,
            cache_root=tmp_path / "source-provider-operational",
            provider_ids=("ace",),
            expected_snapshots=(
                ProviderExpectedSnapshot(
                    source_provider_id="ace",
                    etf_id="../escape",
                    observed_date="2026-05-12",
                ),
            ),
        )

def test_reconcile_provider_holdings_history_cli_reports_expected_gaps(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        canonical_dir = tmp_path / "canonical"
        cache_root = tmp_path / "source-provider-operational"
        _write_history(
            canonical_dir / "holdings_history.json",
            {
                "2026-05-12": [
                    _holding_row(
                        source_provider_id="hyundai",
                        etf_id="etf_hyundai_other",
                        observed_date="2026-05-12",
                        security_id="US0004",
                    ),
                ],
                "2026-05-11": [
                    _holding_row(
                        source_provider_id="kodex",
                        etf_id="etf_kodex_k",
                        observed_date="2026-05-11",
                        security_id="US0005",
                    ),
                ],
            },
        )
        stdout = StringIO()

        exit_code = await run_cli_async(
            [
                "reconcile-provider-holdings-history",
                "--canonical-history-dir",
                str(canonical_dir),
                "--cache-root",
                str(cache_root),
                "--source-provider",
                "hyundai",
                "--expected-snapshot",
                "hyundai:etf_hyundai_2912753:2026-05-11",
            ],
            stdout=stdout,
        )

        payload = json.loads(stdout.getvalue())
        assert exit_code == 0
        assert payload["schema_version"] == (
            "agent_treport.source_provider_operational_cache."
            "holdings_history_reconciliation.v1"
        )
        assert payload["status"] == "missing_provider_histories"
        assert payload["providers"][0]["source_provider_id"] == "hyundai"
        assert payload["providers"][0]["missing_canonical_dates"] == ["2026-05-11"]
        assert payload["expected_snapshot_gaps"] == [
            {
                "source_provider_id": "hyundai",
                "etf_id": "etf_hyundai_2912753",
                "observed_date": "2026-05-11",
                "status": "missing_from_canonical_history",
            }
        ]

    _run_async(scenario())


def test_live_canonical_history_reconciliation_preserves_provider_coverage(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).parents[1]
    canonical_dir = repo_root / "data" / "agent_treport" / "live-source" / "holdings-history"

    summary = reconcile_provider_holdings_histories(
        canonical_history_dir=canonical_dir,
        cache_root=tmp_path / "source-provider-operational",
        expected_snapshots=(
            ProviderExpectedSnapshot(
                source_provider_id="hyundai",
                etf_id="etf_hyundai_2912753",
                observed_date="2026-05-11",
            ),
        ),
    )

    providers = {item["source_provider_id"]: item for item in summary["providers"]}
    assert summary["canonical"]["record_count"] == 21967
    assert summary["canonical"]["snapshot_count"] == 445
    assert summary["provider_totals"] == {
        "record_count": 21967,
        "snapshot_count": 445,
        "etf_count": 113,
    }
    assert {
        provider_id: providers[provider_id]["record_count"]
        for provider_id in providers
    } == {
        "ace": 5689,
        "hyundai": 604,
        "kodex": 3398,
        "rise": 1960,
        "sol": 1473,
        "tiger": 5785,
        "timefolio": 3058,
    }
    assert providers["kodex"]["dates"] == [
        "2026-05-14",
        "2026-05-13",
        "2026-05-12",
    ]
    assert providers["kodex"]["missing_canonical_dates"] == [
        "2026-05-15",
        "2026-05-11",
        "2026-04-20",
        "2025-06-05",
        "2025-06-04",
    ]
    assert summary["expected_snapshot_gaps"] == [
        {
            "source_provider_id": "hyundai",
            "etf_id": "etf_hyundai_2912753",
            "observed_date": "2026-05-11",
            "status": "missing_from_canonical_history",
        }
    ]
