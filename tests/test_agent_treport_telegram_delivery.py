from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Iterator
from io import StringIO
from pathlib import Path
from typing import Any

import pytest

from agent_treport.cli import run_cli_async
from agent_treport.signal_report.telegram_delivery import (
    RealTelegramBotApiClient,
    TelegramCredentials,
    TelegramDeliveryProviderError,
)


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


def _eligible_handoff(
    *,
    run_id: str = "run_delivery_ready",
    message_text: str = "<b>ETF signal</b>\n<code>artifact_treport_html_report</code>",
    target_package: Path | None = None,
    **overrides: object,
) -> dict[str, Any]:
    package_path = target_package or (
        Path("data/agent_treport/live-source/daily-smoke-summaries") / run_id
    )
    telegram_artifact_id = "artifact_treport_telegram_alert"
    handoff: dict[str, Any] = {
        "schema_version": "agent_treport.native_operational_handoff.v1",
        "run_id": run_id,
        "status": "user_ready",
        "delivery_blocked": False,
        "references": {
            "artifacts": {
                "telegram_alert": {
                    "artifact_id": telegram_artifact_id,
                    "name": "telegram_alert.txt",
                    "media_type": "text/plain",
                    "path": "preview/artifacts/telegram_alert.txt",
                }
            }
        },
        "preview": {
            "type": "pre_publish",
            "telegram_delivery": "not_sent",
            "result_package_path": str(package_path),
            "telegram_message": {
                "artifact_id": telegram_artifact_id,
                "parse_mode": "HTML",
                "send_method": "sendMessage",
                "delivery_status": "not_sent",
                "text": message_text,
            },
        },
        "closure": {
            "full_live_pre_publish_artifact_closure": {
                "status": "met",
                "telegram_message_body_included": True,
                "telegram_alert_artifact_id": telegram_artifact_id,
                "missing_artifacts": [],
            },
            "full_user_ready_closure": {
                "status": "met",
                "delivery_blocked": False,
                "blocking_reasons": [],
            },
        },
    }
    handoff.update(overrides)
    return handoff


def _write_handoff(tmp_path: Path, payload: dict[str, Any]) -> Path:
    artifact_path = tmp_path / "preview" / "artifacts" / "telegram_alert.txt"
    message = payload.get("preview", {}).get("telegram_message", {})
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(str(message.get("text", "")), encoding="utf-8")
    path = tmp_path / "preview" / "pre_publish_handoff.json"
    _write_json(path, payload)
    return path


def _delivery_args(handoff_path: Path, *extra: str) -> list[str]:
    return ["deliver-telegram-alert", "--handoff-path", str(handoff_path), *extra]


async def _run_delivery(
    handoff_path: Path,
    *extra: str,
    telegram_client_factory: Any | None = None,
) -> tuple[int, dict[str, Any], str]:
    stdout = StringIO()
    stderr = StringIO()
    if telegram_client_factory is None:
        exit_code = await run_cli_async(
            _delivery_args(handoff_path, *extra),
            stdout=stdout,
            stderr=stderr,
        )
    else:
        exit_code = await run_cli_async(
            _delivery_args(handoff_path, *extra),
            stdout=stdout,
            stderr=stderr,
            telegram_client_factory=telegram_client_factory,
        )
    payload = json.loads(stdout.getvalue()) if stdout.getvalue() else {}
    assert isinstance(payload, dict)
    return exit_code, payload, stderr.getvalue()


def _approve_template(path: Path) -> None:
    template = _read_json(path)
    template["status"] = "approved"
    _write_json(path, template)


def _approval_template_path(payload: dict[str, Any]) -> Path:
    raw = payload["approval_template_path"]
    assert isinstance(raw, str)
    return Path(raw)


def _receipt_path(payload: dict[str, Any]) -> Path:
    raw = payload["receipt_path"]
    assert isinstance(raw, str)
    return Path(raw)


def _summary_path(payload: dict[str, Any]) -> Path:
    raw = payload["delivery_summary_path"]
    assert isinstance(raw, str)
    return Path(raw)


class _RecordingTelegramClient:
    adapter_name = "recording_fake_telegram"

    def __init__(
        self,
        calls: list[dict[str, str]],
        *,
        outcome: object | None = None,
    ) -> None:
        self._calls = calls
        self._outcome = outcome or {"provider_message_id": "provider-message-42"}

    def send_message(self, *, text: str, parse_mode: str) -> object:
        self._calls.append({"text": text, "parse_mode": parse_mode})
        if isinstance(self._outcome, BaseException):
            raise self._outcome
        return self._outcome


@pytest.fixture(autouse=True)
def _isolate_delivery_package_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    monkeypatch.chdir(tmp_path)
    yield


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ({"status": "operator_review_only"}, "status_not_user_ready"),
        ({"status": "failed"}, "status_not_user_ready"),
        ({"delivery_blocked": True}, "delivery_blocked"),
        (
            {
                "closure": {
                    "full_user_ready_closure": {
                        "status": "blocked",
                        "delivery_blocked": True,
                        "blocking_reasons": ["readiness_hold"],
                    }
                }
            },
            "full_user_ready_closure_not_met",
        ),
        (
            {"preview": {"telegram_delivery": "sent"}},
            "telegram_delivery_not_sendable",
        ),
        (
            {"preview": {"telegram_delivery": "not_sent"}},
            "telegram_message_missing",
        ),
        ({"references": {"artifacts": {}}}, "telegram_artifact_missing"),
    ],
)
def test_telegram_delivery_rejects_ineligible_handoff_before_approval_or_network(
    tmp_path: Path,
    mutation: dict[str, object],
    reason: str,
) -> None:
    async def scenario() -> None:
        handoff = _eligible_handoff()
        if mutation == {"preview": {"telegram_delivery": "not_sent"}}:
            handoff["preview"].pop("telegram_message")
        else:
            for key, value in mutation.items():
                if isinstance(value, dict) and isinstance(handoff.get(key), dict):
                    handoff[key].update(value)
                else:
                    handoff[key] = value
        handoff_path = _write_handoff(tmp_path, handoff)
        calls: list[dict[str, str]] = []

        exit_code, payload, stderr = await _run_delivery(
            handoff_path,
            telegram_client_factory=lambda: _RecordingTelegramClient(calls),
        )

        assert stderr == ""
        assert exit_code == 1
        assert payload["telegram_delivery"] == "failed"
        assert payload["reason"] == reason
        assert calls == []
        assert "approval_preflight_path" not in payload

    run_async(scenario())


def test_telegram_delivery_writes_path_safe_preflight_and_pending_template_before_approval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "secret-token-value")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "987654321")
        handoff = _eligible_handoff(run_id="run_preflight")
        handoff_path = _write_handoff(tmp_path, handoff)
        calls: list[dict[str, str]] = []

        exit_code, payload, stderr = await _run_delivery(
            handoff_path,
            telegram_client_factory=lambda: _RecordingTelegramClient(calls),
        )

        preflight_path = Path(payload["approval_preflight_path"])
        template_path = _approval_template_path(payload)
        preflight = _read_json(preflight_path)
        template = _read_json(template_path)
        disclosure = preflight["disclosure"]
        message_text = handoff["preview"]["telegram_message"]["text"]
        message_fingerprint = hashlib.sha256(message_text.encode("utf-8")).hexdigest()
        serialized = "\n".join(
            [
                json.dumps(payload, ensure_ascii=False),
                preflight_path.read_text(encoding="utf-8"),
                template_path.read_text(encoding="utf-8"),
            ]
        )

        assert stderr == ""
        assert exit_code == 1
        assert payload["telegram_delivery"] == "failed"
        assert payload["reason"] == "telegram_delivery_approval_required"
        assert calls == []
        assert preflight["schema_version"] == "agent_treport.telegram_delivery_preflight.v1"
        assert preflight["approval"]["required_scopes"] == ["telegram_delivery"]
        assert disclosure["handoff_identity"] == str(
            Path("preview") / "pre_publish_handoff.json"
        )
        assert disclosure["run_id"] == "run_preflight"
        assert disclosure["telegram_alert_artifact_id"] == (
            "artifact_treport_telegram_alert"
        )
        assert disclosure["message_fingerprint"] == message_fingerprint
        assert disclosure["message_length"] == len(message_text)
        assert disclosure["parse_mode"] == "HTML"
        assert disclosure["target_alias"] == "default"
        assert disclosure["credential_expectations"] == [
            "TELEGRAM_BOT_TOKEN",
            "TELEGRAM_CHAT_ID",
        ]
        assert "raw_chat_id" in disclosure["excluded_raw_fields"]
        assert template["status"] == "pending"
        assert template["target_alias"] == "default"
        assert str(tmp_path) not in serialized
        assert "secret-token-value" not in serialized
        assert "987654321" not in serialized
        assert message_text not in serialized

    run_async(scenario())


def test_telegram_delivery_approved_fake_send_writes_receipt_and_summary_without_mutating_handoff(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        message_text = "<b>Approved send</b>\n<code>artifact_treport_html_report</code>"
        handoff = _eligible_handoff(run_id="run_fake_success", message_text=message_text)
        handoff_path = _write_handoff(tmp_path, handoff)
        before = handoff_path.read_bytes()
        first_exit, first_payload, _ = await _run_delivery(handoff_path)
        assert first_exit == 1
        _approve_template(_approval_template_path(first_payload))
        calls: list[dict[str, str]] = []

        exit_code, payload, stderr = await _run_delivery(
            handoff_path,
            telegram_client_factory=lambda: _RecordingTelegramClient(calls),
        )

        receipt = _read_json(_receipt_path(payload))
        summary = _read_json(_summary_path(payload))
        receipt_and_summary_serialized = "\n".join(
            [
                json.dumps(receipt, ensure_ascii=False),
                json.dumps(summary, ensure_ascii=False),
            ]
        )

        assert stderr == ""
        assert exit_code == 0
        assert payload["telegram_delivery"] == "sent"
        assert payload["live"] is False
        assert calls == [{"text": message_text, "parse_mode": "HTML"}]
        assert receipt["delivery_status"] == "sent"
        assert receipt["attempt_count"] == 1
        assert receipt["adapter_name"] == "recording_fake_telegram"
        assert receipt["provider_message_id_hash"]
        assert "provider-message-42" not in json.dumps(receipt)
        assert summary["latest_delivery_status"] == "sent"
        assert summary["receipt_path"] == payload["receipt_path"]
        assert handoff_path.read_bytes() == before
        assert str(tmp_path) not in receipt_and_summary_serialized
        assert message_text not in receipt_and_summary_serialized
        assert "raw_request" not in receipt_and_summary_serialized
        assert "raw_response" not in receipt_and_summary_serialized
        assert "Traceback" not in receipt_and_summary_serialized

    run_async(scenario())


@pytest.mark.parametrize(
    ("outcome", "error_code"),
    [
        (RuntimeError("token secret-token-value chat 987654321"), "telegram_send_failed"),
        (TimeoutError("timed out after raw endpoint"), "telegram_timeout"),
        (OSError("network path leaked"), "telegram_network_failure"),
    ],
)
def test_telegram_delivery_fake_failures_write_safe_failed_receipt(
    tmp_path: Path,
    outcome: BaseException,
    error_code: str,
) -> None:
    async def scenario() -> None:
        handoff_path = _write_handoff(
            tmp_path,
            _eligible_handoff(run_id=f"run_{error_code}"),
        )
        first_exit, first_payload, _ = await _run_delivery(handoff_path)
        assert first_exit == 1
        _approve_template(_approval_template_path(first_payload))
        calls: list[dict[str, str]] = []

        exit_code, payload, stderr = await _run_delivery(
            handoff_path,
            telegram_client_factory=lambda: _RecordingTelegramClient(
                calls,
                outcome=outcome,
            ),
        )

        receipt = _read_json(_receipt_path(payload))
        serialized = json.dumps(receipt, ensure_ascii=False)

        assert stderr == ""
        assert exit_code == 1
        assert payload["telegram_delivery"] == "failed"
        assert receipt["delivery_status"] == "failed"
        assert receipt["safe_error"]["code"] == error_code
        assert "secret-token-value" not in serialized
        assert "987654321" not in serialized
        assert str(tmp_path) not in serialized
        assert "raw endpoint" not in serialized
        assert "network path leaked" not in serialized
        assert calls

    run_async(scenario())


def test_telegram_delivery_existing_sent_receipt_blocks_duplicate_before_network(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        handoff_path = _write_handoff(
            tmp_path,
            _eligible_handoff(run_id="run_duplicate_sent"),
        )
        first_exit, first_payload, _ = await _run_delivery(handoff_path)
        assert first_exit == 1
        _approve_template(_approval_template_path(first_payload))
        first_calls: list[dict[str, str]] = []
        sent_exit, sent_payload, _ = await _run_delivery(
            handoff_path,
            telegram_client_factory=lambda: _RecordingTelegramClient(first_calls),
        )
        duplicate_calls: list[dict[str, str]] = []

        duplicate_exit, duplicate_payload, stderr = await _run_delivery(
            handoff_path,
            telegram_client_factory=lambda: _RecordingTelegramClient(duplicate_calls),
        )

        duplicate_receipt = _read_json(_receipt_path(duplicate_payload))
        summary = _read_json(_summary_path(duplicate_payload))

        assert sent_exit == 0
        assert sent_payload["telegram_delivery"] == "sent"
        assert first_calls
        assert stderr == ""
        assert duplicate_exit == 1
        assert duplicate_payload["telegram_delivery"] == "duplicate_blocked"
        assert duplicate_payload["reason"] == "duplicate_sent_receipt_exists"
        assert duplicate_calls == []
        assert duplicate_receipt["delivery_status"] == "duplicate_blocked"
        assert summary["latest_delivery_status"] == "duplicate_blocked"

    run_async(scenario())


def test_telegram_delivery_failed_only_receipts_require_retry_flag_and_append_attempts(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        handoff_path = _write_handoff(
            tmp_path,
            _eligible_handoff(run_id="run_retry_failed"),
        )
        first_exit, first_payload, _ = await _run_delivery(handoff_path)
        assert first_exit == 1
        _approve_template(_approval_template_path(first_payload))
        first_calls: list[dict[str, str]] = []
        failed_exit, failed_payload, _ = await _run_delivery(
            handoff_path,
            telegram_client_factory=lambda: _RecordingTelegramClient(
                first_calls,
                outcome=RuntimeError("first failure secret"),
            ),
        )
        blocked_calls: list[dict[str, str]] = []

        blocked_exit, blocked_payload, stderr = await _run_delivery(
            handoff_path,
            telegram_client_factory=lambda: _RecordingTelegramClient(blocked_calls),
        )

        retry_calls: list[dict[str, str]] = []
        retry_exit, retry_payload, _ = await _run_delivery(
            handoff_path,
            "--retry-failed-delivery",
            telegram_client_factory=lambda: _RecordingTelegramClient(retry_calls),
        )
        first_receipt = _read_json(_receipt_path(failed_payload))
        retry_receipt = _read_json(_receipt_path(retry_payload))

        assert failed_exit == 1
        assert first_calls
        assert stderr == ""
        assert blocked_exit == 1
        assert blocked_payload["telegram_delivery"] == "failed"
        assert blocked_payload["reason"] == "failed_delivery_retry_required"
        assert blocked_calls == []
        assert retry_exit == 0
        assert retry_payload["telegram_delivery"] == "sent"
        assert retry_calls
        assert first_receipt["delivery_status"] == "failed"
        assert first_receipt["attempt_count"] == 1
        assert retry_receipt["delivery_status"] == "sent"
        assert retry_receipt["attempt_count"] == 2
        assert _receipt_path(failed_payload) != _receipt_path(retry_payload)

    run_async(scenario())


def test_telegram_delivery_live_requires_credentials_after_approval_and_writes_safe_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        handoff_path = _write_handoff(
            tmp_path,
            _eligible_handoff(run_id="run_live_missing_credentials"),
        )
        first_exit, first_payload, _ = await _run_delivery(handoff_path)
        assert first_exit == 1
        _approve_template(_approval_template_path(first_payload))

        exit_code, payload, stderr = await _run_delivery(handoff_path, "--live")

        receipt = _read_json(_receipt_path(payload))
        serialized = json.dumps(receipt, ensure_ascii=False)

        assert stderr == ""
        assert exit_code == 1
        assert payload["telegram_delivery"] == "failed"
        assert payload["live"] is True
        assert receipt["delivery_status"] == "failed"
        assert receipt["safe_error"]["code"] == "telegram_missing_bot_token"
        assert "TELEGRAM_BOT_TOKEN" in serialized
        assert "TELEGRAM_CHAT_ID" in serialized
        assert str(tmp_path) not in serialized

    run_async(scenario())


@pytest.mark.parametrize(
    ("field", "value", "expected_status"),
    [
        ("target_alias", "ops", "outside_approved_bounds"),
        ("required_scopes", [], "missing_scope"),
        ("boundary_fingerprint", "tampered", "outside_approved_bounds"),
    ],
)
def test_telegram_delivery_invalid_approval_boundary_blocks_before_network(
    tmp_path: Path,
    field: str,
    value: object,
    expected_status: str,
) -> None:
    async def scenario() -> None:
        handoff_path = _write_handoff(
            tmp_path,
            _eligible_handoff(run_id=f"run_invalid_approval_{field}"),
        )
        first_exit, first_payload, _ = await _run_delivery(handoff_path)
        assert first_exit == 1
        template_path = _approval_template_path(first_payload)
        template = _read_json(template_path)
        template["status"] = "approved"
        template[field] = value
        _write_json(template_path, template)
        calls: list[dict[str, str]] = []

        exit_code, payload, stderr = await _run_delivery(
            handoff_path,
            telegram_client_factory=lambda: _RecordingTelegramClient(calls),
        )

        assert stderr == ""
        assert exit_code == 1
        assert payload["telegram_delivery"] == "failed"
        assert payload["reason"] == "telegram_delivery_approval_required"
        assert payload["approval"]["status"] == expected_status
        assert calls == []

    run_async(scenario())


def test_real_telegram_bot_api_client_uses_narrow_send_message_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    class Response:
        status_code = 200

        def json(self) -> dict[str, object]:
            return {"ok": True, "result": {"message_id": 12345}}

    def fake_post(url: str, *, data: dict[str, object], timeout: float) -> Response:
        calls.append({"url": url, "data": data, "timeout": timeout})
        return Response()

    monkeypatch.setattr(
        "agent_treport.signal_report.telegram_delivery.requests.post",
        fake_post,
    )
    client = RealTelegramBotApiClient(
        credentials=TelegramCredentials(
            bot_token="bot-token-value",
            chat_id="chat-id-value",
        ),
        timeout_seconds=3.0,
    )

    result = client.send_message(text="<b>hello</b>", parse_mode="HTML")

    assert result == {"provider_message_id": "12345"}
    assert calls == [
        {
            "url": "https://api.telegram.org/botbot-token-value/sendMessage",
            "data": {
                "chat_id": "chat-id-value",
                "text": "<b>hello</b>",
                "parse_mode": "HTML",
            },
            "timeout": 3.0,
        }
    ]


def test_real_telegram_bot_api_client_api_rejection_raises_safe_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        status_code = 400

        def json(self) -> dict[str, object]:
            return {"ok": False, "description": "raw rejection detail"}

    monkeypatch.setattr(
        "agent_treport.signal_report.telegram_delivery.requests.post",
        lambda *args, **kwargs: Response(),
    )
    client = RealTelegramBotApiClient(
        credentials=TelegramCredentials(
            bot_token="bot-token-value",
            chat_id="chat-id-value",
        )
    )

    with pytest.raises(TelegramDeliveryProviderError) as exc_info:
        client.send_message(text="<b>hello</b>", parse_mode="HTML")

    assert exc_info.value.code == "telegram_api_rejected"
    assert exc_info.value.safe_message == "Telegram API rejected delivery."
    assert exc_info.value.details == {"http_status": 400}
    assert "raw rejection detail" not in exc_info.value.safe_message
