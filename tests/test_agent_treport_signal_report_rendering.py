from __future__ import annotations

import re
from typing import Any, cast

import pytest

from agent_treport.signal_report import (
    MarkdownSignalReportRenderer,
    TelegramSignalAlertRenderer,
    build_signal_report_payload,
    load_fixture_signal_report_inputs,
)
from agent_treport.signal_report.domain.payload import SignalReportPayload
from agent_treport.signal_report.domain.snapshots import MultiETFHoldingsSnapshots


def test_markdown_signal_report_renderer_renders_payload_first_preview() -> None:
    fixture = load_fixture_signal_report_inputs()
    payload = build_signal_report_payload(
        snapshots=fixture.snapshots,
        focus_etf_id=fixture.focus_etf_id,
        evidence=fixture.evidence,
    )

    markdown = MarkdownSignalReportRenderer().render(
        payload=payload,
        model_commentary="모델 코멘터리: 점수와 라벨은 canonical payload를 그대로 설명합니다.",
    )

    assert "# Signal Intelligence Report" in markdown
    assert "## Executive Summary" in markdown
    assert "## Signal Board" in markdown
    assert "## Ticker Dossiers" in markdown
    assert "## Data Quality" in markdown
    assert "## Methodology" in markdown
    assert "## Model Commentary" in markdown
    assert "중점 모니터링" in markdown
    assert "| 1 | NVDA | NVIDIA Corp. | multi_etf_accumulation |" in markdown
    assert "| position_change_strength |" in markdown
    assert "모델 코멘터리" in markdown
    assert "매니저" not in markdown
    assert "운용진" not in markdown
    assert "ETF 브랜드" in markdown
    assert "운용역" in markdown
    assert "BUY" not in markdown
    assert "HOLD" not in markdown
    assert "SELL" not in markdown
    assert "목표가" not in markdown


def test_markdown_signal_report_renderer_renders_target_sections_in_order() -> None:
    payload = _fixture_payload()

    markdown = MarkdownSignalReportRenderer().render(payload=payload)

    headings = [
        "## Executive Summary",
        "## Signal Board",
        "## Market Map",
        "## ETF Follow Sheets",
        "## Ticker Dossiers",
        "## Evidence Ledger",
        "## Data Quality",
        "## Methodology",
    ]
    positions = [markdown.index(heading) for heading in headings]
    assert positions == sorted(positions)

    market_map = _section(markdown, "## Market Map", "## ETF Follow Sheets")
    assert "### By Theme" in market_map
    assert "### By Sector" in market_map
    assert "### By Country" in market_map
    assert "| Category | Weight Delta pp | Net Flow KRW | Signal Count |" in market_map
    assert "| AI infrastructure | 4.00 | 101000000.00 | 1 |" in market_map
    assert "| Information Technology | -0.70 | 58000000.00 | 3 |" in market_map
    assert "| US | 2.20 | 106000000.00 | 5 |" in market_map
    assert "- Cash position: weight_delta_pp=-0.8; read=현금성 비중 축소" in market_map
    assert (
        "- Concentration: top_signal_count=5; "
        "note=상위 신호가 AI 인프라와 플랫폼 테마에 집중되어 있습니다."
    ) in market_map
    assert (
        "- Crowding: multi_etf_signal_count=3; "
        "note=동일 방향 변화가 여러 ETF에서 반복될수록 crowding 가능성을 함께 봅니다."
    ) in market_map
    assert "Cloud platforms" not in market_map

    etf_sheets = _section(markdown, "## ETF Follow Sheets", "## Ticker Dossiers")
    assert etf_sheets.count("\n### ") == len(payload.etf_follow_sheets)
    assert "### etf_focus_ai - AI Innovation Active ETF" in etf_sheets
    assert "- ETF Brand ID: brand_alpha" in etf_sheets
    assert "- Source Provider ID: provider_fixture" in etf_sheets
    assert "- Focus ETF: True" in etf_sheets
    assert "- Top Holdings: NVDA, PLTR, TSLA, CASH, META" in etf_sheets
    assert "- Exited Positions: None" in etf_sheets
    assert "- Cash Change pp: -0.80" in etf_sheets
    assert "| Theme | Weight Delta pp | Net Flow KRW | Signal Count |" in etf_sheets

    evidence = _section(markdown, "## Evidence Ledger", "## Data Quality")
    assert "| Title | Source | Type | Stance | Strength | Claim | Used In |" in evidence
    evidence_rows = [
        line
        for line in evidence.splitlines()
        if line.startswith("| ")
        and not line.startswith("| Title ")
        and not line.startswith("| ---")
    ]
    assert len(evidence_rows) == len(payload.evidence_ledger)
    assert (
        "| NVDA 다중 ETF 비중 확대 신호 관측 | holdings_snapshot | holding_change | "
        "supporting | strong | NVDA 다중 ETF 비중 확대 신호 | "
        "NVDA 다중 ETF 비중 확대 신호, Ticker Dossier: NVDA |"
    ) in evidence
    assert "ev_holding_change_signal_NVDA_multi_etf_accumulation" not in evidence
    assert "https://example.com/nvda-earnings" not in evidence
    assert "signal:NVDA:multi_etf_accumulation" not in evidence


def test_markdown_signal_report_renderer_renders_target_section_fallbacks() -> None:
    payload = _payload_with_nested_updates(
        _fixture_payload(),
        {
            "market_map": {
                "by_theme": [],
                "by_sector": [],
                "by_country": [],
            },
            "etf_follow_sheets": [
                {
                    "top_holdings": ["A", "B", "C", "D", "E", "F"],
                    "new_positions": [],
                    "exited_positions": [],
                    "increased_positions": [],
                    "decreased_positions": [],
                    "theme_exposure_changes": [],
                }
            ],
            "evidence_ledger": [
                {
                    "claim_scope": None,
                    "used_in": [],
                },
                {
                    "claim_scope": "signal:UNKNOWN:missing",
                    "used_in": ["unsupported:UNKNOWN"],
                },
            ],
        },
    )

    markdown = MarkdownSignalReportRenderer().render(payload=payload)

    market_map = _section(markdown, "## Market Map", "## ETF Follow Sheets")
    assert "### By Theme\n\n- None" in market_map
    assert "### By Sector\n\n- None" in market_map
    assert "### By Country\n\n- None" in market_map

    first_etf = _section(
        markdown,
        "### etf_focus_ai - AI Innovation Active ETF",
        "### etf_cloud_disruption",
    )
    assert "- Top Holdings: A, B, C, D, E" in first_etf
    assert "A, B, C, D, E, F" not in first_etf
    assert "- New Positions: None" in first_etf
    assert "- Increased Positions: None" in first_etf
    assert "- Theme Exposure Changes: None" in first_etf

    evidence = _section(markdown, "## Evidence Ledger", "## Data Quality")
    assert "보고서 전체 근거" in evidence
    assert "관련 신호 미확인" in evidence
    assert "관련 위치 미확인" in evidence
    assert "미사용" in evidence
    assert "signal:UNKNOWN:missing" not in evidence
    assert "unsupported:UNKNOWN" not in evidence


def test_markdown_signal_board_renders_no_signal_message() -> None:
    fixture = load_fixture_signal_report_inputs()
    payload = build_signal_report_payload(
        snapshots=_snapshots_with_current_equal_to_previous(fixture.snapshots),
        focus_etf_id=fixture.focus_etf_id,
        evidence=fixture.evidence,
    )

    markdown = MarkdownSignalReportRenderer().render(payload=payload)

    assert "## Signal Board" in markdown
    assert "의미 있는 ETF 보유 변화 신호가 발견되지 않았습니다." in markdown


def test_markdown_omits_unsafe_investment_commentary() -> None:
    fixture = load_fixture_signal_report_inputs()
    payload = build_signal_report_payload(
        snapshots=fixture.snapshots,
        focus_etf_id=fixture.focus_etf_id,
        evidence=fixture.evidence,
    )

    markdown = MarkdownSignalReportRenderer().render(
        payload=payload,
        model_commentary="BUY rating with a target price of 500.",
    )

    assert "## Model Commentary" in markdown
    assert "모델 코멘터리는 report commentary policy 위반으로 생략되었습니다." in markdown
    assert "BUY rating" not in markdown
    assert "target price" not in markdown


def test_markdown_omits_canonical_conflict_commentary() -> None:
    fixture = load_fixture_signal_report_inputs()
    payload = build_signal_report_payload(
        snapshots=fixture.snapshots,
        focus_etf_id=fixture.focus_etf_id,
        evidence=fixture.evidence,
    )

    markdown = MarkdownSignalReportRenderer().render(
        payload=payload,
        model_commentary="The score should be adjusted and review_label should change.",
    )

    assert "모델 코멘터리는 report commentary policy 위반으로 생략되었습니다." in markdown
    assert "score should be adjusted" not in markdown
    assert "review_label should change" not in markdown


def test_markdown_keeps_safe_model_commentary() -> None:
    fixture = load_fixture_signal_report_inputs()
    payload = build_signal_report_payload(
        snapshots=fixture.snapshots,
        focus_etf_id=fixture.focus_etf_id,
        evidence=fixture.evidence,
    )

    markdown = MarkdownSignalReportRenderer().render(
        payload=payload,
        model_commentary="Payload commentary: the report explains the fixed signal board.",
    )

    assert "Payload commentary: the report explains the fixed signal board." in markdown
    assert "policy 위반" not in markdown


def test_telegram_signal_alert_renderer_renders_korean_first_contract() -> None:
    payload = _fixture_payload()

    alert = TelegramSignalAlertRenderer().render(
        payload=payload,
        full_report_reference="artifact_treport_html_report",
    )

    assert alert.startswith(
        "<b>ETF 시그널 브리핑</b>\n"
        "<code>2026-05-08</code> | ETF 3개 | fixture_active_etf_universe\n"
    )
    assert "<b>오늘의 핵심</b>" in alert
    assert (
        "NVDA는 3개 ETF에서 비중 확대가 겹쳐 중점 모니터링 신호로 분류됐습니다."
        in alert
    )
    assert "<b>핵심 신호 Top 5</b>" in alert
    assert (
        "1. <code>NVDA</code> | 중점 모니터링(focus) | 확인(Confirmed) | 81점"
        in alert
    )
    assert "   3개 ETF에서 같은 방향의 확대 신호가 확인됐습니다." in alert
    assert "<b>데이터 품질</b>" in alert
    assert (
        "상태 limited; 이슈 4개; "
        "fixture 기반 deterministic 분석이며 live enrichment는 포함하지 않습니다."
        in alert
    )
    assert "<b>전체 리포트</b>\nHTML artifact: <code>artifact_treport_html_report</code>" in alert


def test_telegram_signal_alert_renderer_uses_top_five_by_rank_without_label_filtering() -> None:
    payload = _payload_with_nested_updates(
        _fixture_payload(),
        {
            "signal_board": [
                {},
                {},
                {},
                {},
                {"rank": 6},
                {"rank": 5},
            ],
        },
    )

    alert = TelegramSignalAlertRenderer().render(
        payload=payload,
        full_report_reference="artifact_treport_html_report",
    )

    signal_lines = re.findall(r"^\d+\.\s*<code>", alert, flags=re.MULTILINE)
    assert len(signal_lines) == 5
    assert "5. <code>미확인</code> | 판단 유보(defer) | 활용 불가(Unusable) | 0점" in alert
    assert "6. <code>MSFT</code>" not in alert


def test_telegram_signal_alert_renderer_renders_no_signal_fallback() -> None:
    fixture = load_fixture_signal_report_inputs()
    payload = build_signal_report_payload(
        snapshots=_snapshots_with_current_equal_to_previous(fixture.snapshots),
        focus_etf_id=fixture.focus_etf_id,
        evidence=fixture.evidence,
    )

    alert = TelegramSignalAlertRenderer().render(
        payload=payload,
        full_report_reference="artifact_treport_html_report",
    )

    assert "<b>오늘의 핵심</b>" in alert
    assert payload.executive_summary.headline in alert
    assert "<b>핵심 신호 Top 0</b>\n- 의미 있는 ETF 보유 변화 신호가 발견되지 않았습니다." in alert
    assert "<b>데이터 품질</b>" in alert
    assert "HTML artifact: <code>artifact_treport_html_report</code>" in alert


def test_telegram_signal_alert_renderer_escapes_payload_text_and_creates_only_b_code_tags() -> None:
    payload = _payload_with_nested_updates(
        _fixture_payload(),
        {
            "meta": {
                "universe": "ETF <script>alert(1)</script>",
            },
            "signal_board": [
                {
                    "ticker": "NVDA<bad>",
                    "primary_reason": "A&B <script>alert(1)</script>",
                },
            ],
            "data_quality": {
                "limitations": ["Limit <img src=x> A&B"],
                "coverage_notes": ["Coverage <iframe>"],
            },
        },
    )

    alert = TelegramSignalAlertRenderer().render(
        payload=payload,
        full_report_reference="artifact_treport_html_report",
    )

    assert "<script" not in alert
    assert "<img" not in alert
    assert "<iframe" not in alert
    assert "ETF &lt;script&gt;alert(1)&lt;/script&gt;" in alert
    assert "<code>NVDA&lt;bad&gt;</code>" in alert
    assert "A&amp;B &lt;script&gt;alert(1)&lt;/script&gt;" in alert
    assert "Limit &lt;img src=x&gt; A&amp;B" in alert
    rendered_tags = {
        match.group(1).lower().lstrip("/")
        for match in re.finditer(r"<(/?\w+)", alert)
    }
    assert rendered_tags == {"b", "code"}


def test_telegram_signal_alert_renderer_does_not_accept_model_commentary() -> None:
    payload = _fixture_payload()
    render = cast(Any, TelegramSignalAlertRenderer().render)

    with pytest.raises(TypeError):
        render(
            payload=payload,
            full_report_reference="artifact_treport_html_report",
            model_commentary="do not render model text",
        )


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


def _section(markdown: str, start_heading: str, end_heading: str) -> str:
    start = markdown.index(start_heading)
    end = markdown.index(end_heading, start + len(start_heading))
    return markdown[start:end]


def _snapshots_with_current_equal_to_previous(
    snapshots: MultiETFHoldingsSnapshots,
) -> MultiETFHoldingsSnapshots:
    data = snapshots.model_dump(mode="json")
    for etf in data["etfs"]:
        etf["current"] = list(etf["previous"])
    return MultiETFHoldingsSnapshots.model_validate(data)
