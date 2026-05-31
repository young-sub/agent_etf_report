from __future__ import annotations

from collections.abc import Iterable, Mapping
from html import escape
from urllib.parse import urlparse

from agent_pack.models import JsonValue

from agent_treport.signal_report.domain.commentary_policy import (
    OMITTED_MODEL_COMMENTARY_MESSAGE,
    evaluate_model_commentary,
)
from agent_treport.signal_report.domain.payload import (
    ETFFollowSheet,
    EvidenceLedgerItem,
    MarketMapSlice,
    SignalBoardRow,
    SignalReportPayload,
    TickerDossier,
)
from agent_treport.signal_report.renderers.display import (
    EvidenceDisplayReference,
    display_ticker,
    safe_ascii_slug,
)

NO_MATCH_MESSAGE = "\uc870\uac74\uc5d0 \ub9de\ub294 \uc2e0\ud638\uac00 \uc5c6\uc2b5\ub2c8\ub2e4"
NO_SIGNAL_MESSAGE = (
    "\uc758\ubbf8 \uc788\ub294 ETF \ubcf4\uc720 \ubcc0\ud654 \uc2e0\ud638\uac00 "
    "\ubc1c\uacac\ub418\uc9c0 \uc54a\uc558\uc2b5\ub2c8\ub2e4."
)
NO_LINK_MESSAGE = "\ub9c1\ud06c \uc5c6\uc74c"
NOSCRIPT_MESSAGE = (
    "JavaScript\uac00 \uaebc\uc838 \uc788\uc5b4 \ud544\ud130\uc640 "
    "\uc815\ub82c\uc740 \uc0ac\uc6a9\ud560 \uc218 \uc5c6\uc9c0\ub9cc "
    "\uc804\uccb4 \ubcf4\uace0\uc11c \ub0b4\uc6a9\uc740 \ud45c\uc2dc\ub429\ub2c8\ub2e4."
)
REVIEW_FILTER_OPTIONS = (
    ("focus", "\uc911\uc810 \ubaa8\ub2c8\ud130\ub9c1"),
    ("monitor", "\ubaa8\ub2c8\ud130\ub9c1"),
    ("caution", "\uc720\uc758"),
    ("defer", "\ud310\ub2e8 \uc720\ubcf4"),
)
EVIDENCE_FILTER_OPTIONS = (
    ("Confirmed", "\ud655\uc778"),
    ("Plausible", "\ud0c0\ub2f9"),
    ("Weak", "\uc81c\ud55c\uc801"),
    ("Conflicted", "\uc0c1\ucda9"),
    ("Unusable", "\ud65c\uc6a9 \ubd88\uac00"),
)


class HTMLResearchReportRenderer:
    def render(
        self,
        *,
        payload: SignalReportPayload,
        model_commentary: str | None = None,
    ) -> str:
        commentary = _model_commentary_text(model_commentary)
        evidence_display = EvidenceDisplayReference.from_payload(payload)
        dossier_anchors = _dossier_anchors(payload)
        sections = [
            "<!doctype html>",
            '<html lang="ko">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            "<title>Signal Intelligence Report</title>",
            "<style>",
            _stylesheet(),
            "</style>",
            "</head>",
            "<body>",
            '<header class="report-header">',
            '<p class="eyebrow">HTML Research Report</p>',
            "<h1>Signal Intelligence Report</h1>",
            f"<p>{_h(payload.executive_summary.headline)}</p>",
            _metadata_grid(payload),
            _top_nav(),
            "</header>",
            "<main>",
            self._executive_summary(payload),
            self._signal_board(payload, dossier_anchors=dossier_anchors),
            self._ticker_dossiers(payload),
            self._evidence_ledger(payload, evidence_display=evidence_display),
            self._etf_follow_sheets(payload),
            self._market_map(payload),
            self._data_quality(payload, model_commentary=commentary),
            self._methodology(payload),
            "</main>",
            "<script>",
            _signal_board_script(),
            "</script>",
            "</body>",
            "</html>",
        ]
        return "\n".join(sections) + "\n"

    def _executive_summary(self, payload: SignalReportPayload) -> str:
        cards = [
            ("ETF count", _integer(payload.coverage.etf_count)),
            ("ETF brand count", _integer(payload.coverage.brand_count)),
            ("Securities count", _integer(payload.coverage.securities_count)),
            ("Mapped security ratio", _ratio(payload.coverage.mapped_security_ratio)),
            ("Price coverage ratio", _ratio(payload.coverage.price_coverage_ratio)),
            ("News coverage ratio", _ratio(payload.coverage.news_coverage_ratio)),
            ("Analyst coverage", _ratio(payload.coverage.analyst_coverage_ratio)),
            ("Cash position", _mapping_summary(payload.market_map.cash_position)),
            ("Concentration", _mapping_summary(payload.market_map.concentration)),
            ("Crowding", _mapping_summary(payload.market_map.crowding)),
        ]
        return "\n".join(
            [
                '<section id="executive-summary">',
                "<h2>Executive Summary</h2>",
                f"<p>{_h(payload.executive_summary.market_read)}</p>",
                '<div class="summary-cards">',
                *(
                    '<article class="summary-card">'
                    f"<h3>{_h(label)}</h3><p>{_h(value)}</p></article>"
                    for label, value in cards
                ),
                "</div>",
                "<h3>Top Takeaways</h3>",
                _list(payload.executive_summary.top_takeaways),
                "<h3>Primary Risks</h3>",
                _list(payload.executive_summary.primary_risks),
                "</section>",
            ]
        )

    def _signal_board(
        self,
        payload: SignalReportPayload,
        *,
        dossier_anchors: Mapping[str, str],
    ) -> str:
        has_signals = bool(payload.signal_board)
        disabled = " disabled" if not has_signals else ""
        table_open = (
            '<table class="signal-board-table">'
            if has_signals
            else '<table class="signal-board-table" hidden>'
        )
        lines = [
            '<section id="signal-board">',
            "<h2>Signal Board</h2>",
            f"<noscript>{NOSCRIPT_MESSAGE}</noscript>",
            '<div class="signal-controls">',
            '<label for="signal-search">Search</label>',
            f'<input id="signal-search"{disabled} type="search" '
            'placeholder="Ticker or name">',
            '<label for="review-filter">Review</label>',
            f'<select id="review-filter"{disabled}>',
            _bilingual_option("all", "All", "\uc804\uccb4"),
            *(
                _bilingual_option(value, value, korean)
                for value, korean in REVIEW_FILTER_OPTIONS
            ),
            "</select>",
            '<label for="evidence-filter">Evidence</label>',
            f'<select id="evidence-filter"{disabled}>',
            _bilingual_option("all", "All", "\uc804\uccb4"),
            *(
                _bilingual_option(value, value, korean)
                for value, korean in EVIDENCE_FILTER_OPTIONS
            ),
            "</select>",
            '<label for="sort-by">Sort</label>',
            f'<select id="sort-by"{disabled}>',
            '<option value="rank">rank</option>',
            '<option value="signal_score">signal_score</option>',
            '<option value="ticker">ticker</option>',
            "</select>",
            '<label for="sort-direction">Direction</label>',
            f'<select id="sort-direction"{disabled}>',
            '<option value="asc">ascending</option>',
            '<option value="desc">descending</option>',
            "</select>",
            "</div>",
            f'<p id="signal-empty-message" class="empty-state" hidden>{NO_MATCH_MESSAGE}</p>',
        ]
        if not has_signals:
            lines.append(f'<p class="empty-state">{NO_SIGNAL_MESSAGE}</p>')
        lines.extend(
            [
                table_open,
                "<thead><tr>",
                "<th>Rank</th>",
                "<th>Ticker/Name</th>",
                "<th>Direction</th>",
                "<th>Signal Type</th>",
                "<th>ETF Count</th>",
                "<th>Weight \u0394 pp</th>",
                "<th>Net Flow KRW</th>",
                "<th>Score</th>",
                "<th>Evidence</th>",
                "<th>Review</th>",
                "<th>Primary Reason</th>",
                "</tr></thead>",
                '<tbody data-signal-board-body="true">',
            ]
        )
        for signal in payload.signal_board:
            lines.extend(
                self._signal_board_rows(signal, dossier_anchors=dossier_anchors)
            )
        lines.extend(["</tbody>", "</table>", "</section>"])
        return "\n".join(lines)

    def _signal_board_rows(
        self,
        signal: SignalBoardRow,
        *,
        dossier_anchors: Mapping[str, str],
    ) -> list[str]:
        key = f"rank-{signal.rank}"
        ticker = display_ticker(signal.ticker)
        search_text = " ".join(
            (
                ticker,
                signal.name,
                signal.signal_direction,
                signal.signal_type,
                signal.evidence_grade,
                signal.review_label,
                signal.primary_reason,
            )
        ).lower()
        ticker_cell = _h(ticker)
        if signal.ticker is not None and signal.ticker in dossier_anchors:
            ticker_cell = f'<a href="#{_attr(dossier_anchors[signal.ticker])}">{ticker_cell}</a>'
        row = [
            '<tr class="signal-row" '
            f'data-signal-key="{_attr(key)}" '
            f'data-rank="{signal.rank}" '
            f'data-ticker="{_attr(ticker)}" '
            f'data-score="{signal.signal_score}" '
            f'data-review-label="{_attr(signal.review_label)}" '
            f'data-evidence-grade="{_attr(signal.evidence_grade)}" '
            f'data-search-text="{_attr(search_text)}">',
            f"<td>{signal.rank}</td>",
            f"<td><strong>{ticker_cell}</strong><br>{_h(signal.name)}</td>",
            f"<td>{_h(signal.display.signal_direction)}</td>",
            f"<td>{_h(signal.signal_type)}</td>",
            f"<td>{len(signal.participating_etfs)}</td>",
            f"<td>{_optional_decimal(signal.weight_delta_pp)}</td>",
            f"<td>{_optional_krw(signal.net_flow_estimate_krw)}</td>",
            f"<td>{signal.signal_score}</td>",
            f"<td>{_h(signal.evidence_grade)}<br>{_h(signal.display.evidence_grade)}</td>",
            f"<td>{_h(signal.review_label)}<br>{_h(signal.display.review_label)}</td>",
            f"<td>{_h(signal.primary_reason)}</td>",
            "</tr>",
            f'<tr class="score-components-row" data-signal-key="{_attr(key)}">',
            '<td colspan="11">',
            '<details class="score-components">',
            "<summary>Score Components</summary>",
            "<table><tbody>",
        ]
        for name, value in signal.score_components.model_dump(mode="json").items():
            row.append(f"<tr><th>{_h(name)}</th><td>{_h(value)}</td></tr>")
        row.extend(
            [
                "</tbody></table>",
                "</details>",
                "</td>",
                "</tr>",
            ]
        )
        return row

    def _ticker_dossiers(self, payload: SignalReportPayload) -> str:
        lines = ['<section id="ticker-dossiers">', "<h2>Ticker Dossiers</h2>"]
        if not payload.ticker_dossiers:
            lines.append('<p class="empty-state">None</p>')
        for index, dossier in enumerate(payload.ticker_dossiers):
            rank = _dossier_rank(payload, dossier=dossier, index=index)
            dossier_id = _dossier_id(dossier, rank=rank)
            open_attr = " open" if rank <= 3 else ""
            facts = dossier.holding_facts
            lines.extend(
                [
                    f'<article class="dossier-card" id="{_attr(dossier_id)}">',
                    f'<details class="dossier-details"{open_attr}>',
                    f"<summary>Ticker Dossier: {_h(display_ticker(dossier.ticker))} - "
                    f"{_h(dossier.name)}</summary>",
                    f"<p>{_h(dossier.summary)}</p>",
                    "<h3>Holding Facts</h3>",
                    "<ul>",
                    f"<li>Participating ETFs: {facts.participating_etfs}</li>",
                    f"<li>ETF IDs: {_h(_join_or_none(facts.participating_etf_ids))}</li>",
                    f"<li>Weight delta pp: {_optional_decimal(facts.weight_delta_pp)}</li>",
                    "<li>Holding delta shares: "
                    f"{_optional_decimal(facts.holding_delta_shares)}</li>",
                    f"<li>Net flow KRW: {_optional_krw(facts.net_flow_estimate_krw)}</li>",
                    "</ul>",
                    "<h3>Why Now</h3>",
                    f"<p>{_h(dossier.why_now_hypothesis)}</p>",
                    "<h3>Supporting Evidence</h3>",
                    _list(dossier.supporting_evidence),
                    "<h3>Counter Evidence</h3>",
                    _list(dossier.counter_evidence),
                    "<h3>Invalidation Conditions</h3>",
                    _list(dossier.invalidation_conditions),
                    "<h3>Final Label</h3>",
                    f"<p>{_h(dossier.final_label)} / {_h(dossier.display.get('final_label'))}</p>",
                    "</details>",
                    "</article>",
                ]
            )
        lines.append("</section>")
        return "\n".join(lines)

    def _evidence_ledger(
        self,
        payload: SignalReportPayload,
        *,
        evidence_display: EvidenceDisplayReference,
    ) -> str:
        lines = [
            '<section id="evidence-ledger">',
            "<h2>Evidence Ledger</h2>",
            '<table class="evidence-table">',
            "<thead><tr>",
            "<th>Title</th>",
            "<th>Source</th>",
            "<th>Type</th>",
            "<th>Role</th>",
            "<th>Stance</th>",
            "<th>Strength</th>",
            "<th>Claim</th>",
            "<th>Used In</th>",
            "<th>Link</th>",
            "</tr></thead>",
            "<tbody>",
        ]
        for item in payload.evidence_ledger:
            lines.append(_evidence_row(item, evidence_display=evidence_display))
        lines.extend(["</tbody>", "</table>", "</section>"])
        return "\n".join(lines)

    def _etf_follow_sheets(self, payload: SignalReportPayload) -> str:
        lines = ['<section id="etf-follow-sheets">', "<h2>ETF Follow Sheets</h2>"]
        if payload.etf_follow_sheets:
            lines.append('<nav class="anchor-list">')
            for sheet in payload.etf_follow_sheets:
                lines.append(
                    f'<a href="#{_attr(_etf_id(sheet))}">{_h(sheet.etf_id)}</a>'
                )
            lines.append("</nav>")
        else:
            lines.append('<p class="empty-state">None</p>')
        for sheet in payload.etf_follow_sheets:
            lines.extend(_etf_sheet(sheet))
        lines.append("</section>")
        return "\n".join(lines)

    def _market_map(self, payload: SignalReportPayload) -> str:
        return "\n".join(
            [
                '<section id="market-map">',
                "<h2>Market Map</h2>",
                _market_map_table("By Theme", payload.market_map.by_theme),
                _market_map_table("By Sector", payload.market_map.by_sector),
                _market_map_table("By Country", payload.market_map.by_country),
                '<div class="summary-cards">',
                _summary_card("Cash position", _mapping_summary(payload.market_map.cash_position)),
                _summary_card("Concentration", _mapping_summary(payload.market_map.concentration)),
                _summary_card("Crowding", _mapping_summary(payload.market_map.crowding)),
                "</div>",
                "</section>",
            ]
        )

    def _data_quality(
        self,
        payload: SignalReportPayload,
        *,
        model_commentary: str | None,
    ) -> str:
        lines = [
            '<section id="data-quality">',
            "<h2>Data Quality</h2>",
            f"<p><strong>Overall:</strong> {_h(payload.data_quality.overall)}</p>",
            "<h3>Limitations</h3>",
            _list(payload.data_quality.limitations),
            "<h3>Issues</h3>",
        ]
        if payload.data_quality.issues:
            lines.extend(
                "<p>"
                f"{_h(issue.code)} ({_h(issue.severity)}) {_h(issue.scope)}: "
                f"{_h(issue.message)}</p>"
                for issue in payload.data_quality.issues
            )
        else:
            lines.append('<p class="empty-state">None</p>')
        lines.extend(["<h3>Coverage Notes</h3>", _list(payload.data_quality.coverage_notes)])
        if model_commentary:
            lines.extend(
                [
                    '<section class="model-commentary">',
                    "<h3>Model Commentary</h3>",
                    f"<p>{_h(model_commentary)}</p>",
                    "</section>",
                ]
            )
        lines.append("</section>")
        return "\n".join(lines)

    def _methodology(self, payload: SignalReportPayload) -> str:
        return "\n".join(
            [
                '<section id="methodology">',
                "<h2>Methodology</h2>",
                f"<p><strong>Analysis mode:</strong> {_h(payload.methodology.analysis_mode)}</p>",
                "<p><strong>Scoring version:</strong> "
                f"{_h(payload.methodology.scoring_version)}</p>",
                _mapping_table("Score Components", payload.methodology.score_components),
                _mapping_table("Review Label Meanings", payload.methodology.review_labels),
                _mapping_table("Evidence Grade Meanings", payload.methodology.evidence_grades),
                f"<p><strong>Limitations policy:</strong> "
                f"{_h(payload.methodology.limitations_policy)}</p>",
                "</section>",
            ]
        )


def _top_nav() -> str:
    links = (
        ("Summary", "#executive-summary"),
        ("Signal Board", "#signal-board"),
        ("Dossiers", "#ticker-dossiers"),
        ("Evidence", "#evidence-ledger"),
        ("ETF Sheets", "#etf-follow-sheets"),
        ("Market Map", "#market-map"),
        ("Data Quality", "#data-quality"),
        ("Methodology", "#methodology"),
    )
    return "\n".join(
        [
            '<nav class="top-nav">',
            *(f'<a href="{href}">{label}</a>' for label, href in links),
            "</nav>",
        ]
    )


def _metadata_grid(payload: SignalReportPayload) -> str:
    period = payload.meta.period
    entries = (
        ("As of", payload.meta.as_of_date),
        ("Current period", period.get("current")),
        ("Previous period", period.get("previous")),
        ("Lookback days", period.get("lookback_days")),
        ("Universe", payload.meta.universe),
        ("Report version", payload.meta.report_version),
        ("Scoring version", payload.meta.scoring_version),
    )
    return "\n".join(
        [
            '<dl class="metadata-grid">',
            *(
                f"<div><dt>{_h(label)}</dt><dd>{_h(_metadata_value(value))}</dd></div>"
                for label, value in entries
            ),
            "</dl>",
        ]
    )


def _metadata_value(value: object) -> str:
    if value is None:
        return "n/a"
    return str(value)


def _bilingual_option(value: str, english: str, korean: str) -> str:
    return f'<option value="{_attr(value)}">{_h(english)} / {_h(korean)}</option>'


def _model_commentary_text(model_commentary: str | None) -> str | None:
    if not model_commentary:
        return None
    commentary_policy = evaluate_model_commentary(model_commentary)
    if commentary_policy.status == "omitted":
        return OMITTED_MODEL_COMMENTARY_MESSAGE
    return commentary_policy.text or None


def _dossier_anchors(payload: SignalReportPayload) -> dict[str, str]:
    anchors: dict[str, str] = {}
    for index, dossier in enumerate(payload.ticker_dossiers):
        if dossier.ticker is None:
            continue
        rank = _dossier_rank(payload, dossier=dossier, index=index)
        anchors[dossier.ticker] = _dossier_id(dossier, rank=rank)
    return anchors


def _dossier_rank(
    payload: SignalReportPayload,
    *,
    dossier: TickerDossier,
    index: int,
) -> int:
    if dossier.ticker is not None:
        for signal in payload.signal_board:
            if signal.ticker == dossier.ticker:
                return signal.rank
    return index + 1


def _dossier_id(dossier: TickerDossier, *, rank: int) -> str:
    if dossier.ticker is None:
        return f"dossier-rank-{rank}"
    slug = safe_ascii_slug(dossier.ticker, fallback=str(rank))
    return f"dossier-{slug}-{rank}"


def _evidence_row(
    item: EvidenceLedgerItem,
    *,
    evidence_display: EvidenceDisplayReference,
) -> str:
    claim = evidence_display.claim_scope(item.claim_scope)
    used_in = evidence_display.used_in(item.used_in)
    return "\n".join(
        [
            "<tr>",
            f"<td>{_h(item.title)}</td>",
            f"<td>{_h(item.source)}</td>",
            f"<td>{_h(item.type)}</td>",
            f"<td>{_h(item.evidence_role)}</td>",
            f"<td>{_h(item.stance)}</td>",
            f"<td>{_h(item.strength)}</td>",
            f"<td>{_h(claim)}</td>",
            f"<td>{_h(used_in)}</td>",
            f"<td>{_evidence_link(item.url)}</td>",
            "</tr>",
        ]
    )


def _evidence_link(url: str | None) -> str:
    safe_url = _safe_http_url(url)
    if safe_url is None:
        return _h(NO_LINK_MESSAGE)
    return (
        f'<a href="{_attr(safe_url)}" target="_blank" rel="noopener noreferrer">'
        "Open / \uc5f4\uae30</a>"
    )


def _safe_http_url(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return url


def _etf_sheet(sheet: ETFFollowSheet) -> list[str]:
    lines = [
        f'<article class="etf-card" id="{_attr(_etf_id(sheet))}">',
        '<details class="etf-details">',
        f"<summary>{_h(sheet.etf_id)} - {_h(sheet.etf_name)}</summary>",
        "<ul>",
        f"<li>ETF Brand ID: {_h(sheet.brand_id)}</li>",
        f"<li>Source Provider ID: {_h(sheet.source_provider_id)}</li>",
        f"<li>Focus ETF: {_h(sheet.is_focus)}</li>",
        f"<li>Top Holdings: {_h(_join_limited_or_none(sheet.top_holdings))}</li>",
        f"<li>New Positions: {_h(_join_limited_or_none(sheet.new_positions))}</li>",
        f"<li>Exited Positions: {_h(_join_limited_or_none(sheet.exited_positions))}</li>",
        f"<li>Increased Positions: {_h(_join_limited_or_none(sheet.increased_positions))}</li>",
        f"<li>Decreased Positions: {_h(_join_limited_or_none(sheet.decreased_positions))}</li>",
        f"<li>Cash Change pp: {_optional_decimal(sheet.cash_change_pp)}</li>",
        f"<li>ETF Brand Behavior Read: {_h(sheet.brand_behavior_read)}</li>",
        f"<li>Data Quality: {_h(_mapping_summary(sheet.data_quality))}</li>",
        "</ul>",
        _market_map_table(
            "Theme Exposure Changes",
            sheet.theme_exposure_changes,
            category_label="Theme",
        ),
        "</details>",
        "</article>",
    ]
    return lines


def _etf_id(sheet: ETFFollowSheet) -> str:
    return f"etf-{safe_ascii_slug(sheet.etf_id, fallback='sheet')}"


def _market_map_table(
    title: str,
    rows: tuple[MarketMapSlice, ...],
    *,
    category_label: str = "Category",
) -> str:
    if not rows:
        return f"<h3>{_h(title)}</h3>\n<p class=\"empty-state\">None</p>"
    lines = [
        f"<h3>{_h(title)}</h3>",
        "<table>",
        "<thead><tr>",
        f"<th>{_h(category_label)}</th>",
        "<th>Weight \u0394 pp</th>",
        "<th>Net Flow KRW</th>",
        "<th>Signal Count</th>",
        "</tr></thead>",
        "<tbody>",
    ]
    for row in rows:
        lines.extend(
            [
                "<tr>",
                f"<td>{_h(row.key)}</td>",
                f"<td>{_optional_decimal(row.weight_delta_pp)}</td>",
                f"<td>{_optional_krw(row.net_flow_estimate_krw)}</td>",
                f"<td>{row.signal_count}</td>",
                "</tr>",
            ]
        )
    lines.extend(["</tbody>", "</table>"])
    return "\n".join(lines)


def _mapping_table(title: str, values: Mapping[str, str]) -> str:
    lines = [
        f"<h3>{_h(title)}</h3>",
        "<table>",
        "<thead><tr><th>Key</th><th>Meaning</th></tr></thead>",
        "<tbody>",
    ]
    for key, value in values.items():
        lines.append(f"<tr><td>{_h(key)}</td><td>{_h(value)}</td></tr>")
    lines.extend(["</tbody>", "</table>"])
    return "\n".join(lines)


def _summary_card(label: str, value: str) -> str:
    return (
        '<article class="summary-card">'
        f"<h3>{_h(label)}</h3><p>{_h(value)}</p></article>"
    )


def _list(values: Iterable[str]) -> str:
    rendered = tuple(values)
    if not rendered:
        return '<p class="empty-state">None</p>'
    return "\n".join(
        ["<ul>", *(f"<li>{_h(value)}</li>" for value in rendered), "</ul>"]
    )


def _mapping_summary(values: Mapping[str, JsonValue]) -> str:
    if not values:
        return "None"
    return "; ".join(f"{key}={value}" for key, value in values.items())


def _join_limited_or_none(values: tuple[str, ...]) -> str:
    return ", ".join(values[:5]) if values else "None"


def _join_or_none(values: tuple[str, ...]) -> str:
    return ", ".join(values) if values else "None"


def _ratio(value: float | None) -> str:
    if value is None:
        return "not run"
    return f"{value * 100:.0f}%"


def _integer(value: int) -> str:
    return f"{value:,}"


def _optional_decimal(value: float | int | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:,.2f}"


def _optional_krw(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:,.0f}"


def _h(value: object) -> str:
    return escape("" if value is None else str(value), quote=True)


def _attr(value: object) -> str:
    return _h(value)


def _stylesheet() -> str:
    return """
:root {
  color: #202124;
  background: #f7f7f4;
  font-family: Arial, Helvetica, sans-serif;
}
* { box-sizing: border-box; }
body { margin: 0; line-height: 1.5; }
.report-header, main { width: min(1180px, calc(100% - 32px)); margin: 0 auto; }
.report-header { padding: 32px 0 16px; }
.eyebrow { margin: 0 0 8px; color: #6f4e2f; font-weight: 700; }
h1, h2, h3 { line-height: 1.2; letter-spacing: 0; }
h1 { margin: 0 0 8px; font-size: 2.25rem; }
h2 { margin-top: 32px; border-bottom: 2px solid #d6d2c8; padding-bottom: 8px; }
h3 { margin-top: 20px; }
a { color: #0b5cad; }
.metadata-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 8px 12px;
  margin: 16px 0;
}
.metadata-grid div {
  border-top: 1px solid #d6d2c8;
  padding-top: 8px;
}
.metadata-grid dt {
  color: #5f625f;
  font-size: 0.82rem;
  font-weight: 700;
}
.metadata-grid dd {
  margin: 2px 0 0;
  font-weight: 700;
}
.top-nav, .anchor-list, .signal-controls {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 12px;
  align-items: center;
}
.top-nav a, .anchor-list a {
  padding: 4px 0;
  font-weight: 700;
}
section { padding: 8px 0 20px; }
.summary-cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 10px;
}
.summary-card, .dossier-card, .etf-card {
  border: 1px solid #d6d2c8;
  border-radius: 8px;
  background: #fff;
  padding: 12px;
}
.summary-card h3 { margin: 0 0 6px; font-size: 0.95rem; color: #4f5d2f; }
.summary-card p { margin: 0; }
.signal-controls {
  margin: 16px 0;
  padding: 12px;
  border: 1px solid #d6d2c8;
  border-radius: 8px;
  background: #fff;
}
input, select {
  min-height: 34px;
  border: 1px solid #aaa59a;
  border-radius: 6px;
  padding: 4px 8px;
  background: #fff;
}
table {
  width: 100%;
  border-collapse: collapse;
  margin: 12px 0;
  background: #fff;
}
th, td {
  border: 1px solid #d6d2c8;
  padding: 8px;
  vertical-align: top;
  text-align: left;
}
th { background: #ece8de; }
.signal-board-table th, .signal-board-table td { font-size: 0.92rem; }
.score-components-row td { background: #fbfaf7; }
details summary { cursor: pointer; font-weight: 700; }
.empty-state { color: #6a655d; font-style: italic; }
.model-commentary { border-top: 1px solid #d6d2c8; margin-top: 16px; }
@media (max-width: 760px) {
  .report-header, main { width: min(100% - 20px, 1180px); }
  table { display: block; overflow-x: auto; }
  .signal-controls { align-items: stretch; }
  input, select { flex: 1 1 160px; }
}
@media print {
  body { background: #fff; }
  .signal-controls, script { display: none; }
  a { color: inherit; text-decoration: none; }
}
""".strip()


def _signal_board_script() -> str:
    return """
(function () {
  var tbody = document.querySelector("[data-signal-board-body]");
  if (!tbody) { return; }
  var search = document.getElementById("signal-search");
  var review = document.getElementById("review-filter");
  var evidence = document.getElementById("evidence-filter");
  var sortBy = document.getElementById("sort-by");
  var sortDirection = document.getElementById("sort-direction");
  var empty = document.getElementById("signal-empty-message");

  function pairs() {
    return Array.prototype.map.call(tbody.querySelectorAll("tr.signal-row"), function (row) {
      return { row: row, detail: row.nextElementSibling };
    });
  }

  function valueFor(pair, key) {
    if (key === "rank") { return Number(pair.row.dataset.rank || 0); }
    if (key === "signal_score") { return Number(pair.row.dataset.score || 0); }
    return (pair.row.dataset.ticker || "").toLowerCase();
  }

  function matches(pair) {
    var query = (search && search.value ? search.value : "").trim().toLowerCase();
    var reviewValue = review ? review.value : "all";
    var evidenceValue = evidence ? evidence.value : "all";
    var text = pair.row.dataset.searchText || "";
    if (query && text.indexOf(query) === -1) { return false; }
    if (reviewValue !== "all" && pair.row.dataset.reviewLabel !== reviewValue) { return false; }
    if (evidenceValue !== "all" && pair.row.dataset.evidenceGrade !== evidenceValue) {
      return false;
    }
    return true;
  }

  function apply() {
    var key = sortBy ? sortBy.value : "rank";
    var direction = sortDirection ? sortDirection.value : "asc";
    var visible = 0;
    var sorted = pairs().sort(function (left, right) {
      var a = valueFor(left, key);
      var b = valueFor(right, key);
      if (a < b) { return direction === "asc" ? -1 : 1; }
      if (a > b) { return direction === "asc" ? 1 : -1; }
      return Number(left.row.dataset.rank || 0) - Number(right.row.dataset.rank || 0);
    });
    sorted.forEach(function (pair) {
      var show = matches(pair);
      pair.row.hidden = !show;
      if (pair.detail) { pair.detail.hidden = !show; }
      if (show) { visible += 1; }
      tbody.appendChild(pair.row);
      if (pair.detail) { tbody.appendChild(pair.detail); }
    });
    if (empty) { empty.hidden = visible !== 0; }
  }

  [search, review, evidence, sortDirection].forEach(function (control) {
    if (control) { control.addEventListener("input", apply); }
    if (control) { control.addEventListener("change", apply); }
  });
  if (sortBy) {
    sortBy.addEventListener("change", function () {
      if (sortDirection) {
        sortDirection.value = sortBy.value === "signal_score" ? "desc" : "asc";
      }
      apply();
    });
  }
  apply();
})();
""".strip()
