from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from typing import Any

import pytest

from agent_treport.cli import run_cli_async


def run_async(awaitable):
    return asyncio.run(awaitable)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _message_fingerprint(message_text: str) -> str:
    return hashlib.sha256(message_text.encode("utf-8")).hexdigest()


def _idempotency_key(
    *,
    run_id: str,
    telegram_alert_artifact_id: str,
    message_fingerprint: str,
    target_alias: str,
) -> str:
    identity = {
        "message_fingerprint": message_fingerprint,
        "run_id": run_id,
        "target_alias": target_alias,
        "telegram_alert_artifact_id": telegram_alert_artifact_id,
    }
    canonical = json.dumps(
        identity,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return "telegram_delivery_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _eligible_handoff(
    *,
    package_path: Path,
    run_id: str = "run_daily_closure",
    message_text: str = "<b>Daily publish</b>\n<code>artifact_treport_html_report</code>",
    telegram_alert_artifact_id: str = "artifact_treport_telegram_alert",
    **overrides: object,
) -> dict[str, Any]:
    handoff: dict[str, Any] = {
        "schema_version": "agent_treport.native_operational_handoff.v1",
        "run_id": run_id,
        "status": "user_ready",
        "delivery_blocked": False,
        "references": {
            "artifacts": {
                "telegram_alert": {
                    "artifact_id": telegram_alert_artifact_id,
                    "media_type": "text/plain",
                    "name": "telegram_alert.txt",
                    "path": "preview/artifacts/telegram_alert.txt",
                }
            }
        },
        "preview": {
            "result_package_path": str(package_path),
            "telegram_delivery": "not_sent",
            "telegram_message": {
                "artifact_id": telegram_alert_artifact_id,
                "delivery_status": "not_sent",
                "parse_mode": "HTML",
                "send_method": "sendMessage",
                "text": message_text,
            },
            "type": "pre_publish",
        },
        "closure": {
            "full_live_pre_publish_artifact_closure": {
                "missing_artifacts": [],
                "status": "met",
                "telegram_alert_artifact_id": telegram_alert_artifact_id,
                "telegram_message_body_included": True,
            },
            "full_user_ready_closure": {
                "blocking_reasons": [],
                "delivery_blocked": False,
                "status": "met",
            },
        },
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(handoff.get(key), dict):
            handoff[key].update(value)
        else:
            handoff[key] = value
    return handoff


def _write_handoff(package_path: Path, payload: dict[str, Any]) -> Path:
    path = package_path / "pre_publish_handoff.json"
    _write_json(path, payload)
    return path


def _write_validation(
    package_path: Path,
    *,
    status: str = "passed",
    command: str = (
        "../.venv/Scripts/python.exe -m pytest "
        "tests/test_agent_treport_pre_publish_preview.py"
    ),
    result: str = "passed",
    summary: str = "1 passed",
) -> None:
    _write_json(
        package_path / "validation_command_results.json",
        {
            "schema_version": "agent_treport.pre_publish.validation_command_results.v1",
            "status": status,
            "commands": [
                {
                    "command": command,
                    "result": result,
                    "summary": summary,
                }
            ],
        },
    )


def _receipt_identity(
    handoff: dict[str, Any],
    *,
    target_alias: str = "default",
) -> dict[str, str]:
    run_id = str(handoff["run_id"])
    message = handoff["preview"]["telegram_message"]
    artifact_id = str(message["artifact_id"])
    fingerprint = _message_fingerprint(str(message["text"]))
    return {
        "idempotency_key": _idempotency_key(
            run_id=run_id,
            telegram_alert_artifact_id=artifact_id,
            message_fingerprint=fingerprint,
            target_alias=target_alias,
        ),
        "message_fingerprint": fingerprint,
        "run_id": run_id,
        "target_alias": target_alias,
        "telegram_alert_artifact_id": artifact_id,
    }


def _write_receipt(
    package_path: Path,
    handoff: dict[str, Any],
    *,
    delivery_status: str,
    attempt_count: int = 1,
    target_alias: str = "default",
    identity_overrides: dict[str, str] | None = None,
) -> Path:
    identity = _receipt_identity(handoff, target_alias=target_alias)
    identity.update(identity_overrides or {})
    receipt_path = (
        package_path
        / "telegram_delivery_receipts"
        / (
            f"telegram_delivery_receipt_{identity['idempotency_key'][-16:]}"
            f"_attempt_{attempt_count:03d}.json"
        )
    )
    receipt: dict[str, Any] = {
        "schema_version": "agent_treport.telegram_delivery_receipt.v1",
        "delivery_status": delivery_status,
        "idempotency_key": identity["idempotency_key"],
        "run_id": identity["run_id"],
        "telegram_alert_artifact_id": identity["telegram_alert_artifact_id"],
        "message_fingerprint": identity["message_fingerprint"],
        "message_length": len(str(handoff["preview"]["telegram_message"]["text"])),
        "parse_mode": "HTML",
        "target_alias": identity["target_alias"],
        "approved_scope": "telegram_delivery",
        "approval": {
            "required_scopes": ["telegram_delivery"],
            "status": "approved",
            "valid": True,
        },
        "attempt_count": attempt_count,
        "attempted_at": "2026-05-19T00:00:00+00:00",
        "adapter_name": "telegram_bot_api" if delivery_status == "sent" else "none",
        "receipt_path": str(receipt_path.relative_to(package_path)),
        "package_path": str(package_path),
        "handoff_path": "pre_publish_handoff.json",
    }
    if delivery_status == "failed":
        receipt["adapter_name"] = "telegram_bot_api"
        receipt["safe_error"] = {
            "code": "telegram_timeout",
            "details": {},
            "message": "Telegram delivery timed out.",
            "retryable": True,
        }
    if delivery_status == "duplicate_blocked":
        receipt["safe_error"] = {
            "code": "duplicate_sent_receipt_exists",
            "details": {},
            "message": "A sent Telegram delivery receipt already exists.",
            "retryable": False,
        }
    _write_json(receipt_path, receipt)
    return receipt_path


def _write_delivery_summary(
    package_path: Path,
    handoff: dict[str, Any],
    *,
    latest_delivery_status: str = "duplicate_blocked",
    live: bool = True,
    target_alias: str = "default",
    identity_overrides: dict[str, str] | None = None,
) -> None:
    identity = _receipt_identity(handoff, target_alias=target_alias)
    identity.update(identity_overrides or {})
    receipt_paths = [
        str(path.relative_to(package_path))
        for path in sorted((package_path / "telegram_delivery_receipts").glob("*.json"))
    ]
    _write_json(
        package_path / "telegram_delivery_summary.json",
        {
            "schema_version": "agent_treport.telegram_delivery_summary.v1",
            "latest_delivery_status": latest_delivery_status,
            "reason": None,
            "idempotency_key": identity["idempotency_key"],
            "run_id": identity["run_id"],
            "telegram_alert_artifact_id": identity["telegram_alert_artifact_id"],
            "message_fingerprint": identity["message_fingerprint"],
            "message_length": len(str(handoff["preview"]["telegram_message"]["text"])),
            "parse_mode": "HTML",
            "target_alias": identity["target_alias"],
            "approved_scope": "telegram_delivery",
            "approval": {"status": "approved", "valid": True},
            "live": live,
            "receipt_path": receipt_paths[-1] if receipt_paths else None,
            "receipt_paths": receipt_paths,
            "delivery_summary_path": "telegram_delivery_summary.json",
            "package_path": str(package_path),
            "handoff_path": "pre_publish_handoff.json",
        },
    )


def _write_operator_flow_approval(*, status: str = "approved") -> Path:
    approval_path = Path("data/agent_treport/approvals/operator_approved_daily_publish_flow.json")
    _write_json(
        approval_path,
        {
            "schema_version": "agent_treport.operator_approved_daily_publish_flow.v1",
            "status": status,
            "approved_at": "2026-05-19T00:00:00+00:00",
            "approved_by": "operator",
            "approved_flow": "manual_daily_publish_flow",
            "approved_scopes": [
                "external_evidence_collection",
                "model_export",
                "actual_telegram_delivery",
                "duplicate_send_check",
                "closure_verification",
            ],
            "approved_provider_set": [
                "finnhub",
                "yfinance",
                "dart",
                "alpha_vantage",
                "newsapi",
                "naver",
            ],
            "approved_model_boundary": "manual pre-publish model export only",
            "approved_delivery_target_aliases": ["default"],
            "requires_manual_operator_execution": True,
            "out_of_scope": ["scheduler", "autonomous_delivery"],
            "safety_exclusions": ["raw_payload_storage", "credential_storage"],
            "source_decision_summary": "Approves the documented manual daily publish flow.",
        },
    )
    return approval_path


async def _run_closure(package_path: Path) -> tuple[int, dict[str, Any], str]:
    stdout = StringIO()
    stderr = StringIO()
    exit_code = await run_cli_async(
        ["verify-daily-publish-closure", "--package-path", str(package_path)],
        stdout=stdout,
        stderr=stderr,
        collection_now=lambda: datetime(2026, 5, 19, tzinfo=UTC),
    )
    payload = json.loads(stdout.getvalue()) if stdout.getvalue() else {}
    assert isinstance(payload, dict)
    return exit_code, payload, stderr.getvalue()


@pytest.fixture(autouse=True)
def _isolate_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    monkeypatch.chdir(tmp_path)
    yield


def _prepared_package(
    package_path: Path,
    *,
    run_id: str = "run_daily_closure",
) -> dict[str, Any]:
    handoff = _eligible_handoff(package_path=package_path, run_id=run_id)
    _write_handoff(package_path, handoff)
    _write_validation(package_path)
    return handoff


def test_verify_daily_publish_closure_writes_closure_met_for_live_sent_and_duplicate_receipts(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        package_path = Path("packages/run_closure_met")
        handoff = _prepared_package(package_path, run_id="run_closure_met")
        sent_path = _write_receipt(
            package_path,
            handoff,
            delivery_status="sent",
            attempt_count=1,
        )
        duplicate_path = _write_receipt(
            package_path,
            handoff,
            delivery_status="duplicate_blocked",
            attempt_count=2,
        )
        _write_delivery_summary(package_path, handoff, latest_delivery_status="duplicate_blocked")
        approval_path = _write_operator_flow_approval()
        handoff_before = (package_path / "pre_publish_handoff.json").read_bytes()
        summary_before = (package_path / "telegram_delivery_summary.json").read_bytes()

        exit_code, payload, stderr = await _run_closure(package_path)

        closure = _read_json(package_path / "daily_publish_closure.json")
        assert stderr == ""
        assert exit_code == 0
        assert payload["closure_status"] == "closure_met"
        assert closure["schema_version"] == "agent_treport.daily_publish_closure.v1"
        assert closure["closure_status"] == "closure_met"
        assert closure["closure_met"] is True
        assert closure["run_id"] == "run_closure_met"
        assert closure["target_alias"] == "default"
        assert closure["telegram_alert_artifact_id"] == "artifact_treport_telegram_alert"
        assert closure["message_fingerprint"] == _receipt_identity(handoff)["message_fingerprint"]
        assert closure["evidence_checks"] == {
            "duplicate_blocked": "passed",
            "identity_consistency": "passed",
            "live_sent_receipt": "passed",
            "operator_approved_daily_publish_flow": "passed",
            "pre_publish_user_ready": "passed",
            "validation_passed": "passed",
        }
        assert closure["receipt_summary"]["matching_sent_receipt_count"] == 1
        assert closure["receipt_summary"]["matching_duplicate_blocked_receipt_count"] == 1
        assert closure["receipt_summary"]["selected_sent_receipt_path"] == str(
            sent_path.relative_to(package_path)
        )
        assert closure["receipt_summary"]["selected_duplicate_blocked_receipt_path"] == str(
            duplicate_path.relative_to(package_path)
        )
        assert str(approval_path) in closure["source_files"]
        assert (package_path / "pre_publish_handoff.json").read_bytes() == handoff_before
        assert (package_path / "telegram_delivery_summary.json").read_bytes() == summary_before
        assert str(tmp_path) not in json.dumps(closure, ensure_ascii=False)

    run_async(scenario())


def test_verify_daily_publish_closure_writes_missing_handoff_without_error(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        package_path = Path("packages/missing_handoff")
        package_path.mkdir(parents=True)

        exit_code, payload, stderr = await _run_closure(package_path)

        closure = _read_json(package_path / "daily_publish_closure.json")
        assert stderr == ""
        assert exit_code == 0
        assert payload["closure_status"] == "missing_handoff"
        assert closure["closure_status"] == "missing_handoff"
        assert closure["closure_met"] is False
        assert closure["evidence_checks"]["pre_publish_user_ready"] == "not_available"
        assert closure["source_files"] == []

    run_async(scenario())


def test_verify_daily_publish_closure_keeps_ineligible_handoff_from_closure_met(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        package_path = Path("packages/pre_publish_not_ready")
        handoff = _eligible_handoff(
            package_path=package_path,
            run_id="run_pre_publish_not_ready",
            status="operator_review_only",
            delivery_blocked=True,
        )
        _write_handoff(package_path, handoff)
        _write_validation(package_path)
        _write_receipt(package_path, handoff, delivery_status="sent", attempt_count=1)
        _write_receipt(
            package_path,
            handoff,
            delivery_status="duplicate_blocked",
            attempt_count=2,
        )
        _write_delivery_summary(package_path, handoff)

        exit_code, payload, stderr = await _run_closure(package_path)

        closure = _read_json(package_path / "daily_publish_closure.json")
        assert stderr == ""
        assert exit_code == 0
        assert payload["closure_status"] == "pre_publish_not_user_ready"
        assert closure["evidence_checks"]["pre_publish_user_ready"] == "failed"
        assert "handoff_has_delivery_evidence_but_is_not_user_ready" in closure["warnings"]
        assert "status=operator_review_only" in closure["limitations"]
        assert "delivery_blocked=true" in closure["limitations"]

    run_async(scenario())


@pytest.mark.parametrize(
    ("write_validation", "status"),
    [
        (None, "missing"),
        (
            lambda package_path: _write_validation(
                package_path,
                status="failed",
                result="failed",
            ),
            "failed",
        ),
    ],
)
def test_verify_daily_publish_closure_requires_passed_validation(
    tmp_path: Path,
    write_validation: Callable[[Path], None] | None,
    status: str,
) -> None:
    async def scenario() -> None:
        package_path = Path(f"packages/validation_{status}")
        handoff = _eligible_handoff(package_path=package_path, run_id=f"run_validation_{status}")
        _write_handoff(package_path, handoff)
        if write_validation is not None:
            write_validation(package_path)
        _write_receipt(package_path, handoff, delivery_status="sent", attempt_count=1)
        _write_receipt(
            package_path,
            handoff,
            delivery_status="duplicate_blocked",
            attempt_count=2,
        )
        _write_delivery_summary(package_path, handoff)

        exit_code, payload, stderr = await _run_closure(package_path)

        closure = _read_json(package_path / "daily_publish_closure.json")
        assert stderr == ""
        assert exit_code == 0
        assert payload["closure_status"] == "validation_not_passed"
        assert closure["validation_summary"]["validation_status"] == status
        assert closure["evidence_checks"]["validation_passed"] == "failed"

    run_async(scenario())


@pytest.mark.parametrize(
    ("receipt_status", "expected_status"),
    [
        ("duplicate_blocked", "duplicate_only_without_sent"),
        ("failed", "failed_delivery"),
        ("sent", "sent_without_duplicate_proof"),
    ],
)
def test_verify_daily_publish_closure_classifies_matching_receipt_gaps(
    tmp_path: Path,
    receipt_status: str,
    expected_status: str,
) -> None:
    async def scenario() -> None:
        package_path = Path(f"packages/{expected_status}")
        handoff = _prepared_package(package_path, run_id=f"run_{expected_status}")
        _write_receipt(package_path, handoff, delivery_status=receipt_status)
        _write_delivery_summary(
            package_path,
            handoff,
            latest_delivery_status=receipt_status,
            live=True,
        )

        exit_code, payload, stderr = await _run_closure(package_path)

        assert stderr == ""
        assert exit_code == 0
        assert payload["closure_status"] == expected_status

    run_async(scenario())


def test_verify_daily_publish_closure_classifies_not_sent_when_no_matching_receipt_exists(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        package_path = Path("packages/not_sent")
        _prepared_package(package_path, run_id="run_not_sent")

        exit_code, payload, stderr = await _run_closure(package_path)

        closure = _read_json(package_path / "daily_publish_closure.json")
        assert stderr == ""
        assert exit_code == 0
        assert payload["closure_status"] == "not_sent"
        assert closure["receipt_summary"]["matching_sent_receipt_count"] == 0
        assert closure["receipt_summary"]["matching_duplicate_blocked_receipt_count"] == 0

    run_async(scenario())


def test_verify_daily_publish_closure_classifies_stale_receipt_when_all_receipts_mismatch(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        package_path = Path("packages/stale_receipts")
        handoff = _prepared_package(package_path, run_id="run_current")
        _write_receipt(
            package_path,
            handoff,
            delivery_status="sent",
            identity_overrides={"run_id": "run_previous"},
        )

        exit_code, payload, stderr = await _run_closure(package_path)

        closure = _read_json(package_path / "daily_publish_closure.json")
        assert stderr == ""
        assert exit_code == 0
        assert payload["closure_status"] == "stale_receipt"
        assert closure["receipt_summary"]["mismatched_stale_receipt_count"] == 1
        assert "stale_or_mismatched_receipts_present" in closure["warnings"]

    run_async(scenario())


def test_verify_daily_publish_closure_lets_matching_receipts_win_over_mismatched_extras(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        package_path = Path("packages/matching_wins")
        handoff = _prepared_package(package_path, run_id="run_matching_wins")
        _write_receipt(package_path, handoff, delivery_status="sent", attempt_count=1)
        _write_receipt(
            package_path,
            handoff,
            delivery_status="duplicate_blocked",
            attempt_count=2,
        )
        _write_receipt(
            package_path,
            handoff,
            delivery_status="sent",
            attempt_count=3,
            identity_overrides={"run_id": "run_previous"},
        )
        _write_delivery_summary(package_path, handoff)

        exit_code, payload, stderr = await _run_closure(package_path)

        closure = _read_json(package_path / "daily_publish_closure.json")
        assert stderr == ""
        assert exit_code == 0
        assert payload["closure_status"] == "closure_met"
        assert closure["receipt_summary"]["mismatched_stale_receipt_count"] == 1
        assert "stale_or_mismatched_receipts_present" in closure["warnings"]

    run_async(scenario())


def test_verify_daily_publish_closure_requires_sent_and_duplicate_same_target_alias(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        package_path = Path("packages/target_alias_mismatch")
        handoff = _prepared_package(package_path, run_id="run_target_alias_mismatch")
        _write_receipt(
            package_path,
            handoff,
            delivery_status="sent",
            attempt_count=1,
            target_alias="default",
        )
        _write_receipt(
            package_path,
            handoff,
            delivery_status="duplicate_blocked",
            attempt_count=2,
            target_alias="ops",
        )
        _write_delivery_summary(package_path, handoff, target_alias="default")

        exit_code, payload, stderr = await _run_closure(package_path)

        closure = _read_json(package_path / "daily_publish_closure.json")
        assert stderr == ""
        assert exit_code == 0
        assert payload["closure_status"] == "sent_without_duplicate_proof"
        assert closure["target_alias"] == "default"
        assert closure["evidence_checks"]["identity_consistency"] == "failed"
        assert closure["evidence_checks"]["duplicate_blocked"] == "failed"
        assert closure["receipt_summary"]["mismatched_stale_receipt_count"] == 1

    run_async(scenario())


def test_verify_daily_publish_closure_counts_prior_failed_attempts_as_recovered(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        package_path = Path("packages/recovered_failure")
        handoff = _prepared_package(package_path, run_id="run_recovered_failure")
        _write_receipt(package_path, handoff, delivery_status="failed", attempt_count=1)
        _write_receipt(package_path, handoff, delivery_status="sent", attempt_count=2)
        _write_receipt(
            package_path,
            handoff,
            delivery_status="duplicate_blocked",
            attempt_count=3,
        )
        _write_delivery_summary(package_path, handoff)

        exit_code, payload, stderr = await _run_closure(package_path)

        closure = _read_json(package_path / "daily_publish_closure.json")
        assert stderr == ""
        assert exit_code == 0
        assert payload["closure_status"] == "closure_met"
        assert closure["receipt_summary"]["matching_failed_receipt_count"] == 1
        assert closure["receipt_summary"]["recovered_failed_attempt_count"] == 1

    run_async(scenario())


def test_verify_daily_publish_closure_uses_receipts_when_summary_latest_status_conflicts(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        package_path = Path("packages/summary_conflict")
        handoff = _prepared_package(package_path, run_id="run_summary_conflict")
        _write_receipt(package_path, handoff, delivery_status="sent", attempt_count=1)
        _write_receipt(
            package_path,
            handoff,
            delivery_status="duplicate_blocked",
            attempt_count=2,
        )
        _write_delivery_summary(package_path, handoff, latest_delivery_status="failed")

        exit_code, payload, stderr = await _run_closure(package_path)

        closure = _read_json(package_path / "daily_publish_closure.json")
        assert stderr == ""
        assert exit_code == 0
        assert payload["closure_status"] == "closure_met"
        assert closure["receipt_summary"]["summary_conflict_warning"] == (
            "delivery_summary_conflicts_with_matching_receipts"
        )
        assert "delivery_summary_conflicts_with_matching_receipts" in closure["warnings"]

    run_async(scenario())


def test_verify_daily_publish_closure_requires_live_delivery_evidence(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        package_path = Path("packages/fake_sent")
        handoff = _prepared_package(package_path, run_id="run_fake_sent")
        _write_receipt(package_path, handoff, delivery_status="sent", attempt_count=1)
        _write_receipt(
            package_path,
            handoff,
            delivery_status="duplicate_blocked",
            attempt_count=2,
        )
        _write_delivery_summary(package_path, handoff, live=False)

        exit_code, payload, stderr = await _run_closure(package_path)

        closure = _read_json(package_path / "daily_publish_closure.json")
        assert stderr == ""
        assert exit_code == 0
        assert payload["closure_status"] == "not_sent"
        assert closure["evidence_checks"]["live_sent_receipt"] == "failed"
        assert "live_delivery_evidence_missing" in closure["limitations"]

    run_async(scenario())


def test_verify_daily_publish_closure_redacts_unsafe_validation_commands_and_fails_validation(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        package_path = Path("packages/unsafe_validation")
        handoff = _eligible_handoff(
            package_path=package_path,
            run_id="run_unsafe_validation",
            message_text="<b>Unsafe body must not appear</b>",
        )
        _write_handoff(package_path, handoff)
        _write_validation(
            package_path,
            command=(
                f"{tmp_path}\\secret\\.venv\\Scripts\\python.exe -m pytest "
                "file://leak https://api.telegram.org/botTOKEN/sendMessage "
                "raw_chat_id=123456 raw_request={} Traceback"
            ),
            summary="<b>Unsafe body must not appear</b>",
        )
        _write_receipt(package_path, handoff, delivery_status="sent", attempt_count=1)
        _write_receipt(
            package_path,
            handoff,
            delivery_status="duplicate_blocked",
            attempt_count=2,
        )
        _write_delivery_summary(package_path, handoff)

        exit_code, payload, stderr = await _run_closure(package_path)

        closure = _read_json(package_path / "daily_publish_closure.json")
        serialized = json.dumps(closure, ensure_ascii=False)
        assert stderr == ""
        assert exit_code == 0
        assert payload["closure_status"] == "validation_not_passed"
        assert closure["validation_summary"]["commands_omitted"] is True
        assert "unsafe_validation_command_results_redacted" in closure["limitations"]
        assert str(tmp_path) not in serialized
        assert "file://" not in serialized
        assert "api.telegram.org" not in serialized
        assert "Unsafe body must not appear" not in serialized
        assert "raw_chat_id" not in serialized
        assert "raw_request" not in serialized
        assert "Traceback" not in serialized

    run_async(scenario())


@pytest.mark.parametrize(
    ("approval_status", "expected_check"),
    [
        (None, "not_available"),
        ("revoked", "failed"),
    ],
)
def test_verify_daily_publish_closure_treats_operator_flow_approval_as_warning_only(
    tmp_path: Path,
    approval_status: str | None,
    expected_check: str,
) -> None:
    async def scenario() -> None:
        package_path = Path(f"packages/approval_{approval_status or 'missing'}")
        handoff = _prepared_package(
            package_path,
            run_id=f"run_approval_{approval_status or 'missing'}",
        )
        _write_receipt(package_path, handoff, delivery_status="sent", attempt_count=1)
        _write_receipt(
            package_path,
            handoff,
            delivery_status="duplicate_blocked",
            attempt_count=2,
        )
        _write_delivery_summary(package_path, handoff)
        if approval_status is not None:
            _write_operator_flow_approval(status=approval_status)

        exit_code, payload, stderr = await _run_closure(package_path)

        closure = _read_json(package_path / "daily_publish_closure.json")
        assert stderr == ""
        assert exit_code == 0
        assert payload["closure_status"] == "closure_met"
        assert closure["evidence_checks"]["operator_approved_daily_publish_flow"] == expected_check
        assert "operator_approved_daily_publish_flow_not_approved" in closure["warnings"]

    run_async(scenario())
