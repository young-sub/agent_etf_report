from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from math import isclose

from agent_pack.models import JsonValue

from agent_treport.signal_report.domain.evidence import (
    EvidenceItemInput,
    EvidenceStrength,
    ObservedDirection,
)
from agent_treport.signal_report.domain.investment_language_policy import (
    find_prohibited_investment_language,
)
from agent_treport.signal_report.domain.payload import (
    DataQuality,
    DataQualityIssue,
    ETFFollowSheet,
    EvidenceGrade,
    EvidenceLedgerItem,
    ExecutiveSummary,
    HoldingFacts,
    MarketMap,
    MarketMapSlice,
    Methodology,
    ReviewLabel,
    ScoreComponents,
    SignalBoardRow,
    SignalDisplay,
    SignalReportCoverage,
    SignalReportMeta,
    SignalReportPayload,
    TickerDossier,
)
from agent_treport.signal_report.domain.signals import (
    EVIDENCE_GRADE_DISPLAY,
    REVIEW_LABEL_DISPLAY,
    SIGNAL_DIRECTION_DISPLAY,
)
from agent_treport.signal_report.domain.snapshots import (
    ETFHoldingsSnapshots,
    MultiETFHoldingsSnapshots,
    SecurityHolding,
)

_GENERATED_AT = "2026-05-09T00:00:00+00:00"
_NO_SIGNAL_HEADLINE = "해당 기간에 의미 있는 ETF 보유 변화 신호가 발견되지 않았습니다."
_DATA_QUALITY_PENALTIES = {
    "missing_ticker": 20,
    "missing_classification": 8,
    "missing_price": 6,
}


@dataclass(frozen=True)
class _Change:
    etf_id: str
    security_id: str
    security_group_id: str | None
    member_security_ids: tuple[str, ...]
    listing_keys: tuple[str, ...]
    key: str
    ticker: str | None
    name: str
    market: str | None
    sector: str | None
    theme: str | None
    country: str | None
    previous_weight: float
    current_weight: float
    weight_delta: float
    previous_shares: float | None
    current_shares: float | None
    market_value_delta: float | None
    is_cash: bool
    data_quality_warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class _HoldingAggregate:
    key: str
    security_id: str
    security_group_id: str | None
    member_security_ids: tuple[str, ...]
    listing_keys: tuple[str, ...]
    ticker: str | None
    name: str
    market: str | None
    sector: str | None
    theme: str | None
    country: str | None
    weight_percent: float
    shares: float | None
    market_value_krw: float | None
    price_krw: float | None
    is_cash: bool
    data_quality_warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class _OperationalDataQualityProjection:
    issues: tuple[DataQualityIssue, ...]
    limitations: tuple[str, ...]
    coverage_notes: tuple[str, ...]


def build_signal_report_payload(
    *,
    snapshots: MultiETFHoldingsSnapshots,
    focus_etf_id: str | None = None,
    focus_etf_ids: tuple[str, ...] | None = None,
    evidence: tuple[EvidenceItemInput, ...] | None = None,
    operational_provenance: Mapping[str, JsonValue] | None = None,
) -> SignalReportPayload:
    evidence_items = () if evidence is None else evidence
    external_evidence_summary = _external_evidence_summary(operational_provenance)
    resolved_focus_etf_ids = _resolve_payload_focus_etf_ids(
        focus_etf_id=focus_etf_id,
        focus_etf_ids=focus_etf_ids,
    )
    changes = _build_changes(snapshots)
    issues = _build_data_quality_issues(snapshots)
    coverage = _build_coverage(
        snapshots=snapshots,
        evidence=evidence,
        external_evidence_summary=external_evidence_summary,
    )
    evidence_by_ticker = _evidence_by_ticker(evidence_items)
    evidence_by_claim = _evidence_by_claim(evidence_items)
    signal_rows = _build_signal_board(
        changes=changes,
        evidence_by_ticker=evidence_by_ticker,
        data_quality_issues=issues,
        focus_etf_id=focus_etf_id,
    )

    return SignalReportPayload(
        meta=SignalReportMeta(
            report_id=f"signal_report_{snapshots.current_date}",
            as_of_date=snapshots.as_of_date,
            period={
                "current": snapshots.current_date,
                "previous": snapshots.previous_date,
                "lookback_days": snapshots.lookback_days,
            },
            universe=snapshots.universe,
            report_type="weekly_etf_signal",
            language="ko",
            generated_at=_GENERATED_AT,
            report_version="signal_report_payload_v1",
            scoring_version="signal_score_v1",
            focus_etf_id=focus_etf_id,
            focus_etf_ids=resolved_focus_etf_ids,
        ),
        coverage=coverage,
        executive_summary=_build_executive_summary(signal_rows=signal_rows, coverage=coverage),
        signal_board=tuple(signal_rows),
        market_map=_build_market_map(changes),
        etf_follow_sheets=_build_etf_follow_sheets(
            snapshots=snapshots,
            changes=changes,
            focus_etf_id=focus_etf_id,
            focus_etf_ids=resolved_focus_etf_ids,
        ),
        ticker_dossiers=_build_ticker_dossiers(
            signal_rows=signal_rows,
            evidence_by_claim=evidence_by_claim,
        ),
        evidence_ledger=_build_evidence_ledger(evidence=evidence_items, signal_rows=signal_rows),
        methodology=_build_methodology(),
        data_quality=_build_data_quality(
            issues=issues,
            coverage=coverage,
            operational_provenance=operational_provenance,
        ),
    )


def _resolve_payload_focus_etf_ids(
    *,
    focus_etf_id: str | None,
    focus_etf_ids: tuple[str, ...] | None,
) -> tuple[str, ...]:
    if focus_etf_ids:
        return focus_etf_ids
    if focus_etf_id is not None:
        return (focus_etf_id,)
    return ()


def _build_changes(snapshots: MultiETFHoldingsSnapshots) -> tuple[_Change, ...]:
    changes: list[_Change] = []
    for etf in snapshots.etfs:
        previous = _holdings_by_identity(etf.previous)
        current = _holdings_by_identity(etf.current)
        for key in sorted(previous.keys() | current.keys()):
            before = previous.get(key)
            after = current.get(key)
            reference = after or before
            if reference is None:
                continue
            previous_weight = before.weight_percent if before is not None else 0.0
            current_weight = after.weight_percent if after is not None else 0.0
            weight_delta = round(current_weight - previous_weight, 4)
            if isclose(weight_delta, 0.0, abs_tol=0.0001):
                continue
            previous_value = before.market_value_krw if before is not None else None
            current_value = after.market_value_krw if after is not None else None
            market_value_delta = (
                None
                if previous_value is None or current_value is None
                else round(current_value - previous_value, 2)
            )
            changes.append(
                _Change(
                    etf_id=etf.etf_id,
                    security_id=reference.security_id,
                    security_group_id=reference.security_group_id,
                    member_security_ids=reference.member_security_ids,
                    listing_keys=reference.listing_keys,
                    key=key,
                    ticker=reference.ticker,
                    name=reference.name,
                    market=reference.market,
                    sector=reference.sector,
                    theme=reference.theme,
                    country=reference.country,
                    previous_weight=previous_weight,
                    current_weight=current_weight,
                    weight_delta=weight_delta,
                    previous_shares=before.shares if before is not None else None,
                    current_shares=after.shares if after is not None else None,
                    market_value_delta=market_value_delta,
                    is_cash=reference.is_cash,
                    data_quality_warnings=reference.data_quality_warnings,
                )
            )
    return tuple(changes)


def _holdings_by_identity(
    holdings: tuple[SecurityHolding, ...],
) -> dict[str, _HoldingAggregate]:
    grouped: dict[str, list[SecurityHolding]] = defaultdict(list)
    for holding in holdings:
        grouped[_holding_key(holding)].append(holding)
    return {
        key: _holding_aggregate(key=key, holdings=tuple(items))
        for key, items in grouped.items()
    }


def _holding_aggregate(
    *, key: str, holdings: tuple[SecurityHolding, ...]
) -> _HoldingAggregate:
    ordered = tuple(sorted(holdings, key=lambda item: item.security_id))
    reference = ordered[0]
    group_name = _first_text(item.security_group_name for item in ordered)
    group_ticker = _first_text(item.security_group_ticker for item in ordered)
    ticker = group_ticker or _first_text(item.ticker for item in ordered)
    warnings = (
        ("missing_security_group_display_label",)
        if reference.security_group_id is not None and group_name is None
        else ()
    )
    return _HoldingAggregate(
        key=key,
        security_id=reference.security_id,
        security_group_id=reference.security_group_id,
        member_security_ids=tuple(dict.fromkeys(item.security_id for item in ordered)),
        listing_keys=tuple(
            dict.fromkeys(
                item.listing_key for item in ordered if item.listing_key is not None
            )
        ),
        ticker=ticker,
        name=(
            group_name
            or _security_group_fallback_label(reference.security_group_id)
            or reference.name
        ),
        market=_first_text(item.market for item in ordered),
        sector=_first_text(item.sector for item in ordered),
        theme=_first_text(item.theme for item in ordered),
        country=_first_text(item.country for item in ordered),
        weight_percent=round(sum(item.weight_percent for item in ordered), 4),
        shares=_sum_optional(item.shares for item in ordered),
        market_value_krw=_sum_optional(item.market_value_krw for item in ordered),
        price_krw=_first_float(item.price_krw for item in ordered),
        is_cash=all(item.is_cash for item in ordered),
        data_quality_warnings=warnings,
    )


def _build_signal_board(
    *,
    changes: tuple[_Change, ...],
    evidence_by_ticker: dict[str, tuple[EvidenceItemInput, ...]],
    data_quality_issues: tuple[DataQualityIssue, ...],
    focus_etf_id: str | None,
) -> tuple[SignalBoardRow, ...]:
    grouped: dict[str, list[_Change]] = defaultdict(list)
    for change in changes:
        if not change.is_cash:
            grouped[change.key].append(change)

    unranked = [
        _signal_from_changes(
            key=key,
            changes=tuple(items),
            evidence=tuple(evidence_by_ticker.get(_ticker_key(items), ())),
            data_quality_penalty=_data_quality_penalty_for_changes(
                changes=tuple(items),
                issues=data_quality_issues,
            ),
            focus_etf_id=focus_etf_id,
        )
        for key, items in grouped.items()
    ]
    sorted_rows = sorted(
        unranked,
        key=lambda row: (
            -row.signal_score,
            row.ticker or "",
            row.name,
        ),
    )
    return tuple(
        row.model_copy(update={"rank": index})
        for index, row in enumerate(sorted_rows, start=1)
    )


def _data_quality_penalty_for_changes(
    *,
    changes: tuple[_Change, ...],
    issues: tuple[DataQualityIssue, ...],
) -> int:
    issue_codes_by_scope: dict[str, set[str]] = defaultdict(set)
    for issue in issues:
        issue_codes_by_scope[issue.scope].add(issue.code)

    issue_codes: set[str] = set()
    for change in changes:
        scope = f"{change.etf_id}:{change.security_id}"
        issue_codes.update(issue_codes_by_scope.get(scope, set()))

    return min(20, sum(_DATA_QUALITY_PENALTIES.get(code, 0) for code in issue_codes))


def _signal_from_changes(
    *,
    key: str,
    changes: tuple[_Change, ...],
    evidence: tuple[EvidenceItemInput, ...],
    data_quality_penalty: int,
    focus_etf_id: str | None,
) -> SignalBoardRow:
    reference = changes[0]
    net_delta = round(sum(change.weight_delta for change in changes), 4)
    positive_count = sum(1 for change in changes if change.weight_delta > 0)
    negative_count = sum(1 for change in changes if change.weight_delta < 0)
    direction = "increase" if net_delta > 0 else "decrease"
    directional_count = positive_count if net_delta > 0 else negative_count
    signal_type = _signal_type(
        changes=changes,
        direction=direction,
        directional_count=directional_count,
    )
    claim_key = _identity_claim_key(reference)
    claim_scope = _claim_scope(claim_key=claim_key, signal_type=signal_type)
    claim_evidence = _evidence_for_claim(evidence=evidence, claim_scope=claim_scope)
    scoreable_evidence = _scoreable_evidence(claim_evidence)
    evidence_support = _external_evidence_support(claim_evidence)
    contradiction_penalty = _contradiction_penalty(claim_evidence)
    if reference.ticker is None:
        data_quality_penalty = 20
    components = ScoreComponents(
        position_change_strength=min(30, round(abs(net_delta) * 6)),
        cross_etf_confirmation=20 if directional_count >= 2 else 5,
        portfolio_materiality=min(15, round(max(change.current_weight for change in changes) * 2)),
        external_evidence_support=evidence_support,
        recency_alignment=_recency_alignment(scoreable_evidence),
        data_quality_penalty=data_quality_penalty,
        contradiction_penalty=contradiction_penalty,
    )
    signal_score = (
        components.position_change_strength
        + components.cross_etf_confirmation
        + components.portfolio_materiality
        + components.external_evidence_support
        + components.recency_alignment
        - components.data_quality_penalty
        - components.contradiction_penalty
    )
    evidence_grade = _evidence_grade(
        evidence=claim_evidence,
        contradiction_penalty=contradiction_penalty,
        data_quality_penalty=data_quality_penalty,
    )
    review_label = _review_label(
        score=signal_score,
        components=components,
        evidence_grade=evidence_grade,
    )
    return SignalBoardRow(
        rank=0,
        aggregation_key=key,
        security_group_id=reference.security_group_id,
        member_security_ids=_member_security_ids(changes),
        listing_keys=_listing_keys(changes),
        data_quality_warnings=tuple(
            dict.fromkeys(
                warning
                for change in changes
                for warning in change.data_quality_warnings
            )
        ),
        claim_scope=claim_scope,
        ticker=reference.ticker,
        name=reference.name,
        market=reference.market,
        sector=reference.sector,
        theme=reference.theme,
        signal_direction=direction,
        signal_type=signal_type,
        participating_etfs=_participating_etf_ids(changes=changes, direction=direction),
        net_flow_estimate_krw=_sum_optional(change.market_value_delta for change in changes),
        weight_delta_pp=net_delta,
        holding_delta_shares=_holding_delta_shares(changes),
        new_or_exit=_new_or_exit(changes),
        signal_score=max(0, signal_score),
        confidence=_confidence(review_label=review_label, evidence_grade=evidence_grade),
        evidence_grade=evidence_grade,
        review_label=review_label,
        primary_reason=_primary_reason(
            name=reference.name,
            direction=direction,
            signal_type=signal_type,
            directional_count=directional_count,
            evidence_grade=evidence_grade,
        ),
        score_components=components,
        display=SignalDisplay(
            review_label=REVIEW_LABEL_DISPLAY[review_label],
            signal_direction=SIGNAL_DIRECTION_DISPLAY[direction],
            evidence_grade=EVIDENCE_GRADE_DISPLAY[evidence_grade],
            claim_scope=_claim_display(
                ticker_or_name=reference.ticker or reference.name,
                signal_type=signal_type,
                participating_etfs=_participating_etf_ids(
                    changes=changes,
                    direction=direction,
                ),
                focus_etf_id=focus_etf_id,
            ),
        ),
    )


def _signal_type(
    *,
    changes: tuple[_Change, ...],
    direction: str,
    directional_count: int,
) -> str:
    if directional_count >= 2 and direction == "increase":
        return "multi_etf_accumulation"
    if directional_count >= 2 and direction == "decrease":
        return "multi_etf_distribution"
    if len(changes) == 1 and changes[0].previous_weight == 0 and changes[0].current_weight > 0:
        return "new_position"
    if len(changes) == 1 and changes[0].current_weight == 0 and changes[0].previous_weight > 0:
        return "full_exit"
    return "weight_increase" if direction == "increase" else "weight_decrease"


def _claim_scope(*, claim_key: str, signal_type: str) -> str:
    return f"signal:{claim_key}:{signal_type}"


def _identity_claim_key(change: _Change) -> str:
    if change.security_group_id is not None:
        return f"security_group:{change.security_group_id}"
    return f"security:{change.security_id}"


def _claim_display(
    *,
    ticker_or_name: str,
    signal_type: str,
    participating_etfs: tuple[str, ...],
    focus_etf_id: str | None,
) -> str:
    focus_prefix = (
        "focus ETF "
        if focus_etf_id is not None
        and len(participating_etfs) == 1
        and participating_etfs[0] == focus_etf_id
        else ""
    )
    if signal_type == "multi_etf_accumulation":
        return f"{ticker_or_name} 다중 ETF 비중 확대 신호"
    if signal_type == "multi_etf_distribution":
        return f"{ticker_or_name} 다중 ETF 비중 축소 신호"
    if signal_type == "new_position":
        return f"{ticker_or_name} {focus_prefix}신규 편입 신호"
    if signal_type == "full_exit":
        return f"{ticker_or_name} {focus_prefix}전량 제외 신호"
    if signal_type == "weight_increase":
        return f"{ticker_or_name} {focus_prefix}비중 확대 신호"
    if signal_type == "weight_decrease":
        return f"{ticker_or_name} {focus_prefix}비중 축소 신호"
    return f"{ticker_or_name} 보유 변화 신호"


def _evidence_for_claim(
    *, evidence: tuple[EvidenceItemInput, ...], claim_scope: str
) -> tuple[EvidenceItemInput, ...]:
    return tuple(item for item in evidence if item.claim_scope == claim_scope)


def _scoreable_evidence(evidence: tuple[EvidenceItemInput, ...]) -> tuple[EvidenceItemInput, ...]:
    return tuple(
        item
        for item in evidence
        if _supports_interpretation(item) or _challenges_interpretation(item)
    )


def _supports_interpretation(item: EvidenceItemInput) -> bool:
    return (
        item.evidence_role == "interpretation_support"
        and item.stance == "supporting"
        and item.relevance in {"high", "medium"}
        and item.novelty in {"new", "unknown"}
        and _has_interpretation_basis(item)
    )


def _challenges_interpretation(item: EvidenceItemInput) -> bool:
    return (
        (item.evidence_role == "interpretation_challenge" or item.stance == "counter")
        and item.relevance in {"high", "medium"}
        and item.novelty in {"new", "unknown"}
        and _has_interpretation_basis(item)
    )


def _has_interpretation_basis(item: EvidenceItemInput) -> bool:
    return item.interpretation_basis is not None and bool(item.interpretation_basis.strip())


def _external_evidence_support(evidence: tuple[EvidenceItemInput, ...]) -> int:
    supporting = [item for item in evidence if _supports_interpretation(item)]
    if any(item.strength == "strong" for item in supporting):
        return 12
    if any(item.strength == "moderate" for item in supporting):
        return 8
    if supporting:
        return 3
    return 0


def _contradiction_penalty(evidence: tuple[EvidenceItemInput, ...]) -> int:
    counter = [item for item in evidence if _challenges_interpretation(item)]
    if any(item.strength == "strong" for item in counter):
        return 15
    if any(item.strength == "moderate" for item in counter):
        return 10
    if counter:
        return 5
    return 0


def _recency_alignment(evidence: tuple[EvidenceItemInput, ...]) -> int:
    if not evidence:
        return 0
    recent_cutoff = datetime(2026, 5, 1, tzinfo=UTC)
    for item in evidence:
        if item.published_at is None:
            continue
        try:
            published = datetime.fromisoformat(item.published_at.replace("Z", "+00:00"))
        except ValueError:
            continue
        if published >= recent_cutoff:
            return 10
    return 5


def _evidence_grade(
    *,
    evidence: tuple[EvidenceItemInput, ...],
    contradiction_penalty: int,
    data_quality_penalty: int,
) -> EvidenceGrade:
    if data_quality_penalty >= 20:
        return "Unusable"
    if contradiction_penalty:
        return "Conflicted"
    supporting = [item for item in evidence if _supports_interpretation(item)]
    if any(
        item.strength == "strong" and item.type in {"company_disclosure", "earnings"}
        for item in supporting
    ):
        return "Confirmed"
    if any(item.strength in {"strong", "moderate"} for item in supporting):
        return "Plausible"
    return "Weak"


def _review_label(
    *,
    score: int,
    components: ScoreComponents,
    evidence_grade: EvidenceGrade,
) -> ReviewLabel:
    if evidence_grade == "Unusable":
        return "defer"
    if evidence_grade == "Conflicted":
        return "caution"
    if score >= 55 and components.cross_etf_confirmation >= 20:
        return "focus"
    if score >= 20:
        return "monitor"
    return "defer"


def _confidence(*, review_label: ReviewLabel, evidence_grade: EvidenceGrade) -> float:
    if review_label == "focus":
        return 0.82
    if evidence_grade == "Conflicted":
        return 0.46
    if review_label == "defer":
        return 0.2
    return 0.58


def _primary_reason(
    *,
    name: str,
    direction: str,
    signal_type: str,
    directional_count: int,
    evidence_grade: EvidenceGrade,
) -> str:
    if evidence_grade == "Unusable":
        return f"{name} 신호는 식별자 또는 분류 품질 한계로 해석을 보류합니다."
    if evidence_grade == "Conflicted":
        return f"{name} 비중 변화는 확인되지만 외부 근거가 엇갈려 단정하기 어렵습니다."
    if "multi_etf" in signal_type:
        verb = "확대" if direction == "increase" else "축소"
        return f"{directional_count}개 ETF에서 같은 방향의 {verb} 신호가 확인됐습니다."
    return f"{name} 변화는 단일 focus ETF 렌즈에서 먼저 관찰된 신호입니다."


def _build_executive_summary(
    *, signal_rows: tuple[SignalBoardRow, ...], coverage: SignalReportCoverage
) -> ExecutiveSummary:
    if not signal_rows:
        return ExecutiveSummary(
            headline=_NO_SIGNAL_HEADLINE,
            market_read=(
                f"이번 분석 기간의 {coverage.etf_count}개 ETF 보유 내역에서 "
                "signal board에 올릴 만한 비중 변화가 확인되지 않았습니다."
            ),
            top_takeaways=(
                "signal_board와 ticker_dossiers는 비어 있습니다.",
                (
                    f"현재 ETF 커버리지는 {coverage.etf_count}개이고 "
                    f"ETF 브랜드는 {coverage.brand_count}개입니다."
                ),
                (
                    "데이터 품질과 커버리지 정보는 payload에 그대로 남겨 "
                    "후속 비교 기준으로 사용합니다."
                ),
            ),
            primary_risks=(
                "보유 변화가 없다는 것은 가격 방향이나 투자 판단을 뜻하지 않습니다.",
                (
                    "누락된 enrichment나 지연된 holdings 업데이트가 있으면 "
                    "다음 기간에 다시 확인해야 합니다."
                ),
            ),
        )
    top = signal_rows[0]
    return ExecutiveSummary(
        headline=f"{top.name}가 다중 ETF 신호 보드의 최상위에 올랐습니다.",
        market_read=(
            "이번 fixture는 여러 ETF에서 반복되는 AI 인프라 노출 변화가 핵심이지만, "
            "일부 종목은 근거가 약하거나 상충되어 후속 확인이 필요합니다."
        ),
        top_takeaways=(
            f"{top.ticker or top.name} 신호는 {top.display.review_label} 대상으로 분류됐습니다.",
            (
                f"현재 ETF 커버리지는 {coverage.etf_count}개이며 "
                f"ETF 브랜드는 {coverage.brand_count}개입니다."
            ),
            "focus ETF는 전체 유니버스 비교 안에서 해석되며 단독 방법론으로 분리하지 않았습니다.",
        ),
        primary_risks=(
            "가격, 뉴스, 애널리스트 커버리지가 모두 완전하지 않습니다.",
            "상충 근거가 있는 신호는 방향성을 단정하지 않고 유의 대상으로 남겼습니다.",
        ),
    )


def _build_market_map(changes: tuple[_Change, ...]) -> MarketMap:
    return MarketMap(
        by_theme=_aggregate_market_slice(changes, attribute="theme"),
        by_sector=_aggregate_market_slice(changes, attribute="sector"),
        by_country=_aggregate_market_slice(changes, attribute="country"),
        cash_position=_cash_position(changes),
        concentration={
            "top_signal_count": min(
                5,
                len({change.key for change in changes if not change.is_cash}),
            ),
            "note": "상위 신호가 AI 인프라와 플랫폼 테마에 집중되어 있습니다.",
        },
        crowding={
            "multi_etf_signal_count": sum(
                1
                for _, grouped in _group_changes(changes).items()
                if len({change.etf_id for change in grouped}) >= 2
            ),
            "note": "동일 방향 변화가 여러 ETF에서 반복될수록 crowding 가능성을 함께 봅니다.",
        },
    )


def _aggregate_market_slice(
    changes: tuple[_Change, ...], *, attribute: str
) -> tuple[MarketMapSlice, ...]:
    grouped: dict[str, list[_Change]] = defaultdict(list)
    for change in changes:
        if change.is_cash:
            continue
        key = getattr(change, attribute) or "미분류"
        grouped[key].append(change)
    slices = [
        MarketMapSlice(
            key=key,
            weight_delta_pp=round(sum(change.weight_delta for change in items), 4),
            net_flow_estimate_krw=_sum_optional(change.market_value_delta for change in items),
            signal_count=len({change.key for change in items}),
        )
        for key, items in grouped.items()
    ]
    return tuple(sorted(slices, key=lambda item: (-abs(item.weight_delta_pp), item.key)))


def _cash_position(changes: tuple[_Change, ...]) -> dict[str, float | str | None]:
    cash_changes = [change for change in changes if change.is_cash]
    if not cash_changes:
        return {"weight_delta_pp": None, "read": "현금성 포지션 변화는 fixture에 없습니다."}
    total = round(sum(change.weight_delta for change in cash_changes), 4)
    read = "현금성 비중 확대" if total > 0 else "현금성 비중 축소"
    return {"weight_delta_pp": total, "read": read}


def _build_etf_follow_sheets(
    *,
    snapshots: MultiETFHoldingsSnapshots,
    changes: tuple[_Change, ...],
    focus_etf_id: str | None,
    focus_etf_ids: tuple[str, ...],
) -> tuple[ETFFollowSheet, ...]:
    by_etf: dict[str, tuple[_Change, ...]] = {
        etf.etf_id: tuple(change for change in changes if change.etf_id == etf.etf_id)
        for etf in snapshots.etfs
    }
    sheets = [
        _build_etf_follow_sheet(
            etf=etf,
            changes=by_etf[etf.etf_id],
            focus_etf_id=focus_etf_id,
            focus_etf_ids=focus_etf_ids,
        )
        for etf in snapshots.etfs
    ]
    return tuple(sorted(sheets, key=lambda sheet: (not sheet.is_focus, sheet.etf_id)))


def _build_etf_follow_sheet(
    *,
    etf: ETFHoldingsSnapshots,
    changes: tuple[_Change, ...],
    focus_etf_id: str | None,
    focus_etf_ids: tuple[str, ...],
) -> ETFFollowSheet:
    focus_etf_id_set = set(focus_etf_ids)
    top_holdings = tuple(
        holding.ticker or holding.name
        for holding in sorted(etf.current, key=lambda item: item.weight_percent, reverse=True)[:5]
    )
    new_positions = tuple(
        change.ticker or change.name
        for change in changes
        if change.previous_weight == 0 and change.current_weight > 0 and not change.is_cash
    )
    exited_positions = tuple(
        change.ticker or change.name
        for change in changes
        if change.current_weight == 0 and change.previous_weight > 0 and not change.is_cash
    )
    increased_positions = tuple(
        change.ticker or change.name
        for change in changes
        if change.weight_delta > 0 and change.previous_weight > 0 and not change.is_cash
    )
    decreased_positions = tuple(
        change.ticker or change.name
        for change in changes
        if change.weight_delta < 0 and change.current_weight > 0 and not change.is_cash
    )
    cash_changes = [change for change in changes if change.is_cash]
    return ETFFollowSheet(
        etf_id=etf.etf_id,
        etf_name=etf.etf_name,
        brand_id=etf.brand_id,
        source_provider_id=etf.source_provider_id,
        is_focus=etf.etf_id in focus_etf_id_set,
        top_holdings=top_holdings,
        new_positions=tuple(sorted(new_positions)),
        exited_positions=tuple(sorted(exited_positions)),
        increased_positions=tuple(sorted(increased_positions)),
        decreased_positions=tuple(sorted(decreased_positions)),
        cash_change_pp=round(sum(change.weight_delta for change in cash_changes), 4)
        if cash_changes
        else None,
        theme_exposure_changes=_aggregate_market_slice(changes, attribute="theme"),
        brand_behavior_read=(
            "focus ETF에서는 AI 인프라 확대와 일부 플랫폼 축소가 동시에 보여 "
            "단일한 의도보다 재배분 가능성을 우선 점검합니다."
            if etf.etf_id in focus_etf_id_set
            else "다른 ETF와 같은 방향인지 비교해 신호 강도를 해석합니다."
        ),
        data_quality={
            "missing_ticker_count": sum(1 for holding in etf.current if holding.ticker is None),
            "missing_price_count": sum(1 for holding in etf.current if holding.price_krw is None),
        },
    )


def _build_ticker_dossiers(
    *,
    signal_rows: tuple[SignalBoardRow, ...],
    evidence_by_claim: dict[str, tuple[EvidenceItemInput, ...]],
) -> tuple[TickerDossier, ...]:
    dossiers: list[TickerDossier] = []
    for signal in signal_rows[:5]:
        evidence = tuple(evidence_by_claim.get(signal.claim_scope, ()))
        supporting = tuple(item.title for item in evidence if item.stance == "supporting")
        counter = tuple(item.title for item in evidence if item.stance == "counter")
        dossiers.append(
            TickerDossier(
                aggregation_key=signal.aggregation_key,
                security_group_id=signal.security_group_id,
                ticker=signal.ticker,
                name=signal.name,
                summary=signal.primary_reason,
                holding_facts=HoldingFacts(
                    participating_etfs=len(signal.participating_etfs),
                    participating_etf_ids=signal.participating_etfs,
                    member_security_ids=signal.member_security_ids,
                    listing_keys=signal.listing_keys,
                    weight_delta_pp=signal.weight_delta_pp,
                    holding_delta_shares=signal.holding_delta_shares,
                    net_flow_estimate_krw=signal.net_flow_estimate_krw,
                ),
                why_now_hypothesis=(
                    "보유 변화와 외부 근거가 같은 방향이면 운용역 관심 변화 가능성을 검토합니다. "
                    "근거가 약하면 여러 해석을 열어둡니다."
                ),
                supporting_evidence=supporting,
                counter_evidence=counter,
                invalidation_conditions=(
                    "다음 공시에서 같은 방향 변화가 반복되지 않을 때",
                    "가격 또는 이벤트 근거가 보유 변화와 반대로 확인될 때",
                ),
                final_label=signal.review_label,
                display={"final_label": signal.display.review_label},
            )
        )
    return tuple(dossiers)


def _build_evidence_ledger(
    *,
    evidence: tuple[EvidenceItemInput, ...],
    signal_rows: tuple[SignalBoardRow, ...],
) -> tuple[EvidenceLedgerItem, ...]:
    signals_by_claim = {signal.claim_scope: signal for signal in signal_rows}
    signals_by_display_key: dict[str, list[SignalBoardRow]] = defaultdict(list)
    for signal in signal_rows:
        signals_by_display_key[signal.ticker or signal.name].append(signal)
    ledger: list[EvidenceLedgerItem] = [
        _holding_change_evidence_item(signal) for signal in signal_rows
    ]
    for item in evidence:
        key = item.ticker or item.scope
        used_in = ()
        if item.claim_scope in signals_by_claim:
            signal = signals_by_claim[item.claim_scope]
            used_in = (item.claim_scope, f"ticker_dossier:{_dossier_ref(signal)}")
        elif key in signals_by_display_key and len(signals_by_display_key[key]) == 1:
            used_in = (f"ticker_dossier:{_dossier_ref(signals_by_display_key[key][0])}",)
        ledger.append(
            EvidenceLedgerItem(
                evidence_id=item.evidence_id,
                ticker=item.ticker,
                scope=item.scope,
                type=item.type,
                source=item.source,
                title=_report_safe_evidence_title(item),
                published_at=item.published_at,
                url=item.url,
                stance=item.stance,
                strength=item.strength,
                claim_scope=item.claim_scope,
                evidence_role=item.evidence_role,
                relevance=item.relevance,
                novelty=item.novelty,
                interpretation_basis=item.interpretation_basis,
                observed_direction=item.observed_direction,
                used_in=used_in,
            )
        )
    return tuple(ledger)


def _report_safe_evidence_title(item: EvidenceItemInput) -> str:
    title = " ".join(item.title.split())
    if title and not find_prohibited_investment_language(title):
        return title
    return _fallback_evidence_title(item)


def _fallback_evidence_title(item: EvidenceItemInput) -> str:
    subject = item.ticker or "External"
    evidence_type = item.type.replace("_", " ")
    source = " ".join(item.source.split())
    if source and not find_prohibited_investment_language(source):
        return f"{subject} {evidence_type} evidence from {source}"
    return f"{subject} {evidence_type} evidence"


def _holding_change_evidence_item(signal: SignalBoardRow) -> EvidenceLedgerItem:
    used_in = (signal.claim_scope,)
    if signal.rank <= 5:
        used_in = (signal.claim_scope, f"ticker_dossier:{_dossier_ref(signal)}")
    return EvidenceLedgerItem(
        evidence_id=f"ev_holding_change_{_evidence_id_suffix(signal.claim_scope)}",
        ticker=signal.ticker,
        scope=None,
        type="holding_change",
        source="holdings_snapshot",
        title=f"{signal.display.claim_scope} 관측",
        published_at=None,
        url=None,
        stance="supporting",
        strength=_holding_change_strength(signal),
        claim_scope=signal.claim_scope,
        evidence_role="primary_observation",
        relevance="high",
        novelty="new",
        interpretation_basis="ETF holdings change is the primary observation for this claim.",
        observed_direction=_observed_direction(signal.signal_direction),
        used_in=used_in,
    )


def _holding_change_strength(signal: SignalBoardRow) -> EvidenceStrength:
    if (
        signal.score_components.cross_etf_confirmation >= 20
        or signal.score_components.position_change_strength >= 20
    ):
        return "strong"
    if signal.score_components.position_change_strength >= 10:
        return "moderate"
    return "weak"


def _observed_direction(signal_direction: str) -> ObservedDirection:
    if signal_direction == "increase":
        return "increase"
    if signal_direction == "decrease":
        return "decrease"
    return "uncertain"


def _evidence_id_suffix(value: str) -> str:
    return "".join(character if character.isalnum() else "_" for character in value)


def _build_methodology() -> Methodology:
    return Methodology(
        analysis_mode="multi_etf_signal_analysis",
        scoring_version="signal_score_v1",
        score_components={
            "position_change_strength": "ETF 내 비중, 주식 수, 추정 금액 변화의 크기입니다.",
            "cross_etf_confirmation": "같은 방향 변화가 여러 ETF에서 확인되는 정도입니다.",
            "portfolio_materiality": (
                "현재 ETF 포트폴리오 안에서 해당 종목 변화가 차지하는 중요도입니다."
            ),
            "external_evidence_support": (
                "보유 변화 밖의 공시, 실적, 뉴스 근거가 같은 해석을 지지하는 정도입니다."
            ),
            "recency_alignment": "근거 시점이 분석 기간과 맞물리는 정도입니다.",
            "data_quality_penalty": "식별자, 가격, 분류, 커버리지 누락에 대한 감점입니다.",
            "contradiction_penalty": "외부 근거가 보유 변화 해석과 충돌할 때의 감점입니다.",
        },
        evidence_grades={
            "Confirmed": "보유 변화와 직접적인 강한 근거가 함께 있습니다.",
            "Plausible": "보유 변화와 보조 근거가 같은 방향입니다.",
            "Weak": "보유 변화는 있으나 외부 근거가 제한적입니다.",
            "Conflicted": "보유 변화와 외부 근거가 엇갈립니다.",
            "Unusable": "데이터 품질 한계로 해석을 보류합니다.",
        },
        review_labels=REVIEW_LABEL_DISPLAY,
        limitations_policy="누락 enrichment는 null, 빈 배열, data_quality 항목으로 명시합니다.",
    )


def _build_data_quality(
    *,
    issues: tuple[DataQualityIssue, ...],
    coverage: SignalReportCoverage,
    operational_provenance: Mapping[str, JsonValue] | None = None,
) -> DataQuality:
    limitations = ["fixture 기반 deterministic 분석이며 live enrichment는 포함하지 않습니다."]
    if coverage.price_coverage_ratio < 1:
        limitations.append("일부 가격 데이터가 없어 가격 기반 해석은 제한됩니다.")
    if coverage.news_coverage_ratio is None:
        limitations.append("뉴스 enrichment는 실행되지 않았습니다.")
    elif coverage.news_coverage_ratio < 1:
        limitations.append("뉴스 근거가 모든 신호에 제공되지는 않았습니다.")
    if coverage.analyst_coverage_ratio is None:
        limitations.append("애널리스트 커버리지는 v1에서 제공되지 않았습니다.")
    elif coverage.analyst_coverage_ratio < 1:
        limitations.append("애널리스트 커버리지가 일부 신호에 제공되지 않았습니다.")
    operational = _operational_data_quality_projection(operational_provenance)
    external = _external_evidence_data_quality_projection(operational_provenance)
    all_issues = issues + operational.issues
    limitations.extend(operational.limitations)
    limitations.extend(external.limitations)
    return DataQuality(
        overall="limited" if all_issues or len(limitations) > 1 else "complete",
        issues=all_issues,
        limitations=tuple(limitations),
        coverage_notes=(
            f"ticker_mapping_coverage_ratio={coverage.ticker_mapping_coverage_ratio:.2f}",
            f"classification_coverage_ratio={coverage.classification_coverage_ratio:.2f}",
            _coverage_note(
                "news_coverage_ratio",
                coverage.news_coverage_ratio,
                none_label="not_run",
            ),
            _coverage_note(
                "analyst_coverage_ratio",
                coverage.analyst_coverage_ratio,
                none_label="not_provided",
            ),
            *operational.coverage_notes,
            *external.coverage_notes,
        ),
    )


def _external_evidence_data_quality_projection(
    provenance: Mapping[str, JsonValue] | None,
) -> _OperationalDataQualityProjection:
    summary = _external_evidence_summary(provenance)
    if summary is None:
        return _OperationalDataQualityProjection(issues=(), limitations=(), coverage_notes=())
    category_coverage = summary.get("category_coverage")
    if not isinstance(category_coverage, Mapping):
        return _OperationalDataQualityProjection(issues=(), limitations=(), coverage_notes=())

    limitations: list[str] = []
    coverage_notes: list[str] = []
    for category in ("financial", "disclosure", "news"):
        ratio = _external_category_coverage_ratio(summary, category)
        coverage_notes.append(
            _coverage_note(
                f"external_{category}_coverage_ratio",
                ratio,
                none_label="not_run",
            )
        )
        if ratio is not None and ratio < 1:
            limitations.append(
                f"External {category} evidence did not cover every selected target."
            )
        raw_category = category_coverage.get(category)
        if not isinstance(raw_category, Mapping):
            continue
        notes = raw_category.get("notes")
        if isinstance(notes, list):
            coverage_notes.extend(str(note) for note in notes if isinstance(note, str))
    return _OperationalDataQualityProjection(
        issues=(),
        limitations=tuple(limitations),
        coverage_notes=tuple(coverage_notes),
    )


def _external_evidence_summary(
    provenance: Mapping[str, JsonValue] | None,
) -> Mapping[str, JsonValue] | None:
    if provenance is None:
        return None
    summary = provenance.get("external_evidence_summary")
    return summary if isinstance(summary, Mapping) else None


def _operational_data_quality_projection(
    provenance: Mapping[str, JsonValue] | None,
) -> _OperationalDataQualityProjection:
    if provenance is None:
        return _OperationalDataQualityProjection(issues=(), limitations=(), coverage_notes=())

    issues: list[DataQualityIssue] = []
    limitations: list[str] = []
    coverage_notes: list[str] = []
    if provenance.get("sync_metadata_available") is False:
        issues.append(
            DataQualityIssue(
                code="operational_sync_metadata_unavailable",
                severity="medium",
                scope="operational_holdings",
                message=(
                    "Operational holdings sync metadata was unavailable, so source-data "
                    "diagnostics were not included."
                ),
            )
        )
        limitations.append(
            "운영 보유 데이터 동기화 메타데이터가 없어 원천 데이터 진단이 포함되지 않았습니다."
        )

    focus_handoff = _focus_handoff_data_quality_projection(provenance)
    limitations.extend(focus_handoff.limitations)
    coverage_notes.extend(focus_handoff.coverage_notes)

    sync_quality = provenance.get("sync_quality")
    if not isinstance(sync_quality, Mapping):
        readiness = _readiness_data_quality_projection(provenance.get("operational_readiness"))
        issues.extend(readiness.issues)
        limitations.extend(readiness.limitations)
        coverage_notes.extend(readiness.coverage_notes)
        return _OperationalDataQualityProjection(
            issues=tuple(issues),
            limitations=tuple(limitations),
            coverage_notes=tuple(coverage_notes),
        )

    for item in _sync_quality_items(sync_quality.get("warnings")):
        issues.append(_operational_sync_quality_issue(item=item, severity="medium"))
    for item in _sync_quality_items(sync_quality.get("risk_failures")):
        issues.append(_operational_sync_quality_issue(item=item, severity="high"))

    metrics = sync_quality.get("metrics")
    if isinstance(metrics, Mapping):
        for metric in (
            "cash_derivation_failure_ratio",
            "fit_failure_ratio",
            "unusable_cash_weight_ratio",
            "ticker_mapping_coverage_ratio",
            "missing_source_date_count",
            "skipped_missing_security_id_count",
        ):
            coverage_notes.append(
                f"operational_{metric}={_operational_metric_value(metrics.get(metric))}"
            )

    readiness = _readiness_data_quality_projection(provenance.get("operational_readiness"))
    issues.extend(readiness.issues)
    limitations.extend(readiness.limitations)
    coverage_notes.extend(readiness.coverage_notes)

    status = sync_quality.get("status")
    if status == "warning":
        limitations.append("운영 보유 데이터 동기화 품질 경고가 있어 일부 신호 해석은 제한됩니다.")
    elif status == "risk_failed":
        limitations.append(
            "운영 보유 데이터 동기화 품질 리스크가 높아 "
            "운영 데이터 기반 신호는 검증 전 사용을 보류해야 합니다."
        )

    return _OperationalDataQualityProjection(
        issues=tuple(issues),
        limitations=tuple(limitations),
        coverage_notes=tuple(coverage_notes),
    )


def _focus_handoff_data_quality_projection(
    provenance: Mapping[str, JsonValue],
) -> _OperationalDataQualityProjection:
    focus_eligibility = provenance.get("focus_eligibility")
    if not isinstance(focus_eligibility, Mapping):
        return _OperationalDataQualityProjection(issues=(), limitations=(), coverage_notes=())

    limitations: list[str] = []
    coverage_notes: list[str] = []
    if focus_eligibility.get("mixed_comparison_windows") is True:
        limitations.append("Operational handoff used mixed per-ETF comparison windows.")
        coverage_notes.append("focus_mixed_comparison_windows=true")

    exclusions = focus_eligibility.get("handoff_exclusions")
    exclusion_count = len(exclusions) if isinstance(exclusions, list) else 0
    if exclusion_count:
        limitations.append(
            "Operational handoff excluded "
            f"{exclusion_count} unavailable provider target"
            f"{'' if exclusion_count == 1 else 's'} with path-safe evidence."
        )
        coverage_notes.append(f"handoff_exclusion_count={exclusion_count}")
    return _OperationalDataQualityProjection(
        issues=(),
        limitations=tuple(limitations),
        coverage_notes=tuple(coverage_notes),
    )


def _readiness_data_quality_projection(
    value: JsonValue | None,
) -> _OperationalDataQualityProjection:
    if not isinstance(value, Mapping):
        return _OperationalDataQualityProjection(issues=(), limitations=(), coverage_notes=())
    issues: list[DataQualityIssue] = []
    coverage_notes: list[str] = []
    reasons = value.get("reasons")
    if isinstance(reasons, list):
        for item in reasons:
            if not isinstance(item, Mapping):
                continue
            source_items = _readiness_detail_source_items(item)
            projected_items = source_items or (item,)
            for projected in projected_items:
                issues.append(_readiness_data_quality_issue(item=projected, severity="high"))
                metric = projected.get("metric")
                if isinstance(metric, str) and metric:
                    coverage_notes.append(
                        f"readiness_{metric}="
                        f"{_operational_metric_value(projected.get('value'))}"
                    )
    warnings = value.get("warnings")
    if isinstance(warnings, list):
        for item in warnings:
            if not isinstance(item, Mapping):
                continue
            source_items = _readiness_detail_source_items(item)
            projected_items = source_items or (item,)
            for projected in projected_items:
                issues.append(
                    _readiness_data_quality_issue(item=projected, severity="medium")
                )
                metric = projected.get("metric")
                if isinstance(metric, str) and metric:
                    coverage_notes.append(
                        f"readiness_{metric}="
                        f"{_operational_metric_value(projected.get('value'))}"
                    )
    limitations: list[str] = []
    status = value.get("status")
    if status == "ready_with_warnings" and issues:
        limitations.append(
            "Operational readiness warnings are disclosed for this user-ready run."
        )
    return _OperationalDataQualityProjection(
        issues=tuple(issues),
        limitations=tuple(limitations),
        coverage_notes=tuple(coverage_notes),
    )


def _readiness_data_quality_issue(
    *, item: Mapping[str, JsonValue], severity: str
) -> DataQualityIssue:
    code = item.get("code")
    message = item.get("message")
    return DataQualityIssue(
        code=f"readiness_{code}" if isinstance(code, str) else "readiness_unknown",
        severity=severity,
        scope="operational_readiness",
        message=(
            message
            if isinstance(message, str) and message
            else "Operational readiness finding."
        ),
    )


def _readiness_detail_source_items(
    item: Mapping[str, JsonValue],
) -> tuple[Mapping[str, JsonValue], ...]:
    details = item.get("details")
    if not isinstance(details, Mapping):
        return ()
    source_items = details.get("source_items")
    if not isinstance(source_items, list):
        return ()
    return tuple(source for source in source_items if isinstance(source, Mapping))


def _sync_quality_items(value: JsonValue | None) -> tuple[Mapping[str, JsonValue], ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _operational_sync_quality_issue(
    *, item: Mapping[str, JsonValue], severity: str
) -> DataQualityIssue:
    code = item.get("code")
    message = item.get("message")
    return DataQualityIssue(
        code=f"operational_{code}" if isinstance(code, str) else "operational_unknown",
        severity=severity,
        scope="operational_holdings",
        message=message if isinstance(message, str) else "Operational sync quality diagnostic.",
    )


def _operational_metric_value(value: JsonValue | None) -> str:
    if value is None:
        return "not_applicable"
    return str(value)


def _build_coverage(
    *,
    snapshots: MultiETFHoldingsSnapshots,
    evidence: tuple[EvidenceItemInput, ...] | None,
    external_evidence_summary: Mapping[str, JsonValue] | None = None,
) -> SignalReportCoverage:
    all_rows = [holding for etf in snapshots.etfs for holding in (*etf.previous, *etf.current)]
    current_rows = [holding for etf in snapshots.etfs for holding in etf.current]
    mapped = [holding for holding in current_rows if holding.ticker is not None]
    classified = [
        holding
        for holding in current_rows
        if holding.sector is not None and holding.theme is not None and holding.country is not None
    ]
    priced = [
        holding for holding in current_rows if holding.price_krw is not None or holding.is_cash
    ]
    current_tickers = {holding.ticker for holding in current_rows if holding.ticker is not None}
    evidence_tickers = (
        None if evidence is None else {item.ticker for item in evidence if item.ticker is not None}
    )
    external_news_coverage = _external_category_coverage_ratio(
        external_evidence_summary,
        "news",
    )
    return SignalReportCoverage(
        etf_count=len(snapshots.etfs),
        holding_rows=len(all_rows),
        securities_count=len({_holding_key(holding) for holding in current_rows}),
        brand_count=len({etf.brand_id for etf in snapshots.etfs}),
        source_provider_count=len({etf.source_provider_id for etf in snapshots.etfs}),
        mapped_security_ratio=_ratio(len(mapped), len(current_rows)),
        price_coverage_ratio=_ratio(len(priced), len(current_rows)),
        financial_coverage_ratio=_external_category_coverage_ratio(
            external_evidence_summary,
            "financial",
        ),
        disclosure_coverage_ratio=_external_category_coverage_ratio(
            external_evidence_summary,
            "disclosure",
        ),
        news_coverage_ratio=external_news_coverage
        if external_news_coverage is not None
        else (
            None
            if evidence_tickers is None
            else _ratio(len(current_tickers & evidence_tickers), len(current_tickers))
        ),
        analyst_coverage_ratio=None,
        classification_coverage_ratio=_ratio(len(classified), len(current_rows)),
        ticker_mapping_coverage_ratio=_ratio(len(mapped), len(current_rows)),
    )


def _external_category_coverage_ratio(
    external_evidence_summary: Mapping[str, JsonValue] | None,
    category: str,
) -> float | None:
    if external_evidence_summary is None:
        return None
    category_coverage = external_evidence_summary.get("category_coverage")
    if not isinstance(category_coverage, Mapping):
        return None
    category_data = category_coverage.get(category)
    if not isinstance(category_data, Mapping):
        return None
    ratio = category_data.get("coverage_ratio")
    if isinstance(ratio, int | float) and not isinstance(ratio, bool):
        return float(ratio)
    return None


def _build_data_quality_issues(
    snapshots: MultiETFHoldingsSnapshots,
) -> tuple[DataQualityIssue, ...]:
    issues: list[DataQualityIssue] = []
    warned_group_ids: set[str] = set()
    for etf in snapshots.etfs:
        for holding in etf.current:
            scope = f"{etf.etf_id}:{holding.security_id}"
            if holding.ticker is None:
                issues.append(
                    DataQualityIssue(
                        code="missing_ticker",
                        severity="high",
                        scope=scope,
                        message=f"{holding.name} 식별자가 없어 신호 해석을 보류합니다.",
                    )
                )
            if holding.sector is None or holding.theme is None or holding.country is None:
                issues.append(
                    DataQualityIssue(
                        code="missing_classification",
                        severity="medium",
                        scope=scope,
                        message=f"{holding.name} 분류 정보가 일부 누락됐습니다.",
                    )
                )
            if holding.price_krw is None and not holding.is_cash:
                issues.append(
                    DataQualityIssue(
                        code="missing_price",
                        severity="medium",
                        scope=scope,
                        message=f"{holding.name} 가격 enrichment가 없습니다.",
                    )
                )
        for aggregate in _holdings_by_identity(etf.current).values():
            if (
                "missing_security_group_display_label"
                not in aggregate.data_quality_warnings
                or aggregate.security_group_id is None
                or aggregate.security_group_id in warned_group_ids
            ):
                continue
            warned_group_ids.add(aggregate.security_group_id)
            issues.append(
                DataQualityIssue(
                    code="missing_security_group_display_label",
                    severity="medium",
                    scope=f"security_group:{aggregate.security_group_id}",
                    message=(
                        "Reviewed security group is missing display metadata: "
                        f"{aggregate.security_group_id}"
                    ),
                )
            )
    return tuple(issues)


def _evidence_by_ticker(
    evidence: tuple[EvidenceItemInput, ...],
) -> dict[str, tuple[EvidenceItemInput, ...]]:
    grouped: dict[str, list[EvidenceItemInput]] = defaultdict(list)
    for item in evidence:
        key = item.ticker or item.scope
        if key is not None:
            grouped[key].append(item)
    return {key: tuple(items) for key, items in grouped.items()}


def _evidence_by_claim(
    evidence: tuple[EvidenceItemInput, ...],
) -> dict[str, tuple[EvidenceItemInput, ...]]:
    grouped: dict[str, list[EvidenceItemInput]] = defaultdict(list)
    for item in evidence:
        if item.claim_scope is not None:
            grouped[item.claim_scope].append(item)
    return {key: tuple(items) for key, items in grouped.items()}


def _dossier_ref(signal: SignalBoardRow) -> str:
    if signal.security_group_id is not None:
        return f"security_group:{signal.security_group_id}"
    return f"security:{signal.aggregation_key}"


def _holding_key(holding: SecurityHolding) -> str:
    return holding.security_group_id or holding.security_id


def _member_security_ids(changes: tuple[_Change, ...]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            security_id
            for change in changes
            for security_id in change.member_security_ids
        )
    )


def _listing_keys(changes: tuple[_Change, ...]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            listing_key
            for change in changes
            for listing_key in change.listing_keys
        )
    )


def _participating_etf_ids(
    *, changes: tuple[_Change, ...], direction: str
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            change.etf_id for change in changes if _same_direction(change, direction)
        )
    )


def _first_text(values) -> str | None:
    for value in values:
        if value is not None and value.strip():
            return value
    return None


def _first_float(values) -> float | None:
    for value in values:
        if value is not None:
            return value
    return None


def _security_group_fallback_label(security_group_id: str | None) -> str | None:
    if security_group_id is None:
        return None
    return f"Security group {security_group_id}"


def _ticker_key(items: list[_Change]) -> str:
    reference = items[0]
    return reference.ticker or reference.name


def _same_direction(change: _Change, direction: str) -> bool:
    return change.weight_delta > 0 if direction == "increase" else change.weight_delta < 0


def _new_or_exit(changes: tuple[_Change, ...]) -> str | None:
    if all(change.previous_weight == 0 and change.current_weight > 0 for change in changes):
        return "new"
    if all(change.current_weight == 0 and change.previous_weight > 0 for change in changes):
        return "exit"
    return None


def _holding_delta_shares(changes: tuple[_Change, ...]) -> float | None:
    deltas = []
    for change in changes:
        if change.previous_shares is None or change.current_shares is None:
            continue
        deltas.append(change.current_shares - change.previous_shares)
    if not deltas:
        return None
    return round(sum(deltas), 4)


def _sum_optional(values) -> float | None:
    filtered = [value for value in values if value is not None]
    if not filtered:
        return None
    return round(sum(filtered), 2)


def _ratio(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 4)


def _coverage_note(name: str, value: float | None, *, none_label: str) -> str:
    if value is None:
        return f"{name}={none_label}"
    return f"{name}={value:.2f}"


def _group_changes(changes: tuple[_Change, ...]) -> dict[str, tuple[_Change, ...]]:
    grouped: dict[str, list[_Change]] = defaultdict(list)
    for change in changes:
        if not change.is_cash:
            grouped[change.key].append(change)
    return {key: tuple(items) for key, items in grouped.items()}
