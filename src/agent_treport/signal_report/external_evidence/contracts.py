from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol

from agent_pack.models import JsonValue, RuntimeModel, strict_json_object
from pydantic import Field, field_validator

from agent_treport.signal_report.domain.evidence import EvidenceEventType

EvidenceCategory = Literal["financial", "disclosure", "news"]
ProviderOutcomeStatus = Literal[
    "success",
    "skipped",
    "no_data",
    "credential_required",
    "timeout_exhausted",
    "rate_limited_exhausted",
    "blocked",
    "invalid_provider_payload",
    "provider_unavailable",
    "cooldown_active",
]
HoldingsSource = Literal["fixture", "operational", "targets"]
PolicyFailureCode = Literal[
    "credential_required",
    "blocked",
    "rate_limited_exhausted",
    "provider_unavailable",
    "invalid_provider_payload",
    "timeout_exhausted",
]


class FinancialEvidenceDetails(RuntimeModel):
    metric: str
    value: str | float | int | None = None
    comparison_value: str | float | int | None = None
    unit: str | None = None
    period: str | None = None


class DisclosureEvidenceDetails(RuntimeModel):
    filing_id: str
    filing_type: str
    filing_date: str | None = None
    report_date: str | None = None


class NewsEvidenceDetails(RuntimeModel):
    publisher: str
    sentiment_label: str | None = None
    article_id: str | None = None


class ExternalEvidenceCandidate(RuntimeModel):
    candidate_id: str
    provider_id: str
    category: EvidenceCategory
    ticker: str
    source_label: str
    title: str
    published_at: str | None = None
    event_type: EvidenceEventType
    summary: str
    safe_url: str | None = None
    metadata: Mapping[str, JsonValue] = Field(default_factory=dict)
    financial: FinancialEvidenceDetails | None = None
    disclosure: DisclosureEvidenceDetails | None = None
    news: NewsEvidenceDetails | None = None

    @field_validator("metadata", mode="before")
    @classmethod
    def _validate_metadata(cls, value: object) -> dict[str, JsonValue]:
        return strict_json_object(value or {})


class ExternalEvidenceTarget(RuntimeModel):
    ticker: str
    name: str
    aggregation_key: str | None = None
    security_group_id: str | None = None
    member_security_ids: tuple[str, ...] = ()
    listing_keys: tuple[str, ...] = ()
    claim_scope: str | None
    rank: int
    signal_type: str | None = None
    signal_direction: str | None = None
    summary: str


class ExternalEvidenceProviderOutcome(RuntimeModel):
    provider_id: str
    category: EvidenceCategory
    status: ProviderOutcomeStatus
    error_code: str | None = None
    retryable: bool
    attempt_count: int
    stopped_reason: str | None = None
    target_tickers: tuple[str, ...]
    safe_message: str
    deduped_count: int = 0
    metadata: Mapping[str, JsonValue] = Field(default_factory=dict)

    @field_validator("safe_message")
    @classmethod
    def _validate_safe_message(cls, value: str) -> str:
        lowered = value.lower()
        blocked = ("api_key", "authorization", "token=", "secret", "traceback", "raw_payload")
        if any(item in lowered for item in blocked):
            raise ValueError("provider outcome safe_message contains sensitive material")
        return value

    @field_validator("metadata", mode="before")
    @classmethod
    def _validate_metadata(cls, value: object) -> dict[str, JsonValue]:
        return strict_json_object(value or {})


class ExternalEvidenceSummary(RuntimeModel):
    schema_version: str = "agent_treport.external_evidence.summary.v1"
    generated_at: str
    target_selection: Mapping[str, JsonValue]
    provider_outcomes: tuple[ExternalEvidenceProviderOutcome, ...]
    category_coverage: Mapping[str, JsonValue]
    dedupe: Mapping[str, JsonValue]
    policy_failure: Mapping[str, JsonValue] | None = None
    evidence_path: str | None = None
    cooldown_path: str | None = None
    required_provider_ids: tuple[str, ...] = ()
    known_unvalidated_provider_exceptions: tuple[Mapping[str, JsonValue], ...] = ()
    provider_limitations: tuple[Mapping[str, JsonValue], ...] = ()
    evidence_reuse: Mapping[str, JsonValue] = Field(default_factory=dict)
    smoke_boundary: Mapping[str, JsonValue] = Field(default_factory=dict)

    @field_validator(
        "target_selection",
        "category_coverage",
        "dedupe",
        "evidence_reuse",
        "smoke_boundary",
        mode="before",
    )
    @classmethod
    def _validate_json_object(cls, value: object) -> dict[str, JsonValue]:
        return strict_json_object(value or {})

    @field_validator("policy_failure", mode="before")
    @classmethod
    def _validate_optional_json_object(
        cls, value: object
    ) -> dict[str, JsonValue] | None:
        if value is None:
            return None
        return strict_json_object(value)


class ExternalEvidenceCollectionResult(RuntimeModel):
    evidence_count: int
    summary: ExternalEvidenceSummary


class AlignmentClassifier(Protocol):
    def classify(
        self,
        *,
        candidates: Sequence[ExternalEvidenceCandidate],
        signal_rows: Sequence[object],
    ) -> Mapping[str, object]:
        ...


class ExternalEvidenceProvider(Protocol):
    @property
    def provider_id(self) -> str:
        ...

    @property
    def category(self) -> EvidenceCategory:
        ...

    def collect(
        self,
        targets: Sequence[ExternalEvidenceTarget],
        *,
        live: bool,
        request_context: ExternalEvidenceRequestContext,
    ) -> tuple[Sequence[ExternalEvidenceCandidate], ExternalEvidenceProviderOutcome]:
        ...


@dataclass(frozen=True)
class ExternalEvidenceRequestContext:
    now: Callable[[], datetime]
    timeout_seconds: float
    min_interval_seconds: float
    max_attempts: int = 3


@dataclass(frozen=True)
class ExternalEvidenceRequest:
    holdings_source: HoldingsSource
    provider_ids: Sequence[str]
    evidence_path: str | Path
    summary_path: str | Path
    holdings_path: str | Path | None = None
    target_candidates_path: str | Path | None = None
    live: bool = False
    max_targets: int = 2
    focus_etf_id: str | None = None
    focus_etf_ids: Sequence[str] | None = None
    observed_partitions: int = 30
    cooldown_path: str | Path | None = None
    align_claims: bool = False
    classifier: AlignmentClassifier | None = None
    provider_overrides: Mapping[str, ExternalEvidenceProvider] = field(default_factory=dict)
    now: Callable[[], datetime] | None = None
    timeout_seconds: float = 12.0
    min_interval_seconds: float = 0.2
    ignore_cooldown: bool = False
