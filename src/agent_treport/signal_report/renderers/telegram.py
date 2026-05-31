from __future__ import annotations

from html import escape

from agent_treport.signal_report.domain.payload import SignalBoardRow, SignalReportPayload
from agent_treport.signal_report.renderers.display import display_ticker

NO_SIGNAL_ALERT_MESSAGE = "의미 있는 ETF 보유 변화 신호가 발견되지 않았습니다."
NO_ADDITIONAL_LIMITATION_MESSAGE = "추가 제한 사항 없음"


class TelegramSignalAlertRenderer:
    def render(
        self,
        *,
        payload: SignalReportPayload,
        full_report_reference: str,
    ) -> str:
        signals = tuple(sorted(payload.signal_board, key=lambda signal: signal.rank)[:5])
        lines = [
            "<b>ETF 시그널 브리핑</b>",
            (
                f"<code>{_h(payload.meta.as_of_date)}</code> | "
                f"ETF {payload.coverage.etf_count}개 | {_h(payload.meta.universe)}"
            ),
            "<b>오늘의 핵심</b>",
            _headline(payload, signals=signals),
            f"<b>핵심 신호 Top {len(signals)}</b>",
        ]
        if signals:
            for signal in signals:
                lines.extend(_signal_lines(signal))
        else:
            lines.append(f"- {_h(NO_SIGNAL_ALERT_MESSAGE)}")
        lines.extend(
            [
                "<b>데이터 품질</b>",
                _data_quality_line(payload),
                "<b>전체 리포트</b>",
                f"HTML artifact: <code>{_h(full_report_reference)}</code>",
            ]
        )
        return "\n".join(lines)


def _headline(payload: SignalReportPayload, *, signals: tuple[SignalBoardRow, ...]) -> str:
    if not signals:
        return _h(payload.executive_summary.headline)
    top_signal = signals[0]
    subject = top_signal.ticker or top_signal.name
    return (
        f"{_h(subject)}는 {len(top_signal.participating_etfs)}개 ETF에서 "
        f"{_h(top_signal.display.signal_direction)}가 겹쳐 "
        f"{_h(top_signal.display.review_label)} 신호로 분류됐습니다."
    )


def _signal_lines(signal: SignalBoardRow) -> list[str]:
    return [
        (
            f"{signal.rank}. <code>{_h(display_ticker(signal.ticker))}</code> | "
            f"{_h(signal.display.review_label)}({_h(signal.review_label)}) | "
            f"{_h(signal.display.evidence_grade)}({_h(signal.evidence_grade)}) | "
            f"{signal.signal_score}점"
        ),
        f"   {_h(signal.primary_reason)}",
    ]


def _data_quality_line(payload: SignalReportPayload) -> str:
    issue_count = len(payload.data_quality.issues)
    first_note = (
        payload.data_quality.limitations[0]
        if payload.data_quality.limitations
        else (
            payload.data_quality.coverage_notes[0]
            if payload.data_quality.coverage_notes
            else NO_ADDITIONAL_LIMITATION_MESSAGE
        )
    )
    return f"상태 {_h(payload.data_quality.overall)}; 이슈 {issue_count}개; {_h(first_note)}"


def _h(value: object) -> str:
    return escape("" if value is None else str(value), quote=True)
