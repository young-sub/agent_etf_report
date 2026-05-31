from __future__ import annotations

import json

from agent_treport.signal_report import (
    HTMLResearchReportRenderer,
    MarkdownSignalReportRenderer,
    ReportQualityGate,
    build_signal_report_payload,
    load_fixture_signal_report_inputs,
)
from agent_treport.signal_report.domain.evidence import EvidenceItemInput
from agent_treport.signal_report.domain.snapshots import (
    ETFHoldingsSnapshots,
    MultiETFHoldingsSnapshots,
    SecurityHolding,
)


def test_signal_report_payload_builds_ranked_multi_etf_contract() -> None:
    fixture = load_fixture_signal_report_inputs()

    payload = build_signal_report_payload(
        snapshots=fixture.snapshots,
        focus_etf_id=fixture.focus_etf_id,
        evidence=fixture.evidence,
    )

    data = payload.model_dump(mode="json")
    serialized = json.dumps(data, ensure_ascii=False)

    assert set(data) == {
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
    }
    assert data["meta"]["report_type"] == "weekly_etf_signal"
    assert data["meta"]["focus_etf_id"] == "etf_focus_ai"
    assert data["coverage"]["etf_count"] == 3
    assert data["coverage"]["brand_count"] == 2
    assert data["coverage"]["source_provider_count"] == 1

    top_signal = data["signal_board"][0]
    assert top_signal["ticker"] == "NVDA"
    assert top_signal["signal_type"] == "multi_etf_accumulation"
    assert top_signal["review_label"] == "focus"
    assert top_signal["display"]["review_label"] == "중점 모니터링"
    assert top_signal["evidence_grade"] in {"Confirmed", "Plausible"}
    assert set(top_signal["score_components"]) == {
        "position_change_strength",
        "cross_etf_confirmation",
        "portfolio_materiality",
        "external_evidence_support",
        "recency_alignment",
        "data_quality_penalty",
        "contradiction_penalty",
    }

    focus_only = next(signal for signal in data["signal_board"] if signal["ticker"] == "TSLA")
    assert focus_only["participating_etfs"] == ["etf_focus_ai"]
    assert focus_only["rank"] > top_signal["rank"]

    assert {"focus", "monitor", "caution", "defer"}.issubset(
        {signal["review_label"] for signal in data["signal_board"]}
    )
    assert data["etf_follow_sheets"][0]["etf_id"] == "etf_focus_ai"
    assert data["ticker_dossiers"][0]["holding_facts"]["participating_etfs"] >= 2
    assert data["evidence_ledger"][0]["used_in"]
    assert data["data_quality"]["limitations"]
    assert '"manager_count"' not in serialized
    assert '"provider_count"' not in serialized
    assert '"manager_id"' not in serialized
    assert '"provider_id"' not in serialized
    assert '"manager_behavior_read"' not in serialized
    assert "매니저" not in serialized
    assert "운용진" not in serialized
    assert "ETF 브랜드" in serialized
    assert "운용역" in serialized
    assert "action_label" not in serialized
    assert "BUY" not in serialized
    assert "HOLD" not in serialized
    assert "SELL" not in serialized


def test_matching_claim_scoped_curated_evidence_affects_support() -> None:
    fixture = load_fixture_signal_report_inputs()
    payload_without_evidence = build_signal_report_payload(
        snapshots=fixture.snapshots,
        focus_etf_id=fixture.focus_etf_id,
        evidence=(),
    )
    nvda_without_evidence = next(
        signal for signal in payload_without_evidence.signal_board if signal.ticker == "NVDA"
    )

    payload = build_signal_report_payload(
        snapshots=fixture.snapshots,
        focus_etf_id=fixture.focus_etf_id,
        evidence=(
            _evidence(
                ticker="NVDA",
                claim_scope=nvda_without_evidence.claim_scope,
                evidence_role="interpretation_support",
                stance="supporting",
                strength="strong",
                relevance="high",
                novelty="new",
                interpretation_basis=(
                    "Earnings evidence is curated for this exact NVDA accumulation claim."
                ),
            ),
        ),
    )

    nvda = next(signal for signal in payload.signal_board if signal.ticker == "NVDA")
    assert nvda.claim_scope == "signal:security:sec_nvda:multi_etf_accumulation"
    assert nvda.score_components.external_evidence_support == 12
    assert nvda.display.claim_scope == "NVDA 다중 ETF 비중 확대 신호"


def test_same_ticker_mismatched_claim_scope_does_not_affect_support() -> None:
    fixture = load_fixture_signal_report_inputs()

    payload = build_signal_report_payload(
        snapshots=fixture.snapshots,
        focus_etf_id=fixture.focus_etf_id,
        evidence=(
            _evidence(
                ticker="NVDA",
                claim_scope="signal:NVDA:weight_decrease",
                evidence_role="interpretation_support",
                stance="supporting",
                strength="strong",
                relevance="high",
                novelty="new",
                interpretation_basis="Curated, but tied to a different NVDA claim.",
            ),
        ),
    )

    nvda = next(signal for signal in payload.signal_board if signal.ticker == "NVDA")
    assert nvda.score_components.external_evidence_support == 0


def test_same_ticker_false_alias_remains_separate_identity_scoped_signals() -> None:
    snapshots = _identity_regression_snapshots(
        previous=(
            _security_holding(
                security_id="ES0113900J37",
                ticker="SAN",
                name="Banco Santander SA",
                weight_percent=1.0,
            ),
            _security_holding(
                security_id="FR0000120578",
                ticker="SAN",
                name="Sanofi SA",
                weight_percent=1.0,
            ),
        ),
        current=(
            _security_holding(
                security_id="ES0113900J37",
                ticker="SAN",
                name="Banco Santander SA",
                weight_percent=2.0,
            ),
            _security_holding(
                security_id="FR0000120578",
                ticker="SAN",
                name="Sanofi SA",
                weight_percent=3.0,
            ),
        ),
    )

    payload = build_signal_report_payload(
        snapshots=snapshots,
        evidence=(
            _evidence(
                ticker="SAN",
                claim_scope="signal:security:ES0113900J37:weight_increase",
                evidence_role="interpretation_support",
                stance="supporting",
                strength="strong",
                relevance="high",
                novelty="new",
                interpretation_basis="Evidence is scoped to Banco Santander, not Sanofi.",
            ),
        ),
    )

    san_signals = [signal for signal in payload.signal_board if signal.ticker == "SAN"]

    assert {signal.claim_scope for signal in san_signals} == {
        "signal:security:FR0000120578:weight_increase",
        "signal:security:ES0113900J37:weight_increase",
    }
    support_by_claim = {
        signal.claim_scope: signal.score_components.external_evidence_support
        for signal in san_signals
    }
    assert support_by_claim["signal:security:ES0113900J37:weight_increase"] == 12
    assert support_by_claim["signal:security:FR0000120578:weight_increase"] == 0
    assert {signal.name for signal in san_signals} == {"Banco Santander SA", "Sanofi SA"}


def test_same_ticker_false_alias_dossiers_do_not_share_claim_evidence() -> None:
    snapshots = _identity_regression_snapshots(
        previous=(
            _security_holding(
                security_id="ES0113900J37",
                ticker="SAN",
                name="Banco Santander SA",
                weight_percent=1.0,
            ),
            _security_holding(
                security_id="FR0000120578",
                ticker="SAN",
                name="Sanofi SA",
                weight_percent=1.0,
            ),
        ),
        current=(
            _security_holding(
                security_id="ES0113900J37",
                ticker="SAN",
                name="Banco Santander SA",
                weight_percent=2.0,
            ),
            _security_holding(
                security_id="FR0000120578",
                ticker="SAN",
                name="Sanofi SA",
                weight_percent=3.0,
            ),
        ),
    )

    payload = build_signal_report_payload(
        snapshots=snapshots,
        evidence=(
            _evidence(
                ticker="SAN",
                claim_scope="signal:security:ES0113900J37:weight_increase",
                evidence_role="interpretation_support",
                stance="supporting",
                strength="strong",
                relevance="high",
                novelty="new",
                interpretation_basis="Evidence is scoped to Banco Santander, not Sanofi.",
            ),
        ),
    )

    santander = next(
        dossier for dossier in payload.ticker_dossiers if dossier.name == "Banco Santander SA"
    )
    sanofi = next(dossier for dossier in payload.ticker_dossiers if dossier.name == "Sanofi SA")
    santander_ledger = next(
        item
        for item in payload.evidence_ledger
        if item.evidence_id == "ev_claim_scoped"
    )

    assert santander.supporting_evidence == ("Curated evidence title",)
    assert sanofi.supporting_evidence == ()
    assert santander_ledger.used_in == (
        "signal:security:ES0113900J37:weight_increase",
        "ticker_dossier:security:ES0113900J37",
    )


def test_goog_googl_share_classes_remain_separate_with_bad_ticker_mapping() -> None:
    snapshots = _identity_regression_snapshots(
        previous=(
            _security_holding(
                security_id="US02079K1079",
                ticker="GOOG",
                name="Alphabet Inc. Class C",
                weight_percent=1.0,
            ),
            _security_holding(
                security_id="US02079K3059",
                ticker="GOOG",
                name="Alphabet Inc. Class A",
                weight_percent=1.0,
            ),
        ),
        current=(
            _security_holding(
                security_id="US02079K1079",
                ticker="GOOG",
                name="Alphabet Inc. Class C",
                weight_percent=2.0,
            ),
            _security_holding(
                security_id="US02079K3059",
                ticker="GOOG",
                name="Alphabet Inc. Class A",
                weight_percent=2.5,
            ),
        ),
    )

    payload = build_signal_report_payload(snapshots=snapshots, evidence=())

    goog_signals = [signal for signal in payload.signal_board if signal.ticker == "GOOG"]

    assert {signal.claim_scope for signal in goog_signals} == {
        "signal:security:US02079K1079:weight_increase",
        "signal:security:US02079K3059:weight_increase",
    }
    assert {signal.name for signal in goog_signals} == {
        "Alphabet Inc. Class C",
        "Alphabet Inc. Class A",
    }
    assert all(signal.security_group_id is None for signal in goog_signals)


def test_reviewed_same_share_class_alias_aggregates_by_security_group() -> None:
    snapshots = _identity_regression_snapshots(
        previous=(
            _security_holding(
                security_id="alias_primary",
                ticker="ALP",
                name="Alias Primary Corp.",
                weight_percent=1.0,
                security_group_id="group_alias",
                listing_key="XNAS:ALP",
                security_group_name="Reviewed Alias Corp.",
                security_group_ticker="ALP",
            ),
            _security_holding(
                security_id="alias_secondary",
                ticker="ALP",
                name="Alias Secondary Corp.",
                weight_percent=0.5,
                security_group_id="group_alias",
                listing_key="XNAS:ALP",
                security_group_name="Reviewed Alias Corp.",
                security_group_ticker="ALP",
            ),
        ),
        current=(
            _security_holding(
                security_id="alias_primary",
                ticker="ALP",
                name="Alias Primary Corp.",
                weight_percent=2.0,
                security_group_id="group_alias",
                listing_key="XNAS:ALP",
                security_group_name="Reviewed Alias Corp.",
                security_group_ticker="ALP",
            ),
            _security_holding(
                security_id="alias_secondary",
                ticker="ALP",
                name="Alias Secondary Corp.",
                weight_percent=1.5,
                security_group_id="group_alias",
                listing_key="XNAS:ALP",
                security_group_name="Reviewed Alias Corp.",
                security_group_ticker="ALP",
            ),
        ),
    )

    payload = build_signal_report_payload(snapshots=snapshots, evidence=())

    signal = next(signal for signal in payload.signal_board if signal.ticker == "ALP")
    dossier = next(item for item in payload.ticker_dossiers if item.ticker == "ALP")

    assert signal.claim_scope == "signal:security_group:group_alias:weight_increase"
    assert signal.aggregation_key == "group_alias"
    assert signal.security_group_id == "group_alias"
    assert signal.member_security_ids == ("alias_primary", "alias_secondary")
    assert signal.listing_keys == ("XNAS:ALP",)
    assert signal.name == "Reviewed Alias Corp."
    assert signal.weight_delta_pp == 2.0
    assert signal.participating_etfs == ("etf_identity",)
    assert dossier.holding_facts.member_security_ids == (
        "alias_primary",
        "alias_secondary",
    )


def test_reviewed_group_missing_display_label_uses_fallback_and_warning() -> None:
    snapshots = _identity_regression_snapshots(
        previous=(
            _security_holding(
                security_id="group_member_a",
                ticker="MLA",
                name="Member A Corp.",
                weight_percent=1.0,
                security_group_id="group_missing_label",
                listing_key="XNAS:MLA",
            ),
        ),
        current=(
            _security_holding(
                security_id="group_member_a",
                ticker="MLA",
                name="Member A Corp.",
                weight_percent=2.0,
                security_group_id="group_missing_label",
                listing_key="XNAS:MLA",
            ),
        ),
    )

    payload = build_signal_report_payload(snapshots=snapshots, evidence=())

    signal = payload.signal_board[0]
    issue = next(
        item
        for item in payload.data_quality.issues
        if item.code == "missing_security_group_display_label"
    )

    assert signal.claim_scope == "signal:security_group:group_missing_label:weight_increase"
    assert signal.name == "Security group group_missing_label"
    assert signal.data_quality_warnings == ("missing_security_group_display_label",)
    assert issue.scope == "security_group:group_missing_label"
    assert "group_missing_label" in issue.message


def test_context_low_relevance_or_repeat_evidence_does_not_affect_score() -> None:
    fixture = load_fixture_signal_report_inputs()
    baseline = build_signal_report_payload(
        snapshots=fixture.snapshots,
        focus_etf_id=fixture.focus_etf_id,
        evidence=(),
    )
    nvda_baseline = next(signal for signal in baseline.signal_board if signal.ticker == "NVDA")

    payload = build_signal_report_payload(
        snapshots=fixture.snapshots,
        focus_etf_id=fixture.focus_etf_id,
        evidence=(
            _evidence(
                ticker="NVDA",
                claim_scope=nvda_baseline.claim_scope,
                evidence_role="context",
                stance="supporting",
                strength="strong",
                relevance="high",
                novelty="new",
                interpretation_basis="Context only.",
            ),
            _evidence(
                evidence_id="ev_low_relevance",
                ticker="NVDA",
                claim_scope=nvda_baseline.claim_scope,
                evidence_role="interpretation_support",
                stance="supporting",
                strength="strong",
                relevance="low",
                novelty="new",
                interpretation_basis="Curated but not relevant enough.",
            ),
            _evidence(
                evidence_id="ev_repeat",
                ticker="NVDA",
                claim_scope=nvda_baseline.claim_scope,
                evidence_role="interpretation_support",
                stance="supporting",
                strength="strong",
                relevance="high",
                novelty="repeat",
                interpretation_basis="Repeat evidence should not move the score.",
            ),
        ),
    )

    nvda = next(signal for signal in payload.signal_board if signal.ticker == "NVDA")
    assert nvda.score_components.external_evidence_support == 0
    assert nvda.signal_score == nvda_baseline.signal_score


def test_counter_evidence_produces_conflicted_caution() -> None:
    fixture = load_fixture_signal_report_inputs()
    baseline = build_signal_report_payload(
        snapshots=fixture.snapshots,
        focus_etf_id=fixture.focus_etf_id,
        evidence=(),
    )
    nvda_baseline = next(signal for signal in baseline.signal_board if signal.ticker == "NVDA")

    payload = build_signal_report_payload(
        snapshots=fixture.snapshots,
        focus_etf_id=fixture.focus_etf_id,
        evidence=(
            _evidence(
                ticker="NVDA",
                claim_scope=nvda_baseline.claim_scope,
                evidence_role="interpretation_challenge",
                stance="counter",
                strength="moderate",
                relevance="medium",
                novelty="unknown",
                interpretation_basis="Curated challenge to this exact holdings-change claim.",
            ),
        ),
    )

    nvda = next(signal for signal in payload.signal_board if signal.ticker == "NVDA")
    assert nvda.score_components.contradiction_penalty == 10
    assert nvda.evidence_grade == "Conflicted"
    assert nvda.review_label == "caution"


def test_mapped_ticker_missing_price_gets_data_quality_penalty() -> None:
    fixture = load_fixture_signal_report_inputs()
    snapshots = _snapshots_with_current_updates(
        fixture.snapshots,
        etf_id="etf_focus_ai",
        security_id="sec_nvda",
        updates={"price_krw": None},
    )

    payload = build_signal_report_payload(
        snapshots=snapshots,
        focus_etf_id=fixture.focus_etf_id,
        evidence=fixture.evidence,
    )

    nvda = next(signal for signal in payload.signal_board if signal.ticker == "NVDA")
    assert nvda.score_components.data_quality_penalty == 6
    assert nvda.evidence_grade != "Unusable"


def test_missing_ticker_still_becomes_unusable_defer() -> None:
    fixture = load_fixture_signal_report_inputs()

    payload = build_signal_report_payload(
        snapshots=fixture.snapshots,
        focus_etf_id=fixture.focus_etf_id,
        evidence=fixture.evidence,
    )

    unmapped = next(signal for signal in payload.signal_board if signal.ticker is None)
    assert unmapped.score_components.data_quality_penalty == 20
    assert unmapped.evidence_grade == "Unusable"
    assert unmapped.review_label == "defer"


def test_operational_risk_failed_provenance_projects_high_data_quality_issue() -> None:
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
                    "cash_derivation_failure_ratio": 0.25,
                    "fit_failure_ratio": None,
                    "unusable_cash_weight_ratio": 0.1,
                    "ticker_mapping_coverage_ratio": 0.42,
                    "missing_source_date_count": 2,
                    "skipped_missing_security_id_count": 1,
                    "cash_derivation_failure_distribution": {
                        "by_reason": {"no_weight_fit_sample": 1},
                        "by_date": {"2026-05-08": 1},
                    },
                },
                "warnings": [],
                "risk_failures": [
                    {
                        "code": "low_ticker_mapping_coverage",
                        "message": "Ticker mapping coverage is too low.",
                        "metric": "ticker_mapping_coverage_ratio",
                        "value": 0.42,
                        "threshold": 0.5,
                    }
                ],
            },
        },
    )

    issue = next(
        item
        for item in payload.data_quality.issues
        if item.code == "operational_low_ticker_mapping_coverage"
    )
    assert issue.severity == "high"
    assert issue.scope == "operational_holdings"
    assert issue.message == "Ticker mapping coverage is too low."
    assert (
        "운영 보유 데이터 동기화 품질 리스크가 높아 "
        "운영 데이터 기반 신호는 검증 전 사용을 보류해야 합니다."
        in payload.data_quality.limitations
    )
    assert "operational_cash_derivation_failure_ratio=0.25" in payload.data_quality.coverage_notes
    assert "operational_fit_failure_ratio=not_applicable" in payload.data_quality.coverage_notes
    assert "operational_ticker_mapping_coverage_ratio=0.42" in payload.data_quality.coverage_notes
    assert not any(
        "cash_derivation_failure_distribution" in note
        for note in payload.data_quality.coverage_notes
    )


def test_operational_focus_handoff_notes_mixed_windows_and_exclusions() -> None:
    fixture = load_fixture_signal_report_inputs()

    payload = build_signal_report_payload(
        snapshots=fixture.snapshots,
        focus_etf_id=fixture.focus_etf_id,
        focus_etf_ids=("etf_focus_ai", "etf_peer_ai", "etf_peer_cloud"),
        evidence=fixture.evidence,
        operational_provenance={
            "focus_eligibility": {
                "mixed_comparison_windows": True,
                "handoff_exclusions": [
                    {
                        "source_provider_id": "hyundai",
                        "etf_id": "etf_hyundai_2912753",
                        "scope": "holdings_snapshot",
                        "reason_code": "invalid_provider_payload",
                        "observed_dates_missing": ["2026-05-11"],
                    }
                ],
            }
        },
    )

    assert payload.meta.focus_etf_ids == (
        "etf_focus_ai",
        "etf_peer_ai",
        "etf_peer_cloud",
    )
    assert (
        "Operational handoff used mixed per-ETF comparison windows."
        in payload.data_quality.limitations
    )
    assert (
        "Operational handoff excluded 1 unavailable provider target with path-safe evidence."
        in payload.data_quality.limitations
    )
    assert "focus_mixed_comparison_windows=true" in payload.data_quality.coverage_notes
    assert "handoff_exclusion_count=1" in payload.data_quality.coverage_notes
    rendered = json.dumps(payload.model_dump(mode="json"), ensure_ascii=False)
    assert "provider_etf_id" not in rendered
    assert "raw provider" not in rendered


def test_operational_warning_provenance_projects_medium_issue_and_limitation() -> None:
    fixture = load_fixture_signal_report_inputs()

    payload = build_signal_report_payload(
        snapshots=fixture.snapshots,
        focus_etf_id=fixture.focus_etf_id,
        evidence=fixture.evidence,
        operational_provenance={
            "sync_metadata_available": True,
            "sync_quality": {
                "schema_version": "agent_treport.operational_holdings.sync_quality.v1",
                "status": "warning",
                "metrics": {
                    "cash_derivation_failure_ratio": 0.08,
                    "fit_failure_ratio": 0.03,
                    "unusable_cash_weight_ratio": None,
                    "ticker_mapping_coverage_ratio": 0.75,
                    "missing_source_date_count": 1,
                    "skipped_missing_security_id_count": 0,
                },
                "warnings": [
                    {
                        "code": "cash_derivation_failure_ratio_high",
                        "message": "Cash derivation failures were elevated.",
                        "metric": "cash_derivation_failure_ratio",
                        "value": 0.08,
                        "threshold": 0.05,
                    }
                ],
                "risk_failures": [],
            },
        },
    )

    issue = next(
        item
        for item in payload.data_quality.issues
        if item.code == "operational_cash_derivation_failure_ratio_high"
    )
    assert issue.severity == "medium"
    assert issue.scope == "operational_holdings"
    assert issue.message == "Cash derivation failures were elevated."
    assert "운영 보유 데이터 동기화 품질 경고가 있어 일부 신호 해석은 제한됩니다." in (
        payload.data_quality.limitations
    )
    assert "operational_unusable_cash_weight_ratio=not_applicable" in (
        payload.data_quality.coverage_notes
    )
    assert "operational_missing_source_date_count=1" in payload.data_quality.coverage_notes


def test_unavailable_operational_sync_metadata_projects_medium_issue() -> None:
    fixture = load_fixture_signal_report_inputs()

    payload = build_signal_report_payload(
        snapshots=fixture.snapshots,
        focus_etf_id=fixture.focus_etf_id,
        evidence=fixture.evidence,
        operational_provenance={"sync_metadata_available": False},
    )

    issue = next(
        item
        for item in payload.data_quality.issues
        if item.code == "operational_sync_metadata_unavailable"
    )
    assert issue.severity == "medium"
    assert issue.scope == "operational_holdings"
    assert issue.message == (
        "Operational holdings sync metadata was unavailable, so source-data diagnostics were "
        "not included."
    )
    assert (
        "운영 보유 데이터 동기화 메타데이터가 없어 원천 데이터 진단이 포함되지 않았습니다."
        in payload.data_quality.limitations
    )


def test_missing_classification_and_price_lower_score_without_unusable() -> None:
    fixture = load_fixture_signal_report_inputs()
    baseline = build_signal_report_payload(
        snapshots=fixture.snapshots,
        focus_etf_id=fixture.focus_etf_id,
        evidence=(),
    )
    baseline_nvda = next(signal for signal in baseline.signal_board if signal.ticker == "NVDA")
    snapshots = _snapshots_with_current_updates(
        fixture.snapshots,
        etf_id="etf_focus_ai",
        security_id="sec_nvda",
        updates={"sector": None, "theme": None, "country": None, "price_krw": None},
    )

    payload = build_signal_report_payload(
        snapshots=snapshots,
        focus_etf_id=fixture.focus_etf_id,
        evidence=(),
    )

    nvda = next(signal for signal in payload.signal_board if signal.ticker == "NVDA")
    assert nvda.score_components.data_quality_penalty == 14
    assert nvda.signal_score == baseline_nvda.signal_score - 14
    assert nvda.evidence_grade != "Unusable"
    assert nvda.review_label != "defer"


def test_no_material_holdings_change_builds_empty_signal_payload() -> None:
    fixture = load_fixture_signal_report_inputs()
    snapshots = _snapshots_with_current_equal_to_previous(fixture.snapshots)

    payload = build_signal_report_payload(
        snapshots=snapshots,
        focus_etf_id=fixture.focus_etf_id,
        evidence=fixture.evidence,
    )

    assert payload.signal_board == ()
    assert payload.ticker_dossiers == ()
    assert payload.executive_summary.headline == (
        "해당 기간에 의미 있는 ETF 보유 변화 신호가 발견되지 않았습니다."
    )
    assert payload.market_map.by_theme == ()
    assert payload.market_map.by_sector == ()
    assert payload.market_map.by_country == ()
    assert payload.coverage.holding_rows > 0
    assert payload.data_quality.coverage_notes


def test_omitted_evidence_marks_news_coverage_as_not_run() -> None:
    fixture = load_fixture_signal_report_inputs()

    payload = build_signal_report_payload(
        snapshots=fixture.snapshots,
        focus_etf_id=fixture.focus_etf_id,
    )

    assert payload.coverage.news_coverage_ratio is None
    assert payload.coverage.analyst_coverage_ratio is None
    assert "news_coverage_ratio=not_run" in payload.data_quality.coverage_notes
    assert "analyst_coverage_ratio=not_provided" in payload.data_quality.coverage_notes


def test_empty_evidence_marks_news_coverage_as_zero_not_unknown() -> None:
    fixture = load_fixture_signal_report_inputs()

    payload = build_signal_report_payload(
        snapshots=fixture.snapshots,
        focus_etf_id=fixture.focus_etf_id,
        evidence=(),
    )

    assert payload.coverage.news_coverage_ratio == 0.0
    assert payload.coverage.analyst_coverage_ratio is None
    assert "news_coverage_ratio=0.00" in payload.data_quality.coverage_notes
    assert "analyst_coverage_ratio=not_provided" in payload.data_quality.coverage_notes


def test_evidence_ledger_includes_holding_change_entries_for_signals() -> None:
    fixture = load_fixture_signal_report_inputs()

    payload = build_signal_report_payload(
        snapshots=fixture.snapshots,
        focus_etf_id=fixture.focus_etf_id,
        evidence=(),
    )

    holding_entries = [item for item in payload.evidence_ledger if item.type == "holding_change"]
    assert len(holding_entries) == len(payload.signal_board)
    nvda = next(signal for signal in payload.signal_board if signal.ticker == "NVDA")
    nvda_entry = next(item for item in holding_entries if item.claim_scope == nvda.claim_scope)
    assert nvda_entry.observed_direction == nvda.signal_direction
    assert nvda_entry.evidence_role == "primary_observation"
    assert nvda_entry.stance == "supporting"
    assert nvda_entry.used_in == (nvda.claim_scope, "ticker_dossier:security:sec_nvda")
    assert "비중 확대" in nvda_entry.title


def test_holding_change_entries_do_not_inflate_external_evidence_support() -> None:
    fixture = load_fixture_signal_report_inputs()

    payload = build_signal_report_payload(
        snapshots=fixture.snapshots,
        focus_etf_id=fixture.focus_etf_id,
        evidence=(),
    )

    nvda = next(signal for signal in payload.signal_board if signal.ticker == "NVDA")
    assert any(
        item.type == "holding_change" and item.claim_scope == nvda.claim_scope
        for item in payload.evidence_ledger
    )
    assert nvda.score_components.external_evidence_support == 0


def test_external_evidence_titles_are_report_safe_for_rendered_reports() -> None:
    fixture = load_fixture_signal_report_inputs()
    base_payload = build_signal_report_payload(
        snapshots=fixture.snapshots,
        focus_etf_id=fixture.focus_etf_id,
        evidence=(),
    )
    nvda = next(signal for signal in base_payload.signal_board if signal.ticker == "NVDA")

    payload = build_signal_report_payload(
        snapshots=fixture.snapshots,
        focus_etf_id=fixture.focus_etf_id,
        evidence=(
            _evidence(
                evidence_id="ev_unsafe_en",
                ticker="NVDA",
                claim_scope=nvda.claim_scope,
                evidence_role="context",
                stance="neutral",
                strength="weak",
                relevance="medium",
                novelty="new",
                interpretation_basis="Provider-normalized news context.",
                evidence_type="news",
                source="Yahoo Finance",
                title="QQQM Is a Better Buy Than QQQ -- For 1 Powerful Reason",
            ),
            _evidence(
                evidence_id="ev_unsafe_ko",
                ticker="NVDA",
                claim_scope=nvda.claim_scope,
                evidence_role="context",
                stance="neutral",
                strength="weak",
                relevance="medium",
                novelty="new",
                interpretation_basis="Provider-normalized news context.",
                evidence_type="news",
                source="Naver News Search",
                title="KB증권, 5월 셋째주 삼성전기 등 34종목 매수 추천",
            ),
            _evidence(
                evidence_id="ev_blank",
                ticker="NVDA",
                claim_scope=nvda.claim_scope,
                evidence_role="context",
                stance="neutral",
                strength="weak",
                relevance="medium",
                novelty="new",
                interpretation_basis="Provider-normalized news context.",
                evidence_type="news",
                source="NewsAPI",
                title=" ",
            ),
        ),
    )

    serialized = json.dumps(payload.model_dump(mode="json"), ensure_ascii=False)
    markdown = MarkdownSignalReportRenderer().render(payload=payload)
    html = HTMLResearchReportRenderer().render(payload=payload)
    quality = ReportQualityGate().evaluate(payload=payload, markdown=markdown, html=html)

    assert quality.status == "passed"
    assert "Better Buy" not in serialized
    assert "매수 추천" not in serialized
    assert "ev_blank" in serialized
    assert "NVDA news evidence from NewsAPI" in serialized


def _evidence(
    *,
    evidence_id: str = "ev_claim_scoped",
    ticker: str,
    claim_scope: str,
    evidence_role: str,
    stance: str,
    strength: str,
    relevance: str | None,
    novelty: str | None,
    interpretation_basis: str | None,
    evidence_type: str = "earnings",
    source: str = "Focused test evidence",
    title: str = "Curated evidence title",
) -> EvidenceItemInput:
    return EvidenceItemInput.model_validate(
        {
            "evidence_id": evidence_id,
            "ticker": ticker,
            "scope": None,
            "type": evidence_type,
            "source": source,
            "title": title,
            "published_at": "2026-05-06T13:00:00+00:00",
            "url": "https://example.com/evidence",
            "stance": stance,
            "strength": strength,
            "claim_scope": claim_scope,
            "evidence_role": evidence_role,
            "relevance": relevance,
            "novelty": novelty,
            "interpretation_basis": interpretation_basis,
        }
    )


def _snapshots_with_current_updates(
    snapshots: MultiETFHoldingsSnapshots,
    *,
    etf_id: str,
    security_id: str,
    updates: dict[str, object],
) -> MultiETFHoldingsSnapshots:
    data = snapshots.model_dump(mode="json")
    for etf in data["etfs"]:
        if etf["etf_id"] != etf_id:
            continue
        for holding in etf["current"]:
            if holding["security_id"] == security_id:
                holding.update(updates)
                return MultiETFHoldingsSnapshots.model_validate(data)
    raise AssertionError(f"holding not found: {etf_id}:{security_id}")


def _snapshots_with_current_equal_to_previous(
    snapshots: MultiETFHoldingsSnapshots,
) -> MultiETFHoldingsSnapshots:
    data = snapshots.model_dump(mode="json")
    for etf in data["etfs"]:
        etf["current"] = list(etf["previous"])
    return MultiETFHoldingsSnapshots.model_validate(data)


def _identity_regression_snapshots(
    *,
    previous: tuple[SecurityHolding, ...],
    current: tuple[SecurityHolding, ...],
) -> MultiETFHoldingsSnapshots:
    return MultiETFHoldingsSnapshots(
        as_of_date="2026-05-15",
        previous_date="2026-05-08",
        current_date="2026-05-15",
        lookback_days=7,
        universe="identity_regression",
        etfs=(
            ETFHoldingsSnapshots(
                etf_id="etf_identity",
                etf_name="Identity Regression ETF",
                brand_id="brand_identity",
                source_provider_id="fixture",
                previous=previous,
                current=current,
            ),
        ),
    )


def _security_holding(
    *,
    security_id: str,
    ticker: str | None,
    name: str,
    weight_percent: float,
    security_group_id: str | None = None,
    listing_key: str | None = None,
    security_group_name: str | None = None,
    security_group_ticker: str | None = None,
) -> SecurityHolding:
    return SecurityHolding(
        security_id=security_id,
        ticker=ticker,
        name=name,
        market="US",
        sector="Technology",
        theme="AI",
        country="US",
        weight_percent=weight_percent,
        shares=100.0,
        market_value_krw=1000.0 * weight_percent,
        price_krw=10.0,
        is_cash=False,
        security_group_id=security_group_id,
        listing_key=listing_key,
        security_group_name=security_group_name,
        security_group_ticker=security_group_ticker,
    )
