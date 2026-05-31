from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path

from agent_treport.cli import run_cli_async
from agent_treport.signal_report.adapters.live_source_replacement import (
    LiveBaselineProviderInput,
    apply_live_source_rolling_retention,
    plan_live_baseline_snapshots,
    run_live_baseline_backfill,
    summarize_live_collection_health,
    verify_representative_equivalence,
)
from agent_treport.signal_report.adapters.source_acquisition import (
    AceSourceProvider,
    FakeSourceProvider,
    HoldingsFetchResult,
    HoldingsFetchTarget,
    HyundaiSourceProvider,
    KodexSourceProvider,
    RiseSourceProvider,
    SolSourceProvider,
    TigerSourceProvider,
    TimefolioSourceProvider,
    collect_source_catalog,
    update_holdings_history_source,
)


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _run_async(awaitable):
    return asyncio.run(awaitable)


def _write_normalized_manifest(
    root: Path,
    rows_by_date: dict[str, list[dict[str, object]]],
) -> Path:
    export_dir = root / "operational"
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
    _write_json(
        manifest_path,
        {
            "schema_version": "agent_treport.operational_holdings.v1",
            "storage_format": "normalized_partitioned_jsonl_v1",
            "dates": list(rows_by_date),
            "record_count": sum(len(rows) for rows in rows_by_date.values()),
            "partitions": partitions,
        },
    )
    return manifest_path


def _normalized_row(
    *,
    etf_id: str = "2ETF35",
    source_provider_id: str = "provider_kodex_fake",
    partition_date: str = "2026-05-11",
    security_id: str = "US67066G1040",
    weight_percent: float = 7.5,
    market_value_krw: float | None = 240000000.0,
    shares: float | None = 1500.0,
) -> dict[str, object]:
    if not etf_id.startswith("etf_"):
        etf_id = f"etf_{source_provider_id}_{etf_id.lower()}"
    return {
        "etf_id": etf_id,
        "etf_name": "Representative Active ETF",
        "brand_id": "brand_samsung",
        "source_provider_id": source_provider_id,
        "as_of_date": partition_date,
        "security_id": security_id,
        "ticker": "NVDA",
        "name": "NVIDIA Corp.",
        "market": "US",
        "sector": "Information Technology",
        "theme": "AI infrastructure",
        "country": "US",
        "weight_percent": weight_percent,
        "shares": shares,
        "market_value_krw": market_value_krw,
        "price_krw": 160000.0,
        "is_cash": False,
        "security_classification": "ticker_candidate",
    }


def _source_holding(
    *,
    security_id: str = "US67066G1040",
    weight_percent: float = 7.5,
    market_value_krw: float | None = 240000000.0,
    shares: float | None = 1500.0,
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
        "shares": shares,
        "market_value_krw": market_value_krw,
        "price_krw": 160000.0,
        "is_cash": False,
        "security_classification": "ticker_candidate",
    }


def _fetch_result(
    *,
    source_provider_id: str,
    provider_etf_id: str,
    requested_date: str,
    outcome: str = "fetched",
    failure_code_class: str | None = None,
    holdings: tuple[dict[str, object], ...] | None = None,
) -> HoldingsFetchResult:
    return HoldingsFetchResult(
        source_provider_id=source_provider_id,
        provider_etf_id=provider_etf_id,
        etf_id=f"etf_{source_provider_id}_{provider_etf_id.lower()}",
        requested_date=requested_date,
        outcome=outcome,
        provider_query_date=requested_date,
        observed_date=requested_date if outcome == "fetched" else None,
        holdings=tuple((_source_holding(),) if holdings is None else holdings),
        failure_code_class=failure_code_class,
    )


def _write_fake_source_provider_fixture(
    root: Path,
    *,
    holdings: list[dict[str, object]],
    outcome: str = "fetched",
    observed_date: str | None = "2026-05-11",
    requested_date: str = "2026-05-11",
) -> Path:
    fixture_path = root / "source_provider_fixture.json"
    _write_json(
        fixture_path,
        {
            "schema_version": "agent_treport.source_provider.fake.v1",
            "source_provider_id": "provider_kodex_fake",
            "catalog": {
                "complete": True,
                "entries": [
                    {
                        "source_provider_id": "provider_kodex_fake",
                        "provider_etf_id": "2ETF35",
                        "etf_id": "etf_provider_kodex_fake_2etf35",
                        "etf_name": "Representative Active ETF",
                        "brand_id": "brand_samsung",
                        "brand_name": "Samsung Asset Management",
                        "strategy_label": "active",
                    }
                ],
            },
            "holdings_results": [
                {
                    "source_provider_id": "provider_kodex_fake",
                    "provider_etf_id": "2ETF35",
                    "requested_date": requested_date,
                    "observed_date": observed_date,
                    "outcome": outcome,
                    "holdings": holdings,
                }
            ],
        },
    )
    return fixture_path


def _write_multi_etf_fake_source_provider_fixture(
    root: Path,
    *,
    holdings_by_provider_and_date: dict[tuple[str, str], list[dict[str, object]]],
) -> Path:
    fixture_path = root / "source_provider_multi_fixture.json"
    entries = [
        {
            "source_provider_id": "provider_kodex_fake",
            "provider_etf_id": "2ETF35",
            "etf_id": "etf_provider_kodex_fake_2etf35",
            "etf_name": "Representative Active ETF A",
            "brand_id": "brand_samsung",
            "brand_name": "Samsung Asset Management",
            "strategy_label": "active",
        },
        {
            "source_provider_id": "provider_kodex_fake",
            "provider_etf_id": "2ETF36",
            "etf_id": "etf_provider_kodex_fake_2etf36",
            "etf_name": "Representative Active ETF B",
            "brand_id": "brand_samsung",
            "brand_name": "Samsung Asset Management",
            "strategy_label": "active",
        },
    ]
    _write_json(
        fixture_path,
        {
            "schema_version": "agent_treport.source_provider.fake.v1",
            "source_provider_id": "provider_kodex_fake",
            "catalog": {"complete": True, "entries": entries},
            "holdings_results": [
                {
                    "source_provider_id": "provider_kodex_fake",
                    "provider_etf_id": provider_etf_id,
                    "requested_date": requested_date,
                    "observed_date": requested_date,
                    "outcome": "fetched",
                    "holdings": holdings,
                }
                for (provider_etf_id, requested_date), holdings in (
                    holdings_by_provider_and_date.items()
                )
            ],
        },
    )
    return fixture_path


def _write_provider_fixture(
    root: Path,
    *,
    source_provider_id: str,
    provider_etf_ids: list[str],
    holdings_results: list[dict[str, object]],
    filename: str,
) -> Path:
    fixture_path = root / filename
    _write_json(
        fixture_path,
        {
            "schema_version": "agent_treport.source_provider.fake.v1",
            "source_provider_id": source_provider_id,
            "catalog": {
                "complete": True,
                "entries": [
                    {
                        "source_provider_id": source_provider_id,
                        "provider_etf_id": provider_etf_id,
                        "etf_id": f"etf_{source_provider_id}_{provider_etf_id.lower()}",
                        "etf_name": f"{source_provider_id} Active ETF {provider_etf_id}",
                        "brand_id": f"brand_{source_provider_id}",
                        "brand_name": f"{source_provider_id} Asset Management",
                        "strategy_label": "active",
                    }
                    for provider_etf_id in provider_etf_ids
                ],
            },
            "holdings_results": holdings_results,
        },
    )
    return fixture_path


class _RecordingProvider:
    def __init__(self, provider: FakeSourceProvider) -> None:
        self.source_provider_id = provider.source_provider_id
        self._provider = provider
        self.fetched_targets: list[tuple[str, str]] = []

    def fetch_catalog(self):
        return self._provider.fetch_catalog()

    def fetch_holdings(self, target):
        self.fetched_targets.append((target.provider_etf_id, target.requested_date))
        return self._provider.fetch_holdings(target)


class _SequencedProvider:
    def __init__(
        self,
        provider: FakeSourceProvider,
        results: dict[tuple[str, str], list[HoldingsFetchResult]],
    ) -> None:
        self.source_provider_id = provider.source_provider_id
        self._provider = provider
        self._results = results
        self.fetched_targets: list[tuple[str, str]] = []

    def fetch_catalog(self):
        return self._provider.fetch_catalog()

    def fetch_holdings(self, target):
        key = (target.provider_etf_id, target.requested_date)
        self.fetched_targets.append(key)
        results = self._results.get(key)
        if results:
            return results.pop(0)
        return self._provider.fetch_holdings(target)


class _NetworkUnavailableSession:
    def get(self, *args, **kwargs):
        raise RuntimeError("synthetic network disconnect")

    def post(self, *args, **kwargs):
        raise RuntimeError("synthetic network disconnect")


def _collect_catalog(fixture_path: Path, catalog_dir: Path) -> FakeSourceProvider:
    provider = FakeSourceProvider.from_fixture_path(fixture_path)
    collect_source_catalog(
        provider=provider,
        dest_dir=catalog_dir,
        now=lambda: datetime(2026, 5, 11, 0, 0, tzinfo=UTC),
    )
    return provider


def test_live_source_provider_network_exception_is_provider_unavailable() -> None:
    provider_classes = (
        KodexSourceProvider,
        AceSourceProvider,
        HyundaiSourceProvider,
        TimefolioSourceProvider,
        TigerSourceProvider,
        RiseSourceProvider,
        SolSourceProvider,
    )

    for provider_class in provider_classes:
        provider = provider_class(session=_NetworkUnavailableSession())
        target = HoldingsFetchTarget(
            source_provider_id=provider.source_provider_id,
            provider_etf_id="dummy",
            etf_id=f"etf_{provider.source_provider_id}_dummy",
            requested_date="2026-05-18",
            provider_query_date="2026-05-18",
        )

        result = provider.fetch_holdings(target)

        assert result.outcome == "failed"
        assert result.failure_code_class == "provider_unavailable"
        assert result.observed_date is None


def test_provider_unavailable_failure_does_not_create_retry_cooldown(
    tmp_path: Path,
) -> None:
    fixture_path = _write_provider_fixture(
        tmp_path,
        source_provider_id="provider_alpha",
        provider_etf_ids=["A1"],
        filename="alpha_provider_unavailable.json",
        holdings_results=[],
    )
    catalog_dir = tmp_path / "catalog"
    provider = _SequencedProvider(
        _collect_catalog(fixture_path, catalog_dir),
        {
            ("A1", "2026-05-18"): [
                _fetch_result(
                    source_provider_id="provider_alpha",
                    provider_etf_id="A1",
                    requested_date="2026-05-18",
                    outcome="failed",
                    failure_code_class="provider_unavailable",
                    holdings=(),
                )
            ],
        },
    )
    history_dir = tmp_path / "live_history"

    summary = update_holdings_history_source(
        provider=provider,
        source_catalog_path=catalog_dir / "source_catalog.json",
        universe_state_path=catalog_dir / "universe_state.json",
        history_dir=history_dir,
        requested_date="2026-05-18",
        provider_etf_ids={"A1"},
        now=lambda: datetime(2026, 5, 19, 1, 0, tzinfo=UTC),
    )

    target_outcome = summary["target_outcomes"][0]
    assert target_outcome["outcome"] == "failed"
    assert target_outcome["reason_code"] == "provider_unavailable"
    assert "blocked_until" not in target_outcome
    assert "retry_after" not in target_outcome
    assert not (history_dir / "source_retry_state.json").exists()


def test_representative_equivalence_passes_within_amount_weight_and_share_tolerances(
    tmp_path: Path,
) -> None:
    operational_manifest = _write_normalized_manifest(
        tmp_path,
        {
            "2026-05-11": [
                _normalized_row(
                    weight_percent=7.5,
                    market_value_krw=240000000.0,
                    shares=1500.0,
                )
            ]
        },
    )
    fixture_path = _write_fake_source_provider_fixture(
        tmp_path,
        holdings=[
            _source_holding(
                weight_percent=7.509,
                market_value_krw=240000001.0,
                shares=1500.000001,
            )
        ],
    )
    catalog_dir = tmp_path / "catalog"
    provider = _collect_catalog(fixture_path, catalog_dir)

    summary = verify_representative_equivalence(
        provider=provider,
        source_catalog_path=catalog_dir / "source_catalog.json",
        universe_state_path=catalog_dir / "universe_state.json",
        history_dir=tmp_path / "live_history",
        operational_manifest_path=operational_manifest,
        provider_etf_id="2ETF35",
        requested_date="2026-05-11",
        now=lambda: datetime(2026, 5, 11, 1, 0, tzinfo=UTC),
    )

    assert summary["passed"] is True
    assert summary["source_provider_id"] == "provider_kodex_fake"
    assert summary["provider_etf_id"] == "2ETF35"
    assert summary["etf_id"] == "etf_provider_kodex_fake_2etf35"
    assert summary["observed_date"] == "2026-05-11"
    assert summary["live_row_count"] == 1
    assert summary["operational_row_count"] == 1
    assert summary["matched_constituent_count"] == 1
    assert summary["mismatch_counts"] == {
        "missing_live_security_code": 0,
        "missing_operational_security_code": 0,
        "weight_mismatch": 0,
        "market_value_mismatch": 0,
        "shares_mismatch": 0,
    }
    assert summary["warning_counts"] == {"missing_shares": 0}
    assert summary["tolerances"] == {
        "weight_percent_abs": 0.01,
        "market_value_krw_abs": 1.0,
        "shares_abs": 0.000001,
    }
    assert summary["mismatch_samples"] == []
    rendered = json.dumps(summary, ensure_ascii=False)
    assert str(tmp_path) not in rendered
    assert "NVIDIA Corp" not in rendered


def test_representative_equivalence_blocks_on_code_weight_and_amount_mismatches(
    tmp_path: Path,
) -> None:
    operational_manifest = _write_normalized_manifest(
        tmp_path,
        {
            "2026-05-11": [
                _normalized_row(security_id="SEC_A", weight_percent=10.0),
                _normalized_row(security_id="SEC_B", weight_percent=5.0),
            ]
        },
    )
    fixture_path = _write_fake_source_provider_fixture(
        tmp_path,
        holdings=[
            _source_holding(
                security_id="SEC_A",
                weight_percent=10.02,
                market_value_krw=240000002.0,
            ),
            _source_holding(security_id="SEC_C", weight_percent=5.0),
        ],
    )
    catalog_dir = tmp_path / "catalog"
    provider = _collect_catalog(fixture_path, catalog_dir)

    summary = verify_representative_equivalence(
        provider=provider,
        source_catalog_path=catalog_dir / "source_catalog.json",
        universe_state_path=catalog_dir / "universe_state.json",
        history_dir=tmp_path / "live_history",
        operational_manifest_path=operational_manifest,
        provider_etf_id="2ETF35",
        requested_date="2026-05-11",
        now=lambda: datetime(2026, 5, 11, 1, 0, tzinfo=UTC),
    )

    assert summary["passed"] is False
    assert summary["matched_constituent_count"] == 1
    assert summary["mismatch_counts"] == {
        "missing_live_security_code": 1,
        "missing_operational_security_code": 1,
        "weight_mismatch": 1,
        "market_value_mismatch": 1,
        "shares_mismatch": 0,
    }
    assert summary["mismatch_sample_count"] == 4
    assert summary["mismatch_samples"] == [
        {
            "security_id": "SEC_B",
            "field": "security_id",
            "issue": "missing_live_security_code",
        },
        {
            "security_id": "SEC_C",
            "field": "security_id",
            "issue": "missing_operational_security_code",
        },
        {
            "security_id": "SEC_A",
            "field": "weight_percent",
            "issue": "outside_tolerance",
            "absolute_difference": 0.02,
        },
        {
            "security_id": "SEC_A",
            "field": "market_value_krw",
            "issue": "outside_tolerance",
            "absolute_difference": 2.0,
        },
    ]


def test_representative_equivalence_warns_when_shares_missing_on_one_side(
    tmp_path: Path,
) -> None:
    operational_manifest = _write_normalized_manifest(
        tmp_path,
        {"2026-05-11": [_normalized_row(shares=None)]},
    )
    fixture_path = _write_fake_source_provider_fixture(
        tmp_path,
        holdings=[_source_holding(shares=1500.0)],
    )
    catalog_dir = tmp_path / "catalog"
    provider = _collect_catalog(fixture_path, catalog_dir)

    summary = verify_representative_equivalence(
        provider=provider,
        source_catalog_path=catalog_dir / "source_catalog.json",
        universe_state_path=catalog_dir / "universe_state.json",
        history_dir=tmp_path / "live_history",
        operational_manifest_path=operational_manifest,
        provider_etf_id="2ETF35",
        requested_date="2026-05-11",
        now=lambda: datetime(2026, 5, 11, 1, 0, tzinfo=UTC),
    )

    assert summary["passed"] is True
    assert summary["warning_counts"] == {"missing_shares": 1}
    assert summary["warning_samples"] == [
        {
            "security_id": "US67066G1040",
            "field": "shares",
            "issue": "missing_on_one_side",
        }
    ]


def test_representative_equivalence_does_not_fetch_alternate_after_failure(
    tmp_path: Path,
) -> None:
    operational_manifest = _write_normalized_manifest(
        tmp_path,
        {"2026-05-11": [_normalized_row(etf_id="2ETF35", security_id="SEC_A")]},
    )
    fixture_path = _write_multi_etf_fake_source_provider_fixture(
        tmp_path,
        holdings_by_provider_and_date={
            ("2ETF36", "2026-05-11"): [_source_holding(security_id="SEC_B")]
        },
    )
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    payload["holdings_results"].append(
        {
            "source_provider_id": "provider_kodex_fake",
            "provider_etf_id": "2ETF35",
            "requested_date": "2026-05-11",
            "outcome": "failed",
            "failure_code_class": "provider_response",
            "holdings": [],
        }
    )
    fixture_path.write_text(json.dumps(payload), encoding="utf-8")
    catalog_dir = tmp_path / "catalog"
    provider = _RecordingProvider(_collect_catalog(fixture_path, catalog_dir))

    summary = verify_representative_equivalence(
        provider=provider,
        source_catalog_path=catalog_dir / "source_catalog.json",
        universe_state_path=catalog_dir / "universe_state.json",
        history_dir=tmp_path / "live_history",
        operational_manifest_path=operational_manifest,
        provider_etf_id="2ETF35",
        requested_date="2026-05-11",
        now=lambda: datetime(2026, 5, 11, 1, 0, tzinfo=UTC),
    )

    assert summary["passed"] is False
    assert summary["fetch_outcome"] == "failed"
    assert provider.fetched_targets == [("2ETF35", "2026-05-11")]


def test_live_baseline_plan_requests_only_missing_required_snapshots(
    tmp_path: Path,
) -> None:
    operational_manifest = _write_normalized_manifest(
        tmp_path,
        {
            "2026-05-11": [
                _normalized_row(etf_id="2ETF35", security_id="SEC_A"),
                _normalized_row(etf_id="2ETF36", security_id="SEC_B"),
            ],
            "2026-05-08": [
                _normalized_row(
                    etf_id="2ETF35",
                    partition_date="2026-05-08",
                    security_id="SEC_A",
                ),
                _normalized_row(
                    etf_id="2ETF36",
                    partition_date="2026-05-08",
                    security_id="SEC_B",
                ),
            ],
        },
    )
    fixture_path = _write_multi_etf_fake_source_provider_fixture(
        tmp_path,
        holdings_by_provider_and_date={
            ("2ETF35", "2026-05-11"): [_source_holding(security_id="SEC_A")],
            ("2ETF36", "2026-05-08"): [_source_holding(security_id="SEC_B")],
        },
    )
    catalog_dir = tmp_path / "catalog"
    history_dir = tmp_path / "live_history"
    provider = _collect_catalog(fixture_path, catalog_dir)
    update_holdings_history_source(
        provider=provider,
        source_catalog_path=catalog_dir / "source_catalog.json",
        universe_state_path=catalog_dir / "universe_state.json",
        history_dir=history_dir,
        requested_date="2026-05-11",
        provider_etf_ids={"2ETF35"},
        now=lambda: datetime(2026, 5, 11, 1, 0, tzinfo=UTC),
    )
    update_holdings_history_source(
        provider=provider,
        source_catalog_path=catalog_dir / "source_catalog.json",
        universe_state_path=catalog_dir / "universe_state.json",
        history_dir=history_dir,
        requested_date="2026-05-08",
        provider_etf_ids={"2ETF36"},
        now=lambda: datetime(2026, 5, 11, 1, 5, tzinfo=UTC),
    )

    plan = plan_live_baseline_snapshots(
        source_provider_id="provider_kodex_fake",
        source_catalog_path=catalog_dir / "source_catalog.json",
        universe_state_path=catalog_dir / "universe_state.json",
        history_dir=history_dir,
        operational_manifest_path=operational_manifest,
    )

    assert plan["schema_version"] == "agent_treport.live_source.baseline_plan.v1"
    assert plan["source_provider_id"] == "provider_kodex_fake"
    assert plan["tracked_active_strategy_etf_count"] == 2
    assert plan["required_snapshot_count"] == 4
    assert plan["existing_snapshot_count"] == 2
    assert plan["missing_snapshot_count"] == 2
    assert plan["ready_for_baseline_export"] is False
    assert plan["requests"] == [
        {
            "source_provider_id": "provider_kodex_fake",
            "provider_etf_id": "2ETF35",
            "etf_id": "etf_provider_kodex_fake_2etf35",
            "requested_date": "2026-05-08",
            "required_observed_date": "2026-05-08",
            "window_position": "prior",
            "date_alignment": {
                "latest_observed_date": "2026-05-11",
                "prior_business_date": "2026-05-08",
                "prior_observed_date": "2026-05-08",
                "status": "exact_prior_business_date",
            },
        },
        {
            "source_provider_id": "provider_kodex_fake",
            "provider_etf_id": "2ETF36",
            "etf_id": "etf_provider_kodex_fake_2etf36",
            "requested_date": "2026-05-11",
            "required_observed_date": "2026-05-11",
            "window_position": "latest",
            "date_alignment": {
                "latest_observed_date": "2026-05-11",
                "prior_business_date": "2026-05-08",
                "prior_observed_date": "2026-05-08",
                "status": "exact_prior_business_date",
            },
        },
    ]
    rendered = json.dumps(plan, ensure_ascii=False)
    assert str(tmp_path) not in rendered
    assert "NVIDIA Corp" not in rendered


def test_live_baseline_plan_records_nearest_prior_observed_date_alignment(
    tmp_path: Path,
) -> None:
    operational_manifest = _write_normalized_manifest(
        tmp_path,
        {
            "2026-05-14": [_normalized_row(etf_id="2ETF35", partition_date="2026-05-14")],
            "2026-05-12": [_normalized_row(etf_id="2ETF35", partition_date="2026-05-12")],
        },
    )
    fixture_path = _write_multi_etf_fake_source_provider_fixture(
        tmp_path,
        holdings_by_provider_and_date={},
    )
    catalog_dir = tmp_path / "catalog"
    provider = _collect_catalog(fixture_path, catalog_dir)

    plan = plan_live_baseline_snapshots(
        source_provider_id=provider.source_provider_id,
        source_catalog_path=catalog_dir / "source_catalog.json",
        universe_state_path=catalog_dir / "universe_state.json",
        history_dir=tmp_path / "empty_history",
        operational_manifest_path=operational_manifest,
    )

    assert plan["missing_snapshot_count"] == 4
    assert plan["requests"][1]["window_position"] == "prior"
    assert plan["requests"][1]["required_observed_date"] == "2026-05-12"
    assert plan["requests"][1]["date_alignment"] == {
        "latest_observed_date": "2026-05-14",
        "prior_business_date": "2026-05-13",
        "prior_observed_date": "2026-05-12",
        "status": "nearest_available_prior_observed_business_date",
    }
    assert plan["requests"][2]["window_position"] == "latest_discovery"
    assert plan["requests"][3]["window_position"] == "prior_discovery"
    assert plan["window_gap_count"] == 0
    assert plan["window_gap_samples"] == []


def test_daily_source_acquisition_summary_preserves_multiple_provider_runs(
    tmp_path: Path,
) -> None:
    history_dir = tmp_path / "live_history"
    kodex_fixture = _write_provider_fixture(
        tmp_path,
        source_provider_id="kodex",
        provider_etf_ids=["K1"],
        filename="kodex.json",
        holdings_results=[
            {
                "source_provider_id": "kodex",
                "provider_etf_id": "K1",
                "requested_date": "2026-05-11",
                "observed_date": "2026-05-11",
                "outcome": "fetched",
                "holdings": [_source_holding(security_id="SEC_K")],
            }
        ],
    )
    ace_fixture = _write_provider_fixture(
        tmp_path,
        source_provider_id="ace",
        provider_etf_ids=["A1"],
        filename="ace.json",
        holdings_results=[
            {
                "source_provider_id": "ace",
                "provider_etf_id": "A1",
                "requested_date": "2026-05-11",
                "observed_date": "2026-05-11",
                "outcome": "fetched",
                "holdings": [_source_holding(security_id="SEC_A")],
            }
        ],
    )
    kodex_catalog_dir = tmp_path / "kodex_catalog"
    ace_catalog_dir = tmp_path / "ace_catalog"
    kodex = _collect_catalog(kodex_fixture, kodex_catalog_dir)
    ace = _collect_catalog(ace_fixture, ace_catalog_dir)

    update_holdings_history_source(
        provider=kodex,
        source_catalog_path=kodex_catalog_dir / "source_catalog.json",
        universe_state_path=kodex_catalog_dir / "universe_state.json",
        history_dir=history_dir,
        requested_date="2026-05-11",
        provider_etf_ids={"K1"},
        now=lambda: datetime(2026, 5, 11, 1, 0, tzinfo=UTC),
    )
    update_holdings_history_source(
        provider=ace,
        source_catalog_path=ace_catalog_dir / "source_catalog.json",
        universe_state_path=ace_catalog_dir / "universe_state.json",
        history_dir=history_dir,
        requested_date="2026-05-11",
        provider_etf_ids={"A1"},
        now=lambda: datetime(2026, 5, 11, 1, 5, tzinfo=UTC),
    )

    summary = json.loads(
        (history_dir / "source_acquisition_summary.json").read_text(encoding="utf-8")
    )

    assert summary["source_provider_id"] == "multiple"
    assert summary["source_provider_ids"] == ["kodex", "ace"]
    assert [item["source_provider_id"] for item in summary["provider_results"]] == [
        "kodex",
        "ace",
    ]
    assert summary["aggregate_counts"]["target_count"] == 2
    assert summary["aggregate_counts"]["fetched"] == 2
    assert summary["aggregate_counts"]["written_snapshot_count"] == 2
    assert {item["source_provider_id"] for item in summary["target_outcomes"]} == {
        "kodex",
        "ace",
    }
    rendered = json.dumps(summary, ensure_ascii=False)
    assert str(tmp_path) not in rendered
    assert "provider_etf_id" not in rendered
    assert "https://" not in rendered


def test_live_baseline_plan_requests_discovery_window_without_operational_copy(
    tmp_path: Path,
) -> None:
    operational_manifest = _write_normalized_manifest(
        tmp_path,
        {
            "2026-05-11": [_normalized_row(etf_id="2ETF35", security_id="SEC_A")],
            "2026-05-08": [
                _normalized_row(
                    etf_id="2ETF35",
                    partition_date="2026-05-08",
                    security_id="SEC_A",
                )
            ],
        },
    )
    fixture_path = _write_multi_etf_fake_source_provider_fixture(
        tmp_path,
        holdings_by_provider_and_date={},
    )
    catalog_dir = tmp_path / "catalog"
    provider = _collect_catalog(fixture_path, catalog_dir)

    plan = plan_live_baseline_snapshots(
        source_provider_id=provider.source_provider_id,
        source_catalog_path=catalog_dir / "source_catalog.json",
        universe_state_path=catalog_dir / "universe_state.json",
        history_dir=tmp_path / "empty_history",
        operational_manifest_path=operational_manifest,
    )

    assert plan["tracked_active_strategy_etf_count"] == 2
    assert plan["required_snapshot_count"] == 4
    assert plan["existing_snapshot_count"] == 0
    assert plan["missing_snapshot_count"] == 4
    assert plan["window_gap_count"] == 0
    assert plan["requests"][2:] == [
        {
            "source_provider_id": "provider_kodex_fake",
            "provider_etf_id": "2ETF36",
            "etf_id": "etf_provider_kodex_fake_2etf36",
            "requested_date": "2026-05-11",
            "required_observed_date": "2026-05-11",
            "window_position": "latest_discovery",
            "date_alignment": {
                "latest_observed_date": "2026-05-11",
                "prior_business_date": "2026-05-08",
                "prior_observed_date": "2026-05-08",
                "status": "exact_prior_business_date",
            },
        },
        {
            "source_provider_id": "provider_kodex_fake",
            "provider_etf_id": "2ETF36",
            "etf_id": "etf_provider_kodex_fake_2etf36",
            "requested_date": "2026-05-08",
            "required_observed_date": "2026-05-08",
            "window_position": "prior_discovery",
            "date_alignment": {
                "latest_observed_date": "2026-05-11",
                "prior_business_date": "2026-05-08",
                "prior_observed_date": "2026-05-08",
                "status": "exact_prior_business_date",
            },
        },
    ]
    rendered = json.dumps(plan, ensure_ascii=False)
    assert str(tmp_path) not in rendered
    assert "NVIDIA Corp" not in rendered


def test_live_baseline_plan_skips_discovered_latest_when_planning_prior(
    tmp_path: Path,
) -> None:
    operational_manifest = _write_normalized_manifest(
        tmp_path,
        {
            "2026-05-11": [_normalized_row(etf_id="2ETF35", security_id="SEC_A")],
            "2026-05-08": [
                _normalized_row(
                    etf_id="2ETF35",
                    partition_date="2026-05-08",
                    security_id="SEC_A",
                )
            ],
        },
    )
    fixture_path = _write_multi_etf_fake_source_provider_fixture(
        tmp_path,
        holdings_by_provider_and_date={
            ("2ETF35", "2026-05-11"): [_source_holding(security_id="SEC_A")],
            ("2ETF35", "2026-05-08"): [_source_holding(security_id="SEC_A")],
            ("2ETF36", "2026-05-11"): [_source_holding(security_id="SEC_B")],
        },
    )
    catalog_dir = tmp_path / "catalog"
    history_dir = tmp_path / "live_history"
    provider = _collect_catalog(fixture_path, catalog_dir)
    update_holdings_history_source(
        provider=provider,
        source_catalog_path=catalog_dir / "source_catalog.json",
        universe_state_path=catalog_dir / "universe_state.json",
        history_dir=history_dir,
        requested_date="2026-05-11",
        provider_etf_ids={"2ETF35"},
        now=lambda: datetime(2026, 5, 11, 1, 0, tzinfo=UTC),
    )
    update_holdings_history_source(
        provider=provider,
        source_catalog_path=catalog_dir / "source_catalog.json",
        universe_state_path=catalog_dir / "universe_state.json",
        history_dir=history_dir,
        requested_date="2026-05-08",
        provider_etf_ids={"2ETF35"},
        now=lambda: datetime(2026, 5, 11, 1, 5, tzinfo=UTC),
    )
    update_holdings_history_source(
        provider=provider,
        source_catalog_path=catalog_dir / "source_catalog.json",
        universe_state_path=catalog_dir / "universe_state.json",
        history_dir=history_dir,
        requested_date="2026-05-11",
        provider_etf_ids={"2ETF36"},
        now=lambda: datetime(2026, 5, 11, 1, 10, tzinfo=UTC),
    )

    plan = plan_live_baseline_snapshots(
        source_provider_id=provider.source_provider_id,
        source_catalog_path=catalog_dir / "source_catalog.json",
        universe_state_path=catalog_dir / "universe_state.json",
        history_dir=history_dir,
        operational_manifest_path=operational_manifest,
    )

    assert plan["required_snapshot_count"] == 4
    assert plan["existing_snapshot_count"] == 3
    assert plan["missing_snapshot_count"] == 1
    assert plan["window_gap_count"] == 0
    assert plan["requests"] == [
        {
            "source_provider_id": "provider_kodex_fake",
            "provider_etf_id": "2ETF36",
            "etf_id": "etf_provider_kodex_fake_2etf36",
            "requested_date": "2026-05-08",
            "required_observed_date": "2026-05-08",
            "window_position": "prior_discovery",
            "date_alignment": {
                "latest_observed_date": "2026-05-11",
                "prior_business_date": "2026-05-08",
                "prior_observed_date": "2026-05-08",
                "status": "exact_prior_business_date",
            },
        }
    ]


def test_live_baseline_backfill_does_not_start_bulk_when_any_representative_fails(
    tmp_path: Path,
) -> None:
    operational_manifest = _write_normalized_manifest(
        tmp_path,
        {
            "2026-05-11": [
                _normalized_row(
                    source_provider_id="provider_alpha",
                    etf_id="A1",
                    security_id="SEC_A",
                    weight_percent=10.0,
                ),
                _normalized_row(
                    source_provider_id="provider_beta",
                    etf_id="B1",
                    security_id="SEC_B",
                    weight_percent=10.0,
                ),
            ],
            "2026-05-08": [
                _normalized_row(
                    source_provider_id="provider_alpha",
                    etf_id="A1",
                    partition_date="2026-05-08",
                    security_id="SEC_A",
                ),
                _normalized_row(
                    source_provider_id="provider_beta",
                    etf_id="B1",
                    partition_date="2026-05-08",
                    security_id="SEC_B",
                ),
            ],
        },
    )
    alpha_fixture = _write_provider_fixture(
        tmp_path,
        source_provider_id="provider_alpha",
        provider_etf_ids=["A1"],
        filename="alpha.json",
        holdings_results=[
            {
                "source_provider_id": "provider_alpha",
                "provider_etf_id": "A1",
                "requested_date": "2026-05-11",
                "observed_date": "2026-05-11",
                "outcome": "fetched",
                "holdings": [_source_holding(security_id="SEC_A", weight_percent=10.0)],
            }
        ],
    )
    beta_fixture = _write_provider_fixture(
        tmp_path,
        source_provider_id="provider_beta",
        provider_etf_ids=["B1", "B2"],
        filename="beta.json",
        holdings_results=[
            {
                "source_provider_id": "provider_beta",
                "provider_etf_id": "B1",
                "requested_date": "2026-05-11",
                "observed_date": "2026-05-11",
                "outcome": "fetched",
                "holdings": [_source_holding(security_id="SEC_B", weight_percent=10.02)],
            },
            {
                "source_provider_id": "provider_beta",
                "provider_etf_id": "B2",
                "requested_date": "2026-05-08",
                "observed_date": "2026-05-08",
                "outcome": "fetched",
                "holdings": [_source_holding(security_id="SEC_B2")],
            },
        ],
    )
    alpha_catalog_dir = tmp_path / "alpha_catalog"
    beta_catalog_dir = tmp_path / "beta_catalog"
    alpha = _RecordingProvider(_collect_catalog(alpha_fixture, alpha_catalog_dir))
    beta = _RecordingProvider(_collect_catalog(beta_fixture, beta_catalog_dir))

    summary = run_live_baseline_backfill(
        provider_inputs=(
            LiveBaselineProviderInput(
                provider=alpha,
                source_catalog_path=alpha_catalog_dir / "source_catalog.json",
                universe_state_path=alpha_catalog_dir / "universe_state.json",
                representative_provider_etf_id="A1",
                representative_requested_date="2026-05-11",
            ),
            LiveBaselineProviderInput(
                provider=beta,
                source_catalog_path=beta_catalog_dir / "source_catalog.json",
                universe_state_path=beta_catalog_dir / "universe_state.json",
                representative_provider_etf_id="B1",
                representative_requested_date="2026-05-11",
            ),
        ),
        operational_manifest_path=operational_manifest,
        history_dir=tmp_path / "live_history",
        now=lambda: datetime(2026, 5, 11, 1, 0, tzinfo=UTC),
    )

    assert summary["bulk_started"] is False
    assert summary["representative_pass_count"] == 1
    assert summary["representative_fail_count"] == 1
    assert beta.fetched_targets == [("B1", "2026-05-11")]
    assert summary["provider_results"] == []


def test_live_baseline_backfill_fetches_missing_requests_with_spacing_and_overrides(
    tmp_path: Path,
) -> None:
    operational_manifest = _write_normalized_manifest(
        tmp_path,
        {
            "2026-05-11": [
                _normalized_row(source_provider_id="sol", etf_id="S1", security_id="SEC_A"),
                _normalized_row(source_provider_id="sol", etf_id="S2", security_id="SEC_B"),
            ],
            "2026-05-08": [
                _normalized_row(
                    source_provider_id="sol",
                    etf_id="S1",
                    partition_date="2026-05-08",
                    security_id="SEC_A",
                ),
                _normalized_row(
                    source_provider_id="sol",
                    etf_id="S2",
                    partition_date="2026-05-08",
                    security_id="SEC_B",
                ),
            ],
        },
    )
    fixture = _write_provider_fixture(
        tmp_path,
        source_provider_id="sol",
        provider_etf_ids=["S1", "S2"],
        filename="sol.json",
        holdings_results=[
            {
                "source_provider_id": "sol",
                "provider_etf_id": provider_etf_id,
                "requested_date": requested_date,
                "observed_date": requested_date,
                "outcome": "fetched",
                "holdings": [
                    _source_holding(
                        security_id="SEC_A",
                        weight_percent=7.5,
                        market_value_krw=240000000.0,
                    )
                ],
            }
            for provider_etf_id in ("S1", "S2")
            for requested_date in ("2026-05-11", "2026-05-08")
        ],
    )
    catalog_dir = tmp_path / "catalog"
    provider = _RecordingProvider(_collect_catalog(fixture, catalog_dir))
    sleeps: list[float] = []

    summary = run_live_baseline_backfill(
        provider_inputs=(
            LiveBaselineProviderInput(
                provider=provider,
                source_catalog_path=catalog_dir / "source_catalog.json",
                universe_state_path=catalog_dir / "universe_state.json",
                representative_provider_etf_id="S1",
                representative_requested_date="2026-05-11",
            ),
        ),
        operational_manifest_path=operational_manifest,
        history_dir=tmp_path / "live_history",
        now=lambda: datetime(2026, 5, 11, 1, 0, tzinfo=UTC),
        sleep=sleeps.append,
        jitter=lambda jitter_max: jitter_max,
    )

    assert summary["bulk_started"] is True
    assert summary["bulk_completed"] is True
    assert provider.fetched_targets == [
        ("S1", "2026-05-11"),
        ("S1", "2026-05-08"),
        ("S2", "2026-05-11"),
        ("S2", "2026-05-08"),
    ]
    assert sleeps == [2.8, 2.8]
    provider_result = summary["provider_results"][0]
    assert provider_result["pacing"] == {
        "base_delay_seconds": 2.0,
        "jitter_max_seconds": 0.8,
    }
    assert provider_result["aggregate_counts"]["fetched"] == 3
    assert provider_result["written_snapshot_count"] == 3


def test_live_baseline_backfill_stops_provider_after_rate_limit(
    tmp_path: Path,
) -> None:
    operational_manifest = _write_normalized_manifest(
        tmp_path,
        {
            "2026-05-11": [
                _normalized_row(source_provider_id="provider_alpha", etf_id="A1")
            ],
            "2026-05-08": [
                _normalized_row(
                    source_provider_id="provider_alpha",
                    etf_id="A1",
                    partition_date="2026-05-08",
                )
            ],
        },
    )
    fixture = _write_provider_fixture(
        tmp_path,
        source_provider_id="provider_alpha",
        provider_etf_ids=["A1"],
        filename="alpha_rate_limited.json",
        holdings_results=[
            {
                "source_provider_id": "provider_alpha",
                "provider_etf_id": "A1",
                "requested_date": "2026-05-11",
                "observed_date": "2026-05-11",
                "outcome": "fetched",
                "holdings": [_source_holding()],
            },
            {
                "source_provider_id": "provider_alpha",
                "provider_etf_id": "A1",
                "requested_date": "2026-05-08",
                "outcome": "rate_limited",
                "failure_code_class": "rate_limited",
                "holdings": [],
            },
        ],
    )
    catalog_dir = tmp_path / "catalog"
    provider = _RecordingProvider(_collect_catalog(fixture, catalog_dir))

    summary = run_live_baseline_backfill(
        provider_inputs=(
            LiveBaselineProviderInput(
                provider=provider,
                source_catalog_path=catalog_dir / "source_catalog.json",
                universe_state_path=catalog_dir / "universe_state.json",
                representative_provider_etf_id="A1",
                representative_requested_date="2026-05-11",
            ),
        ),
        operational_manifest_path=operational_manifest,
        history_dir=tmp_path / "live_history",
        now=lambda: datetime(2026, 5, 11, 1, 0, tzinfo=UTC),
    )

    provider_result = summary["provider_results"][0]
    assert summary["bulk_started"] is True
    assert summary["bulk_completed"] is False
    assert provider_result["stopped"] is True
    assert provider_result["stop_reason"] == "rate_limited"
    assert provider_result["aggregate_counts"]["rate_limited"] == 1
    assert provider.fetched_targets == [("A1", "2026-05-11"), ("A1", "2026-05-08")]


def test_live_baseline_backfill_is_incomplete_when_targets_fail_without_stopping(
    tmp_path: Path,
) -> None:
    operational_manifest = _write_normalized_manifest(
        tmp_path,
        {
            "2026-05-11": [
                _normalized_row(source_provider_id="provider_alpha", etf_id="A1")
            ],
            "2026-05-08": [
                _normalized_row(
                    source_provider_id="provider_alpha",
                    etf_id="A1",
                    partition_date="2026-05-08",
                )
            ],
        },
    )
    fixture = _write_provider_fixture(
        tmp_path,
        source_provider_id="provider_alpha",
        provider_etf_ids=["A1"],
        filename="alpha_failed_target.json",
        holdings_results=[
            {
                "source_provider_id": "provider_alpha",
                "provider_etf_id": "A1",
                "requested_date": "2026-05-11",
                "observed_date": "2026-05-11",
                "outcome": "fetched",
                "holdings": [_source_holding()],
            },
            {
                "source_provider_id": "provider_alpha",
                "provider_etf_id": "A1",
                "requested_date": "2026-05-08",
                "outcome": "failed",
                "failure_code_class": "invalid_provider_payload",
                "holdings": [],
            },
        ],
    )
    catalog_dir = tmp_path / "catalog"
    provider = _RecordingProvider(_collect_catalog(fixture, catalog_dir))

    summary = run_live_baseline_backfill(
        provider_inputs=(
            LiveBaselineProviderInput(
                provider=provider,
                source_catalog_path=catalog_dir / "source_catalog.json",
                universe_state_path=catalog_dir / "universe_state.json",
                representative_provider_etf_id="A1",
                representative_requested_date="2026-05-11",
            ),
        ),
        operational_manifest_path=operational_manifest,
        history_dir=tmp_path / "live_history",
        now=lambda: datetime(2026, 5, 11, 1, 0, tzinfo=UTC),
    )

    provider_result = summary["provider_results"][0]
    assert summary["bulk_started"] is True
    assert summary["bulk_completed"] is False
    assert provider_result["stopped"] is False
    assert provider_result["aggregate_counts"]["failed"] == 1
    assert provider.fetched_targets == [("A1", "2026-05-11"), ("A1", "2026-05-08")]


def test_live_baseline_backfill_retries_blocked_target_after_two_and_ten_minutes(
    tmp_path: Path,
) -> None:
    operational_manifest = _write_normalized_manifest(
        tmp_path,
        {
            "2026-05-11": [
                _normalized_row(source_provider_id="provider_alpha", etf_id="A1")
            ],
            "2026-05-08": [
                _normalized_row(
                    source_provider_id="provider_alpha",
                    etf_id="A1",
                    partition_date="2026-05-08",
                )
            ],
        },
    )
    fixture = _write_provider_fixture(
        tmp_path,
        source_provider_id="provider_alpha",
        provider_etf_ids=["A1"],
        filename="alpha_retryable_blocked.json",
        holdings_results=[
            {
                "source_provider_id": "provider_alpha",
                "provider_etf_id": "A1",
                "requested_date": "2026-05-11",
                "observed_date": "2026-05-11",
                "outcome": "fetched",
                "holdings": [_source_holding()],
            },
            {
                "source_provider_id": "provider_alpha",
                "provider_etf_id": "A1",
                "requested_date": "2026-05-08",
                "observed_date": "2026-05-08",
                "outcome": "fetched",
                "holdings": [_source_holding()],
            },
        ],
    )
    catalog_dir = tmp_path / "catalog"
    provider = _SequencedProvider(
        _collect_catalog(fixture, catalog_dir),
        {
            ("A1", "2026-05-11"): [
                _fetch_result(
                    source_provider_id="provider_alpha",
                    provider_etf_id="A1",
                    requested_date="2026-05-11",
                ),
                _fetch_result(
                    source_provider_id="provider_alpha",
                    provider_etf_id="A1",
                    requested_date="2026-05-11",
                ),
            ],
            ("A1", "2026-05-08"): [
                _fetch_result(
                    source_provider_id="provider_alpha",
                    provider_etf_id="A1",
                    requested_date="2026-05-08",
                    outcome="rate_limited",
                    failure_code_class="blocked",
                    holdings=(),
                ),
                _fetch_result(
                    source_provider_id="provider_alpha",
                    provider_etf_id="A1",
                    requested_date="2026-05-08",
                    outcome="rate_limited",
                    failure_code_class="blocked",
                    holdings=(),
                ),
                _fetch_result(
                    source_provider_id="provider_alpha",
                    provider_etf_id="A1",
                    requested_date="2026-05-08",
                ),
            ],
        },
    )
    sleeps: list[float] = []

    summary = run_live_baseline_backfill(
        provider_inputs=(
            LiveBaselineProviderInput(
                provider=provider,
                source_catalog_path=catalog_dir / "source_catalog.json",
                universe_state_path=catalog_dir / "universe_state.json",
                representative_provider_etf_id="A1",
                representative_requested_date="2026-05-11",
            ),
        ),
        operational_manifest_path=operational_manifest,
        history_dir=tmp_path / "live_history",
        now=lambda: datetime(2026, 5, 11, 1, 0, tzinfo=UTC),
        sleep=sleeps.append,
        jitter=lambda jitter_max: 0.0,
    )

    provider_result = summary["provider_results"][0]
    assert summary["bulk_completed"] is True
    assert provider_result["stopped"] is False
    assert provider_result["aggregate_counts"]["rate_limited"] == 0
    assert provider_result["target_outcomes"][-1]["retry_attempt_count"] == 2
    assert provider.fetched_targets == [
        ("A1", "2026-05-11"),
        ("A1", "2026-05-08"),
        ("A1", "2026-05-08"),
        ("A1", "2026-05-08"),
    ]
    assert sleeps == [120.0, 600.0]


def test_live_baseline_backfill_stops_after_three_blocked_attempts(
    tmp_path: Path,
) -> None:
    operational_manifest = _write_normalized_manifest(
        tmp_path,
        {
            "2026-05-11": [
                _normalized_row(source_provider_id="provider_alpha", etf_id="A1")
            ],
            "2026-05-08": [
                _normalized_row(
                    source_provider_id="provider_alpha",
                    etf_id="A1",
                    partition_date="2026-05-08",
                )
            ],
        },
    )
    fixture = _write_provider_fixture(
        tmp_path,
        source_provider_id="provider_alpha",
        provider_etf_ids=["A1"],
        filename="alpha_blocked.json",
        holdings_results=[
            {
                "source_provider_id": "provider_alpha",
                "provider_etf_id": "A1",
                "requested_date": "2026-05-11",
                "observed_date": "2026-05-11",
                "outcome": "fetched",
                "holdings": [_source_holding()],
            },
        ],
    )
    catalog_dir = tmp_path / "catalog"
    blocked_result = _fetch_result(
        source_provider_id="provider_alpha",
        provider_etf_id="A1",
        requested_date="2026-05-08",
        outcome="rate_limited",
        failure_code_class="blocked",
        holdings=(),
    )
    provider = _SequencedProvider(
        _collect_catalog(fixture, catalog_dir),
        {
            ("A1", "2026-05-11"): [
                _fetch_result(
                    source_provider_id="provider_alpha",
                    provider_etf_id="A1",
                    requested_date="2026-05-11",
                ),
                _fetch_result(
                    source_provider_id="provider_alpha",
                    provider_etf_id="A1",
                    requested_date="2026-05-11",
                ),
            ],
            ("A1", "2026-05-08"): [
                blocked_result,
                blocked_result,
                blocked_result,
            ],
        },
    )
    sleeps: list[float] = []

    summary = run_live_baseline_backfill(
        provider_inputs=(
            LiveBaselineProviderInput(
                provider=provider,
                source_catalog_path=catalog_dir / "source_catalog.json",
                universe_state_path=catalog_dir / "universe_state.json",
                representative_provider_etf_id="A1",
                representative_requested_date="2026-05-11",
            ),
        ),
        operational_manifest_path=operational_manifest,
        history_dir=tmp_path / "live_history",
        now=lambda: datetime(2026, 5, 11, 1, 0, tzinfo=UTC),
        sleep=sleeps.append,
        jitter=lambda jitter_max: 0.0,
    )

    provider_result = summary["provider_results"][0]
    assert summary["bulk_completed"] is False
    assert provider_result["stopped"] is True
    assert provider_result["stop_reason"] == "blocked"
    assert provider_result["aggregate_counts"]["rate_limited"] == 1
    assert provider_result["target_outcomes"][-1]["retry_attempt_count"] == 2
    assert provider.fetched_targets == [
        ("A1", "2026-05-11"),
        ("A1", "2026-05-08"),
        ("A1", "2026-05-08"),
        ("A1", "2026-05-08"),
    ]
    assert sleeps == [120.0, 600.0]


def test_live_source_rolling_retention_prunes_per_run_evidence_only(
    tmp_path: Path,
) -> None:
    live_root = tmp_path / "live-source"
    history_parts = live_root / "holdings-history" / "holdings_history.json.parts"
    history_parts.mkdir(parents=True)
    (live_root / "holdings-history" / "holdings_history.json").write_text(
        "{}",
        encoding="utf-8",
    )
    (history_parts / "2026-05-11.jsonl").write_text("{}\n", encoding="utf-8")
    evidence_root = live_root / "evidence"
    artifacts_root = live_root / "artifacts"
    for parent in (evidence_root, artifacts_root):
        for index in range(12):
            run_dir = parent / f"run_202605{index:02d}"
            run_dir.mkdir(parents=True)
            (run_dir / "summary.json").write_text("{}", encoding="utf-8")

    summary = apply_live_source_rolling_retention(live_root=live_root)

    assert summary["schema_version"] == "agent_treport.live_source.retention.v1"
    assert summary["keep_latest"] == 10
    assert summary["retention_roots"] == [
        {"name": "evidence", "before_count": 12, "after_count": 10, "pruned_count": 2},
        {"name": "artifacts", "before_count": 12, "after_count": 10, "pruned_count": 2},
    ]
    assert (live_root / "holdings-history" / "holdings_history.json").is_file()
    assert (history_parts / "2026-05-11.jsonl").is_file()
    assert not (evidence_root / "run_20260500").exists()
    assert not (evidence_root / "run_20260501").exists()
    assert (evidence_root / "run_20260502").is_dir()
    assert (evidence_root / "run_20260511").is_dir()
    rendered = json.dumps(summary, ensure_ascii=False)
    assert str(tmp_path) not in rendered
    assert "holdings_history.json" not in rendered


def test_live_source_rolling_retention_keeps_protected_current_run(
    tmp_path: Path,
) -> None:
    live_root = tmp_path / "live-source"
    summaries_root = live_root / "daily-smoke-summaries"
    protected_run = summaries_root / "run_20260519_validated_provider_preflight_001"
    protected_run.mkdir(parents=True)
    (protected_run / "smoke_summary.json").write_text("{}", encoding="utf-8")
    for index in range(10):
        run_dir = summaries_root / f"run_pre_publish_{index:02d}"
        run_dir.mkdir(parents=True)
        (run_dir / "smoke_summary.json").write_text("{}", encoding="utf-8")

    summary = apply_live_source_rolling_retention(
        live_root=live_root,
        retention_roots=("daily-smoke-summaries",),
        protected_run_dir_names=("run_20260519_validated_provider_preflight_001",),
    )

    assert summary["retention_roots"] == [
        {
            "name": "daily-smoke-summaries",
            "before_count": 11,
            "after_count": 10,
            "pruned_count": 1,
            "protected_count": 1,
        }
    ]
    assert protected_run.is_dir()
    assert not (summaries_root / "run_pre_publish_00").exists()
    assert (summaries_root / "run_pre_publish_09").is_dir()


def test_live_collection_health_summary_reports_provider_level_gaps(
    tmp_path: Path,
) -> None:
    operational_manifest = _write_normalized_manifest(
        tmp_path,
        {
            "2026-05-11": [
                _normalized_row(etf_id="2ETF35", security_id="SEC_A"),
                _normalized_row(etf_id="2ETF36", security_id="SEC_B"),
            ],
            "2026-05-08": [
                _normalized_row(
                    etf_id="2ETF35",
                    partition_date="2026-05-08",
                    security_id="SEC_A",
                ),
                _normalized_row(
                    etf_id="2ETF36",
                    partition_date="2026-05-08",
                    security_id="SEC_B",
                ),
            ],
        },
    )
    fixture_path = _write_multi_etf_fake_source_provider_fixture(
        tmp_path,
        holdings_by_provider_and_date={
            ("2ETF35", "2026-05-11"): [_source_holding(security_id="SEC_A")],
            ("2ETF36", "2026-05-08"): [_source_holding(security_id="SEC_B")],
        },
    )
    catalog_dir = tmp_path / "catalog"
    history_dir = tmp_path / "live_history"
    provider = _collect_catalog(fixture_path, catalog_dir)
    update_holdings_history_source(
        provider=provider,
        source_catalog_path=catalog_dir / "source_catalog.json",
        universe_state_path=catalog_dir / "universe_state.json",
        history_dir=history_dir,
        requested_date="2026-05-11",
        provider_etf_ids={"2ETF35"},
        now=lambda: datetime(2026, 5, 11, 1, 0, tzinfo=UTC),
    )
    update_holdings_history_source(
        provider=provider,
        source_catalog_path=catalog_dir / "source_catalog.json",
        universe_state_path=catalog_dir / "universe_state.json",
        history_dir=history_dir,
        requested_date="2026-05-08",
        provider_etf_ids={"2ETF36"},
        now=lambda: datetime(2026, 5, 11, 1, 5, tzinfo=UTC),
    )

    summary = summarize_live_collection_health(
        source_provider_id="provider_kodex_fake",
        source_catalog_path=catalog_dir / "source_catalog.json",
        universe_state_path=catalog_dir / "universe_state.json",
        history_dir=history_dir,
        operational_manifest_path=operational_manifest,
        latest_source_summary={
            "target_outcomes": [
                {
                    "etf_id": "etf_provider_kodex_fake_2etf35",
                    "outcome": "failed",
                    "failure_code_class": "provider_response",
                }
            ]
        },
    )

    assert summary == {
        "schema_version": "agent_treport.live_source.daily_health.v1",
        "source_provider_id": "provider_kodex_fake",
        "tracked_active_strategy_etf_count": 2,
        "current_up_to_date_count": 1,
        "missing_snapshot_count": 2,
        "failed_target_count": 1,
        "stale_target_count": 1,
        "window_gap_count": 0,
        "next_backfill_target_count": 2,
        "last_successful_observed_date": "2026-05-11",
        "missing_snapshot_etf_id_samples": [
            "etf_provider_kodex_fake_2etf35",
            "etf_provider_kodex_fake_2etf36",
        ],
        "failed_target_etf_id_samples": ["etf_provider_kodex_fake_2etf35"],
        "stale_target_etf_id_samples": ["etf_provider_kodex_fake_2etf36"],
        "window_gap_etf_id_samples": [],
        "next_backfill_etf_id_samples": [
            "etf_provider_kodex_fake_2etf35",
            "etf_provider_kodex_fake_2etf36",
        ],
    }
    rendered = json.dumps(summary, ensure_ascii=False)
    assert str(tmp_path) not in rendered
    assert "provider_response" not in rendered


def test_daily_collection_health_reports_discovery_targets_without_operational_copy(
    tmp_path: Path,
) -> None:
    operational_manifest = _write_normalized_manifest(
        tmp_path,
        {
            "2026-05-11": [_normalized_row(etf_id="2ETF35", security_id="SEC_A")],
            "2026-05-08": [
                _normalized_row(
                    etf_id="2ETF35",
                    partition_date="2026-05-08",
                    security_id="SEC_A",
                )
            ],
        },
    )
    fixture_path = _write_multi_etf_fake_source_provider_fixture(
        tmp_path,
        holdings_by_provider_and_date={
            ("2ETF35", "2026-05-11"): [_source_holding(security_id="SEC_A")],
            ("2ETF35", "2026-05-08"): [_source_holding(security_id="SEC_A")],
        },
    )
    catalog_dir = tmp_path / "catalog"
    history_dir = tmp_path / "live_history"
    provider = _collect_catalog(fixture_path, catalog_dir)
    update_holdings_history_source(
        provider=provider,
        source_catalog_path=catalog_dir / "source_catalog.json",
        universe_state_path=catalog_dir / "universe_state.json",
        history_dir=history_dir,
        requested_date="2026-05-11",
        provider_etf_ids={"2ETF35"},
        now=lambda: datetime(2026, 5, 11, 1, 0, tzinfo=UTC),
    )
    update_holdings_history_source(
        provider=provider,
        source_catalog_path=catalog_dir / "source_catalog.json",
        universe_state_path=catalog_dir / "universe_state.json",
        history_dir=history_dir,
        requested_date="2026-05-08",
        provider_etf_ids={"2ETF35"},
        now=lambda: datetime(2026, 5, 11, 1, 5, tzinfo=UTC),
    )

    summary = summarize_live_collection_health(
        source_provider_id="provider_kodex_fake",
        source_catalog_path=catalog_dir / "source_catalog.json",
        universe_state_path=catalog_dir / "universe_state.json",
        history_dir=history_dir,
        operational_manifest_path=operational_manifest,
    )

    assert summary["missing_snapshot_count"] == 2
    assert summary["window_gap_count"] == 0
    assert summary["window_gap_etf_id_samples"] == []
    assert summary["next_backfill_target_count"] == 2
    assert summary["next_backfill_etf_id_samples"] == [
        "etf_provider_kodex_fake_2etf36"
    ]


def test_run_live_source_baseline_cli_uses_config_and_gates_bulk(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        operational_manifest = _write_normalized_manifest(
            tmp_path,
            {
                "2026-05-11": [_normalized_row(etf_id="2ETF35", security_id="SEC_A")],
                "2026-05-08": [
                    _normalized_row(
                        etf_id="2ETF35",
                        partition_date="2026-05-08",
                        security_id="SEC_A",
                    )
                ],
            },
        )
        fixture_path = _write_fake_source_provider_fixture(
            tmp_path,
            holdings=[_source_holding(security_id="SEC_A")],
        )
        fixture_payload = json.loads(fixture_path.read_text(encoding="utf-8"))
        fixture_payload["holdings_results"].append(
            {
                "source_provider_id": "provider_kodex_fake",
                "provider_etf_id": "2ETF35",
                "requested_date": "2026-05-08",
                "observed_date": "2026-05-08",
                "outcome": "fetched",
                "holdings": [_source_holding(security_id="SEC_A")],
            }
        )
        fixture_path.write_text(json.dumps(fixture_payload), encoding="utf-8")
        catalog_dir = tmp_path / "catalog"
        provider = _collect_catalog(fixture_path, catalog_dir)
        config_path = tmp_path / "baseline_config.json"
        _write_json(
            config_path,
            {
                "schema_version": "agent_treport.live_source.baseline_config.v1",
                "providers": [
                    {
                        "source_provider_id": provider.source_provider_id,
                        "fixture_path": str(fixture_path),
                        "source_catalog_path": str(catalog_dir / "source_catalog.json"),
                        "universe_state_path": str(catalog_dir / "universe_state.json"),
                        "representative_provider_etf_id": "2ETF35",
                        "representative_requested_date": "2026-05-11",
                    }
                ],
            },
        )
        output = StringIO()
        errors = StringIO()

        exit_code = await run_cli_async(
            [
                "run-live-source-baseline",
                "--config-path",
                str(config_path),
                "--operational-holdings-path",
                str(operational_manifest),
                "--history-dir",
                str(tmp_path / "live_history"),
            ],
            stdout=output,
            stderr=errors,
            collection_now=lambda: datetime(2026, 5, 11, 1, 0, tzinfo=UTC),
        )

        payload = json.loads(output.getvalue())
        assert exit_code == 0, errors.getvalue()
        assert errors.getvalue() == ""
        assert payload["schema_version"] == "agent_treport.live_source.baseline_backfill.v1"
        assert payload["bulk_started"] is True
        assert payload["bulk_completed"] is True
        assert payload["representative_pass_count"] == 1
        assert payload["aggregate_counts"]["written_snapshot_count"] == 1
        rendered = output.getvalue()
        assert str(tmp_path) not in rendered
        assert "NVIDIA Corp" not in rendered

    _run_async(scenario())


def test_run_live_source_baseline_live_requires_separate_approval_before_provider_creation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    async def scenario() -> None:
        operational_manifest = _write_normalized_manifest(
            tmp_path,
            rows_by_date={
                "2026-05-08": [_normalized_row(security_id="SEC_A")],
                "2026-05-11": [_normalized_row(security_id="SEC_A")],
            },
        )
        config_path = tmp_path / "baseline_config.json"
        history_dir = tmp_path / "live_history"
        approval_path = tmp_path / "approval" / "baseline_approval.json"
        provider_created = False
        _write_json(
            config_path,
            {
                "schema_version": "agent_treport.live_source.baseline_config.v1",
                "providers": [
                    {
                        "source_provider_id": "tiger",
                        "source_catalog_path": "source_catalog.json",
                        "universe_state_path": "universe_state.json",
                        "representative_provider_etf_id": "KR7001",
                        "representative_requested_date": "2026-05-11",
                    }
                ],
            },
        )

        def fail_if_provider_created(_provider_id: str):
            nonlocal provider_created
            provider_created = True
            raise AssertionError("live baseline provider should wait for approval")

        monkeypatch.setattr(
            "agent_treport.cli.create_live_source_provider",
            fail_if_provider_created,
        )
        stdout = StringIO()
        stderr = StringIO()

        exit_code = await run_cli_async(
            [
                "run-live-source-baseline",
                "--config-path",
                str(config_path),
                "--operational-holdings-path",
                str(operational_manifest),
                "--history-dir",
                str(history_dir),
                "--live",
                "--approval-path",
                str(approval_path),
            ],
            stdout=stdout,
            stderr=stderr,
        )

        payload = json.loads(stdout.getvalue())
        blocked_path = history_dir / "live_source_baseline_approval_block.json"
        preflight_path = history_dir / "daily_operational_external_data_preflight.json"
        preflight = json.loads(preflight_path.read_text(encoding="utf-8"))

        assert stderr.getvalue() == ""
        assert exit_code == 1
        assert provider_created is False
        assert payload == json.loads(blocked_path.read_text(encoding="utf-8"))
        assert payload["approval"]["missing_scopes"] == ["live_source_baseline"]
        assert preflight["approval"]["required_scopes"] == ["live_source_baseline"]
        assert preflight["boundary"]["live_source_provider_ids"] == ["tiger"]
        assert str(tmp_path) not in preflight_path.read_text(encoding="utf-8")

    _run_async(scenario())
