from __future__ import annotations

import re
from collections.abc import Mapping
from html import unescape
from typing import Literal

from agent_pack.models import JsonValue, RuntimeModel, strict_json_object
from pydantic import Field, field_validator, model_validator

from agent_treport.signal_report.domain.investment_language_policy import (
    find_prohibited_investment_language,
)
from agent_treport.signal_report.domain.payload import SignalReportPayload

ReportQualityStatus = Literal["passed", "failed"]
ReportQualitySeverity = Literal["error", "warning"]
ReportQualityScope = Literal["payload", "markdown", "html", "telegram_alert"]
ProhibitedInvestmentLanguagePolicy = Literal["default_investment_language"]

_DEFAULT_MARKDOWN_SECTIONS = (
    "# Signal Intelligence Report",
    "## Executive Summary",
    "## Signal Board",
    "## Market Map",
    "## ETF Follow Sheets",
    "## Ticker Dossiers",
    "## Evidence Ledger",
    "## Data Quality",
    "## Methodology",
)
_DEFAULT_PAYLOAD_SECTIONS = (
    "meta",
    "coverage",
    "executive_summary",
    "signal_board",
    "market_map",
    "etf_follow_sheets",
    "ticker_dossiers",
    "evidence_ledger",
    "methodology",
    "data_quality",
)
_DEFAULT_HTML_SECTIONS = (
    "executive-summary",
    "signal-board",
    "ticker-dossiers",
    "evidence-ledger",
    "etf-follow-sheets",
    "market-map",
    "data-quality",
    "methodology",
)
_DEFAULT_FORBIDDEN_HTML_FRAGMENTS = (
    '<script type="application/json"',
    'id="report-payload"',
)
_TARGET_MARKDOWN_SECTIONS = (
    ("Market Map", "market_map"),
    ("ETF Follow Sheets", "etf_follow_sheets"),
    ("Evidence Ledger", "evidence_ledger"),
)
_TARGET_HTML_SECTIONS = (
    ("market-map", "market_map"),
    ("etf-follow-sheets", "etf_follow_sheets"),
    ("evidence-ledger", "evidence_ledger"),
)
_FALLBACK_CLAIM_SCOPE_PATTERN = re.compile(
    r"signal:(?:security|security_group):[A-Za-z0-9_.\-]+:[A-Za-z0-9_]+|"
    r"signal:[A-Za-z0-9_.\-]+:[A-Za-z0-9_]+"
)
_TELEGRAM_ALERT_SIGNAL_ROW_PATTERN = re.compile(r"^\d+\.\s*<code>", re.MULTILINE)
_TELEGRAM_ALERT_REQUIRED_SECTIONS = (
    ("briefing", "<b>ETF 시그널 브리핑</b>"),
    ("key", "<b>오늘의 핵심</b>"),
    ("signals", "<b>핵심 신호 Top"),
    ("full_report", "<b>전체 리포트</b>"),
)
_TELEGRAM_ALERT_FORBIDDEN_FRAGMENTS = (
    "<script",
    "<a ",
    "<img",
    "<iframe",
    "<object",
    "<embed",
)


class ReportQualityContract(RuntimeModel):
    required_markdown_sections: tuple[str, ...]
    required_payload_sections: tuple[str, ...]
    required_html_sections: tuple[str, ...]
    forbidden_markdown_fragments: tuple[str, ...]
    forbidden_html_fragments: tuple[str, ...]
    prohibited_language_policy: ProhibitedInvestmentLanguagePolicy
    max_allowed_error_count: int

    @classmethod
    def default(cls) -> ReportQualityContract:
        return cls(
            required_markdown_sections=_DEFAULT_MARKDOWN_SECTIONS,
            required_payload_sections=_DEFAULT_PAYLOAD_SECTIONS,
            required_html_sections=_DEFAULT_HTML_SECTIONS,
            forbidden_markdown_fragments=(),
            forbidden_html_fragments=_DEFAULT_FORBIDDEN_HTML_FRAGMENTS,
            prohibited_language_policy="default_investment_language",
            max_allowed_error_count=0,
        )


class ReportQualityViolation(RuntimeModel):
    code: str
    severity: ReportQualitySeverity
    scope: ReportQualityScope
    message: str
    location: str
    details: Mapping[str, JsonValue] = Field(default_factory=dict)

    @field_validator("details", mode="before")
    @classmethod
    def _validate_details(cls, value: object) -> dict[str, JsonValue]:
        return strict_json_object(value or {})


class ReportQualityResult(RuntimeModel):
    status: ReportQualityStatus
    violations: tuple[ReportQualityViolation, ...]
    summary: Mapping[str, JsonValue]

    @field_validator("summary", mode="before")
    @classmethod
    def _validate_summary(cls, value: object) -> dict[str, JsonValue]:
        return strict_json_object(value or {})

    @model_validator(mode="after")
    def _validate_summary_contract(self) -> ReportQualityResult:
        error_count = self.summary.get("error_count")
        warning_count = self.summary.get("warning_count")
        scopes = self.summary.get("scopes")
        blocking = self.summary.get("blocking")
        if not isinstance(error_count, int):
            raise ValueError("summary.error_count must be an integer")
        if not isinstance(warning_count, int):
            raise ValueError("summary.warning_count must be an integer")
        if not isinstance(scopes, Mapping):
            raise ValueError("summary.scopes must be an object")
        payload_count = scopes.get("payload")
        markdown_count = scopes.get("markdown")
        html_count = scopes.get("html")
        telegram_alert_count = scopes.get("telegram_alert")
        if (
            not isinstance(payload_count, int)
            or not isinstance(markdown_count, int)
            or not isinstance(html_count, int)
            or not isinstance(telegram_alert_count, int)
        ):
            raise ValueError(
                "summary.scopes must include payload, markdown, html, "
                "and telegram_alert counts"
            )
        if (
            error_count + warning_count
            != payload_count + markdown_count + html_count + telegram_alert_count
        ):
            raise ValueError("summary counts must be synchronized")
        if not isinstance(blocking, bool):
            raise ValueError("summary.blocking must be a boolean")
        expected_status = "failed" if blocking else "passed"
        if self.status != expected_status:
            raise ValueError("status must be synchronized with summary.blocking")
        return self


class ReportQualityGate:
    def __init__(self, *, contract: ReportQualityContract | None = None) -> None:
        self._contract = contract or ReportQualityContract.default()

    def evaluate(
        self,
        *,
        payload: SignalReportPayload,
        markdown: str,
        html: str | None = None,
        telegram_alert: str | None = None,
    ) -> ReportQualityResult:
        violations: list[ReportQualityViolation] = []
        payload_data = payload.model_dump(mode="json")
        rendered_headings = {line.strip() for line in markdown.splitlines()}

        violations.extend(_payload_required_section_violations(self._contract, payload_data))
        violations.extend(_payload_content_warnings(payload))
        violations.extend(_markdown_required_section_violations(self._contract, rendered_headings))
        violations.extend(_markdown_target_section_value_violations(payload, markdown))
        violations.extend(
            _prohibited_language_violations(self._contract, markdown, scope="markdown")
        )
        violations.extend(_forbidden_markdown_fragment_violations(self._contract, markdown))
        violations.extend(_raw_claim_scope_violations(payload, markdown, scope="markdown"))
        violations.extend(_raw_used_in_violations(payload, markdown, scope="markdown"))
        violations.extend(_signal_reflection_violations(payload, markdown))
        if html is not None:
            violations.extend(_html_required_section_violations(self._contract, html))
            violations.extend(_html_target_section_value_violations(payload, html))
            violations.extend(
                _prohibited_language_violations(self._contract, html, scope="html")
            )
            violations.extend(_forbidden_html_fragment_violations(self._contract, html))
            violations.extend(_raw_claim_scope_violations(payload, html, scope="html"))
            violations.extend(_raw_used_in_violations(payload, html, scope="html"))
            violations.extend(_html_signal_reflection_violations(payload, html))
        if telegram_alert is not None:
            violations.extend(_telegram_alert_required_section_violations(telegram_alert))
            violations.extend(_telegram_alert_length_violations(telegram_alert))
            violations.extend(_telegram_alert_signal_count_violations(telegram_alert))
            violations.extend(_telegram_alert_data_quality_violations(telegram_alert))
            violations.extend(_telegram_alert_full_report_reference_violations(telegram_alert))
            violations.extend(_forbidden_telegram_alert_fragment_violations(telegram_alert))
            violations.extend(
                _prohibited_language_violations(
                    self._contract,
                    telegram_alert,
                    scope="telegram_alert",
                )
            )
            violations.extend(
                _raw_claim_scope_violations(payload, telegram_alert, scope="telegram_alert")
            )
            violations.extend(
                _raw_used_in_violations(payload, telegram_alert, scope="telegram_alert")
            )

        return _quality_result(
            violations=tuple(violations),
            max_allowed_error_count=self._contract.max_allowed_error_count,
        )


def _payload_required_section_violations(
    contract: ReportQualityContract,
    payload_data: Mapping[str, JsonValue],
) -> tuple[ReportQualityViolation, ...]:
    return tuple(
        ReportQualityViolation(
            code="missing_payload_section",
            severity="error",
            scope="payload",
            message=f"Required payload section is missing: {section}.",
            location=f"payload.{section}",
            details={"required_section": section},
        )
        for section in contract.required_payload_sections
        if section not in payload_data
    )


def _payload_content_warnings(payload: SignalReportPayload) -> tuple[ReportQualityViolation, ...]:
    warnings: list[ReportQualityViolation] = []
    if not payload.data_quality.limitations:
        warnings.append(
            ReportQualityViolation(
                code="missing_data_quality_limitations",
                severity="warning",
                scope="payload",
                message="Data-quality limitations are empty.",
                location="payload.data_quality.limitations",
            )
        )
    if not payload.executive_summary.primary_risks:
        warnings.append(
            ReportQualityViolation(
                code="missing_primary_risks",
                severity="warning",
                scope="payload",
                message="Executive summary primary risks are empty.",
                location="payload.executive_summary.primary_risks",
            )
        )
    if not payload.signal_board and payload.coverage.holding_rows > 0:
        warnings.append(
            ReportQualityViolation(
                code="no_signals_with_coverage",
                severity="warning",
                scope="payload",
                message="Coverage exists but no Signal Board rows were produced.",
                location="payload.signal_board",
                details={"holding_rows": payload.coverage.holding_rows},
            )
        )
    return tuple(warnings)


def _markdown_required_section_violations(
    contract: ReportQualityContract,
    rendered_headings: set[str],
) -> tuple[ReportQualityViolation, ...]:
    violations: list[ReportQualityViolation] = []
    for heading in contract.required_markdown_sections:
        if heading in rendered_headings:
            continue
        heading_name = _heading_name(heading)
        violations.append(
            ReportQualityViolation(
                code="missing_markdown_section",
                severity="error",
                scope="markdown",
                message=f"Required Markdown section is missing: {heading}.",
                location=f"markdown.sections.{heading_name}",
                details={"required_heading": heading},
            )
        )
    return tuple(violations)


def _markdown_target_section_value_violations(
    payload: SignalReportPayload,
    markdown: str,
) -> tuple[ReportQualityViolation, ...]:
    required_values = {
        "market_map": _market_map_representative_values(payload),
        "etf_follow_sheets": _etf_follow_sheet_representative_values(payload),
        "evidence_ledger": _evidence_ledger_representative_values(payload),
    }
    section_bounds = {
        "market_map": ("## Market Map", "## ETF Follow Sheets"),
        "etf_follow_sheets": ("## ETF Follow Sheets", "## Ticker Dossiers"),
        "evidence_ledger": ("## Evidence Ledger", "## Data Quality"),
    }
    violations: list[ReportQualityViolation] = []
    for heading, target_section in _TARGET_MARKDOWN_SECTIONS:
        start_heading = f"## {heading}"
        if start_heading not in markdown:
            continue
        start_heading, end_heading = section_bounds[target_section]
        section_markdown = _markdown_section(markdown, start_heading, end_heading)
        missing = tuple(
            name
            for name, value in required_values[target_section]
            if value and not _markdown_section_contains_value(section_markdown, value)
        )
        if not missing:
            continue
        violations.append(
            ReportQualityViolation(
                code="markdown_target_section_value_missing",
                severity="error",
                scope="markdown",
                message="Rendered Markdown is missing target-section payload values.",
                location=f"markdown.sections.{heading}",
                details={"target_section": target_section, "missing": missing},
            )
        )
    return tuple(violations)


def _markdown_section_contains_value(section_markdown: str, value: str) -> bool:
    if value in section_markdown:
        return True
    markdown_cell_value = " ".join(value.splitlines()).replace("|", r"\|")
    return markdown_cell_value in section_markdown


def _html_required_section_violations(
    contract: ReportQualityContract,
    html: str,
) -> tuple[ReportQualityViolation, ...]:
    violations: list[ReportQualityViolation] = []
    for section_id in contract.required_html_sections:
        if f'id="{section_id}"' in html or f"id='{section_id}'" in html:
            continue
        violations.append(
            ReportQualityViolation(
                code="missing_html_section",
                severity="error",
                scope="html",
                message=f"Required HTML section is missing: {section_id}.",
                location=f"html.sections.{section_id}",
                details={"required_section_id": section_id},
            )
        )
    return tuple(violations)


def _prohibited_language_violations(
    contract: ReportQualityContract,
    rendered: str,
    *,
    scope: Literal["markdown", "html", "telegram_alert"],
) -> tuple[ReportQualityViolation, ...]:
    if contract.prohibited_language_policy != "default_investment_language":
        return ()
    categories = find_prohibited_investment_language(rendered)
    if not categories:
        return ()
    return (
        ReportQualityViolation(
            code="prohibited_investment_language",
            severity="error",
            scope=scope,
            message=f"Prohibited investment language appears in rendered {scope}.",
            location=f"{scope}.text",
            details={"categories": categories},
        ),
    )


def _forbidden_markdown_fragment_violations(
    contract: ReportQualityContract,
    markdown: str,
) -> tuple[ReportQualityViolation, ...]:
    return tuple(
        ReportQualityViolation(
            code="forbidden_markdown_fragment",
            severity="error",
            scope="markdown",
            message="Forbidden Markdown fragment appears in rendered Markdown.",
            location="markdown.text",
            details={"fragment": fragment},
        )
        for fragment in contract.forbidden_markdown_fragments
        if fragment and fragment in markdown
    )


def _forbidden_html_fragment_violations(
    contract: ReportQualityContract,
    html: str,
) -> tuple[ReportQualityViolation, ...]:
    return tuple(
        ReportQualityViolation(
            code="forbidden_html_fragment",
            severity="error",
            scope="html",
            message="Forbidden HTML fragment appears in rendered HTML.",
            location="html.text",
            details={"fragment": fragment},
        )
        for fragment in contract.forbidden_html_fragments
        if fragment and fragment in html
    )


def _raw_claim_scope_violations(
    payload: SignalReportPayload,
    rendered: str,
    *,
    scope: Literal["markdown", "html", "telegram_alert"],
) -> tuple[ReportQualityViolation, ...]:
    exposed_scopes: set[str] = set()
    for claim_scope in _payload_claim_scopes(payload):
        if claim_scope in rendered:
            exposed_scopes.add(claim_scope)
    exposed_scopes.update(_FALLBACK_CLAIM_SCOPE_PATTERN.findall(rendered))
    if not exposed_scopes:
        return ()
    return (
        ReportQualityViolation(
            code="raw_claim_scope_exposed",
            severity="error",
            scope=scope,
            message=f"Raw claim-scope identifiers appear in rendered {scope}.",
            location=f"{scope}.claim_scope",
            details={"claim_scope_count": len(exposed_scopes)},
        ),
    )


def _raw_used_in_violations(
    payload: SignalReportPayload,
    rendered: str,
    *,
    scope: Literal["markdown", "html", "telegram_alert"],
) -> tuple[ReportQualityViolation, ...]:
    exposed_refs = {
        reference for reference in _payload_used_in_refs(payload) if reference in rendered
    }
    if not exposed_refs:
        return ()
    return (
        ReportQualityViolation(
            code="raw_used_in_exposed",
            severity="error",
            scope=scope,
            message=f"Raw evidence usage references appear in rendered {scope}.",
            location=f"{scope}.used_in",
            details={"reference_count": len(exposed_refs)},
        ),
    )


def _signal_reflection_violations(
    payload: SignalReportPayload,
    markdown: str,
) -> tuple[ReportQualityViolation, ...]:
    violations: list[ReportQualityViolation] = []
    for signal in payload.signal_board:
        ticker = signal.ticker or "미확인"
        required_values = (
            ("ticker", ticker),
            ("review_label", signal.review_label),
            ("display.review_label", signal.display.review_label),
            ("evidence_grade", signal.evidence_grade),
        )
        missing = tuple(name for name, value in required_values if value not in markdown)
        if missing:
            violations.append(
                ReportQualityViolation(
                    code="markdown_signal_value_missing",
                    severity="error",
                    scope="markdown",
                    message="Rendered Markdown is missing Signal Board payload values.",
                    location=f"markdown.signal_board.rank_{signal.rank}",
                    details={"ticker": ticker, "rank": signal.rank, "missing": missing},
                )
            )
    return tuple(violations)


def _html_target_section_value_violations(
    payload: SignalReportPayload,
    html: str,
) -> tuple[ReportQualityViolation, ...]:
    required_values = {
        "market_map": _market_map_representative_values(payload),
        "etf_follow_sheets": _etf_follow_sheet_representative_values(payload),
        "evidence_ledger": _evidence_ledger_representative_values(payload),
    }
    violations: list[ReportQualityViolation] = []
    for section_id, target_section in _TARGET_HTML_SECTIONS:
        section_html = _html_section(html, section_id)
        if not section_html:
            continue
        section_text = unescape(_html_visible_text(section_html))
        missing = tuple(
            name
            for name, value in required_values[target_section]
            if value and value not in section_text
        )
        if not missing:
            continue
        violations.append(
            ReportQualityViolation(
                code="html_target_section_value_missing",
                severity="error",
                scope="html",
                message="Rendered HTML is missing target-section payload values.",
                location=f"html.sections.{section_id}",
                details={"target_section": target_section, "missing": missing},
            )
        )
    return tuple(violations)


def _html_signal_reflection_violations(
    payload: SignalReportPayload,
    html: str,
) -> tuple[ReportQualityViolation, ...]:
    board_html = _html_section(html, "signal-board")
    violations: list[ReportQualityViolation] = []
    for signal in payload.signal_board:
        ticker = signal.ticker or "미확인"
        row_html = _html_signal_row(board_html, signal.rank)
        row_text = _html_visible_text(row_html)
        required_values = (
            ("ticker", ticker),
            ("review_label", signal.review_label),
            ("evidence_grade", signal.evidence_grade),
        )
        missing = tuple(name for name, value in required_values if value not in row_text)
        if missing:
            violations.append(
                ReportQualityViolation(
                    code="html_signal_value_missing",
                    severity="error",
                    scope="html",
                    message="Rendered HTML is missing Signal Board payload values.",
                    location=f"html.signal_board.rank_{signal.rank}",
                    details={"ticker": ticker, "rank": signal.rank, "missing": missing},
                )
            )
    return tuple(violations)


def _telegram_alert_required_section_violations(
    telegram_alert: str,
) -> tuple[ReportQualityViolation, ...]:
    missing = tuple(
        name
        for name, marker in _TELEGRAM_ALERT_REQUIRED_SECTIONS
        if marker not in telegram_alert
    )
    if not missing:
        return ()
    return (
        ReportQualityViolation(
            code="missing_telegram_alert_section",
            severity="error",
            scope="telegram_alert",
            message="Required Telegram alert section is missing.",
            location="telegram_alert.sections",
            details={"missing_sections": missing},
        ),
    )


def _telegram_alert_length_violations(
    telegram_alert: str,
) -> tuple[ReportQualityViolation, ...]:
    max_length = 4096
    length = len(telegram_alert)
    if length <= max_length:
        return ()
    return (
        ReportQualityViolation(
            code="telegram_alert_too_long",
            severity="error",
            scope="telegram_alert",
            message="Telegram alert text exceeds the sendMessage text limit.",
            location="telegram_alert.text",
            details={"length": length, "max_length": max_length},
        ),
    )


def _telegram_alert_signal_count_violations(
    telegram_alert: str,
) -> tuple[ReportQualityViolation, ...]:
    max_signal_rows = 5
    signal_row_count = len(_TELEGRAM_ALERT_SIGNAL_ROW_PATTERN.findall(telegram_alert))
    if signal_row_count <= max_signal_rows:
        return ()
    return (
        ReportQualityViolation(
            code="telegram_alert_too_many_signals",
            severity="error",
            scope="telegram_alert",
            message="Telegram alert renders more than five signal rows.",
            location="telegram_alert.signal_rows",
            details={
                "signal_row_count": signal_row_count,
                "max_signal_rows": max_signal_rows,
            },
        ),
    )


def _telegram_alert_data_quality_violations(
    telegram_alert: str,
) -> tuple[ReportQualityViolation, ...]:
    required_markers = (
        ("section", "데이터 품질"),
        ("status", "상태 "),
        ("issues", "이슈"),
    )
    missing = tuple(name for name, marker in required_markers if marker not in telegram_alert)
    if not missing:
        return ()
    return (
        ReportQualityViolation(
            code="missing_telegram_alert_data_quality",
            severity="error",
            scope="telegram_alert",
            message="Telegram alert is missing required data-quality markers.",
            location="telegram_alert.data_quality",
            details={"missing_markers": missing},
        ),
    )


def _telegram_alert_full_report_reference_violations(
    telegram_alert: str,
) -> tuple[ReportQualityViolation, ...]:
    if (
        "HTML artifact:" in telegram_alert
        and "<code>artifact_treport_html_report</code>" in telegram_alert
    ):
        return ()
    return (
        ReportQualityViolation(
            code="missing_telegram_alert_full_report_reference",
            severity="error",
            scope="telegram_alert",
            message="Telegram alert is missing the full HTML report artifact reference.",
            location="telegram_alert.full_report_reference",
        ),
    )


def _forbidden_telegram_alert_fragment_violations(
    telegram_alert: str,
) -> tuple[ReportQualityViolation, ...]:
    lowered = telegram_alert.lower()
    return tuple(
        ReportQualityViolation(
            code="forbidden_telegram_alert_fragment",
            severity="error",
            scope="telegram_alert",
            message="Forbidden Telegram alert fragment appears in rendered alert.",
            location="telegram_alert.text",
            details={"fragment": fragment},
        )
        for fragment in _TELEGRAM_ALERT_FORBIDDEN_FRAGMENTS
        if fragment in lowered
    )


def _quality_result(
    *, violations: tuple[ReportQualityViolation, ...], max_allowed_error_count: int
) -> ReportQualityResult:
    error_count = sum(1 for violation in violations if violation.severity == "error")
    warning_count = sum(1 for violation in violations if violation.severity == "warning")
    payload_count = sum(1 for violation in violations if violation.scope == "payload")
    markdown_count = sum(1 for violation in violations if violation.scope == "markdown")
    html_count = sum(1 for violation in violations if violation.scope == "html")
    telegram_alert_count = sum(
        1 for violation in violations if violation.scope == "telegram_alert"
    )
    blocking = error_count > max_allowed_error_count
    return ReportQualityResult(
        status="failed" if blocking else "passed",
        violations=violations,
        summary={
            "error_count": error_count,
            "warning_count": warning_count,
            "scopes": {
                "payload": payload_count,
                "markdown": markdown_count,
                "html": html_count,
                "telegram_alert": telegram_alert_count,
            },
            "blocking": blocking,
        },
    )


def _market_map_representative_values(payload: SignalReportPayload) -> tuple[tuple[str, str], ...]:
    values: list[tuple[str, str]] = []
    for section_name, rows in (
        ("by_theme", payload.market_map.by_theme),
        ("by_sector", payload.market_map.by_sector),
        ("by_country", payload.market_map.by_country),
    ):
        values.extend(
            (f"{section_name}[{index}].key", row.key) for index, row in enumerate(rows[:5])
        )
    return tuple(values)


def _etf_follow_sheet_representative_values(
    payload: SignalReportPayload,
) -> tuple[tuple[str, str], ...]:
    values: list[tuple[str, str]] = []
    for index, sheet in enumerate(payload.etf_follow_sheets):
        values.extend(
            (
                (f"etf_follow_sheets[{index}].etf_id", sheet.etf_id),
                (f"etf_follow_sheets[{index}].etf_name", sheet.etf_name),
            )
        )
    return tuple(values)


def _evidence_ledger_representative_values(
    payload: SignalReportPayload,
) -> tuple[tuple[str, str], ...]:
    return tuple(
        (f"evidence_ledger[{index}].title", item.title)
        for index, item in enumerate(payload.evidence_ledger)
    )


def _markdown_section(markdown: str, start_heading: str, end_heading: str) -> str:
    start = markdown.find(start_heading)
    if start < 0:
        return ""
    section_start = start + len(start_heading)
    end = markdown.find(end_heading, section_start)
    if end < 0:
        return markdown[section_start:]
    return markdown[section_start:end]


def _html_section(html: str, section_id: str) -> str:
    id_marker = f'id="{section_id}"'
    id_position = html.find(id_marker)
    if id_position < 0:
        return ""
    section_start = html.rfind("<section", 0, id_position)
    if section_start < 0:
        section_start = id_position
    next_section = html.find("\n<section id=", id_position + len(id_marker))
    if next_section < 0:
        next_section = html.find("\n</main>", id_position + len(id_marker))
    if next_section < 0:
        return html[section_start:]
    return html[section_start:next_section]


def _html_signal_row(board_html: str, rank: int) -> str:
    row_marker = f'<tr class="signal-row" data-signal-key="rank-{rank}"'
    row_start = board_html.find(row_marker)
    if row_start < 0:
        return ""
    row_end = board_html.find("</tr>", row_start)
    if row_end < 0:
        return board_html[row_start:]
    return board_html[row_start:row_end]


def _html_visible_text(html: str) -> str:
    return re.sub(r"<[^>]*>", " ", html)


def _payload_claim_scopes(payload: SignalReportPayload) -> tuple[str, ...]:
    scopes: list[str] = [signal.claim_scope for signal in payload.signal_board]
    scopes.extend(
        item.claim_scope for item in payload.evidence_ledger if item.claim_scope is not None
    )
    return tuple(dict.fromkeys(scopes))


def _payload_used_in_refs(payload: SignalReportPayload) -> tuple[str, ...]:
    references: list[str] = []
    for item in payload.evidence_ledger:
        references.extend(item.used_in)
    return tuple(dict.fromkeys(references))


def _heading_name(heading: str) -> str:
    return heading.lstrip("#").strip()
