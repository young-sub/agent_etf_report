from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from agent_treport.signal_report.approval import (
    APPROVAL_PROFILE_SCHEMA_VERSION,
    boundary_fingerprint,
    build_approval_template,
    build_daily_approval_boundary,
    evaluate_approval_profile,
)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _approved_profile(boundary: dict[str, object]) -> dict[str, object]:
    approval = build_approval_template(boundary=boundary)
    approval["status"] = "approved"
    return approval


def test_daily_approval_rejects_tampered_boundary_fingerprint(tmp_path: Path) -> None:
    boundary = build_daily_approval_boundary(
        required_scopes=("live_external_evidence",),
        external_evidence_provider_ids=("finnhub",),
        approved_max_target_count=2,
        data_classes=("external_evidence_target_identifiers",),
    )
    approval = build_approval_template(boundary=boundary)
    approval.update(
        {
            "schema_version": APPROVAL_PROFILE_SCHEMA_VERSION,
            "status": "approved",
            "boundary_fingerprint": "0" * 64,
        }
    )
    approval_path = tmp_path / "approval.json"
    _write_json(approval_path, approval)

    summary = evaluate_approval_profile(
        approval_path=approval_path,
        boundary=boundary,
        now=datetime(2026, 5, 17, tzinfo=UTC),
    )

    assert summary["valid"] is False
    assert summary["status"] == "outside_approved_bounds"
    assert summary["outside_approved_bounds"] == ["boundary_fingerprint"]


def test_daily_approval_rejects_pending_revoked_and_expired_profiles(
    tmp_path: Path,
) -> None:
    boundary = build_daily_approval_boundary(
        required_scopes=("model_export",),
        model_exports=(
            {
                "provider": "codex",
                "model": "gpt-test",
                "export_scope": "path_safe_pre_publish_report_context_for_commentary_generation",
            },
        ),
        data_classes=("model_commentary_prompt_context",),
    )
    now = datetime(2026, 5, 17, tzinfo=UTC)

    for status, expires_at, expected in (
        ("pending", None, "pending"),
        ("revoked", None, "revoked"),
        ("approved", "2026-05-16T00:00:00+00:00", "expired"),
    ):
        approval = build_approval_template(boundary=boundary)
        approval["status"] = status
        approval["expires_at"] = expires_at
        approval_path = tmp_path / f"{expected}.json"
        _write_json(approval_path, approval)

        summary = evaluate_approval_profile(
            approval_path=approval_path,
            boundary=boundary,
            now=now,
        )

        assert summary["valid"] is False
        assert summary["status"] == expected


def test_daily_approval_allows_narrower_provider_subset_and_target_count(
    tmp_path: Path,
) -> None:
    approved_boundary = build_daily_approval_boundary(
        required_scopes=("live_external_evidence",),
        external_evidence_provider_ids=("finnhub", "dart", "naver"),
        approved_max_target_count=5,
        data_classes=("external_evidence_target_identifiers",),
    )
    requested_boundary = build_daily_approval_boundary(
        required_scopes=("live_external_evidence",),
        external_evidence_provider_ids=("finnhub",),
        approved_max_target_count=2,
        data_classes=("external_evidence_target_identifiers",),
    )
    approval_path = tmp_path / "approval.json"
    _write_json(approval_path, _approved_profile(approved_boundary))

    summary = evaluate_approval_profile(
        approval_path=approval_path,
        boundary=requested_boundary,
        now=datetime(2026, 5, 17, tzinfo=UTC),
    )

    assert summary["valid"] is True
    assert summary["status"] == "approved"
    assert summary["approved_boundary_fingerprint"] == boundary_fingerprint(
        approved_boundary
    )
    assert summary["requested_boundary_fingerprint"] == boundary_fingerprint(
        requested_boundary
    )


def test_daily_approval_allows_narrower_model_export_with_disclosed_exception(
    tmp_path: Path,
) -> None:
    known_sec_exception = {
        "provider_id": "sec_edgar",
        "exception_type": "known_unvalidated_provider_exception",
        "execution_status": "not_requested",
        "required_for_user_ready_closure": False,
    }
    approved_boundary = build_daily_approval_boundary(
        required_scopes=("live_external_evidence", "model_export"),
        external_evidence_provider_ids=("finnhub",),
        known_unvalidated_provider_exceptions=(known_sec_exception,),
        model_exports=(
            {
                "provider": "codex",
                "model": "default",
                "export_scope": "path_safe_pre_publish_report_context_for_commentary_generation",
            },
        ),
        approved_max_target_count=25,
        data_classes=(
            "external_evidence_target_identifiers",
            "model_commentary_prompt_context",
        ),
    )
    requested_boundary = build_daily_approval_boundary(
        required_scopes=("model_export",),
        model_exports=approved_boundary["model_exports"],
        approved_max_target_count=0,
        data_classes=("model_commentary_prompt_context",),
    )
    approval_path = tmp_path / "approval.json"
    _write_json(approval_path, _approved_profile(approved_boundary))

    summary = evaluate_approval_profile(
        approval_path=approval_path,
        boundary=requested_boundary,
        now=datetime(2026, 5, 19, tzinfo=UTC),
    )

    assert summary["valid"] is True
    assert summary["status"] == "approved"


def test_daily_approval_blocks_provider_target_and_model_expansion(
    tmp_path: Path,
) -> None:
    approved_boundary = build_daily_approval_boundary(
        required_scopes=("live_external_evidence", "model_export"),
        external_evidence_provider_ids=("finnhub",),
        model_exports=(
            {
                "provider": "codex",
                "model": "gpt-test",
                "export_scope": "path_safe_pre_publish_report_context_for_commentary_generation",
            },
        ),
        approved_max_target_count=2,
        data_classes=(
            "external_evidence_target_identifiers",
            "model_commentary_prompt_context",
        ),
    )
    approval_path = tmp_path / "approval.json"
    _write_json(approval_path, _approved_profile(approved_boundary))
    cases = (
        (
            "provider expansion",
            build_daily_approval_boundary(
                required_scopes=("live_external_evidence", "model_export"),
                external_evidence_provider_ids=("finnhub", "naver"),
                model_exports=approved_boundary["model_exports"],
                approved_max_target_count=2,
                data_classes=approved_boundary["data_classes"],
            ),
            "external_evidence_provider_ids",
        ),
        (
            "target expansion",
            build_daily_approval_boundary(
                required_scopes=("live_external_evidence", "model_export"),
                external_evidence_provider_ids=("finnhub",),
                model_exports=approved_boundary["model_exports"],
                approved_max_target_count=3,
                data_classes=approved_boundary["data_classes"],
            ),
            "approved_max_target_count",
        ),
        (
            "model expansion",
            build_daily_approval_boundary(
                required_scopes=("live_external_evidence", "model_export"),
                external_evidence_provider_ids=("finnhub",),
                model_exports=(
                    {
                        "provider": "codex",
                        "model": "gpt-other",
                        "export_scope": (
                            "path_safe_pre_publish_report_context_for_commentary_generation"
                        ),
                    },
                ),
                approved_max_target_count=2,
                data_classes=approved_boundary["data_classes"],
            ),
            "model_exports",
        ),
    )

    for label, boundary, expected_mismatch in cases:
        summary = evaluate_approval_profile(
            approval_path=approval_path,
            boundary=boundary,
            now=datetime(2026, 5, 17, tzinfo=UTC),
        )

        assert summary["valid"] is False, label
        assert summary["status"] == "outside_approved_bounds", label
        assert expected_mismatch in summary["outside_approved_bounds"]
