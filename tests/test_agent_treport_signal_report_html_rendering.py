from __future__ import annotations

from html import escape

from agent_treport.signal_report import (
    HTMLResearchReportRenderer,
    build_signal_report_payload,
    load_fixture_signal_report_inputs,
)
from agent_treport.signal_report.domain.commentary_policy import (
    OMITTED_MODEL_COMMENTARY_MESSAGE,
)
from agent_treport.signal_report.domain.payload import SignalReportPayload
from agent_treport.signal_report.domain.snapshots import MultiETFHoldingsSnapshots

NO_MATCH_MESSAGE = "\uc870\uac74\uc5d0 \ub9de\ub294 \uc2e0\ud638\uac00 \uc5c6\uc2b5\ub2c8\ub2e4"
NO_SIGNAL_MESSAGE = (
    "\uc758\ubbf8 \uc788\ub294 ETF \ubcf4\uc720 \ubcc0\ud654 \uc2e0\ud638\uac00 "
    "\ubc1c\uacac\ub418\uc9c0 \uc54a\uc558\uc2b5\ub2c8\ub2e4."
)
NO_LINK_MESSAGE = "\ub9c1\ud06c \uc5c6\uc74c"
KOREAN_NOSCRIPT_MESSAGE = (
    "JavaScript\uac00 \uaebc\uc838 \uc788\uc5b4 \ud544\ud130\uc640 "
    "\uc815\ub82c\uc740 \uc0ac\uc6a9\ud560 \uc218 \uc5c6\uc9c0\ub9cc "
    "\uc804\uccb4 \ubcf4\uace0\uc11c \ub0b4\uc6a9\uc740 \ud45c\uc2dc\ub429\ub2c8\ub2e4."
)


def test_html_research_report_renderer_renders_fixture_exploration_surface() -> None:
    payload = _fixture_payload()

    rendered = HTMLResearchReportRenderer().render(
        payload=payload,
        model_commentary="Payload commentary: fixed signal board.",
    )

    assert rendered.endswith("</html>\n")
    assert '<meta charset="utf-8">' in rendered
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in rendered
    assert "<title>Signal Intelligence Report</title>" in rendered
    assert "<h1>Signal Intelligence Report</h1>" in rendered
    assert "HTML Research Report" in rendered
    assert "<style>" in rendered
    assert "<script>" in rendered
    assert "<noscript>" in rendered
    assert KOREAN_NOSCRIPT_MESSAGE in rendered
    assert NO_MATCH_MESSAGE in rendered
    assert '<dl class="metadata-grid">' in rendered
    for label, value in (
        ("As of", payload.meta.as_of_date),
        ("Current period", payload.meta.period["current"]),
        ("Previous period", payload.meta.period["previous"]),
        ("Lookback days", payload.meta.period["lookback_days"]),
        ("Universe", payload.meta.universe),
        ("Report version", payload.meta.report_version),
        ("Scoring version", payload.meta.scoring_version),
    ):
        assert f"<dt>{label}</dt><dd>{value}</dd>" in rendered
    assert payload.meta.report_id not in rendered
    assert payload.meta.generated_at not in rendered
    assert "<dt>Language</dt>" not in rendered
    assert "<dt>Report type</dt>" not in rendered

    section_ids = [
        "executive-summary",
        "signal-board",
        "ticker-dossiers",
        "evidence-ledger",
        "etf-follow-sheets",
        "market-map",
        "data-quality",
        "methodology",
    ]
    positions = [rendered.index(f'id="{section_id}"') for section_id in section_ids]
    assert positions == sorted(positions)

    for label, href in (
        ("Summary", "#executive-summary"),
        ("Signal Board", "#signal-board"),
        ("Dossiers", "#ticker-dossiers"),
        ("Evidence", "#evidence-ledger"),
        ("ETF Sheets", "#etf-follow-sheets"),
        ("Market Map", "#market-map"),
        ("Data Quality", "#data-quality"),
        ("Methodology", "#methodology"),
    ):
        assert f'<a href="{href}">{label}</a>' in rendered

    assert 'id="signal-search"' in rendered
    assert 'id="review-filter"' in rendered
    assert '<option value="all">All / \uc804\uccb4</option>' in rendered
    assert (
        '<option value="focus">focus / \uc911\uc810 \ubaa8\ub2c8\ud130\ub9c1</option>'
        in rendered
    )
    assert '<option value="monitor">monitor / \ubaa8\ub2c8\ud130\ub9c1</option>' in rendered
    assert '<option value="caution">caution / \uc720\uc758</option>' in rendered
    assert '<option value="defer">defer / \ud310\ub2e8 \uc720\ubcf4</option>' in rendered
    assert 'id="evidence-filter"' in rendered
    assert '<option value="Confirmed">Confirmed / \ud655\uc778</option>' in rendered
    assert '<option value="Plausible">Plausible / \ud0c0\ub2f9</option>' in rendered
    assert '<option value="Weak">Weak / \uc81c\ud55c\uc801</option>' in rendered
    assert '<option value="Conflicted">Conflicted / \uc0c1\ucda9</option>' in rendered
    assert '<option value="Unusable">Unusable / \ud65c\uc6a9 \ubd88\uac00</option>' in rendered
    assert 'id="sort-by"' in rendered
    assert 'value="signal_score"' in rendered
    assert 'id="sort-direction"' in rendered
    assert "<th>Rank</th>" in rendered
    assert "<th>Ticker/Name</th>" in rendered
    assert "<th>Direction</th>" in rendered
    assert "<th>Signal Type</th>" in rendered
    assert "<th>ETF Count</th>" in rendered
    assert "<th>Weight \u0394 pp</th>" in rendered
    assert "<th>Net Flow KRW</th>" in rendered
    assert "<th>Score</th>" in rendered
    assert "<th>Evidence</th>" in rendered
    assert "<th>Review</th>" in rendered
    assert "<th>Primary Reason</th>" in rendered

    first_signal = payload.signal_board[0]
    signal_marker = '<tr class="signal-row" data-signal-key="rank-1"'
    detail_marker = '<tr class="score-components-row" data-signal-key="rank-1"'
    second_signal_marker = '<tr class="signal-row" data-signal-key="rank-2"'
    assert signal_marker in rendered
    assert detail_marker in rendered
    assert rendered.index(signal_marker) < rendered.index(detail_marker)
    assert rendered.index(detail_marker) < rendered.index(second_signal_marker)
    assert f'data-rank="{first_signal.rank}"' in rendered
    assert 'data-ticker="NVDA"' in rendered
    assert f'data-score="{first_signal.signal_score}"' in rendered
    assert 'data-review-label="focus"' in rendered
    assert 'data-evidence-grade="Confirmed"' in rendered
    assert 'data-search-text=' in rendered
    assert '<td colspan="11">' in rendered
    assert '<details class="score-components">' in rendered

    assert 'id="dossier-nvda-1"' in rendered
    assert '<details class="dossier-details" open>' in rendered
    assert "Ticker Dossier: NVDA" in rendered
    assert "Holding Facts" in rendered
    assert "Invalidation Conditions" in rendered

    assert "<th>Title</th>" in rendered
    assert "<th>Source</th>" in rendered
    assert "<th>Type</th>" in rendered
    assert "<th>Role</th>" in rendered
    assert "<th>Stance</th>" in rendered
    assert "<th>Strength</th>" in rendered
    assert "<th>Claim</th>" in rendered
    assert "<th>Used In</th>" in rendered
    assert "<th>Link</th>" in rendered
    assert (
        '<a href="https://example.com/nvda-earnings" target="_blank" '
        'rel="noopener noreferrer">Open / \uc5f4\uae30</a>'
    ) in rendered
    assert payload.signal_board[0].claim_scope not in rendered
    assert "ticker_dossier:NVDA" not in rendered

    assert "Coverage" in rendered
    assert "ETF count" in rendered
    assert "Mapped security ratio" in rendered
    assert "Analyst coverage" in rendered
    assert "Analyst coverage ratio" not in rendered
    assert "not run" in rendered
    assert "Cash position" in rendered
    assert "Concentration" in rendered
    assert "Crowding" in rendered
    assert "Payload commentary: fixed signal board." in rendered
    assert rendered.index("Payload commentary: fixed signal board.") > rendered.index(
        'id="data-quality"'
    )
    assert rendered.index("Payload commentary: fixed signal board.") < rendered.index(
        'id="methodology"'
    )

    assert '<script type="application/json"' not in rendered
    assert 'id="report-payload"' not in rendered
    lowered = rendered.lower()
    for forbidden in ("<link ", "<script src=", "<img ", "<iframe ", "<object ", "<embed "):
        assert forbidden not in lowered


def test_html_research_report_renderer_sanitizes_unsafe_evidence_urls() -> None:
    payload = _payload_with_nested_updates(
        _fixture_payload(),
        {"evidence_ledger": [{"url": "javascript:alert(1)"}]},
    )

    rendered = HTMLResearchReportRenderer().render(payload=payload)

    assert "javascript:alert(1)" not in rendered
    assert NO_LINK_MESSAGE in rendered


def test_html_research_report_renderer_escapes_text_and_attribute_values() -> None:
    payload = _payload_with_nested_updates(
        _fixture_payload(),
        {
            "signal_board": [
                {
                    "ticker": 'NVDA" data-x="bad',
                    "name": "<script>alert(1)</script>",
                }
            ],
        },
    )

    rendered = HTMLResearchReportRenderer().render(payload=payload)

    assert "<script>alert(1)</script>" not in rendered
    assert "data-x=\"bad" not in rendered
    assert "NVDA&quot; data-x=&quot;bad" in rendered
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered


def test_html_research_report_renderer_omits_unsafe_model_commentary() -> None:
    rendered = HTMLResearchReportRenderer().render(
        payload=_fixture_payload(),
        model_commentary="BUY rating with a target price of 500.",
    )

    assert "BUY rating" not in rendered
    assert "target price" not in rendered
    assert escape(OMITTED_MODEL_COMMENTARY_MESSAGE) in rendered


def test_html_research_report_renderer_renders_no_signal_state() -> None:
    fixture = load_fixture_signal_report_inputs()
    payload = build_signal_report_payload(
        snapshots=_snapshots_with_current_equal_to_previous(fixture.snapshots),
        focus_etf_id=fixture.focus_etf_id,
        evidence=fixture.evidence,
    )

    rendered = HTMLResearchReportRenderer().render(payload=payload)

    assert '<section id="signal-board"' in rendered
    assert NO_SIGNAL_MESSAGE in rendered
    assert 'id="signal-search" disabled' in rendered
    assert 'id="review-filter" disabled' in rendered
    assert 'id="evidence-filter" disabled' in rendered
    assert 'id="sort-by" disabled' in rendered
    assert '<table class="signal-board-table" hidden>' in rendered
    assert 'class="signal-row"' not in rendered


def _fixture_payload() -> SignalReportPayload:
    fixture = load_fixture_signal_report_inputs()
    return build_signal_report_payload(
        snapshots=fixture.snapshots,
        focus_etf_id=fixture.focus_etf_id,
        evidence=fixture.evidence,
    )


def _payload_with_nested_updates(
    payload: SignalReportPayload,
    updates: dict[str, object],
) -> SignalReportPayload:
    data = payload.model_dump(mode="json")
    for section, section_updates in updates.items():
        if isinstance(section_updates, dict):
            data[section].update(section_updates)
            continue
        if isinstance(section_updates, list):
            for index, item_updates in enumerate(section_updates):
                data[section][index].update(item_updates)
            continue
        data[section] = section_updates
    return SignalReportPayload.model_validate(data)


def _snapshots_with_current_equal_to_previous(
    snapshots: MultiETFHoldingsSnapshots,
) -> MultiETFHoldingsSnapshots:
    data = snapshots.model_dump(mode="json")
    for etf in data["etfs"]:
        etf["current"] = list(etf["previous"])
    return MultiETFHoldingsSnapshots.model_validate(data)
