from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from agent_pack.models import JsonValue, RuntimeModel

from agent_treport.signal_report.domain.evidence import (
    EvidenceEventType,
    EvidenceNovelty,
    EvidenceRelevance,
    EvidenceRole,
    EvidenceStance,
    EvidenceStrength,
    ObservedDirection,
)

ReviewLabel = Literal["focus", "monitor", "caution", "defer"]
EvidenceGrade = Literal["Confirmed", "Plausible", "Weak", "Conflicted", "Unusable"]


class SignalReportMeta(RuntimeModel):
    report_id: str
    as_of_date: str
    period: Mapping[str, JsonValue]
    universe: str
    report_type: str
    language: str
    generated_at: str
    report_version: str
    scoring_version: str
    focus_etf_id: str | None
    focus_etf_ids: tuple[str, ...] = ()


class SignalReportCoverage(RuntimeModel):
    etf_count: int
    holding_rows: int
    securities_count: int
    brand_count: int
    source_provider_count: int
    mapped_security_ratio: float
    price_coverage_ratio: float
    financial_coverage_ratio: float | None = None
    disclosure_coverage_ratio: float | None = None
    news_coverage_ratio: float | None
    analyst_coverage_ratio: float | None
    classification_coverage_ratio: float
    ticker_mapping_coverage_ratio: float


class ExecutiveSummary(RuntimeModel):
    headline: str
    market_read: str
    top_takeaways: tuple[str, ...]
    primary_risks: tuple[str, ...]


class ScoreComponents(RuntimeModel):
    position_change_strength: int
    cross_etf_confirmation: int
    portfolio_materiality: int
    external_evidence_support: int
    recency_alignment: int
    data_quality_penalty: int
    contradiction_penalty: int


class SignalDisplay(RuntimeModel):
    review_label: str
    signal_direction: str
    evidence_grade: str
    claim_scope: str


class SignalBoardRow(RuntimeModel):
    rank: int
    aggregation_key: str
    security_group_id: str | None = None
    member_security_ids: tuple[str, ...] = ()
    listing_keys: tuple[str, ...] = ()
    data_quality_warnings: tuple[str, ...] = ()
    claim_scope: str
    ticker: str | None
    name: str
    market: str | None
    sector: str | None
    theme: str | None
    signal_direction: str
    signal_type: str
    participating_etfs: tuple[str, ...]
    net_flow_estimate_krw: float | None
    weight_delta_pp: float | None
    holding_delta_shares: float | None
    new_or_exit: str | None
    signal_score: int
    confidence: float
    evidence_grade: EvidenceGrade
    review_label: ReviewLabel
    primary_reason: str
    score_components: ScoreComponents
    display: SignalDisplay


class MarketMapSlice(RuntimeModel):
    key: str
    weight_delta_pp: float
    net_flow_estimate_krw: float | None
    signal_count: int


class MarketMap(RuntimeModel):
    by_theme: tuple[MarketMapSlice, ...]
    by_sector: tuple[MarketMapSlice, ...]
    by_country: tuple[MarketMapSlice, ...]
    cash_position: Mapping[str, JsonValue]
    concentration: Mapping[str, JsonValue]
    crowding: Mapping[str, JsonValue]


class ETFFollowSheet(RuntimeModel):
    etf_id: str
    etf_name: str
    brand_id: str
    source_provider_id: str
    is_focus: bool
    top_holdings: tuple[str, ...]
    new_positions: tuple[str, ...]
    exited_positions: tuple[str, ...]
    increased_positions: tuple[str, ...]
    decreased_positions: tuple[str, ...]
    cash_change_pp: float | None
    theme_exposure_changes: tuple[MarketMapSlice, ...]
    brand_behavior_read: str
    data_quality: Mapping[str, JsonValue]


class HoldingFacts(RuntimeModel):
    participating_etfs: int
    participating_etf_ids: tuple[str, ...]
    member_security_ids: tuple[str, ...] = ()
    listing_keys: tuple[str, ...] = ()
    weight_delta_pp: float | None
    holding_delta_shares: float | None
    net_flow_estimate_krw: float | None


class TickerDossier(RuntimeModel):
    aggregation_key: str
    security_group_id: str | None = None
    ticker: str | None
    name: str
    summary: str
    holding_facts: HoldingFacts
    why_now_hypothesis: str
    supporting_evidence: tuple[str, ...]
    counter_evidence: tuple[str, ...]
    invalidation_conditions: tuple[str, ...]
    final_label: ReviewLabel
    display: Mapping[str, JsonValue]


class EvidenceLedgerItem(RuntimeModel):
    evidence_id: str
    ticker: str | None
    scope: str | None
    type: EvidenceEventType
    source: str
    title: str
    published_at: str | None
    url: str | None
    stance: EvidenceStance
    strength: EvidenceStrength
    claim_scope: str | None
    evidence_role: EvidenceRole
    relevance: EvidenceRelevance | None
    novelty: EvidenceNovelty | None
    interpretation_basis: str | None
    observed_direction: ObservedDirection | None
    used_in: tuple[str, ...]


class Methodology(RuntimeModel):
    analysis_mode: str
    scoring_version: str
    score_components: Mapping[str, str]
    evidence_grades: Mapping[str, str]
    review_labels: Mapping[str, str]
    limitations_policy: str


class DataQualityIssue(RuntimeModel):
    code: str
    severity: str
    scope: str
    message: str


class DataQuality(RuntimeModel):
    overall: str
    issues: tuple[DataQualityIssue, ...]
    limitations: tuple[str, ...]
    coverage_notes: tuple[str, ...]


class SignalReportPayload(RuntimeModel):
    meta: SignalReportMeta
    coverage: SignalReportCoverage
    executive_summary: ExecutiveSummary
    signal_board: tuple[SignalBoardRow, ...]
    market_map: MarketMap
    etf_follow_sheets: tuple[ETFFollowSheet, ...]
    ticker_dossiers: tuple[TickerDossier, ...]
    evidence_ledger: tuple[EvidenceLedgerItem, ...]
    methodology: Methodology
    data_quality: DataQuality
