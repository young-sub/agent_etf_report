from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import requests
from agent_pack.models import JsonValue

TELEGRAM_DELIVERY_SCOPE = "telegram_delivery"
TELEGRAM_BOT_TOKEN_ENV_VAR = "TELEGRAM_BOT_TOKEN"
DEFAULT_TELEGRAM_CHAT_ID_ENV_VAR = "TELEGRAM_CHAT_ID"
TELEGRAM_DELIVERY_PREFLIGHT_SCHEMA_VERSION = (
    "agent_treport.telegram_delivery_preflight.v1"
)
TELEGRAM_DELIVERY_APPROVAL_SCHEMA_VERSION = (
    "agent_treport.telegram_delivery_approval.v1"
)
TELEGRAM_DELIVERY_RECEIPT_SCHEMA_VERSION = (
    "agent_treport.telegram_delivery_receipt.v1"
)
TELEGRAM_DELIVERY_SUMMARY_SCHEMA_VERSION = (
    "agent_treport.telegram_delivery_summary.v1"
)
DEFAULT_TELEGRAM_DELIVERY_PREFLIGHT_FILENAME = "telegram_delivery_preflight.json"
DEFAULT_TELEGRAM_DELIVERY_APPROVAL_TEMPLATE_FILENAME = (
    "telegram_delivery_approval_template.json"
)
TELEGRAM_DELIVERY_RECEIPT_DIRNAME = "telegram_delivery_receipts"
TELEGRAM_DELIVERY_SUMMARY_FILENAME = "telegram_delivery_summary.json"
TELEGRAM_HTML_PARSE_MODE = "HTML"

TELEGRAM_DELIVERY_EXCLUDED_RAW_FIELDS = (
    "bot_token",
    "raw_chat_id",
    "raw_request",
    "raw_response",
    "raw_api_payload",
    "absolute_local_paths",
    "stack_traces",
    ".env_contents",
    "credential_values",
    "message_text",
)


class TelegramDeliveryInputError(RuntimeError):
    """Raised when the handoff or delivery command input is invalid."""


class TelegramDeliveryProviderError(RuntimeError):
    def __init__(
        self,
        *,
        code: str,
        safe_message: str,
        retryable: bool,
        details: Mapping[str, JsonValue] | None = None,
    ) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
        self.retryable = retryable
        self.details = dict(details or {})


class TelegramDeliveryClient(Protocol):
    adapter_name: str

    def send_message(self, *, text: str, parse_mode: str) -> object:
        ...


type TelegramClientFactory = Callable[[], TelegramDeliveryClient]


@dataclass(frozen=True)
class TelegramDeliveryContext:
    handoff_path: Path
    handoff_identity: str
    package_path: Path
    run_id: str
    telegram_alert_artifact_id: str
    message_text: str
    message_fingerprint: str
    message_length: int
    parse_mode: str
    target_alias: str
    credential_env_vars: tuple[str, str]
    idempotency_key: str


@dataclass(frozen=True)
class TelegramCredentials:
    bot_token: str
    chat_id: str


class FakeTelegramDeliveryClient:
    adapter_name = "fake_telegram_delivery"

    def send_message(self, *, text: str, parse_mode: str) -> object:
        _ = text, parse_mode
        return {"provider_message_id": "fake-telegram-message"}


class RealTelegramBotApiClient:
    adapter_name = "telegram_bot_api"

    def __init__(
        self,
        *,
        credentials: TelegramCredentials,
        timeout_seconds: float = 12.0,
    ) -> None:
        self._bot_token = credentials.bot_token
        self._chat_id = credentials.chat_id
        self._timeout_seconds = timeout_seconds

    def send_message(self, *, text: str, parse_mode: str) -> object:
        url = f"https://api.telegram.org/bot{self._bot_token}/sendMessage"
        try:
            response = requests.post(
                url,
                data={
                    "chat_id": self._chat_id,
                    "text": text,
                    "parse_mode": parse_mode,
                },
                timeout=self._timeout_seconds,
            )
        except requests.Timeout as exc:
            raise TelegramDeliveryProviderError(
                code="telegram_timeout",
                safe_message="Telegram delivery timed out.",
                retryable=True,
            ) from exc
        except requests.RequestException as exc:
            raise TelegramDeliveryProviderError(
                code="telegram_network_failure",
                safe_message="Telegram delivery network request failed.",
                retryable=True,
            ) from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise TelegramDeliveryProviderError(
                code="telegram_invalid_response",
                safe_message="Telegram delivery returned an invalid response.",
                retryable=False,
                details={"http_status": response.status_code},
            ) from exc
        if not isinstance(payload, Mapping) or payload.get("ok") is not True:
            raise TelegramDeliveryProviderError(
                code="telegram_api_rejected",
                safe_message="Telegram API rejected delivery.",
                retryable=False,
                details={"http_status": response.status_code},
            )
        result = payload.get("result")
        message_id = result.get("message_id") if isinstance(result, Mapping) else None
        return {
            "provider_message_id": str(message_id)
            if isinstance(message_id, str | int)
            else None
        }


def run_telegram_delivery(
    *,
    handoff_path: Path,
    target_alias: str,
    approval_path: Path | None,
    preflight_path: Path | None,
    live: bool,
    retry_failed_delivery: bool,
    telegram_client_factory: TelegramClientFactory | None = None,
    now: Callable[[], datetime] | None = None,
) -> tuple[int, dict[str, JsonValue]]:
    current_time = _current_time(now)
    handoff = _read_json_object(handoff_path, label="pre-publish handoff")
    eligibility_error = _delivery_eligibility_error(handoff)
    if eligibility_error is not None:
        return 1, _command_payload(
            context=None,
            telegram_delivery="failed",
            reason=eligibility_error,
            live=live,
        )
    context = _delivery_context(
        handoff=handoff,
        handoff_path=handoff_path,
        target_alias=target_alias,
    )
    boundary = _approval_boundary(context)
    resolved_preflight_path = preflight_path or (
        context.package_path / DEFAULT_TELEGRAM_DELIVERY_PREFLIGHT_FILENAME
    )
    template_path = resolved_preflight_path.with_name(
        DEFAULT_TELEGRAM_DELIVERY_APPROVAL_TEMPLATE_FILENAME
    )
    resolved_approval_path = approval_path or template_path
    preflight = _build_preflight(
        context=context,
        boundary=boundary,
        generated_at=current_time,
    )
    template = _build_approval_template(context=context, boundary=boundary)
    _write_json(resolved_preflight_path, preflight)
    _write_template_if_safe(
        template_path=template_path,
        approval_path=resolved_approval_path,
        template=template,
    )
    approval_summary = evaluate_telegram_delivery_approval(
        approval_path=resolved_approval_path,
        boundary=boundary,
        now=current_time,
    )
    if not approval_summary["valid"]:
        summary_path = _write_delivery_summary(
            context=context,
            delivery_status="failed",
            approval_summary=approval_summary,
            receipt=None,
            live=live,
            reason="telegram_delivery_approval_required",
        )
        return 1, _command_payload(
            context=context,
            telegram_delivery="failed",
            reason="telegram_delivery_approval_required",
            approval_summary=approval_summary,
            preflight_path=resolved_preflight_path,
            approval_template_path=template_path,
            approval_path=resolved_approval_path,
            delivery_summary_path=summary_path,
            live=live,
        )

    existing_receipts = _existing_receipts(context)
    if _has_sent_receipt(existing_receipts):
        receipt = _write_receipt(
            context=context,
            delivery_status="duplicate_blocked",
            approval_summary=approval_summary,
            attempt_count=len(existing_receipts) + 1,
            attempted_at=current_time,
            adapter_name="none",
            safe_error={
                "code": "duplicate_sent_receipt_exists",
                "message": "A sent Telegram delivery receipt already exists.",
                "retryable": False,
                "details": {},
            },
        )
        summary_path = _write_delivery_summary(
            context=context,
            delivery_status="duplicate_blocked",
            approval_summary=approval_summary,
            receipt=receipt,
            live=live,
            reason="duplicate_sent_receipt_exists",
        )
        return 1, _command_payload(
            context=context,
            telegram_delivery="duplicate_blocked",
            reason="duplicate_sent_receipt_exists",
            approval_summary=approval_summary,
            preflight_path=resolved_preflight_path,
            approval_template_path=template_path,
            approval_path=resolved_approval_path,
            receipt_path=Path(str(receipt["receipt_path"])),
            delivery_summary_path=summary_path,
            live=live,
        )
    if _has_failed_receipt(existing_receipts) and not retry_failed_delivery:
        summary_path = _write_delivery_summary(
            context=context,
            delivery_status="failed",
            approval_summary=approval_summary,
            receipt=None,
            live=live,
            reason="failed_delivery_retry_required",
        )
        return 1, _command_payload(
            context=context,
            telegram_delivery="failed",
            reason="failed_delivery_retry_required",
            approval_summary=approval_summary,
            preflight_path=resolved_preflight_path,
            approval_template_path=template_path,
            approval_path=resolved_approval_path,
            delivery_summary_path=summary_path,
            live=live,
        )

    attempt_count = len(existing_receipts) + 1
    adapter_name = "unknown"
    client: TelegramDeliveryClient
    if live and telegram_client_factory is None:
        credentials_result = _load_credentials(context)
        if isinstance(credentials_result, Mapping):
            receipt = _write_receipt(
                context=context,
                delivery_status="failed",
                approval_summary=approval_summary,
                attempt_count=attempt_count,
                attempted_at=current_time,
                adapter_name="telegram_bot_api",
                safe_error=credentials_result,
            )
            summary_path = _write_delivery_summary(
                context=context,
                delivery_status="failed",
                approval_summary=approval_summary,
                receipt=receipt,
                live=live,
                reason=str(credentials_result["code"]),
            )
            return 1, _command_payload(
                context=context,
                telegram_delivery="failed",
                reason=str(credentials_result["code"]),
                approval_summary=approval_summary,
                preflight_path=resolved_preflight_path,
                approval_template_path=template_path,
                approval_path=resolved_approval_path,
                receipt_path=Path(str(receipt["receipt_path"])),
                delivery_summary_path=summary_path,
                live=live,
            )
        client = RealTelegramBotApiClient(credentials=credentials_result)
    elif telegram_client_factory is not None:
        client = telegram_client_factory()
    else:
        client = FakeTelegramDeliveryClient()
    adapter_name = str(getattr(client, "adapter_name", adapter_name))
    try:
        provider_result = client.send_message(
            text=context.message_text,
            parse_mode=context.parse_mode,
        )
    except TelegramDeliveryProviderError as exc:
        safe_error = {
            "code": exc.code,
            "message": exc.safe_message,
            "retryable": exc.retryable,
            "details": dict(exc.details),
        }
        receipt = _write_receipt(
            context=context,
            delivery_status="failed",
            approval_summary=approval_summary,
            attempt_count=attempt_count,
            attempted_at=current_time,
            adapter_name=adapter_name,
            safe_error=safe_error,
        )
        summary_path = _write_delivery_summary(
            context=context,
            delivery_status="failed",
            approval_summary=approval_summary,
            receipt=receipt,
            live=live,
            reason=exc.code,
        )
        return 1, _command_payload(
            context=context,
            telegram_delivery="failed",
            reason=exc.code,
            approval_summary=approval_summary,
            preflight_path=resolved_preflight_path,
            approval_template_path=template_path,
            approval_path=resolved_approval_path,
            receipt_path=Path(str(receipt["receipt_path"])),
            delivery_summary_path=summary_path,
            live=live,
        )
    except TimeoutError:
        safe_error = _safe_error(
            code="telegram_timeout",
            message="Telegram delivery timed out.",
            retryable=True,
        )
        receipt = _write_receipt(
            context=context,
            delivery_status="failed",
            approval_summary=approval_summary,
            attempt_count=attempt_count,
            attempted_at=current_time,
            adapter_name=adapter_name,
            safe_error=safe_error,
        )
        summary_path = _write_delivery_summary(
            context=context,
            delivery_status="failed",
            approval_summary=approval_summary,
            receipt=receipt,
            live=live,
            reason="telegram_timeout",
        )
        return 1, _command_payload(
            context=context,
            telegram_delivery="failed",
            reason="telegram_timeout",
            approval_summary=approval_summary,
            preflight_path=resolved_preflight_path,
            approval_template_path=template_path,
            approval_path=resolved_approval_path,
            receipt_path=Path(str(receipt["receipt_path"])),
            delivery_summary_path=summary_path,
            live=live,
        )
    except OSError:
        safe_error = _safe_error(
            code="telegram_network_failure",
            message="Telegram delivery network request failed.",
            retryable=True,
        )
        receipt = _write_receipt(
            context=context,
            delivery_status="failed",
            approval_summary=approval_summary,
            attempt_count=attempt_count,
            attempted_at=current_time,
            adapter_name=adapter_name,
            safe_error=safe_error,
        )
        summary_path = _write_delivery_summary(
            context=context,
            delivery_status="failed",
            approval_summary=approval_summary,
            receipt=receipt,
            live=live,
            reason="telegram_network_failure",
        )
        return 1, _command_payload(
            context=context,
            telegram_delivery="failed",
            reason="telegram_network_failure",
            approval_summary=approval_summary,
            preflight_path=resolved_preflight_path,
            approval_template_path=template_path,
            approval_path=resolved_approval_path,
            receipt_path=Path(str(receipt["receipt_path"])),
            delivery_summary_path=summary_path,
            live=live,
        )
    except Exception:
        safe_error = _safe_error(
            code="telegram_send_failed",
            message="Telegram delivery failed.",
            retryable=False,
        )
        receipt = _write_receipt(
            context=context,
            delivery_status="failed",
            approval_summary=approval_summary,
            attempt_count=attempt_count,
            attempted_at=current_time,
            adapter_name=adapter_name,
            safe_error=safe_error,
        )
        summary_path = _write_delivery_summary(
            context=context,
            delivery_status="failed",
            approval_summary=approval_summary,
            receipt=receipt,
            live=live,
            reason="telegram_send_failed",
        )
        return 1, _command_payload(
            context=context,
            telegram_delivery="failed",
            reason="telegram_send_failed",
            approval_summary=approval_summary,
            preflight_path=resolved_preflight_path,
            approval_template_path=template_path,
            approval_path=resolved_approval_path,
            receipt_path=Path(str(receipt["receipt_path"])),
            delivery_summary_path=summary_path,
            live=live,
        )

    provider_message_id = _provider_message_id(provider_result)
    receipt = _write_receipt(
        context=context,
        delivery_status="sent",
        approval_summary=approval_summary,
        attempt_count=attempt_count,
        attempted_at=current_time,
        adapter_name=adapter_name,
        provider_message_id=provider_message_id,
    )
    summary_path = _write_delivery_summary(
        context=context,
        delivery_status="sent",
        approval_summary=approval_summary,
        receipt=receipt,
        live=live,
        reason=None,
    )
    return 0, _command_payload(
        context=context,
        telegram_delivery="sent",
        reason=None,
        approval_summary=approval_summary,
        preflight_path=resolved_preflight_path,
        approval_template_path=template_path,
        approval_path=resolved_approval_path,
        receipt_path=Path(str(receipt["receipt_path"])),
        delivery_summary_path=summary_path,
        live=live,
    )


def _delivery_eligibility_error(handoff: Mapping[str, JsonValue]) -> str | None:
    if handoff.get("status") != "user_ready":
        return "status_not_user_ready"
    if handoff.get("delivery_blocked") is not False:
        return "delivery_blocked"
    closure = handoff.get("closure")
    closure_mapping = closure if isinstance(closure, Mapping) else {}
    user_ready = closure_mapping.get("full_user_ready_closure")
    user_ready_mapping = user_ready if isinstance(user_ready, Mapping) else {}
    if user_ready_mapping.get("status") != "met":
        return "full_user_ready_closure_not_met"
    preview = handoff.get("preview")
    preview_mapping = preview if isinstance(preview, Mapping) else {}
    if preview_mapping.get("telegram_delivery") != "not_sent":
        return "telegram_delivery_not_sendable"
    message = preview_mapping.get("telegram_message")
    if not isinstance(message, Mapping) or not isinstance(message.get("text"), str):
        return "telegram_message_missing"
    if not str(message.get("text", "")).strip():
        return "telegram_message_missing"
    artifacts = _reference_artifacts(handoff)
    telegram_artifact = artifacts.get("telegram_alert")
    if not isinstance(telegram_artifact, Mapping):
        return "telegram_artifact_missing"
    artifact_id = telegram_artifact.get("artifact_id")
    if not isinstance(artifact_id, str) or not artifact_id:
        return "telegram_artifact_missing"
    if message.get("artifact_id") != artifact_id:
        return "telegram_artifact_missing"
    return None


def _delivery_context(
    *,
    handoff: Mapping[str, JsonValue],
    handoff_path: Path,
    target_alias: str,
) -> TelegramDeliveryContext:
    if not _target_alias_is_safe(target_alias):
        raise TelegramDeliveryInputError(
            "target alias must use letters, numbers, dash, or underscore"
        )
    run_id = _required_text(handoff.get("run_id"), label="handoff run_id")
    preview = _required_mapping(handoff.get("preview"), label="handoff preview")
    message = _required_mapping(
        preview.get("telegram_message"),
        label="handoff preview.telegram_message",
    )
    message_text = _required_text(
        message.get("text"),
        label="handoff preview.telegram_message.text",
    )
    parse_mode = str(message.get("parse_mode") or TELEGRAM_HTML_PARSE_MODE)
    artifacts = _reference_artifacts(handoff)
    telegram_artifact = _required_mapping(
        artifacts.get("telegram_alert"),
        label="handoff telegram_alert artifact",
    )
    artifact_id = _required_text(
        telegram_artifact.get("artifact_id"),
        label="handoff telegram alert artifact_id",
    )
    message_fingerprint = _sha256_text(message_text)
    package_path = _result_package_path(handoff=handoff, handoff_path=handoff_path)
    credential_env_vars = (
        TELEGRAM_BOT_TOKEN_ENV_VAR,
        _chat_id_env_var(target_alias),
    )
    idempotency_key = _idempotency_key(
        run_id=run_id,
        telegram_alert_artifact_id=artifact_id,
        message_fingerprint=message_fingerprint,
        target_alias=target_alias,
    )
    return TelegramDeliveryContext(
        handoff_path=handoff_path,
        handoff_identity=_path_safe_path_text(handoff_path),
        package_path=package_path,
        run_id=run_id,
        telegram_alert_artifact_id=artifact_id,
        message_text=message_text,
        message_fingerprint=message_fingerprint,
        message_length=len(message_text),
        parse_mode=parse_mode,
        target_alias=target_alias,
        credential_env_vars=credential_env_vars,
        idempotency_key=idempotency_key,
    )


def _approval_boundary(context: TelegramDeliveryContext) -> dict[str, JsonValue]:
    return {
        "required_scopes": [TELEGRAM_DELIVERY_SCOPE],
        "handoff_identity": context.handoff_identity,
        "run_id": context.run_id,
        "telegram_alert_artifact_id": context.telegram_alert_artifact_id,
        "message_fingerprint": context.message_fingerprint,
        "message_length": context.message_length,
        "parse_mode": context.parse_mode,
        "target_alias": context.target_alias,
        "credential_expectations": list(context.credential_env_vars),
        "excluded_raw_fields": list(TELEGRAM_DELIVERY_EXCLUDED_RAW_FIELDS),
    }


def _boundary_fingerprint(boundary: Mapping[str, JsonValue]) -> str:
    canonical = json.dumps(
        boundary,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _build_preflight(
    *,
    context: TelegramDeliveryContext,
    boundary: Mapping[str, JsonValue],
    generated_at: datetime,
) -> dict[str, JsonValue]:
    return {
        "schema_version": TELEGRAM_DELIVERY_PREFLIGHT_SCHEMA_VERSION,
        "generated_at": generated_at.isoformat(),
        "command": "deliver-telegram-alert",
        "approval": {
            "required_scopes": [TELEGRAM_DELIVERY_SCOPE],
            "boundary_fingerprint": _boundary_fingerprint(boundary),
        },
        "boundary": dict(boundary),
        "disclosure": {
            "handoff_identity": context.handoff_identity,
            "run_id": context.run_id,
            "telegram_alert_artifact_id": context.telegram_alert_artifact_id,
            "message_fingerprint": context.message_fingerprint,
            "message_length": context.message_length,
            "parse_mode": context.parse_mode,
            "target_alias": context.target_alias,
            "required_scopes": [TELEGRAM_DELIVERY_SCOPE],
            "credential_expectations": list(context.credential_env_vars),
            "excluded_raw_fields": list(TELEGRAM_DELIVERY_EXCLUDED_RAW_FIELDS),
        },
    }


def _build_approval_template(
    *,
    context: TelegramDeliveryContext,
    boundary: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    return {
        "schema_version": TELEGRAM_DELIVERY_APPROVAL_SCHEMA_VERSION,
        "status": "pending",
        "required_scopes": [TELEGRAM_DELIVERY_SCOPE],
        "boundary_fingerprint": _boundary_fingerprint(boundary),
        "handoff_identity": context.handoff_identity,
        "run_id": context.run_id,
        "telegram_alert_artifact_id": context.telegram_alert_artifact_id,
        "message_fingerprint": context.message_fingerprint,
        "message_length": context.message_length,
        "parse_mode": context.parse_mode,
        "target_alias": context.target_alias,
        "credential_expectations": list(context.credential_env_vars),
        "excluded_raw_fields": list(TELEGRAM_DELIVERY_EXCLUDED_RAW_FIELDS),
        "expires_at": None,
        "instructions": [
            "Review the sibling Telegram delivery preflight before approving.",
            "Set status to approved only for this exact message and target alias.",
            "Set status to revoked to block delivery.",
        ],
    }


def evaluate_telegram_delivery_approval(
    *,
    approval_path: Path,
    boundary: Mapping[str, JsonValue],
    now: datetime,
) -> dict[str, JsonValue]:
    required_scopes = _text_list(boundary.get("required_scopes"))
    requested_fingerprint = _boundary_fingerprint(boundary)
    if not approval_path.is_file():
        return {
            "valid": False,
            "status": "missing",
            "approval_path": _path_safe_path_text(approval_path),
            "required_scopes": required_scopes,
            "missing_scopes": required_scopes,
            "unapproved_scopes": required_scopes,
            "outside_approved_bounds": [],
            "approved_boundary_fingerprint": None,
            "requested_boundary_fingerprint": requested_fingerprint,
        }
    try:
        profile = json.loads(approval_path.read_text(encoding="utf-8"))
    except Exception:
        return _invalid_approval_summary(
            approval_path=approval_path,
            required_scopes=required_scopes,
            requested_fingerprint=requested_fingerprint,
            status="invalid",
            reason="approval profile is unreadable or invalid JSON",
        )
    if not isinstance(profile, Mapping):
        return _invalid_approval_summary(
            approval_path=approval_path,
            required_scopes=required_scopes,
            requested_fingerprint=requested_fingerprint,
            status="invalid",
            reason="approval profile must be a JSON object",
        )
    status = profile.get("status")
    approved_scopes = _text_list(profile.get("approved_scopes") or profile.get("required_scopes"))
    unapproved_scopes = sorted(set(required_scopes).difference(approved_scopes))
    expired = _is_expired(profile.get("expires_at"), now=now)
    outside_bounds = _telegram_approval_bound_mismatches(profile=profile, boundary=boundary)
    approved_fingerprint = profile.get("boundary_fingerprint")
    valid = (
        status == "approved"
        and not expired
        and not unapproved_scopes
        and not outside_bounds
        and approved_fingerprint == requested_fingerprint
    )
    if status == "approved" and expired:
        result_status = "expired"
    elif status in {"pending", "revoked"}:
        result_status = str(status)
    elif status == "approved" and unapproved_scopes:
        result_status = "missing_scope"
    elif status == "approved" and outside_bounds:
        result_status = "outside_approved_bounds"
    elif status == "approved":
        result_status = "approved"
    else:
        result_status = "invalid"
    return {
        "valid": valid,
        "status": result_status,
        "approval_path": _path_safe_path_text(approval_path),
        "required_scopes": required_scopes,
        "missing_scopes": unapproved_scopes
        if unapproved_scopes
        else ([] if valid else required_scopes),
        "unapproved_scopes": unapproved_scopes,
        "outside_approved_bounds": outside_bounds,
        "approved_boundary_fingerprint": approved_fingerprint
        if isinstance(approved_fingerprint, str)
        else None,
        "requested_boundary_fingerprint": requested_fingerprint,
    }


def _telegram_approval_bound_mismatches(
    *,
    profile: Mapping[str, object],
    boundary: Mapping[str, JsonValue],
) -> list[str]:
    mismatches: list[str] = []
    if profile.get("boundary_fingerprint") != _boundary_fingerprint(boundary):
        mismatches.append("boundary_fingerprint")
    for field in (
        "handoff_identity",
        "run_id",
        "telegram_alert_artifact_id",
        "message_fingerprint",
        "message_length",
        "parse_mode",
        "target_alias",
    ):
        if profile.get(field) != boundary.get(field):
            mismatches.append(field)
    if _text_list(profile.get("excluded_raw_fields")) != _text_list(
        boundary.get("excluded_raw_fields")
    ):
        mismatches.append("excluded_raw_fields")
    if _text_list(profile.get("credential_expectations")) != _text_list(
        boundary.get("credential_expectations")
    ):
        mismatches.append("credential_expectations")
    return list(dict.fromkeys(mismatches))


def _write_receipt(
    *,
    context: TelegramDeliveryContext,
    delivery_status: str,
    approval_summary: Mapping[str, JsonValue],
    attempt_count: int,
    attempted_at: datetime,
    adapter_name: str,
    provider_message_id: str | None = None,
    safe_error: Mapping[str, JsonValue] | None = None,
) -> dict[str, JsonValue]:
    receipt_dir = context.package_path / TELEGRAM_DELIVERY_RECEIPT_DIRNAME
    receipt_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = receipt_dir / (
        f"telegram_delivery_receipt_{context.idempotency_key[-16:]}"
        f"_attempt_{attempt_count:03d}.json"
    )
    receipt: dict[str, JsonValue] = {
        "schema_version": TELEGRAM_DELIVERY_RECEIPT_SCHEMA_VERSION,
        "delivery_status": delivery_status,
        "idempotency_key": context.idempotency_key,
        "run_id": context.run_id,
        "telegram_alert_artifact_id": context.telegram_alert_artifact_id,
        "message_fingerprint": context.message_fingerprint,
        "message_length": context.message_length,
        "parse_mode": context.parse_mode,
        "target_alias": context.target_alias,
        "approved_scope": TELEGRAM_DELIVERY_SCOPE,
        "approval": _path_safe_approval_summary(approval_summary),
        "attempt_count": attempt_count,
        "attempted_at": attempted_at.isoformat(),
        "adapter_name": adapter_name,
        "receipt_path": _path_safe_path_text(receipt_path),
        "package_path": _path_safe_path_text(context.package_path),
        "handoff_path": context.handoff_identity,
    }
    if provider_message_id:
        receipt["provider_message_id_hash"] = _sha256_text(provider_message_id)
    if safe_error is not None:
        receipt["safe_error"] = dict(safe_error)
    _write_json(receipt_path, receipt)
    return receipt


def _write_delivery_summary(
    *,
    context: TelegramDeliveryContext,
    delivery_status: str,
    approval_summary: Mapping[str, JsonValue],
    receipt: Mapping[str, JsonValue] | None,
    live: bool,
    reason: str | None,
) -> Path:
    summary_path = context.package_path / TELEGRAM_DELIVERY_SUMMARY_FILENAME
    receipt_paths = [
        str(item.get("receipt_path"))
        for item in _existing_receipts(context)
        if isinstance(item.get("receipt_path"), str)
    ]
    receipt_path = receipt.get("receipt_path") if isinstance(receipt, Mapping) else None
    summary: dict[str, JsonValue] = {
        "schema_version": TELEGRAM_DELIVERY_SUMMARY_SCHEMA_VERSION,
        "latest_delivery_status": delivery_status,
        "reason": reason,
        "idempotency_key": context.idempotency_key,
        "run_id": context.run_id,
        "telegram_alert_artifact_id": context.telegram_alert_artifact_id,
        "message_fingerprint": context.message_fingerprint,
        "message_length": context.message_length,
        "parse_mode": context.parse_mode,
        "target_alias": context.target_alias,
        "approved_scope": TELEGRAM_DELIVERY_SCOPE,
        "approval": _path_safe_approval_summary(approval_summary),
        "live": live,
        "receipt_path": receipt_path if isinstance(receipt_path, str) else None,
        "receipt_paths": receipt_paths,
        "delivery_summary_path": _path_safe_path_text(summary_path),
        "package_path": _path_safe_path_text(context.package_path),
        "handoff_path": context.handoff_identity,
    }
    _write_json(summary_path, summary)
    return summary_path


def _existing_receipts(context: TelegramDeliveryContext) -> list[dict[str, JsonValue]]:
    receipt_dir = context.package_path / TELEGRAM_DELIVERY_RECEIPT_DIRNAME
    if not receipt_dir.is_dir():
        return []
    receipts: list[dict[str, JsonValue]] = []
    for path in sorted(receipt_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("idempotency_key") == context.idempotency_key:
            receipts.append(payload)
    return receipts


def _has_sent_receipt(receipts: Sequence[Mapping[str, JsonValue]]) -> bool:
    return any(receipt.get("delivery_status") == "sent" for receipt in receipts)


def _has_failed_receipt(receipts: Sequence[Mapping[str, JsonValue]]) -> bool:
    return any(receipt.get("delivery_status") == "failed" for receipt in receipts)


def _load_credentials(
    context: TelegramDeliveryContext,
) -> TelegramCredentials | dict[str, JsonValue]:
    bot_token = _env_value(TELEGRAM_BOT_TOKEN_ENV_VAR)
    if not bot_token:
        return _safe_error(
            code="telegram_missing_bot_token",
            message="Telegram bot token is not available.",
            retryable=True,
            details={
                "missing_env_var": TELEGRAM_BOT_TOKEN_ENV_VAR,
                "credential_env_vars": list(context.credential_env_vars),
            },
        )
    chat_env_var = context.credential_env_vars[1]
    chat_id = _env_value(chat_env_var)
    if not chat_id:
        return _safe_error(
            code="telegram_missing_chat_target",
            message="Telegram chat target is not available.",
            retryable=True,
            details={
                "missing_env_var": chat_env_var,
                "credential_env_vars": list(context.credential_env_vars),
            },
        )
    return TelegramCredentials(bot_token=bot_token, chat_id=chat_id)


def _env_value(name: str) -> str | None:
    value = os.getenv(name)
    if value:
        return value
    env_path = Path.cwd() / ".env"
    if not env_path.is_file():
        return None
    try:
        dotenv = importlib.import_module("dotenv")
        values = dotenv.dotenv_values(env_path)
    except Exception:
        return None
    dotenv_value = values.get(name)
    if isinstance(dotenv_value, str) and dotenv_value:
        return dotenv_value
    return None


def _command_payload(
    *,
    context: TelegramDeliveryContext | None,
    telegram_delivery: str,
    reason: str | None,
    live: bool,
    approval_summary: Mapping[str, JsonValue] | None = None,
    preflight_path: Path | None = None,
    approval_template_path: Path | None = None,
    approval_path: Path | None = None,
    receipt_path: Path | None = None,
    delivery_summary_path: Path | None = None,
) -> dict[str, JsonValue]:
    payload: dict[str, JsonValue] = {
        "schema_version": "agent_treport.telegram_delivery.command_result.v1",
        "telegram_delivery": telegram_delivery,
        "delivery_status": telegram_delivery,
        "live": live,
    }
    if reason is not None:
        payload["reason"] = reason
    if context is not None:
        payload.update(
            {
                "run_id": context.run_id,
                "telegram_alert_artifact_id": context.telegram_alert_artifact_id,
                "message_fingerprint": context.message_fingerprint,
                "message_length": context.message_length,
                "parse_mode": context.parse_mode,
                "target_alias": context.target_alias,
                "idempotency_key": context.idempotency_key,
                "handoff_path": context.handoff_identity,
                "package_path": _path_safe_path_text(context.package_path),
            }
        )
    if approval_summary is not None:
        payload["approval"] = _path_safe_approval_summary(approval_summary)
    if preflight_path is not None:
        payload["approval_preflight_path"] = _path_safe_path_text(preflight_path)
    if approval_template_path is not None:
        payload["approval_template_path"] = _path_safe_path_text(approval_template_path)
    if approval_path is not None:
        payload["approval_path"] = _path_safe_path_text(approval_path)
    if receipt_path is not None:
        payload["receipt_path"] = _path_safe_path_text(receipt_path)
    if delivery_summary_path is not None:
        payload["delivery_summary_path"] = _path_safe_path_text(delivery_summary_path)
    return payload


def _reference_artifacts(payload: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
    references = payload.get("references")
    references_mapping = references if isinstance(references, Mapping) else {}
    artifacts = references_mapping.get("artifacts")
    return artifacts if isinstance(artifacts, Mapping) else {}


def _result_package_path(
    *,
    handoff: Mapping[str, JsonValue],
    handoff_path: Path,
) -> Path:
    preview = handoff.get("preview")
    preview_mapping = preview if isinstance(preview, Mapping) else {}
    package_path = preview_mapping.get("result_package_path")
    if isinstance(package_path, str) and package_path:
        return Path(package_path)
    return handoff_path.parent / "telegram-delivery"


def _write_template_if_safe(
    *,
    template_path: Path,
    approval_path: Path,
    template: Mapping[str, JsonValue],
) -> None:
    same_path = _same_resolved_path(template_path, approval_path)
    if same_path and approval_path.exists():
        return
    _write_json(template_path, template)


def _same_resolved_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return left.absolute() == right.absolute()


def _safe_error(
    *,
    code: str,
    message: str,
    retryable: bool,
    details: Mapping[str, JsonValue] | None = None,
) -> dict[str, JsonValue]:
    return {
        "code": code,
        "message": message,
        "retryable": retryable,
        "details": dict(details or {}),
    }


def _provider_message_id(provider_result: object) -> str | None:
    if isinstance(provider_result, Mapping):
        value = provider_result.get("provider_message_id")
        if isinstance(value, str) and value:
            return value
    return None


def _path_safe_approval_summary(
    summary: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    return {
        "valid": bool(summary.get("valid")),
        "status": str(summary.get("status")),
        "required_scopes": _text_list(summary.get("required_scopes")),
        "missing_scopes": _text_list(summary.get("missing_scopes")),
        "unapproved_scopes": _text_list(summary.get("unapproved_scopes")),
        "outside_approved_bounds": _text_list(summary.get("outside_approved_bounds")),
        "approved_boundary_fingerprint": summary.get("approved_boundary_fingerprint")
        if isinstance(summary.get("approved_boundary_fingerprint"), str)
        else None,
        "requested_boundary_fingerprint": summary.get("requested_boundary_fingerprint")
        if isinstance(summary.get("requested_boundary_fingerprint"), str)
        else None,
    }


def _invalid_approval_summary(
    *,
    approval_path: Path,
    required_scopes: Sequence[str],
    requested_fingerprint: str,
    status: str,
    reason: str,
) -> dict[str, JsonValue]:
    return {
        "valid": False,
        "status": status,
        "approval_path": _path_safe_path_text(approval_path),
        "required_scopes": list(required_scopes),
        "missing_scopes": list(required_scopes),
        "unapproved_scopes": list(required_scopes),
        "outside_approved_bounds": [],
        "approved_boundary_fingerprint": None,
        "requested_boundary_fingerprint": requested_fingerprint,
        "reason": reason,
    }


def _is_expired(value: object, *, now: datetime) -> bool:
    if value is None:
        return False
    if not isinstance(value, str) or not value:
        return True
    try:
        expires_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return True
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    current = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    return expires_at <= current


def _text_list(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [item for item in value if isinstance(item, str)]


def _required_text(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise TelegramDeliveryInputError(f"{label} must be a non-empty string")
    return value


def _required_mapping(value: object, *, label: str) -> Mapping[str, JsonValue]:
    if not isinstance(value, Mapping):
        raise TelegramDeliveryInputError(f"{label} must be a JSON object")
    return value


def _target_alias_is_safe(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value))


def _chat_id_env_var(target_alias: str) -> str:
    if target_alias == "default":
        return DEFAULT_TELEGRAM_CHAT_ID_ENV_VAR
    return "TELEGRAM_CHAT_ID_" + re.sub(r"[^A-Za-z0-9]", "_", target_alias).upper()


def _idempotency_key(
    *,
    run_id: str,
    telegram_alert_artifact_id: str,
    message_fingerprint: str,
    target_alias: str,
) -> str:
    identity = {
        "run_id": run_id,
        "telegram_alert_artifact_id": telegram_alert_artifact_id,
        "message_fingerprint": message_fingerprint,
        "target_alias": target_alias,
    }
    canonical = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "telegram_delivery_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _current_time(now: Callable[[], datetime] | None) -> datetime:
    current = now() if now is not None else datetime.now(UTC)
    return current if current.tzinfo is not None else current.replace(tzinfo=UTC)


def _read_json_object(path: Path, *, label: str) -> dict[str, JsonValue]:
    if not path.is_file():
        raise TelegramDeliveryInputError(f"{label} file not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TelegramDeliveryInputError(f"invalid JSON input: {path}: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise TelegramDeliveryInputError(f"{label} input must be a JSON object: {path}")
    return payload


def _write_json(path: Path, payload: Mapping[str, JsonValue]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _path_safe_path_text(path: str | Path) -> str:
    path_value = Path(path)
    if not path_value.is_absolute():
        return str(path_value)
    try:
        return os.path.relpath(path_value.resolve(), Path.cwd().resolve())
    except ValueError:
        return path_value.name
