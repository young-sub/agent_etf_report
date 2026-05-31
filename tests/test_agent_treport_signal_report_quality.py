from __future__ import annotations

import pytest

from agent_treport.signal_report import (
    HTMLResearchReportRenderer,
    MarkdownSignalReportRenderer,
    ReportQualityContract,
    ReportQualityGate,
    TelegramSignalAlertRenderer,
    build_signal_report_payload,
    load_fixture_signal_report_inputs,
)
from agent_treport.signal_report.domain.payload import SignalReportPayload
from agent_treport.signal_report.domain.snapshots import MultiETFHoldingsSnapshots


def test_default_fixture_markdown_passes_without_target_coverage_warnings() -> None:
    payload = _fixture_payload()
    markdown = MarkdownSignalReportRenderer().render(payload=payload)

    result = ReportQualityGate().evaluate(payload=payload, markdown=markdown)

    assert result.status == "passed"
    assert result.summary["blocking"] is False
    assert result.summary["error_count"] == 0
    assert result.summary["warning_count"] == 0
    assert "markdown_target_section_not_rendered" not in {
        violation.code for violation in result.violations
    }


def test_html_quality_scope_detects_missing_required_section() -> None:
    payload = _fixture_payload()
    markdown = MarkdownSignalReportRenderer().render(payload=payload)
    html = HTMLResearchReportRenderer().render(payload=payload).replace(
        'id="evidence-ledger"',
        'id="evidence-notes"',
    )

    result = ReportQualityGate().evaluate(payload=payload, markdown=markdown, html=html)

    violation = _single_violation(result, "missing_html_section")
    assert result.status == "failed"
    assert set(result.summary["scopes"]) == {"payload", "markdown", "html", "telegram_alert"}
    assert result.summary["scopes"]["html"] >= 1
    assert violation.scope == "html"
    assert violation.severity == "error"
    assert violation.location == "html.sections.evidence-ledger"
    assert violation.details == {"required_section_id": "evidence-ledger"}


def test_html_quality_scope_detects_raw_claim_scope_and_prohibited_language() -> None:
    payload = _fixture_payload()
    markdown = MarkdownSignalReportRenderer().render(payload=payload)
    html = HTMLResearchReportRenderer().render(payload=payload)
    exposed_html = f"{html}\n{payload.signal_board[0].claim_scope}\nBUY rating"

    result = ReportQualityGate().evaluate(
        payload=payload,
        markdown=markdown,
        html=exposed_html,
    )

    raw_claim_scope = _single_violation(result, "raw_claim_scope_exposed")
    prohibited_language = _single_violation(result, "prohibited_investment_language")
    assert raw_claim_scope.scope == "html"
    assert raw_claim_scope.location == "html.claim_scope"
    assert raw_claim_scope.details == {"claim_scope_count": 1}
    assert prohibited_language.scope == "html"
    assert prohibited_language.location == "html.text"


def test_html_quality_scope_checks_target_values_and_signal_board_values() -> None:
    payload = _fixture_payload()
    markdown = MarkdownSignalReportRenderer().render(payload=payload)
    html = (
        HTMLResearchReportRenderer()
        .render(payload=payload)
        .replace(">AI infrastructure</td>", ">AI hidden</td>")
        .replace("etf_focus_ai", "etf_hidden")
        .replace(
            ">NVIDIA data-center revenue beat supports AI infrastructure demand</td>",
            ">hidden evidence</td>",
        )
        .replace(">NVDA</a>", ">hidden</a>", 1)
        .replace(">focus<br>", ">hidden<br>", 1)
        .replace(">Confirmed<br>", ">hidden<br>", 1)
    )

    result = ReportQualityGate().evaluate(payload=payload, markdown=markdown, html=html)

    target_violations = [
        violation
        for violation in result.violations
        if violation.code == "html_target_section_value_missing"
    ]
    signal_violation = _single_violation(result, "html_signal_value_missing")
    assert result.status == "failed"
    assert {violation.scope for violation in target_violations} == {"html"}
    assert {violation.details["target_section"] for violation in target_violations} == {
        "market_map",
        "etf_follow_sheets",
        "evidence_ledger",
    }
    assert signal_violation.scope == "html"
    assert signal_violation.location == "html.signal_board.rank_1"
    assert signal_violation.details == {
        "ticker": "NVDA",
        "rank": 1,
        "missing": ("ticker", "review_label", "evidence_grade"),
    }


def test_html_quality_scope_accepts_escaped_target_values() -> None:
    payload_data = _fixture_payload().model_dump(mode="json")
    payload_data["etf_follow_sheets"][0]["etf_name"] = "AI R&D Active ETF"
    payload = SignalReportPayload.model_validate(payload_data)
    markdown = MarkdownSignalReportRenderer().render(payload=payload)
    html = HTMLResearchReportRenderer().render(payload=payload)

    result = ReportQualityGate().evaluate(payload=payload, markdown=markdown, html=html)

    assert result.status == "passed"
    assert "AI R&amp;D Active ETF" in html
    assert not any(
        violation.code == "html_target_section_value_missing"
        for violation in result.violations
    )


def test_html_quality_scope_skips_html_checks_when_html_is_none() -> None:
    payload = _fixture_payload()
    markdown = MarkdownSignalReportRenderer().render(payload=payload)

    result = ReportQualityGate().evaluate(payload=payload, markdown=markdown, html=None)

    assert result.status == "passed"
    assert result.summary["scopes"] == {
        "payload": 0,
        "markdown": 0,
        "html": 0,
        "telegram_alert": 0,
    }


def test_telegram_alert_quality_scope_skips_checks_when_alert_is_none() -> None:
    payload = _fixture_payload()
    markdown = MarkdownSignalReportRenderer().render(payload=payload)
    html = HTMLResearchReportRenderer().render(payload=payload)

    result = ReportQualityGate().evaluate(
        payload=payload,
        markdown=markdown,
        html=html,
        telegram_alert=None,
    )

    assert result.status == "passed"
    assert set(result.summary["scopes"]) == {"payload", "markdown", "html", "telegram_alert"}
    assert result.summary["scopes"]["telegram_alert"] == 0


def test_telegram_alert_quality_scope_passes_fixture_alert() -> None:
    payload = _fixture_payload()
    markdown = MarkdownSignalReportRenderer().render(payload=payload)
    html = HTMLResearchReportRenderer().render(payload=payload)
    telegram_alert = _fixture_telegram_alert(payload)

    result = ReportQualityGate().evaluate(
        payload=payload,
        markdown=markdown,
        html=html,
        telegram_alert=telegram_alert,
    )

    assert result.status == "passed"
    assert result.summary["scopes"]["telegram_alert"] == 0


def test_telegram_alert_quality_scope_blocks_more_than_five_signal_rows() -> None:
    payload = _fixture_payload()
    markdown = MarkdownSignalReportRenderer().render(payload=payload)
    telegram_alert = _fixture_telegram_alert(payload) + "\n6. <code>EXTRA</code> | row"

    result = ReportQualityGate().evaluate(
        payload=payload,
        markdown=markdown,
        telegram_alert=telegram_alert,
    )

    violation = _single_violation(result, "telegram_alert_too_many_signals")
    assert result.status == "failed"
    assert violation.scope == "telegram_alert"
    assert violation.location == "telegram_alert.signal_rows"
    assert violation.details == {"signal_row_count": 6, "max_signal_rows": 5}


def test_telegram_alert_quality_scope_blocks_4097_chars() -> None:
    payload = _fixture_payload()
    markdown = MarkdownSignalReportRenderer().render(payload=payload)
    telegram_alert = _fixture_telegram_alert(payload)
    telegram_alert = telegram_alert + ("x" * (4097 - len(telegram_alert)))

    result = ReportQualityGate().evaluate(
        payload=payload,
        markdown=markdown,
        telegram_alert=telegram_alert,
    )

    violation = _single_violation(result, "telegram_alert_too_long")
    assert result.status == "failed"
    assert violation.scope == "telegram_alert"
    assert violation.details == {"length": 4097, "max_length": 4096}


def test_telegram_alert_quality_scope_blocks_missing_data_quality_markers() -> None:
    payload = _fixture_payload()
    markdown = MarkdownSignalReportRenderer().render(payload=payload)
    telegram_alert = _fixture_telegram_alert(payload).replace("상태 limited", "limited")

    result = ReportQualityGate().evaluate(
        payload=payload,
        markdown=markdown,
        telegram_alert=telegram_alert,
    )

    violation = _single_violation(result, "missing_telegram_alert_data_quality")
    assert result.status == "failed"
    assert violation.scope == "telegram_alert"
    assert violation.location == "telegram_alert.data_quality"


def test_telegram_alert_quality_scope_blocks_missing_full_report_reference() -> None:
    payload = _fixture_payload()
    markdown = MarkdownSignalReportRenderer().render(payload=payload)
    telegram_alert = _fixture_telegram_alert(payload).replace(
        "HTML artifact: <code>artifact_treport_html_report</code>",
        "HTML artifact: artifact_treport_report",
    )

    result = ReportQualityGate().evaluate(
        payload=payload,
        markdown=markdown,
        telegram_alert=telegram_alert,
    )

    violation = _single_violation(result, "missing_telegram_alert_full_report_reference")
    assert result.status == "failed"
    assert violation.scope == "telegram_alert"
    assert violation.location == "telegram_alert.full_report_reference"


def test_telegram_alert_quality_scope_blocks_forbidden_fragments() -> None:
    payload = _fixture_payload()
    markdown = MarkdownSignalReportRenderer().render(payload=payload)
    telegram_alert = _fixture_telegram_alert(payload) + '\n<a href="https://example.test">'

    result = ReportQualityGate().evaluate(
        payload=payload,
        markdown=markdown,
        telegram_alert=telegram_alert,
    )

    violation = _single_violation(result, "forbidden_telegram_alert_fragment")
    assert result.status == "failed"
    assert violation.scope == "telegram_alert"
    assert violation.details == {"fragment": "<a "}


def test_telegram_alert_quality_scope_blocks_prohibited_language() -> None:
    payload = _fixture_payload()
    markdown = MarkdownSignalReportRenderer().render(payload=payload)
    telegram_alert = _fixture_telegram_alert(payload) + "\nBUY rating"

    result = ReportQualityGate().evaluate(
        payload=payload,
        markdown=markdown,
        telegram_alert=telegram_alert,
    )

    violation = _single_violation(result, "prohibited_investment_language")
    assert result.status == "failed"
    assert violation.scope == "telegram_alert"
    assert violation.location == "telegram_alert.text"


def test_telegram_alert_quality_scope_reuses_raw_payload_exposure_checks() -> None:
    payload = _fixture_payload()
    markdown = MarkdownSignalReportRenderer().render(payload=payload)
    telegram_alert = (
        f"{_fixture_telegram_alert(payload)}\n"
        f"{payload.signal_board[0].claim_scope}\n"
        "ticker_dossier:security:sec_nvda"
    )

    result = ReportQualityGate().evaluate(
        payload=payload,
        markdown=markdown,
        telegram_alert=telegram_alert,
    )

    raw_claim_scope = _single_violation(result, "raw_claim_scope_exposed")
    raw_used_in = _single_violation(result, "raw_used_in_exposed")
    assert raw_claim_scope.scope == "telegram_alert"
    assert raw_claim_scope.location == "telegram_alert.claim_scope"
    assert raw_used_in.scope == "telegram_alert"
    assert raw_used_in.location == "telegram_alert.used_in"


def test_custom_forbidden_html_fragments_block_report() -> None:
    payload = _fixture_payload()
    markdown = MarkdownSignalReportRenderer().render(payload=payload)
    html = HTMLResearchReportRenderer().render(payload=payload)
    contract = ReportQualityContract.default().model_copy(
        update={"forbidden_html_fragments": ("DO_NOT_RENDER",)}
    )

    result = ReportQualityGate(contract=contract).evaluate(
        payload=payload,
        markdown=markdown,
        html=f"{html}\nDO_NOT_RENDER",
    )

    violation = _single_violation(result, "forbidden_html_fragment")
    assert result.status == "failed"
    assert violation.scope == "html"
    assert violation.location == "html.text"
    assert violation.details == {"fragment": "DO_NOT_RENDER"}


def test_prohibited_investment_language_blocks_without_exposing_match_text() -> None:
    payload = _fixture_payload()
    markdown = MarkdownSignalReportRenderer().render(payload=payload)
    unsafe_text = "BUY rating with a target price of 500."

    result = ReportQualityGate().evaluate(
        payload=payload,
        markdown=f"{markdown}\n\n{unsafe_text}",
    )

    violation = _single_violation(result, "prohibited_investment_language")
    assert result.status == "failed"
    assert violation.severity == "error"
    assert violation.scope == "markdown"
    assert violation.location == "markdown.text"
    assert violation.details == {
        "categories": ("buy_hold_sell", "price_target"),
    }
    assert unsafe_text not in result.model_dump_json()
    assert "BUY" not in result.model_dump_json()
    assert "target price" not in result.model_dump_json()


def test_missing_markdown_section_returns_release_blocking_violation() -> None:
    payload = _fixture_payload()
    markdown = MarkdownSignalReportRenderer().render(payload=payload).replace(
        "## Data Quality",
        "## Data Notes",
    )

    result = ReportQualityGate().evaluate(payload=payload, markdown=markdown)

    violation = _single_violation(result, "missing_markdown_section")
    assert result.status == "failed"
    assert violation.severity == "error"
    assert violation.scope == "markdown"
    assert violation.location == "markdown.sections.Data Quality"
    assert violation.details == {"required_heading": "## Data Quality"}


def test_missing_target_markdown_section_returns_release_blocking_violation() -> None:
    payload = _fixture_payload()
    markdown = MarkdownSignalReportRenderer().render(payload=payload).replace(
        "## Market Map",
        "## Market Notes",
    )

    result = ReportQualityGate().evaluate(payload=payload, markdown=markdown)

    violation = _single_violation(result, "missing_markdown_section")
    assert result.status == "failed"
    assert violation.severity == "error"
    assert violation.scope == "markdown"
    assert violation.location == "markdown.sections.Market Map"
    assert violation.details == {"required_heading": "## Market Map"}


def test_missing_target_section_values_return_release_blocking_violations() -> None:
    payload = _fixture_payload()
    markdown = (
        MarkdownSignalReportRenderer()
        .render(payload=payload)
        .replace("AI infrastructure", "AI hidden")
        .replace("### etf_focus_ai - AI Innovation Active ETF", "### hidden ETF")
        .replace("NVDA 다중 ETF 비중 확대 신호 관측", "hidden evidence title")
    )

    result = ReportQualityGate().evaluate(payload=payload, markdown=markdown)

    violations = [
        violation
        for violation in result.violations
        if violation.code == "markdown_target_section_value_missing"
    ]
    assert result.status == "failed"
    assert {violation.severity for violation in violations} == {"error"}
    assert {violation.details["target_section"] for violation in violations} == {
        "market_map",
        "etf_follow_sheets",
        "evidence_ledger",
    }
    assert {violation.scope for violation in violations} == {"markdown"}


def test_markdown_target_section_values_allow_escaped_table_pipes() -> None:
    payload_data = _fixture_payload().model_dump(mode="json")
    payload_data["evidence_ledger"][0]["title"] = (
        "[Resale] $69.59 | router headline with markdown table delimiter"
    )
    payload = SignalReportPayload.model_validate(payload_data)
    markdown = MarkdownSignalReportRenderer().render(payload=payload)

    result = ReportQualityGate().evaluate(payload=payload, markdown=markdown)

    assert result.status == "passed"
    assert not any(
        violation.code == "markdown_target_section_value_missing"
        for violation in result.violations
    )


def test_raw_claim_scope_exposure_is_aggregated_without_raw_scope_strings() -> None:
    payload = _fixture_payload()
    markdown = MarkdownSignalReportRenderer().render(payload=payload)
    exposed = "\n".join(signal.claim_scope for signal in payload.signal_board[:2])

    result = ReportQualityGate().evaluate(
        payload=payload,
        markdown=f"{markdown}\n\n{exposed}",
    )

    violation = _single_violation(result, "raw_claim_scope_exposed")
    assert violation.location == "markdown.claim_scope"
    assert violation.details == {"claim_scope_count": 2}
    dumped = result.model_dump_json()
    assert payload.signal_board[0].claim_scope not in dumped
    assert payload.signal_board[1].claim_scope not in dumped


def test_raw_used_in_exposure_is_aggregated_without_raw_reference_strings() -> None:
    payload = _fixture_payload()
    markdown = MarkdownSignalReportRenderer().render(payload=payload)
    exposed = "ticker_dossier:security:sec_nvda"

    result = ReportQualityGate().evaluate(
        payload=payload,
        markdown=f"{markdown}\n\n{exposed}",
    )

    violation = _single_violation(result, "raw_used_in_exposed")
    assert violation.location == "markdown.used_in"
    assert violation.details == {"reference_count": 1}
    assert exposed not in result.model_dump_json()


def test_signal_reflection_reports_missing_values_per_affected_row() -> None:
    payload = _fixture_payload()
    markdown = "\n".join(
        [
            "# Signal Intelligence Report",
            "## Executive Summary",
            "## Signal Board",
            "## Ticker Dossiers",
            "## Data Quality",
            "## Methodology",
        ]
    )

    result = ReportQualityGate().evaluate(payload=payload, markdown=markdown)

    missing = [
        violation
        for violation in result.violations
        if violation.code == "markdown_signal_value_missing"
    ]
    assert len(missing) == len(payload.signal_board)
    assert missing[0].location == "markdown.signal_board.rank_1"
    assert missing[0].details == {
        "ticker": "NVDA",
        "rank": 1,
        "missing": (
            "ticker",
            "review_label",
            "display.review_label",
            "evidence_grade",
        ),
    }


@pytest.mark.parametrize(
    ("payload_update", "expected_code"),
    [
        ({"data_quality": {"limitations": []}}, "missing_data_quality_limitations"),
        ({"executive_summary": {"primary_risks": []}}, "missing_primary_risks"),
    ],
)
def test_payload_content_warnings(payload_update: dict[str, object], expected_code: str) -> None:
    payload = _payload_with_nested_updates(_fixture_payload(), payload_update)
    markdown = MarkdownSignalReportRenderer().render(payload=payload)

    result = ReportQualityGate().evaluate(payload=payload, markdown=markdown)

    violation = _single_violation(result, expected_code)
    assert result.status == "passed"
    assert violation.severity == "warning"
    assert violation.scope == "payload"


def test_operational_risk_failed_data_quality_issue_does_not_block_quality_gate() -> None:
    fixture = load_fixture_signal_report_inputs()
    payload = build_signal_report_payload(
        snapshots=fixture.snapshots,
        focus_etf_id=fixture.focus_etf_id,
        evidence=fixture.evidence,
        operational_provenance={
            "sync_metadata_available": True,
            "sync_quality": {
                "schema_version": "agent_treport.operational_holdings.sync_quality.v1",
                "status": "risk_failed",
                "metrics": {
                    "cash_derivation_failure_ratio": 0.24,
                    "fit_failure_ratio": 0.2,
                    "unusable_cash_weight_ratio": 0.04,
                    "ticker_mapping_coverage_ratio": 0.49,
                    "missing_source_date_count": 0,
                    "skipped_missing_security_id_count": 0,
                },
                "warnings": [],
                "risk_failures": [
                    {
                        "code": "low_ticker_mapping_coverage",
                        "message": "Ticker mapping coverage is too low.",
                        "metric": "ticker_mapping_coverage_ratio",
                        "value": 0.49,
                        "threshold": 0.5,
                    }
                ],
            },
        },
    )
    markdown = MarkdownSignalReportRenderer().render(payload=payload)
    html = HTMLResearchReportRenderer().render(payload=payload)
    telegram_alert = TelegramSignalAlertRenderer().render(
        payload=payload,
        full_report_reference="artifact_treport_html_report",
    )

    result = ReportQualityGate().evaluate(
        payload=payload,
        markdown=markdown,
        html=html,
        telegram_alert=telegram_alert,
    )

    assert any(
        issue.code == "operational_low_ticker_mapping_coverage" and issue.severity == "high"
        for issue in payload.data_quality.issues
    )
    assert result.status == "passed"
    assert result.summary["blocking"] is False


def test_no_signals_with_coverage_payload_warning() -> None:
    fixture = load_fixture_signal_report_inputs()
    payload = build_signal_report_payload(
        snapshots=_snapshots_with_current_equal_to_previous(fixture.snapshots),
        focus_etf_id=fixture.focus_etf_id,
        evidence=fixture.evidence,
    )
    markdown = MarkdownSignalReportRenderer().render(payload=payload)

    result = ReportQualityGate().evaluate(payload=payload, markdown=markdown)

    violation = _single_violation(result, "no_signals_with_coverage")
    assert result.status == "passed"
    assert violation.details == {"holding_rows": payload.coverage.holding_rows}


def test_custom_forbidden_markdown_fragments_block_report() -> None:
    payload = _fixture_payload()
    markdown = MarkdownSignalReportRenderer().render(payload=payload)
    contract = ReportQualityContract.default().model_copy(
        update={"forbidden_markdown_fragments": ("DO_NOT_RENDER",)}
    )

    result = ReportQualityGate(contract=contract).evaluate(
        payload=payload,
        markdown=f"{markdown}\nDO_NOT_RENDER",
    )

    violation = _single_violation(result, "forbidden_markdown_fragment")
    assert result.status == "failed"
    assert violation.location == "markdown.text"
    assert violation.details == {"fragment": "DO_NOT_RENDER"}


def test_custom_required_payload_sections_block_when_missing() -> None:
    payload = _fixture_payload()
    markdown = MarkdownSignalReportRenderer().render(payload=payload)
    contract = ReportQualityContract.default().model_copy(
        update={"required_payload_sections": ("unsupported_section",)}
    )

    result = ReportQualityGate(contract=contract).evaluate(
        payload=payload,
        markdown=markdown,
    )

    violation = _single_violation(result, "missing_payload_section")
    assert result.status == "failed"
    assert violation.location == "payload.unsupported_section"
    assert violation.details == {"required_section": "unsupported_section"}


def test_max_allowed_error_count_keeps_status_and_blocking_synchronized() -> None:
    payload = _fixture_payload()
    markdown = MarkdownSignalReportRenderer().render(payload=payload).replace(
        "## Data Quality",
        "## Data Notes",
    )
    contract = ReportQualityContract.default().model_copy(
        update={"max_allowed_error_count": 1}
    )

    result = ReportQualityGate(contract=contract).evaluate(
        payload=payload,
        markdown=markdown,
    )

    assert result.summary["error_count"] == 1
    assert result.summary["blocking"] is False
    assert result.status == "passed"


def _fixture_payload() -> SignalReportPayload:
    fixture = load_fixture_signal_report_inputs()
    return build_signal_report_payload(
        snapshots=fixture.snapshots,
        focus_etf_id=fixture.focus_etf_id,
        evidence=fixture.evidence,
    )


def _fixture_telegram_alert(payload: SignalReportPayload) -> str:
    return TelegramSignalAlertRenderer().render(
        payload=payload,
        full_report_reference="artifact_treport_html_report",
    )


def _single_violation(result, code: str):
    matches = [violation for violation in result.violations if violation.code == code]
    assert len(matches) == 1
    return matches[0]


def _payload_with_nested_updates(
    payload: SignalReportPayload,
    updates: dict[str, object],
) -> SignalReportPayload:
    data = payload.model_dump(mode="json")
    for section, section_updates in updates.items():
        if isinstance(section_updates, dict):
            data[section].update(section_updates)
        else:
            data[section] = section_updates
    return SignalReportPayload.model_validate(data)


def _snapshots_with_current_equal_to_previous(
    snapshots: MultiETFHoldingsSnapshots,
) -> MultiETFHoldingsSnapshots:
    data = snapshots.model_dump(mode="json")
    for etf in data["etfs"]:
        etf["current"] = list(etf["previous"])
    return MultiETFHoldingsSnapshots.model_validate(data)
