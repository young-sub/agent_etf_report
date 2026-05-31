from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from agent_pack.models import JsonValue

DAILY_PUBLISH_CLOSURE_SCHEMA_VERSION = "agent_treport.daily_publish_closure.v1"
OPERATOR_APPROVED_DAILY_PUBLISH_FLOW_SCHEMA_VERSION = (
    "agent_treport.operator_approved_daily_publish_flow.v1"
)
DAILY_PUBLISH_CLOSURE_FILENAME = "daily_publish_closure.json"
PRE_PUBLISH_HANDOFF_FILENAME = "pre_publish_handoff.json"
TELEGRAM_DELIVERY_SUMMARY_FILENAME = "telegram_delivery_summary.json"
TELEGRAM_DELIVERY_RECEIPT_DIRNAME = "telegram_delivery_receipts"
VALIDATION_COMMAND_RESULTS_FILENAME = "validation_command_results.json"
DEFAULT_OPERATOR_APPROVED_DAILY_PUBLISH_FLOW_PATH = Path(
    "data/agent_treport/approvals/operator_approved_daily_publish_flow.json"
)

_CHECK_NAMES = (
    "pre_publish_user_ready",
    "live_sent_receipt",
    "duplicate_blocked",
    "identity_consistency",
    "validation_passed",
    "operator_approved_daily_publish_flow",
)
_SENSITIVE_TEXT_PATTERNS = (
    re.compile(r"[A-Za-z]:[\\/]"),
    re.compile(r"\\\\[^\\/]+[\\/]"),
    re.compile(r"file://", re.IGNORECASE),
    re.compile(r"https?://", re.IGNORECASE),
    re.compile(r"api\.telegram\.org", re.IGNORECASE),
    re.compile(r"(^|[\\/])\.env($|[\\/])", re.IGNORECASE),
    re.compile(r"\braw_chat_id\b", re.IGNORECASE),
    re.compile(r"\braw_request\b", re.IGNORECASE),
    re.compile(r"\braw_response\b", re.IGNORECASE),
    re.compile(r"\braw_api_payload\b", re.IGNORECASE),
    re.compile(r"\bTraceback\b"),
    re.compile(r"File \"[^\"]+\", line \d+"),
    re.compile(r"<\s*/?\s*(b|code|i|a)(\s|>)", re.IGNORECASE),
    re.compile(r"\bTELEGRAM_BOT_TOKEN\s*=", re.IGNORECASE),
    re.compile(r"\bTELEGRAM_CHAT_ID(?:_[A-Z0-9_]+)?\s*=", re.IGNORECASE),
)


class DailyPublishClosureInputError(RuntimeError):
    """Raised when the supplied result package cannot be verified or written."""


@dataclass(frozen=True)
class _HandoffIdentity:
    run_id: str
    telegram_alert_artifact_id: str
    message_fingerprint: str
    message_length: int


@dataclass(frozen=True)
class _ReceiptEvidence:
    path: Path
    relative_path: str
    payload: Mapping[str, JsonValue]
    matches_handoff: bool

    @property
    def delivery_status(self) -> str:
        status = self.payload.get("delivery_status")
        return status if isinstance(status, str) else "unknown"

    @property
    def attempt_count(self) -> int:
        value = self.payload.get("attempt_count")
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        return 0

    @property
    def target_alias(self) -> str | None:
        value = self.payload.get("target_alias")
        return value if isinstance(value, str) and value else None


def verify_daily_publish_closure(
    *,
    package_path: Path,
    operator_approval_path: Path = DEFAULT_OPERATOR_APPROVED_DAILY_PUBLISH_FLOW_PATH,
    now: Callable[[], datetime] | None = None,
) -> dict[str, JsonValue]:
    package_path = Path(package_path)
    if not package_path.is_dir():
        raise DailyPublishClosureInputError(
            f"result package directory not found: {package_path}"
        )
    output_path = package_path / DAILY_PUBLISH_CLOSURE_FILENAME
    generated_at = _current_time(now).isoformat()

    handoff_path = package_path / PRE_PUBLISH_HANDOFF_FILENAME
    if not handoff_path.is_file():
        payload = _build_payload(
            generated_at=generated_at,
            closure_status="missing_handoff",
            run_id=None,
            target_alias=None,
            telegram_alert_artifact_id=None,
            message_fingerprint=None,
            evidence_checks={
                **_initial_evidence_checks(),
                "pre_publish_user_ready": "not_available",
            },
            receipt_summary=_empty_receipt_summary(),
            validation_summary={
                "validation_status": "not_available",
                "commands": [],
                "commands_omitted": False,
                "missing_validation_artifact": True,
            },
            warnings=[],
            limitations=["pre_publish_handoff_missing"],
            source_files=[],
        )
        _write_json(output_path, payload)
        return payload

    handoff = _read_json_object(handoff_path, label="pre-publish handoff")
    identity = _handoff_identity(handoff)
    receipts = _load_receipts(package_path=package_path, identity=identity)
    summary_path = package_path / TELEGRAM_DELIVERY_SUMMARY_FILENAME
    summary = _read_optional_json_object(summary_path)
    validation_path = package_path / VALIDATION_COMMAND_RESULTS_FILENAME
    validation_summary, validation_passed, validation_limitations = _validation_summary(
        validation_path
    )
    operator_check, operator_warning, operator_source = _operator_approval_check(
        operator_approval_path
    )
    pre_publish_passed, pre_publish_limitations = _pre_publish_user_ready(handoff)
    receipt_summary, receipt_facts = _receipt_summary(
        identity=identity,
        receipts=receipts,
        summary=summary,
    )

    warnings: list[str] = []
    limitations: list[str] = []
    limitations.extend(pre_publish_limitations)
    limitations.extend(validation_limitations)
    if operator_warning is not None:
        warnings.append(operator_warning)
    if receipt_facts["mismatched_receipt_count"] > 0:
        warnings.append("stale_or_mismatched_receipts_present")
    summary_conflict = receipt_summary.get("summary_conflict_warning")
    if isinstance(summary_conflict, str):
        warnings.append(summary_conflict)

    matching_delivery_evidence_exists = bool(
        receipt_facts["matching_sent_count"]
        or receipt_facts["matching_duplicate_count"]
        or receipt_facts["matching_failed_count"]
    )
    if not pre_publish_passed and matching_delivery_evidence_exists:
        warnings.append("handoff_has_delivery_evidence_but_is_not_user_ready")

    closure_status = _closure_status(
        pre_publish_passed=pre_publish_passed,
        validation_passed=validation_passed,
        receipt_facts=receipt_facts,
    )
    if (
        receipt_facts["matching_sent_count"]
        and not receipt_facts["matching_live_sent_count"]
    ):
        limitations.append("live_delivery_evidence_missing")

    target_alias = _closure_target_alias(
        receipts=receipts,
        summary=summary,
        identity=identity,
    )
    evidence_checks = {
        "pre_publish_user_ready": "passed" if pre_publish_passed else "failed",
        "live_sent_receipt": "passed"
        if receipt_facts["matching_live_sent_count"]
        else "failed",
        "duplicate_blocked": "passed"
        if receipt_facts["matching_duplicate_count"]
        else "failed",
        "identity_consistency": "passed"
        if receipt_facts["identity_consistent"]
        else "failed",
        "validation_passed": "passed" if validation_passed else "failed",
        "operator_approved_daily_publish_flow": operator_check,
    }
    source_files = _source_files(
        package_path=package_path,
        handoff_path=handoff_path,
        validation_path=validation_path,
        summary_path=summary_path,
        receipts=receipts,
        operator_source=operator_source,
    )
    payload = _build_payload(
        generated_at=generated_at,
        closure_status=closure_status,
        run_id=identity.run_id,
        target_alias=target_alias,
        telegram_alert_artifact_id=identity.telegram_alert_artifact_id,
        message_fingerprint=identity.message_fingerprint,
        evidence_checks=evidence_checks,
        receipt_summary=receipt_summary,
        validation_summary=validation_summary,
        warnings=_unique_text(warnings),
        limitations=_unique_text(limitations),
        source_files=source_files,
    )
    _write_json(output_path, payload)
    return payload


def _build_payload(
    *,
    generated_at: str,
    closure_status: str,
    run_id: str | None,
    target_alias: str | None,
    telegram_alert_artifact_id: str | None,
    message_fingerprint: str | None,
    evidence_checks: Mapping[str, JsonValue],
    receipt_summary: Mapping[str, JsonValue],
    validation_summary: Mapping[str, JsonValue],
    warnings: Sequence[str],
    limitations: Sequence[str],
    source_files: Sequence[str],
) -> dict[str, JsonValue]:
    return {
        "schema_version": DAILY_PUBLISH_CLOSURE_SCHEMA_VERSION,
        "generated_at": generated_at,
        "closure_status": closure_status,
        "closure_met": closure_status == "closure_met",
        "run_id": run_id,
        "target_alias": target_alias,
        "telegram_alert_artifact_id": telegram_alert_artifact_id,
        "message_fingerprint": message_fingerprint,
        "evidence_checks": dict(evidence_checks),
        "receipt_summary": dict(receipt_summary),
        "validation_summary": dict(validation_summary),
        "warnings": list(warnings),
        "limitations": list(limitations),
        "source_files": list(source_files),
    }


def _closure_status(
    *,
    pre_publish_passed: bool,
    validation_passed: bool,
    receipt_facts: Mapping[str, int | bool],
) -> str:
    if not pre_publish_passed:
        return "pre_publish_not_user_ready"
    if not validation_passed:
        return "validation_not_passed"
    if receipt_facts["total_receipt_count"] and not receipt_facts["matching_receipt_count"]:
        return "stale_receipt"
    if receipt_facts["matching_duplicate_count"] and not receipt_facts["matching_sent_count"]:
        return "duplicate_only_without_sent"
    if receipt_facts["matching_failed_count"] and not receipt_facts["matching_sent_count"]:
        return "failed_delivery"
    if not receipt_facts["matching_sent_count"]:
        return "not_sent"
    if not receipt_facts["matching_live_sent_count"]:
        return "not_sent"
    if not receipt_facts["matching_duplicate_count"]:
        return "sent_without_duplicate_proof"
    return "closure_met"


def _initial_evidence_checks() -> dict[str, JsonValue]:
    return {name: "not_available" for name in _CHECK_NAMES}


def _empty_receipt_summary() -> dict[str, JsonValue]:
    return {
        "matching_sent_receipt_count": 0,
        "matching_duplicate_blocked_receipt_count": 0,
        "matching_failed_receipt_count": 0,
        "mismatched_stale_receipt_count": 0,
        "recovered_failed_attempt_count": 0,
        "selected_sent_receipt_path": None,
        "selected_duplicate_blocked_receipt_path": None,
        "summary_conflict_warning": None,
    }


def _receipt_summary(
    *,
    identity: _HandoffIdentity,
    receipts: Sequence[_ReceiptEvidence],
    summary: Mapping[str, JsonValue] | None,
) -> tuple[dict[str, JsonValue], dict[str, int | bool]]:
    closure_target_alias = _closure_target_alias(
        receipts=receipts,
        summary=summary,
        identity=identity,
    )
    matching = [
        receipt
        for receipt in receipts
        if receipt.matches_handoff and receipt.target_alias == closure_target_alias
    ]
    matching_sent = _receipts_by_status(matching, "sent")
    matching_duplicate = _receipts_by_status(matching, "duplicate_blocked")
    matching_failed = _receipts_by_status(matching, "failed")
    mismatched_count = len(receipts) - len(matching)
    selected_sent = matching_sent[-1] if matching_sent else None
    selected_duplicate = matching_duplicate[-1] if matching_duplicate else None
    summary_matches = _summary_matches_identity(summary, identity=identity)
    summary_live = summary_matches and summary is not None and summary.get("live") is True
    matching_live_sent_count = len(matching_sent) if summary_live else 0
    conflict_warning = _summary_conflict_warning(
        summary=summary,
        summary_matches=summary_matches,
        matching_receipts=matching,
    )
    receipt_summary = _empty_receipt_summary()
    receipt_summary.update(
        {
            "matching_sent_receipt_count": len(matching_sent),
            "matching_duplicate_blocked_receipt_count": len(matching_duplicate),
            "matching_failed_receipt_count": len(matching_failed),
            "mismatched_stale_receipt_count": mismatched_count,
            "recovered_failed_attempt_count": len(matching_failed)
            if matching_sent and matching_duplicate
            else 0,
            "selected_sent_receipt_path": selected_sent.relative_path
            if selected_sent is not None
            else None,
            "selected_duplicate_blocked_receipt_path": selected_duplicate.relative_path
            if selected_duplicate is not None
            else None,
            "summary_conflict_warning": conflict_warning,
        }
    )
    facts: dict[str, int | bool] = {
        "total_receipt_count": len(receipts),
        "matching_receipt_count": len(matching),
        "matching_sent_count": len(matching_sent),
        "matching_live_sent_count": matching_live_sent_count,
        "matching_duplicate_count": len(matching_duplicate),
        "matching_failed_count": len(matching_failed),
        "mismatched_receipt_count": mismatched_count,
        "identity_consistent": selected_sent is not None
        and selected_duplicate is not None,
    }
    return receipt_summary, facts


def _receipts_by_status(
    receipts: Sequence[_ReceiptEvidence],
    status: str,
) -> list[_ReceiptEvidence]:
    return sorted(
        [receipt for receipt in receipts if receipt.delivery_status == status],
        key=lambda receipt: (receipt.attempt_count, receipt.relative_path),
    )


def _summary_conflict_warning(
    *,
    summary: Mapping[str, JsonValue] | None,
    summary_matches: bool,
    matching_receipts: Sequence[_ReceiptEvidence],
) -> str | None:
    if summary is None or not matching_receipts:
        return None
    if not summary_matches:
        return "delivery_summary_conflicts_with_matching_receipts"
    latest_status = summary.get("latest_delivery_status")
    if not isinstance(latest_status, str):
        return "delivery_summary_conflicts_with_matching_receipts"
    matching_statuses = {receipt.delivery_status for receipt in matching_receipts}
    if latest_status not in matching_statuses:
        return "delivery_summary_conflicts_with_matching_receipts"
    return None


def _load_receipts(
    *,
    package_path: Path,
    identity: _HandoffIdentity,
) -> list[_ReceiptEvidence]:
    receipt_dir = package_path / TELEGRAM_DELIVERY_RECEIPT_DIRNAME
    if not receipt_dir.is_dir():
        return []
    receipts: list[_ReceiptEvidence] = []
    for path in sorted(receipt_dir.glob("*.json")):
        payload = _read_optional_json_object(path)
        if payload is None:
            continue
        receipts.append(
            _ReceiptEvidence(
                path=path,
                relative_path=_package_relative_path(path, package_path),
                payload=payload,
                matches_handoff=_receipt_matches_identity(payload, identity=identity),
            )
        )
    return receipts


def _receipt_matches_identity(
    receipt: Mapping[str, JsonValue],
    *,
    identity: _HandoffIdentity,
) -> bool:
    target_alias = receipt.get("target_alias")
    if not isinstance(target_alias, str) or not target_alias:
        return False
    if receipt.get("run_id") != identity.run_id:
        return False
    if receipt.get("telegram_alert_artifact_id") != identity.telegram_alert_artifact_id:
        return False
    if receipt.get("message_fingerprint") != identity.message_fingerprint:
        return False
    idempotency_key = receipt.get("idempotency_key")
    if isinstance(idempotency_key, str) and idempotency_key:
        return (
            idempotency_key
            == _idempotency_key(
                run_id=identity.run_id,
                telegram_alert_artifact_id=identity.telegram_alert_artifact_id,
                message_fingerprint=identity.message_fingerprint,
                target_alias=target_alias,
            )
        )
    return True


def _summary_matches_identity(
    summary: Mapping[str, JsonValue] | None,
    *,
    identity: _HandoffIdentity,
) -> bool:
    if summary is None:
        return False
    return _receipt_matches_identity(summary, identity=identity)


def _closure_target_alias(
    *,
    receipts: Sequence[_ReceiptEvidence],
    summary: Mapping[str, JsonValue] | None,
    identity: _HandoffIdentity,
) -> str | None:
    if _summary_matches_identity(summary, identity=identity):
        assert summary is not None
        target_alias = summary.get("target_alias")
        if isinstance(target_alias, str) and target_alias:
            return target_alias
    for status in ("sent", "duplicate_blocked", "failed"):
        matching = _receipts_by_status(
            [receipt for receipt in receipts if receipt.matches_handoff],
            status,
        )
        if matching and matching[-1].target_alias is not None:
            return matching[-1].target_alias
    if summary is not None:
        target_alias = summary.get("target_alias")
        if isinstance(target_alias, str) and target_alias:
            return target_alias
    return None


def _pre_publish_user_ready(
    handoff: Mapping[str, JsonValue],
) -> tuple[bool, list[str]]:
    limitations: list[str] = []
    if handoff.get("status") != "user_ready":
        limitations.append(f"status={handoff.get('status')}")
    if handoff.get("delivery_blocked") is not False:
        limitations.append("delivery_blocked=true")
    closure = handoff.get("closure")
    closure_mapping = closure if isinstance(closure, Mapping) else {}
    user_ready = closure_mapping.get("full_user_ready_closure")
    user_ready_mapping = user_ready if isinstance(user_ready, Mapping) else {}
    if user_ready_mapping.get("status") != "met":
        limitations.append(
            "full_user_ready_closure.status="
            f"{user_ready_mapping.get('status')}"
        )
    return not limitations, limitations


def _validation_summary(
    validation_path: Path,
) -> tuple[dict[str, JsonValue], bool, list[str]]:
    if not validation_path.is_file():
        return (
            {
                "validation_status": "missing",
                "commands": [],
                "commands_omitted": False,
                "missing_validation_artifact": True,
            },
            False,
            ["validation_command_results_missing"],
        )
    payload = _read_json_object(validation_path, label="validation command results")
    raw_status = payload.get("status")
    status = raw_status if isinstance(raw_status, str) and raw_status else "invalid"
    commands_value = payload.get("commands")
    commands = commands_value if isinstance(commands_value, list) else []
    safe_commands: list[dict[str, JsonValue]] = []
    commands_unsafe = False
    for command in commands:
        if not isinstance(command, Mapping):
            commands_unsafe = True
            continue
        safe_command = _safe_validation_command(command)
        if safe_command is None:
            commands_unsafe = True
            continue
        safe_commands.append(safe_command)
    if commands_unsafe:
        return (
            {
                "validation_status": "unsafe",
                "commands": [],
                "commands_omitted": True,
                "missing_validation_artifact": False,
            },
            False,
            ["unsafe_validation_command_results_redacted"],
        )
    return (
        {
            "validation_status": status,
            "commands": safe_commands,
            "commands_omitted": False,
            "missing_validation_artifact": False,
        },
        status == "passed",
        [] if status == "passed" else [f"validation_status={status}"],
    )


def _safe_validation_command(
    command: Mapping[str, JsonValue],
) -> dict[str, JsonValue] | None:
    safe: dict[str, JsonValue] = {}
    for key in ("command", "result", "summary"):
        value = command.get(key)
        if value is None:
            continue
        if not isinstance(value, str) or not _text_is_safe(value):
            return None
        safe[key] = value
    return safe


def _operator_approval_check(
    approval_path: Path,
) -> tuple[str, str | None, str | None]:
    if not approval_path.is_file():
        return "not_available", "operator_approved_daily_publish_flow_not_approved", None
    payload = _read_optional_json_object(approval_path)
    source = _repo_relative_path(approval_path)
    if payload is None:
        return "failed", "operator_approved_daily_publish_flow_not_approved", source
    if (
        payload.get("schema_version")
        == OPERATOR_APPROVED_DAILY_PUBLISH_FLOW_SCHEMA_VERSION
        and payload.get("status") == "approved"
    ):
        return "passed", None, source
    return "failed", "operator_approved_daily_publish_flow_not_approved", source


def _source_files(
    *,
    package_path: Path,
    handoff_path: Path,
    validation_path: Path,
    summary_path: Path,
    receipts: Sequence[_ReceiptEvidence],
    operator_source: str | None,
) -> list[str]:
    source_files = [_package_relative_path(handoff_path, package_path)]
    if validation_path.is_file():
        source_files.append(_package_relative_path(validation_path, package_path))
    if summary_path.is_file():
        source_files.append(_package_relative_path(summary_path, package_path))
    source_files.extend(receipt.relative_path for receipt in receipts)
    if operator_source is not None:
        source_files.append(operator_source)
    return _unique_text(source_files)


def _handoff_identity(handoff: Mapping[str, JsonValue]) -> _HandoffIdentity:
    run_id = _required_text(handoff.get("run_id"), label="handoff run_id")
    preview = _required_mapping(handoff.get("preview"), label="handoff preview")
    message = _required_mapping(
        preview.get("telegram_message"),
        label="handoff preview.telegram_message",
    )
    artifact_id = _required_text(
        message.get("artifact_id"),
        label="handoff telegram alert artifact id",
    )
    message_text = _required_text(
        message.get("text"),
        label="handoff telegram message text",
    )
    return _HandoffIdentity(
        run_id=run_id,
        telegram_alert_artifact_id=artifact_id,
        message_fingerprint=_sha256_text(message_text),
        message_length=len(message_text),
    )


def _required_text(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise DailyPublishClosureInputError(f"{label} must be a non-empty string")
    return value


def _required_mapping(value: object, *, label: str) -> Mapping[str, JsonValue]:
    if not isinstance(value, Mapping):
        raise DailyPublishClosureInputError(f"{label} must be a JSON object")
    return value


def _read_json_object(path: Path, *, label: str) -> dict[str, JsonValue]:
    if not path.is_file():
        raise DailyPublishClosureInputError(f"{label} file not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DailyPublishClosureInputError(
            f"invalid JSON input: {path}: {exc.msg}"
        ) from exc
    if not isinstance(payload, dict):
        raise DailyPublishClosureInputError(f"{label} input must be a JSON object: {path}")
    return payload


def _read_optional_json_object(path: Path) -> dict[str, JsonValue] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _write_json(path: Path, payload: Mapping[str, JsonValue]) -> None:
    try:
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    except OSError as exc:
        raise DailyPublishClosureInputError(
            f"could not write daily publish closure artifact: {path}"
        ) from exc


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


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _text_is_safe(value: str) -> bool:
    return not any(pattern.search(value) for pattern in _SENSITIVE_TEXT_PATTERNS)


def _package_relative_path(path: Path, package_path: Path) -> str:
    try:
        return str(path.resolve().relative_to(package_path.resolve()))
    except ValueError:
        return _repo_relative_path(path)


def _repo_relative_path(path: Path) -> str:
    path_value = Path(path)
    if not path_value.is_absolute():
        return str(path_value)
    try:
        return os.path.relpath(path_value.resolve(), Path.cwd().resolve())
    except ValueError:
        return path_value.name


def _unique_text(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _current_time(now: Callable[[], datetime] | None) -> datetime:
    current = now() if now is not None else datetime.now(UTC)
    return current if current.tzinfo is not None else current.replace(tzinfo=UTC)
