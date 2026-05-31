from __future__ import annotations

import asyncio
import json
import shlex
import shutil
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from typing import Any

import pytest
from agent_pack.inspection import RunInspectionService
from agent_pack.models import (
    ArtifactRef,
    JsonBlock,
    Message,
    ModelRequest,
    ModelResponse,
    Run,
    RunResult,
    RunSnapshot,
    TextBlock,
    ToolCall,
)
from agent_pack.models_client import FakeModelClient, ModelClient, ModelProviderConfig
from agent_pack.store import SQLiteRunStore
from agent_pack.trace_export import build_trace_export_record

from agent_treport import cli as treport_cli
from agent_treport.cli import run_cli_async
from agent_treport.signal_report.adapters.operational_holdings import (
    compute_operational_export_fingerprint,
)

FIXTURE_ROOT = Path(__file__).parent / "fixtures"
OPERATIONAL_SOURCE_MANIFEST = (
    FIXTURE_ROOT / "operational_holdings_source" / "url_holdings_cumulative.json"
)
OPERATIONAL_NORMALIZED_MANIFEST = (
    FIXTURE_ROOT / "operational_holdings" / "url_holdings_cumulative.json"
)
SECURITY_MAPPING = FIXTURE_ROOT / "security_mapping" / "security_mapping.json"


def run_async(awaitable):
    return asyncio.run(awaitable)


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_stock_mapping_csv(path: Path, rows: list[dict[str, str]]) -> None:
    header = "stock_code,stock_name,symbol,exchange,updated_at\n"
    body = "".join(
        (
            f"{row['stock_code']},{row['stock_name']},{row['symbol']},"
            f"{row['exchange']},{row['updated_at']}\n"
        )
        for row in rows
    )
    path.write_text(header + body, encoding="utf-8")


def _write_cli_normalized_holdings(
    tmp_path: Path,
    rows_by_date: dict[str, list[dict[str, object]]],
) -> Path:
    export_dir = tmp_path / "operational-holdings"
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


def _write_cli_native_collection_fixture(tmp_path: Path) -> Path:
    fixture_path = tmp_path / "native_fixture.json"
    _write_json(
        fixture_path,
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
                        }
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
            "quality_warnings": [],
            "limitations": [
                {
                    "code": "fixture_backed_collection",
                    "message": "Native collection used fixture holdings only.",
                }
            ],
        },
    )
    return fixture_path


def _write_cli_native_universe_fixture(tmp_path: Path) -> Path:
    fixture_path = tmp_path / "native_universe_fixture.json"
    _write_json(
        fixture_path,
        {
            "schema_version": "agent_treport.native_universe.fixture.v1",
            "complete": True,
            "brands": [
                {
                    "brand_id": "brand_alpha",
                    "brand_name": "Alpha Asset Management",
                    "source_provider_id": "provider_universe_fixture",
                }
            ],
            "etfs": [
                {
                    "etf_id": "etf_focus_ai",
                    "etf_name": "Tracked AI ETF",
                    "brand_id": "brand_alpha",
                    "source_provider_id": "provider_universe_fixture",
                }
            ],
        },
    )
    return fixture_path


def _source_holding(**overrides: object) -> dict[str, object]:
    holding: dict[str, object] = {
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
    holding.update(overrides)
    return holding


def _write_cli_fake_source_provider_fixture(tmp_path: Path) -> Path:
    fixture_path = tmp_path / "source_provider_fixture.json"
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
                        "etf_id": "etf_kodex_ai",
                        "etf_name": "KODEX AI ETF",
                        "brand_id": "brand_samsung",
                        "brand_name": "Samsung Asset Management",
                        "strategy_label": "active",
                        "locator": "https://provider.example/internal/2ETF35",
                    }
                ],
            },
            "holdings_results": [
                {
                    "source_provider_id": "provider_kodex_fake",
                    "provider_etf_id": "2ETF35",
                    "requested_date": "2026-05-11",
                    "observed_date": "2026-05-10",
                    "outcome": "fetched",
                    "holdings": [_source_holding()],
                }
            ],
        },
    )
    return fixture_path


def _write_cli_source_handoff_fixture(
    tmp_path: Path,
    *,
    etfs: list[dict[str, str]],
    holdings_by_provider_and_date: dict[tuple[str, str], list[dict[str, object]]],
    filename: str = "source_handoff_fixture.json",
) -> Path:
    fixture_path = tmp_path / filename
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
                        "provider_etf_id": etf["provider_etf_id"],
                        "etf_id": etf["etf_id"],
                        "etf_name": etf["etf_name"],
                        "brand_id": "brand_samsung",
                        "brand_name": "Samsung Asset Management",
                        "strategy_label": "active",
                        "locator": (
                            "https://provider.example/internal/"
                            f"{etf['provider_etf_id']}"
                        ),
                    }
                    for etf in etfs
                ],
            },
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


def _write_cli_security_resolution(
    path: Path,
    *,
    security_ids: list[str],
) -> None:
    _write_json(
        path,
        {
            "schema_version": "agent_treport.security_resolution_export.v1",
            "mappings": [
                {
                    "security_id": security_id,
                    "ticker": f"{security_id.removeprefix('sec_').upper()}_REVIEWED",
                    "name": f"{security_id} reviewed",
                    "exchange": "NASDAQ",
                    "security_classification": "ticker_candidate",
                }
                for security_id in security_ids
            ],
            "exclusions": [],
        },
    )


async def _prepare_cli_source_acquired_readiness(
    *,
    fixture_path: Path,
    source_dir: Path,
    history_dir: Path,
    export_dir: Path,
    readiness_path: Path,
    security_resolution_path: Path | None = None,
    requested_dates: tuple[str, ...] = ("2026-05-08", "2026-05-11"),
    focus_etf_id: str = "etf_focus_ai",
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    collect_exit = await run_cli_async(
        [
            "collect-source-catalog",
            "--fixture-path",
            str(fixture_path),
            "--dest",
            str(source_dir),
        ],
        stdout=StringIO(),
        collection_now=lambda: datetime(2026, 5, 11, 0, 45, tzinfo=UTC),
    )
    assert collect_exit == 0
    for requested_date in requested_dates:
        update_exit = await run_cli_async(
            [
                "update-holdings-history-source",
                "--fixture-path",
                str(fixture_path),
                "--source-catalog-path",
                str(source_dir / "source_catalog.json"),
                "--universe-state-path",
                str(source_dir / "universe_state.json"),
                "--history-dir",
                str(history_dir),
                "--requested-date",
                requested_date,
            ],
            stdout=StringIO(),
            collection_now=lambda: datetime(2026, 5, 11, 1, 0, tzinfo=UTC),
        )
        assert update_exit == 0

    export_args = [
        "export-holdings-comparison",
        "--history-dir",
        str(history_dir),
        "--universe-state-path",
        str(source_dir / "universe_state.json"),
        "--dest",
        str(export_dir),
    ]
    if security_resolution_path is not None:
        export_args.extend(["--security-resolution-path", str(security_resolution_path)])
    export_stdout = StringIO()
    export_exit = await run_cli_async(
        export_args,
        stdout=export_stdout,
        collection_now=lambda: datetime(2026, 5, 11, 1, 30, tzinfo=UTC),
    )
    assert export_exit == 0

    manifest_path = export_dir / "url_holdings_cumulative.json"
    readiness_stdout = StringIO()
    readiness_exit = await run_cli_async(
        [
            "check-operational-readiness",
            "--holdings-path",
            str(manifest_path),
            "--focus-etf-id",
            focus_etf_id,
            "--observed-partitions",
            "2",
        ],
        stdout=readiness_stdout,
        readiness_now=_cli_readiness_now,
    )
    assert readiness_exit == 0
    readiness_path.write_text(readiness_stdout.getvalue(), encoding="utf-8")
    return (
        manifest_path,
        json.loads(export_stdout.getvalue()),
        json.loads(readiness_stdout.getvalue()),
    )


def _cli_normalized_row(
    *,
    security_id: str,
    name: str,
    ticker: str | None,
    security_classification: str,
    is_cash: bool = False,
) -> dict[str, object]:
    return {
        "etf_id": "etf_focus_ai",
        "etf_name": "AI Innovation Active ETF",
        "brand_id": "brand_alpha",
        "source_provider_id": "provider_operational_fixture",
        "as_of_date": "2026-05-11",
        "security_id": security_id,
        "ticker": ticker,
        "name": name,
        "market": None,
        "sector": None,
        "theme": None,
        "country": None,
        "weight_percent": 1.0,
        "shares": 1.0,
        "market_value_krw": 1.0,
        "price_krw": None,
        "is_cash": is_cash,
        "security_classification": security_classification,
    }


class _FakeOpenFigiClient:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.calls: list[list[dict[str, object]]] = []
        self.max_jobs_per_request: int | None = None

    def post_mapping(self, jobs: list[dict[str, object]]) -> object:
        self.calls.append(jobs)
        if not self.responses:
            raise AssertionError("unexpected OpenFIGI call")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _cli_readiness_now() -> datetime:
    return datetime(2026, 5, 11, 1, 0, tzinfo=UTC)


def _copy_cli_ready_operational_export(tmp_path: Path) -> Path:
    destination = tmp_path / "operational-holdings"
    shutil.copytree(FIXTURE_ROOT / "operational_holdings", destination)
    manifest_path = destination / "url_holdings_cumulative.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["synced_at"] = "2026-05-11T01:00:00+00:00"
    _write_json(manifest_path, manifest)
    metadata_path = destination / "sync_metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata.update(
        {
            "synced_at": "2026-05-11T01:00:00+00:00",
            "copied_dates": manifest["dates"],
            "copied_partition_count": len(manifest["partitions"]),
            "copied_record_count": manifest["record_count"],
            "mapped_security_count": 9,
            "unmapped_security_count": 1,
            "unmapped_security_samples": [],
            "sync_quality": {
                "schema_version": "agent_treport.operational_holdings.sync_quality.v1",
                "status": "ok",
                "metrics": {
                    "ticker_mapping_coverage_ratio": 0.9,
                    "mapped_security_count": 9,
                    "unmapped_security_count": 1,
                    "missing_source_date_count": 0,
                },
                "warnings": [],
                "risk_failures": [],
            },
        }
    )
    _write_json(metadata_path, metadata)
    return manifest_path


def _cli_ready_readiness(manifest_path: Path, **overrides: object) -> dict[str, object]:
    readiness: dict[str, object] = {
        "schema_version": "agent_treport.operational_run_readiness.v1",
        "status": "ready",
        "user_ready_allowed": True,
        "holdings_path": str(manifest_path),
        "focus_etf_id": "etf_focus_ai",
        "requested_observed_partitions": 3,
        "current_date": "2026-05-11",
        "previous_date": "2026-05-08",
        "export_fingerprint": compute_operational_export_fingerprint(manifest_path),
        "warnings": [],
        "reasons": [],
        "next_actions": [],
        "summary": {},
        "final_user_ready_requirements": {
            "readiness_user_ready_allowed": True,
            "run_report_status_required": "succeeded",
            "report_quality_status_required": "passed",
            "warning_disclosure_required": False,
        },
    }
    readiness.update(overrides)
    return readiness


def _write_cli_focus_etf_set(path: Path, focus_etf_ids: list[str]) -> None:
    _write_json(
        path,
        {
            "schema_version": "agent_treport.focus_etf_set.v1",
            "focus_etf_ids": focus_etf_ids,
            "label": "Focused handoff",
        },
    )


def _sync_metadata_payload(samples: list[dict[str, object]]) -> dict[str, object]:
    return {
        "schema_version": "agent_treport.operational_holdings.sync_metadata.v1",
        "unmapped_security_samples": samples,
    }


def _recovery_sample(
    security_id: str = "SEC_A",
    name: str = "Alpha Corp.",
) -> dict[str, object]:
    return {
        "security_id": security_id,
        "name": name,
        "observed_row_count": 1,
        "observed_etf_count": 1,
        "observed_date_count": 1,
        "name_alias_count": 0,
    }


def _assistant_text_response(payload: object) -> ModelResponse:
    return ModelResponse(
        message=Message(
            role="assistant",
            content=(TextBlock(text=json.dumps(payload, ensure_ascii=False)),),
        )
    )


def test_agent_treport_cli_runs_signal_report_workflow_with_codex_model_option(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        sqlite_path = tmp_path / "state" / "treport.sqlite3"
        artifact_root = tmp_path / "artifacts"
        factory_calls: list[ModelProviderConfig] = []

        def model_client_factory(config: ModelProviderConfig) -> FakeModelClient:
            factory_calls.append(config)
            return FakeModelClient(
                [
                    ModelResponse(
                        message=Message(
                            role="assistant",
                            content=(TextBlock(text="CLI Codex analysis text."),),
                        )
                    )
                ]
            )

        exit_code = await run_cli_async(
            [
                "run-report",
                "--run-id",
                "run_treport_cli",
                "--sqlite-path",
                str(sqlite_path),
                "--artifact-root",
                str(artifact_root),
                "--model",
                "codex",
                "--codex-model",
                "gpt-test",
                "--model-timeout-seconds",
                "12.5",
            ],
            stdout=stdout,
            model_client_factory=model_client_factory,
        )

        payload = json.loads(stdout.getvalue())
        report_path = artifact_root / "artifact_treport_report.md"
        html_report_path = artifact_root / "artifact_treport_html_report.html"
        telegram_alert_path = artifact_root / "artifact_treport_telegram_alert.txt"
        signal_payload_path = artifact_root / "artifact_treport_signal_payload.json"
        quality_path = artifact_root / "artifact_treport_quality.json"
        report = report_path.read_text(encoding="utf-8")
        html_report = html_report_path.read_text(encoding="utf-8")
        telegram_alert = telegram_alert_path.read_text(encoding="utf-8")
        signal_payload = json.loads(signal_payload_path.read_text(encoding="utf-8"))
        quality = json.loads(quality_path.read_text(encoding="utf-8"))
        sqlite_path_abs = str(sqlite_path.resolve())
        artifact_root_abs = str(artifact_root.resolve())
        inspect_argv = [
            "agent-treport",
            "inspect",
            "--run-id",
            "run_treport_cli",
            "--sqlite-path",
            sqlite_path_abs,
        ]
        assert exit_code == 0
        assert factory_calls == [
            ModelProviderConfig(provider="codex", model="gpt-test", timeout_seconds=12.5)
        ]
        assert payload["status"] == "succeeded"
        assert payload["output"]["state"]["report_artifact_id"] == "artifact_treport_report"
        assert payload["output"]["state"]["html_report_artifact_id"] == (
            "artifact_treport_html_report"
        )
        assert payload["output"]["state"]["telegram_alert_artifact_id"] == (
            "artifact_treport_telegram_alert"
        )
        assert payload["output"]["state"]["signal_payload_artifact_id"] == (
            "artifact_treport_signal_payload"
        )
        assert payload["output"]["state"]["report_quality_artifact_id"] == (
            "artifact_treport_quality"
        )
        assert payload["output"]["state"]["report_quality_status"] == "passed"
        assert payload["output"]["state"]["report_quality_summary"]["blocking"] is False
        assert set(payload["output"]["user_ready"]) == {
            "run_id",
            "sqlite_path",
            "artifact_root",
            "artifacts",
            "commands",
        }
        assert payload["output"]["user_ready"]["run_id"] == "run_treport_cli"
        assert payload["output"]["user_ready"]["sqlite_path"] == sqlite_path_abs
        assert payload["output"]["user_ready"]["artifact_root"] == artifact_root_abs
        assert payload["output"]["user_ready"]["commands"] == {
            "inspect_argv": inspect_argv,
            "inspect": shlex.join(inspect_argv),
        }
        assert set(payload["output"]["user_ready"]["artifacts"]) == {
            "canonical_payload",
            "markdown_report",
            "html_report",
            "telegram_alert",
            "quality_report",
        }
        assert payload["output"]["user_ready"]["artifacts"]["canonical_payload"] == {
            "artifact_id": "artifact_treport_signal_payload",
            "name": "signal_payload.json",
            "media_type": "application/json",
            "uri": signal_payload_path.resolve().as_uri(),
            "path": str(signal_payload_path.resolve()),
        }
        assert payload["output"]["user_ready"]["artifacts"]["markdown_report"] == {
            "artifact_id": "artifact_treport_report",
            "name": "report.md",
            "media_type": "text/markdown",
            "uri": report_path.resolve().as_uri(),
            "path": str(report_path.resolve()),
        }
        assert payload["output"]["user_ready"]["artifacts"]["html_report"] == {
            "artifact_id": "artifact_treport_html_report",
            "name": "report.html",
            "media_type": "text/html",
            "uri": html_report_path.resolve().as_uri(),
            "path": str(html_report_path.resolve()),
        }
        assert payload["output"]["user_ready"]["artifacts"]["telegram_alert"] == {
            "artifact_id": "artifact_treport_telegram_alert",
            "name": "telegram_alert.txt",
            "media_type": "text/plain",
            "uri": telegram_alert_path.resolve().as_uri(),
            "path": str(telegram_alert_path.resolve()),
        }
        assert payload["output"]["user_ready"]["artifacts"]["quality_report"] == {
            "artifact_id": "artifact_treport_quality",
            "name": "quality.json",
            "media_type": "application/json",
            "uri": quality_path.resolve().as_uri(),
            "path": str(quality_path.resolve()),
        }
        assert signal_payload["signal_board"][0]["ticker"] == "NVDA"
        assert quality["status"] == "passed"
        assert quality["summary"]["error_count"] == 0
        assert quality["summary"]["warning_count"] == 0
        assert "markdown_target_section_not_rendered" not in {
            violation["code"] for violation in quality["violations"]
        }
        assert "CLI Codex analysis text." in report
        assert "<title>Signal Intelligence Report</title>" in html_report
        assert "CLI Codex analysis text." in html_report
        assert "<b>ETF 시그널 브리핑</b>" in telegram_alert
        assert "CLI Codex analysis text." not in telegram_alert
        assert "## Market Map" in report
        assert "## ETF Follow Sheets" in report
        assert "## Evidence Ledger" in report
        assert "중점 모니터링" in report
        assert sqlite_path.is_file()
        assert signal_payload_path.is_file()
        assert html_report_path.is_file()
        assert telegram_alert_path.is_file()
        assert quality_path.is_file()
        assert report_path.is_file()

    run_async(scenario())


def test_agent_treport_run_report_requires_approval_before_real_codex_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        sqlite_path = tmp_path / "state" / "treport.sqlite3"
        artifact_root = tmp_path / "artifacts"
        approval_path = tmp_path / "approval" / "daily_approval.json"
        model_created = False

        def fail_if_model_created(_config):
            nonlocal model_created
            model_created = True
            raise AssertionError("real Codex model should wait for approval")

        monkeypatch.setattr(treport_cli, "create_model_client", fail_if_model_created)

        exit_code = await run_cli_async(
            [
                "run-report",
                "--run-id",
                "run_report_unapproved_model",
                "--sqlite-path",
                str(sqlite_path),
                "--artifact-root",
                str(artifact_root),
                "--model",
                "codex",
                "--approval-path",
                str(approval_path),
            ],
            stdout=stdout,
            stderr=stderr,
        )

        payload = json.loads(stdout.getvalue())
        blocked_path = artifact_root / "run_report_approval_block.json"
        preflight_path = artifact_root / "daily_operational_external_data_preflight.json"
        preflight = json.loads(preflight_path.read_text(encoding="utf-8"))

        assert stderr.getvalue() == ""
        assert exit_code == 1
        assert model_created is False
        assert payload == json.loads(blocked_path.read_text(encoding="utf-8"))
        assert payload["approval"]["missing_scopes"] == ["model_export"]
        assert preflight["approval"]["required_scopes"] == ["model_export"]
        assert preflight["boundary"]["model_exports"] == [
            {
                "export_scope": (
                    "path_safe_pre_publish_report_context_for_commentary_generation"
                ),
                "model": "default",
                "provider": "codex",
            }
        ]
        assert str(tmp_path) not in preflight_path.read_text(encoding="utf-8")

    run_async(scenario())


def test_agent_treport_collect_holdings_fixture_command_writes_native_collection(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        fixture_path = _write_cli_native_collection_fixture(tmp_path)
        dest_dir = tmp_path / "native_collected"

        exit_code = await run_cli_async(
            [
                "collect-holdings-fixture",
                "--fixture-path",
                str(fixture_path),
                "--dest",
                str(dest_dir),
                "--observed-partitions",
                "2",
            ],
            stdout=stdout,
            stderr=stderr,
            collection_now=lambda: datetime(2026, 5, 11, 1, 0, tzinfo=UTC),
        )

        payload = json.loads(stdout.getvalue())
        manifest_path = dest_dir / "url_holdings_cumulative.json"
        summary_path = dest_dir / "collection_summary.json"

        assert exit_code == 0
        assert stderr.getvalue() == ""
        assert stdout.getvalue().count("\n") == 1
        assert manifest_path.is_file()
        assert summary_path.is_file()
        assert not (dest_dir / "sync_metadata.json").exists()
        assert payload["schema_version"] == "agent_treport.native_collection.summary.v1"
        assert payload["collection_source_type"] == "fixture"
        assert payload["collected_at"] == "2026-05-11T01:00:00+00:00"
        assert payload["observed_dates"] == ["2026-05-11", "2026-05-08"]
        assert payload["normalized_output"]["manifest_path"] == "url_holdings_cumulative.json"

    run_async(scenario())


def test_agent_treport_collect_universe_fixture_command_writes_state_and_summary(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        fixture_path = _write_cli_native_universe_fixture(tmp_path)
        dest_dir = tmp_path / "native_universe"

        exit_code = await run_cli_async(
            [
                "collect-universe-fixture",
                "--fixture-path",
                str(fixture_path),
                "--dest",
                str(dest_dir),
            ],
            stdout=stdout,
            stderr=stderr,
            collection_now=lambda: datetime(2026, 5, 11, 0, 30, tzinfo=UTC),
        )

        payload = json.loads(stdout.getvalue())
        state = json.loads(
            (dest_dir / "universe_state.json").read_text(encoding="utf-8")
        )

        assert exit_code == 0
        assert stderr.getvalue() == ""
        assert stdout.getvalue().count("\n") == 1
        assert payload["schema_version"] == "agent_treport.native_universe.summary.v1"
        assert payload["collected_at"] == "2026-05-11T00:30:00+00:00"
        assert payload["active_etf_count"] == 1
        assert payload["active_brand_count"] == 1
        assert payload["etf_change_counts"]["added"] == 1
        assert payload["state_output"] == {"state_path": "universe_state.json"}
        assert state["etfs"][0]["etf_name"] == "Tracked AI ETF"
        assert state["brands"][0]["source_provider_id"] == "provider_universe_fixture"

    run_async(scenario())


def test_agent_treport_collect_source_catalog_command_uses_fake_fixture_by_default(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        fixture_path = _write_cli_fake_source_provider_fixture(tmp_path)
        dest_dir = tmp_path / "source_catalog"

        exit_code = await run_cli_async(
            [
                "collect-source-catalog",
                "--fixture-path",
                str(fixture_path),
                "--dest",
                str(dest_dir),
            ],
            stdout=stdout,
            stderr=stderr,
            collection_now=lambda: datetime(2026, 5, 11, 0, 45, tzinfo=UTC),
        )

        payload = json.loads(stdout.getvalue())
        summary_text = stdout.getvalue()
        assert exit_code == 0, stderr.getvalue()
        assert stderr.getvalue() == ""
        assert payload["schema_version"] == "agent_treport.source_acquisition.summary.v1"
        assert payload["source_provider_id"] == "provider_kodex_fake"
        assert payload["run_outcome"] == "succeeded"
        assert payload["catalog_entry_count"] == 1
        assert (dest_dir / "source_catalog.json").is_file()
        assert (dest_dir / "universe_state.json").is_file()
        assert "https://" not in summary_text
        assert str(tmp_path) not in summary_text

    run_async(scenario())


def test_agent_treport_update_holdings_history_source_command_uses_fake_fixture(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        fixture_path = _write_cli_fake_source_provider_fixture(tmp_path)
        source_dir = tmp_path / "source_catalog"
        history_dir = tmp_path / "holdings_history"
        await run_cli_async(
            [
                "collect-source-catalog",
                "--fixture-path",
                str(fixture_path),
                "--dest",
                str(source_dir),
            ],
            stdout=StringIO(),
            collection_now=lambda: datetime(2026, 5, 11, 0, 45, tzinfo=UTC),
        )
        stdout = StringIO()
        stderr = StringIO()

        exit_code = await run_cli_async(
            [
                "update-holdings-history-source",
                "--fixture-path",
                str(fixture_path),
                "--source-catalog-path",
                str(source_dir / "source_catalog.json"),
                "--universe-state-path",
                str(source_dir / "universe_state.json"),
                "--history-dir",
                str(history_dir),
                "--requested-date",
                "2026-05-11",
            ],
            stdout=stdout,
            stderr=stderr,
            collection_now=lambda: datetime(2026, 5, 11, 1, 30, tzinfo=UTC),
        )

        assert exit_code == 0, stderr.getvalue()
        assert stderr.getvalue() == ""
        payload = json.loads(stdout.getvalue())
        assert payload["schema_version"] == "agent_treport.source_acquisition.summary.v1"
        assert payload["run_outcome"] == "succeeded"
        assert payload["requested_dates"] == ["2026-05-11"]
        assert payload["observed_dates"] == ["2026-05-10"]
        assert payload["aggregate_counts"]["fetched"] == 1
        assert (history_dir / "holdings_history.json").is_file()
        assert "https://" not in stdout.getvalue()
        assert "sec_nvda" not in stdout.getvalue()
        assert str(tmp_path) not in stdout.getvalue()

    run_async(scenario())


def test_agent_treport_update_holdings_history_source_provider_etf_id_bounds_targets(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        fixture_path = tmp_path / "source_provider_fixture.json"
        _write_json(
            fixture_path,
            {
                "schema_version": "agent_treport.source_provider.fake.v1",
                "source_provider_id": "provider_kodex_fake",
                "catalog": {"complete": True, "entries": []},
                "holdings_results": [
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
                        "observed_date": "2026-05-11",
                        "outcome": "fetched",
                        "holdings": [
                            _source_holding(security_id="sec_tsla", ticker="TSLA")
                        ],
                    },
                ],
            },
        )
        source_dir = tmp_path / "source_catalog"
        history_dir = tmp_path / "holdings_history"
        source_dir.mkdir()
        _write_json(
            source_dir / "source_catalog.json",
            {
                "schema_version": "agent_treport.source_catalog.v1",
                "source_provider_id": "provider_kodex_fake",
                "complete": True,
                "collected_at": "2026-05-11T00:45:00+00:00",
                "entries": [
                    {
                        "source_provider_id": "provider_kodex_fake",
                        "provider_etf_id": "2ETF35",
                        "etf_id": "etf_kodex_ai",
                        "etf_name": "KODEX AI ETF",
                        "brand_id": "brand_samsung",
                        "brand_name": "Samsung Asset Management",
                        "is_active_strategy_etf": True,
                        "active_strategy_source": "source_metadata",
                        "active_strategy_confidence": "high",
                    },
                    {
                        "source_provider_id": "provider_kodex_fake",
                        "provider_etf_id": "2ETF36",
                        "etf_id": "etf_kodex_robotics",
                        "etf_name": "KODEX Robotics ETF",
                        "brand_id": "brand_samsung",
                        "brand_name": "Samsung Asset Management",
                        "is_active_strategy_etf": True,
                        "active_strategy_source": "source_metadata",
                        "active_strategy_confidence": "high",
                    },
                ],
            },
        )
        _write_json(
            source_dir / "universe_state.json",
            {
                "schema_version": "agent_treport.native_universe.state.v1",
                "collection_source_type": "source_provider",
                "updated_at": "2026-05-11T00:45:00+00:00",
                "etfs": [
                    {
                        "etf_id": "etf_kodex_ai",
                        "etf_name": "KODEX AI ETF",
                        "brand_id": "brand_samsung",
                        "source_provider_id": "provider_kodex_fake",
                        "status": "active",
                    },
                    {
                        "etf_id": "etf_kodex_robotics",
                        "etf_name": "KODEX Robotics ETF",
                        "brand_id": "brand_samsung",
                        "source_provider_id": "provider_kodex_fake",
                        "status": "active",
                    },
                ],
                "brands": [
                    {
                        "brand_id": "brand_samsung",
                        "brand_name": "Samsung Asset Management",
                        "source_provider_id": "provider_kodex_fake",
                        "status": "active",
                    }
                ],
            },
        )
        stdout = StringIO()
        stderr = StringIO()

        exit_code = await run_cli_async(
            [
                "update-holdings-history-source",
                "--fixture-path",
                str(fixture_path),
                "--source-catalog-path",
                str(source_dir / "source_catalog.json"),
                "--universe-state-path",
                str(source_dir / "universe_state.json"),
                "--history-dir",
                str(history_dir),
                "--requested-date",
                "2026-05-11",
                "--provider-etf-id",
                "2ETF35",
            ],
            stdout=stdout,
            stderr=stderr,
            collection_now=lambda: datetime(2026, 5, 11, 1, 30, tzinfo=UTC),
        )

        assert exit_code == 0, stderr.getvalue()
        assert stderr.getvalue() == ""
        payload = json.loads(stdout.getvalue())
        assert payload["aggregate_counts"]["target_count"] == 1
        assert [item["etf_id"] for item in payload["target_outcomes"]] == [
            "etf_kodex_ai"
        ]

    run_async(scenario())


def test_agent_treport_update_holdings_history_source_command_defaults_requested_date(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        fixture_path = tmp_path / "source_provider_fixture.json"
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
                            "etf_id": "etf_kodex_ai",
                            "etf_name": "KODEX AI ETF",
                            "brand_id": "brand_samsung",
                            "brand_name": "Samsung Asset Management",
                            "strategy_label": "active",
                            "locator": "https://provider.example/internal/2ETF35",
                        }
                    ],
                },
                "holdings_results": [
                    {
                        "source_provider_id": "provider_kodex_fake",
                        "provider_etf_id": "2ETF35",
                        "requested_date": "2026-05-14",
                        "observed_date": "2026-05-14",
                        "outcome": "fetched",
                        "holdings": [_source_holding()],
                    }
                ],
            },
        )
        source_dir = tmp_path / "source_catalog"
        history_dir = tmp_path / "holdings_history"
        await run_cli_async(
            [
                "collect-source-catalog",
                "--fixture-path",
                str(fixture_path),
                "--dest",
                str(source_dir),
            ],
            stdout=StringIO(),
            collection_now=lambda: datetime(2026, 5, 15, 0, 45, tzinfo=UTC),
        )
        stdout = StringIO()
        stderr = StringIO()

        exit_code = await run_cli_async(
            [
                "update-holdings-history-source",
                "--fixture-path",
                str(fixture_path),
                "--source-catalog-path",
                str(source_dir / "source_catalog.json"),
                "--universe-state-path",
                str(source_dir / "universe_state.json"),
                "--history-dir",
                str(history_dir),
            ],
            stdout=stdout,
            stderr=stderr,
            collection_now=lambda: datetime(2026, 5, 15, 1, 30, tzinfo=UTC),
        )

        assert exit_code == 0, stderr.getvalue()
        assert stderr.getvalue() == ""
        payload = json.loads(stdout.getvalue())
        assert payload["requested_dates"] == ["2026-05-14"]
        assert "provider_query_date" not in payload["target_outcomes"][0]
        assert payload["observed_dates"] == ["2026-05-14"]
        assert (history_dir / "holdings_history.json").is_file()
        assert "https://" not in stdout.getvalue()
        assert str(tmp_path) not in stdout.getvalue()

    run_async(scenario())


def test_agent_treport_source_catalog_live_provider_requires_explicit_live_flag(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        fixture_path = _write_cli_fake_source_provider_fixture(tmp_path)

        exit_code = await run_cli_async(
            [
                "collect-source-catalog",
                "--source-provider",
                "kodex",
                "--fixture-path",
                str(fixture_path),
                "--dest",
                str(tmp_path / "source_catalog"),
            ],
            stdout=stdout,
            stderr=stderr,
        )

        assert exit_code == 2
        assert stdout.getvalue() == ""
        assert stderr.getvalue() == (
            "agent-treport: error: live source acquisition requires --live\n"
        )

    run_async(scenario())


def test_agent_treport_live_source_catalog_requires_approval_before_provider_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        dest = tmp_path / "source_catalog"
        approval_path = tmp_path / "approval" / "daily_approval.json"
        provider_created = False

        def fail_if_provider_created(_provider_id: str):
            nonlocal provider_created
            provider_created = True
            raise AssertionError("live provider should not be created before approval")

        monkeypatch.setattr(
            treport_cli,
            "create_live_source_provider",
            fail_if_provider_created,
        )

        exit_code = await run_cli_async(
            [
                "collect-source-catalog",
                "--source-provider",
                "tiger",
                "--live",
                "--dest",
                str(dest),
                "--approval-path",
                str(approval_path),
            ],
            stdout=stdout,
            stderr=stderr,
        )

        payload = json.loads(stdout.getvalue())
        blocked_path = dest / "source_catalog_approval_block.json"
        preflight_path = dest / "daily_operational_external_data_preflight.json"
        template_path = dest / "daily_operational_external_data_approval_template.json"
        preflight = json.loads(preflight_path.read_text(encoding="utf-8"))

        assert stderr.getvalue() == ""
        assert exit_code == 1
        assert provider_created is False
        assert payload == json.loads(blocked_path.read_text(encoding="utf-8"))
        assert payload["status"] == "failed"
        assert payload["reason"] == "external_data_approval_required"
        assert payload["approval"]["status"] == "missing"
        assert payload["approval"]["missing_scopes"] == ["live_source_catalog"]
        assert payload["references"]["artifacts"]["approval_preflight"]["path"] == str(
            preflight_path.resolve()
        )
        assert payload["references"]["artifacts"]["approval_template"]["path"] == str(
            template_path.resolve()
        )
        assert preflight["approval"]["required_scopes"] == ["live_source_catalog"]
        assert preflight["boundary"]["live_source_provider_ids"] == ["tiger"]
        assert str(tmp_path) not in preflight_path.read_text(encoding="utf-8")

    run_async(scenario())


def test_agent_treport_live_holdings_source_requires_approval_before_provider_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        history_dir = tmp_path / "holdings_history"
        approval_path = tmp_path / "approval" / "daily_approval.json"
        provider_created = False

        def fail_if_provider_created(_provider_id: str):
            nonlocal provider_created
            provider_created = True
            raise AssertionError("live provider should not be created before approval")

        monkeypatch.setattr(
            treport_cli,
            "create_live_source_provider",
            fail_if_provider_created,
        )

        exit_code = await run_cli_async(
            [
                "update-holdings-history-source",
                "--source-provider",
                "tiger",
                "--live",
                "--source-catalog-path",
                str(tmp_path / "source_catalog.json"),
                "--universe-state-path",
                str(tmp_path / "universe_state.json"),
                "--history-dir",
                str(history_dir),
                "--provider-etf-id",
                "KR7001",
                "--approval-path",
                str(approval_path),
            ],
            stdout=stdout,
            stderr=stderr,
        )

        payload = json.loads(stdout.getvalue())
        blocked_path = history_dir / "holdings_source_approval_block.json"
        preflight_path = history_dir / "daily_operational_external_data_preflight.json"
        preflight = json.loads(preflight_path.read_text(encoding="utf-8"))

        assert stderr.getvalue() == ""
        assert exit_code == 1
        assert provider_created is False
        assert payload == json.loads(blocked_path.read_text(encoding="utf-8"))
        assert payload["status"] == "failed"
        assert payload["reason"] == "external_data_approval_required"
        assert payload["approval"]["missing_scopes"] == ["live_holdings_acquisition"]
        assert preflight["approval"]["required_scopes"] == [
            "live_holdings_acquisition"
        ]
        assert preflight["boundary"]["live_source_provider_ids"] == ["tiger"]
        assert preflight["boundary"]["approved_max_target_count"] == 1
        assert str(tmp_path) not in preflight_path.read_text(encoding="utf-8")

    run_async(scenario())


def test_agent_treport_source_catalog_accepts_ace_as_explicit_live_provider_choice(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        fixture_path = _write_cli_fake_source_provider_fixture(tmp_path)

        exit_code = await run_cli_async(
            [
                "collect-source-catalog",
                "--source-provider",
                "ace",
                "--fixture-path",
                str(fixture_path),
                "--dest",
                str(tmp_path / "source_catalog"),
            ],
            stdout=stdout,
            stderr=stderr,
        )

        assert exit_code == 2
        assert stdout.getvalue() == ""
        assert stderr.getvalue() == (
            "agent-treport: error: live source acquisition requires --live\n"
        )

    run_async(scenario())


def test_agent_treport_source_catalog_accepts_hyundai_as_explicit_live_provider_choice(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        fixture_path = _write_cli_fake_source_provider_fixture(tmp_path)

        exit_code = await run_cli_async(
            [
                "collect-source-catalog",
                "--source-provider",
                "hyundai",
                "--fixture-path",
                str(fixture_path),
                "--dest",
                str(tmp_path / "source_catalog"),
            ],
            stdout=stdout,
            stderr=stderr,
        )

        assert exit_code == 2
        assert stdout.getvalue() == ""
        assert stderr.getvalue() == (
            "agent-treport: error: live source acquisition requires --live\n"
        )

    run_async(scenario())


def test_agent_treport_source_catalog_accepts_timefolio_as_explicit_live_provider_choice(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        fixture_path = _write_cli_fake_source_provider_fixture(tmp_path)

        exit_code = await run_cli_async(
            [
                "collect-source-catalog",
                "--source-provider",
                "timefolio",
                "--fixture-path",
                str(fixture_path),
                "--dest",
                str(tmp_path / "source_catalog"),
            ],
            stdout=stdout,
            stderr=stderr,
        )

        assert exit_code == 2
        assert stdout.getvalue() == ""
        assert stderr.getvalue() == (
            "agent-treport: error: live source acquisition requires --live\n"
        )

    run_async(scenario())


def test_agent_treport_source_catalog_accepts_tiger_as_explicit_live_provider_choice(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        fixture_path = _write_cli_fake_source_provider_fixture(tmp_path)

        exit_code = await run_cli_async(
            [
                "collect-source-catalog",
                "--source-provider",
                "tiger",
                "--fixture-path",
                str(fixture_path),
                "--dest",
                str(tmp_path / "source_catalog"),
            ],
            stdout=stdout,
            stderr=stderr,
        )

        assert exit_code == 2
        assert stdout.getvalue() == ""
        assert stderr.getvalue() == (
            "agent-treport: error: live source acquisition requires --live\n"
        )

    run_async(scenario())


def test_agent_treport_source_catalog_accepts_rise_as_explicit_live_provider_choice(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        fixture_path = _write_cli_fake_source_provider_fixture(tmp_path)

        exit_code = await run_cli_async(
            [
                "collect-source-catalog",
                "--source-provider",
                "rise",
                "--fixture-path",
                str(fixture_path),
                "--dest",
                str(tmp_path / "source_catalog"),
            ],
            stdout=stdout,
            stderr=stderr,
        )

        assert exit_code == 2
        assert stdout.getvalue() == ""
        assert stderr.getvalue() == (
            "agent-treport: error: live source acquisition requires --live\n"
        )

    run_async(scenario())


def test_agent_treport_source_catalog_accepts_sol_as_explicit_live_provider_choice(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        fixture_path = _write_cli_fake_source_provider_fixture(tmp_path)

        exit_code = await run_cli_async(
            [
                "collect-source-catalog",
                "--source-provider",
                "sol",
                "--fixture-path",
                str(fixture_path),
                "--dest",
                str(tmp_path / "source_catalog"),
            ],
            stdout=stdout,
            stderr=stderr,
        )

        assert exit_code == 2
        assert stdout.getvalue() == ""
        assert stderr.getvalue() == (
            "agent-treport: error: live source acquisition requires --live\n"
        )

    run_async(scenario())


def test_agent_treport_source_catalog_live_flag_requires_provider_selection(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        fixture_path = _write_cli_fake_source_provider_fixture(tmp_path)

        exit_code = await run_cli_async(
            [
                "collect-source-catalog",
                "--live",
                "--fixture-path",
                str(fixture_path),
                "--dest",
                str(tmp_path / "source_catalog"),
            ],
            stdout=stdout,
            stderr=stderr,
        )

        assert exit_code == 2
        assert stdout.getvalue() == ""
        assert stderr.getvalue() == (
            "agent-treport: error: live source acquisition requires --source-provider\n"
        )

    run_async(scenario())


def test_agent_treport_source_history_live_requires_selected_provider_etf(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()

        exit_code = await run_cli_async(
            [
                "update-holdings-history-source",
                "--live",
                "--source-provider",
                "kodex",
                "--source-catalog-path",
                str(tmp_path / "source_catalog.json"),
                "--universe-state-path",
                str(tmp_path / "universe_state.json"),
                "--history-dir",
                str(tmp_path / "holdings_history"),
                "--requested-date",
                "2026-05-11",
            ],
            stdout=stdout,
            stderr=stderr,
        )

        assert exit_code == 2
        assert stdout.getvalue() == ""
        assert stderr.getvalue() == (
            "agent-treport: error: live source holdings requires --provider-etf-id\n"
        )

    run_async(scenario())


def test_agent_treport_collect_holdings_fixture_command_accepts_universe_state(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        universe_stdout = StringIO()
        universe_stderr = StringIO()
        universe_fixture_path = _write_cli_native_universe_fixture(tmp_path)
        universe_dir = tmp_path / "native_universe"
        await run_cli_async(
            [
                "collect-universe-fixture",
                "--fixture-path",
                str(universe_fixture_path),
                "--dest",
                str(universe_dir),
            ],
            stdout=universe_stdout,
            stderr=universe_stderr,
            collection_now=lambda: datetime(2026, 5, 11, 0, 30, tzinfo=UTC),
        )
        stdout = StringIO()
        stderr = StringIO()
        fixture_path = _write_cli_native_collection_fixture(tmp_path)
        dest_dir = tmp_path / "native_collected"

        exit_code = await run_cli_async(
            [
                "collect-holdings-fixture",
                "--fixture-path",
                str(fixture_path),
                "--dest",
                str(dest_dir),
                "--observed-partitions",
                "1",
                "--universe-state-path",
                str(universe_dir / "universe_state.json"),
            ],
            stdout=stdout,
            stderr=stderr,
            collection_now=lambda: datetime(2026, 5, 11, 1, 0, tzinfo=UTC),
        )

        latest_partition = (
            dest_dir / "url_holdings_cumulative.json.parts" / "2026-05-11.jsonl"
        )
        row = json.loads(latest_partition.read_text(encoding="utf-8").splitlines()[0])

        assert exit_code == 0
        assert stderr.getvalue() == ""
        assert row["etf_name"] == "Tracked AI ETF"
        assert row["source_provider_id"] == "provider_universe_fixture"

    run_async(scenario())


def test_agent_treport_native_history_commands_update_export_and_readiness(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        universe_stdout = StringIO()
        update_stdout = StringIO()
        export_stdout = StringIO()
        readiness_stdout = StringIO()
        universe_dir = tmp_path / "native_universe"
        history_dir = tmp_path / "holdings_history"
        export_dir = tmp_path / "latest_comparison"
        universe_fixture_path = _write_cli_native_universe_fixture(tmp_path)
        holdings_fixture_path = _write_cli_native_collection_fixture(tmp_path)

        universe_exit = await run_cli_async(
            [
                "collect-universe-fixture",
                "--fixture-path",
                str(universe_fixture_path),
                "--dest",
                str(universe_dir),
            ],
            stdout=universe_stdout,
            collection_now=lambda: datetime(2026, 5, 11, 0, 30, tzinfo=UTC),
        )
        update_exit = await run_cli_async(
            [
                "update-holdings-history-fixture",
                "--fixture-path",
                str(holdings_fixture_path),
                "--universe-state-path",
                str(universe_dir / "universe_state.json"),
                "--history-dir",
                str(history_dir),
                "--observed-partitions",
                "2",
            ],
            stdout=update_stdout,
            collection_now=lambda: datetime(2026, 5, 11, 1, 0, tzinfo=UTC),
        )
        export_exit = await run_cli_async(
            [
                "export-holdings-comparison",
                "--history-dir",
                str(history_dir),
                "--universe-state-path",
                str(universe_dir / "universe_state.json"),
                "--dest",
                str(export_dir),
            ],
            stdout=export_stdout,
            collection_now=lambda: datetime(2026, 5, 11, 1, 30, tzinfo=UTC),
        )
        manifest_path = export_dir / "url_holdings_cumulative.json"
        readiness_exit = await run_cli_async(
            [
                "check-operational-readiness",
                "--holdings-path",
                str(manifest_path),
                "--focus-etf-id",
                "etf_focus_ai",
                "--observed-partitions",
                "2",
            ],
            stdout=readiness_stdout,
            readiness_now=_cli_readiness_now,
        )

        update_payload = json.loads(update_stdout.getvalue())
        export_payload = json.loads(export_stdout.getvalue())
        readiness_payload = json.loads(readiness_stdout.getvalue())
        assert universe_exit == 0
        assert update_exit == 0
        assert export_exit == 0
        assert readiness_exit == 0
        assert (history_dir / "holdings_history.json").is_file()
        assert manifest_path.is_file()
        assert update_payload["schema_version"] == (
            "agent_treport.native_holdings.history_update.v1"
        )
        assert update_payload["added_snapshot_count"] == 2
        assert update_payload["selected_active_etf_ids"] == ["etf_focus_ai"]
        assert export_payload["collection_source_type"] == "native_history"
        assert export_payload["observed_dates"] == ["2026-05-11", "2026-05-08"]
        assert export_payload["active_etf_coverage"]["coverage_ratio"] == 1.0
        assert export_payload["security_coverage"]["security_resolution_available"] is False
        assert readiness_payload["status"] == "ready_with_warnings"
        assert readiness_payload["readiness_evidence_type"] == "native_history"
        assert [warning["code"] for warning in readiness_payload["warnings"]] == [
            "security_resolution_missing"
        ]
        assert readiness_payload["current_date"] == "2026-05-11"
        assert readiness_payload["previous_date"] == "2026-05-08"

    run_async(scenario())


def test_agent_treport_export_holdings_comparison_applies_security_resolution_option(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        universe_dir = tmp_path / "native_universe"
        history_dir = tmp_path / "holdings_history"
        export_dir = tmp_path / "latest_comparison"
        resolution_path = tmp_path / "security_resolution.json"
        universe_fixture_path = _write_cli_native_universe_fixture(tmp_path)
        holdings_fixture_path = _write_cli_native_collection_fixture(tmp_path)

        await run_cli_async(
            [
                "collect-universe-fixture",
                "--fixture-path",
                str(universe_fixture_path),
                "--dest",
                str(universe_dir),
            ],
            stdout=StringIO(),
            collection_now=lambda: datetime(2026, 5, 11, 0, 30, tzinfo=UTC),
        )
        await run_cli_async(
            [
                "update-holdings-history-fixture",
                "--fixture-path",
                str(holdings_fixture_path),
                "--universe-state-path",
                str(universe_dir / "universe_state.json"),
                "--history-dir",
                str(history_dir),
                "--observed-partitions",
                "2",
            ],
            stdout=StringIO(),
            collection_now=lambda: datetime(2026, 5, 11, 1, 0, tzinfo=UTC),
        )
        _write_json(
            resolution_path,
            {
                "schema_version": "agent_treport.security_resolution_export.v1",
                "mappings": [
                    {
                        "security_id": "sec_nvda",
                        "ticker": "NVDA_REVIEWED",
                        "name": "NVIDIA Corp.",
                        "exchange": "NASDAQ",
                        "security_classification": "ticker_candidate",
                    }
                ],
                "exclusions": [],
            },
        )
        first_stdout = StringIO()
        first_exit = await run_cli_async(
            [
                "export-holdings-comparison",
                "--history-dir",
                str(history_dir),
                "--universe-state-path",
                str(universe_dir / "universe_state.json"),
                "--dest",
                str(export_dir),
                "--security-resolution-path",
                str(resolution_path),
            ],
            stdout=first_stdout,
            collection_now=lambda: datetime(2026, 5, 11, 1, 30, tzinfo=UTC),
        )
        manifest_path = export_dir / "url_holdings_cumulative.json"
        first_summary = json.loads(first_stdout.getvalue())
        first_rows = [
            json.loads(line)
            for line in (
                export_dir / "url_holdings_cumulative.json.parts" / "2026-05-11.jsonl"
            )
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        first_fingerprint = compute_operational_export_fingerprint(manifest_path)

        _write_json(
            resolution_path,
            {
                "schema_version": "agent_treport.security_resolution_export.v1",
                "mappings": [
                    {
                        "security_id": "sec_nvda",
                        "ticker": "NVDA_NEXT",
                        "name": "NVIDIA Corp.",
                        "exchange": "NASDAQ",
                        "security_classification": "ticker_candidate",
                    }
                ],
                "exclusions": [],
            },
        )
        second_stdout = StringIO()
        second_exit = await run_cli_async(
            [
                "export-holdings-comparison",
                "--history-dir",
                str(history_dir),
                "--universe-state-path",
                str(universe_dir / "universe_state.json"),
                "--dest",
                str(export_dir),
                "--security-resolution-path",
                str(resolution_path),
            ],
            stdout=second_stdout,
            collection_now=lambda: datetime(2026, 5, 11, 2, 0, tzinfo=UTC),
        )
        second_summary = json.loads(second_stdout.getvalue())
        second_rows = [
            json.loads(line)
            for line in (
                export_dir / "url_holdings_cumulative.json.parts" / "2026-05-11.jsonl"
            )
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        second_fingerprint = compute_operational_export_fingerprint(manifest_path)
        history_rows = [
            json.loads(line)
            for line in (
                history_dir / "holdings_history.json.parts" / "2026-05-11.jsonl"
            )
            .read_text(encoding="utf-8")
            .splitlines()
        ]

        assert first_exit == 0
        assert second_exit == 0
        assert first_rows[0]["ticker"] == "NVDA_REVIEWED"
        assert second_rows[0]["ticker"] == "NVDA_NEXT"
        assert history_rows[0]["ticker"] == "NVDA"
        assert first_summary["security_coverage"]["security_resolution_available"] is True
        assert first_summary["security_coverage"]["reviewed_mapping_applied_count"] == 2
        assert first_summary["security_coverage"]["ticker_mapping_coverage_ratio"] == 1.0
        assert first_fingerprint != second_fingerprint
        assert first_summary["normalized_output"]["fingerprint"] == first_fingerprint
        assert second_summary["normalized_output"]["fingerprint"] == second_fingerprint

    run_async(scenario())


def test_agent_treport_sync_operational_holdings_command_prints_metadata_json(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        dest_dir = tmp_path / "operational-holdings"

        exit_code = await run_cli_async(
            [
                "sync-operational-holdings",
                "--source",
                str(OPERATIONAL_SOURCE_MANIFEST),
                "--dest",
                str(dest_dir),
                "--observed-partitions",
                "2",
            ],
            stdout=stdout,
            stderr=stderr,
        )

        payload = json.loads(stdout.getvalue())

        assert exit_code == 0
        assert stderr.getvalue() == ""
        assert payload["schema_version"] == (
            "agent_treport.operational_holdings.sync_metadata.v1"
        )
        assert payload["copied_dates"] == ["2026-05-11", "2026-05-08"]
        assert payload["copied_record_count"] == 16
        assert payload["sync_quality"]["schema_version"] == (
            "agent_treport.operational_holdings.sync_quality.v1"
        )
        assert payload["security_mapping_available"] is False
        assert payload["security_mapping_path"] is None
        assert payload["mapped_security_count"] == 0
        assert payload["unmapped_security_count"] == 10
        assert payload["unmapped_security_samples"] == json.loads(
            (dest_dir / "sync_metadata.json").read_text(encoding="utf-8")
        )["unmapped_security_samples"]
        assert all(
            set(sample) == {
                "security_id",
                "name",
                "observed_row_count",
                "observed_etf_count",
                "observed_date_count",
                "name_alias_count",
            }
            for sample in payload["unmapped_security_samples"]
        )
        assert payload["sync_quality"]["status"] == "risk_failed"
        assert payload["sync_quality"]["metrics"]["cash_derivation_attempt_count"] == 3
        assert payload["sync_quality"]["metrics"]["cash_derivation_failure_ratio"] == 0.0
        assert payload["sync_quality"]["metrics"]["skipped_missing_security_id_count"] == 1
        assert payload["sync_quality"]["metrics"]["security_mapping_available"] is False
        assert payload["sync_quality"]["metrics"]["mapped_security_count"] == 0
        assert payload["sync_quality"]["metrics"]["unmapped_security_count"] == 10
        assert payload["sync_quality"]["metrics"]["ticker_mapping_coverage_ratio"] == 0.0
        assert payload["sync_quality"]["warnings"] == [
            {
                "code": "skipped_missing_security_id",
                "message": "Non-cash source rows without a stable security id were skipped.",
                "metric": "skipped_missing_security_id_count",
                "value": 1,
                "threshold": 0,
            }
        ]
        assert payload["sync_quality"]["risk_failures"] == [
            {
                "code": "low_ticker_mapping_coverage",
                "message": "Ticker mapping coverage fell below the operational review threshold.",
                "metric": "ticker_mapping_coverage_ratio",
                "value": 0.0,
                "threshold": 0.5,
            }
        ]
        assert (dest_dir / "url_holdings_cumulative.json").is_file()
        assert (
            dest_dir / "url_holdings_cumulative.json.parts" / "2026-05-11.jsonl"
        ).is_file()

    run_async(scenario())


def test_agent_treport_sync_operational_holdings_command_accepts_security_mapping(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        dest_dir = tmp_path / "operational-holdings"

        exit_code = await run_cli_async(
            [
                "sync-operational-holdings",
                "--source",
                str(OPERATIONAL_SOURCE_MANIFEST),
                "--dest",
                str(dest_dir),
                "--observed-partitions",
                "2",
                "--security-mapping-path",
                str(SECURITY_MAPPING),
            ],
            stdout=stdout,
            stderr=stderr,
        )

        payload = json.loads(stdout.getvalue())

        assert exit_code == 0
        assert stderr.getvalue() == ""
        assert payload["security_mapping_available"] is True
        assert payload["security_mapping_path"] == str(SECURITY_MAPPING)
        assert payload["mapped_security_count"] == 6
        assert payload["unmapped_security_count"] == 4
        assert payload["sync_quality"]["metrics"]["security_mapping_available"] is True
        assert payload["sync_quality"]["metrics"]["mapped_security_count"] == 6
        assert payload["sync_quality"]["metrics"]["unmapped_security_count"] == 4
        assert payload["sync_quality"]["metrics"]["ticker_mapping_coverage_ratio"] == 0.6

    run_async(scenario())


def test_agent_treport_sync_operational_holdings_missing_security_mapping_returns_exit_2(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()

        exit_code = await run_cli_async(
            [
                "sync-operational-holdings",
                "--source",
                str(OPERATIONAL_SOURCE_MANIFEST),
                "--dest",
                str(tmp_path / "operational-holdings"),
                "--security-mapping-path",
                str(tmp_path / "missing-security-mapping.json"),
            ],
            stdout=stdout,
            stderr=stderr,
        )

        assert exit_code == 2
        assert stdout.getvalue() == ""
        assert "security mapping file not found" in stderr.getvalue()
        assert stderr.getvalue().startswith("agent-treport: error: ")

    run_async(scenario())


def test_agent_treport_sync_operational_holdings_rejects_mapping_and_resolution_paths(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        mapping_path = tmp_path / "security_mapping.json"
        resolution_path = tmp_path / "security_resolution.json"
        _write_json(
            mapping_path,
            {"schema_version": "agent_treport.security_mapping.v1", "mappings": []},
        )
        _write_json(
            resolution_path,
            {
                "schema_version": "agent_treport.security_resolution_export.v1",
                "mappings": [],
                "exclusions": [],
            },
        )

        exit_code = await run_cli_async(
            [
                "sync-operational-holdings",
                "--source",
                str(OPERATIONAL_SOURCE_MANIFEST),
                "--dest",
                str(tmp_path / "operational-holdings"),
                "--security-mapping-path",
                str(mapping_path),
                "--security-resolution-path",
                str(resolution_path),
            ],
            stdout=stdout,
            stderr=stderr,
        )

        assert exit_code == 2
        assert stdout.getvalue() == ""
        assert "--security-resolution-path and --security-mapping-path" in stderr.getvalue()
        assert stderr.getvalue().startswith("agent-treport: error: ")

    run_async(scenario())


def test_agent_treport_import_security_master_seed_creates_auto_verified_entries(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        stock_mapping_csv = tmp_path / "stock_mapping.csv"
        workspace = tmp_path / "security-master"
        output_path = workspace / "security_master.json"
        _write_stock_mapping_csv(
            stock_mapping_csv,
            [
                {
                    "stock_code": "US67066G1040",
                    "stock_name": "NVIDIA Corp",
                    "symbol": "NVDA",
                    "exchange": "NMS",
                    "updated_at": "2024-12-25 23:04",
                }
            ],
        )

        exit_code = await run_cli_async(
            [
                "import-security-master-seed",
                "--stock-mapping-csv",
                str(stock_mapping_csv),
                "--workspace",
                str(workspace),
                "--output-path",
                str(output_path),
            ],
            stdout=stdout,
            stderr=stderr,
        )

        payload = json.loads(stdout.getvalue())
        master = json.loads(output_path.read_text(encoding="utf-8"))

        assert exit_code == 0
        assert stderr.getvalue() == ""
        assert payload["schema_version"] == "agent_treport.security_master.import_result.v1"
        assert payload["auto_verified_count"] == 1
        assert payload["conflict_count"] == 0
        assert master["schema_version"] == "agent_treport.security_master.v1"
        assert master["entries"] == [
            {
                "security_id": "US67066G1040",
                "name": "NVIDIA Corp",
                "ticker": "NVDA",
                "exchange": "NMS",
                "status": "auto_verified",
                "confidence": "high",
                "security_classification": "ticker_candidate",
                "identifier_type": "isin",
                "sources": [
                    {
                        "source": "stock_mapping_csv",
                        "ticker": "NVDA",
                        "name": "NVIDIA Corp",
                        "exchange": "NMS",
                        "updated_at": "2024-12-25 23:04",
                    }
                ],
            }
        ]

    run_async(scenario())


def test_agent_treport_import_security_master_seed_keeps_existing_verified_entry(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        stock_mapping_csv = tmp_path / "stock_mapping.csv"
        workspace = tmp_path / "security-master"
        output_path = workspace / "security_master.json"
        workspace.mkdir()
        _write_json(
            output_path,
            {
                "schema_version": "agent_treport.security_master.v1",
                "entries": [
                    {
                        "security_id": "US67066G1040",
                        "name": "Operator NVIDIA",
                        "ticker": "NVDA",
                        "exchange": "operator",
                        "status": "verified",
                        "confidence": "high",
                        "security_classification": "ticker_candidate",
                        "identifier_type": "isin",
                        "sources": [{"source": "operator_review"}],
                    }
                ],
            },
        )
        _write_stock_mapping_csv(
            stock_mapping_csv,
            [
                {
                    "stock_code": "US67066G1040",
                    "stock_name": "NVIDIA Corp",
                    "symbol": "NVDA",
                    "exchange": "NMS",
                    "updated_at": "2024-12-25 23:04",
                }
            ],
        )

        exit_code = await run_cli_async(
            [
                "import-security-master-seed",
                "--stock-mapping-csv",
                str(stock_mapping_csv),
                "--workspace",
                str(workspace),
                "--output-path",
                str(output_path),
            ],
            stdout=stdout,
            stderr=stderr,
        )

        master = json.loads(output_path.read_text(encoding="utf-8"))

        assert exit_code == 0
        assert stderr.getvalue() == ""
        assert master["entries"][0]["name"] == "Operator NVIDIA"
        assert master["entries"][0]["status"] == "verified"
        assert master["entries"][0]["sources"] == [{"source": "operator_review"}]

    run_async(scenario())


def test_agent_treport_import_security_master_seed_writes_conflict_review_queue(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        stock_mapping_csv = tmp_path / "stock_mapping.csv"
        workspace = tmp_path / "security-master"
        output_path = workspace / "security_master.json"
        review_queue_path = workspace / "review_queue.json"
        workspace.mkdir()
        _write_json(
            output_path,
            {
                "schema_version": "agent_treport.security_master.v1",
                "entries": [
                    {
                        "security_id": "US67066G1040",
                        "name": "Operator NVIDIA",
                        "ticker": "NVDA",
                        "exchange": "operator",
                        "status": "auto_verified",
                        "confidence": "high",
                        "security_classification": "ticker_candidate",
                        "identifier_type": "isin",
                        "sources": [{"source": "previous_seed"}],
                    }
                ],
            },
        )
        _write_stock_mapping_csv(
            stock_mapping_csv,
            [
                {
                    "stock_code": "US67066G1040",
                    "stock_name": "NVIDIA Corp",
                    "symbol": "NVDA_ALT",
                    "exchange": "NMS",
                    "updated_at": "2024-12-25 23:04",
                }
            ],
        )

        exit_code = await run_cli_async(
            [
                "import-security-master-seed",
                "--stock-mapping-csv",
                str(stock_mapping_csv),
                "--workspace",
                str(workspace),
                "--output-path",
                str(output_path),
            ],
            stdout=stdout,
            stderr=stderr,
        )

        payload = json.loads(stdout.getvalue())
        master = json.loads(output_path.read_text(encoding="utf-8"))
        review_queue = json.loads(review_queue_path.read_text(encoding="utf-8"))

        assert exit_code == 0
        assert stderr.getvalue() == ""
        assert payload["conflict_count"] == 1
        assert master["entries"][0]["ticker"] == "NVDA"
        assert master["entries"][0]["status"] == "auto_verified"
        assert review_queue == {
            "schema_version": "agent_treport.security_master.review_queue.v1",
            "items": [
                {
                    "security_id": "US67066G1040",
                    "reason": "seed_mapping_conflict",
                    "existing_ticker": "NVDA",
                    "candidate_ticker": "NVDA_ALT",
                    "candidate_name": "NVIDIA Corp",
                    "candidate_exchange": "NMS",
                    "source": "stock_mapping_csv",
                }
            ],
        }

    run_async(scenario())


def test_agent_treport_export_security_resolution_excludes_unapproved_master_entries(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        master_path = tmp_path / "security_master.json"
        output_path = tmp_path / "security_resolution.json"
        _write_json(
            master_path,
            {
                "schema_version": "agent_treport.security_master.v1",
                "entries": [
                    {
                        "security_id": "US67066G1040",
                        "name": "NVIDIA Corp",
                        "ticker": "NVDA",
                        "exchange": "NMS",
                        "status": "verified",
                        "confidence": "high",
                        "security_classification": "ticker_candidate",
                        "identifier_type": "isin",
                        "security_group_id": "nvidia_common",
                        "listing_key": "XNAS:NVDA",
                        "security_group_name": "NVIDIA Common Stock",
                        "security_group_ticker": "NVDA",
                        "sources": [{"source": "operator_review"}],
                    },
                    {
                        "security_id": "US88160R1014",
                        "name": "Tesla Inc",
                        "ticker": "TSLA",
                        "exchange": "NMS",
                        "status": "auto_verified",
                        "confidence": "high",
                        "security_classification": "ticker_candidate",
                        "identifier_type": "isin",
                        "sources": [{"source": "stock_mapping_csv"}],
                    },
                    {
                        "security_id": "BOND00000001",
                        "name": "Corporate Bond",
                        "ticker": None,
                        "exchange": None,
                        "status": "excluded",
                        "confidence": "high",
                        "security_classification": "non_equity",
                        "identifier_type": "non_equity",
                        "sources": [{"source": "structural_rule"}],
                    },
                    {
                        "security_id": "SEC_PROPOSED",
                        "name": "Proposed Inc",
                        "ticker": "PROP",
                        "exchange": "NMS",
                        "status": "proposed",
                        "confidence": "medium",
                        "security_classification": "ticker_candidate",
                        "identifier_type": "unknown",
                        "sources": [{"source": "candidate"}],
                    },
                    {
                        "security_id": "SEC_REVIEW",
                        "name": "Review Inc",
                        "ticker": "REV",
                        "exchange": "NMS",
                        "status": "review_required",
                        "confidence": "medium",
                        "security_classification": "ticker_candidate",
                        "identifier_type": "unknown",
                        "sources": [{"source": "candidate"}],
                    },
                    {
                        "security_id": "SEC_UNRESOLVED",
                        "name": "Unresolved Inc",
                        "ticker": None,
                        "exchange": None,
                        "status": "unresolved",
                        "confidence": "low",
                        "security_classification": "unknown",
                        "identifier_type": "unknown",
                        "sources": [{"source": "observation"}],
                    },
                    {
                        "security_id": "SEC_CONFLICT",
                        "name": "Conflict Inc",
                        "ticker": "CNFL",
                        "exchange": "NMS",
                        "status": "conflict",
                        "confidence": "low",
                        "security_classification": "ticker_candidate",
                        "identifier_type": "unknown",
                        "sources": [{"source": "conflict"}],
                    },
                ],
            },
        )

        exit_code = await run_cli_async(
            [
                "export-security-resolution",
                "--security-master-path",
                str(master_path),
                "--output-path",
                str(output_path),
            ],
            stdout=stdout,
            stderr=stderr,
        )

        payload = json.loads(stdout.getvalue())
        export = json.loads(output_path.read_text(encoding="utf-8"))

        assert exit_code == 0
        assert stderr.getvalue() == ""
        assert payload["schema_version"] == (
            "agent_treport.security_resolution_export_result.v1"
        )
        assert payload["mapping_count"] == 2
        assert payload["exclusion_count"] == 1
        assert payload["suppressed_count"] == 4
        assert export == {
            "schema_version": "agent_treport.security_resolution_export.v1",
            "mappings": [
                {
                    "security_id": "US67066G1040",
                    "ticker": "NVDA",
                    "name": "NVIDIA Corp",
                    "exchange": "NMS",
                    "security_classification": "ticker_candidate",
                    "security_group_id": "nvidia_common",
                    "listing_key": "XNAS:NVDA",
                    "security_group_name": "NVIDIA Common Stock",
                    "security_group_ticker": "NVDA",
                },
                {
                    "security_id": "US88160R1014",
                    "ticker": "TSLA",
                    "name": "Tesla Inc",
                    "exchange": "NMS",
                    "security_classification": "ticker_candidate",
                },
            ],
            "exclusions": [
                {
                    "security_id": "BOND00000001",
                    "name": "Corporate Bond",
                    "security_classification": "non_equity",
                    "reason": "excluded",
                }
            ],
        }

    run_async(scenario())


def test_agent_treport_resolve_security_master_observes_holdings_and_writes_review_queue(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        holdings_path = _write_cli_normalized_holdings(
            tmp_path,
            {
                "2026-05-11": [
                    _cli_normalized_row(
                        security_id="US67066G1040",
                        name="NVIDIA From Holdings",
                        ticker="NVDX",
                        security_classification="ticker_candidate",
                    ),
                    _cli_normalized_row(
                        security_id="BOND00000001",
                        name="Corporate Bond",
                        ticker=None,
                        security_classification="non_equity",
                    ),
                    _cli_normalized_row(
                        security_id="US9999999999",
                        name="Unresolved Equity",
                        ticker=None,
                        security_classification="ticker_candidate",
                    ),
                ]
            },
        )
        master_path = tmp_path / "security_master.json"
        output_path = tmp_path / "security_master.resolved.json"
        review_queue_path = tmp_path / "review_queue.json"
        _write_json(
            master_path,
            {
                "schema_version": "agent_treport.security_master.v1",
                "entries": [
                    {
                        "security_id": "US67066G1040",
                        "name": "Operator NVIDIA",
                        "ticker": "NVDA",
                        "exchange": "operator",
                        "status": "verified",
                        "confidence": "high",
                        "security_classification": "ticker_candidate",
                        "identifier_type": "isin",
                        "sources": [{"source": "operator_review"}],
                    }
                ],
            },
        )

        exit_code = await run_cli_async(
            [
                "resolve-security-master",
                "--holdings-path",
                str(holdings_path),
                "--security-master-path",
                str(master_path),
                "--output-path",
                str(output_path),
                "--review-queue-path",
                str(review_queue_path),
                "--observed-partitions",
                "1",
                "--disable-openfigi-lookup",
            ],
            stdout=stdout,
            stderr=stderr,
        )

        payload = json.loads(stdout.getvalue())
        resolved = json.loads(output_path.read_text(encoding="utf-8"))
        review_queue = json.loads(review_queue_path.read_text(encoding="utf-8"))
        entries = {entry["security_id"]: entry for entry in resolved["entries"]}

        assert exit_code == 0
        assert stderr.getvalue() == ""
        assert payload["schema_version"] == "agent_treport.security_master.resolve_result.v1"
        assert payload["observed_security_count"] == 3
        assert payload["excluded_count"] == 1
        assert payload["unresolved_count"] == 1
        assert payload["conflict_count"] == 1
        assert entries["US67066G1040"]["ticker"] == "NVDA"
        assert entries["US67066G1040"]["name"] == "Operator NVIDIA"
        assert entries["BOND00000001"]["status"] == "excluded"
        assert entries["BOND00000001"]["security_classification"] == "non_equity"
        assert entries["US9999999999"]["status"] == "unresolved"
        assert entries["US9999999999"]["security_classification"] == "ticker_candidate"
        assert review_queue == {
            "schema_version": "agent_treport.security_master.review_queue.v1",
            "items": [
                {
                    "security_id": "US67066G1040",
                    "reason": "holding_ticker_conflict",
                    "existing_ticker": "NVDA",
                    "candidate_ticker": "NVDX",
                    "candidate_name": "NVIDIA From Holdings",
                    "source": "holdings_observation",
                },
                {
                    "security_id": "US9999999999",
                    "reason": "ticker_candidate_unresolved",
                    "candidate_name": "Unresolved Equity",
                    "source": "holdings_observation",
                },
            ],
        }

    run_async(scenario())


def test_agent_treport_resolve_security_master_auto_verifies_structural_equity_ids(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        holdings_path = _write_cli_normalized_holdings(
            tmp_path,
            {
                "2026-05-11": [
                    _cli_normalized_row(
                        security_id="005930",
                        name="Samsung Electronics",
                        ticker=None,
                        security_classification="ticker_candidate",
                    ),
                    _cli_normalized_row(
                        security_id="KR7005930003",
                        name="Samsung Electronics ISIN",
                        ticker=None,
                        security_classification="ticker_candidate",
                    ),
                    _cli_normalized_row(
                        security_id="AAPL US Equity",
                        name="Apple Inc",
                        ticker=None,
                        security_classification="ticker_candidate",
                    ),
                    _cli_normalized_row(
                        security_id="000338 C2 Equity",
                        name="Weichai Power Co Ltd",
                        ticker=None,
                        security_classification="ticker_candidate",
                    ),
                ]
            },
        )
        master_path = tmp_path / "security_master.json"
        output_path = tmp_path / "security_master.resolved.json"
        review_queue_path = tmp_path / "review_queue.json"
        _write_json(
            master_path,
            {"schema_version": "agent_treport.security_master.v1", "entries": []},
        )

        exit_code = await run_cli_async(
            [
                "resolve-security-master",
                "--holdings-path",
                str(holdings_path),
                "--security-master-path",
                str(master_path),
                "--output-path",
                str(output_path),
                "--review-queue-path",
                str(review_queue_path),
                "--observed-partitions",
                "1",
                "--disable-openfigi-lookup",
            ],
            stdout=stdout,
            stderr=stderr,
        )

        payload = json.loads(stdout.getvalue())
        resolved = json.loads(output_path.read_text(encoding="utf-8"))
        review_queue = json.loads(review_queue_path.read_text(encoding="utf-8"))
        entries = {entry["security_id"]: entry for entry in resolved["entries"]}

        assert exit_code == 0
        assert stderr.getvalue() == ""
        assert payload["auto_verified_count"] == 4
        assert payload["unresolved_count"] == 0
        assert entries["005930"]["status"] == "auto_verified"
        assert entries["005930"]["ticker"] == "005930"
        assert entries["005930"]["exchange"] == "KRX"
        assert entries["005930"]["identifier_type"] == "krx_code"
        assert entries["KR7005930003"]["status"] == "auto_verified"
        assert entries["KR7005930003"]["ticker"] == "005930"
        assert entries["KR7005930003"]["exchange"] == "KRX"
        assert entries["KR7005930003"]["identifier_type"] == "isin"
        assert entries["AAPL US Equity"]["status"] == "auto_verified"
        assert entries["AAPL US Equity"]["ticker"] == "AAPL"
        assert entries["AAPL US Equity"]["exchange"] == "US"
        assert entries["AAPL US Equity"]["identifier_type"] == "bloomberg_equity_code"
        assert entries["000338 C2 Equity"]["status"] == "auto_verified"
        assert entries["000338 C2 Equity"]["ticker"] == "000338"
        assert entries["000338 C2 Equity"]["exchange"] == "C2"
        assert entries["000338 C2 Equity"]["identifier_type"] == "bloomberg_equity_code"
        assert review_queue["items"] == []

    run_async(scenario())


def test_agent_treport_resolve_security_master_openfigi_auto_verifies_unambiguous_equity(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        holdings_path = _write_cli_normalized_holdings(
            tmp_path,
            {
                "2026-05-11": [
                    _cli_normalized_row(
                        security_id="US9999999999",
                        name="OpenFIGI Equity",
                        ticker=None,
                        security_classification="ticker_candidate",
                    )
                ]
            },
        )
        master_path = tmp_path / "security_master.json"
        output_path = tmp_path / "security_master.resolved.json"
        review_queue_path = tmp_path / "review_queue.json"
        _write_json(
            master_path,
            {"schema_version": "agent_treport.security_master.v1", "entries": []},
        )
        fake_client = _FakeOpenFigiClient(
            [
                [
                    {
                        "data": [
                            {
                                "ticker": "FIGI",
                                "name": "OpenFIGI Equity",
                                "exchCode": "US",
                                "marketSector": "Equity",
                                "securityType": "Common Stock",
                                "securityType2": "Common Stock",
                            }
                        ]
                    }
                ]
            ]
        )

        exit_code = await run_cli_async(
            [
                "resolve-security-master",
                "--holdings-path",
                str(holdings_path),
                "--security-master-path",
                str(master_path),
                "--output-path",
                str(output_path),
                "--review-queue-path",
                str(review_queue_path),
                "--observed-partitions",
                "1",
            ],
            stdout=stdout,
            stderr=stderr,
            openfigi_client_factory=lambda: fake_client,
        )

        payload = json.loads(stdout.getvalue())
        resolved = json.loads(output_path.read_text(encoding="utf-8"))
        review_queue = json.loads(review_queue_path.read_text(encoding="utf-8"))

        assert exit_code == 0
        assert stderr.getvalue() == ""
        assert len(fake_client.calls) == 1
        assert fake_client.calls[0] == [{"idType": "ID_ISIN", "idValue": "US9999999999"}]
        assert payload["openfigi_lookup_enabled"] is True
        assert payload["openfigi_lookup_count"] == 1
        assert payload["warnings"] == []
        assert resolved["entries"][0]["status"] == "auto_verified"
        assert resolved["entries"][0]["ticker"] == "FIGI"
        assert resolved["entries"][0]["sources"][0]["source"] == "holdings_observation"
        assert review_queue["items"] == []

    run_async(scenario())


def test_agent_treport_resolve_security_master_retries_existing_unresolved_with_openfigi(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        holdings_path = _write_cli_normalized_holdings(
            tmp_path,
            {
                "2026-05-11": [
                    _cli_normalized_row(
                        security_id="US9999999999",
                        name="Existing Unresolved Equity",
                        ticker=None,
                        security_classification="ticker_candidate",
                    )
                ]
            },
        )
        master_path = tmp_path / "security_master.resolved.json"
        output_path = tmp_path / "security_master.next.json"
        review_queue_path = tmp_path / "review_queue.json"
        _write_json(
            master_path,
            {
                "schema_version": "agent_treport.security_master.v1",
                "entries": [
                    {
                        "security_id": "US9999999999",
                        "name": "Existing Unresolved Equity",
                        "ticker": None,
                        "exchange": None,
                        "status": "unresolved",
                        "confidence": "low",
                        "security_classification": "ticker_candidate",
                        "identifier_type": "isin",
                        "sources": [{"source": "holdings_observation"}],
                    }
                ],
            },
        )
        fake_client = _FakeOpenFigiClient(
            [
                [
                    {
                        "data": [
                            {
                                "ticker": "FIGI",
                                "name": "Existing Unresolved Equity",
                                "exchCode": "US",
                                "marketSector": "Equity",
                                "securityType": "Common Stock",
                                "securityType2": "Common Stock",
                            }
                        ]
                    }
                ]
            ]
        )

        exit_code = await run_cli_async(
            [
                "resolve-security-master",
                "--holdings-path",
                str(holdings_path),
                "--security-master-path",
                str(master_path),
                "--output-path",
                str(output_path),
                "--review-queue-path",
                str(review_queue_path),
                "--observed-partitions",
                "1",
            ],
            stdout=stdout,
            stderr=stderr,
            openfigi_client_factory=lambda: fake_client,
        )

        payload = json.loads(stdout.getvalue())
        resolved = json.loads(output_path.read_text(encoding="utf-8"))
        review_queue = json.loads(review_queue_path.read_text(encoding="utf-8"))

        assert exit_code == 0
        assert stderr.getvalue() == ""
        assert fake_client.calls == [[{"idType": "ID_ISIN", "idValue": "US9999999999"}]]
        assert payload["auto_verified_count"] == 1
        assert payload["unresolved_count"] == 0
        assert resolved["entries"][0]["status"] == "auto_verified"
        assert resolved["entries"][0]["ticker"] == "FIGI"
        assert review_queue["items"] == []

    run_async(scenario())


def test_agent_treport_resolve_security_master_openfigi_auto_verifies_primary_us_exchange(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        holdings_path = _write_cli_normalized_holdings(
            tmp_path,
            {
                "2026-05-11": [
                    _cli_normalized_row(
                        security_id="US9999999999",
                        name="US Multi Venue Equity",
                        ticker=None,
                        security_classification="ticker_candidate",
                    )
                ]
            },
        )
        master_path = tmp_path / "security_master.json"
        output_path = tmp_path / "security_master.resolved.json"
        review_queue_path = tmp_path / "review_queue.json"
        _write_json(
            master_path,
            {"schema_version": "agent_treport.security_master.v1", "entries": []},
        )
        fake_client = _FakeOpenFigiClient(
            [
                [
                    {
                        "data": [
                            {
                                "ticker": "LITE",
                                "name": "US Multi Venue Equity",
                                "exchCode": "US",
                                "marketSector": "Equity",
                                "securityType": "Common Stock",
                                "securityType2": "Common Stock",
                            },
                            {
                                "ticker": "LU2",
                                "name": "US Multi Venue Equity",
                                "exchCode": "GR",
                                "marketSector": "Equity",
                                "securityType": "Common Stock",
                                "securityType2": "Common Stock",
                            },
                        ]
                    }
                ]
            ]
        )

        exit_code = await run_cli_async(
            [
                "resolve-security-master",
                "--holdings-path",
                str(holdings_path),
                "--security-master-path",
                str(master_path),
                "--output-path",
                str(output_path),
                "--review-queue-path",
                str(review_queue_path),
                "--observed-partitions",
                "1",
            ],
            stdout=stdout,
            stderr=stderr,
            openfigi_client_factory=lambda: fake_client,
        )

        resolved = json.loads(output_path.read_text(encoding="utf-8"))
        review_queue = json.loads(review_queue_path.read_text(encoding="utf-8"))

        assert exit_code == 0
        assert stderr.getvalue() == ""
        assert resolved["entries"][0]["status"] == "auto_verified"
        assert resolved["entries"][0]["ticker"] == "LITE"
        assert resolved["entries"][0]["exchange"] == "US"
        assert review_queue["items"] == []

    run_async(scenario())


def test_agent_treport_resolve_security_master_openfigi_respects_client_job_limit(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        rows = [
            _cli_normalized_row(
                security_id="KR7005930003",
                name="Samsung Electronics ISIN",
                ticker=None,
                security_classification="ticker_candidate",
            ),
        ] + [
            _cli_normalized_row(
                security_id=f"US{i:010d}",
                name=f"Unresolved ISIN {i}",
                ticker=None,
                security_classification="ticker_candidate",
            )
            for i in range(11)
        ]
        holdings_path = _write_cli_normalized_holdings(tmp_path, {"2026-05-11": rows})
        master_path = tmp_path / "security_master.json"
        output_path = tmp_path / "security_master.resolved.json"
        review_queue_path = tmp_path / "review_queue.json"
        _write_json(
            master_path,
            {"schema_version": "agent_treport.security_master.v1", "entries": []},
        )
        fake_client = _FakeOpenFigiClient(
            [
                [{} for _ in range(10)],
                [{}],
            ]
        )
        fake_client.max_jobs_per_request = 10

        exit_code = await run_cli_async(
            [
                "resolve-security-master",
                "--holdings-path",
                str(holdings_path),
                "--security-master-path",
                str(master_path),
                "--output-path",
                str(output_path),
                "--review-queue-path",
                str(review_queue_path),
                "--observed-partitions",
                "1",
            ],
            stdout=stdout,
            stderr=stderr,
            openfigi_client_factory=lambda: fake_client,
        )

        payload = json.loads(stdout.getvalue())

        assert exit_code == 0
        assert stderr.getvalue() == ""
        assert [len(call) for call in fake_client.calls] == [10, 1]
        assert all(job["idValue"] != "KR7005930003" for call in fake_client.calls for job in call)
        assert payload["warnings"] == []

    run_async(scenario())


def test_agent_treport_resolve_security_master_openfigi_429_stops_further_calls(
    tmp_path: Path,
) -> None:
    from agent_treport.signal_report.adapters.openfigi import OpenFigiRateLimitError

    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        rows = [
            _cli_normalized_row(
                security_id=f"US{i:010d}",
                name=f"Unresolved Equity {i}",
                ticker=None,
                security_classification="ticker_candidate",
            )
            for i in range(51)
        ]
        holdings_path = _write_cli_normalized_holdings(tmp_path, {"2026-05-11": rows})
        master_path = tmp_path / "security_master.json"
        output_path = tmp_path / "security_master.resolved.json"
        review_queue_path = tmp_path / "review_queue.json"
        _write_json(
            master_path,
            {"schema_version": "agent_treport.security_master.v1", "entries": []},
        )
        fake_client = _FakeOpenFigiClient([OpenFigiRateLimitError("rate limited")])

        exit_code = await run_cli_async(
            [
                "resolve-security-master",
                "--holdings-path",
                str(holdings_path),
                "--security-master-path",
                str(master_path),
                "--output-path",
                str(output_path),
                "--review-queue-path",
                str(review_queue_path),
                "--observed-partitions",
                "1",
            ],
            stdout=stdout,
            stderr=stderr,
            openfigi_client_factory=lambda: fake_client,
        )

        payload = json.loads(stdout.getvalue())
        review_queue = json.loads(review_queue_path.read_text(encoding="utf-8"))

        assert exit_code == 0
        assert stderr.getvalue() == ""
        assert len(fake_client.calls) == 1
        assert len(fake_client.calls[0]) == 50
        assert payload["openfigi_lookup_count"] == 0
        assert payload["warnings"] == [
            {
                "code": "openfigi_rate_limited",
                "message": "OpenFIGI rate limit reached; lookup stopped for this run.",
            }
        ]
        assert len(review_queue["items"]) == 51

    run_async(scenario())


def test_agent_treport_resolve_security_master_openfigi_request_error_records_warning(
    tmp_path: Path,
) -> None:
    from agent_treport.signal_report.adapters.openfigi import OpenFigiRequestError

    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        holdings_path = _write_cli_normalized_holdings(
            tmp_path,
            {
                "2026-05-11": [
                    _cli_normalized_row(
                        security_id="US9999999999",
                        name="Unresolved Equity",
                        ticker=None,
                        security_classification="ticker_candidate",
                    )
                ]
            },
        )
        master_path = tmp_path / "security_master.json"
        output_path = tmp_path / "security_master.resolved.json"
        review_queue_path = tmp_path / "review_queue.json"
        _write_json(
            master_path,
            {"schema_version": "agent_treport.security_master.v1", "entries": []},
        )
        fake_client = _FakeOpenFigiClient([OpenFigiRequestError("network failed")])

        exit_code = await run_cli_async(
            [
                "resolve-security-master",
                "--holdings-path",
                str(holdings_path),
                "--security-master-path",
                str(master_path),
                "--output-path",
                str(output_path),
                "--review-queue-path",
                str(review_queue_path),
                "--observed-partitions",
                "1",
            ],
            stdout=stdout,
            stderr=stderr,
            openfigi_client_factory=lambda: fake_client,
        )

        payload = json.loads(stdout.getvalue())
        review_queue = json.loads(review_queue_path.read_text(encoding="utf-8"))

        assert exit_code == 0
        assert stderr.getvalue() == ""
        assert payload["warnings"] == [
            {
                "code": "openfigi_request_failed",
                "message": "OpenFIGI lookup failed; lookup stopped for this run.",
            }
        ]
        assert len(review_queue["items"]) == 1

    run_async(scenario())


def test_agent_treport_apply_security_mapping_patch_writes_merged_mapping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        mapping_path = tmp_path / "security_mapping.json"
        patch_path = tmp_path / "patch.json"
        output_path = tmp_path / "merged.json"
        monkeypatch.chdir(tmp_path)
        _write_json(
            mapping_path,
            {
                "schema_version": "agent_treport.security_mapping.v1",
                "mappings": [
                    {"security_id": "SEC_B", "ticker": "Beta"},
                    {"security_id": "SEC_C", "ticker": "Same"},
                ],
            },
        )
        _write_json(
            patch_path,
            {
                "schema_version": "agent_treport.security_mapping.patch.v1",
                "mappings": [
                    {"security_id": " SEC_A ", "ticker": " nVdA "},
                    {"security_id": "SEC_C", "ticker": "Same"},
                ],
            },
        )

        exit_code = await run_cli_async(
            [
                "apply-security-mapping-patch",
                "--security-mapping-path",
                "security_mapping.json",
                "--patch-path",
                "patch.json",
                "--output-path",
                "merged.json",
            ],
            stdout=stdout,
            stderr=stderr,
        )

        assert exit_code == 0
        assert stderr.getvalue() == ""
        assert json.loads(stdout.getvalue()) == {
            "schema_version": "agent_treport.security_mapping.patch_apply_result.v1",
            "status": "succeeded",
            "security_mapping_path": "security_mapping.json",
            "patch_path": "patch.json",
            "output_path": "merged.json",
            "added_mapping_count": 1,
            "replaced_mapping_count": 0,
            "unchanged_mapping_count": 1,
            "total_mapping_count": 3,
        }
        assert stdout.getvalue().count("\n") == 1
        assert output_path.read_text(encoding="utf-8").endswith("\n")
        assert json.loads(output_path.read_text(encoding="utf-8")) == {
            "schema_version": "agent_treport.security_mapping.v1",
            "mappings": [
                {"security_id": "SEC_A", "ticker": "nVdA"},
                {"security_id": "SEC_B", "ticker": "Beta"},
                {"security_id": "SEC_C", "ticker": "Same"},
            ],
        }

    run_async(scenario())


def test_agent_treport_apply_security_mapping_patch_stdout_schema_ignores_summary_extras(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        mapping_path = tmp_path / "security_mapping.json"
        patch_path = tmp_path / "patch.json"
        output_path = tmp_path / "merged.json"
        _write_json(
            mapping_path,
            {
                "schema_version": "agent_treport.security_mapping.v1",
                "mappings": [{"security_id": "SEC_A", "ticker": "AAA"}],
            },
        )
        _write_json(
            patch_path,
            {
                "schema_version": "agent_treport.security_mapping.patch.v1",
                "mappings": [{"security_id": "SEC_B", "ticker": "BBB"}],
            },
        )

        def merge_with_extra_summary_keys(
            existing_mapping: object,
            patch: object,
            *,
            allow_replacements: bool = False,
        ) -> tuple[dict[str, object], dict[str, object]]:
            assert existing_mapping == {"SEC_A": "AAA"}
            assert allow_replacements is False
            assert isinstance(patch, dict)
            return (
                {
                    "schema_version": "agent_treport.security_mapping.v1",
                    "mappings": [
                        {"security_id": "SEC_A", "ticker": "AAA"},
                        {"security_id": "SEC_B", "ticker": "BBB"},
                    ],
                },
                {
                    "added_mapping_count": 1,
                    "replaced_mapping_count": 0,
                    "unchanged_mapping_count": 0,
                    "total_mapping_count": 2,
                    "summary": {"leak": True},
                    "status": "overridden",
                    "security_mapping_path": "overridden.json",
                    "extra_count": 999,
                },
            )

        monkeypatch.setattr(
            treport_cli,
            "merge_security_mapping_patch",
            merge_with_extra_summary_keys,
        )

        exit_code = await run_cli_async(
            [
                "apply-security-mapping-patch",
                "--security-mapping-path",
                str(mapping_path),
                "--patch-path",
                str(patch_path),
                "--output-path",
                str(output_path),
            ],
            stdout=stdout,
            stderr=stderr,
        )

        assert exit_code == 0
        assert stderr.getvalue() == ""
        assert json.loads(stdout.getvalue()) == {
            "schema_version": "agent_treport.security_mapping.patch_apply_result.v1",
            "status": "succeeded",
            "security_mapping_path": str(mapping_path),
            "patch_path": str(patch_path),
            "output_path": str(output_path),
            "added_mapping_count": 1,
            "replaced_mapping_count": 0,
            "unchanged_mapping_count": 0,
            "total_mapping_count": 2,
        }

    run_async(scenario())


def test_agent_treport_apply_security_mapping_patch_rejects_existing_output_without_overwrite(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        mapping_path = tmp_path / "security_mapping.json"
        patch_path = tmp_path / "patch.json"
        output_path = tmp_path / "merged.json"
        _write_json(
            mapping_path,
            {
                "schema_version": "agent_treport.security_mapping.v1",
                "mappings": [{"security_id": "SEC_A", "ticker": "AAA"}],
            },
        )
        _write_json(
            patch_path,
            {
                "schema_version": "agent_treport.security_mapping.patch.v1",
                "mappings": [{"security_id": "SEC_B", "ticker": "BBB"}],
            },
        )
        output_path.write_text("do not replace", encoding="utf-8")

        exit_code = await run_cli_async(
            [
                "apply-security-mapping-patch",
                "--security-mapping-path",
                str(mapping_path),
                "--patch-path",
                str(patch_path),
                "--output-path",
                str(output_path),
            ],
            stdout=stdout,
            stderr=stderr,
        )

        assert exit_code == 2
        assert stdout.getvalue() == ""
        assert "output path already exists" in stderr.getvalue()
        assert stderr.getvalue().startswith("agent-treport: error: ")
        assert output_path.read_text(encoding="utf-8") == "do not replace"

    run_async(scenario())


def test_agent_treport_apply_security_mapping_patch_overwrites_existing_output_when_requested(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        mapping_path = tmp_path / "security_mapping.json"
        patch_path = tmp_path / "patch.json"
        output_path = tmp_path / "merged.json"
        _write_json(
            mapping_path,
            {
                "schema_version": "agent_treport.security_mapping.v1",
                "mappings": [{"security_id": "SEC_A", "ticker": "AAA"}],
            },
        )
        _write_json(
            patch_path,
            {
                "schema_version": "agent_treport.security_mapping.patch.v1",
                "mappings": [{"security_id": "SEC_B", "ticker": "BBB"}],
            },
        )
        output_path.write_text("replace me", encoding="utf-8")

        exit_code = await run_cli_async(
            [
                "apply-security-mapping-patch",
                "--security-mapping-path",
                str(mapping_path),
                "--patch-path",
                str(patch_path),
                "--output-path",
                str(output_path),
                "--overwrite",
            ],
            stdout=stdout,
            stderr=stderr,
        )

        assert exit_code == 0
        assert stderr.getvalue() == ""
        assert json.loads(stdout.getvalue())["added_mapping_count"] == 1
        assert json.loads(output_path.read_text(encoding="utf-8"))["mappings"] == [
            {"security_id": "SEC_A", "ticker": "AAA"},
            {"security_id": "SEC_B", "ticker": "BBB"},
        ]

    run_async(scenario())


def test_agent_treport_apply_security_mapping_patch_blocks_replacement_without_values(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        mapping_path = tmp_path / "security_mapping.json"
        patch_path = tmp_path / "patch.json"
        output_path = tmp_path / "merged.json"
        _write_json(
            mapping_path,
            {
                "schema_version": "agent_treport.security_mapping.v1",
                "mappings": [{"security_id": "SEC_A", "ticker": "OLD"}],
            },
        )
        _write_json(
            patch_path,
            {
                "schema_version": "agent_treport.security_mapping.patch.v1",
                "mappings": [{"security_id": "SEC_A", "ticker": "NEW"}],
            },
        )

        exit_code = await run_cli_async(
            [
                "apply-security-mapping-patch",
                "--security-mapping-path",
                str(mapping_path),
                "--patch-path",
                str(patch_path),
                "--output-path",
                str(output_path),
            ],
            stdout=stdout,
            stderr=stderr,
        )

        assert exit_code == 2
        assert stdout.getvalue() == ""
        assert "SEC_A" in stderr.getvalue()
        assert "existing mapping conflict" in stderr.getvalue()
        assert "OLD" not in stderr.getvalue()
        assert "NEW" not in stderr.getvalue()
        assert not output_path.exists()

    run_async(scenario())


def test_agent_treport_apply_security_mapping_patch_allows_replacement_when_requested(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        mapping_path = tmp_path / "security_mapping.json"
        patch_path = tmp_path / "patch.json"
        output_path = tmp_path / "merged.json"
        _write_json(
            mapping_path,
            {
                "schema_version": "agent_treport.security_mapping.v1",
                "mappings": [{"security_id": "SEC_A", "ticker": "OLD"}],
            },
        )
        _write_json(
            patch_path,
            {
                "schema_version": "agent_treport.security_mapping.patch.v1",
                "mappings": [{"security_id": "SEC_A", "ticker": "NEW"}],
            },
        )

        exit_code = await run_cli_async(
            [
                "apply-security-mapping-patch",
                "--security-mapping-path",
                str(mapping_path),
                "--patch-path",
                str(patch_path),
                "--output-path",
                str(output_path),
                "--allow-replacements",
            ],
            stdout=stdout,
            stderr=stderr,
        )

        assert exit_code == 0
        assert stderr.getvalue() == ""
        assert json.loads(stdout.getvalue()) == {
            "schema_version": "agent_treport.security_mapping.patch_apply_result.v1",
            "status": "succeeded",
            "security_mapping_path": str(mapping_path),
            "patch_path": str(patch_path),
            "output_path": str(output_path),
            "added_mapping_count": 0,
            "replaced_mapping_count": 1,
            "unchanged_mapping_count": 0,
            "total_mapping_count": 1,
        }
        assert json.loads(output_path.read_text(encoding="utf-8"))["mappings"] == [
            {"security_id": "SEC_A", "ticker": "NEW"}
        ]

    run_async(scenario())


def test_agent_treport_apply_security_mapping_patch_rejects_output_equal_to_patch_path(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        mapping_path = tmp_path / "security_mapping.json"
        patch_path = tmp_path / "patch.json"
        _write_json(
            mapping_path,
            {
                "schema_version": "agent_treport.security_mapping.v1",
                "mappings": [{"security_id": "SEC_A", "ticker": "AAA"}],
            },
        )
        patch_text = json.dumps(
            {
                "schema_version": "agent_treport.security_mapping.patch.v1",
                "mappings": [{"security_id": "SEC_B", "ticker": "BBB"}],
            },
            ensure_ascii=False,
        )
        patch_path.write_text(patch_text, encoding="utf-8")

        exit_code = await run_cli_async(
            [
                "apply-security-mapping-patch",
                "--security-mapping-path",
                str(mapping_path),
                "--patch-path",
                str(patch_path),
                "--output-path",
                str(patch_path),
                "--overwrite",
            ],
            stdout=stdout,
            stderr=stderr,
        )

        assert exit_code == 2
        assert stdout.getvalue() == ""
        assert "output path must not equal patch path" in stderr.getvalue()
        assert patch_path.read_text(encoding="utf-8") == patch_text

    run_async(scenario())


def test_agent_treport_apply_security_mapping_patch_can_overwrite_security_mapping_path(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        mapping_path = tmp_path / "security_mapping.json"
        patch_path = tmp_path / "patch.json"
        _write_json(
            mapping_path,
            {
                "schema_version": "agent_treport.security_mapping.v1",
                "mappings": [{"security_id": "SEC_A", "ticker": "AAA"}],
            },
        )
        _write_json(
            patch_path,
            {
                "schema_version": "agent_treport.security_mapping.patch.v1",
                "mappings": [{"security_id": "SEC_B", "ticker": "BBB"}],
            },
        )

        exit_code = await run_cli_async(
            [
                "apply-security-mapping-patch",
                "--security-mapping-path",
                str(mapping_path),
                "--patch-path",
                str(patch_path),
                "--output-path",
                str(mapping_path),
                "--overwrite",
            ],
            stdout=stdout,
            stderr=stderr,
        )

        assert exit_code == 0
        assert stderr.getvalue() == ""
        assert json.loads(stdout.getvalue())["added_mapping_count"] == 1
        assert json.loads(mapping_path.read_text(encoding="utf-8"))["mappings"] == [
            {"security_id": "SEC_A", "ticker": "AAA"},
            {"security_id": "SEC_B", "ticker": "BBB"},
        ]

    run_async(scenario())


def test_agent_treport_apply_security_mapping_patch_rejects_missing_output_parent(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        mapping_path = tmp_path / "security_mapping.json"
        patch_path = tmp_path / "patch.json"
        _write_json(
            mapping_path,
            {
                "schema_version": "agent_treport.security_mapping.v1",
                "mappings": [{"security_id": "SEC_A", "ticker": "AAA"}],
            },
        )
        _write_json(
            patch_path,
            {
                "schema_version": "agent_treport.security_mapping.patch.v1",
                "mappings": [{"security_id": "SEC_B", "ticker": "BBB"}],
            },
        )

        exit_code = await run_cli_async(
            [
                "apply-security-mapping-patch",
                "--security-mapping-path",
                str(mapping_path),
                "--patch-path",
                str(patch_path),
                "--output-path",
                str(tmp_path / "missing" / "merged.json"),
            ],
            stdout=stdout,
            stderr=stderr,
        )

        assert exit_code == 2
        assert stdout.getvalue() == ""
        assert "output parent directory does not exist" in stderr.getvalue()

    run_async(scenario())


def test_agent_treport_apply_security_mapping_patch_write_failure_is_sanitized(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        mapping_path = tmp_path / "security_mapping.json"
        patch_path = tmp_path / "patch.json"
        directory_output_path = tmp_path / "directory-output"
        directory_output_path.mkdir()
        _write_json(
            mapping_path,
            {
                "schema_version": "agent_treport.security_mapping.v1",
                "mappings": [{"security_id": "SEC_A", "ticker": "AAA"}],
            },
        )
        _write_json(
            patch_path,
            {
                "schema_version": "agent_treport.security_mapping.patch.v1",
                "mappings": [{"security_id": "SEC_B", "ticker": "BBB"}],
            },
        )

        exit_code = await run_cli_async(
            [
                "apply-security-mapping-patch",
                "--security-mapping-path",
                str(mapping_path),
                "--patch-path",
                str(patch_path),
                "--output-path",
                str(directory_output_path),
                "--overwrite",
            ],
            stdout=stdout,
            stderr=stderr,
        )

        payload = json.loads(stderr.getvalue())
        assert exit_code == 1
        assert stdout.getvalue() == ""
        assert payload["reason"] == "security_mapping_patch_apply_failed"
        assert payload["error"]["code"] == "security_mapping_patch_apply_failed"
        assert payload["error"]["message"] == "operation failed"

    run_async(scenario())


def test_agent_treport_propose_security_mapping_recovery_writes_validated_proposal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        sync_metadata_path = tmp_path / "sync_metadata.json"
        output_path = tmp_path / "proposal.json"
        monkeypatch.chdir(tmp_path)
        model_calls: list[ModelProviderConfig] = []
        model = FakeModelClient(
            [
                ModelResponse(
                    message=Message(
                        role="assistant",
                        content=(
                            TextBlock(
                                text=json.dumps(
                                    {
                                        "proposals": [
                                            {
                                                "security_id": "SEC_B",
                                                "name": "Beta Inc.",
                                                "proposed_ticker": None,
                                                "status": "unresolved",
                                                "confidence": "low",
                                                "rationale": "No deterministic ticker.",
                                            },
                                            {
                                                "security_id": "SEC_A",
                                                "name": "Alpha Corp.",
                                                "proposed_ticker": " NvDa ",
                                                "status": "proposed",
                                                "confidence": "high",
                                                "rationale": "Name matches listed issuer.",
                                            },
                                        ]
                                    }
                                )
                            ),
                        ),
                    )
                )
            ]
        )
        _write_json(
            sync_metadata_path,
            {
                "schema_version": "agent_treport.operational_holdings.sync_metadata.v1",
                "source_manifest_path": str(tmp_path / "must-not-leak.json"),
                "security_mapping_path": str(tmp_path / "mapping-must-not-leak.json"),
                "unmapped_security_samples": [
                    {
                        "security_id": "SEC_A",
                        "name": "Alpha Corp.",
                        "observed_row_count": 3,
                        "observed_etf_count": 2,
                        "observed_date_count": 2,
                        "name_alias_count": 0,
                    },
                    {
                        "security_id": "SEC_B",
                        "name": "Beta Inc.",
                        "observed_row_count": 1,
                        "observed_etf_count": 1,
                        "observed_date_count": 1,
                        "name_alias_count": 1,
                    },
                ],
            },
        )

        exit_code = await run_cli_async(
            [
                "propose-security-mapping-recovery",
                "--sync-metadata-path",
                "sync_metadata.json",
                "--model",
                "codex",
                "--codex-model",
                "gpt-test",
                "--model-timeout-seconds",
                "7.5",
                "--output-path",
                "proposal.json",
            ],
            stdout=stdout,
            stderr=stderr,
            model_client_factory=lambda config: model_calls.append(config) or model,
        )

        request = model.requests[0]
        rendered_prompt = "\n".join(
            block.text
            for message in request.messages
            for block in message.content
            if isinstance(block, TextBlock)
        )

        assert exit_code == 0
        assert stderr.getvalue() == ""
        assert model_calls == [
            ModelProviderConfig(provider="codex", model="gpt-test", timeout_seconds=7.5)
        ]
        assert [message.role for message in request.messages] == ["system", "user"]
        assert all(len(message.content) == 1 for message in request.messages)
        assert "SEC_A" in rendered_prompt
        assert "Alpha Corp." in rendered_prompt
        assert "observed_row_count" in rendered_prompt
        assert "must-not-leak" not in rendered_prompt
        assert str(tmp_path) not in rendered_prompt
        assert json.loads(stdout.getvalue()) == {
            "schema_version": (
                "agent_treport.security_mapping.recovery_proposal_result.v1"
            ),
            "status": "succeeded",
            "sync_metadata_path": "sync_metadata.json",
            "output_path": "proposal.json",
            "sample_count": 2,
            "proposal_count": 2,
            "proposed_count": 1,
            "unresolved_count": 1,
            "model_called": True,
        }
        assert stdout.getvalue().count("\n") == 1
        assert json.loads(output_path.read_text(encoding="utf-8")) == {
            "schema_version": "agent_treport.security_mapping.recovery_proposal.v1",
            "source_sync_metadata_path": "sync_metadata.json",
            "proposals": [
                {
                    "security_id": "SEC_A",
                    "name": "Alpha Corp.",
                    "proposed_ticker": "NvDa",
                    "status": "proposed",
                    "confidence": "high",
                    "rationale": "Name matches listed issuer.",
                },
                {
                    "security_id": "SEC_B",
                    "name": "Beta Inc.",
                    "proposed_ticker": None,
                    "status": "unresolved",
                    "confidence": "low",
                    "rationale": "No deterministic ticker.",
                },
            ],
        }

    run_async(scenario())


def test_agent_treport_propose_security_mapping_recovery_accepts_collection_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        collection_summary_path = tmp_path / "collection_summary.json"
        output_path = tmp_path / "proposal.json"
        monkeypatch.chdir(tmp_path)
        model_calls: list[ModelProviderConfig] = []
        model = FakeModelClient(
            [
                ModelResponse(
                    message=Message(
                        role="assistant",
                        content=(
                            TextBlock(
                                text=json.dumps(
                                    {
                                        "proposals": [
                                            {
                                                "security_id": "SEC_A",
                                                "name": "Alpha Corp.",
                                                "proposed_ticker": "ALPH",
                                                "status": "proposed",
                                                "confidence": "medium",
                                                "rationale": "Aggregate name match.",
                                            },
                                            {
                                                "security_id": "SEC_UNKNOWN",
                                                "name": "Unknown Instrument",
                                                "proposed_ticker": None,
                                                "status": "unresolved",
                                                "confidence": "low",
                                                "rationale": "Unknown classification.",
                                            },
                                        ]
                                    }
                                )
                            ),
                        ),
                    )
                )
            ]
        )
        _write_json(
            collection_summary_path,
            {
                "schema_version": "agent_treport.native_collection.summary.v1",
                "collection_source_type": "native_history",
                "observed_dates": ["2026-05-11", "2026-05-08"],
                "active_etf_coverage": {
                    "missing_active_etf_ids": ["etf_focus_ai"],
                },
                "normalized_output": {
                    "manifest_path": str(tmp_path / "must-not-leak.json"),
                    "fingerprint": {"sha256": "abc"},
                },
                "security_coverage": {
                    "security_resolution_available": True,
                    "mapped_ticker_candidate_count": 1,
                    "unresolved_ticker_candidate_count": 1,
                    "unknown_count": 1,
                    "non_ticker_excluded_count": 0,
                    "reviewed_mapping_applied_count": 0,
                    "reviewed_exclusion_applied_count": 0,
                    "ticker_mapping_coverage_ratio": 0.5,
                    "recovery_sample_count": 2,
                    "recovery_samples": [
                        {
                            "security_id": "SEC_A",
                            "name": "Alpha Corp.",
                            "observed_row_count": 3,
                            "observed_etf_count": 2,
                            "observed_date_count": 2,
                            "name_alias_count": 0,
                        },
                        {
                            "security_id": "SEC_UNKNOWN",
                            "name": "Unknown Instrument",
                            "observed_row_count": 1,
                            "observed_etf_count": 1,
                            "observed_date_count": 1,
                            "name_alias_count": 0,
                            "security_classification": "unknown",
                        },
                    ],
                },
            },
        )

        exit_code = await run_cli_async(
            [
                "propose-security-mapping-recovery",
                "--collection-summary-path",
                "collection_summary.json",
                "--model",
                "codex",
                "--output-path",
                "proposal.json",
            ],
            stdout=stdout,
            stderr=stderr,
            model_client_factory=lambda config: model_calls.append(config) or model,
        )

        request = model.requests[0]
        rendered_prompt = "\n".join(
            block.text
            for message in request.messages
            for block in message.content
            if isinstance(block, TextBlock)
        )

        assert exit_code == 0
        assert stderr.getvalue() == ""
        assert model_calls == [
            ModelProviderConfig(provider="codex", model=None, timeout_seconds=300)
        ]
        assert "SEC_A" in rendered_prompt
        assert "SEC_UNKNOWN" in rendered_prompt
        assert "security_classification" in rendered_prompt
        assert "unknown" in rendered_prompt
        assert "etf_focus_ai" not in rendered_prompt
        assert "2026-05-11" not in rendered_prompt
        assert "must-not-leak" not in rendered_prompt
        assert str(tmp_path) not in rendered_prompt
        assert json.loads(stdout.getvalue()) == {
            "schema_version": (
                "agent_treport.security_mapping.recovery_proposal_result.v1"
            ),
            "status": "succeeded",
            "source_evidence_type": "native_collection_summary",
            "collection_summary_path": "collection_summary.json",
            "output_path": "proposal.json",
            "sample_count": 2,
            "proposal_count": 2,
            "proposed_count": 1,
            "unresolved_count": 1,
            "model_called": True,
        }
        assert json.loads(output_path.read_text(encoding="utf-8")) == {
            "schema_version": "agent_treport.security_mapping.recovery_proposal.v1",
            "source_evidence_type": "native_collection_summary",
            "source_collection_summary_path": "collection_summary.json",
            "proposals": [
                {
                    "security_id": "SEC_A",
                    "name": "Alpha Corp.",
                    "proposed_ticker": "ALPH",
                    "status": "proposed",
                    "confidence": "medium",
                    "rationale": "Aggregate name match.",
                },
                {
                    "security_id": "SEC_UNKNOWN",
                    "name": "Unknown Instrument",
                    "proposed_ticker": None,
                    "status": "unresolved",
                    "confidence": "low",
                    "rationale": "Unknown classification.",
                },
            ],
        }

    run_async(scenario())


def test_agent_treport_propose_security_mapping_recovery_empty_samples_skip_model_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        sync_metadata_path = tmp_path / "sync_metadata.json"
        output_path = tmp_path / "proposal.json"
        monkeypatch.chdir(tmp_path)
        model_calls: list[ModelProviderConfig] = []
        _write_json(sync_metadata_path, _sync_metadata_payload([]))

        exit_code = await run_cli_async(
            [
                "propose-security-mapping-recovery",
                "--sync-metadata-path",
                "sync_metadata.json",
                "--model",
                "codex",
                "--output-path",
                "proposal.json",
            ],
            stdout=stdout,
            stderr=stderr,
            model_client_factory=lambda config: model_calls.append(config) or FakeModelClient([]),
        )

        assert exit_code == 0
        assert stderr.getvalue() == ""
        assert model_calls == []
        assert json.loads(stdout.getvalue()) == {
            "schema_version": (
                "agent_treport.security_mapping.recovery_proposal_result.v1"
            ),
            "status": "succeeded",
            "sync_metadata_path": "sync_metadata.json",
            "output_path": "proposal.json",
            "sample_count": 0,
            "proposal_count": 0,
            "proposed_count": 0,
            "unresolved_count": 0,
            "model_called": False,
        }
        assert json.loads(output_path.read_text(encoding="utf-8")) == {
            "schema_version": "agent_treport.security_mapping.recovery_proposal.v1",
            "source_sync_metadata_path": "sync_metadata.json",
            "proposals": [],
        }

    run_async(scenario())


def test_agent_treport_propose_security_mapping_recovery_preflights_output_before_model(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        sync_metadata_path = tmp_path / "sync_metadata.json"
        output_path = tmp_path / "proposal.json"
        model_calls: list[ModelProviderConfig] = []
        _write_json(sync_metadata_path, _sync_metadata_payload([_recovery_sample()]))
        output_path.write_text("keep me", encoding="utf-8")

        exit_code = await run_cli_async(
            [
                "propose-security-mapping-recovery",
                "--sync-metadata-path",
                str(sync_metadata_path),
                "--model",
                "codex",
                "--output-path",
                str(output_path),
            ],
            stdout=stdout,
            stderr=stderr,
            model_client_factory=lambda config: model_calls.append(config) or FakeModelClient([]),
        )

        assert exit_code == 2
        assert stdout.getvalue() == ""
        assert "output path already exists" in stderr.getvalue()
        assert model_calls == []
        assert output_path.read_text(encoding="utf-8") == "keep me"

    run_async(scenario())


def test_agent_treport_propose_security_mapping_recovery_never_overwrites_sync_metadata(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        sync_metadata_path = tmp_path / "sync_metadata.json"
        original = json.dumps(_sync_metadata_payload([]), ensure_ascii=False)
        sync_metadata_path.write_text(original, encoding="utf-8")

        exit_code = await run_cli_async(
            [
                "propose-security-mapping-recovery",
                "--sync-metadata-path",
                str(sync_metadata_path),
                "--model",
                "codex",
                "--output-path",
                str(sync_metadata_path),
                "--overwrite",
            ],
            stdout=stdout,
            stderr=stderr,
            model_client_factory=lambda _config: FakeModelClient([]),
        )

        assert exit_code == 2
        assert stdout.getvalue() == ""
        assert "output path must not equal sync metadata path" in stderr.getvalue()
        assert sync_metadata_path.read_text(encoding="utf-8") == original

    run_async(scenario())


def test_agent_treport_propose_security_mapping_recovery_invalid_metadata_is_input_error(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        sync_metadata_path = tmp_path / "sync_metadata.json"
        output_path = tmp_path / "proposal.json"
        model_calls: list[ModelProviderConfig] = []
        _write_json(
            sync_metadata_path,
            {
                "schema_version": "wrong",
                "unmapped_security_samples": [_recovery_sample()],
            },
        )

        exit_code = await run_cli_async(
            [
                "propose-security-mapping-recovery",
                "--sync-metadata-path",
                str(sync_metadata_path),
                "--model",
                "codex",
                "--output-path",
                str(output_path),
            ],
            stdout=stdout,
            stderr=stderr,
            model_client_factory=lambda config: model_calls.append(config) or FakeModelClient([]),
        )

        assert exit_code == 2
        assert stdout.getvalue() == ""
        assert stderr.getvalue() == "agent-treport: error: invalid sync metadata schema\n"
        assert model_calls == []
        assert not output_path.exists()

    run_async(scenario())


@pytest.mark.parametrize(
    "response",
    [
        ModelResponse(message=None),
        ModelResponse(
            message=Message(
                role="assistant",
                content=(TextBlock(text="```json\n{}\n```"),),
            )
        ),
        ModelResponse(
            message=Message(
                role="assistant",
                content=(JsonBlock(value={"proposals": []}),),
            )
        ),
        ModelResponse(
            message=Message(
                role="assistant",
                content=(TextBlock(text="{}"), TextBlock(text="{}")),
            )
        ),
        ModelResponse(
            message=Message(
                role="assistant",
                content=(TextBlock(text=json.dumps({"proposals": []})),),
            ),
            tool_calls=(
                ToolCall(
                    id="toolcall_recovery",
                    tool_name="load_skill",
                    arguments={},
                    origin="model",
                ),
            ),
        ),
        _assistant_text_response({"schema_version": "not allowed", "proposals": []}),
        _assistant_text_response(
            {
                "proposals": [
                    {
                        "security_id": "SEC_A",
                        "name": "Alpha Corp.",
                        "proposed_ticker": "AAA",
                        "status": "proposed",
                        "confidence": "high",
                        "rationale": "ok",
                    },
                    {
                        "security_id": "SEC_A",
                        "name": "Alpha Corp.",
                        "proposed_ticker": "AAA",
                        "status": "proposed",
                        "confidence": "high",
                        "rationale": "duplicate",
                    },
                ]
            }
        ),
        _assistant_text_response({"proposals": []}),
        _assistant_text_response(
            {
                "proposals": [
                    {
                        "security_id": "SEC_EXTRA",
                        "name": "Extra Corp.",
                        "proposed_ticker": "EXT",
                        "status": "proposed",
                        "confidence": "medium",
                        "rationale": "extra",
                    }
                ]
            }
        ),
        _assistant_text_response(
            {
                "proposals": [
                    {
                        "security_id": "SEC_A",
                        "name": "Wrong Name",
                        "proposed_ticker": "AAA",
                        "status": "proposed",
                        "confidence": "high",
                        "rationale": "mismatch",
                    }
                ]
            }
        ),
        _assistant_text_response(
            {
                "proposals": [
                    {
                        "security_id": "SEC_A",
                        "name": "Alpha Corp.",
                        "proposed_ticker": None,
                        "status": "proposed",
                        "confidence": "high",
                        "rationale": "bad ticker",
                    }
                ]
            }
        ),
        _assistant_text_response(
            {
                "proposals": [
                    {
                        "security_id": "SEC_A",
                        "name": "Alpha Corp.",
                        "proposed_ticker": "AAA",
                        "status": "unresolved",
                        "confidence": "low",
                        "rationale": "bad unresolved ticker",
                    }
                ]
            }
        ),
    ],
)
def test_agent_treport_propose_security_mapping_recovery_rejects_invalid_model_output(
    tmp_path: Path,
    response: ModelResponse,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        sync_metadata_path = tmp_path / "sync_metadata.json"
        output_path = tmp_path / "proposal.json"
        _write_json(sync_metadata_path, _sync_metadata_payload([_recovery_sample()]))

        exit_code = await run_cli_async(
            [
                "propose-security-mapping-recovery",
                "--sync-metadata-path",
                str(sync_metadata_path),
                "--model",
                "codex",
                "--output-path",
                str(output_path),
            ],
            stdout=stdout,
            stderr=stderr,
            model_client_factory=lambda _config: FakeModelClient([response]),
        )

        payload = json.loads(stderr.getvalue())
        assert exit_code == 1
        assert stdout.getvalue() == ""
        assert payload["reason"] == "security_mapping_recovery_proposal_failed"
        assert payload["error"]["code"] == "security_mapping_recovery_proposal_failed"
        assert payload["error"]["message"] == "operation failed"
        assert "proposal security_id" not in stderr.getvalue()
        assert not output_path.exists()

    run_async(scenario())


def test_agent_treport_propose_security_mapping_recovery_does_not_mutate_mapping_file(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        mapping_path = tmp_path / "security_mapping.json"
        sync_metadata_path = tmp_path / "sync_metadata.json"
        output_path = tmp_path / "proposal.json"
        mapping_text = json.dumps(
            {
                "schema_version": "agent_treport.security_mapping.v1",
                "mappings": [{"security_id": "SEC_A", "ticker": "OLD"}],
            },
            ensure_ascii=False,
        )
        mapping_path.write_text(mapping_text, encoding="utf-8")
        metadata = _sync_metadata_payload([_recovery_sample()])
        metadata["security_mapping_path"] = str(mapping_path)
        _write_json(sync_metadata_path, metadata)

        exit_code = await run_cli_async(
            [
                "propose-security-mapping-recovery",
                "--sync-metadata-path",
                str(sync_metadata_path),
                "--model",
                "codex",
                "--output-path",
                str(output_path),
            ],
            stdout=stdout,
            stderr=stderr,
            model_client_factory=lambda _config: FakeModelClient(
                [
                    _assistant_text_response(
                        {
                            "proposals": [
                                {
                                    "security_id": "SEC_A",
                                    "name": "Alpha Corp.",
                                    "proposed_ticker": "NEW",
                                    "status": "proposed",
                                    "confidence": "medium",
                                    "rationale": "review candidate",
                                }
                            ]
                        }
                    )
                ]
            ),
        )

        assert exit_code == 0
        assert stderr.getvalue() == ""
        assert mapping_path.read_text(encoding="utf-8") == mapping_text

    run_async(scenario())


def test_agent_treport_security_mapping_recovery_loop_improves_sync_coverage(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        first_sync_stdout = StringIO()
        apply_stdout = StringIO()
        second_sync_stdout = StringIO()
        no_mapping_dest = tmp_path / "without-mapping"
        with_mapping_dest = tmp_path / "with-mapping"
        base_mapping_path = tmp_path / "base_security_mapping.json"
        patch_path = tmp_path / "reviewed_patch.json"
        merged_mapping_path = tmp_path / "merged_security_mapping.json"
        _write_json(
            base_mapping_path,
            {
                "schema_version": "agent_treport.security_mapping.v1",
                "mappings": [],
            },
        )

        first_exit = await run_cli_async(
            [
                "sync-operational-holdings",
                "--source",
                str(OPERATIONAL_SOURCE_MANIFEST),
                "--dest",
                str(no_mapping_dest),
                "--observed-partitions",
                "2",
            ],
            stdout=first_sync_stdout,
        )
        first_payload = json.loads(first_sync_stdout.getvalue())
        samples = first_payload["unmapped_security_samples"]
        _write_json(
            patch_path,
            {
                "schema_version": "agent_treport.security_mapping.patch.v1",
                "mappings": [
                    {
                        "security_id": sample["security_id"],
                        "ticker": f"TICKER_{index}",
                    }
                    for index, sample in enumerate(samples, 1)
                ],
            },
        )

        apply_exit = await run_cli_async(
            [
                "apply-security-mapping-patch",
                "--security-mapping-path",
                str(base_mapping_path),
                "--patch-path",
                str(patch_path),
                "--output-path",
                str(merged_mapping_path),
            ],
            stdout=apply_stdout,
        )

        second_exit = await run_cli_async(
            [
                "sync-operational-holdings",
                "--source",
                str(OPERATIONAL_SOURCE_MANIFEST),
                "--dest",
                str(with_mapping_dest),
                "--observed-partitions",
                "2",
                "--security-mapping-path",
                str(merged_mapping_path),
            ],
            stdout=second_sync_stdout,
        )
        second_payload = json.loads(second_sync_stdout.getvalue())

        assert first_exit == 0
        assert first_payload["sync_quality"]["metrics"]["ticker_mapping_coverage_ratio"] == 0.0
        assert samples
        assert apply_exit == 0
        assert json.loads(apply_stdout.getvalue())["added_mapping_count"] == len(samples)
        assert second_exit == 0
        assert (
            second_payload["sync_quality"]["metrics"]["ticker_mapping_coverage_ratio"]
            > first_payload["sync_quality"]["metrics"]["ticker_mapping_coverage_ratio"]
        )
        assert second_payload["sync_quality"]["metrics"]["ticker_mapping_coverage_ratio"] == 1.0
        assert second_payload["unmapped_security_samples"] == []

    run_async(scenario())


def test_agent_treport_sync_operational_holdings_input_error_returns_exit_2(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()

        exit_code = await run_cli_async(
            [
                "sync-operational-holdings",
                "--source",
                str(OPERATIONAL_SOURCE_MANIFEST),
                "--dest",
                str(tmp_path / "operational-holdings"),
                "--observed-partitions",
                "0",
            ],
            stdout=stdout,
            stderr=stderr,
        )

        assert exit_code == 2
        assert stdout.getvalue() == ""
        assert stderr.getvalue() == (
            "agent-treport: error: observed-partitions must be a positive integer\n"
        )

    run_async(scenario())


def test_agent_treport_check_operational_readiness_command_prints_ready_json(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        manifest_path = _copy_cli_ready_operational_export(tmp_path)

        exit_code = await run_cli_async(
            [
                "check-operational-readiness",
                "--holdings-path",
                str(manifest_path),
                "--focus-etf-id",
                "etf_focus_ai",
            ],
            stdout=stdout,
            stderr=stderr,
            readiness_now=_cli_readiness_now,
        )

        payload = json.loads(stdout.getvalue())

        assert exit_code == 0
        assert stderr.getvalue() == ""
        assert stdout.getvalue().count("\n") == 1
        assert payload["schema_version"] == "agent_treport.operational_run_readiness.v1"
        assert payload["status"] == "ready"
        assert payload["user_ready_allowed"] is True
        assert payload["current_date"] == "2026-05-11"
        assert payload["previous_date"] == "2026-05-08"
        assert payload["export_fingerprint"] == compute_operational_export_fingerprint(
            manifest_path
        )
        assert [action["code"] for action in payload["next_actions"]] == ["run_report"]

    run_async(scenario())


def test_agent_treport_check_operational_readiness_command_prints_hold_json(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        manifest_path = _copy_cli_ready_operational_export(tmp_path)
        (manifest_path.parent / "sync_metadata.json").unlink()

        exit_code = await run_cli_async(
            [
                "check-operational-readiness",
                "--holdings-path",
                str(manifest_path),
                "--focus-etf-id",
                "etf_focus_ai",
            ],
            stdout=stdout,
            stderr=stderr,
            readiness_now=_cli_readiness_now,
        )

        payload = json.loads(stdout.getvalue())

        assert exit_code == 0
        assert stderr.getvalue() == ""
        assert payload["status"] == "hold"
        assert payload["user_ready_allowed"] is False
        assert payload["export_fingerprint"] == compute_operational_export_fingerprint(
            manifest_path
        )
        assert [reason["code"] for reason in payload["reasons"]] == [
            "sync_metadata_missing"
        ]

    run_async(scenario())


def test_agent_treport_check_operational_readiness_accepts_focus_etf_set_path(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        manifest_path = _copy_cli_ready_operational_export(tmp_path)
        focus_set_path = tmp_path / "focus_set.json"
        _write_cli_focus_etf_set(
            focus_set_path,
            ["etf_focus_ai", "etf_peer_ai"],
        )

        exit_code = await run_cli_async(
            [
                "check-operational-readiness",
                "--holdings-path",
                str(manifest_path),
                "--focus-etf-set-path",
                str(focus_set_path),
            ],
            stdout=stdout,
            stderr=stderr,
            readiness_now=_cli_readiness_now,
        )

        payload = json.loads(stdout.getvalue())

        assert exit_code == 0
        assert stderr.getvalue() == ""
        assert payload["status"] == "hold"
        assert payload["focus_etf_ids"] == ["etf_focus_ai", "etf_peer_ai"]
        assert [reason["code"] for reason in payload["reasons"]] == [
            "insufficient_focus_etf_eligibility"
        ]
        assert payload["focus_eligibility"]["eligible_focus_etf_ids"] == [
            "etf_focus_ai",
            "etf_peer_ai",
        ]

    run_async(scenario())


def test_agent_treport_check_operational_readiness_invalid_options_return_exit_2(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        manifest_path = _copy_cli_ready_operational_export(tmp_path)

        exit_code = await run_cli_async(
            [
                "check-operational-readiness",
                "--holdings-path",
                str(manifest_path),
                "--focus-etf-id",
                "etf_focus_ai",
                "--observed-partitions",
                "0",
            ],
            stdout=stdout,
            stderr=stderr,
            readiness_now=_cli_readiness_now,
        )

        assert exit_code == 2
        assert stdout.getvalue() == ""
        assert stderr.getvalue() == (
            "agent-treport: error: observed-partitions must be a positive integer\n"
        )

    run_async(scenario())


def test_agent_treport_check_operational_readiness_rejects_negative_max_age(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        manifest_path = _copy_cli_ready_operational_export(tmp_path)

        exit_code = await run_cli_async(
            [
                "check-operational-readiness",
                "--holdings-path",
                str(manifest_path),
                "--focus-etf-id",
                "etf_focus_ai",
                "--max-observed-age-days",
                "-1",
            ],
            stdout=stdout,
            stderr=stderr,
            readiness_now=_cli_readiness_now,
        )

        assert exit_code == 2
        assert stdout.getvalue() == ""
        assert stderr.getvalue() == (
            "agent-treport: error: max-observed-age-days must be a non-negative integer\n"
        )

    run_async(scenario())


def test_agent_treport_check_operational_readiness_rejects_invalid_timezone(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        manifest_path = _copy_cli_ready_operational_export(tmp_path)

        exit_code = await run_cli_async(
            [
                "check-operational-readiness",
                "--holdings-path",
                str(manifest_path),
                "--focus-etf-id",
                "etf_focus_ai",
                "--operator-timezone",
                "Invalid/Zone",
            ],
            stdout=stdout,
            stderr=stderr,
            readiness_now=_cli_readiness_now,
        )

        assert exit_code == 2
        assert stdout.getvalue() == ""
        assert stderr.getvalue() == (
            "agent-treport: error: invalid operator timezone: Invalid/Zone\n"
        )

    run_async(scenario())


def test_agent_treport_check_operational_readiness_explicit_missing_metadata_is_exit_2(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        manifest_path = _copy_cli_ready_operational_export(tmp_path)

        exit_code = await run_cli_async(
            [
                "check-operational-readiness",
                "--holdings-path",
                str(manifest_path),
                "--focus-etf-id",
                "etf_focus_ai",
                "--sync-metadata-path",
                str(tmp_path / "missing-sync-metadata.json"),
            ],
            stdout=stdout,
            stderr=stderr,
            readiness_now=_cli_readiness_now,
        )

        assert exit_code == 2
        assert stdout.getvalue() == ""
        assert "sync metadata file not found" in stderr.getvalue()
        assert stderr.getvalue().startswith("agent-treport: error: ")

    run_async(scenario())


def test_agent_treport_check_operational_readiness_invalid_json_is_exit_2(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        manifest_path = tmp_path / "url_holdings_cumulative.json"
        manifest_path.write_text("{not-json", encoding="utf-8")

        exit_code = await run_cli_async(
            [
                "check-operational-readiness",
                "--holdings-path",
                str(manifest_path),
                "--focus-etf-id",
                "etf_focus_ai",
            ],
            stdout=stdout,
            stderr=stderr,
            readiness_now=_cli_readiness_now,
        )

        assert exit_code == 2
        assert stdout.getvalue() == ""
        assert "operational export fingerprint could not be computed: manifest JSON is invalid" in (
            stderr.getvalue()
        )
        assert stderr.getvalue().startswith("agent-treport: error: ")

    run_async(scenario())


def test_agent_treport_run_report_operational_requires_focus_etf_before_resources(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        sqlite_path = tmp_path / "state" / "treport.sqlite3"
        artifact_root = tmp_path / "artifacts"
        model_calls: list[ModelProviderConfig] = []

        exit_code = await run_cli_async(
            [
                "run-report",
                "--holdings-source",
                "operational",
                "--holdings-path",
                str(OPERATIONAL_NORMALIZED_MANIFEST),
                "--sqlite-path",
                str(sqlite_path),
                "--artifact-root",
                str(artifact_root),
                "--model",
                "codex",
            ],
            stdout=stdout,
            stderr=stderr,
            model_client_factory=lambda config: model_calls.append(config) or FakeModelClient([]),
        )

        assert exit_code == 2
        assert stdout.getvalue() == ""
        assert stderr.getvalue() == (
            "agent-treport: error: --focus-etf-id or --focus-etf-set-path is required\n"
        )
        assert model_calls == []
        assert not sqlite_path.exists()
        assert not artifact_root.exists()

    run_async(scenario())


def test_agent_treport_run_report_preflights_operational_manifest_before_resources(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        sqlite_path = tmp_path / "state" / "treport.sqlite3"
        artifact_root = tmp_path / "artifacts"
        model_calls: list[ModelProviderConfig] = []

        exit_code = await run_cli_async(
            [
                "run-report",
                "--holdings-source",
                "operational",
                "--holdings-path",
                str(OPERATIONAL_SOURCE_MANIFEST),
                "--focus-etf-id",
                "etf_focus_ai",
                "--sqlite-path",
                str(sqlite_path),
                "--artifact-root",
                str(artifact_root),
                "--model",
                "codex",
            ],
            stdout=stdout,
            stderr=stderr,
            model_client_factory=lambda config: model_calls.append(config) or FakeModelClient([]),
        )

        assert exit_code == 2
        assert stdout.getvalue() == ""
        assert "run sync-operational-holdings first" in stderr.getvalue()
        assert stderr.getvalue().startswith("agent-treport: error: ")
        assert model_calls == []
        assert not sqlite_path.exists()
        assert not artifact_root.exists()

    run_async(scenario())


def test_agent_treport_run_report_operational_requires_readiness_before_resources(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        manifest_path = _copy_cli_ready_operational_export(tmp_path)
        sqlite_path = tmp_path / "state" / "treport.sqlite3"
        artifact_root = tmp_path / "artifacts"
        model_calls: list[ModelProviderConfig] = []

        exit_code = await run_cli_async(
            [
                "run-report",
                "--holdings-source",
                "operational",
                "--holdings-path",
                str(manifest_path),
                "--focus-etf-id",
                "etf_focus_ai",
                "--sqlite-path",
                str(sqlite_path),
                "--artifact-root",
                str(artifact_root),
                "--model",
                "codex",
            ],
            stdout=stdout,
            stderr=stderr,
            model_client_factory=lambda config: model_calls.append(config) or FakeModelClient([]),
        )

        assert exit_code == 2
        assert stdout.getvalue() == ""
        assert "readiness handoff is required" in stderr.getvalue()
        assert stderr.getvalue().startswith("agent-treport: error: ")
        assert model_calls == []
        assert not sqlite_path.exists()
        assert not artifact_root.exists()

    run_async(scenario())


def test_agent_treport_run_report_rejects_mismatched_readiness_before_resources(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        manifest_path = _copy_cli_ready_operational_export(tmp_path)
        readiness_path = tmp_path / "readiness.json"
        _write_json(
            readiness_path,
            _cli_ready_readiness(
                manifest_path,
                holdings_path=str(tmp_path / "other-holdings.json"),
                focus_etf_id="etf_other",
                requested_observed_partitions=99,
                current_date="2026-05-10",
                previous_date="2026-05-07",
                export_fingerprint={
                    "algorithm": "sha256",
                    "scope": "copied_manifest_and_referenced_partitions_v1",
                    "value": "0" * 64,
                },
            ),
        )
        sqlite_path = tmp_path / "state" / "treport.sqlite3"
        artifact_root = tmp_path / "artifacts"
        model_calls: list[ModelProviderConfig] = []

        exit_code = await run_cli_async(
            [
                "run-report",
                "--holdings-source",
                "operational",
                "--holdings-path",
                str(manifest_path),
                "--focus-etf-id",
                "etf_focus_ai",
                "--observed-partitions",
                "3",
                "--readiness-path",
                str(readiness_path),
                "--allow-operator-review-output",
                "--sqlite-path",
                str(sqlite_path),
                "--artifact-root",
                str(artifact_root),
                "--model",
                "codex",
            ],
            stdout=stdout,
            stderr=stderr,
            model_client_factory=lambda config: model_calls.append(config) or FakeModelClient([]),
        )

        assert exit_code == 2
        assert stdout.getvalue() == ""
        assert stderr.getvalue() == (
            "agent-treport: error: readiness handoff does not match operational run: "
            "holdings_path, requested_observed_partitions, "
            "current_date, previous_date, focus_etf_id, export_fingerprint\n"
        )
        assert model_calls == []
        assert not sqlite_path.exists()
        assert not artifact_root.exists()

    run_async(scenario())


@pytest.mark.parametrize(
    "fingerprint",
    [
        None,
        "not-an-object",
        {
            "algorithm": "md5",
            "scope": "copied_manifest_and_referenced_partitions_v1",
            "value": "0" * 64,
        },
        {"algorithm": "sha256", "scope": "other_scope", "value": "0" * 64},
        {
            "algorithm": "sha256",
            "scope": "copied_manifest_and_referenced_partitions_v1",
            "value": "not-lowercase-hex",
        },
    ],
)
def test_agent_treport_run_report_rejects_missing_or_malformed_fingerprint_before_resources(
    tmp_path: Path,
    fingerprint: object,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        manifest_path = _copy_cli_ready_operational_export(tmp_path)
        readiness = _cli_ready_readiness(manifest_path)
        if fingerprint is None:
            readiness.pop("export_fingerprint")
        else:
            readiness["export_fingerprint"] = fingerprint
        readiness_path = tmp_path / "readiness.json"
        _write_json(readiness_path, readiness)
        sqlite_path = tmp_path / "state" / "treport.sqlite3"
        artifact_root = tmp_path / "artifacts"
        model_calls: list[ModelProviderConfig] = []

        exit_code = await run_cli_async(
            [
                "run-report",
                "--holdings-source",
                "operational",
                "--holdings-path",
                str(manifest_path),
                "--focus-etf-id",
                "etf_focus_ai",
                "--observed-partitions",
                "3",
                "--readiness-path",
                str(readiness_path),
                "--allow-operator-review-output",
                "--sqlite-path",
                str(sqlite_path),
                "--artifact-root",
                str(artifact_root),
                "--model",
                "codex",
            ],
            stdout=stdout,
            stderr=stderr,
            model_client_factory=lambda config: model_calls.append(config) or FakeModelClient([]),
        )

        assert exit_code == 2
        assert stdout.getvalue() == ""
        assert stderr.getvalue() == (
            "agent-treport: error: readiness handoff does not match operational run: "
            "export_fingerprint\n"
        )
        assert "0000000000000000000000000000000000000000000000000000000000000000" not in (
            stderr.getvalue()
        )
        assert model_calls == []
        assert not sqlite_path.exists()
        assert not artifact_root.exists()

    run_async(scenario())


def test_agent_treport_run_report_rejects_export_changed_after_readiness_before_resources(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        manifest_path = _copy_cli_ready_operational_export(tmp_path)
        readiness_path = tmp_path / "readiness.json"
        _write_json(readiness_path, _cli_ready_readiness(manifest_path))
        partition_path = (
            manifest_path.parent
            / "url_holdings_cumulative.json.parts"
            / "2026-05-11.jsonl"
        )
        rows = partition_path.read_text(encoding="utf-8").splitlines()
        changed_row = json.loads(rows[0])
        changed_row["weight_percent"] = 99.0
        rows[0] = json.dumps(changed_row, ensure_ascii=False)
        partition_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
        sqlite_path = tmp_path / "state" / "treport.sqlite3"
        artifact_root = tmp_path / "artifacts"
        model_calls: list[ModelProviderConfig] = []

        exit_code = await run_cli_async(
            [
                "run-report",
                "--holdings-source",
                "operational",
                "--holdings-path",
                str(manifest_path),
                "--focus-etf-id",
                "etf_focus_ai",
                "--observed-partitions",
                "3",
                "--readiness-path",
                str(readiness_path),
                "--allow-operator-review-output",
                "--sqlite-path",
                str(sqlite_path),
                "--artifact-root",
                str(artifact_root),
                "--model",
                "codex",
            ],
            stdout=stdout,
            stderr=stderr,
            model_client_factory=lambda config: model_calls.append(config) or FakeModelClient([]),
        )

        assert exit_code == 2
        assert stdout.getvalue() == ""
        assert stderr.getvalue() == (
            "agent-treport: error: readiness handoff does not match operational run: "
            "export_fingerprint\n"
        )
        assert str(manifest_path) not in stderr.getvalue()
        assert "weight_percent" not in stderr.getvalue()
        assert "synced_at" not in stderr.getvalue()
        assert model_calls == []
        assert not sqlite_path.exists()
        assert not artifact_root.exists()

    run_async(scenario())


def test_agent_treport_run_report_rejects_unreadable_current_fingerprint_before_resources(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        manifest_path = _copy_cli_ready_operational_export(tmp_path)
        readiness_path = tmp_path / "readiness.json"
        _write_json(readiness_path, _cli_ready_readiness(manifest_path))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["dates"].append("2026-05-01")
        manifest["partitions"]["2026-05-01"] = {
            "file": "url_holdings_cumulative.json.parts/2026-05-01.jsonl",
            "record_count": 1,
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        bad_partition = (
            manifest_path.parent
            / "url_holdings_cumulative.json.parts"
            / "2026-05-01.jsonl"
        )
        bad_partition.write_text("{not-json\n", encoding="utf-8")
        sqlite_path = tmp_path / "state" / "treport.sqlite3"
        artifact_root = tmp_path / "artifacts"
        model_calls: list[ModelProviderConfig] = []

        exit_code = await run_cli_async(
            [
                "run-report",
                "--holdings-source",
                "operational",
                "--holdings-path",
                str(manifest_path),
                "--focus-etf-id",
                "etf_focus_ai",
                "--observed-partitions",
                "3",
                "--readiness-path",
                str(readiness_path),
                "--sqlite-path",
                str(sqlite_path),
                "--artifact-root",
                str(artifact_root),
                "--model",
                "codex",
            ],
            stdout=stdout,
            stderr=stderr,
            model_client_factory=lambda config: model_calls.append(config) or FakeModelClient([]),
        )

        assert exit_code == 2
        assert stdout.getvalue() == ""
        assert "operational export fingerprint could not be computed: " in stderr.getvalue()
        assert "partition JSONL row is invalid for date 2026-05-01" in stderr.getvalue()
        assert str(bad_partition) not in stderr.getvalue()
        assert model_calls == []
        assert not sqlite_path.exists()
        assert not artifact_root.exists()

    run_async(scenario())


def test_agent_treport_run_report_rejects_hold_readiness_without_override_before_resources(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        manifest_path = _copy_cli_ready_operational_export(tmp_path)
        readiness_path = tmp_path / "readiness.json"
        _write_json(
            readiness_path,
            _cli_ready_readiness(
                manifest_path,
                status="hold",
                user_ready_allowed=False,
                reasons=[
                    {
                        "code": "sync_quality_risk_failed",
                        "severity": "hold",
                        "message": "review needed",
                    }
                ],
            ),
        )
        sqlite_path = tmp_path / "state" / "treport.sqlite3"
        artifact_root = tmp_path / "artifacts"
        model_calls: list[ModelProviderConfig] = []

        exit_code = await run_cli_async(
            [
                "run-report",
                "--holdings-source",
                "operational",
                "--holdings-path",
                str(manifest_path),
                "--focus-etf-id",
                "etf_focus_ai",
                "--observed-partitions",
                "3",
                "--readiness-path",
                str(readiness_path),
                "--sqlite-path",
                str(sqlite_path),
                "--artifact-root",
                str(artifact_root),
                "--model",
                "codex",
            ],
            stdout=stdout,
            stderr=stderr,
            model_client_factory=lambda config: model_calls.append(config) or FakeModelClient([]),
        )

        assert exit_code == 2
        assert stdout.getvalue() == ""
        assert "operational readiness status hold requires --allow-operator-review-output" in (
            stderr.getvalue()
        )
        assert model_calls == []
        assert not sqlite_path.exists()
        assert not artifact_root.exists()

    run_async(scenario())


def test_agent_treport_run_report_rejects_failed_readiness_even_with_override(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        manifest_path = _copy_cli_ready_operational_export(tmp_path)
        readiness_path = tmp_path / "readiness.json"
        _write_json(
            readiness_path,
            _cli_ready_readiness(
                manifest_path,
                status="failed",
                user_ready_allowed=False,
                reasons=[
                    {
                        "code": "invalid_manifest_contract",
                        "severity": "failed",
                        "message": "input contract broken",
                    }
                ],
            ),
        )
        sqlite_path = tmp_path / "state" / "treport.sqlite3"
        artifact_root = tmp_path / "artifacts"
        model_calls: list[ModelProviderConfig] = []

        exit_code = await run_cli_async(
            [
                "run-report",
                "--holdings-source",
                "operational",
                "--holdings-path",
                str(manifest_path),
                "--focus-etf-id",
                "etf_focus_ai",
                "--observed-partitions",
                "3",
                "--readiness-path",
                str(readiness_path),
                "--allow-operator-review-output",
                "--sqlite-path",
                str(sqlite_path),
                "--artifact-root",
                str(artifact_root),
                "--model",
                "codex",
            ],
            stdout=stdout,
            stderr=stderr,
            model_client_factory=lambda config: model_calls.append(config) or FakeModelClient([]),
        )

        assert exit_code == 2
        assert stdout.getvalue() == ""
        assert "operational readiness status failed blocks run-report" in stderr.getvalue()
        assert model_calls == []
        assert not sqlite_path.exists()
        assert not artifact_root.exists()

    run_async(scenario())


def test_agent_treport_run_report_rejects_ready_readiness_with_review_override_before_resources(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        manifest_path = _copy_cli_ready_operational_export(tmp_path)
        readiness_path = tmp_path / "readiness.json"
        _write_json(readiness_path, _cli_ready_readiness(manifest_path))
        sqlite_path = tmp_path / "state" / "treport.sqlite3"
        artifact_root = tmp_path / "artifacts"
        model_calls: list[ModelProviderConfig] = []

        exit_code = await run_cli_async(
            [
                "run-report",
                "--holdings-source",
                "operational",
                "--holdings-path",
                str(manifest_path),
                "--focus-etf-id",
                "etf_focus_ai",
                "--observed-partitions",
                "3",
                "--readiness-path",
                str(readiness_path),
                "--allow-operator-review-output",
                "--sqlite-path",
                str(sqlite_path),
                "--artifact-root",
                str(artifact_root),
                "--model",
                "codex",
            ],
            stdout=stdout,
            stderr=stderr,
            model_client_factory=lambda config: model_calls.append(config) or FakeModelClient([]),
        )

        assert exit_code == 2
        assert stdout.getvalue() == ""
        assert (
            "operator-review override is only valid for hold or missing readiness"
            in stderr.getvalue()
        )
        assert model_calls == []
        assert not sqlite_path.exists()
        assert not artifact_root.exists()

    run_async(scenario())


def test_agent_treport_run_report_rejects_warning_readiness_with_review_override_before_resources(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        manifest_path = _copy_cli_ready_operational_export(tmp_path)
        readiness_path = tmp_path / "readiness.json"
        _write_json(
            readiness_path,
            _cli_ready_readiness(
                manifest_path,
                status="ready_with_warnings",
                warnings=[
                    {
                        "code": "cash_derivation_failure_ratio",
                        "severity": "warning",
                        "message": "Cash derivation warning requires user disclosure.",
                    }
                ],
            ),
        )
        sqlite_path = tmp_path / "state" / "treport.sqlite3"
        artifact_root = tmp_path / "artifacts"
        model_calls: list[ModelProviderConfig] = []

        exit_code = await run_cli_async(
            [
                "run-report",
                "--holdings-source",
                "operational",
                "--holdings-path",
                str(manifest_path),
                "--focus-etf-id",
                "etf_focus_ai",
                "--observed-partitions",
                "3",
                "--readiness-path",
                str(readiness_path),
                "--allow-operator-review-output",
                "--sqlite-path",
                str(sqlite_path),
                "--artifact-root",
                str(artifact_root),
                "--model",
                "codex",
            ],
            stdout=stdout,
            stderr=stderr,
            model_client_factory=lambda config: model_calls.append(config) or FakeModelClient([]),
        )

        assert exit_code == 2
        assert stdout.getvalue() == ""
        assert (
            "operator-review override is only valid for hold or missing readiness"
            in stderr.getvalue()
        )
        assert model_calls == []
        assert not sqlite_path.exists()
        assert not artifact_root.exists()

    run_async(scenario())


def test_agent_treport_run_report_uses_operational_holdings_source(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        readiness_path = tmp_path / "readiness.json"
        _write_json(readiness_path, _cli_ready_readiness(OPERATIONAL_NORMALIZED_MANIFEST))
        sqlite_path = tmp_path / "state" / "treport.sqlite3"
        artifact_root = tmp_path / "artifacts"

        exit_code = await run_cli_async(
            [
                "run-report",
                "--run-id",
                "run_treport_operational_cli",
                "--holdings-source",
                "operational",
                "--holdings-path",
                str(OPERATIONAL_NORMALIZED_MANIFEST),
                "--focus-etf-id",
                "etf_focus_ai",
                "--observed-partitions",
                "3",
                "--readiness-path",
                str(readiness_path),
                "--sqlite-path",
                str(sqlite_path),
                "--artifact-root",
                str(artifact_root),
                "--model",
                "codex",
            ],
            stdout=stdout,
            model_client_factory=lambda _config: FakeModelClient(
                [
                    ModelResponse(
                        message=Message(
                            role="assistant",
                            content=(TextBlock(text="operational holdings commentary"),),
                        )
                    )
                ]
            ),
        )

        payload = json.loads(stdout.getvalue())
        signal_payload = json.loads(
            (artifact_root / "artifact_treport_signal_payload.json").read_text(
                encoding="utf-8"
            )
        )

        assert exit_code == 0
        assert payload["status"] == "succeeded"
        assert signal_payload["meta"]["as_of_date"] == "2026-05-11"
        assert signal_payload["meta"]["period"]["previous"] == "2026-05-08"
        assert signal_payload["meta"]["focus_etf_id"] == "etf_focus_ai"

    run_async(scenario())


def test_agent_treport_run_report_accepts_focus_etf_set_path_for_operational_source(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        manifest_path = _copy_cli_ready_operational_export(tmp_path)
        focus_set_path = tmp_path / "focus_set.json"
        focus_etf_ids = ["etf_focus_ai", "etf_peer_ai"]
        _write_cli_focus_etf_set(focus_set_path, focus_etf_ids)
        readiness_path = tmp_path / "readiness.json"
        _write_json(
            readiness_path,
            _cli_ready_readiness(
                manifest_path,
                status="hold",
                user_ready_allowed=False,
                focus_etf_ids=focus_etf_ids,
                focus_eligibility={
                    "minimum_eligible_focus_etf_count": 3,
                    "eligible_focus_etf_count": 2,
                    "eligible_focus_etf_ids": focus_etf_ids,
                    "ineligible_focus_etf_ids": [],
                    "mixed_comparison_windows": False,
                    "comparison_windows": [
                        {
                            "etf_id": "etf_focus_ai",
                            "selected_current_date": "2026-05-11",
                            "selected_previous_date": "2026-05-08",
                        },
                        {
                            "etf_id": "etf_peer_ai",
                            "selected_current_date": "2026-05-11",
                            "selected_previous_date": "2026-05-08",
                        },
                    ],
                    "handoff_exclusions": [],
                },
                reasons=[
                    {
                        "code": "insufficient_focus_etf_eligibility",
                        "severity": "hold",
                        "message": "Fewer than three focus ETFs are eligible.",
                    }
                ],
                final_user_ready_requirements={
                    "readiness_user_ready_allowed": False,
                    "run_report_status_required": "succeeded",
                    "report_quality_status_required": "passed",
                    "warning_disclosure_required": False,
                },
            ),
        )
        sqlite_path = tmp_path / "state" / "treport.sqlite3"
        artifact_root = tmp_path / "artifacts"

        exit_code = await run_cli_async(
            [
                "run-report",
                "--run-id",
                "run_treport_operational_focus_set",
                "--holdings-source",
                "operational",
                "--holdings-path",
                str(manifest_path),
                "--focus-etf-set-path",
                str(focus_set_path),
                "--observed-partitions",
                "3",
                "--readiness-path",
                str(readiness_path),
                "--allow-operator-review-output",
                "--sqlite-path",
                str(sqlite_path),
                "--artifact-root",
                str(artifact_root),
                "--model",
                "codex",
            ],
            stdout=stdout,
            model_client_factory=lambda _config: FakeModelClient(
                [
                    ModelResponse(
                        message=Message(
                            role="assistant",
                            content=(TextBlock(text="focus set commentary"),),
                        )
                    )
                ]
            ),
        )

        payload = json.loads(stdout.getvalue())
        signal_payload = json.loads(
            (artifact_root / "artifact_treport_signal_payload.json").read_text(
                encoding="utf-8"
            )
        )
        readiness_artifact = json.loads(
            (
                artifact_root / "artifact_treport_operational_readiness.json"
            ).read_text(encoding="utf-8")
        )

        assert exit_code == 0
        assert payload["status"] == "succeeded"
        assert payload["output"]["operator_review_only"]["reason"] == "readiness_hold"
        assert signal_payload["meta"]["focus_etf_ids"] == focus_etf_ids
        assert readiness_artifact["focus_etf_ids"] == focus_etf_ids

    run_async(scenario())


def test_agent_treport_run_report_user_ready_includes_operational_readiness_artifact(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        manifest_path = _copy_cli_ready_operational_export(tmp_path)
        readiness_path = tmp_path / "readiness.json"
        _write_json(readiness_path, _cli_ready_readiness(manifest_path))
        sqlite_path = tmp_path / "state" / "treport.sqlite3"
        artifact_root = tmp_path / "artifacts"

        exit_code = await run_cli_async(
            [
                "run-report",
                "--run-id",
                "run_treport_operational_ready",
                "--holdings-source",
                "operational",
                "--holdings-path",
                str(manifest_path),
                "--focus-etf-id",
                "etf_focus_ai",
                "--observed-partitions",
                "3",
                "--readiness-path",
                str(readiness_path),
                "--sqlite-path",
                str(sqlite_path),
                "--artifact-root",
                str(artifact_root),
                "--model",
                "codex",
            ],
            stdout=stdout,
            model_client_factory=lambda _config: FakeModelClient(
                [
                    ModelResponse(
                        message=Message(
                            role="assistant",
                            content=(TextBlock(text="ready operational commentary"),),
                        )
                    )
                ]
            ),
        )

        payload = json.loads(stdout.getvalue())
        user_ready = payload["output"]["user_ready"]
        readiness_artifact_path = (
            artifact_root / "artifact_treport_operational_readiness.json"
        )
        readiness_artifact = json.loads(readiness_artifact_path.read_text(encoding="utf-8"))

        assert exit_code == 0
        assert readiness_artifact["status"] == "ready"
        assert readiness_artifact["focus_etf_id"] == "etf_focus_ai"
        assert readiness_artifact["current_date"] == "2026-05-11"
        assert readiness_artifact["previous_date"] == "2026-05-08"
        assert readiness_artifact["export_fingerprint"] == (
            compute_operational_export_fingerprint(manifest_path)
        )
        assert "holdings_path" not in readiness_artifact
        assert "sync_metadata_path" not in readiness_artifact
        assert "export_fingerprint" not in user_ready["readiness"]
        assert user_ready["readiness"] == {
            "status": "ready",
            "focus_etf_id": "etf_focus_ai",
            "current_date": "2026-05-11",
            "previous_date": "2026-05-08",
            "disclosures": [],
            "readiness_artifact_id": "artifact_treport_operational_readiness",
        }
        assert user_ready["artifacts"]["readiness"] == {
            "artifact_id": "artifact_treport_operational_readiness",
            "name": "operational_readiness.json",
            "media_type": "application/json",
            "uri": readiness_artifact_path.resolve().as_uri(),
            "path": str(readiness_artifact_path.resolve()),
        }

    run_async(scenario())


def test_agent_treport_run_report_projects_readiness_warnings_to_user_ready_and_payload(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        manifest_path = _copy_cli_ready_operational_export(tmp_path)
        readiness_path = tmp_path / "readiness.json"
        _write_json(
            readiness_path,
            _cli_ready_readiness(
                manifest_path,
                status="ready_with_warnings",
                security_coverage={
                    "security_resolution_available": True,
                    "mapped_ticker_candidate_count": 3,
                    "unresolved_ticker_candidate_count": 1,
                    "unknown_count": 1,
                    "non_ticker_excluded_count": 2,
                    "reviewed_mapping_applied_count": 1,
                    "reviewed_exclusion_applied_count": 1,
                    "ticker_mapping_coverage_ratio": 0.75,
                    "recovery_sample_count": 1,
                    "recovery_samples": [
                        {
                            "security_id": "SEC_UNKNOWN",
                            "name": "Unknown Instrument",
                            "observed_row_count": 1,
                            "observed_etf_count": 1,
                            "observed_date_count": 1,
                            "name_alias_count": 0,
                            "security_classification": "unknown",
                        }
                    ],
                },
                warnings=[
                    {
                        "code": "cash_derivation_failure_ratio",
                        "severity": "warning",
                        "message": "Cash derivation warning requires user disclosure.",
                        "metric": "cash_derivation_failure_ratio",
                        "value": 0.08,
                        "threshold": 0.05,
                        "source_sample_rows": [{"path": str(tmp_path / "raw.jsonl")}],
                    }
                ],
                next_actions=[
                    {
                        "code": "review_cash_derivation_warning",
                        "required": False,
                        "for_codes": ["cash_derivation_failure_ratio"],
                    }
                ],
            ),
        )
        sqlite_path = tmp_path / "state" / "treport.sqlite3"
        artifact_root = tmp_path / "artifacts"

        exit_code = await run_cli_async(
            [
                "run-report",
                "--run-id",
                "run_treport_operational_warning",
                "--holdings-source",
                "operational",
                "--holdings-path",
                str(manifest_path),
                "--focus-etf-id",
                "etf_focus_ai",
                "--observed-partitions",
                "3",
                "--readiness-path",
                str(readiness_path),
                "--sqlite-path",
                str(sqlite_path),
                "--artifact-root",
                str(artifact_root),
                "--model",
                "codex",
            ],
            stdout=stdout,
            model_client_factory=lambda _config: FakeModelClient(
                [
                    ModelResponse(
                        message=Message(
                            role="assistant",
                            content=(TextBlock(text="warning operational commentary"),),
                        )
                    )
                ]
            ),
        )

        payload = json.loads(stdout.getvalue())
        user_ready = payload["output"]["user_ready"]
        signal_payload = json.loads(
            (artifact_root / "artifact_treport_signal_payload.json").read_text(
                encoding="utf-8"
            )
        )
        readiness_artifact = json.loads(
            (artifact_root / "artifact_treport_operational_readiness.json").read_text(
                encoding="utf-8"
            )
        )
        issue = next(
            issue
            for issue in signal_payload["data_quality"]["issues"]
            if issue["code"] == "readiness_cash_derivation_failure_ratio"
        )

        assert exit_code == 0
        assert user_ready["readiness"]["status"] == "ready_with_warnings"
        assert user_ready["readiness"]["disclosures"] == [
            {
                "code": "readiness_cash_derivation_failure_ratio",
                "severity": "medium",
                "message": "Cash derivation warning requires user disclosure.",
                "metric": "cash_derivation_failure_ratio",
                "value": 0.08,
                "threshold": 0.05,
            }
        ]
        assert issue["severity"] == "medium"
        assert issue["scope"] == "operational_readiness"
        assert issue["message"]
        assert readiness_artifact["security_coverage"] == {
            "security_resolution_available": True,
            "mapped_ticker_candidate_count": 3,
            "unresolved_ticker_candidate_count": 1,
            "unknown_count": 1,
            "non_ticker_excluded_count": 2,
            "reviewed_mapping_applied_count": 1,
            "reviewed_exclusion_applied_count": 1,
            "ticker_mapping_coverage_ratio": 0.75,
            "recovery_sample_count": 1,
            "recovery_samples": [
                {
                    "security_id": "SEC_UNKNOWN",
                    "name": "Unknown Instrument",
                    "observed_row_count": 1,
                    "observed_etf_count": 1,
                    "observed_date_count": 1,
                    "name_alias_count": 0,
                    "security_classification": "unknown",
                }
            ],
        }
        assert "readiness_cash_derivation_failure_ratio=0.08" in (
            signal_payload["data_quality"]["coverage_notes"]
        )
        rendered = json.dumps(
            {
                "user_ready": user_ready["readiness"],
                "data_quality": signal_payload["data_quality"],
            },
            ensure_ascii=False,
        )
        assert str(tmp_path) not in rendered
        assert "source_sample_rows" not in rendered

    run_async(scenario())


def test_agent_treport_run_report_rejects_warning_readiness_without_disclosures(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        manifest_path = _copy_cli_ready_operational_export(tmp_path)
        readiness_path = tmp_path / "readiness.json"
        _write_json(
            readiness_path,
            _cli_ready_readiness(
                manifest_path,
                status="ready_with_warnings",
                warnings=[],
            ),
        )
        sqlite_path = tmp_path / "state" / "treport.sqlite3"
        artifact_root = tmp_path / "artifacts"
        model_calls: list[ModelProviderConfig] = []

        exit_code = await run_cli_async(
            [
                "run-report",
                "--holdings-source",
                "operational",
                "--holdings-path",
                str(manifest_path),
                "--focus-etf-id",
                "etf_focus_ai",
                "--observed-partitions",
                "3",
                "--readiness-path",
                str(readiness_path),
                "--sqlite-path",
                str(sqlite_path),
                "--artifact-root",
                str(artifact_root),
                "--model",
                "codex",
            ],
            stdout=stdout,
            stderr=stderr,
            model_client_factory=lambda config: model_calls.append(config) or FakeModelClient([]),
        )

        assert exit_code == 2
        assert stdout.getvalue() == ""
        assert "ready_with_warnings readiness requires disclosure warnings" in (
            stderr.getvalue()
        )
        assert model_calls == []
        assert not sqlite_path.exists()
        assert not artifact_root.exists()

    run_async(scenario())


def test_agent_treport_run_report_hold_override_outputs_operator_review_only(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        manifest_path = _copy_cli_ready_operational_export(tmp_path)
        readiness_path = tmp_path / "readiness.json"
        _write_json(
            readiness_path,
            _cli_ready_readiness(
                manifest_path,
                status="hold",
                user_ready_allowed=False,
                reasons=[
                    {
                        "code": "cash_derivation_failure_ratio",
                        "severity": "hold",
                        "message": "Cash derivation risk requires operator review.",
                        "metric": "cash_derivation_failure_ratio",
                        "value": 0.25,
                        "threshold": 0.2,
                    }
                ],
                next_actions=[
                    {
                        "code": "review_cash_derivation_risk",
                        "required": True,
                        "for_codes": ["cash_derivation_failure_ratio"],
                    }
                ],
            ),
        )
        sqlite_path = tmp_path / "state" / "treport.sqlite3"
        artifact_root = tmp_path / "artifacts"

        exit_code = await run_cli_async(
            [
                "run-report",
                "--run-id",
                "run_treport_operational_hold_review",
                "--holdings-source",
                "operational",
                "--holdings-path",
                str(manifest_path),
                "--focus-etf-id",
                "etf_focus_ai",
                "--observed-partitions",
                "3",
                "--readiness-path",
                str(readiness_path),
                "--allow-operator-review-output",
                "--sqlite-path",
                str(sqlite_path),
                "--artifact-root",
                str(artifact_root),
                "--model",
                "codex",
            ],
            stdout=stdout,
            model_client_factory=lambda _config: FakeModelClient(
                [
                    ModelResponse(
                        message=Message(
                            role="assistant",
                            content=(TextBlock(text="hold review commentary"),),
                        )
                    )
                ]
            ),
        )

        payload = json.loads(stdout.getvalue())
        review = payload["output"]["operator_review_only"]
        signal_payload = json.loads(
            (artifact_root / "artifact_treport_signal_payload.json").read_text(
                encoding="utf-8"
            )
        )
        issue = next(
            issue
            for issue in signal_payload["data_quality"]["issues"]
            if issue["code"] == "readiness_cash_derivation_failure_ratio"
        )

        assert exit_code == 0
        assert payload["status"] == "succeeded"
        assert "user_ready" not in payload["output"]
        assert review["reason"] == "readiness_hold"
        assert review["readiness_artifact_id"] == "artifact_treport_operational_readiness"
        assert review["artifacts"]["readiness"]["name"] == "operational_readiness.json"
        assert review["artifacts"]["markdown_report"]["artifact_id"] == (
            "artifact_treport_report"
        )
        assert review["commands"]["inspect"].startswith("agent-treport inspect ")
        assert issue["severity"] == "high"
        assert issue["scope"] == "operational_readiness"
        assert "readiness_cash_derivation_failure_ratio=0.25" in (
            signal_payload["data_quality"]["coverage_notes"]
        )

    run_async(scenario())


def test_agent_treport_run_report_missing_readiness_override_outputs_operator_review_only(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        manifest_path = _copy_cli_ready_operational_export(tmp_path)
        sqlite_path = tmp_path / "state" / "treport.sqlite3"
        artifact_root = tmp_path / "artifacts"

        exit_code = await run_cli_async(
            [
                "run-report",
                "--run-id",
                "run_treport_operational_missing_readiness_review",
                "--holdings-source",
                "operational",
                "--holdings-path",
                str(manifest_path),
                "--focus-etf-id",
                "etf_focus_ai",
                "--observed-partitions",
                "3",
                "--allow-operator-review-output",
                "--sqlite-path",
                str(sqlite_path),
                "--artifact-root",
                str(artifact_root),
                "--model",
                "codex",
            ],
            stdout=stdout,
            model_client_factory=lambda _config: FakeModelClient(
                [
                    ModelResponse(
                        message=Message(
                            role="assistant",
                            content=(TextBlock(text="missing readiness review"),),
                        )
                    )
                ]
            ),
        )

        payload = json.loads(stdout.getvalue())
        review = payload["output"]["operator_review_only"]
        readiness_artifact_path = (
            artifact_root / "artifact_treport_operational_readiness.json"
        )
        readiness_artifact = json.loads(
            readiness_artifact_path.read_text(encoding="utf-8")
        )
        signal_payload = json.loads(
            (artifact_root / "artifact_treport_signal_payload.json").read_text(
                encoding="utf-8"
            )
        )
        issue = next(
            issue
            for issue in signal_payload["data_quality"]["issues"]
            if issue["code"] == "readiness_readiness_not_provided"
        )

        assert exit_code == 0
        assert "user_ready" not in payload["output"]
        assert review["reason"] == "readiness_not_provided"
        assert review["readiness_artifact_id"] == "artifact_treport_operational_readiness"
        assert readiness_artifact_path.is_file()
        assert readiness_artifact["status"] == "not_provided"
        assert readiness_artifact["reasons"][0]["code"] == "readiness_not_provided"
        assert readiness_artifact["focus_etf_id"] == "etf_focus_ai"
        assert readiness_artifact["requested_observed_partitions"] == 3
        assert readiness_artifact["current_date"] == "2026-05-11"
        assert readiness_artifact["previous_date"] == "2026-05-08"
        assert "export_fingerprint" not in readiness_artifact
        assert readiness_artifact["final_user_ready_requirements"] == {
            "readiness_user_ready_allowed": False,
            "run_report_status_required": "succeeded",
            "report_quality_status_required": "passed",
            "warning_disclosure_required": False,
        }
        assert issue["severity"] == "high"
        assert issue["scope"] == "operational_readiness"
        assert "holdings_path" not in readiness_artifact
        assert "sync_metadata_path" not in readiness_artifact
        assert "source_path" not in json.dumps(readiness_artifact, ensure_ascii=False)
        assert "source_sample_rows" not in json.dumps(
            readiness_artifact,
            ensure_ascii=False,
        )

    run_async(scenario())


def test_agent_treport_operational_ready_handoff_e2e_user_ready_and_inspect(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        readiness_stdout = StringIO()
        run_stdout = StringIO()
        inspect_stdout = StringIO()
        manifest_path = _copy_cli_ready_operational_export(tmp_path)
        readiness_path = tmp_path / "readiness.json"
        sqlite_path = tmp_path / "state" / "treport.sqlite3"
        artifact_root = tmp_path / "artifacts"

        readiness_exit = await run_cli_async(
            [
                "check-operational-readiness",
                "--holdings-path",
                str(manifest_path),
                "--focus-etf-id",
                "etf_focus_ai",
                "--observed-partitions",
                "3",
            ],
            stdout=readiness_stdout,
            readiness_now=_cli_readiness_now,
        )
        readiness_path.write_text(readiness_stdout.getvalue(), encoding="utf-8")

        run_exit = await run_cli_async(
            [
                "run-report",
                "--run-id",
                "run_treport_operational_ready_e2e",
                "--holdings-source",
                "operational",
                "--holdings-path",
                str(manifest_path),
                "--focus-etf-id",
                "etf_focus_ai",
                "--observed-partitions",
                "3",
                "--readiness-path",
                str(readiness_path),
                "--sqlite-path",
                str(sqlite_path),
                "--artifact-root",
                str(artifact_root),
                "--model",
                "codex",
            ],
            stdout=run_stdout,
            model_client_factory=lambda _config: FakeModelClient(
                [
                    ModelResponse(
                        message=Message(
                            role="assistant",
                            content=(TextBlock(text="ready e2e commentary"),),
                        )
                    )
                ]
            ),
        )

        inspect_exit = await run_cli_async(
            [
                "inspect",
                "--run-id",
                "run_treport_operational_ready_e2e",
                "--sqlite-path",
                str(sqlite_path),
            ],
            stdout=inspect_stdout,
        )

        readiness = json.loads(readiness_stdout.getvalue())
        run_payload = json.loads(run_stdout.getvalue())
        inspect_payload = json.loads(inspect_stdout.getvalue())
        user_ready = run_payload["output"]["user_ready"]
        inspect_artifact_ids = {
            artifact["artifact_id"] for artifact in inspect_payload["artifacts"]
        }

        assert readiness_exit == 0
        assert readiness["status"] == "ready"
        assert run_exit == 0
        assert inspect_exit == 0
        assert run_payload["status"] == "succeeded"
        assert user_ready["readiness"]["status"] == "ready"
        assert user_ready["readiness"]["disclosures"] == []
        assert {
            "readiness",
            "canonical_payload",
            "markdown_report",
            "html_report",
            "telegram_alert",
            "quality_report",
        }.issubset(user_ready["artifacts"])
        assert {
            "artifact_treport_operational_readiness",
            "artifact_treport_signal_payload",
            "artifact_treport_report",
            "artifact_treport_html_report",
            "artifact_treport_telegram_alert",
            "artifact_treport_quality",
        }.issubset(inspect_artifact_ids)

    run_async(scenario())


def test_agent_treport_native_collection_handoff_e2e_user_ready(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        collect_stdout = StringIO()
        readiness_stdout = StringIO()
        run_stdout = StringIO()
        fixture_path = _write_cli_native_collection_fixture(tmp_path)
        dest_dir = tmp_path / "native_collected"
        readiness_path = tmp_path / "native_readiness.json"
        sqlite_path = tmp_path / "state" / "treport.sqlite3"
        artifact_root = tmp_path / "artifacts"

        collect_exit = await run_cli_async(
            [
                "collect-holdings-fixture",
                "--fixture-path",
                str(fixture_path),
                "--dest",
                str(dest_dir),
                "--observed-partitions",
                "2",
            ],
            stdout=collect_stdout,
            collection_now=lambda: datetime(2026, 5, 11, 1, 0, tzinfo=UTC),
        )
        manifest_path = dest_dir / "url_holdings_cumulative.json"
        readiness_exit = await run_cli_async(
            [
                "check-operational-readiness",
                "--holdings-path",
                str(manifest_path),
                "--focus-etf-id",
                "etf_focus_ai",
                "--observed-partitions",
                "2",
            ],
            stdout=readiness_stdout,
            readiness_now=_cli_readiness_now,
        )
        readiness_path.write_text(readiness_stdout.getvalue(), encoding="utf-8")

        run_exit = await run_cli_async(
            [
                "run-report",
                "--run-id",
                "run_treport_native_collection_ready_e2e",
                "--holdings-source",
                "operational",
                "--holdings-path",
                str(manifest_path),
                "--focus-etf-id",
                "etf_focus_ai",
                "--observed-partitions",
                "2",
                "--readiness-path",
                str(readiness_path),
                "--sqlite-path",
                str(sqlite_path),
                "--artifact-root",
                str(artifact_root),
                "--model",
                "codex",
            ],
            stdout=run_stdout,
            model_client_factory=lambda _config: FakeModelClient(
                [
                    ModelResponse(
                        message=Message(
                            role="assistant",
                            content=(TextBlock(text="native collection commentary"),),
                        )
                    )
                ]
            ),
        )

        readiness = json.loads(readiness_stdout.getvalue())
        run_payload = json.loads(run_stdout.getvalue())
        signal_payload = json.loads(
            (artifact_root / "artifact_treport_signal_payload.json").read_text(
                encoding="utf-8"
            )
        )
        issue_codes = {
            issue["code"] for issue in signal_payload["data_quality"]["issues"]
        }

        assert collect_exit == 0
        assert readiness_exit == 0
        assert readiness["status"] == "ready_with_warnings"
        assert readiness["readiness_evidence_type"] == "native_collection"
        assert run_exit == 0
        assert run_payload["status"] == "succeeded"
        assert "user_ready" in run_payload["output"]
        assert "operator_review_only" not in run_payload["output"]
        assert run_payload["output"]["user_ready"]["readiness"]["status"] == (
            "ready_with_warnings"
        )
        assert run_payload["output"]["user_ready"]["readiness"]["disclosures"] == [
            {
                "code": "readiness_fixture_backed_collection",
                "severity": "medium",
                "message": "Native collection used fixture holdings only.",
            }
        ]
        assert "readiness_fixture_backed_collection" in issue_codes
        assert "operational_sync_metadata_unavailable" not in issue_codes

    run_async(scenario())


def test_agent_treport_source_acquired_native_history_handoff_e2e_user_ready(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        run_stdout = StringIO()
        source_dir = tmp_path / "source_catalog"
        history_dir = tmp_path / "holdings_history"
        export_dir = tmp_path / "latest_comparison"
        readiness_path = tmp_path / "readiness.json"
        resolution_path = tmp_path / "security_resolution.json"
        sqlite_path = tmp_path / "state" / "treport.sqlite3"
        artifact_root = tmp_path / "artifacts"
        etfs = [
            {
                "provider_etf_id": "2ETF35",
                "etf_id": "etf_focus_ai",
                "etf_name": "KODEX AI ETF",
            },
            {
                "provider_etf_id": "2ETF36",
                "etf_id": "etf_peer_robotics",
                "etf_name": "KODEX Robotics ETF",
            },
        ]
        fixture_path = _write_cli_source_handoff_fixture(
            tmp_path,
            etfs=etfs,
            holdings_by_provider_and_date={
                ("2ETF35", "2026-05-08"): [
                    _source_holding(
                        security_id="sec_nvda",
                        ticker="NVDA",
                        name="NVIDIA Corp.",
                        weight_percent=6.0,
                    )
                ],
                ("2ETF35", "2026-05-11"): [
                    _source_holding(
                        security_id="sec_nvda",
                        ticker="NVDA",
                        name="NVIDIA Corp.",
                        weight_percent=7.5,
                    )
                ],
                ("2ETF36", "2026-05-08"): [
                    _source_holding(
                        security_id="sec_msft",
                        ticker="MSFT",
                        name="Microsoft Corp.",
                        weight_percent=5.5,
                    )
                ],
                ("2ETF36", "2026-05-11"): [
                    _source_holding(
                        security_id="sec_msft",
                        ticker="MSFT",
                        name="Microsoft Corp.",
                        weight_percent=6.25,
                    )
                ],
            },
        )
        _write_cli_security_resolution(
            resolution_path,
            security_ids=["sec_msft", "sec_nvda"],
        )

        manifest_path, export_summary, readiness = (
            await _prepare_cli_source_acquired_readiness(
                fixture_path=fixture_path,
                source_dir=source_dir,
                history_dir=history_dir,
                export_dir=export_dir,
                readiness_path=readiness_path,
                security_resolution_path=resolution_path,
            )
        )
        run_exit = await run_cli_async(
            [
                "run-report",
                "--run-id",
                "run_treport_source_acquired_ready_e2e",
                "--holdings-source",
                "operational",
                "--holdings-path",
                str(manifest_path),
                "--focus-etf-id",
                "etf_focus_ai",
                "--observed-partitions",
                "2",
                "--readiness-path",
                str(readiness_path),
                "--sqlite-path",
                str(sqlite_path),
                "--artifact-root",
                str(artifact_root),
                "--model",
                "codex",
            ],
            stdout=run_stdout,
            model_client_factory=lambda _config: FakeModelClient(
                [
                    ModelResponse(
                        message=Message(
                            role="assistant",
                            content=(TextBlock(text="source acquired ready commentary"),),
                        )
                    )
                ]
            ),
        )

        run_payload = json.loads(run_stdout.getvalue())
        user_ready = run_payload["output"]["user_ready"]
        readiness_artifact = json.loads(
            (artifact_root / "artifact_treport_operational_readiness.json").read_text(
                encoding="utf-8"
            )
        )
        source_summary = json.loads(
            (history_dir / "source_acquisition_summary.json").read_text(
                encoding="utf-8"
            )
        )
        signal_payload = json.loads(
            (artifact_root / "artifact_treport_signal_payload.json").read_text(
                encoding="utf-8"
            )
        )
        report_visible = json.dumps(
            {
                "user_ready": user_ready["readiness"],
                "data_quality": signal_payload["data_quality"],
            },
            ensure_ascii=False,
        )

        assert run_exit == 0
        assert export_summary["collection_source_type"] == "native_history"
        assert export_summary["active_etf_coverage"]["coverage_ratio"] == 1.0
        assert export_summary["security_coverage"]["security_resolution_available"] is True
        assert export_summary["normalized_output"]["fingerprint"] == (
            compute_operational_export_fingerprint(manifest_path)
        )
        assert readiness["status"] == "ready"
        assert readiness["readiness_evidence_type"] == "native_history"
        assert readiness["collection_summary_path"] == "collection_summary.json"
        assert readiness["export_fingerprint"] == export_summary["normalized_output"][
            "fingerprint"
        ]
        assert run_payload["status"] == "succeeded"
        assert "user_ready" in run_payload["output"]
        assert "operator_review_only" not in run_payload["output"]
        assert user_ready["readiness"]["status"] == "ready"
        assert user_ready["readiness"]["disclosures"] == []
        assert user_ready["artifacts"]["readiness"]["artifact_id"] == (
            "artifact_treport_operational_readiness"
        )
        assert readiness_artifact["status"] == "ready"
        assert readiness_artifact["export_fingerprint"] == readiness["export_fingerprint"]
        assert source_summary["schema_version"] == (
            "agent_treport.source_acquisition.summary.v1"
        )
        assert source_summary["aggregate_counts"]["fetched"] == 2
        rendered_source_summary = json.dumps(source_summary, ensure_ascii=False)
        assert "provider_etf_id" not in rendered_source_summary
        assert str(tmp_path) not in rendered_source_summary
        assert "https://" not in rendered_source_summary
        for forbidden in (
            "provider_etf_id",
            "2ETF35",
            "2ETF36",
            "source_acquisition_summary",
            "source_provider_id",
            "failure_code_class",
            "retry_attempt_count",
            "https://",
            str(tmp_path),
        ):
            assert forbidden not in report_visible

    run_async(scenario())


def test_agent_treport_source_acquired_native_history_without_security_resolution_discloses_warning(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        source_dir = tmp_path / "source_catalog"
        history_dir = tmp_path / "holdings_history"
        export_dir = tmp_path / "latest_comparison"
        readiness_path = tmp_path / "readiness.json"
        sqlite_path = tmp_path / "state" / "treport.sqlite3"
        artifact_root = tmp_path / "artifacts"
        fixture_path = _write_cli_source_handoff_fixture(
            tmp_path,
            filename="source_warning_fixture.json",
            etfs=[
                {
                    "provider_etf_id": "2ETF35",
                    "etf_id": "etf_focus_ai",
                    "etf_name": "KODEX AI ETF",
                }
            ],
            holdings_by_provider_and_date={
                ("2ETF35", "2026-05-08"): [
                    _source_holding(weight_percent=6.0)
                ],
                ("2ETF35", "2026-05-11"): [
                    _source_holding(weight_percent=7.5)
                ],
            },
        )

        manifest_path, export_summary, readiness = (
            await _prepare_cli_source_acquired_readiness(
                fixture_path=fixture_path,
                source_dir=source_dir,
                history_dir=history_dir,
                export_dir=export_dir,
                readiness_path=readiness_path,
            )
        )
        run_stdout = StringIO()
        run_exit = await run_cli_async(
            [
                "run-report",
                "--run-id",
                "run_treport_source_acquired_warning_e2e",
                "--holdings-source",
                "operational",
                "--holdings-path",
                str(manifest_path),
                "--focus-etf-id",
                "etf_focus_ai",
                "--observed-partitions",
                "2",
                "--readiness-path",
                str(readiness_path),
                "--sqlite-path",
                str(sqlite_path),
                "--artifact-root",
                str(artifact_root),
                "--model",
                "codex",
            ],
            stdout=run_stdout,
            model_client_factory=lambda _config: FakeModelClient(
                [
                    ModelResponse(
                        message=Message(
                            role="assistant",
                            content=(TextBlock(text="source acquired warning commentary"),),
                        )
                    )
                ]
            ),
        )

        run_payload = json.loads(run_stdout.getvalue())
        user_ready = run_payload["output"]["user_ready"]
        signal_payload = json.loads(
            (artifact_root / "artifact_treport_signal_payload.json").read_text(
                encoding="utf-8"
            )
        )
        issue_codes = {
            issue["code"] for issue in signal_payload["data_quality"]["issues"]
        }
        report_visible = json.dumps(
            {
                "user_ready": user_ready["readiness"],
                "data_quality": signal_payload["data_quality"],
            },
            ensure_ascii=False,
        )

        assert run_exit == 0
        assert export_summary["security_coverage"]["security_resolution_available"] is False
        assert readiness["status"] == "ready_with_warnings"
        assert readiness["warnings"] == [
            {
                "code": "security_resolution_missing",
                "severity": "warning",
                "message": "Native history export was not run with reviewed security resolution.",
                "metric": "security_resolution_available",
                "value": False,
                "threshold": True,
            }
        ]
        assert user_ready["readiness"]["status"] == "ready_with_warnings"
        assert user_ready["readiness"]["disclosures"] == [
            {
                "code": "readiness_security_resolution_missing",
                "severity": "medium",
                "message": "Native history export was not run with reviewed security resolution.",
                "metric": "security_resolution_available",
                "value": False,
                "threshold": True,
            }
        ]
        assert "readiness_security_resolution_missing" in issue_codes
        assert "readiness_security_resolution_available=False" in (
            signal_payload["data_quality"]["coverage_notes"]
        )
        assert "operator_review_only" not in run_payload["output"]
        for forbidden in (
            "provider_etf_id",
            "2ETF35",
            "source_acquisition_summary",
            "source_provider_id",
            "failure_code_class",
            "retry_attempt_count",
            "https://",
            str(tmp_path),
        ):
            assert forbidden not in report_visible

    run_async(scenario())


def test_agent_treport_source_acquired_native_history_hold_override_outputs_operator_review_only(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        source_dir = tmp_path / "source_catalog"
        history_dir = tmp_path / "holdings_history"
        export_dir = tmp_path / "latest_comparison"
        readiness_path = tmp_path / "readiness.json"
        resolution_path = tmp_path / "security_resolution.json"
        sqlite_path = tmp_path / "state" / "treport.sqlite3"
        artifact_root = tmp_path / "artifacts"
        etfs = [
            {
                "provider_etf_id": f"2ETF3{index}",
                "etf_id": etf_id,
                "etf_name": f"KODEX Test ETF {index}",
            }
            for index, etf_id in enumerate(
                [
                    "etf_focus_ai",
                    "etf_peer_1",
                    "etf_peer_2",
                    "etf_peer_3",
                    "etf_peer_4",
                    "etf_peer_5",
                ],
                start=5,
            )
        ]
        holdings_by_provider_and_date: dict[
            tuple[str, str], list[dict[str, object]]
        ] = {}
        security_ids: list[str] = []
        for etf in etfs:
            security_id = f"sec_{etf['etf_id'].removeprefix('etf_')}"
            security_ids.append(security_id)
            provider_etf_id = etf["provider_etf_id"]
            holdings_by_provider_and_date[(provider_etf_id, "2026-05-11")] = [
                    _source_holding(
                        security_id=security_id,
                        ticker=None,
                        name=f"{etf['etf_name']} Holding",
                        weight_percent=7.5,
                    )
            ]
            if etf["etf_id"] not in {"etf_peer_4", "etf_peer_5"}:
                holdings_by_provider_and_date[(provider_etf_id, "2026-05-08")] = [
                        _source_holding(
                            security_id=security_id,
                            ticker=None,
                            name=f"{etf['etf_name']} Holding",
                            weight_percent=6.0,
                        )
                ]
        fixture_path = _write_cli_source_handoff_fixture(
            tmp_path,
            filename="source_hold_fixture.json",
            etfs=etfs,
            holdings_by_provider_and_date=holdings_by_provider_and_date,
        )
        _write_cli_security_resolution(resolution_path, security_ids=security_ids[:1])

        manifest_path, _, readiness = await _prepare_cli_source_acquired_readiness(
            fixture_path=fixture_path,
            source_dir=source_dir,
            history_dir=history_dir,
            export_dir=export_dir,
            readiness_path=readiness_path,
            security_resolution_path=resolution_path,
        )
        run_stdout = StringIO()
        run_exit = await run_cli_async(
            [
                "run-report",
                "--run-id",
                "run_treport_source_acquired_hold_review",
                "--holdings-source",
                "operational",
                "--holdings-path",
                str(manifest_path),
                "--focus-etf-id",
                "etf_focus_ai",
                "--observed-partitions",
                "2",
                "--readiness-path",
                str(readiness_path),
                "--allow-operator-review-output",
                "--sqlite-path",
                str(sqlite_path),
                "--artifact-root",
                str(artifact_root),
                "--model",
                "codex",
            ],
            stdout=run_stdout,
            model_client_factory=lambda _config: FakeModelClient(
                [
                    ModelResponse(
                        message=Message(
                            role="assistant",
                            content=(TextBlock(text="source acquired hold commentary"),),
                        )
                    )
                ]
            ),
        )

        run_payload = json.loads(run_stdout.getvalue())
        review = run_payload["output"]["operator_review_only"]
        signal_payload = json.loads(
            (artifact_root / "artifact_treport_signal_payload.json").read_text(
                encoding="utf-8"
            )
        )
        issue = next(
            issue
            for issue in signal_payload["data_quality"]["issues"]
            if issue["code"] == "readiness_low_ticker_mapping_coverage"
        )
        report_visible = json.dumps(
            {
                "operator_review_only": {
                    "reason": review["reason"],
                    "readiness_artifact_id": review["readiness_artifact_id"],
                },
                "data_quality": signal_payload["data_quality"],
            },
            ensure_ascii=False,
        )

        assert readiness["status"] == "hold"
        assert readiness["reasons"][0]["code"] == "low_ticker_mapping_coverage"
        assert run_exit == 0
        assert run_payload["status"] == "succeeded"
        assert "user_ready" not in run_payload["output"]
        assert review["reason"] == "readiness_hold"
        assert review["readiness_artifact_id"] == (
            "artifact_treport_operational_readiness"
        )
        assert issue["severity"] == "high"
        assert issue["scope"] == "operational_readiness"
        assert any(
            note.startswith("readiness_ticker_mapping_coverage_ratio=")
            for note in signal_payload["data_quality"]["coverage_notes"]
        )
        for forbidden in (
            "provider_etf_id",
            "2ETF35",
            "2ETF36",
            "source_provider_id",
            "failure_code_class",
            "retry_attempt_count",
            "missing_active_etf_ids",
            "etf_peer_4",
            "etf_peer_5",
            "https://",
            str(tmp_path),
        ):
            assert forbidden not in report_visible

    run_async(scenario())


def test_agent_treport_source_acquired_native_history_non_focus_warning_allows_user_ready(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        source_dir = tmp_path / "source_catalog"
        history_dir = tmp_path / "holdings_history"
        export_dir = tmp_path / "latest_comparison"
        readiness_path = tmp_path / "readiness.json"
        resolution_path = tmp_path / "security_resolution.json"
        sqlite_path = tmp_path / "state" / "treport.sqlite3"
        artifact_root = tmp_path / "artifacts"
        etfs = [
            {
                "provider_etf_id": f"2ETF4{index}",
                "etf_id": etf_id,
                "etf_name": f"KODEX Coverage ETF {index}",
            }
            for index, etf_id in enumerate(
                [
                    "etf_focus_ai",
                    "etf_peer_1",
                    "etf_peer_2",
                    "etf_peer_3",
                    "etf_peer_4",
                    "etf_peer_5",
                ],
                start=0,
            )
        ]
        holdings_by_provider_and_date: dict[
            tuple[str, str], list[dict[str, object]]
        ] = {}
        security_ids: list[str] = []
        for etf in etfs:
            security_id = f"sec_{etf['etf_id'].removeprefix('etf_')}"
            security_ids.append(security_id)
            provider_etf_id = etf["provider_etf_id"]
            holdings_by_provider_and_date[(provider_etf_id, "2026-05-11")] = [
                _source_holding(
                    security_id=security_id,
                    ticker=security_id.removeprefix("sec_").upper(),
                    name=f"{etf['etf_name']} Holding",
                    weight_percent=7.5,
                )
            ]
            if etf["etf_id"] != "etf_peer_5":
                holdings_by_provider_and_date[(provider_etf_id, "2026-05-08")] = [
                    _source_holding(
                        security_id=security_id,
                        ticker=security_id.removeprefix("sec_").upper(),
                        name=f"{etf['etf_name']} Holding",
                        weight_percent=6.0,
                    )
                ]
        fixture_path = _write_cli_source_handoff_fixture(
            tmp_path,
            filename="source_non_focus_warning_fixture.json",
            etfs=etfs,
            holdings_by_provider_and_date=holdings_by_provider_and_date,
        )
        _write_cli_security_resolution(resolution_path, security_ids=security_ids)

        manifest_path, _, readiness = await _prepare_cli_source_acquired_readiness(
            fixture_path=fixture_path,
            source_dir=source_dir,
            history_dir=history_dir,
            export_dir=export_dir,
            readiness_path=readiness_path,
            security_resolution_path=resolution_path,
        )
        run_stdout = StringIO()
        run_exit = await run_cli_async(
            [
                "run-report",
                "--run-id",
                "run_treport_source_acquired_non_focus_warning",
                "--holdings-source",
                "operational",
                "--holdings-path",
                str(manifest_path),
                "--focus-etf-id",
                "etf_focus_ai",
                "--observed-partitions",
                "2",
                "--readiness-path",
                str(readiness_path),
                "--sqlite-path",
                str(sqlite_path),
                "--artifact-root",
                str(artifact_root),
                "--model",
                "codex",
            ],
            stdout=run_stdout,
            model_client_factory=lambda _config: FakeModelClient(
                [
                    ModelResponse(
                        message=Message(
                            role="assistant",
                            content=(TextBlock(text="source acquired coverage warning"),),
                        )
                    )
                ]
            ),
        )

        run_payload = json.loads(run_stdout.getvalue())
        user_ready = run_payload["output"]["user_ready"]
        signal_payload = json.loads(
            (artifact_root / "artifact_treport_signal_payload.json").read_text(
                encoding="utf-8"
            )
        )
        report_visible = json.dumps(
            {
                "user_ready": user_ready["readiness"],
                "data_quality": signal_payload["data_quality"],
            },
            ensure_ascii=False,
        )

        assert readiness["status"] == "ready_with_warnings"
        assert readiness["warnings"][0]["code"] == "active_etf_coverage_gap"
        assert readiness["warnings"][0]["value"] == 0.8
        assert readiness["warnings"][0]["details"]["missing_active_etf_ids"] == [
            "etf_peer_5"
        ]
        assert run_exit == 0
        assert run_payload["status"] == "succeeded"
        assert "operator_review_only" not in run_payload["output"]
        assert user_ready["readiness"]["status"] == "ready_with_warnings"
        assert user_ready["readiness"]["disclosures"] == [
                {
                    "code": "readiness_active_etf_coverage_gap",
                    "severity": "medium",
                    "message": (
                        "Some non-focus active ETFs are missing one side of the "
                        "comparison window; this is operator diagnostic coverage "
                        "and does not block the focus handoff."
                    ),
                    "metric": "non_focus_active_etf_coverage_ratio",
                    "value": 0.8,
                    "threshold": 0.8,
            }
        ]
        assert "readiness_non_focus_active_etf_coverage_ratio=0.8" in (
            signal_payload["data_quality"]["coverage_notes"]
        )
        for forbidden in (
            "provider_etf_id",
            "2ETF40",
            "source_provider_id",
            "missing_active_etf_ids",
            "etf_peer_5",
            "failure_code_class",
            "retry_attempt_count",
            "https://",
            str(tmp_path),
        ):
            assert forbidden not in report_visible

    run_async(scenario())


def test_agent_treport_source_acquired_failed_readiness_blocks_run_report_before_resources(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        source_dir = tmp_path / "source_catalog"
        history_dir = tmp_path / "holdings_history"
        export_dir = tmp_path / "latest_comparison"
        readiness_path = tmp_path / "readiness.json"
        resolution_path = tmp_path / "security_resolution.json"
        sqlite_path = tmp_path / "state" / "treport.sqlite3"
        artifact_root = tmp_path / "artifacts"
        etfs = [
            {
                "provider_etf_id": "2ETF35",
                "etf_id": "etf_focus_ai",
                "etf_name": "KODEX AI ETF",
            },
            {
                "provider_etf_id": "2ETF36",
                "etf_id": "etf_peer_robotics",
                "etf_name": "KODEX Robotics ETF",
            },
        ]
        fixture_path = _write_cli_source_handoff_fixture(
            tmp_path,
            filename="source_failed_readiness_fixture.json",
            etfs=etfs,
            holdings_by_provider_and_date={
                ("2ETF35", "2026-05-11"): [
                    _source_holding(weight_percent=7.5)
                ],
                ("2ETF36", "2026-05-08"): [
                    _source_holding(
                        security_id="sec_msft",
                        ticker="MSFT",
                        name="Microsoft Corp.",
                        weight_percent=5.5,
                    )
                ],
                ("2ETF36", "2026-05-11"): [
                    _source_holding(
                        security_id="sec_msft",
                        ticker="MSFT",
                        name="Microsoft Corp.",
                        weight_percent=6.25,
                    )
                ],
            },
        )
        _write_cli_security_resolution(
            resolution_path,
            security_ids=["sec_msft", "sec_nvda"],
        )

        manifest_path, _, readiness = await _prepare_cli_source_acquired_readiness(
            fixture_path=fixture_path,
            source_dir=source_dir,
            history_dir=history_dir,
            export_dir=export_dir,
            readiness_path=readiness_path,
            security_resolution_path=resolution_path,
        )
        run_stdout = StringIO()
        run_stderr = StringIO()
        model_calls: list[ModelProviderConfig] = []
        run_exit = await run_cli_async(
            [
                "run-report",
                "--run-id",
                "run_treport_source_acquired_failed_readiness",
                "--holdings-source",
                "operational",
                "--holdings-path",
                str(manifest_path),
                "--focus-etf-id",
                "etf_focus_ai",
                "--observed-partitions",
                "2",
                "--readiness-path",
                str(readiness_path),
                "--allow-operator-review-output",
                "--sqlite-path",
                str(sqlite_path),
                "--artifact-root",
                str(artifact_root),
                "--model",
                "codex",
            ],
            stdout=run_stdout,
            stderr=run_stderr,
            model_client_factory=lambda config: model_calls.append(config)
            or FakeModelClient([]),
        )

        assert readiness["status"] == "failed"
        assert [reason["code"] for reason in readiness["reasons"]] == [
            "focus_current_snapshot_not_found",
            "focus_previous_snapshot_not_found",
        ]
        assert run_exit == 2
        assert run_stdout.getvalue() == ""
        assert "operational readiness status failed blocks run-report" in (
            run_stderr.getvalue()
        )
        assert model_calls == []
        assert not sqlite_path.exists()
        assert not artifact_root.exists()

    run_async(scenario())


def test_agent_treport_cli_failed_domain_run_prints_json_and_returns_exit_1(
    tmp_path: Path,
) -> None:
    class FailingModelClient:
        async def complete(self, request: ModelRequest) -> ModelResponse:
            _ = request
            raise RuntimeError("model unavailable")

    async def scenario() -> None:
        stdout = StringIO()
        sqlite_path = tmp_path / "state" / "treport.sqlite3"
        artifact_root = tmp_path / "artifacts"

        def model_client_factory(_config: ModelProviderConfig) -> ModelClient:
            return FailingModelClient()

        exit_code = await run_cli_async(
            [
                "run-report",
                "--run-id",
                "run_treport_cli_failed",
                "--sqlite-path",
                str(sqlite_path),
                "--artifact-root",
                str(artifact_root),
                "--model",
                "codex",
            ],
            stdout=stdout,
            model_client_factory=model_client_factory,
        )

        payload = json.loads(stdout.getvalue())

        assert exit_code == 1
        assert payload["status"] == "failed"
        assert payload["output"]["reason"] == "model_analysis_failed"
        assert payload["output"]["runtime_failure"]["failed_step"] == "analyze-data"
        assert "user_ready" not in payload["output"]

    run_async(scenario())


def test_agent_treport_cli_store_failure_prints_sanitized_stderr_json_and_empty_stdout(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        blocked_parent = tmp_path / "not-a-directory"
        blocked_parent.write_text("file blocks sqlite parent", encoding="utf-8")

        exit_code = await run_cli_async(
            [
                "run-report",
                "--run-id",
                "run_treport_cli_store_failed",
                "--sqlite-path",
                str(blocked_parent / "treport.sqlite3"),
                "--artifact-root",
                str(tmp_path / "artifacts"),
                "--model",
                "codex",
            ],
            stdout=stdout,
            stderr=stderr,
            model_client_factory=lambda _config: FakeModelClient([]),
        )

        payload = json.loads(stderr.getvalue())

        assert exit_code == 1
        assert stdout.getvalue() == ""
        assert payload == {
            "reason": "run_store_failed",
            "error": {
                "code": "run_store_failed",
                "type": "FileExistsError",
                "message": "run store failed",
            },
        }
        assert "Traceback" not in stderr.getvalue()

    run_async(scenario())


def test_agent_treport_inspect_prints_run_inspection_snapshot_from_sqlite(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        setup_stdout = StringIO()
        inspect_stdout = StringIO()
        sqlite_path = tmp_path / "state" / "treport.sqlite3"
        artifact_root = tmp_path / "artifacts"

        await run_cli_async(
            [
                "run-report",
                "--run-id",
                "run_treport_cli_inspect",
                "--sqlite-path",
                str(sqlite_path),
                "--artifact-root",
                str(artifact_root),
                "--model",
                "codex",
            ],
            stdout=setup_stdout,
            model_client_factory=lambda _config: FakeModelClient(
                [
                    ModelResponse(
                        message=Message(
                            role="assistant",
                            content=(TextBlock(text="inspection model text"),),
                        )
                    )
                ]
            ),
        )

        exit_code = await run_cli_async(
            [
                "inspect",
                "--run-id",
                "run_treport_cli_inspect",
                "--sqlite-path",
                str(sqlite_path),
            ],
            stdout=inspect_stdout,
        )

        store = SQLiteRunStore(str(sqlite_path))
        try:
            expected = await RunInspectionService(store).build_snapshot(
                "run_treport_cli_inspect"
            )
        finally:
            await store.close()

        assert exit_code == 0
        assert inspect_stdout.getvalue().count("\n") == 1
        assert json.loads(inspect_stdout.getvalue()) == expected.model_dump(mode="json")

    run_async(scenario())


def test_agent_treport_inspect_returns_clear_not_found_for_missing_run(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()

        exit_code = await run_cli_async(
            [
                "inspect",
                "--run-id",
                "run_missing",
                "--sqlite-path",
                str(tmp_path / "runtime.sqlite3"),
            ],
            stdout=stdout,
            stderr=stderr,
        )

        assert exit_code == 1
        assert stdout.getvalue() == ""
        assert stderr.getvalue() == "run not found: run_missing\n"

    run_async(scenario())


def test_agent_treport_inspect_store_failure_prints_sanitized_stderr_json_and_empty_stdout(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()

        exit_code = await run_cli_async(
            [
                "inspect",
                "--run-id",
                "run_store_failed",
                "--sqlite-path",
                str(tmp_path / "missing-parent" / "runtime.sqlite3"),
            ],
            stdout=stdout,
            stderr=stderr,
        )

        payload = json.loads(stderr.getvalue())

        assert exit_code == 1
        assert stdout.getvalue() == ""
        assert payload["reason"] == "run_store_failed"
        assert payload["error"]["code"] == "run_store_failed"
        assert payload["error"]["message"] == "run store failed"

    run_async(scenario())


def test_agent_treport_cli_model_factory_failure_prints_sanitized_stderr_json_and_empty_stdout(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()

        def model_client_factory(_config: ModelProviderConfig) -> ModelClient:
            raise RuntimeError("api_key=secret")

        exit_code = await run_cli_async(
            [
                "run-report",
                "--run-id",
                "run_treport_cli_model_failed",
                "--sqlite-path",
                str(tmp_path / "state" / "treport.sqlite3"),
                "--artifact-root",
                str(tmp_path / "artifacts"),
                "--model",
                "codex",
            ],
            stdout=stdout,
            stderr=stderr,
            model_client_factory=model_client_factory,
        )

        payload = json.loads(stderr.getvalue())

        assert exit_code == 1
        assert stdout.getvalue() == ""
        assert payload == {
            "reason": "model_client_failed",
            "error": {
                "code": "model_client_failed",
                "type": "RuntimeError",
                "message": "model client failed",
            },
        }
        assert "secret" not in stderr.getvalue()

    run_async(scenario())


def test_agent_treport_cli_artifact_store_failure_prints_sanitized_stderr_json_and_empty_stdout(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        blocked_parent = tmp_path / "not-a-directory"
        blocked_parent.write_text("file blocks artifact root", encoding="utf-8")

        exit_code = await run_cli_async(
            [
                "run-report",
                "--run-id",
                "run_treport_cli_artifact_failed",
                "--sqlite-path",
                str(tmp_path / "state" / "treport.sqlite3"),
                "--artifact-root",
                str(blocked_parent / "artifacts"),
                "--model",
                "codex",
            ],
            stdout=stdout,
            stderr=stderr,
            model_client_factory=lambda _config: FakeModelClient([]),
        )

        payload = json.loads(stderr.getvalue())

        assert exit_code == 1
        assert stdout.getvalue() == ""
        assert payload == {
            "reason": "artifact_store_failed",
            "error": {
                "code": "artifact_store_failed",
                "type": "FileExistsError",
                "message": "artifact store failed",
            },
        }

    run_async(scenario())


def test_user_ready_contract_requires_success_artifact_refs(tmp_path: Path) -> None:
    result = RunResult(
        run_id="run_treport_contract_failed",
        status="succeeded",
        output={
            "state": {
                "signal_payload_artifact_id": "artifact_treport_signal_payload",
                "report_artifact_id": "artifact_treport_report",
                "html_report_artifact_id": "artifact_treport_html_report",
                "report_quality_artifact_id": "artifact_treport_quality",
            }
        },
        artifacts=(
            ArtifactRef(
                artifact_id="artifact_treport_signal_payload",
                name="signal_payload.json",
                uri=(tmp_path / "signal_payload.json").resolve().as_uri(),
                media_type="application/json",
            ),
            ArtifactRef(
                artifact_id="artifact_treport_report",
                name="report.md",
                uri=(tmp_path / "report.md").resolve().as_uri(),
                media_type="text/markdown",
            ),
            ArtifactRef(
                artifact_id="artifact_treport_quality",
                name="quality.json",
                uri=(tmp_path / "quality.json").resolve().as_uri(),
                media_type="application/json",
            ),
        ),
    )

    with pytest.raises(ValueError, match="missing expected artifact reference: html_report"):
        treport_cli._build_user_ready(
            result=result,
            sqlite_path=tmp_path / "treport.sqlite3",
            artifact_root=tmp_path / "artifacts",
        )


def test_user_ready_contract_requires_telegram_alert_artifact_ref(tmp_path: Path) -> None:
    result = RunResult(
        run_id="run_treport_contract_failed",
        status="succeeded",
        output={
            "state": {
                "signal_payload_artifact_id": "artifact_treport_signal_payload",
                "report_artifact_id": "artifact_treport_report",
                "html_report_artifact_id": "artifact_treport_html_report",
                "telegram_alert_artifact_id": "artifact_treport_telegram_alert",
                "report_quality_artifact_id": "artifact_treport_quality",
            }
        },
        artifacts=(
            ArtifactRef(
                artifact_id="artifact_treport_signal_payload",
                name="signal_payload.json",
                uri=(tmp_path / "signal_payload.json").resolve().as_uri(),
                media_type="application/json",
            ),
            ArtifactRef(
                artifact_id="artifact_treport_report",
                name="report.md",
                uri=(tmp_path / "report.md").resolve().as_uri(),
                media_type="text/markdown",
            ),
            ArtifactRef(
                artifact_id="artifact_treport_html_report",
                name="report.html",
                uri=(tmp_path / "report.html").resolve().as_uri(),
                media_type="text/html",
            ),
            ArtifactRef(
                artifact_id="artifact_treport_quality",
                name="quality.json",
                uri=(tmp_path / "quality.json").resolve().as_uri(),
                media_type="application/json",
            ),
        ),
    )

    with pytest.raises(ValueError, match="missing expected artifact reference: telegram_alert"):
        treport_cli._build_user_ready(
            result=result,
            sqlite_path=tmp_path / "treport.sqlite3",
            artifact_root=tmp_path / "artifacts",
        )


def test_user_ready_file_uri_path_projection_accepts_only_local_file_uris(tmp_path: Path) -> None:
    local_path = tmp_path / "artifact.md"

    assert treport_cli._path_from_file_uri(local_path.resolve().as_uri()) == str(
        local_path.resolve()
    )
    assert treport_cli._path_from_file_uri("file://localhost/C:/tmp/artifact.md") == str(
        Path("C:/tmp/artifact.md").resolve()
    )
    assert treport_cli._path_from_file_uri("https://example.test/artifact.md") is None
    assert treport_cli._path_from_file_uri("file://example.test/artifact.md") is None


def _write_delivery_closure_review_package(
    package_path: Path,
    *,
    run_id: str = "run_delivery_projection",
) -> None:
    package_path.mkdir()
    _write_json(
        package_path / "telegram_delivery_summary.json",
        {
            "schema_version": "agent_treport.telegram_delivery_summary.v1",
            "latest_delivery_status": "duplicate_blocked",
            "run_id": run_id,
            "telegram_alert_artifact_id": "artifact_treport_telegram_alert",
            "target_alias": "default",
            "approval": {"status": "approved", "valid": True},
            "receipt_paths": [
                "telegram_delivery_receipts/sent.json",
                "telegram_delivery_receipts/duplicate.json",
            ],
            "delivery_summary_path": "telegram_delivery_summary.json",
            "message_text": "must not leak",
        },
    )
    _write_json(
        package_path / "daily_publish_closure.json",
        {
            "schema_version": "agent_treport.daily_publish_closure.v1",
            "closure_status": "closure_met",
            "closure_met": True,
            "run_id": run_id,
            "telegram_alert_artifact_id": "artifact_treport_telegram_alert",
            "target_alias": "default",
            "evidence_checks": {
                "duplicate_blocked": "passed",
                "identity_consistency": "passed",
                "live_sent_receipt": "passed",
                "operator_approved_daily_publish_flow": "passed",
                "pre_publish_user_ready": "passed",
                "validation_passed": "passed",
            },
            "receipt_summary": {
                "matching_sent_receipt_count": 1,
                "matching_duplicate_blocked_receipt_count": 1,
                "selected_sent_receipt_path": "telegram_delivery_receipts/sent.json",
                "selected_duplicate_blocked_receipt_path": (
                    "telegram_delivery_receipts/duplicate.json"
                ),
            },
            "warnings": [],
            "limitations": [],
            "source_files": [
                "telegram_delivery_summary.json",
                "telegram_delivery_receipts/sent.json",
                "telegram_delivery_receipts/duplicate.json",
            ],
            "message_text": "must not leak",
        },
    )


def test_project_delivery_closure_review_persists_runstore_review_summary(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        package_path = tmp_path / "package"
        _write_delivery_closure_review_package(package_path)
        delivery_summary_before = (
            package_path / "telegram_delivery_summary.json"
        ).read_bytes()
        closure_before = (package_path / "daily_publish_closure.json").read_bytes()
        sqlite_path = tmp_path / "runtime.sqlite3"
        store = SQLiteRunStore(str(sqlite_path))
        await store.create_run(Run(id="run_delivery_projection", status="succeeded"))
        await store.save_snapshot(
            RunSnapshot(
                run_id="run_delivery_projection",
                step_index=3,
                state={"existing": "value"},
            )
        )
        await store.close()
        stdout = StringIO()
        stderr = StringIO()

        exit_code = await run_cli_async(
            [
                "project-delivery-closure-review",
                "--package-path",
                str(package_path),
                "--sqlite-path",
                str(sqlite_path),
                "--run-id",
                "run_delivery_projection",
                "--subject-id",
                "artifact_treport_telegram_alert",
            ],
            stdout=stdout,
            stderr=stderr,
        )

        assert exit_code == 0
        assert stderr.getvalue() == ""
        payload = json.loads(stdout.getvalue())
        assert payload["run_id"] == "run_delivery_projection"
        assert payload["subject_id"] == "artifact_treport_telegram_alert"
        assert payload["review_summary"]["review_status"] == "passed"
        assert payload["review_summary"]["closure_status"] == "closure_met"
        assert "must not leak" not in stdout.getvalue()
        reopened = SQLiteRunStore(str(sqlite_path))
        snapshot = await reopened.get_latest_snapshot("run_delivery_projection")
        assert snapshot is not None
        assert snapshot.step_index == 3
        assert snapshot.state["existing"] == "value"
        summaries = snapshot.state["agent_pack_review_summaries"]
        assert isinstance(summaries, list | tuple)
        assert len(summaries) == 1
        assert summaries[0]["id"] == "review.external_delivery"
        assert summaries[0]["subject_id"] == "artifact_treport_telegram_alert"
        assert "must not leak" not in json.dumps(summaries, ensure_ascii=False)
        record = await build_trace_export_record(
            store=reopened,
            run_id="run_delivery_projection",
        )
        assert record.review_summaries[0].closure_status == "closure_met"
        await reopened.close()

        second_stdout = StringIO()
        second_stderr = StringIO()
        second_exit_code = await run_cli_async(
            [
                "project-delivery-closure-review",
                "--package-path",
                str(package_path),
                "--sqlite-path",
                str(sqlite_path),
                "--run-id",
                "run_delivery_projection",
                "--subject-id",
                "artifact_treport_telegram_alert",
            ],
            stdout=second_stdout,
            stderr=second_stderr,
        )
        assert second_exit_code == 0
        assert second_stderr.getvalue() == ""
        second_payload = json.loads(second_stdout.getvalue())
        assert second_payload["review_summary_count"] == 1
        rerun_store = SQLiteRunStore(str(sqlite_path))
        rerun_snapshot = await rerun_store.get_latest_snapshot("run_delivery_projection")
        assert rerun_snapshot is not None
        rerun_summaries = rerun_snapshot.state["agent_pack_review_summaries"]
        assert isinstance(rerun_summaries, list | tuple)
        assert len(rerun_summaries) == 1
        await rerun_store.close()

        assert (package_path / "telegram_delivery_summary.json").read_bytes() == (
            delivery_summary_before
        )
        assert (
            package_path / "daily_publish_closure.json"
        ).read_bytes() == closure_before

    run_async(scenario())


def test_project_delivery_closure_review_requires_existing_snapshot(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        package_path = tmp_path / "package"
        _write_delivery_closure_review_package(package_path)
        sqlite_path = tmp_path / "runtime.sqlite3"
        store = SQLiteRunStore(str(sqlite_path))
        await store.create_run(Run(id="run_delivery_projection", status="succeeded"))
        await store.close()
        stdout = StringIO()
        stderr = StringIO()

        exit_code = await run_cli_async(
            [
                "project-delivery-closure-review",
                "--package-path",
                str(package_path),
                "--sqlite-path",
                str(sqlite_path),
                "--run-id",
                "run_delivery_projection",
                "--subject-id",
                "artifact_treport_telegram_alert",
            ],
            stdout=stdout,
            stderr=stderr,
        )

        assert exit_code == 2
        assert stdout.getvalue() == ""
        assert "run snapshot not found: run_delivery_projection" in stderr.getvalue()

    run_async(scenario())
