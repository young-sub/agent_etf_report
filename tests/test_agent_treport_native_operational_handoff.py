from __future__ import annotations

import asyncio
import json
import shlex
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from typing import Any

import pytest
from agent_pack.models import Message, ModelResponse, TextBlock
from agent_pack.models_client import FakeModelClient

from agent_treport.cli import build_parser, run_cli_async


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
    ticker: str | None = None,
) -> dict[str, object]:
    return {
        "etf_id": etf_id,
        "etf_name": etf_name,
        "brand_id": brand_id,
        "source_provider_id": provider_id,
        "as_of_date": observed_date,
        "security_id": security_id,
        "ticker": ticker,
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


def _write_holdings_history(history_dir: Path, *, source_tickers: bool = False) -> None:
    tickers = {
        "sec_nvda": "NVDA",
        "sec_msft": "MSFT",
        "sec_avgo": "AVGO",
        "sec_kodex_gap": "KODEXGAP",
    }

    def ticker_for(security_id: str) -> str | None:
        return tickers[security_id] if source_tickers else None

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
            ticker=ticker_for("sec_nvda"),
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
            ticker=ticker_for("sec_msft"),
        ),
        _history_row(
            etf_id="etf_sol_semis",
            etf_name="SOL Semiconductors Active ETF",
            brand_id="brand_sol",
            provider_id="sol",
            observed_date=current_date,
            security_id="sec_avgo",
            name="Broadcom Inc.",
            weight_percent=5.5,
            ticker=ticker_for("sec_avgo"),
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
            ticker=ticker_for("sec_kodex_gap"),
        ),
    ]
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
            ticker=ticker_for("sec_nvda"),
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
            ticker=ticker_for("sec_msft"),
        ),
        _history_row(
            etf_id="etf_sol_semis",
            etf_name="SOL Semiconductors Active ETF",
            brand_id="brand_sol",
            provider_id="sol",
            observed_date=previous_date,
            security_id="sec_avgo",
            name="Broadcom Inc.",
            weight_percent=4.0,
            ticker=ticker_for("sec_avgo"),
        ),
    ]
    _write_jsonl(
        history_dir / "holdings_history.json.parts" / f"{current_date}.jsonl",
        current_rows,
    )
    _write_jsonl(
        history_dir / "holdings_history.json.parts" / f"{previous_date}.jsonl",
        previous_rows,
    )
    _write_json(
        history_dir / "holdings_history.json",
        {
            "schema_version": "agent_treport.native_holdings.history.v1",
            "storage_format": "native_history_partitioned_jsonl_v1",
            "updated_at": "2026-05-15T01:30:00+00:00",
            "dates": [current_date, previous_date],
            "record_count": len(current_rows) + len(previous_rows),
            "snapshot_count": 7,
            "partitions": {
                current_date: {
                    "file": f"holdings_history.json.parts/{current_date}.jsonl",
                    "record_count": len(current_rows),
                    "snapshot_count": 4,
                },
                previous_date: {
                    "file": f"holdings_history.json.parts/{previous_date}.jsonl",
                    "record_count": len(previous_rows),
                    "snapshot_count": 3,
                },
            },
        },
    )


def _write_source_acquisition_success(
    history_dir: Path,
    *,
    etf_id: str = "etf_tiger_robotics",
) -> None:
    _write_json(
        history_dir / "source_acquisition_summary.json",
        {
            "schema_version": "agent_treport.source_acquisition.summary.v1",
            "source_provider_id": "tiger",
            "run_outcome": "succeeded",
            "updated_at": "2026-05-15T01:35:00+00:00",
            "history_store": {"manifest_path": "holdings_history.json"},
            "requested_dates": ["2026-05-15"],
            "observed_dates": ["2026-05-15"],
            "target_outcomes": [
                {
                    "source_provider_id": "tiger",
                    "brand_id": "brand_tiger",
                    "etf_id": etf_id,
                    "scope": "holdings_snapshot",
                    "requested_date": "2026-05-15",
                    "observed_date": "2026-05-15",
                    "date_alignment": {
                        "requested_date": "2026-05-15",
                        "observed_date": "2026-05-15",
                        "status": "matched",
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
            ],
            "row_count": 1,
            "written_snapshot_count": 1,
            "provider_rollout_status": "supported",
            "warnings": [],
            "aggregate_counts": {
                "target_count": 1,
                "fetched": 1,
                "skipped_existing": 0,
                "failed": 0,
                "rate_limited": 0,
                "unsupported": 0,
                "written_snapshot_count": 1,
                "row_count": 1,
            },
        },
    )


def _write_aggregated_source_acquisition_success(history_dir: Path) -> None:
    targets = [
        _source_acquisition_target(
            source_provider_id="ace",
            brand_id="brand_ace",
            etf_id="etf_ace_ai",
            outcome="skipped_existing",
        ),
        _source_acquisition_target(
            source_provider_id="tiger",
            brand_id="brand_tiger",
            etf_id="etf_tiger_robotics",
            outcome="fetched",
        ),
        _source_acquisition_target(
            source_provider_id="sol",
            brand_id="brand_sol",
            etf_id="etf_sol_semis",
            outcome="skipped_existing",
        ),
    ]
    _write_json(
        history_dir / "source_acquisition_summary.json",
        {
            "schema_version": "agent_treport.source_acquisition.summary.v1",
            "source_provider_id": "multiple",
            "source_provider_ids": ["ace", "tiger", "sol"],
            "run_outcome": "succeeded",
            "updated_at": "2026-05-15T01:35:00+00:00",
            "history_store": {"manifest_path": "holdings_history.json"},
            "requested_dates": ["2026-05-15"],
            "observed_dates": ["2026-05-15"],
            "target_outcomes": targets,
            "row_count": 3,
            "written_snapshot_count": 1,
            "provider_rollout_status": "supported",
            "warnings": [],
            "aggregate_counts": {
                "target_count": len(targets),
                "fetched": 1,
                "skipped_existing": 2,
                "failed": 0,
                "rate_limited": 0,
                "unsupported": 0,
                "written_snapshot_count": 1,
                "row_count": 3,
            },
        },
    )


def _source_acquisition_target(
    *,
    source_provider_id: str,
    brand_id: str,
    etf_id: str,
    outcome: str,
) -> dict[str, object]:
    return {
        "source_provider_id": source_provider_id,
        "brand_id": brand_id,
        "etf_id": etf_id,
        "scope": "holdings_snapshot",
        "requested_date": "2026-05-15",
        "observed_date": "2026-05-15",
        "date_alignment": {
            "requested_date": "2026-05-15",
            "observed_date": "2026-05-15",
            "status": "matched",
        },
        "latest_upload_freshness": {
            "status": "fresh_latest",
            "observed_date": "2026-05-15",
            "latest_acceptable_observed_date": "2026-05-14",
        },
        "outcome": outcome,
        "row_count": 1,
        "reason_code": None,
        "retry_attempt_count": 0,
    }


def _write_security_resolution(path: Path, *, security_ids: list[str] | None = None) -> None:
    tickers = {
        "sec_nvda": "NVDA",
        "sec_msft": "MSFT",
        "sec_avgo": "AVGO",
        "sec_kodex_gap": "KODEXGAP",
    }
    selected_ids = security_ids or list(tickers)
    _write_json(
        path,
        {
            "schema_version": "agent_treport.security_resolution_export.v1",
            "mappings": [
                {
                    "security_id": security_id,
                    "ticker": tickers[security_id],
                    "name": f"{tickers[security_id]} reviewed",
                    "exchange": "NASDAQ",
                    "security_classification": "ticker_candidate",
                }
                for security_id in selected_ids
            ],
            "exclusions": [],
        },
    )


def _write_focus_set(path: Path) -> None:
    _write_json(
        path,
        {
            "schema_version": "agent_treport.focus_etf_set.v1",
            "focus_etf_ids": ["etf_ace_ai", "etf_tiger_robotics", "etf_sol_semis"],
        },
    )


def _write_external_evidence(
    evidence_path: Path,
    summary_path: Path,
    *,
    partial_failure: bool = False,
    unsafe_title: bool = False,
) -> None:
    title = "NVIDIA announces data center supply update"
    if unsafe_title:
        title = "BUY rating with a target price of 500"
    _write_json(
        evidence_path,
        [] if partial_failure else [
            {
                "evidence_id": "ev_nvda_news",
                "ticker": "NVDA",
                "type": "news",
                "source": "fixture_news",
                "title": title,
                "published_at": "2026-05-15T00:00:00+00:00",
                "url": None,
                "stance": "supporting",
                "strength": "moderate",
                "claim_scope": (
                    "signal:security:provider=ace|security=sec_nvda:weight_increase"
                ),
                "evidence_role": "interpretation_support",
                "relevance": "high",
                "novelty": "new",
                "interpretation_basis": "Fixture evidence is scoped to the exact claim.",
                "observed_direction": "increase",
            }
        ],
    )
    provider_status = "failed" if partial_failure else "success"
    news_notes: list[str] = []
    if partial_failure:
        news_notes.append("news provider unavailable")
    _write_json(
        summary_path,
        {
            "schema_version": "agent_treport.external_evidence.summary.v1",
            "target_selection": {
                "selected_targets": [
                    {
                        "ticker": "NVDA",
                        "claim_scope": (
                            "signal:security:provider=ace|security=sec_nvda"
                            ":weight_increase"
                        ),
                    }
                ],
                "excluded_targets": [],
                "max_targets": 2,
            },
            "provider_outcomes": [
                {
                    "provider_id": "fixture_news",
                    "category": "news",
                    "status": provider_status,
                    "error_code": "provider_unavailable" if partial_failure else None,
                    "retryable": False,
                    "attempt_count": 1,
                    "stopped_reason": None,
                    "target_tickers": ["NVDA"],
                    "safe_message": (
                        "Fixture news evidence unavailable."
                        if partial_failure
                        else "Fixture news evidence collected."
                    ),
                    "deduped_count": 0,
                }
            ],
            "category_coverage": {
                "financial": {"coverage_ratio": 1.0, "notes": []},
                "disclosure": {"coverage_ratio": 1.0, "notes": []},
                "news": {
                    "coverage_ratio": 0.0 if partial_failure else 1.0,
                    "notes": news_notes,
                },
            },
            "dedupe": {
                "deduped_count": 0,
                "per_category_counts": {"news": 0 if partial_failure else 1},
            },
            "policy_failure": None,
            "evidence_path": "external_evidence.json",
            "cooldown_path": None,
        },
    )


def test_native_operational_handoff_acceptance_mode_fails_without_source_holdings_evidence(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        history_dir = tmp_path / "holdings-history"
        universe_state_path = tmp_path / "universe" / "universe_state.json"
        focus_set_path = tmp_path / "focus_set.json"
        resolution_path = tmp_path / "security_resolution.json"
        evidence_path = tmp_path / "external_evidence.json"
        evidence_summary_path = tmp_path / "external_evidence_summary.json"
        dest = tmp_path / "handoff"
        _write_holdings_history(history_dir)
        _write_universe_state(universe_state_path)
        _write_focus_set(focus_set_path)
        _write_security_resolution(resolution_path)
        _write_external_evidence(evidence_path, evidence_summary_path)

        exit_code, payload, stderr = await _run_native_handoff(
            history_dir=history_dir,
            universe_state_path=universe_state_path,
            focus_set_path=focus_set_path,
            dest=dest,
            security_resolution_path=resolution_path,
            evidence_path=evidence_path,
            evidence_summary_path=evidence_summary_path,
            require_verified_operational_flow_acceptance=True,
        )

        assert stderr == ""
        assert exit_code == 1
        assert payload["status"] == "user_ready"
        assert payload["verified_operational_flow_acceptance"] == {
            "status": "not_met",
            "unmet_criteria": ["bounded_live_source_holdings"],
        }

    run_async(scenario())


def test_native_operational_handoff_acceptance_mode_requires_source_target_in_export(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        history_dir = tmp_path / "holdings-history"
        universe_state_path = tmp_path / "universe" / "universe_state.json"
        focus_set_path = tmp_path / "focus_set.json"
        resolution_path = tmp_path / "security_resolution.json"
        evidence_path = tmp_path / "external_evidence.json"
        evidence_summary_path = tmp_path / "external_evidence_summary.json"
        dest = tmp_path / "handoff"
        _write_holdings_history(history_dir)
        _write_source_acquisition_success(history_dir, etf_id="etf_not_exported")
        _write_universe_state(universe_state_path)
        _write_focus_set(focus_set_path)
        _write_security_resolution(resolution_path)
        _write_external_evidence(evidence_path, evidence_summary_path)

        exit_code, payload, stderr = await _run_native_handoff(
            history_dir=history_dir,
            universe_state_path=universe_state_path,
            focus_set_path=focus_set_path,
            dest=dest,
            security_resolution_path=resolution_path,
            evidence_path=evidence_path,
            evidence_summary_path=evidence_summary_path,
            require_verified_operational_flow_acceptance=True,
        )

        assert stderr == ""
        assert exit_code == 1
        assert payload["status"] == "user_ready"
        assert payload["verified_operational_flow_acceptance"] == {
            "status": "not_met",
            "unmet_criteria": ["bounded_live_source_holdings"],
        }

    run_async(scenario())


async def _run_native_handoff(
    *,
    history_dir: Path,
    universe_state_path: Path,
    focus_set_path: Path,
    dest: Path,
    security_resolution_path: Path | None,
    evidence_path: Path | None,
    evidence_summary_path: Path | None,
    allow_operator_review_output: bool = False,
    require_verified_operational_flow_acceptance: bool = False,
    run_id: str = "run_native_handoff_user_ready",
) -> tuple[int, dict[str, Any], str]:
    stdout = StringIO()
    stderr = StringIO()
    argv = [
        "run-native-operational-handoff",
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
        "--dest",
        str(dest),
        "--model",
        "codex",
    ]
    if security_resolution_path is not None:
        argv.extend(["--security-resolution-path", str(security_resolution_path)])
    if evidence_path is not None:
        argv.extend(["--evidence-path", str(evidence_path)])
    if evidence_summary_path is not None:
        argv.extend(["--evidence-summary-path", str(evidence_summary_path)])
    if allow_operator_review_output:
        argv.append("--allow-operator-review-output")
    if require_verified_operational_flow_acceptance:
        argv.append("--require-verified-operational-flow-acceptance")
    exit_code = await run_cli_async(
        argv,
        stdout=stdout,
        stderr=stderr,
        collection_now=lambda: datetime(2026, 5, 15, 1, 45, tzinfo=UTC),
        readiness_now=lambda: datetime(2026, 5, 15, 2, 0, tzinfo=UTC),
        model_client_factory=lambda _config: FakeModelClient(
            [
                ModelResponse(
                    message=Message(
                        role="assistant",
                        content=(TextBlock(text="native handoff commentary"),),
                    )
                )
            ]
        ),
    )
    payload: dict[str, Any] = json.loads(stdout.getvalue()) if stdout.getvalue() else {}
    return exit_code, payload, stderr.getvalue()


def test_native_operational_handoff_cli_produces_verified_operational_flow_user_ready(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        history_dir = tmp_path / "holdings-history"
        universe_state_path = tmp_path / "universe" / "universe_state.json"
        focus_set_path = tmp_path / "focus_set.json"
        resolution_path = tmp_path / "security_resolution.json"
        evidence_path = tmp_path / "external_evidence.json"
        evidence_summary_path = tmp_path / "external_evidence_summary.json"
        dest = tmp_path / "handoff"
        _write_holdings_history(history_dir)
        _write_source_acquisition_success(history_dir)
        _write_universe_state(universe_state_path)
        _write_focus_set(focus_set_path)
        _write_security_resolution(resolution_path)
        _write_external_evidence(evidence_path, evidence_summary_path)

        stdout = StringIO()
        exit_code = await run_cli_async(
            [
                "run-native-operational-handoff",
                "--run-id",
                "run_native_handoff_user_ready",
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
                "--require-verified-operational-flow-acceptance",
            ],
            stdout=stdout,
            collection_now=lambda: datetime(2026, 5, 15, 1, 45, tzinfo=UTC),
            readiness_now=lambda: datetime(2026, 5, 15, 2, 0, tzinfo=UTC),
            model_client_factory=lambda _config: FakeModelClient(
                [
                    ModelResponse(
                        message=Message(
                            role="assistant",
                            content=(TextBlock(text="native handoff commentary"),),
                        )
                    )
                ]
            ),
        )

        payload = json.loads(stdout.getvalue())
        handoff_path = dest / "native_operational_handoff.json"
        exclusion_summary_path = dest / "provider_etf_exclusion_summary.json"

        assert exit_code == 0
        assert payload["schema_version"] == "agent_treport.native_operational_handoff.v1"
        assert payload["status"] == "user_ready"
        assert payload["delivery_blocked"] is False
        assert payload["verified_operational_flow_acceptance"] == {
            "status": "passed",
            "unmet_criteria": [],
        }
        assert "bun" + "dle7_acceptance" not in payload
        assert payload["registered_cohort_statement"] == (
            "This report evaluated the registered live provider cohort, disclosed "
            "excluded providers/ETFs with reasons, and judged user-ready status "
            "using the remaining eligible cohort."
        )
        assert payload["readiness"]["status"] == "ready_with_warnings"
        assert payload["readiness"]["disclosures"]
        assert set(payload["references"]["artifacts"]) == {
            "canonical_payload",
            "markdown_report",
            "html_report",
            "telegram_alert",
            "quality_report",
            "readiness",
            "source_acquisition_summary",
            "collection_summary",
            "external_evidence_summary",
            "provider_etf_exclusion_summary",
        }
        assert payload["references"]["artifacts"]["provider_etf_exclusion_summary"][
            "path"
        ] == str(exclusion_summary_path.resolve())
        assert payload["references"]["follow_up"]["inspect"] == shlex.join(
            payload["references"]["follow_up"]["inspect_argv"]
        )
        assert set(payload["references"]["follow_up"]) == {"inspect", "inspect_argv"}
        assert handoff_path.is_file()
        assert json.loads(handoff_path.read_text(encoding="utf-8")) == payload

        exclusion_summary = json.loads(
            exclusion_summary_path.read_text(encoding="utf-8")
        )
        assert exclusion_summary["registered_provider_ids"] == [
            "kodex",
            "ace",
            "hyundai",
            "timefolio",
            "tiger",
            "rise",
            "sol",
        ]
        assert exclusion_summary["eligible_analysis_cohort"]["provider_ids"] == [
            "ace",
            "tiger",
            "sol",
        ]
        assert exclusion_summary["excluded_providers"] == [
            {"source_provider_id": "kodex", "reason": "no_eligible_etfs"},
            {"source_provider_id": "hyundai", "reason": "no_registered_active_etfs"},
            {"source_provider_id": "timefolio", "reason": "no_registered_active_etfs"},
            {"source_provider_id": "rise", "reason": "no_registered_active_etfs"},
        ]
        assert exclusion_summary["excluded_etfs"] == [
            {
                "etf_id": "etf_kodex_missing",
                "source_provider_id": "kodex",
                "reason": "missing_comparison_window",
            }
        ]
        rendered = json.dumps(
            {
                "handoff": payload,
                "exclusion_summary": exclusion_summary,
            },
            ensure_ascii=False,
        )
        assert "provider_etf_id" not in rendered
        assert "https://" not in rendered

    run_async(scenario())


def test_native_operational_handoff_accepts_aggregated_source_acquisition_summary(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        history_dir = tmp_path / "holdings-history"
        universe_state_path = tmp_path / "universe" / "universe_state.json"
        focus_set_path = tmp_path / "focus_set.json"
        resolution_path = tmp_path / "security_resolution.json"
        evidence_path = tmp_path / "external_evidence.json"
        evidence_summary_path = tmp_path / "external_evidence_summary.json"
        dest = tmp_path / "handoff"
        _write_holdings_history(history_dir)
        _write_aggregated_source_acquisition_success(history_dir)
        _write_universe_state(universe_state_path)
        _write_focus_set(focus_set_path)
        _write_security_resolution(resolution_path)
        _write_external_evidence(evidence_path, evidence_summary_path)

        exit_code, payload, stderr = await _run_native_handoff(
            history_dir=history_dir,
            universe_state_path=universe_state_path,
            focus_set_path=focus_set_path,
            dest=dest,
            security_resolution_path=resolution_path,
            evidence_path=evidence_path,
            evidence_summary_path=evidence_summary_path,
            require_verified_operational_flow_acceptance=True,
            run_id="run_native_handoff_aggregate_source",
        )

        assert stderr == ""
        assert exit_code == 0
        assert payload["status"] == "user_ready"
        assert payload["verified_operational_flow_acceptance"] == {
            "status": "passed",
            "unmet_criteria": [],
        }
        assert "source_acquisition_summary" in payload["references"]["artifacts"]

    run_async(scenario())


def test_native_operational_handoff_requires_approval_before_real_codex_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        history_dir = tmp_path / "holdings-history"
        universe_state_path = tmp_path / "universe" / "universe_state.json"
        focus_set_path = tmp_path / "focus_set.json"
        resolution_path = tmp_path / "security_resolution.json"
        evidence_path = tmp_path / "external_evidence.json"
        evidence_summary_path = tmp_path / "external_evidence_summary.json"
        approval_path = tmp_path / "approval" / "daily_approval.json"
        dest = tmp_path / "handoff"
        stdout = StringIO()
        stderr = StringIO()
        model_created = False
        _write_holdings_history(history_dir)
        _write_source_acquisition_success(history_dir)
        _write_universe_state(universe_state_path)
        _write_focus_set(focus_set_path)
        _write_security_resolution(resolution_path)
        _write_external_evidence(evidence_path, evidence_summary_path)

        def fail_if_model_created(_config):
            nonlocal model_created
            model_created = True
            raise AssertionError("real Codex model should wait for approval")

        monkeypatch.setattr(
            "agent_treport.cli.create_model_client",
            fail_if_model_created,
        )

        exit_code = await run_cli_async(
            [
                "run-native-operational-handoff",
                "--run-id",
                "run_native_handoff_unapproved_model",
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
                "--approval-path",
                str(approval_path),
            ],
            stdout=stdout,
            stderr=stderr,
            collection_now=lambda: datetime(2026, 5, 15, 1, 45, tzinfo=UTC),
            readiness_now=lambda: datetime(2026, 5, 15, 2, 0, tzinfo=UTC),
        )

        payload: dict[str, Any] = json.loads(stdout.getvalue())
        preflight_path = dest / "artifacts" / (
            "daily_operational_external_data_preflight.json"
        )

        assert stderr.getvalue() == ""
        assert exit_code == 1
        assert model_created is False
        assert payload["status"] == "failed"
        assert payload["reason"] == "external_data_approval_required"
        assert payload["approval"]["missing_scopes"] == ["model_export"]
        assert payload["references"]["artifacts"]["approval_preflight"]["path"] == str(
            preflight_path.resolve()
        )
        assert (dest / "native_operational_handoff.json").is_file()

    run_async(scenario())


def test_native_operational_handoff_user_ready_without_reviewed_security_is_not_verified_flow(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        history_dir = tmp_path / "holdings-history"
        universe_state_path = tmp_path / "universe" / "universe_state.json"
        focus_set_path = tmp_path / "focus_set.json"
        evidence_path = tmp_path / "external_evidence.json"
        evidence_summary_path = tmp_path / "external_evidence_summary.json"
        dest = tmp_path / "handoff"
        _write_holdings_history(history_dir, source_tickers=True)
        _write_source_acquisition_success(history_dir)
        _write_universe_state(universe_state_path)
        _write_focus_set(focus_set_path)
        _write_external_evidence(evidence_path, evidence_summary_path)

        exit_code, payload, stderr = await _run_native_handoff(
            history_dir=history_dir,
            universe_state_path=universe_state_path,
            focus_set_path=focus_set_path,
            dest=dest,
            security_resolution_path=None,
            evidence_path=evidence_path,
            evidence_summary_path=evidence_summary_path,
        )

        assert stderr == ""
        assert exit_code == 0
        assert payload["status"] == "user_ready"
        assert payload["verified_operational_flow_acceptance"] == {
            "status": "not_met",
            "unmet_criteria": ["reviewed_security_identity"],
        }
        assert {
            disclosure["code"] for disclosure in payload["readiness"]["disclosures"]
        } >= {"readiness_security_resolution_missing"}

    run_async(scenario())


def test_native_operational_handoff_acceptance_mode_fails_when_external_evidence_not_run(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        history_dir = tmp_path / "holdings-history"
        universe_state_path = tmp_path / "universe" / "universe_state.json"
        focus_set_path = tmp_path / "focus_set.json"
        resolution_path = tmp_path / "security_resolution.json"
        dest = tmp_path / "handoff"
        _write_holdings_history(history_dir)
        _write_source_acquisition_success(history_dir)
        _write_universe_state(universe_state_path)
        _write_focus_set(focus_set_path)
        _write_security_resolution(resolution_path)

        exit_code, payload, stderr = await _run_native_handoff(
            history_dir=history_dir,
            universe_state_path=universe_state_path,
            focus_set_path=focus_set_path,
            dest=dest,
            security_resolution_path=resolution_path,
            evidence_path=None,
            evidence_summary_path=None,
            require_verified_operational_flow_acceptance=True,
        )

        summary_path = dest / "external_evidence_summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        assert stderr == ""
        assert exit_code == 1
        assert payload["status"] == "user_ready"
        assert payload["verified_operational_flow_acceptance"] == {
            "status": "not_met",
            "unmet_criteria": ["external_evidence_summary"],
        }
        assert payload["external_evidence"]["status"] == "not_run"
        assert summary["status"] == "not_run"

    run_async(scenario())


def test_native_operational_handoff_partial_external_evidence_discloses_limitations(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        history_dir = tmp_path / "holdings-history"
        universe_state_path = tmp_path / "universe" / "universe_state.json"
        focus_set_path = tmp_path / "focus_set.json"
        resolution_path = tmp_path / "security_resolution.json"
        evidence_path = tmp_path / "external_evidence.json"
        evidence_summary_path = tmp_path / "external_evidence_summary.json"
        dest = tmp_path / "handoff"
        _write_holdings_history(history_dir)
        _write_source_acquisition_success(history_dir)
        _write_universe_state(universe_state_path)
        _write_focus_set(focus_set_path)
        _write_security_resolution(resolution_path)
        _write_external_evidence(
            evidence_path,
            evidence_summary_path,
            partial_failure=True,
        )

        exit_code, payload, stderr = await _run_native_handoff(
            history_dir=history_dir,
            universe_state_path=universe_state_path,
            focus_set_path=focus_set_path,
            dest=dest,
            security_resolution_path=resolution_path,
            evidence_path=evidence_path,
            evidence_summary_path=evidence_summary_path,
            require_verified_operational_flow_acceptance=True,
        )

        assert stderr == ""
        assert exit_code == 0
        assert payload["status"] == "user_ready"
        assert payload["verified_operational_flow_acceptance"]["status"] == "passed"
        assert payload["external_evidence"]["category_coverage"]["news"]["notes"] == [
            "news provider unavailable"
        ]

    run_async(scenario())


def test_native_operational_handoff_hold_override_is_operator_review_only(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        history_dir = tmp_path / "holdings-history"
        universe_state_path = tmp_path / "universe" / "universe_state.json"
        focus_set_path = tmp_path / "focus_set.json"
        resolution_path = tmp_path / "security_resolution.json"
        evidence_path = tmp_path / "external_evidence.json"
        evidence_summary_path = tmp_path / "external_evidence_summary.json"
        dest = tmp_path / "handoff"
        _write_holdings_history(history_dir)
        _write_universe_state(universe_state_path)
        _write_focus_set(focus_set_path)
        _write_security_resolution(resolution_path, security_ids=["sec_nvda"])
        _write_external_evidence(evidence_path, evidence_summary_path)

        exit_code, payload, stderr = await _run_native_handoff(
            history_dir=history_dir,
            universe_state_path=universe_state_path,
            focus_set_path=focus_set_path,
            dest=dest,
            security_resolution_path=resolution_path,
            evidence_path=evidence_path,
            evidence_summary_path=evidence_summary_path,
            allow_operator_review_output=True,
        )

        assert stderr == ""
        assert exit_code == 0
        assert payload["status"] == "operator_review_only"
        assert payload["delivery_blocked"] is True
        assert payload["reason"] == "readiness_hold"
        assert payload["user_ready_status"] == "not user-ready"
        assert "user_ready" not in payload
        assert payload["operator_review_only"]["delivery_blocked"] is True
        assert payload["operator_review_only"]["reason"] == "readiness_hold"

    run_async(scenario())


def test_native_operational_handoff_report_quality_failure_is_failed_with_evidence(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        history_dir = tmp_path / "holdings-history"
        universe_state_path = tmp_path / "universe" / "universe_state.json"
        focus_set_path = tmp_path / "focus_set.json"
        resolution_path = tmp_path / "security_resolution.json"
        evidence_path = tmp_path / "external_evidence.json"
        evidence_summary_path = tmp_path / "external_evidence_summary.json"
        dest = tmp_path / "handoff"
        _write_holdings_history(history_dir)
        _write_universe_state(universe_state_path)
        _write_focus_set(focus_set_path)
        _write_security_resolution(resolution_path)
        _write_external_evidence(
            evidence_path,
            evidence_summary_path,
            unsafe_title=True,
        )

        exit_code, payload, stderr = await _run_native_handoff(
            history_dir=history_dir,
            universe_state_path=universe_state_path,
            focus_set_path=focus_set_path,
            dest=dest,
            security_resolution_path=resolution_path,
            evidence_path=evidence_path,
            evidence_summary_path=evidence_summary_path,
            require_verified_operational_flow_acceptance=True,
            run_id="run_native_handoff_quality_failed",
        )

        artifacts = payload["references"]["artifacts"]
        assert stderr == ""
        assert exit_code == 1
        assert payload["status"] == "failed"
        assert payload["reason"] == "report_quality_failed"
        assert payload["delivery_blocked"] is True
        assert "user_ready" not in payload
        assert "operator_review_only" not in payload
        assert artifacts["quality_report"]["artifact_id"] == "artifact_treport_quality"
        assert artifacts["readiness"]["artifact_id"] == (
            "artifact_treport_operational_readiness"
        )
        assert artifacts["canonical_payload"]["artifact_id"] == (
            "artifact_treport_signal_payload"
        )
        assert "inspect" in payload["references"]["follow_up"]
        assert set(payload["references"]["follow_up"]) == {"inspect", "inspect_argv"}
        assert any(
            "quality_report" in instruction
            for instruction in payload["recovery"]["instructions"]
        )

    run_async(scenario())


def test_native_operational_handoff_rejects_deprecated_acceptance_flag() -> None:
    old_flag = "--require-" + "bun" + "dle7-acceptance"

    with pytest.raises(SystemExit):
        build_parser().parse_args(
            [
                "run-native-operational-handoff",
                "--universe-state-path",
                "universe_state.json",
                "--dest",
                "handoff",
                "--model",
                "codex",
                old_flag,
            ]
        )
