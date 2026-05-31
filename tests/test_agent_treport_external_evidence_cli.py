from __future__ import annotations

import asyncio
import json
from io import StringIO
from pathlib import Path

from agent_pack.models import Message, ModelResponse, TextBlock
from agent_pack.models_client import FakeModelClient

from agent_treport.cli import run_cli_async

FIXTURE_HOLDINGS = Path("src/agent_treport/fixtures/signal_report/holdings.json")


def run_async(awaitable):
    return asyncio.run(awaitable)


def test_collect_external_evidence_cli_writes_fixture_artifacts(tmp_path: Path) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        evidence_path = tmp_path / "external_evidence.json"
        summary_path = tmp_path / "external_evidence_summary.json"

        exit_code = await run_cli_async(
            [
                "collect-external-evidence",
                "--holdings-source",
                "fixture",
                "--holdings-path",
                str(FIXTURE_HOLDINGS),
                "--providers",
                "fixture_financial,fixture_disclosure,fixture_news",
                "--evidence-path",
                str(evidence_path),
                "--summary-path",
                str(summary_path),
            ],
            stdout=stdout,
        )

        payload = json.loads(stdout.getvalue())
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        summary = json.loads(summary_path.read_text(encoding="utf-8"))

        assert exit_code == 0
        assert payload["status"] == "succeeded"
        assert payload["evidence_path"] == str(evidence_path)
        assert payload["summary_path"] == str(summary_path)
        assert len(evidence) == 6
        assert summary["target_selection"]["max_targets"] == 2

    run_async(scenario())


def test_collect_external_evidence_cli_nonzero_policy_failure_leaves_files(
    tmp_path: Path,
    monkeypatch,
) -> None:
    async def scenario() -> None:
        holdings_path = FIXTURE_HOLDINGS.resolve()
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("NAVER_CLIENT_ID", raising=False)
        monkeypatch.delenv("NAVER_CLIENT_SECRET", raising=False)
        stdout = StringIO()
        stderr = StringIO()
        evidence_path = tmp_path / "external_evidence.json"
        summary_path = tmp_path / "external_evidence_summary.json"
        approval_path = tmp_path / "approval" / "daily_approval.json"
        approval_argv = [
            "collect-external-evidence",
            "--holdings-source",
            "fixture",
            "--holdings-path",
            str(holdings_path),
            "--providers",
            "fixture_financial,naver",
            "--live",
            "--max-targets",
            "1",
            "--evidence-path",
            str(evidence_path),
            "--summary-path",
            str(summary_path),
            "--approval-path",
            str(approval_path),
        ]
        preapproval_stdout = StringIO()
        preapproval_exit = await run_cli_async(
            approval_argv,
            stdout=preapproval_stdout,
            stderr=StringIO(),
        )
        template_path = tmp_path / "daily_operational_external_data_approval_template.json"
        approval = json.loads(template_path.read_text(encoding="utf-8"))
        approval["status"] = "approved"
        approval_path.parent.mkdir(parents=True, exist_ok=True)
        approval_path.write_text(json.dumps(approval), encoding="utf-8")

        assert preapproval_exit == 1

        exit_code = await run_cli_async(
            approval_argv,
            stdout=stdout,
            stderr=stderr,
        )

        payload = json.loads(stderr.getvalue())
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        summary = json.loads(summary_path.read_text(encoding="utf-8"))

        assert exit_code == 1
        assert stdout.getvalue() == ""
        assert payload["reason"] == "external_evidence_collection_failed"
        assert payload["error_code"] == "credential_required"
        assert payload["summary_path"] == str(summary_path)
        assert [item["source"] for item in evidence] == ["Fixture Financial Metrics"]
        assert summary["policy_failure"]["provider_id"] == "naver"

    run_async(scenario())


def test_collect_external_evidence_live_requires_approval_before_collection(
    tmp_path: Path,
    monkeypatch,
) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        stderr = StringIO()
        evidence_path = tmp_path / "external_evidence.json"
        summary_path = tmp_path / "external_evidence_summary.json"
        approval_path = tmp_path / "approval" / "daily_approval.json"
        collector_called = False

        def fail_if_collector_called(_request):
            nonlocal collector_called
            collector_called = True
            raise AssertionError("external evidence collection should wait for approval")

        monkeypatch.setattr(
            "agent_treport.cli.collect_external_evidence",
            fail_if_collector_called,
        )

        exit_code = await run_cli_async(
            [
                "collect-external-evidence",
                "--holdings-source",
                "fixture",
                "--holdings-path",
                str(FIXTURE_HOLDINGS),
                "--providers",
                "finnhub,naver",
                "--live",
                "--max-targets",
                "1",
                "--evidence-path",
                str(evidence_path),
                "--summary-path",
                str(summary_path),
                "--approval-path",
                str(approval_path),
            ],
            stdout=stdout,
            stderr=stderr,
        )

        payload = json.loads(stdout.getvalue())
        blocked_path = tmp_path / "external_evidence_approval_block.json"
        preflight_path = tmp_path / "daily_operational_external_data_preflight.json"
        preflight = json.loads(preflight_path.read_text(encoding="utf-8"))

        assert stderr.getvalue() == ""
        assert exit_code == 1
        assert collector_called is False
        assert payload == json.loads(blocked_path.read_text(encoding="utf-8"))
        assert payload["approval"]["missing_scopes"] == ["live_external_evidence"]
        assert preflight["approval"]["required_scopes"] == ["live_external_evidence"]
        assert preflight["boundary"]["external_evidence_provider_ids"] == [
            "finnhub",
            "naver",
        ]
        assert preflight["boundary"]["approved_max_target_count"] == 1
        assert str(tmp_path) not in preflight_path.read_text(encoding="utf-8")

    run_async(scenario())


def test_collect_external_evidence_cli_accepts_target_candidates_path(tmp_path: Path) -> None:
    async def scenario() -> None:
        target_candidates_path = tmp_path / "target_candidates.json"
        target_candidates_path.write_text(
            json.dumps(
                {
                    "signal_board": [
                        {
                            "rank": 1,
                            "ticker": "AAPL",
                            "name": "Apple Inc.",
                            "aggregation_key": "sec_aapl",
                            "member_security_ids": ["sec_aapl"],
                            "listing_keys": ["AAPL:XNAS"],
                            "claim_scope": "signal:security:sec_aapl:weight_increase",
                            "signal_type": "weight_increase",
                            "signal_direction": "increase",
                            "primary_reason": "Existing report target.",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        stdout = StringIO()
        evidence_path = tmp_path / "external_evidence.json"
        summary_path = tmp_path / "external_evidence_summary.json"

        exit_code = await run_cli_async(
            [
                "collect-external-evidence",
                "--holdings-source",
                "targets",
                "--target-candidates-path",
                str(target_candidates_path),
                "--providers",
                "fixture_news",
                "--evidence-path",
                str(evidence_path),
                "--summary-path",
                str(summary_path),
            ],
            stdout=stdout,
        )

        summary = json.loads(summary_path.read_text(encoding="utf-8"))

        assert exit_code == 0
        assert json.loads(stdout.getvalue())["status"] == "succeeded"
        assert summary["target_selection"]["selected_targets"][0]["ticker"] == "AAPL"

    run_async(scenario())


def test_collect_external_evidence_cli_can_ignore_cooldown(tmp_path: Path) -> None:
    async def scenario() -> None:
        cooldown_path = tmp_path / "cooldowns.json"
        cooldown_path.write_text(
            json.dumps(
                {
                    "schema_version": "agent_treport.external_evidence.cooldown.v1",
                    "providers": {
                        "fixture_news": {
                            "cooldown_until": "2999-01-01T00:00:00+00:00",
                            "reason": "blocked",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        stdout = StringIO()
        evidence_path = tmp_path / "external_evidence.json"
        summary_path = tmp_path / "external_evidence_summary.json"

        exit_code = await run_cli_async(
            [
                "collect-external-evidence",
                "--holdings-source",
                "fixture",
                "--holdings-path",
                str(FIXTURE_HOLDINGS),
                "--providers",
                "fixture_news",
                "--evidence-path",
                str(evidence_path),
                "--summary-path",
                str(summary_path),
                "--cooldown-path",
                str(cooldown_path),
                "--ignore-cooldown",
            ],
            stdout=stdout,
        )

        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        summary = json.loads(summary_path.read_text(encoding="utf-8"))

        assert exit_code == 0
        assert json.loads(stdout.getvalue())["status"] == "succeeded"
        assert summary["provider_outcomes"][0]["status"] == "success"
        assert [item["source"] for item in evidence] == ["Fixture News", "Fixture News"]

    run_async(scenario())


def test_run_report_cli_projects_external_evidence_summary(tmp_path: Path) -> None:
    async def scenario() -> None:
        stdout = StringIO()
        sqlite_path = tmp_path / "state" / "treport.sqlite3"
        artifact_root = tmp_path / "artifacts"
        evidence_path = tmp_path / "external_evidence.json"
        summary_path = tmp_path / "external_evidence_summary.json"
        evidence_path.write_text("[]\n", encoding="utf-8")
        summary_path.write_text(
            json.dumps(
                {
                    "schema_version": "agent_treport.external_evidence.summary.v1",
                    "category_coverage": {
                        "financial": {
                            "coverage_ratio": 1.0,
                            "notes": ["financial:NVDA=covered"],
                        },
                        "disclosure": {
                            "coverage_ratio": 0.0,
                            "notes": ["disclosure:NVDA=no_data"],
                        },
                        "news": {
                            "coverage_ratio": 0.5,
                            "notes": ["news:PLTR=failed"],
                        },
                    },
                }
            ),
            encoding="utf-8",
        )

        exit_code = await run_cli_async(
            [
                "run-report",
                "--run-id",
                "run_external_summary_projection",
                "--sqlite-path",
                str(sqlite_path),
                "--artifact-root",
                str(artifact_root),
                "--model",
                "codex",
                "--evidence-path",
                str(evidence_path),
                "--evidence-summary-path",
                str(summary_path),
            ],
            stdout=stdout,
            model_client_factory=lambda _config: FakeModelClient(
                [
                    ModelResponse(
                        message=Message(
                            role="assistant",
                            content=(TextBlock(text="summary projection"),),
                        )
                    )
                ]
            ),
        )

        signal_payload = json.loads(
            (artifact_root / "artifact_treport_signal_payload.json").read_text(
                encoding="utf-8"
            )
        )

        assert exit_code == 0
        assert json.loads(stdout.getvalue())["status"] == "succeeded"
        assert signal_payload["coverage"]["financial_coverage_ratio"] == 1.0
        assert signal_payload["coverage"]["disclosure_coverage_ratio"] == 0.0
        assert "financial:NVDA=covered" in signal_payload["data_quality"]["coverage_notes"]

    run_async(scenario())
