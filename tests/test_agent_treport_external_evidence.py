from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TypedDict

import pytest
from agent_pack.models import JsonValue

from agent_treport.signal_report import (
    build_signal_report_payload,
    load_fixture_signal_report_inputs,
)
from agent_treport.signal_report.external_evidence import (
    AlignmentDecision,
    ExternalEvidenceCandidate,
    ExternalEvidenceCollectionError,
    ExternalEvidenceProviderOutcome,
    ExternalEvidenceRequest,
    ExternalEvidenceTarget,
    FakeAlignmentClassifier,
    collect_external_evidence,
    compile_candidates_to_evidence,
    safe_public_url,
)
from agent_treport.signal_report.external_evidence.contracts import (
    EvidenceCategory,
    ExternalEvidenceProvider,
)
from agent_treport.signal_report.external_evidence.providers import (
    AlphaVantageProvider,
    DartProvider,
    FinnhubProvider,
    NaverNewsProvider,
    NewsApiProvider,
    SecEdgarProvider,
    YFinanceProvider,
)

FIXTURE_HOLDINGS = Path("src/agent_treport/fixtures/signal_report/holdings.json")


class _HttpCall(TypedDict):
    url: str
    params: dict[str, str]
    headers: dict[str, str]
    timeout: float


def test_fixture_collection_selects_top_targets_and_writes_handoff(
    tmp_path: Path,
) -> None:
    evidence_path = tmp_path / "external_evidence.json"
    summary_path = tmp_path / "external_evidence_summary.json"

    result = collect_external_evidence(
        ExternalEvidenceRequest(
            holdings_source="fixture",
            holdings_path=FIXTURE_HOLDINGS,
            provider_ids=("fixture_financial", "fixture_disclosure", "fixture_news"),
            live=False,
            max_targets=2,
            evidence_path=evidence_path,
            summary_path=summary_path,
            now=lambda: datetime(2026, 5, 16, 0, 0, tzinfo=UTC),
        )
    )

    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    assert [target["ticker"] for target in result.summary.target_selection["selected_targets"]] == [
        "NVDA",
        "PLTR",
    ]
    assert summary["target_selection"]["excluded_targets"]
    assert summary["policy_failure"] is None
    assert summary["dedupe"]["deduped_count"] >= 1
    assert {
        (item["ticker"], item["type"], item["source"])
        for item in evidence
    } == {
        ("NVDA", "price_volume", "Fixture Financial Metrics"),
        ("NVDA", "company_disclosure", "Fixture Disclosure"),
        ("NVDA", "news", "Fixture News"),
        ("PLTR", "price_volume", "Fixture Financial Metrics"),
        ("PLTR", "company_disclosure", "Fixture Disclosure"),
        ("PLTR", "news", "Fixture News"),
    }
    assert "raw_payload" not in summary_path.read_text(encoding="utf-8")
    assert "api_key" not in summary_path.read_text(encoding="utf-8")


def test_collection_can_start_from_existing_signal_board_targets(tmp_path: Path) -> None:
    target_candidates_path = tmp_path / "target_candidates.json"
    target_candidates_path.write_text(
        json.dumps(
            {
                "signal_board": [
                    {
                        "rank": 1,
                        "ticker": "AAPL",
                        "name": "Apple Inc.",
                        "aggregation_key": "sec_aapl",
                        "member_security_ids": ["sec_aapl"],
                        "listing_keys": ["AAPL:XNAS"],
                        "claim_scope": "signal:security:sec_aapl:weight_increase",
                        "signal_type": "weight_increase",
                        "signal_direction": "increase",
                        "primary_reason": "Existing report target.",
                    },
                    {
                        "rank": 2,
                        "ticker": None,
                        "name": "Tickerless target",
                        "aggregation_key": "sec_tickerless",
                        "member_security_ids": ["sec_tickerless"],
                        "claim_scope": "signal:security:sec_tickerless:weight_increase",
                        "signal_type": "weight_increase",
                        "signal_direction": "increase",
                        "primary_reason": "Missing ticker.",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    result = collect_external_evidence(
        ExternalEvidenceRequest(
            holdings_source="targets",
            target_candidates_path=target_candidates_path,
            provider_ids=("fixture_news",),
            live=False,
            max_targets=1,
            evidence_path=tmp_path / "external_evidence.json",
            summary_path=tmp_path / "external_evidence_summary.json",
            now=lambda: datetime(2026, 5, 16, 0, 0, tzinfo=UTC),
        )
    )

    selected = result.summary.target_selection["selected_targets"]
    excluded = result.summary.target_selection["excluded_targets"]
    assert selected == (
        {
            "aggregation_key": "sec_aapl",
            "claim_scope": "signal:security:sec_aapl:weight_increase",
            "listing_keys": ("AAPL:XNAS",),
            "member_security_ids": ("sec_aapl",),
            "name": "Apple Inc.",
            "rank": 1,
            "security_group_id": None,
            "signal_direction": "increase",
            "signal_type": "weight_increase",
            "summary": "Existing report target.",
            "ticker": "AAPL",
        },
    )
    assert excluded[0]["reason_code"] == "missing_ticker"


def test_ambiguous_bare_ticker_targets_are_excluded_from_enrichment(tmp_path: Path) -> None:
    target_candidates_path = tmp_path / "target_candidates.json"
    target_candidates_path.write_text(
        json.dumps(
            {
                "signal_board": [
                    {
                        "rank": 1,
                        "ticker": "SAN",
                        "name": "Banco Santander SA",
                        "claim_scope": "signal:security:ES0113900J37:weight_increase",
                        "signal_type": "weight_increase",
                        "signal_direction": "increase",
                        "primary_reason": "Identity-scoped Santander signal.",
                    },
                    {
                        "rank": 2,
                        "ticker": "SAN",
                        "name": "Sanofi SA",
                        "claim_scope": "signal:security:FR0000120578:weight_increase",
                        "signal_type": "weight_increase",
                        "signal_direction": "increase",
                        "primary_reason": "Identity-scoped Sanofi signal.",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    result = collect_external_evidence(
        ExternalEvidenceRequest(
            holdings_source="targets",
            target_candidates_path=target_candidates_path,
            provider_ids=("fixture_news",),
            live=False,
            max_targets=2,
            evidence_path=tmp_path / "external_evidence.json",
            summary_path=tmp_path / "external_evidence_summary.json",
            now=lambda: datetime(2026, 5, 16, 0, 0, tzinfo=UTC),
        )
    )

    selected = result.summary.target_selection["selected_targets"]
    excluded = result.summary.target_selection["excluded_targets"]
    notes = result.summary.category_coverage["news"]["notes"]

    assert selected == ()
    assert [item["reason_code"] for item in excluded] == [
        "ambiguous_bare_ticker",
        "ambiguous_bare_ticker",
    ]
    assert {item["claim_scope"] for item in excluded} == {
        "signal:security:ES0113900J37:weight_increase",
        "signal:security:FR0000120578:weight_increase",
    }
    assert "news:SAN=excluded_ambiguous_bare_ticker" in notes


def test_policy_failure_writes_partial_evidence_and_summary_before_raising(
    tmp_path: Path,
) -> None:
    evidence_path = tmp_path / "external_evidence.json"
    summary_path = tmp_path / "external_evidence_summary.json"

    with pytest.raises(ExternalEvidenceCollectionError) as exc_info:
        collect_external_evidence(
            ExternalEvidenceRequest(
                holdings_source="fixture",
                holdings_path=FIXTURE_HOLDINGS,
                provider_ids=("fixture_financial", "failing_news"),
                live=False,
                max_targets=1,
                evidence_path=evidence_path,
                summary_path=summary_path,
                provider_overrides={
                    "failing_news": _outcome_provider(
                        ExternalEvidenceProviderOutcome(
                            provider_id="failing_news",
                            category="news",
                            status="credential_required",
                            error_code="credential_required",
                            retryable=False,
                            attempt_count=1,
                            stopped_reason="missing_api_key",
                            target_tickers=("NVDA",),
                            safe_message="News credential is required.",
                        )
                    )
                },
                now=lambda: datetime(2026, 5, 16, 0, 0, tzinfo=UTC),
            )
        )

    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    assert exc_info.value.error_code == "credential_required"
    assert [item["source"] for item in evidence] == ["Fixture Financial Metrics"]
    assert summary["policy_failure"]["error_code"] == "credential_required"
    assert summary["provider_outcomes"][-1]["safe_message"] == "News credential is required."


def test_cooldown_suppresses_live_provider_calls_for_24_hours(tmp_path: Path) -> None:
    cooldown_path = tmp_path / "cooldowns.json"
    now = datetime(2026, 5, 16, 0, 0, tzinfo=UTC)
    cooldown_until = now + timedelta(hours=1)
    cooldown_path.write_text(
        json.dumps(
            {
                "schema_version": "agent_treport.external_evidence.cooldown.v1",
                "providers": {
                    "counting_live": {
                        "cooldown_until": cooldown_until.isoformat(),
                        "reason": "rate_limited_exhausted",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    calls: list[str] = []

    result = collect_external_evidence(
        ExternalEvidenceRequest(
            holdings_source="fixture",
            holdings_path=FIXTURE_HOLDINGS,
            provider_ids=("counting_live",),
            live=True,
            max_targets=1,
            evidence_path=tmp_path / "external_evidence.json",
            summary_path=tmp_path / "external_evidence_summary.json",
            cooldown_path=cooldown_path,
            provider_overrides={
                "counting_live": _counting_provider(
                    provider_id="counting_live",
                    category="news",
                    calls=calls,
                )
            },
            now=lambda: now,
        )
    )

    assert calls == []
    assert result.summary.provider_outcomes[0].status == "cooldown_active"
    assert result.summary.provider_outcomes[0].stopped_reason == "rate_limited_exhausted"


def test_collection_can_ignore_cooldown_for_one_session(tmp_path: Path) -> None:
    cooldown_path = tmp_path / "cooldowns.json"
    now = datetime(2026, 5, 16, 0, 0, tzinfo=UTC)
    cooldown_path.write_text(
        json.dumps(
            {
                "schema_version": "agent_treport.external_evidence.cooldown.v1",
                "providers": {
                    "counting_live": {
                        "cooldown_until": (now + timedelta(hours=1)).isoformat(),
                        "reason": "blocked",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    calls: list[str] = []

    result = collect_external_evidence(
        ExternalEvidenceRequest(
            holdings_source="fixture",
            holdings_path=FIXTURE_HOLDINGS,
            provider_ids=("counting_live",),
            live=True,
            max_targets=1,
            evidence_path=tmp_path / "external_evidence.json",
            summary_path=tmp_path / "external_evidence_summary.json",
            cooldown_path=cooldown_path,
            ignore_cooldown=True,
            provider_overrides={
                "counting_live": _counting_provider(
                    provider_id="counting_live",
                    category="news",
                    calls=calls,
                )
            },
            now=lambda: now,
        )
    )

    assert calls == ["NVDA"]
    assert result.summary.provider_outcomes[0].status == "success"


def test_sec_edgar_rate_limit_outcome_writes_15_minute_cooldown(tmp_path: Path) -> None:
    now = datetime(2026, 5, 16, 0, 0, tzinfo=UTC)
    cooldown_path = tmp_path / "cooldowns.json"

    with pytest.raises(ExternalEvidenceCollectionError):
        collect_external_evidence(
            ExternalEvidenceRequest(
                holdings_source="fixture",
                holdings_path=FIXTURE_HOLDINGS,
                provider_ids=("sec_edgar",),
                live=True,
                max_targets=1,
                evidence_path=tmp_path / "external_evidence.json",
                summary_path=tmp_path / "external_evidence_summary.json",
                cooldown_path=cooldown_path,
                provider_overrides={
                    "sec_edgar": _outcome_provider(
                        ExternalEvidenceProviderOutcome(
                            provider_id="sec_edgar",
                            category="disclosure",
                            status="rate_limited_exhausted",
                            error_code="rate_limited_exhausted",
                            retryable=False,
                            attempt_count=3,
                            stopped_reason="http_403",
                            target_tickers=("NVDA",),
                            safe_message="SEC EDGAR request retry policy was exhausted.",
                        )
                    )
                },
                now=lambda: now,
            )
        )

    cooldowns = json.loads(cooldown_path.read_text(encoding="utf-8"))
    assert cooldowns["providers"]["sec_edgar"] == {
        "cooldown_until": (now + timedelta(minutes=15)).isoformat(),
        "reason": "rate_limited_exhausted",
    }


def test_claim_alignment_promotes_only_high_confidence_grounded_decisions() -> None:
    fixture = load_fixture_signal_report_inputs()
    payload = build_signal_report_payload(
        snapshots=fixture.snapshots,
        focus_etf_id=fixture.focus_etf_id,
        evidence=(),
    )
    nvda = next(signal for signal in payload.signal_board if signal.ticker == "NVDA")
    candidates = (
        _candidate("cand_support", ticker="NVDA", category="news", title="NVIDIA raises guidance"),
        _candidate("cand_weak", ticker="NVDA", category="news", title="NVIDIA context mention"),
    )
    classifier = FakeAlignmentClassifier(
        decisions={
            "cand_support": AlignmentDecision(
                candidate_id="cand_support",
                alignment_stance="supporting",
                evidence_role="interpretation_support",
                relevance="high",
                confidence=0.91,
                interpretation_basis="The article directly supports the NVDA accumulation claim.",
            ),
            "cand_weak": AlignmentDecision(
                candidate_id="cand_weak",
                alignment_stance="supporting",
                evidence_role="interpretation_support",
                relevance="high",
                confidence=0.62,
                interpretation_basis="Weakly connected.",
            ),
        }
    )

    evidence = compile_candidates_to_evidence(
        candidates=candidates,
        signal_rows=payload.signal_board,
        classifier=classifier,
    )

    promoted = next(item for item in evidence if item.evidence_id == "cand_support")
    weak = next(item for item in evidence if item.evidence_id == "cand_weak")
    assert promoted.claim_scope == nvda.claim_scope
    assert promoted.evidence_role == "interpretation_support"
    assert promoted.interpretation_basis is not None
    assert weak.claim_scope is None
    assert weak.evidence_role == "context"
    assert weak.interpretation_basis is None


def test_safe_url_filtering_rejects_api_signed_tracking_and_raw_query_urls() -> None:
    assert safe_public_url(
        "https://www.sec.gov/Archives/edgar/data/320193/000032019326000001/index.html",
        provider_id="sec_edgar",
    ) == "https://www.sec.gov/Archives/edgar/data/320193/000032019326000001/index.html"
    assert safe_public_url(
        "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260516000001",
        provider_id="dart",
    ) == "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260516000001"
    assert safe_public_url(
        "https://www.alphavantage.co/query?function=NEWS_SENTIMENT&apikey=demo",
        provider_id="alpha_vantage",
    ) is None
    assert safe_public_url(
        "https://static2.finnhub.io/file/report.pdf?Authorization=secret",
        provider_id="finnhub",
    ) is None
    assert safe_public_url(
        "https://example.com/article?utm_source=provider",
        provider_id="newsapi",
    ) is None


def test_yfinance_missing_optional_dependency_returns_provider_outcome(monkeypatch) -> None:
    def missing_dependency(name: str):
        assert name == "yfinance"
        raise ModuleNotFoundError("No module named 'yfinance'")

    monkeypatch.setattr(
        "agent_treport.signal_report.external_evidence.providers.importlib.import_module",
        missing_dependency,
    )
    target = ExternalEvidenceTarget(
        ticker="NVDA",
        name="NVIDIA Corp.",
        aggregation_key="sec_nvda",
        member_security_ids=("sec_nvda",),
        claim_scope="signal:security:sec_nvda:weight_increase",
        rank=1,
        summary="test target",
    )

    candidates, outcome = YFinanceProvider().collect(
        (target,),
        live=True,
        request_context=_request_context(),
    )

    assert candidates == ()
    assert outcome.provider_id == "yfinance"
    assert outcome.status == "provider_unavailable"
    assert outcome.error_code == "provider_unavailable"
    assert outcome.stopped_reason == "missing_optional_dependency"


def test_live_providers_use_env_example_credential_names_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _provider_target("NVDA")
    calls: list[_HttpCall] = []

    def fake_get(
        url: str,
        *,
        params: Mapping[str, str],
        headers: Mapping[str, str],
        timeout: float,
    ) -> _FakeResponse:
        calls.append(
            {
                "url": url,
                "params": dict(params),
                "headers": dict(headers),
                "timeout": timeout,
            }
        )
        if "newsapi.org" in url:
            return _FakeResponse({"status": "ok", "articles": []})
        if "alphavantage.co" in url:
            return _FakeResponse({"feed": []})
        if "company_tickers.json" in url:
            return _FakeResponse({})
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(
        "agent_treport.signal_report.external_evidence.providers.requests.get",
        fake_get,
    )
    monkeypatch.chdir(tmp_path)

    monkeypatch.setenv("NEWS_API_KEY", "news-example")
    monkeypatch.delenv("NEWSAPI_API_KEY", raising=False)
    _, news_outcome = NewsApiProvider().collect(
        (target,), live=True, request_context=_request_context()
    )
    assert news_outcome.status == "no_data"
    assert calls[-1]["headers"] == {"X-Api-Key": "news-example"}

    monkeypatch.delenv("NEWS_API_KEY", raising=False)
    monkeypatch.setenv("NEWSAPI_API_KEY", "legacy-news")
    _, news_legacy_outcome = NewsApiProvider().collect(
        (target,), live=True, request_context=_request_context()
    )
    assert news_legacy_outcome.status == "credential_required"

    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", "alpha-example")
    monkeypatch.delenv("ALPHA_VANTAGE_API_KEY", raising=False)
    _, alpha_outcome = AlphaVantageProvider().collect(
        (target,), live=True, request_context=_request_context()
    )
    assert alpha_outcome.status == "no_data"
    assert calls[-1]["params"]["apikey"] == "alpha-example"

    monkeypatch.delenv("ALPHAVANTAGE_API_KEY", raising=False)
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "legacy-alpha")
    _, alpha_legacy_outcome = AlphaVantageProvider().collect(
        (target,), live=True, request_context=_request_context()
    )
    assert alpha_legacy_outcome.status == "credential_required"

    monkeypatch.setenv("SEC_USER_AGENT", "Agent TReport test@example.com")
    monkeypatch.delenv("AGENT_TREPORT_SEC_USER_AGENT", raising=False)
    _, sec_outcome = SecEdgarProvider().collect(
        (target,), live=True, request_context=_request_context()
    )
    assert sec_outcome.status == "no_data"
    assert calls[-1]["headers"]["User-Agent"] == "Agent TReport test@example.com"

    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    monkeypatch.setenv("AGENT_TREPORT_SEC_USER_AGENT", "legacy sec")
    _, sec_legacy_outcome = SecEdgarProvider().collect(
        (target,), live=True, request_context=_request_context()
    )
    assert sec_legacy_outcome.status == "credential_required"


def test_alpha_vantage_groups_news_sentiment_targets_with_policy_cap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    targets = tuple(
        _provider_target(ticker)
        for ticker in ("NVDA", "MSFT", "AVGO", "AAPL", "TSLA", "AMD")
    )
    calls: list[_HttpCall] = []

    def fake_get(
        url: str,
        *,
        params: Mapping[str, str],
        headers: Mapping[str, str],
        timeout: float,
    ) -> _FakeResponse:
        calls.append(
            {
                "url": url,
                "params": dict(params),
                "headers": dict(headers),
                "timeout": timeout,
            }
        )
        return _FakeResponse(
            {
                "feed": [
                    {
                        "title": "NVIDIA data center update",
                        "source": "Alpha Source",
                        "time_published": "20260516T010000",
                        "summary": "NVIDIA update",
                        "url": "https://example.com/nvidia",
                        "overall_sentiment_label": "Neutral",
                        "ticker_sentiment": [{"ticker": "NVDA"}],
                    },
                    {
                        "title": "Microsoft AI update",
                        "source": "Alpha Source",
                        "time_published": "20260516T020000",
                        "summary": "Microsoft update",
                        "url": "https://example.com/microsoft",
                        "overall_sentiment_label": "Neutral",
                        "ticker_sentiment": [{"ticker": "MSFT"}],
                    },
                ]
            }
        )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", "alpha-example")
    monkeypatch.setattr(
        "agent_treport.signal_report.external_evidence.providers.requests.get",
        fake_get,
    )

    candidates, outcome = AlphaVantageProvider().collect(
        targets, live=True, request_context=_request_context()
    )

    assert len(calls) == 1
    assert calls[0]["params"]["function"] == "NEWS_SENTIMENT"
    assert calls[0]["params"]["tickers"] == "NVDA,MSFT,AVGO,AAPL,TSLA"
    assert outcome.status == "success"
    assert outcome.target_tickers == ("NVDA", "MSFT", "AVGO", "AAPL", "TSLA")
    assert outcome.metadata == {
        "policy": "alpha_vantage_free_api_grouped_news_sentiment",
        "provider_target_cap": 5,
        "requested_target_count": 6,
        "queried_target_count": 5,
        "omitted_target_count": 1,
    }
    assert {candidate.ticker for candidate in candidates} == {"NVDA", "MSFT"}


def test_live_providers_load_env_file_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _provider_target("NVDA")
    calls: list[_HttpCall] = []

    def fake_get(
        url: str,
        *,
        params: Mapping[str, str],
        headers: Mapping[str, str],
        timeout: float,
    ) -> _FakeResponse:
        calls.append(
            {
                "url": url,
                "params": dict(params),
                "headers": dict(headers),
                "timeout": timeout,
            }
        )
        if "finnhub.io" in url:
            return _FakeResponse({"c": 100.0, "d": 1.0, "dp": 1.0, "t": 1778880000})
        if "alphavantage.co" in url:
            return _FakeResponse({"feed": []})
        if "newsapi.org" in url:
            return _FakeResponse({"status": "ok", "articles": []})
        if "naver.com" in url:
            return _FakeResponse({"items": []})
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                'FINNHUB_API_KEY="finnhub-dotenv"',
                'ALPHAVANTAGE_API_KEY="alpha-dotenv"',
                'NEWS_API_KEY="news-dotenv"',
                'NAVER_CLIENT_ID="naver-id-dotenv"',
                'NAVER_CLIENT_SECRET="naver-secret-dotenv"',
            ]
        ),
        encoding="utf-8",
    )
    for name in (
        "FINNHUB_API_KEY",
        "ALPHAVANTAGE_API_KEY",
        "NEWS_API_KEY",
        "NAVER_CLIENT_ID",
        "NAVER_CLIENT_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        "agent_treport.signal_report.external_evidence.providers.requests.get",
        fake_get,
    )

    _, finnhub_outcome = FinnhubProvider().collect(
        (target,), live=True, request_context=_request_context()
    )
    _, alpha_outcome = AlphaVantageProvider().collect(
        (target,), live=True, request_context=_request_context()
    )
    _, news_outcome = NewsApiProvider().collect(
        (target,), live=True, request_context=_request_context()
    )
    _, naver_outcome = NaverNewsProvider().collect(
        (target,), live=True, request_context=_request_context()
    )

    assert finnhub_outcome.status == "success"
    assert alpha_outcome.status == "no_data"
    assert news_outcome.status == "no_data"
    assert naver_outcome.status == "no_data"
    assert calls[0]["params"]["token"] == "finnhub-dotenv"
    assert calls[1]["params"]["apikey"] == "alpha-dotenv"
    assert calls[2]["headers"]["X-Api-Key"] == "news-dotenv"
    assert calls[3]["headers"] == {
        "X-Naver-Client-Id": "naver-id-dotenv",
        "X-Naver-Client-Secret": "naver-secret-dotenv",
    }


def test_dart_fetches_corp_codes_with_env_example_dart_api_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _provider_target("005930")
    calls: list[_HttpCall] = []

    def fake_get(
        url: str,
        *,
        params: Mapping[str, str],
        headers: Mapping[str, str],
        timeout: float,
    ) -> _FakeResponse:
        calls.append(
            {
                "url": url,
                "params": dict(params),
                "headers": dict(headers),
                "timeout": timeout,
            }
        )
        if "corpCode.xml" in url:
            return _FakeResponse(
                content=_dart_corp_code_zip(
                    """
                    <result>
                      <list>
                        <corp_code>00126380</corp_code>
                        <corp_name>Samsung Electronics</corp_name>
                        <stock_code>005930</stock_code>
                        <modify_date>20260516</modify_date>
                      </list>
                    </result>
                    """
                )
            )
        if "list.json" in url:
            return _FakeResponse(
                {
                    "status": "000",
                    "list": [
                        {
                            "rcept_no": "20260516000001",
                            "report_nm": "Quarterly report",
                            "rcept_dt": "20260516",
                        }
                    ],
                }
            )
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(
        "agent_treport.signal_report.external_evidence.providers.requests.get",
        fake_get,
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DART_API_KEY", "dart-example")
    monkeypatch.delenv("OPENDART_API_KEY", raising=False)
    monkeypatch.delenv("AGENT_TREPORT_DART_CORP_CODES_JSON", raising=False)

    candidates, outcome = DartProvider().collect(
        (target,), live=True, request_context=_request_context()
    )

    assert outcome.status == "success"
    assert candidates[0].disclosure is not None
    assert candidates[0].disclosure.filing_id == "20260516000001"
    assert calls[0]["params"] == {"crtfc_key": "dart-example"}
    assert calls[1]["params"]["corp_code"] == "00126380"
    assert calls[1]["params"]["bgn_de"] == "20250516"
    assert calls[1]["params"]["end_de"] == "20260516"

    monkeypatch.delenv("DART_API_KEY", raising=False)
    monkeypatch.setenv("OPENDART_API_KEY", "legacy-dart")
    _, legacy_outcome = DartProvider().collect(
        (target,), live=True, request_context=_request_context()
    )
    assert legacy_outcome.status == "credential_required"


def test_sec_edgar_falls_back_to_official_ticker_text_when_json_mapping_is_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _provider_target("AAPL")
    calls: list[_HttpCall] = []
    sleep_calls: list[float] = []

    def fake_get(
        url: str,
        *,
        params: Mapping[str, str],
        headers: Mapping[str, str],
        timeout: float,
    ) -> _FakeResponse:
        calls.append(
            {
                "url": url,
                "params": dict(params),
                "headers": dict(headers),
                "timeout": timeout,
            }
        )
        if "company_tickers.json" in url:
            return _FakeResponse(status_code=403, text="Request blocked")
        if "ticker.txt" in url:
            return _FakeResponse(content=b"aapl\t320193\n")
        if "CIK0000320193.json" in url:
            return _FakeResponse(
                {
                    "filings": {
                        "recent": {
                            "form": ["10-Q"],
                            "accessionNumber": ["0000320193-26-000001"],
                            "filingDate": ["2026-05-15"],
                            "reportDate": ["2026-03-31"],
                            "primaryDocument": ["aapl-20260331.htm"],
                        }
                    }
                }
            )
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(
        "agent_treport.signal_report.external_evidence.providers.requests.get",
        fake_get,
    )
    monkeypatch.setattr(
        "agent_treport.signal_report.external_evidence.providers.time.sleep",
        sleep_calls.append,
    )
    monkeypatch.setenv("SEC_USER_AGENT", "Agent TReport test@example.com")

    candidates, outcome = SecEdgarProvider().collect(
        (target,), live=True, request_context=_request_context()
    )

    assert outcome.status == "success"
    assert outcome.attempt_count == 1
    assert [call["url"] for call in calls] == [
        "https://www.sec.gov/files/company_tickers.json",
        "https://www.sec.gov/include/ticker.txt",
        "https://data.sec.gov/submissions/CIK0000320193.json",
    ]
    assert candidates[0].disclosure is not None
    assert candidates[0].disclosure.filing_id == "0000320193-26-000001"
    assert candidates[0].safe_url == (
        "https://www.sec.gov/Archives/edgar/data/320193/"
        "000032019326000001/aapl-20260331.htm"
    )
    assert sleep_calls == [pytest.approx(0.11), pytest.approx(0.11)]


def test_sec_edgar_retries_official_rate_threshold_403(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _provider_target("AAPL")
    calls: list[_HttpCall] = []
    sleep_calls: list[float] = []
    mapping_attempts = 0

    def fake_get(
        url: str,
        *,
        params: Mapping[str, str],
        headers: Mapping[str, str],
        timeout: float,
    ) -> _FakeResponse:
        nonlocal mapping_attempts
        calls.append(
            {
                "url": url,
                "params": dict(params),
                "headers": dict(headers),
                "timeout": timeout,
            }
        )
        if "company_tickers.json" in url:
            mapping_attempts += 1
            if mapping_attempts < 3:
                return _FakeResponse(
                    status_code=403,
                    text="<title>SEC.gov | Request Rate Threshold Exceeded</title>",
                )
            return _FakeResponse({"0": {"ticker": "AAPL", "cik_str": 320193}})
        if "CIK0000320193.json" in url:
            return _FakeResponse(
                {
                    "filings": {
                        "recent": {
                            "form": ["8-K"],
                            "accessionNumber": ["0000320193-26-000002"],
                            "filingDate": ["2026-05-16"],
                        }
                    }
                }
            )
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(
        "agent_treport.signal_report.external_evidence.providers.requests.get",
        fake_get,
    )
    monkeypatch.setattr(
        "agent_treport.signal_report.external_evidence.providers.time.sleep",
        sleep_calls.append,
    )
    monkeypatch.setattr(
        "agent_treport.signal_report.external_evidence.providers.random.uniform",
        lambda start, end: 0,
    )
    monkeypatch.setenv("SEC_USER_AGENT", "Agent TReport test@example.com")

    candidates, outcome = SecEdgarProvider().collect(
        (target,), live=True, request_context=_request_context(min_interval_seconds=10)
    )

    assert outcome.status == "success"
    assert outcome.attempt_count == 3
    assert len(candidates) == 1
    assert [call["url"] for call in calls].count(
        "https://www.sec.gov/files/company_tickers.json"
    ) == 3
    assert sleep_calls == [10, 20, 10]


def test_sec_edgar_paces_sequential_official_requests_below_ten_per_second(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    targets = (_provider_target("AAPL"), _provider_target("MSFT"))
    calls: list[_HttpCall] = []
    sleep_calls: list[float] = []

    def fake_get(
        url: str,
        *,
        params: Mapping[str, str],
        headers: Mapping[str, str],
        timeout: float,
    ) -> _FakeResponse:
        calls.append(
            {
                "url": url,
                "params": dict(params),
                "headers": dict(headers),
                "timeout": timeout,
            }
        )
        if "company_tickers.json" in url:
            return _FakeResponse(
                {
                    "0": {"ticker": "AAPL", "cik_str": 320193},
                    "1": {"ticker": "MSFT", "cik_str": 789019},
                }
            )
        if "CIK0000320193.json" in url:
            return _FakeResponse(
                {
                    "filings": {
                        "recent": {
                            "form": ["8-K"],
                            "accessionNumber": ["0000320193-26-000001"],
                            "filingDate": ["2026-05-16"],
                        }
                    }
                }
            )
        if "CIK0000789019.json" in url:
            return _FakeResponse(
                {
                    "filings": {
                        "recent": {
                            "form": ["10-Q"],
                            "accessionNumber": ["0000789019-26-000001"],
                            "filingDate": ["2026-05-16"],
                        }
                    }
                }
            )
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(
        "agent_treport.signal_report.external_evidence.providers.requests.get",
        fake_get,
    )
    monkeypatch.setattr(
        "agent_treport.signal_report.external_evidence.providers.time.sleep",
        sleep_calls.append,
    )
    monkeypatch.setenv("SEC_USER_AGENT", "Agent TReport test@example.com")

    candidates, outcome = SecEdgarProvider().collect(
        targets,
        live=True,
        request_context=_request_context(min_interval_seconds=0),
    )

    assert outcome.status == "success"
    assert len(candidates) == 2
    assert [call["url"] for call in calls] == [
        "https://www.sec.gov/files/company_tickers.json",
        "https://data.sec.gov/submissions/CIK0000320193.json",
        "https://data.sec.gov/submissions/CIK0000789019.json",
    ]
    assert sleep_calls == [pytest.approx(0.11), pytest.approx(0.11)]


def test_payload_projects_category_coverage_from_external_evidence_summary() -> None:
    fixture = load_fixture_signal_report_inputs()

    payload = build_signal_report_payload(
        snapshots=fixture.snapshots,
        focus_etf_id=fixture.focus_etf_id,
        evidence=(),
        operational_provenance={
            "external_evidence_summary": {
                "category_coverage": {
                    "financial": {
                        "coverage_ratio": 1.0,
                        "notes": ["financial:NVDA=covered"],
                    },
                    "disclosure": {
                        "coverage_ratio": 0.0,
                        "notes": ["disclosure:NVDA=no_data"],
                    },
                    "news": {
                        "coverage_ratio": 0.5,
                        "notes": ["news:PLTR=failed"],
                    },
                }
            }
        },
    )

    assert payload.coverage.financial_coverage_ratio == 1.0
    assert payload.coverage.disclosure_coverage_ratio == 0.0
    assert payload.coverage.news_coverage_ratio == 0.5
    assert "external_financial_coverage_ratio=1.00" in payload.data_quality.coverage_notes
    assert "financial:NVDA=covered" in payload.data_quality.coverage_notes
    assert "External disclosure evidence did not cover every selected target." in (
        payload.data_quality.limitations
    )


def _candidate(
    candidate_id: str,
    *,
    ticker: str,
    category: EvidenceCategory,
    title: str,
) -> ExternalEvidenceCandidate:
    return ExternalEvidenceCandidate(
        candidate_id=candidate_id,
        provider_id="fixture_news",
        category=category,
        ticker=ticker,
        source_label="Fixture News",
        title=title,
        published_at="2026-05-15T00:00:00+00:00",
        event_type="news",
        summary="Fixture candidate summary.",
        safe_url=None,
        metadata={"event_kind": "fixture"},
    )


def _provider_target(ticker: str) -> ExternalEvidenceTarget:
    security_id = f"sec_{ticker.lower()}"
    return ExternalEvidenceTarget(
        ticker=ticker,
        name=f"{ticker} target",
        aggregation_key=security_id,
        member_security_ids=(security_id,),
        claim_scope=f"signal:security:{security_id}:weight_increase",
        rank=1,
        summary="test target",
    )


class _FakeResponse:
    def __init__(
        self,
        payload: JsonValue | None = None,
        *,
        status_code: int = 200,
        text: str = "",
        content: bytes | None = None,
    ) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = text
        self.content = content if content is not None else b""

    def json(self) -> JsonValue:
        if self._payload is None:
            raise ValueError("no json payload")
        return self._payload


def _dart_corp_code_zip(xml_text: str) -> bytes:
    from io import BytesIO
    from zipfile import ZipFile

    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("CORPCODE.xml", xml_text)
    return buffer.getvalue()


def _outcome_provider(outcome: ExternalEvidenceProviderOutcome) -> ExternalEvidenceProvider:
    class Provider:
        provider_id: str = outcome.provider_id
        category: EvidenceCategory = outcome.category

        def collect(self, targets, *, live: bool, request_context):
            _ = targets, live, request_context
            return (), outcome

    return Provider()


def _counting_provider(
    *,
    provider_id: str,
    category: EvidenceCategory,
    calls: list[str],
) -> ExternalEvidenceProvider:
    local_provider_id = provider_id
    local_category: EvidenceCategory = category

    class Provider:
        provider_id: str = local_provider_id
        category: EvidenceCategory = local_category

        def collect(self, targets, *, live: bool, request_context):
            _ = live, request_context
            calls.extend(target.ticker for target in targets)
            return (), ExternalEvidenceProviderOutcome(
                provider_id=local_provider_id,
                category=local_category,
                status="success",
                error_code=None,
                retryable=False,
                attempt_count=1,
                stopped_reason=None,
                target_tickers=tuple(target.ticker for target in targets),
                safe_message="ok",
            )

    return Provider()


def _request_context(*, min_interval_seconds: float = 0):
    from agent_treport.signal_report.external_evidence.contracts import (
        ExternalEvidenceRequestContext,
    )

    return ExternalEvidenceRequestContext(
        now=lambda: datetime(2026, 5, 16, 0, 0, tzinfo=UTC),
        timeout_seconds=1,
        min_interval_seconds=min_interval_seconds,
    )
