from __future__ import annotations

from collections.abc import Mapping

from agent_pack.models import JsonValue

from agent_treport.signal_report.domain.commentary_policy import (
    OMITTED_MODEL_COMMENTARY_MESSAGE,
    evaluate_model_commentary,
)
from agent_treport.signal_report.domain.payload import MarketMapSlice, SignalReportPayload
from agent_treport.signal_report.renderers.display import (
    EvidenceDisplayReference,
    display_ticker,
)


class MarkdownSignalReportRenderer:
    def render(
        self,
        *,
        payload: SignalReportPayload,
        model_commentary: str | None = None,
    ) -> str:
        sections = [
            "# Signal Intelligence Report",
            "",
            f"Report ID: {payload.meta.report_id}",
            f"As of: {payload.meta.as_of_date}",
            f"Mode: {payload.methodology.analysis_mode}",
            "",
            self._executive_summary(payload),
            "",
            self._signal_board(payload),
            "",
            self._market_map(payload),
            "",
            self._etf_follow_sheets(payload),
            "",
            self._ticker_dossiers(payload),
            "",
            self._evidence_ledger(payload),
            "",
            self._data_quality(payload),
            "",
            self._methodology(payload),
        ]
        if model_commentary:
            commentary_policy = evaluate_model_commentary(model_commentary)
            commentary_text = (
                OMITTED_MODEL_COMMENTARY_MESSAGE
                if commentary_policy.status == "omitted"
                else commentary_policy.text
            )
            if commentary_text:
                sections.extend(["", "## Model Commentary", "", commentary_text])
        sections.append("")
        return "\n".join(sections)

    def _executive_summary(self, payload: SignalReportPayload) -> str:
        lines = [
            "## Executive Summary",
            "",
            payload.executive_summary.headline,
            "",
            payload.executive_summary.market_read,
            "",
            "### Top Takeaways",
            "",
        ]
        lines.extend(f"- {item}" for item in payload.executive_summary.top_takeaways)
        lines.extend(["", "### Primary Risks", ""])
        lines.extend(f"- {item}" for item in payload.executive_summary.primary_risks)
        return "\n".join(lines)

    def _signal_board(self, payload: SignalReportPayload) -> str:
        if not payload.signal_board:
            return "\n".join(
                [
                    "## Signal Board",
                    "",
                    payload.executive_summary.headline,
                ]
            )
        lines = [
            "## Signal Board",
            "",
            (
                "| Rank | Ticker | Name | Signal Type | Score | Evidence | Review | "
                "Primary Reason |"
            ),
            "| ---: | --- | --- | --- | ---: | --- | --- | --- |",
        ]
        for signal in payload.signal_board:
            lines.append(
                "| "
                + " | ".join(
                    _markdown_cell(value)
                    for value in (
                        signal.rank,
                        _ticker(signal.ticker),
                        signal.name,
                        signal.signal_type,
                        signal.signal_score,
                        f"{signal.evidence_grade} / {signal.display.evidence_grade}",
                        f"{signal.review_label} / {signal.display.review_label}",
                        signal.primary_reason,
                    )
                )
                + " |"
            )
        return "\n".join(lines)

    def _market_map(self, payload: SignalReportPayload) -> str:
        lines = ["## Market Map", ""]
        lines.extend(_market_map_table("By Theme", payload.market_map.by_theme))
        lines.extend([""])
        lines.extend(_market_map_table("By Sector", payload.market_map.by_sector))
        lines.extend([""])
        lines.extend(_market_map_table("By Country", payload.market_map.by_country))
        lines.extend(
            [
                "",
                f"- Cash position: {_mapping_summary(payload.market_map.cash_position)}",
                f"- Concentration: {_mapping_summary(payload.market_map.concentration)}",
                f"- Crowding: {_mapping_summary(payload.market_map.crowding)}",
            ]
        )
        return "\n".join(lines)

    def _etf_follow_sheets(self, payload: SignalReportPayload) -> str:
        lines = ["## ETF Follow Sheets", ""]
        if not payload.etf_follow_sheets:
            lines.append("- None")
            return "\n".join(lines)

        for sheet in payload.etf_follow_sheets:
            lines.extend(
                [
                    f"### {sheet.etf_id} - {sheet.etf_name}",
                    "",
                    f"- ETF Brand ID: {sheet.brand_id}",
                    f"- Source Provider ID: {sheet.source_provider_id}",
                    f"- Focus ETF: {sheet.is_focus}",
                    f"- Top Holdings: {_join_limited_or_none(sheet.top_holdings)}",
                    f"- New Positions: {_join_limited_or_none(sheet.new_positions)}",
                    f"- Exited Positions: {_join_limited_or_none(sheet.exited_positions)}",
                    f"- Increased Positions: {_join_limited_or_none(sheet.increased_positions)}",
                    f"- Decreased Positions: {_join_limited_or_none(sheet.decreased_positions)}",
                    f"- Cash Change pp: {_format_optional(sheet.cash_change_pp)}",
                    f"- ETF Brand Behavior Read: {sheet.brand_behavior_read}",
                    f"- Data Quality: {_mapping_summary(sheet.data_quality)}",
                    "",
                ]
            )
            if sheet.theme_exposure_changes:
                lines.extend(
                    _market_map_table(
                        "Theme Exposure Changes",
                        sheet.theme_exposure_changes,
                        category_label="Theme",
                        heading_level="####",
                    )
                )
            else:
                lines.append("- Theme Exposure Changes: None")
            lines.append("")
        return "\n".join(lines).rstrip()

    def _ticker_dossiers(self, payload: SignalReportPayload) -> str:
        lines = ["## Ticker Dossiers", ""]
        for dossier in payload.ticker_dossiers:
            lines.extend(
                [
                    f"### {_ticker(dossier.ticker)} - {dossier.name}",
                    "",
                    dossier.summary,
                    "",
                    "- 보유 사실: "
                    f"{dossier.holding_facts.participating_etfs}개 ETF, "
                    f"{_format_optional(dossier.holding_facts.weight_delta_pp)}pp 변화",
                    f"- Why-now hypothesis: {dossier.why_now_hypothesis}",
                    "- Supporting evidence: "
                    + (_join_or_none(dossier.supporting_evidence)),
                    "- Counter evidence: " + (_join_or_none(dossier.counter_evidence)),
                    "- Invalidation: "
                    + (_join_or_none(dossier.invalidation_conditions)),
                    f"- Final label: {dossier.final_label} / {dossier.display['final_label']}",
                    "",
                ]
            )
        return "\n".join(lines).rstrip()

    def _evidence_ledger(self, payload: SignalReportPayload) -> str:
        lines = [
            "## Evidence Ledger",
            "",
            "| Title | Source | Type | Stance | Strength | Claim | Used In |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        evidence_display = EvidenceDisplayReference.from_payload(payload)
        for item in payload.evidence_ledger:
            claim = evidence_display.claim_scope(item.claim_scope)
            used_in = evidence_display.used_in(item.used_in)
            lines.append(
                "| "
                + " | ".join(
                    _markdown_cell(value)
                    for value in (
                        item.title,
                        item.source,
                        item.type,
                        item.stance,
                        item.strength,
                        claim,
                        used_in,
                    )
                )
                + " |"
            )
        return "\n".join(lines)

    def _data_quality(self, payload: SignalReportPayload) -> str:
        lines = [
            "## Data Quality",
            "",
            f"Overall: {payload.data_quality.overall}",
            "",
            "### Limitations",
            "",
        ]
        lines.extend(f"- {item}" for item in payload.data_quality.limitations)
        lines.extend(["", "### Issues", ""])
        if payload.data_quality.issues:
            lines.extend(
                f"- {issue.code} ({issue.severity}) {issue.scope}: {issue.message}"
                for issue in payload.data_quality.issues
            )
        else:
            lines.append("- None")
        return "\n".join(lines)

    def _methodology(self, payload: SignalReportPayload) -> str:
        lines = [
            "## Methodology",
            "",
            f"Scoring version: {payload.methodology.scoring_version}",
            "",
            "| Component | Meaning |",
            "| --- | --- |",
        ]
        lines.extend(
            "| " + " | ".join(_markdown_cell(value) for value in (name, meaning)) + " |"
            for name, meaning in payload.methodology.score_components.items()
        )
        return "\n".join(lines)


def _market_map_table(
    title: str,
    rows: tuple[MarketMapSlice, ...],
    *,
    category_label: str = "Category",
    heading_level: str = "###",
) -> list[str]:
    lines = [f"{heading_level} {title}", ""]
    if not rows:
        lines.append("- None")
        return lines
    lines.extend(
        [
            f"| {category_label} | Weight Delta pp | Net Flow KRW | Signal Count |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    for row in rows[:5]:
        lines.append(
            "| "
            + " | ".join(
                _markdown_cell(value)
                for value in (
                    row.key,
                    _format_optional(row.weight_delta_pp),
                    _format_optional(row.net_flow_estimate_krw),
                    row.signal_count,
                )
            )
            + " |"
        )
    return lines


def _mapping_summary(values: Mapping[str, JsonValue]) -> str:
    if not values:
        return "None"
    return "; ".join(f"{key}={value}" for key, value in values.items())


def _join_limited_or_none(values: tuple[str, ...]) -> str:
    return ", ".join(values[:5]) if values else "None"


def _markdown_cell(value: object) -> str:
    text = "n/a" if value is None else str(value)
    return " ".join(text.splitlines()).replace("|", r"\|")


def _ticker(ticker: str | None) -> str:
    return display_ticker(ticker)


def _format_optional(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def _join_or_none(values: tuple[str, ...]) -> str:
    return ", ".join(values) if values else "없음"
