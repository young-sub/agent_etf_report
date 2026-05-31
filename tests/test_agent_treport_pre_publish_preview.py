from __future__ import annotations

import asyncio
import json
import shlex
import shutil
from collections.abc import Sequence
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from typing import Any, cast

import pytest
from agent_pack.models import Message, ModelResponse, TextBlock
from agent_pack.models_client import FakeModelClient
from agent_pack.store import SQLiteRunStore
from agent_pack.trace_export import build_trace_export_record

from agent_treport.cli import run_cli_async
from agent_treport.signal_report.external_evidence.contracts import (
    EvidenceCategory,
    ExternalEvidenceCandidate,
    ExternalEvidenceProvider,
    ExternalEvidenceProviderOutcome,
    ExternalEvidenceRequestContext,
    ExternalEvidenceTarget,
    ProviderOutcomeStatus,
)

FULL_LIVE_EXTERNAL_PROVIDER_IDS = [
    "finnhub",
    "yfinance",
    "dart",
    "alpha_vantage",
    "newsapi",
    "naver",
]
KNOWN_UNVALIDATED_EXTERNAL_PROVIDER_EXCEPTIONS = ["sec_edgar"]
FULL_LIVE_SOURCE_PROVIDER_IDS = [
    "kodex",
    "ace",
    "hyundai",
    "timefolio",
    "tiger",
    "rise",
    "sol",
]


@pytest.fixture(autouse=True)
def _isolate_daily_smoke_summary_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)


def run_async(awaitable):
    return asyncio.run(awaitable)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _write_universe_state(path: Path) -> None:
    etfs = [
        ("etf_ace_ai", "ACE AI Active ETF", "brand_ace", "ace"),
        ("etf_tiger_robotics", "TIGER Robotics Active ETF", "brand_tiger", "tiger"),
        ("etf_sol_semis", "SOL Semiconductors Active ETF", "brand_sol", "sol"),
        ("etf_kodex_missing", "KODEX Missing Window Active ETF", "brand_kodex", "kodex"),
    ]
    _write_json(
        path,
        {
            "schema_version": "agent_treport.native_universe.state.v1",
            "collection_source_type": "source_provider",
            "updated_at": "2026-05-15T01:00:00+00:00",
            "etfs": [
                {
                    "etf_id": etf_id,
                    "etf_name": etf_name,
                    "brand_id": brand_id,
                    "source_provider_id": provider_id,
                    "status": "active",
                }
                for etf_id, etf_name, brand_id, provider_id in etfs
            ],
            "brands": [
                {
                    "brand_id": brand_id,
                    "brand_name": brand_id.replace("brand_", "").upper(),
                    "source_provider_id": provider_id,
                    "status": "active",
                }
                for _etf_id, _etf_name, brand_id, provider_id in etfs
            ],
        },
    )


def _history_row(
    *,
    etf_id: str,
    etf_name: str,
    brand_id: str,
    provider_id: str,
    observed_date: str,
    security_id: str,
    name: str,
    weight_percent: float,
) -> dict[str, object]:
    return {
        "etf_id": etf_id,
        "etf_name": etf_name,
        "brand_id": brand_id,
        "source_provider_id": provider_id,
        "as_of_date": observed_date,
        "security_id": security_id,
        "ticker": None,
        "name": name,
        "market": "US",
        "sector": "Information Technology",
        "theme": "AI infrastructure",
        "country": "US",
        "weight_percent": weight_percent,
        "shares": 1000.0,
        "market_value_krw": weight_percent * 1000000.0,
        "price_krw": None,
        "is_cash": False,
        "security_classification": "ticker_candidate",
    }


def _write_holdings_history(
    history_dir: Path,
    *,
    include_sol_previous: bool = True,
    include_sol_current: bool = True,
    include_previous_partition: bool = True,
) -> None:
    current_date = "2026-05-15"
    previous_date = "2026-05-14"
    current_rows = [
        _history_row(
            etf_id="etf_ace_ai",
            etf_name="ACE AI Active ETF",
            brand_id="brand_ace",
            provider_id="ace",
            observed_date=current_date,
            security_id="sec_nvda",
            name="NVIDIA Corp.",
            weight_percent=7.5,
        ),
        _history_row(
            etf_id="etf_tiger_robotics",
            etf_name="TIGER Robotics Active ETF",
            brand_id="brand_tiger",
            provider_id="tiger",
            observed_date=current_date,
            security_id="sec_msft",
            name="Microsoft Corp.",
            weight_percent=6.5,
        ),
        _history_row(
            etf_id="etf_kodex_missing",
            etf_name="KODEX Missing Window Active ETF",
            brand_id="brand_kodex",
            provider_id="kodex",
            observed_date=current_date,
            security_id="sec_kodex_gap",
            name="KODEX Gap Holding",
            weight_percent=4.5,
        ),
    ]
    if include_sol_current:
        current_rows.insert(
            2,
            _history_row(
                etf_id="etf_sol_semis",
                etf_name="SOL Semiconductors Active ETF",
                brand_id="brand_sol",
                provider_id="sol",
                observed_date=current_date,
                security_id="sec_avgo",
                name="Broadcom Inc.",
                weight_percent=5.5,
            ),
        )
    previous_rows = [
        _history_row(
            etf_id="etf_ace_ai",
            etf_name="ACE AI Active ETF",
            brand_id="brand_ace",
            provider_id="ace",
            observed_date=previous_date,
            security_id="sec_nvda",
            name="NVIDIA Corp.",
            weight_percent=6.0,
        ),
        _history_row(
            etf_id="etf_tiger_robotics",
            etf_name="TIGER Robotics Active ETF",
            brand_id="brand_tiger",
            provider_id="tiger",
            observed_date=previous_date,
            security_id="sec_msft",
            name="Microsoft Corp.",
            weight_percent=5.0,
        ),
    ]
    if include_sol_previous:
        previous_rows.append(
            _history_row(
                etf_id="etf_sol_semis",
                etf_name="SOL Semiconductors Active ETF",
                brand_id="brand_sol",
                provider_id="sol",
                observed_date=previous_date,
                security_id="sec_avgo",
                name="Broadcom Inc.",
                weight_percent=4.0,
            )
        )
    _write_jsonl(
        history_dir / "holdings_history.json.parts" / f"{current_date}.jsonl",
        current_rows,
    )
    if include_previous_partition:
        _write_jsonl(
            history_dir / "holdings_history.json.parts" / f"{previous_date}.jsonl",
            previous_rows,
        )
    dates = [current_date, previous_date] if include_previous_partition else [current_date]
    current_snapshot_count = len({row["etf_id"] for row in current_rows})
    previous_snapshot_count = len({row["etf_id"] for row in previous_rows})
    partitions: dict[str, dict[str, object]] = {
        current_date: {
            "file": f"holdings_history.json.parts/{current_date}.jsonl",
            "record_count": len(current_rows),
            "snapshot_count": current_snapshot_count,
        }
    }
    if include_previous_partition:
        partitions[previous_date] = {
            "file": f"holdings_history.json.parts/{previous_date}.jsonl",
            "record_count": len(previous_rows),
            "snapshot_count": previous_snapshot_count,
        }
    _write_json(
        history_dir / "holdings_history.json",
        {
            "schema_version": "agent_treport.native_holdings.history.v1",
            "storage_format": "native_history_partitioned_jsonl_v1",
            "updated_at": "2026-05-15T01:30:00+00:00",
            "dates": dates,
            "record_count": len(current_rows)
            + (len(previous_rows) if include_previous_partition else 0),
            "snapshot_count": current_snapshot_count
            + (previous_snapshot_count if include_previous_partition else 0),
            "partitions": partitions,
        },
    )


def _write_security_resolution(path: Path) -> None:
    tickers = {
        "sec_nvda": "NVDA",
        "sec_msft": "MSFT",
        "sec_avgo": "AVGO",
        "sec_kodex_gap": "KODEXGAP",
    }
    _write_json(
        path,
        {
            "schema_version": "agent_treport.security_resolution_export.v1",
            "mappings": [
                {
                    "security_id": security_id,
                    "ticker": ticker,
                    "name": f"{ticker} reviewed",
                    "exchange": "NASDAQ",
                    "security_classification": "ticker_candidate",
                }
                for security_id, ticker in tickers.items()
            ],
            "exclusions": [],
        },
    )


def _write_focus_set(
    path: Path,
    focus_etf_ids: list[str] | None = None,
) -> None:
    _write_json(
        path,
        {
            "schema_version": "agent_treport.focus_etf_set.v1",
            "focus_etf_ids": focus_etf_ids
            or ["etf_ace_ai", "etf_tiger_robotics", "etf_sol_semis"],
        },
    )


def _write_cached_external_evidence(
    evidence_path: Path,
    summary_path: Path,
    *,
    claim_scope: str = "signal:security:sec_nvda:weight_increase",
) -> None:
    _write_json(
        evidence_path,
        [
            {
                "evidence_id": "ev_nvda_financial",
                "ticker": "NVDA",
                "type": "price_volume",
                "source": "Fixture Financial Metrics",
                "title": "NVIDIA fixture financial evidence",
                "published_at": "2026-05-15T00:00:00+00:00",
                "url": None,
                "stance": "supporting",
                "strength": "moderate",
                "claim_scope": claim_scope,
                "evidence_role": "interpretation_support",
                "relevance": "high",
                "novelty": "new",
                "interpretation_basis": "Cached evidence is scoped to the exact claim.",
                "observed_direction": "increase",
            },
            {
                "evidence_id": "ev_nvda_disclosure",
                "ticker": "NVDA",
                "type": "company_disclosure",
                "source": "Fixture Disclosure",
                "title": "NVIDIA fixture disclosure evidence",
                "published_at": "2026-05-14T00:00:00+00:00",
                "url": None,
                "stance": "supporting",
                "strength": "moderate",
                "claim_scope": claim_scope,
                "evidence_role": "interpretation_support",
                "relevance": "high",
                "novelty": "new",
                "interpretation_basis": "Cached evidence is scoped to the exact claim.",
                "observed_direction": "increase",
            },
            {
                "evidence_id": "ev_nvda_news",
                "ticker": "NVDA",
                "type": "news",
                "source": "Fixture News",
                "title": "NVIDIA fixture market news",
                "published_at": "2026-05-13T00:00:00+00:00",
                "url": None,
                "stance": "supporting",
                "strength": "moderate",
                "claim_scope": claim_scope,
                "evidence_role": "interpretation_support",
                "relevance": "high",
                "novelty": "new",
                "interpretation_basis": "Cached evidence is scoped to the exact claim.",
                "observed_direction": "increase",
            },
        ],
    )
    _write_json(
        summary_path,
        {
            "schema_version": "agent_treport.external_evidence.summary.v1",
            "generated_at": "2026-05-15T02:00:00+00:00",
            "target_selection": {
                "selected_targets": [
                    {
                        "rank": 1,
                        "ticker": "NVDA",
                        "name": "NVIDIA Corp.",
                        "aggregation_key": "sec_nvda",
                        "security_group_id": None,
                        "member_security_ids": ["sec_nvda"],
                        "listing_keys": [],
                        "claim_scope": claim_scope,
                        "signal_type": "weight_increase",
                        "signal_direction": "increase",
                        "summary": "Top cached preview target.",
                    }
                ],
                "excluded_targets": [],
                "max_targets": 2,
            },
            "provider_outcomes": [
                {
                    "provider_id": "fixture_financial",
                    "category": "financial",
                    "status": "success",
                    "error_code": None,
                    "retryable": False,
                    "attempt_count": 1,
                    "stopped_reason": None,
                    "target_tickers": ["NVDA"],
                    "safe_message": "Cached financial evidence loaded.",
                    "deduped_count": 0,
                },
                {
                    "provider_id": "fixture_disclosure",
                    "category": "disclosure",
                    "status": "success",
                    "error_code": None,
                    "retryable": False,
                    "attempt_count": 1,
                    "stopped_reason": None,
                    "target_tickers": ["NVDA"],
                    "safe_message": "Cached disclosure evidence loaded.",
                    "deduped_count": 0,
                },
                {
                    "provider_id": "fixture_news",
                    "category": "news",
                    "status": "success",
                    "error_code": None,
                    "retryable": False,
                    "attempt_count": 1,
                    "stopped_reason": None,
                    "target_tickers": ["NVDA"],
                    "safe_message": "Cached news evidence loaded.",
                    "deduped_count": 0,
                },
            ],
            "category_coverage": {
                "financial": {
                    "coverage_ratio": 1.0,
                    "ticker_states": {"NVDA": "covered"},
                    "provider_states": {"fixture_financial": "success"},
                    "notes": ["financial:NVDA=covered"],
                },
                "disclosure": {
                    "coverage_ratio": 1.0,
                    "ticker_states": {"NVDA": "covered"},
                    "provider_states": {"fixture_disclosure": "success"},
                    "notes": ["disclosure:NVDA=covered"],
                },
                "news": {
                    "coverage_ratio": 1.0,
                    "ticker_states": {"NVDA": "covered"},
                    "provider_states": {"fixture_news": "success"},
                    "notes": ["news:NVDA=covered"],
                },
            },
            "dedupe": {
                "deduped_count": 0,
                "per_category_counts": {"financial": 1, "disclosure": 1, "news": 1},
            },
            "policy_failure": None,
            "evidence_path": str(evidence_path),
            "cooldown_path": None,
        },
    )


def test_pre_publish_preview_cached_evidence_user_ready_handoff(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        history_dir = tmp_path / "holdings-history"
        universe_state_path = tmp_path / "universe" / "universe_state.json"
        focus_set_path = tmp_path / "focus_set.json"
        resolution_path = tmp_path / "security_resolution.json"
        evidence_path = tmp_path / "external_evidence.json"
        evidence_summary_path = tmp_path / "external_evidence_summary.json"
        dest = tmp_path / "preview"
        stdout = StringIO()
        stderr = StringIO()
        _write_holdings_history(history_dir)
        _write_universe_state(universe_state_path)
        _write_focus_set(focus_set_path)
        _write_security_resolution(resolution_path)
        _write_cached_external_evidence(evidence_path, evidence_summary_path)

        exit_code = await run_cli_async(
            [
                "run-pre-publish-preview",
                "--run-id",
                "run_pre_publish_cached_user_ready",
                "--history-dir",
                str(history_dir),
                "--universe-state-path",
                str(universe_state_path),
                "--focus-etf-set-path",
                str(focus_set_path),
                "--observed-partitions",
                "2",
                "--security-resolution-path",
                str(resolution_path),
                "--evidence-path",
                str(evidence_path),
                "--evidence-summary-path",
                str(evidence_summary_path),
                "--dest",
                str(dest),
                "--model",
                "codex",
            ],
            stdout=stdout,
            stderr=stderr,
            collection_now=lambda: datetime(2026, 5, 15, 1, 45, tzinfo=UTC),
            readiness_now=lambda: datetime(2026, 5, 15, 2, 0, tzinfo=UTC),
            model_client_factory=lambda _config: FakeModelClient(
                [
                    ModelResponse(
                        message=Message(
                            role="assistant",
                            content=(TextBlock(text="pre-publish preview commentary"),),
                        )
                    )
                ]
            ),
        )

        payload: dict[str, Any] = json.loads(stdout.getvalue())
        artifacts = payload["references"]["artifacts"]
        handoff_path = dest / "pre_publish_handoff.json"

        assert stderr.getvalue() == ""
        assert exit_code == 0
        _assert_pre_publish_payload_path_safe(payload)
        assert payload["status"] == "user_ready"
        assert payload["delivery_blocked"] is False
        assert payload["preview"]["telegram_delivery"] == "not_sent"
        assert payload["external_evidence"]["status"] == "provided"
        assert payload["external_evidence"]["policy_failure"] is None
        assert set(artifacts) >= {
            "canonical_payload",
            "markdown_report",
            "html_report",
            "telegram_alert",
            "quality_report",
            "readiness",
            "collection_summary",
            "external_evidence",
            "external_evidence_summary",
            "provider_etf_exclusion_summary",
        }
        telegram_alert = Path(artifacts["telegram_alert"]["path"]).read_text(
            encoding="utf-8"
        )
        assert payload["preview"]["telegram_message"] == {
            "artifact_id": artifacts["telegram_alert"]["artifact_id"],
            "parse_mode": "HTML",
            "send_method": "sendMessage",
            "delivery_status": "not_sent",
            "text": telegram_alert,
        }
        assert payload["closure"]["full_live_pre_publish_artifact_closure"][
            "status"
        ] == "met"
        assert not Path(artifacts["external_evidence"]["path"]).is_absolute()
        assert Path(artifacts["external_evidence"]["path"]).resolve() == (
            evidence_path.resolve()
        )
        assert payload["references"]["follow_up"]["inspect"] == shlex.join(
            payload["references"]["follow_up"]["inspect_argv"]
        )
        assert set(payload["references"]["follow_up"]) == {"inspect", "inspect_argv"}
        assert "user_ready" in payload
        assert "operator_review_only" not in payload
        assert handoff_path.is_file()
        assert json.loads(handoff_path.read_text(encoding="utf-8")) == payload

    run_async(scenario())


def test_pre_publish_preview_timeout_blocks_user_ready_without_telegram_body(
    tmp_path: Path,
) -> None:
    class SlowModelClient:
        async def complete(self, request):
            _ = request
            await asyncio.sleep(1)
            return ModelResponse(
                message=Message(
                    role="assistant",
                    content=(TextBlock(text="late commentary"),),
                )
            )

    async def scenario() -> None:
        history_dir = tmp_path / "holdings-history"
        universe_state_path = tmp_path / "universe" / "universe_state.json"
        focus_set_path = tmp_path / "focus_set.json"
        resolution_path = tmp_path / "security_resolution.json"
        evidence_path = tmp_path / "external_evidence.json"
        evidence_summary_path = tmp_path / "external_evidence_summary.json"
        dest = tmp_path / "preview"
        stdout = StringIO()
        stderr = StringIO()
        _write_holdings_history(history_dir)
        _write_universe_state(universe_state_path)
        _write_focus_set(focus_set_path)
        _write_security_resolution(resolution_path)
        _write_cached_external_evidence(evidence_path, evidence_summary_path)

        exit_code = await run_cli_async(
            [
                "run-pre-publish-preview",
                "--run-id",
                "run_pre_publish_timeout",
                "--history-dir",
                str(history_dir),
                "--universe-state-path",
                str(universe_state_path),
                "--focus-etf-set-path",
                str(focus_set_path),
                "--observed-partitions",
                "2",
                "--security-resolution-path",
                str(resolution_path),
                "--evidence-path",
                str(evidence_path),
                "--evidence-summary-path",
                str(evidence_summary_path),
                "--preview-timeout-seconds",
                "0.001",
                "--dest",
                str(dest),
                "--model",
                "codex",
            ],
            stdout=stdout,
            stderr=stderr,
            collection_now=lambda: datetime(2026, 5, 15, 1, 45, tzinfo=UTC),
            readiness_now=lambda: datetime(2026, 5, 15, 2, 0, tzinfo=UTC),
            model_client_factory=lambda _config: SlowModelClient(),
        )

        payload = json.loads(stdout.getvalue())

        assert stderr.getvalue() == ""
        assert exit_code == 1
        assert payload["status"] == "failed"
        assert payload["reason"] == "preview_timeout"
        assert payload["timeout"]["status"] == "timed_out"
        assert payload["timeout"]["operator_override"] == "--allow-preview-timeout-overrun"
        assert "telegram_message" not in payload["preview"]
        assert payload["closure"]["full_user_ready_closure"]["status"] == "blocked"
        assert "external_evidence_summary" in payload["references"]["artifacts"]

    run_async(scenario())


def test_pre_publish_preview_default_timeout_is_600_seconds(
    tmp_path: Path,
    monkeypatch,
) -> None:
    async def scenario() -> None:
        history_dir = tmp_path / "holdings-history"
        universe_state_path = tmp_path / "universe" / "universe_state.json"
        focus_set_path = tmp_path / "focus_set.json"
        resolution_path = tmp_path / "security_resolution.json"
        evidence_path = tmp_path / "external_evidence.json"
        evidence_summary_path = tmp_path / "external_evidence_summary.json"
        dest = tmp_path / "preview"
        stdout = StringIO()
        stderr = StringIO()
        observed_timeouts: list[float | None] = []
        _write_holdings_history(history_dir)
        _write_universe_state(universe_state_path)
        _write_focus_set(focus_set_path)
        _write_security_resolution(resolution_path)
        _write_cached_external_evidence(evidence_path, evidence_summary_path)

        async def record_wait_for(awaitable, timeout=None):
            observed_timeouts.append(timeout)
            return await awaitable

        monkeypatch.setattr("agent_treport.cli.asyncio.wait_for", record_wait_for)

        exit_code = await run_cli_async(
            [
                "run-pre-publish-preview",
                "--run-id",
                "run_pre_publish_default_timeout",
                "--history-dir",
                str(history_dir),
                "--universe-state-path",
                str(universe_state_path),
                "--focus-etf-set-path",
                str(focus_set_path),
                "--observed-partitions",
                "2",
                "--security-resolution-path",
                str(resolution_path),
                "--evidence-path",
                str(evidence_path),
                "--evidence-summary-path",
                str(evidence_summary_path),
                "--dest",
                str(dest),
                "--model",
                "codex",
            ],
            stdout=stdout,
            stderr=stderr,
            collection_now=lambda: datetime(2026, 5, 15, 1, 45, tzinfo=UTC),
            readiness_now=lambda: datetime(2026, 5, 15, 2, 0, tzinfo=UTC),
            model_client_factory=lambda _config: FakeModelClient(
                [
                    ModelResponse(
                        message=Message(
                            role="assistant",
                            content=(TextBlock(text="default timeout preview"),),
                        )
                    )
                ]
            ),
        )

        assert stderr.getvalue() == ""
        assert exit_code == 0
        assert observed_timeouts == [600.0]

    run_async(scenario())


def test_pre_publish_preview_defaults_to_live_external_evidence_providers(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        history_dir = tmp_path / "holdings-history"
        universe_state_path = tmp_path / "universe" / "universe_state.json"
        focus_set_path = tmp_path / "focus_set.json"
        resolution_path = tmp_path / "security_resolution.json"
        dest = tmp_path / "preview"
        stdout = StringIO()
        stderr = StringIO()
        calls: list[dict[str, object]] = []
        _write_holdings_history(history_dir)
        _write_universe_state(universe_state_path)
        _write_focus_set(focus_set_path)
        _write_security_resolution(resolution_path)

        exit_code = await run_cli_async(
            [
                "run-pre-publish-preview",
                "--run-id",
                "run_pre_publish_live_defaults",
                "--history-dir",
                str(history_dir),
                "--universe-state-path",
                str(universe_state_path),
                "--focus-etf-set-path",
                str(focus_set_path),
                "--observed-partitions",
                "2",
                "--security-resolution-path",
                str(resolution_path),
                "--dest",
                str(dest),
                "--model",
                "codex",
            ],
            stdout=stdout,
            stderr=stderr,
            collection_now=lambda: datetime(2026, 5, 15, 1, 45, tzinfo=UTC),
            readiness_now=lambda: datetime(2026, 5, 15, 2, 0, tzinfo=UTC),
            external_evidence_provider_overrides={
                **_full_live_no_data_providers(calls),
                "sec_edgar": _failing_provider(
                    provider_id="sec_edgar",
                    category="disclosure",
                    status="rate_limited_exhausted",
                    calls=calls,
                ),
            },
            model_client_factory=lambda _config: FakeModelClient(
                [
                    ModelResponse(
                        message=Message(
                            role="assistant",
                            content=(TextBlock(text="live default preview commentary"),),
                        )
                    )
                ]
            ),
        )

        payload = json.loads(stdout.getvalue())
        summary = json.loads(
            (dest / "external_evidence_summary.json").read_text(encoding="utf-8")
        )

        assert stderr.getvalue() == ""
        assert exit_code == 0
        assert payload["status"] == "user_ready"
        assert payload["delivery_blocked"] is False
        assert payload["preview"]["telegram_delivery"] == "not_sent"
        assert [call["provider_id"] for call in calls] == FULL_LIVE_EXTERNAL_PROVIDER_IDS
        assert {call["live"] for call in calls} == {True}
        assert summary["target_selection"]["max_targets"] == 25
        assert [outcome["provider_id"] for outcome in summary["provider_outcomes"]] == (
            FULL_LIVE_EXTERNAL_PROVIDER_IDS
        )
        assert payload["external_evidence"]["required_provider_ids"] == (
            FULL_LIVE_EXTERNAL_PROVIDER_IDS
        )
        assert [
            item["provider_id"]
            for item in payload["external_evidence"][
                "known_unvalidated_provider_exceptions"
            ]
        ] == KNOWN_UNVALIDATED_EXTERNAL_PROVIDER_EXCEPTIONS
        assert summary["required_provider_ids"] == FULL_LIVE_EXTERNAL_PROVIDER_IDS
        assert [
            item["provider_id"]
            for item in summary["known_unvalidated_provider_exceptions"]
        ] == KNOWN_UNVALIDATED_EXTERNAL_PROVIDER_EXCEPTIONS
        assert {
            outcome["category"] for outcome in summary["provider_outcomes"]
        } == {"financial", "disclosure", "news"}

    run_async(scenario())


def test_pre_publish_preview_reuses_same_smoke_successful_evidence(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        history_dir = tmp_path / "holdings-history"
        universe_state_path = tmp_path / "universe" / "universe_state.json"
        focus_set_path = tmp_path / "focus_set.json"
        resolution_path = tmp_path / "security_resolution.json"
        dest = tmp_path / "preview"
        run_id = "run_pre_publish_reuse_same_smoke"
        first_stdout = StringIO()
        second_stdout = StringIO()
        stderr = StringIO()
        first_calls: list[dict[str, object]] = []
        second_calls: list[dict[str, object]] = []
        _write_holdings_history(history_dir)
        _write_universe_state(universe_state_path)
        _write_focus_set(focus_set_path)
        _write_security_resolution(resolution_path)
        argv = [
            "run-pre-publish-preview",
            "--run-id",
            run_id,
            "--history-dir",
            str(history_dir),
            "--universe-state-path",
            str(universe_state_path),
            "--focus-etf-set-path",
            str(focus_set_path),
            "--observed-partitions",
            "2",
            "--security-resolution-path",
            str(resolution_path),
            "--dest",
            str(dest),
            "--model",
            "codex",
        ]

        first_exit = await run_cli_async(
            argv,
            stdout=first_stdout,
            stderr=stderr,
            collection_now=lambda: datetime(2026, 5, 15, 1, 45, tzinfo=UTC),
            readiness_now=lambda: datetime(2026, 5, 15, 2, 0, tzinfo=UTC),
            external_evidence_provider_overrides=_full_live_no_data_providers(
                first_calls
            ),
            model_client_factory=lambda _config: FakeModelClient(
                [
                    ModelResponse(
                        message=Message(
                            role="assistant",
                            content=(TextBlock(text="first same-smoke commentary"),),
                        )
                    )
                ]
            ),
        )
        second_exit = await run_cli_async(
            argv,
            stdout=second_stdout,
            stderr=stderr,
            collection_now=lambda: datetime(2026, 5, 15, 1, 45, tzinfo=UTC),
            readiness_now=lambda: datetime(2026, 5, 15, 2, 0, tzinfo=UTC),
            external_evidence_provider_overrides={
                provider_id: _failing_provider(
                    provider_id=provider_id,
                    category=cast(EvidenceCategory, category),
                    status="provider_unavailable",
                    calls=second_calls,
                )
                for provider_id, category in {
                    "finnhub": "financial",
                    "yfinance": "financial",
                    "dart": "disclosure",
                    "alpha_vantage": "news",
                    "newsapi": "news",
                    "naver": "news",
                }.items()
            },
            model_client_factory=lambda _config: FakeModelClient(
                [
                    ModelResponse(
                        message=Message(
                            role="assistant",
                            content=(TextBlock(text="second same-smoke commentary"),),
                        )
                    )
                ]
            ),
        )

        second_payload = json.loads(second_stdout.getvalue())
        summary = json.loads(
            (dest / "external_evidence_summary.json").read_text(encoding="utf-8")
        )

        assert stderr.getvalue() == ""
        assert first_exit == 0
        assert second_exit == 0
        assert [call["provider_id"] for call in first_calls] == (
            FULL_LIVE_EXTERNAL_PROVIDER_IDS
        )
        assert second_calls == []
        assert second_payload["status"] == "user_ready"
        assert summary["evidence_reuse"] == {
            "status": "reused",
            "scope": "same_smoke",
            "reason": "matching_smoke_boundary",
            "reused_provider_ids": FULL_LIVE_EXTERNAL_PROVIDER_IDS,
        }

    run_async(scenario())


def test_pre_publish_preview_does_not_reuse_evidence_for_mismatched_smoke_boundary(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        history_dir = tmp_path / "holdings-history"
        universe_state_path = tmp_path / "universe" / "universe_state.json"
        focus_set_path = tmp_path / "focus_set.json"
        resolution_path = tmp_path / "security_resolution.json"
        dest = tmp_path / "preview"
        first_calls: list[dict[str, object]] = []
        second_calls: list[dict[str, object]] = []
        _write_holdings_history(history_dir)
        _write_universe_state(universe_state_path)
        _write_focus_set(focus_set_path)
        _write_security_resolution(resolution_path)

        async def run_once(run_id: str, calls: list[dict[str, object]]) -> int:
            return await run_cli_async(
                [
                    "run-pre-publish-preview",
                    "--run-id",
                    run_id,
                    "--history-dir",
                    str(history_dir),
                    "--universe-state-path",
                    str(universe_state_path),
                    "--focus-etf-set-path",
                    str(focus_set_path),
                    "--observed-partitions",
                    "2",
                    "--security-resolution-path",
                    str(resolution_path),
                    "--dest",
                    str(dest),
                    "--model",
                    "codex",
                ],
                stdout=StringIO(),
                stderr=StringIO(),
                collection_now=lambda: datetime(2026, 5, 15, 1, 45, tzinfo=UTC),
                readiness_now=lambda: datetime(2026, 5, 15, 2, 0, tzinfo=UTC),
                external_evidence_provider_overrides=_full_live_no_data_providers(calls),
                model_client_factory=lambda _config: FakeModelClient(
                    [
                        ModelResponse(
                            message=Message(
                                role="assistant",
                                content=(TextBlock(text=f"{run_id} commentary"),),
                            )
                        )
                    ]
                ),
            )

        first_exit = await run_once("run_pre_publish_reuse_boundary_a", first_calls)
        second_exit = await run_once("run_pre_publish_reuse_boundary_b", second_calls)
        summary = json.loads(
            (dest / "external_evidence_summary.json").read_text(encoding="utf-8")
        )

        assert first_exit == 0
        assert second_exit == 0
        assert [call["provider_id"] for call in first_calls] == (
            FULL_LIVE_EXTERNAL_PROVIDER_IDS
        )
        assert [call["provider_id"] for call in second_calls] == (
            FULL_LIVE_EXTERNAL_PROVIDER_IDS
        )
        assert summary["evidence_reuse"]["status"] == "not_reused"
        assert summary["evidence_reuse"]["reason"] == "smoke_boundary_mismatch"

    run_async(scenario())


def test_pre_publish_preview_writes_path_safe_daily_smoke_summary_package(
    tmp_path: Path,
    monkeypatch,
) -> None:
    async def scenario() -> None:
        monkeypatch.chdir(tmp_path)
        history_dir = tmp_path / "holdings-history"
        universe_state_path = tmp_path / "universe" / "universe_state.json"
        focus_set_path = tmp_path / "focus_set.json"
        resolution_path = tmp_path / "security_resolution.json"
        dest = tmp_path / "preview"
        run_id = "run_pre_publish_result_package"
        stdout = StringIO()
        stderr = StringIO()
        calls: list[dict[str, object]] = []
        _write_holdings_history(history_dir)
        _write_universe_state(universe_state_path)
        _write_focus_set(focus_set_path)
        _write_security_resolution(resolution_path)

        exit_code = await run_cli_async(
            [
                "run-pre-publish-preview",
                "--run-id",
                run_id,
                "--history-dir",
                str(history_dir),
                "--universe-state-path",
                str(universe_state_path),
                "--focus-etf-set-path",
                str(focus_set_path),
                "--observed-partitions",
                "2",
                "--security-resolution-path",
                str(resolution_path),
                "--dest",
                str(dest),
                "--model",
                "codex",
            ],
            stdout=stdout,
            stderr=stderr,
            collection_now=lambda: datetime(2026, 5, 15, 1, 45, tzinfo=UTC),
            readiness_now=lambda: datetime(2026, 5, 15, 2, 0, tzinfo=UTC),
            external_evidence_provider_overrides=_full_live_no_data_providers(calls),
            model_client_factory=lambda _config: FakeModelClient(
                [
                    ModelResponse(
                        message=Message(
                            role="assistant",
                            content=(TextBlock(text="package commentary"),),
                        )
                    )
                ]
            ),
        )

        payload = json.loads(stdout.getvalue())
        package_path = (
            tmp_path
            / "data"
            / "agent_treport"
            / "live-source"
            / "daily-smoke-summaries"
            / run_id
        )

        assert stderr.getvalue() == ""
        assert exit_code == 0
        assert payload["preview"]["result_package_path"] == str(
            Path("data/agent_treport/live-source/daily-smoke-summaries") / run_id
        )
        assert {
            "pre_publish_handoff.json",
            "smoke_summary.json",
            "approval_preflight_summary.json",
            "external_evidence_summary.json",
            "provider_exception_summary.json",
            "validation_command_results.json",
            "canonical_history_unchanged.json",
            "retention_summary.json",
        }.issubset({path.name for path in package_path.iterdir()})
        package_handoff = json.loads(
            (package_path / "pre_publish_handoff.json").read_text(encoding="utf-8")
        )
        smoke_summary = json.loads(
            (package_path / "smoke_summary.json").read_text(encoding="utf-8")
        )
        canonical_history = json.loads(
            (package_path / "canonical_history_unchanged.json").read_text(
                encoding="utf-8"
            )
        )
        serialized_package = "\n".join(
            path.read_text(encoding="utf-8") for path in package_path.glob("*.json")
        )

        assert package_handoff == payload
        assert smoke_summary["full_user_ready_closure"] == (
            payload["closure"]["full_user_ready_closure"]["status"]
        )
        assert "external_evidence_policy_failure" not in payload["closure"][
            "full_user_ready_closure"
        ]["blocking_reasons"]
        assert smoke_summary["validated_provider_outcomes"] == {
            provider_id: "no_data" for provider_id in FULL_LIVE_EXTERNAL_PROVIDER_IDS
        }
        assert canonical_history["canonical_history_mutated"] is False
        assert canonical_history["smoke_history_path"] == "holdings-history"
        assert str(tmp_path) not in serialized_package
        assert "file://" not in serialized_package
        assert "https://" not in serialized_package
        assert "api_key" not in serialized_package.lower()

    run_async(scenario())


def test_pre_publish_preview_tests_do_not_write_daily_package_to_repo_root(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        repo_root = Path(__file__).resolve().parents[1]
        run_id = "zz_pre_publish_package_isolation"
        repo_package = (
            repo_root
            / "data"
            / "agent_treport"
            / "live-source"
            / "daily-smoke-summaries"
            / run_id
        )
        history_dir = tmp_path / "holdings-history"
        universe_state_path = tmp_path / "universe" / "universe_state.json"
        focus_set_path = tmp_path / "focus_set.json"
        resolution_path = tmp_path / "security_resolution.json"
        approval_path = tmp_path / "approval" / "daily_approval.json"
        dest = tmp_path / "preview"
        stdout = StringIO()
        stderr = StringIO()
        _write_holdings_history(history_dir)
        _write_universe_state(universe_state_path)
        _write_focus_set(focus_set_path)
        _write_security_resolution(resolution_path)

        try:
            exit_code = await run_cli_async(
                [
                    "run-pre-publish-preview",
                    "--run-id",
                    run_id,
                    "--history-dir",
                    str(history_dir),
                    "--universe-state-path",
                    str(universe_state_path),
                    "--focus-etf-set-path",
                    str(focus_set_path),
                    "--observed-partitions",
                    "2",
                    "--security-resolution-path",
                    str(resolution_path),
                    "--dest",
                    str(dest),
                    "--approval-path",
                    str(approval_path),
                    "--model",
                    "codex",
                ],
                stdout=stdout,
                stderr=stderr,
                collection_now=lambda: datetime(2026, 5, 15, 1, 45, tzinfo=UTC),
                readiness_now=lambda: datetime(2026, 5, 15, 2, 0, tzinfo=UTC),
                model_client_factory=lambda _config: FakeModelClient([]),
            )
            package_written_to_repo_root = repo_package.exists()
        finally:
            if repo_package.exists():
                shutil.rmtree(repo_package)

        assert stderr.getvalue() == ""
        assert exit_code == 1
        assert package_written_to_repo_root is False

    run_async(scenario())


def test_pre_publish_preview_without_approval_writes_preflight_and_blocks_export(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        history_dir = tmp_path / "holdings-history"
        universe_state_path = tmp_path / "universe" / "universe_state.json"
        focus_set_path = tmp_path / "focus_set.json"
        resolution_path = tmp_path / "security_resolution.json"
        approval_path = tmp_path / "approval" / "daily_approval.json"
        dest = tmp_path / "preview"
        stdout = StringIO()
        stderr = StringIO()
        model_created = False
        _write_holdings_history(history_dir)
        _write_universe_state(universe_state_path)
        _write_focus_set(focus_set_path)
        _write_security_resolution(resolution_path)

        def fail_if_model_is_created(_config):
            nonlocal model_created
            model_created = True
            raise AssertionError("model should not be created before approval")

        exit_code = await run_cli_async(
            [
                "run-pre-publish-preview",
                "--run-id",
                "run_pre_publish_unapproved",
                "--history-dir",
                str(history_dir),
                "--universe-state-path",
                str(universe_state_path),
                "--focus-etf-set-path",
                str(focus_set_path),
                "--observed-partitions",
                "2",
                "--security-resolution-path",
                str(resolution_path),
                "--dest",
                str(dest),
                "--approval-path",
                str(approval_path),
                "--model",
                "codex",
            ],
            stdout=stdout,
            stderr=stderr,
            collection_now=lambda: datetime(2026, 5, 15, 1, 45, tzinfo=UTC),
            readiness_now=lambda: datetime(2026, 5, 15, 2, 0, tzinfo=UTC),
            model_client_factory=fail_if_model_is_created,
        )

        payload: dict[str, Any] = json.loads(stdout.getvalue())
        preflight_path = dest / "daily_operational_external_data_preflight.json"
        template_path = dest / "daily_operational_external_data_approval_template.json"
        preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
        template = json.loads(template_path.read_text(encoding="utf-8"))
        preflight_text = preflight_path.read_text(encoding="utf-8")

        assert stderr.getvalue() == ""
        assert exit_code == 1
        assert model_created is False
        assert not (dest / "external_evidence.json").exists()
        assert payload["status"] == "failed"
        assert payload["delivery_blocked"] is True
        assert payload["reason"] == "external_data_approval_required"
        assert payload["approval"]["status"] == "missing"
        assert payload["approval"]["missing_scopes"] == ["live_external_evidence"]
        approval_preflight_path = payload["references"]["artifacts"]["approval_preflight"][
            "path"
        ]
        approval_template_path = payload["references"]["artifacts"]["approval_template"][
            "path"
        ]
        assert not Path(approval_preflight_path).is_absolute()
        assert not Path(approval_template_path).is_absolute()
        assert Path(approval_preflight_path).resolve() == preflight_path.resolve()
        assert Path(approval_template_path).resolve() == template_path.resolve()
        assert preflight["schema_version"] == (
            "agent_treport.daily_operational_external_data_preflight.v1"
        )
        assert preflight["approval"]["required_scopes"] == ["live_external_evidence"]
        assert preflight["boundary"]["approved_max_target_count"] == 25
        assert preflight["boundary"]["external_evidence_provider_ids"] == (
            FULL_LIVE_EXTERNAL_PROVIDER_IDS
        )
        assert [
            item["provider_id"]
            for item in preflight["boundary"]["known_unvalidated_provider_exceptions"]
        ] == KNOWN_UNVALIDATED_EXTERNAL_PROVIDER_EXCEPTIONS
        assert preflight["boundary"]["live_source_provider_ids"] == (
            FULL_LIVE_SOURCE_PROVIDER_IDS
        )
        assert preflight["boundary"]["live_source_cohort"] == (
            FULL_LIVE_SOURCE_PROVIDER_IDS
        )
        assert preflight["disclosure"]["provider_identities"] == {
            "external_evidence": FULL_LIVE_EXTERNAL_PROVIDER_IDS,
            "known_unvalidated_external_evidence": (
                KNOWN_UNVALIDATED_EXTERNAL_PROVIDER_EXCEPTIONS
            ),
            "live_source": FULL_LIVE_SOURCE_PROVIDER_IDS,
            "model_exports": [],
        }
        assert preflight["disclosure"]["provider_exception_summary"] == [
            {
                "provider_id": "sec_edgar",
                "exception_type": "known_unvalidated_provider_exception",
                "required_for_user_ready_closure": False,
                "execution_status": "not_requested",
            }
        ]
        assert preflight["disclosure"]["approved_max_target_count"] == 25
        assert preflight["disclosure"]["live_source_cohort"] == (
            FULL_LIVE_SOURCE_PROVIDER_IDS
        )
        assert preflight["disclosure"]["focus_etf_ids"] == [
            "etf_ace_ai",
            "etf_tiger_robotics",
            "etf_sol_semis",
        ]
        assert preflight["disclosure"]["credential_expectations"] == [
            "ALPHAVANTAGE_API_KEY",
            "DART_API_KEY",
            "FINNHUB_API_KEY",
            "NAVER_CLIENT_ID",
            "NAVER_CLIENT_SECRET",
            "NEWS_API_KEY",
        ]
        assert preflight["disclosure"]["excluded_raw_fields"] == (
            preflight["boundary"]["excluded_raw_fields"]
        )
        assert "external_evidence_category_requests" in (
            preflight["disclosure"]["data_classes"]
        )
        assert "provider_outcome_summary" in preflight["disclosure"]["data_classes"]
        assert "weight_percent" not in preflight_text
        assert "https://" not in preflight_text
        assert "token=" not in preflight_text.lower()
        assert str(tmp_path) not in preflight_text
        assert template["status"] == "pending"
        assert template["required_scopes"] == ["live_external_evidence"]
        assert [
            item["provider_id"]
            for item in template["known_unvalidated_provider_exceptions"]
        ] == KNOWN_UNVALIDATED_EXTERNAL_PROVIDER_EXCEPTIONS
        assert template["boundary_fingerprint"] == preflight["approval"][
            "boundary_fingerprint"
        ]
        store = SQLiteRunStore(str(dest / "runtime.sqlite3"))
        await store.initialize()
        try:
            record = await build_trace_export_record(
                store=store,
                run_id="run_pre_publish_unapproved",
            )
        finally:
            await store.close()
        governance_summary = next(
            summary
            for summary in record.evidence_summaries
            if summary.kind == "approval_permission_boundary"
        )
        assert governance_summary.status == "approval_required"
        assert governance_summary.summary["subject"] == "pre_publish_external_data"
        assert governance_summary.summary["action"] == "live_external_evidence"
        assert governance_summary.summary["approval_statuses"] == ("requested",)
        assert governance_summary.summary["permission_decisions"] == (
            "approval_required",
        )
        assert governance_summary.inputs["required_scopes"] == (
            "live_external_evidence",
        )
        assert governance_summary.inputs["approved_scopes"] == ()

    run_async(scenario())


def test_pre_publish_preview_cached_evidence_real_model_uses_model_export_approval(
    tmp_path: Path,
    monkeypatch,
) -> None:
    async def scenario() -> None:
        history_dir = tmp_path / "holdings-history"
        universe_state_path = tmp_path / "universe" / "universe_state.json"
        focus_set_path = tmp_path / "focus_set.json"
        resolution_path = tmp_path / "security_resolution.json"
        evidence_path = tmp_path / "external_evidence.json"
        evidence_summary_path = tmp_path / "external_evidence_summary.json"
        approval_path = tmp_path / "approval" / "daily_approval.json"
        dest = tmp_path / "preview"
        _write_holdings_history(history_dir)
        _write_universe_state(universe_state_path)
        _write_focus_set(focus_set_path)
        _write_security_resolution(resolution_path)
        _write_cached_external_evidence(evidence_path, evidence_summary_path)

        monkeypatch.setattr(
            "agent_treport.cli.create_model_client",
            lambda _config: FakeModelClient(
                [
                    ModelResponse(
                        message=Message(
                            role="assistant",
                            content=(TextBlock(text="approved model commentary"),),
                        )
                    )
                ]
            ),
        )

        base_argv = [
            "run-pre-publish-preview",
            "--run-id",
            "run_pre_publish_approved_model",
            "--history-dir",
            str(history_dir),
            "--universe-state-path",
            str(universe_state_path),
            "--focus-etf-set-path",
            str(focus_set_path),
            "--observed-partitions",
            "2",
            "--security-resolution-path",
            str(resolution_path),
            "--evidence-path",
            str(evidence_path),
            "--evidence-summary-path",
            str(evidence_summary_path),
            "--dest",
            str(dest),
            "--approval-path",
            str(approval_path),
            "--model",
            "codex",
        ]
        preapproval_stdout = StringIO()
        preapproval_exit = await run_cli_async(
            base_argv,
            stdout=preapproval_stdout,
            stderr=StringIO(),
            collection_now=lambda: datetime(2026, 5, 15, 1, 45, tzinfo=UTC),
            readiness_now=lambda: datetime(2026, 5, 15, 2, 0, tzinfo=UTC),
        )
        template_path = dest / "daily_operational_external_data_approval_template.json"
        approval = json.loads(template_path.read_text(encoding="utf-8"))
        approval["status"] = "approved"
        approval_path.parent.mkdir(parents=True, exist_ok=True)
        approval_path.write_text(json.dumps(approval), encoding="utf-8")

        stdout = StringIO()
        stderr = StringIO()
        exit_code = await run_cli_async(
            base_argv,
            stdout=stdout,
            stderr=stderr,
            collection_now=lambda: datetime(2026, 5, 15, 1, 45, tzinfo=UTC),
            readiness_now=lambda: datetime(2026, 5, 15, 2, 0, tzinfo=UTC),
        )

        payload: dict[str, Any] = json.loads(stdout.getvalue())

        assert preapproval_exit == 1
        assert stderr.getvalue() == ""
        assert exit_code == 0
        assert payload["status"] == "user_ready"
        assert payload["approval"]["status"] == "approved"
        assert payload["approval"]["missing_scopes"] == []
        assert payload["approval"]["required_scopes"] == ["model_export"]
        assert "approval_preflight" in payload["references"]["artifacts"]
        assert "approval_template" in payload["references"]["artifacts"]

        store = SQLiteRunStore(str(dest / "runtime.sqlite3"))
        await store.initialize()
        try:
            record = await build_trace_export_record(
                store=store,
                run_id="run_pre_publish_approved_model",
            )
        finally:
            await store.close()
        summary_by_id = {summary.id: summary for summary in record.evidence_summaries}
        approval_summary = summary_by_id["approval.daily_external_data"]
        requested_runtime_boundary = approval_summary.summary["boundary_fingerprint"]
        governance_summary = summary_by_id[
            "governance.pre_publish_external_data.model_export."
            f"{requested_runtime_boundary}"
        ]

        assert approval_summary.kind == "approval_trace"
        assert approval_summary.status == "approved"
        assert approval_summary.summary["subject"] == "pre_publish_external_data"
        assert approval_summary.summary["action"] == "model_export"
        assert isinstance(approval_summary.summary["boundary_fingerprint"], str)
        assert (
            approval_summary.summary["approved_boundary_fingerprint"]
            == payload["approval"]["approved_boundary_fingerprint"]
        )
        assert (
            approval_summary.summary["runner_permission_status"]
            == "required_separately"
        )
        assert (
            approval_summary.summary["platform_permission_status"]
            == "not_granted_by_domain_approval"
        )
        assert approval_summary.inputs["required_scopes"] == ("model_export",)
        assert approval_summary.inputs["approved_scopes"] == ("model_export",)
        assert approval_summary.outputs["preflight_artifact_id"] == (
            "artifact_treport_daily_approval_preflight"
        )
        assert approval_summary.outputs["template_artifact_id"] == (
            "artifact_treport_daily_approval_template"
        )
        assert "raw_approval_comments" in approval_summary.source["excluded_raw_fields"]
        assert governance_summary.kind == "approval_permission_boundary"
        assert governance_summary.status == "allowed"
        assert governance_summary.summary["boundary_fingerprint"] == (
            requested_runtime_boundary
        )
        assert governance_summary.summary["approval_statuses"] == ("approved",)
        assert governance_summary.summary["permission_decisions"] == ("allowed",)
        assert governance_summary.summary["enforcement_modes"] == ("observe",)
        assert governance_summary.inputs["required_scopes"] == ("model_export",)
        assert governance_summary.inputs["approved_scopes"] == ("model_export",)
        assert governance_summary.source["event_types"] == (
            "approval_lifecycle_recorded",
            "permission_decision_recorded",
        )

    run_async(scenario())


def test_pre_publish_preview_policy_failure_is_operator_review_only_with_artifacts(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        history_dir = tmp_path / "holdings-history"
        universe_state_path = tmp_path / "universe" / "universe_state.json"
        focus_set_path = tmp_path / "focus_set.json"
        resolution_path = tmp_path / "security_resolution.json"
        dest = tmp_path / "preview"
        stdout = StringIO()
        stderr = StringIO()
        calls: list[dict[str, object]] = []
        _write_holdings_history(history_dir)
        _write_universe_state(universe_state_path)
        _write_focus_set(focus_set_path)
        _write_security_resolution(resolution_path)

        exit_code = await run_cli_async(
            [
                "run-pre-publish-preview",
                "--run-id",
                "run_pre_publish_policy_failure",
                "--history-dir",
                str(history_dir),
                "--universe-state-path",
                str(universe_state_path),
                "--focus-etf-set-path",
                str(focus_set_path),
                "--observed-partitions",
                "2",
                "--security-resolution-path",
                str(resolution_path),
                "--dest",
                str(dest),
                "--model",
                "codex",
            ],
            stdout=stdout,
            stderr=stderr,
            collection_now=lambda: datetime(2026, 5, 15, 1, 45, tzinfo=UTC),
            readiness_now=lambda: datetime(2026, 5, 15, 2, 0, tzinfo=UTC),
            external_evidence_provider_overrides={
                **_full_live_no_data_providers(calls),
                "naver": _failing_provider(
                    provider_id="naver",
                    category="news",
                    status="credential_required",
                    calls=calls,
                ),
            },
            model_client_factory=lambda _config: FakeModelClient(
                [
                    ModelResponse(
                        message=Message(
                            role="assistant",
                            content=(TextBlock(text="policy failure preview commentary"),),
                        )
                    )
                ]
            ),
        )

        payload = json.loads(stdout.getvalue())
        artifacts = payload["references"]["artifacts"]
        summary = json.loads(
            (dest / "external_evidence_summary.json").read_text(encoding="utf-8")
        )

        assert stderr.getvalue() == ""
        assert exit_code == 0
        _assert_pre_publish_payload_path_safe(payload)
        assert payload["status"] == "operator_review_only"
        assert payload["delivery_blocked"] is True
        assert payload["reason"] == "external_evidence_policy_failure"
        assert payload["external_evidence"]["policy_failure"]["error_code"] == (
            "credential_required"
        )
        assert payload["external_evidence"]["required_provider_outcomes"]["naver"] == (
            "credential_required"
        )
        assert payload["external_evidence"]["required_provider_failures"] == [
            {
                "provider_id": "naver",
                "category": "news",
                "status": "credential_required",
                "error_code": "credential_required",
            }
        ]
        assert summary["policy_failure"]["provider_id"] == "naver"
        assert "user_ready" not in payload
        assert payload["operator_review_only"]["delivery_blocked"] is True
        assert payload["operator_review_only"]["reason"] == (
            "external_evidence_policy_failure"
        )
        assert payload["preview"]["telegram_message"]["text"] == Path(
            artifacts["telegram_alert"]["path"]
        ).read_text(encoding="utf-8")
        assert payload["closure"]["full_live_pre_publish_artifact_closure"][
            "status"
        ] == "met"
        assert payload["closure"]["full_user_ready_closure"]["status"] == "blocked"
        assert {
            "canonical_payload",
            "markdown_report",
            "html_report",
            "telegram_alert",
            "quality_report",
            "readiness",
            "external_evidence",
            "external_evidence_summary",
        }.issubset(artifacts)

    run_async(scenario())


def test_pre_publish_preview_sec_edgar_policy_failure_is_disclosed_but_non_blocking(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        history_dir = tmp_path / "holdings-history"
        universe_state_path = tmp_path / "universe" / "universe_state.json"
        focus_set_path = tmp_path / "focus_set.json"
        resolution_path = tmp_path / "security_resolution.json"
        evidence_path = tmp_path / "external_evidence.json"
        evidence_summary_path = tmp_path / "external_evidence_summary.json"
        dest = tmp_path / "preview"
        stdout = StringIO()
        stderr = StringIO()
        _write_holdings_history(history_dir)
        _write_universe_state(universe_state_path)
        _write_focus_set(focus_set_path)
        _write_security_resolution(resolution_path)
        _write_cached_external_evidence(evidence_path, evidence_summary_path)
        summary = json.loads(evidence_summary_path.read_text(encoding="utf-8"))
        summary["provider_outcomes"] = [
            _provider_outcome_json(
                provider_id="finnhub",
                category="financial",
                status="no_data",
            ),
            _provider_outcome_json(
                provider_id="yfinance",
                category="financial",
                status="no_data",
            ),
            _provider_outcome_json(
                provider_id="dart",
                category="disclosure",
                status="no_data",
            ),
            _provider_outcome_json(
                provider_id="alpha_vantage",
                category="news",
                status="no_data",
            ),
            _provider_outcome_json(
                provider_id="newsapi",
                category="news",
                status="no_data",
            ),
            _provider_outcome_json(
                provider_id="naver",
                category="news",
                status="no_data",
            ),
            _provider_outcome_json(
                provider_id="sec_edgar",
                category="disclosure",
                status="rate_limited_exhausted",
                error_code="rate_limited_exhausted",
            ),
        ]
        summary["policy_failure"] = {
            "provider_id": "sec_edgar",
            "category": "disclosure",
            "error_code": "rate_limited_exhausted",
            "safe_message": "SEC EDGAR remained unvalidated in a prior smoke.",
        }
        _write_json(evidence_summary_path, summary)

        exit_code = await run_cli_async(
            [
                "run-pre-publish-preview",
                "--run-id",
                "run_pre_publish_sec_exception_cached",
                "--history-dir",
                str(history_dir),
                "--universe-state-path",
                str(universe_state_path),
                "--focus-etf-set-path",
                str(focus_set_path),
                "--observed-partitions",
                "2",
                "--security-resolution-path",
                str(resolution_path),
                "--evidence-path",
                str(evidence_path),
                "--evidence-summary-path",
                str(evidence_summary_path),
                "--dest",
                str(dest),
                "--model",
                "codex",
            ],
            stdout=stdout,
            stderr=stderr,
            collection_now=lambda: datetime(2026, 5, 15, 1, 45, tzinfo=UTC),
            readiness_now=lambda: datetime(2026, 5, 15, 2, 0, tzinfo=UTC),
            model_client_factory=lambda _config: FakeModelClient(
                [
                    ModelResponse(
                        message=Message(
                            role="assistant",
                            content=(TextBlock(text="sec exception commentary"),),
                        )
                    )
                ]
            ),
        )

        payload = json.loads(stdout.getvalue())

        assert stderr.getvalue() == ""
        assert exit_code == 0
        assert payload["status"] == "user_ready"
        assert "external_evidence_policy_failure" not in payload["closure"][
            "full_user_ready_closure"
        ]["blocking_reasons"]
        assert payload["external_evidence"]["policy_failure"]["provider_id"] == (
            "sec_edgar"
        )
        assert payload["external_evidence"]["required_provider_failures"] == []
        assert payload["external_evidence"]["provider_exception_outcomes"] == {
            "sec_edgar": "rate_limited_exhausted"
        }
        assert [
            item["provider_id"]
            for item in payload["external_evidence"][
                "known_unvalidated_provider_exceptions"
            ]
        ] == KNOWN_UNVALIDATED_EXTERNAL_PROVIDER_EXCEPTIONS

    run_async(scenario())


def test_pre_publish_preview_external_evidence_not_run_is_operator_review_only(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        history_dir = tmp_path / "holdings-history"
        universe_state_path = tmp_path / "universe" / "universe_state.json"
        focus_set_path = tmp_path / "focus_set.json"
        resolution_path = tmp_path / "security_resolution.json"
        evidence_path = tmp_path / "external_evidence.json"
        evidence_summary_path = tmp_path / "external_evidence_summary.json"
        dest = tmp_path / "preview"
        stdout = StringIO()
        stderr = StringIO()
        _write_holdings_history(history_dir)
        _write_universe_state(universe_state_path)
        _write_focus_set(focus_set_path)
        _write_security_resolution(resolution_path)
        _write_json(evidence_path, [])
        _write_not_run_external_evidence_summary(evidence_summary_path)

        exit_code = await run_cli_async(
            [
                "run-pre-publish-preview",
                "--run-id",
                "run_pre_publish_evidence_not_run",
                "--history-dir",
                str(history_dir),
                "--universe-state-path",
                str(universe_state_path),
                "--focus-etf-set-path",
                str(focus_set_path),
                "--observed-partitions",
                "2",
                "--security-resolution-path",
                str(resolution_path),
                "--evidence-path",
                str(evidence_path),
                "--evidence-summary-path",
                str(evidence_summary_path),
                "--dest",
                str(dest),
                "--model",
                "codex",
            ],
            stdout=stdout,
            stderr=stderr,
            collection_now=lambda: datetime(2026, 5, 15, 1, 45, tzinfo=UTC),
            readiness_now=lambda: datetime(2026, 5, 15, 2, 0, tzinfo=UTC),
            model_client_factory=lambda _config: FakeModelClient(
                [
                    ModelResponse(
                        message=Message(
                            role="assistant",
                            content=(TextBlock(text="not-run preview commentary"),),
                        )
                    )
                ]
            ),
        )

        payload = json.loads(stdout.getvalue())
        artifacts = payload["references"]["artifacts"]

        assert stderr.getvalue() == ""
        assert exit_code == 0
        assert payload["status"] == "operator_review_only"
        assert payload["delivery_blocked"] is True
        assert payload["reason"] == "external_evidence_not_run"
        assert payload["external_evidence"]["status"] == "not_run"
        assert "user_ready" not in payload
        assert payload["operator_review_only"]["reason"] == "external_evidence_not_run"
        assert {"canonical_payload", "telegram_alert", "external_evidence_summary"}.issubset(
            artifacts
        )

    run_async(scenario())


def test_pre_publish_preview_requires_all_evidence_categories_for_user_ready(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        history_dir = tmp_path / "holdings-history"
        universe_state_path = tmp_path / "universe" / "universe_state.json"
        focus_set_path = tmp_path / "focus_set.json"
        resolution_path = tmp_path / "security_resolution.json"
        evidence_path = tmp_path / "external_evidence.json"
        evidence_summary_path = tmp_path / "external_evidence_summary.json"
        dest = tmp_path / "preview"
        stdout = StringIO()
        stderr = StringIO()
        _write_holdings_history(history_dir)
        _write_universe_state(universe_state_path)
        _write_focus_set(focus_set_path)
        _write_security_resolution(resolution_path)
        _write_cached_external_evidence(evidence_path, evidence_summary_path)
        summary = json.loads(evidence_summary_path.read_text(encoding="utf-8"))
        summary["provider_outcomes"] = [
            outcome
            for outcome in summary["provider_outcomes"]
            if outcome["category"] != "disclosure"
        ]
        summary["category_coverage"]["disclosure"] = {
            "coverage_ratio": None,
            "status": "not_run",
            "notes": ["disclosure:not_run"],
        }
        _write_json(evidence_summary_path, summary)

        exit_code = await run_cli_async(
            [
                "run-pre-publish-preview",
                "--run-id",
                "run_pre_publish_missing_category",
                "--history-dir",
                str(history_dir),
                "--universe-state-path",
                str(universe_state_path),
                "--focus-etf-set-path",
                str(focus_set_path),
                "--observed-partitions",
                "2",
                "--security-resolution-path",
                str(resolution_path),
                "--evidence-path",
                str(evidence_path),
                "--evidence-summary-path",
                str(evidence_summary_path),
                "--dest",
                str(dest),
                "--model",
                "codex",
            ],
            stdout=stdout,
            stderr=stderr,
            collection_now=lambda: datetime(2026, 5, 15, 1, 45, tzinfo=UTC),
            readiness_now=lambda: datetime(2026, 5, 15, 2, 0, tzinfo=UTC),
            model_client_factory=lambda _config: FakeModelClient(
                [
                    ModelResponse(
                        message=Message(
                            role="assistant",
                            content=(TextBlock(text="missing category commentary"),),
                        )
                    )
                ]
            ),
        )

        payload = json.loads(stdout.getvalue())

        assert stderr.getvalue() == ""
        assert exit_code == 0
        assert payload["status"] == "operator_review_only"
        assert payload["delivery_blocked"] is True
        assert payload["reason"] == "external_evidence_not_run"
        assert payload["external_evidence"]["category_coverage"]["disclosure"][
            "status"
        ] == "not_run"

    run_async(scenario())


def test_pre_publish_preview_rejects_cached_evidence_for_non_target_claim_scope(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        history_dir = tmp_path / "holdings-history"
        universe_state_path = tmp_path / "universe" / "universe_state.json"
        focus_set_path = tmp_path / "focus_set.json"
        resolution_path = tmp_path / "security_resolution.json"
        evidence_path = tmp_path / "external_evidence.json"
        evidence_summary_path = tmp_path / "external_evidence_summary.json"
        dest = tmp_path / "preview"
        stdout = StringIO()
        stderr = StringIO()
        _write_holdings_history(history_dir)
        _write_universe_state(universe_state_path)
        _write_focus_set(focus_set_path)
        _write_security_resolution(resolution_path)
        _write_cached_external_evidence(
            evidence_path,
            evidence_summary_path,
            claim_scope="signal:security:sec_not_on_board:weight_increase",
        )

        exit_code = await run_cli_async(
            [
                "run-pre-publish-preview",
                "--run-id",
                "run_pre_publish_bad_cached_claim",
                "--history-dir",
                str(history_dir),
                "--universe-state-path",
                str(universe_state_path),
                "--focus-etf-set-path",
                str(focus_set_path),
                "--observed-partitions",
                "2",
                "--security-resolution-path",
                str(resolution_path),
                "--evidence-path",
                str(evidence_path),
                "--evidence-summary-path",
                str(evidence_summary_path),
                "--dest",
                str(dest),
                "--model",
                "codex",
            ],
            stdout=stdout,
            stderr=stderr,
            collection_now=lambda: datetime(2026, 5, 15, 1, 45, tzinfo=UTC),
            readiness_now=lambda: datetime(2026, 5, 15, 2, 0, tzinfo=UTC),
            model_client_factory=lambda _config: FakeModelClient(
                [
                    ModelResponse(
                        message=Message(
                            role="assistant",
                            content=(TextBlock(text="should not be called"),),
                        )
                    )
                ]
            ),
        )

        assert exit_code == 2
        assert stdout.getvalue() == ""
        assert "cached external evidence claim_scope does not match" in stderr.getvalue()

    run_async(scenario())


def test_pre_publish_preview_readiness_hold_outputs_review_only_without_override(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        history_dir = tmp_path / "holdings-history"
        universe_state_path = tmp_path / "universe" / "universe_state.json"
        focus_set_path = tmp_path / "focus_set.json"
        resolution_path = tmp_path / "security_resolution.json"
        evidence_path = tmp_path / "external_evidence.json"
        evidence_summary_path = tmp_path / "external_evidence_summary.json"
        dest = tmp_path / "preview"
        stdout = StringIO()
        stderr = StringIO()
        _write_holdings_history(history_dir, include_sol_previous=False)
        _write_universe_state(universe_state_path)
        _write_focus_set(focus_set_path)
        _write_security_resolution(resolution_path)
        _write_cached_external_evidence(evidence_path, evidence_summary_path)

        exit_code = await run_cli_async(
            [
                "run-pre-publish-preview",
                "--run-id",
                "run_pre_publish_hold_review_only",
                "--history-dir",
                str(history_dir),
                "--universe-state-path",
                str(universe_state_path),
                "--focus-etf-set-path",
                str(focus_set_path),
                "--observed-partitions",
                "2",
                "--security-resolution-path",
                str(resolution_path),
                "--evidence-path",
                str(evidence_path),
                "--evidence-summary-path",
                str(evidence_summary_path),
                "--dest",
                str(dest),
                "--model",
                "codex",
            ],
            stdout=stdout,
            stderr=stderr,
            collection_now=lambda: datetime(2026, 5, 15, 1, 45, tzinfo=UTC),
            readiness_now=lambda: datetime(2026, 5, 15, 2, 0, tzinfo=UTC),
            model_client_factory=lambda _config: FakeModelClient(
                [
                    ModelResponse(
                        message=Message(
                            role="assistant",
                            content=(TextBlock(text="hold review-only commentary"),),
                        )
                    )
                ]
            ),
        )

        payload = json.loads(stdout.getvalue())

        assert stderr.getvalue() == ""
        assert exit_code == 0
        assert payload["status"] == "operator_review_only"
        assert payload["delivery_blocked"] is True
        assert payload["reason"] == "readiness_hold"
        assert payload["readiness"]["status"] == "hold"
        assert "user_ready" not in payload
        assert payload["operator_review_only"]["reason"] == "readiness_hold"
        assert {"canonical_payload", "telegram_alert", "readiness"}.issubset(
            payload["references"]["artifacts"]
        )
        assert "telegram_message" in payload["preview"]
        assert payload["closure"]["full_live_pre_publish_artifact_closure"][
            "status"
        ] == "met"
        assert payload["closure"]["full_user_ready_closure"]["status"] == "blocked"
        assert (dest / "pre_publish_handoff.json").is_file()
        assert "operational readiness status hold" not in stderr.getvalue()

    run_async(scenario())


def test_pre_publish_preview_readiness_hold_with_override_is_review_only(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        history_dir = tmp_path / "holdings-history"
        universe_state_path = tmp_path / "universe" / "universe_state.json"
        focus_set_path = tmp_path / "focus_set.json"
        resolution_path = tmp_path / "security_resolution.json"
        evidence_path = tmp_path / "external_evidence.json"
        evidence_summary_path = tmp_path / "external_evidence_summary.json"
        dest = tmp_path / "preview"
        stdout = StringIO()
        stderr = StringIO()
        _write_holdings_history(history_dir, include_sol_previous=False)
        _write_universe_state(universe_state_path)
        _write_focus_set(focus_set_path)
        _write_security_resolution(resolution_path)
        _write_cached_external_evidence(evidence_path, evidence_summary_path)

        exit_code = await run_cli_async(
            [
                "run-pre-publish-preview",
                "--run-id",
                "run_pre_publish_hold_with_override",
                "--history-dir",
                str(history_dir),
                "--universe-state-path",
                str(universe_state_path),
                "--focus-etf-set-path",
                str(focus_set_path),
                "--observed-partitions",
                "2",
                "--security-resolution-path",
                str(resolution_path),
                "--evidence-path",
                str(evidence_path),
                "--evidence-summary-path",
                str(evidence_summary_path),
                "--allow-operator-review-output",
                "--dest",
                str(dest),
                "--model",
                "codex",
            ],
            stdout=stdout,
            stderr=stderr,
            collection_now=lambda: datetime(2026, 5, 15, 1, 45, tzinfo=UTC),
            readiness_now=lambda: datetime(2026, 5, 15, 2, 0, tzinfo=UTC),
            model_client_factory=lambda _config: FakeModelClient(
                [
                    ModelResponse(
                        message=Message(
                            role="assistant",
                            content=(TextBlock(text="hold review-only commentary"),),
                        )
                    )
                ]
            ),
        )

        payload = json.loads(stdout.getvalue())

        assert stderr.getvalue() == ""
        assert exit_code == 0
        assert payload["status"] == "operator_review_only"
        assert payload["delivery_blocked"] is True
        assert payload["reason"] == "readiness_hold"
        assert payload["operator_review_only"]["reason"] == "readiness_hold"
        assert payload["readiness"]["status"] == "hold"
        assert "user_ready" not in payload
        assert {"canonical_payload", "telegram_alert", "readiness"}.issubset(
            payload["references"]["artifacts"]
        )

    run_async(scenario())


def _write_not_run_external_evidence_summary(path: Path) -> None:
    _write_json(
        path,
        {
            "schema_version": "agent_treport.external_evidence.summary.v1",
            "status": "not_run",
            "target_selection": {
                "selected_targets": [],
                "excluded_targets": [],
                "max_targets": 0,
            },
            "provider_outcomes": [],
            "category_coverage": {
                "financial": {
                    "coverage_ratio": None,
                    "status": "not_run",
                    "notes": ["not_run"],
                },
                "disclosure": {
                    "coverage_ratio": None,
                    "status": "not_run",
                    "notes": ["not_run"],
                },
                "news": {
                    "coverage_ratio": None,
                    "status": "not_run",
                    "notes": ["not_run"],
                },
            },
            "dedupe": {"deduped_count": 0},
            "policy_failure": None,
            "evidence_path": None,
            "cooldown_path": None,
        },
    )


def _full_live_no_data_providers(
    calls: list[dict[str, object]],
) -> dict[str, ExternalEvidenceProvider]:
    return {
        "finnhub": _no_data_provider(
            provider_id="finnhub",
            category="financial",
            calls=calls,
        ),
        "yfinance": _no_data_provider(
            provider_id="yfinance",
            category="financial",
            calls=calls,
        ),
        "dart": _no_data_provider(
            provider_id="dart",
            category="disclosure",
            calls=calls,
        ),
        "sec_edgar": _no_data_provider(
            provider_id="sec_edgar",
            category="disclosure",
            calls=calls,
        ),
        "alpha_vantage": _no_data_provider(
            provider_id="alpha_vantage",
            category="news",
            calls=calls,
        ),
        "newsapi": _no_data_provider(
            provider_id="newsapi",
            category="news",
            calls=calls,
        ),
        "naver": _no_data_provider(
            provider_id="naver",
            category="news",
            calls=calls,
        ),
    }


def _no_data_provider(
    *,
    provider_id: str,
    category: EvidenceCategory,
    calls: list[dict[str, object]],
) -> ExternalEvidenceProvider:
    local_provider_id = provider_id
    local_category = category

    class Provider:
        provider_id: str = local_provider_id
        category: EvidenceCategory = local_category

        def collect(
            self,
            targets: Sequence[ExternalEvidenceTarget],
            *,
            live: bool,
            request_context: ExternalEvidenceRequestContext,
        ) -> tuple[Sequence[ExternalEvidenceCandidate], ExternalEvidenceProviderOutcome]:
            _ = request_context
            calls.append(
                {
                    "provider_id": local_provider_id,
                    "category": local_category,
                    "live": live,
                    "target_tickers": [target.ticker for target in targets],
                }
            )
            return (), ExternalEvidenceProviderOutcome(
                provider_id=local_provider_id,
                category=cast(EvidenceCategory, local_category),
                status="no_data",
                error_code=None,
                retryable=False,
                attempt_count=1,
                stopped_reason=None,
                target_tickers=tuple(target.ticker for target in targets),
                safe_message=f"{local_provider_id} returned no fixture evidence.",
            )

    return Provider()


def _provider_outcome_json(
    *,
    provider_id: str,
    category: EvidenceCategory,
    status: ProviderOutcomeStatus,
    error_code: str | None = None,
) -> dict[str, object]:
    return {
        "provider_id": provider_id,
        "category": category,
        "status": status,
        "error_code": error_code,
        "retryable": False,
        "attempt_count": 1,
        "stopped_reason": "fixture_policy_failure" if error_code else None,
        "target_tickers": ["NVDA"],
        "safe_message": f"{provider_id} fixture outcome.",
        "deduped_count": 0,
    }


def _assert_pre_publish_payload_path_safe(payload: dict[str, Any]) -> None:
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "file://" not in serialized

    for key, value in _walk_json_values(payload):
        if key in {"path", "sqlite_path", "artifact_root"} and isinstance(value, str):
            assert not Path(value).is_absolute(), value


def _walk_json_values(value: object) -> list[tuple[str, object]]:
    found: list[tuple[str, object]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            found.append((str(key), item))
            found.extend(_walk_json_values(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_walk_json_values(item))
    return found


def _failing_provider(
    *,
    provider_id: str,
    category: EvidenceCategory,
    status: ProviderOutcomeStatus,
    calls: list[dict[str, object]],
) -> ExternalEvidenceProvider:
    local_provider_id = provider_id
    local_category = category
    local_status = status

    class Provider:
        provider_id: str = local_provider_id
        category: EvidenceCategory = local_category

        def collect(
            self,
            targets: Sequence[ExternalEvidenceTarget],
            *,
            live: bool,
            request_context: ExternalEvidenceRequestContext,
        ) -> tuple[Sequence[ExternalEvidenceCandidate], ExternalEvidenceProviderOutcome]:
            _ = request_context
            calls.append(
                {
                    "provider_id": local_provider_id,
                    "category": local_category,
                    "live": live,
                    "target_tickers": [target.ticker for target in targets],
                }
            )
            return (), ExternalEvidenceProviderOutcome(
                provider_id=local_provider_id,
                category=cast(EvidenceCategory, local_category),
                status=cast(ProviderOutcomeStatus, local_status),
                error_code=local_status,
                retryable=False,
                attempt_count=1,
                stopped_reason="fixture_policy_failure",
                target_tickers=tuple(target.ticker for target in targets),
                safe_message=f"{local_provider_id} fixture policy failure.",
            )

    return Provider()
