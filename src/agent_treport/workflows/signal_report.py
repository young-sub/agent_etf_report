from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Literal, Protocol

from agent_pack.artifacts import WritableArtifactManager
from agent_pack.context import ContextManager
from agent_pack.failure_evidence import exception_error
from agent_pack.models import (
    ArtifactRef,
    ArtifactRefBlock,
    JsonBlock,
    JsonValue,
    RunEvent,
    RunInput,
    RunResult,
    StepResult,
    TextBlock,
)
from agent_pack.models_client import ModelClient
from agent_pack.store import RunStore
from agent_pack.tools import ToolRegistry
from agent_pack.workflow import FunctionStep, ModelStep, RunContext, Workflow

from agent_treport.signal_report import (
    HTMLResearchReportRenderer,
    MarkdownSignalReportRenderer,
    ReportQualityGate,
    ReportQualityResult,
    TelegramSignalAlertRenderer,
    build_signal_report_payload,
    load_fixture_signal_report_inputs,
)
from agent_treport.signal_report.domain.commentary_policy import (
    commentary_policy_summary,
    evaluate_model_commentary,
)
from agent_treport.signal_report.domain.inputs import SignalReportInputs
from agent_treport.signal_report.domain.payload import SignalReportPayload
from agent_treport.signal_report.evaluator_review import (
    build_signal_report_evaluator_review_summary,
)

HOLDINGS_ARTIFACT_ID = "artifact_treport_holdings"
SIGNAL_REPORT_INPUTS_ARTIFACT_ID = "artifact_treport_signal_report_inputs"
OPERATIONAL_HOLDINGS_PROVENANCE_ARTIFACT_ID = (
    "artifact_treport_operational_holdings_provenance"
)
OPERATIONAL_READINESS_ARTIFACT_ID = "artifact_treport_operational_readiness"
CHANGES_ARTIFACT_ID = "artifact_treport_changes"
SUMMARY_ARTIFACT_ID = "artifact_treport_summary"
SIGNAL_PAYLOAD_ARTIFACT_ID = "artifact_treport_signal_payload"
REPORT_ARTIFACT_ID = "artifact_treport_report"
HTML_REPORT_ARTIFACT_ID = "artifact_treport_html_report"
TELEGRAM_ALERT_ARTIFACT_ID = "artifact_treport_telegram_alert"
QUALITY_ARTIFACT_ID = "artifact_treport_quality"
HARNESS_EVALUATOR_ARTIFACT_ID = "artifact_treport_harness_evaluator_review"
MODEL_CONTEXT_SIGNAL_BOARD_LIMIT = 25
MODEL_CONTEXT_FOLLOW_SHEET_LIMIT = 20
MODEL_CONTEXT_DOSSIER_LIMIT = 25
MODEL_CONTEXT_EVIDENCE_LIMIT = 40
MODEL_CONTEXT_DATA_QUALITY_LIMIT = 30

type DomainFailureReason = Literal[
    "data_preparation_failed",
    "model_analysis_failed",
    "report_render_failed",
    "report_quality_failed",
    "artifact_persistence_failed",
    "run_store_failed",
]

DOMAIN_FAILURE_REASONS = {
    "data_preparation_failed",
    "model_analysis_failed",
    "report_render_failed",
    "report_quality_failed",
    "artifact_persistence_failed",
    "run_store_failed",
}


class SignalReportInputProvider(Protocol):
    def load(self) -> SignalReportInputs: ...


class SignalReportMarkdownRenderer(Protocol):
    def render(self, *, payload: SignalReportPayload, model_commentary: str | None = None) -> str:
        ...


class SignalReportHTMLRenderer(Protocol):
    def render(self, *, payload: SignalReportPayload, model_commentary: str | None = None) -> str:
        ...


class SignalReportTelegramAlertRenderer(Protocol):
    def render(self, *, payload: SignalReportPayload, full_report_reference: str) -> str:
        ...


class SignalReportQualityGate(Protocol):
    def evaluate(
        self,
        *,
        payload: SignalReportPayload,
        markdown: str,
        html: str | None = None,
        telegram_alert: str | None = None,
    ) -> ReportQualityResult:
        ...


class CachedSignalReportInputProvider:
    def __init__(
        self,
        *,
        inputs: SignalReportInputs,
        provenance: dict[str, JsonValue] | None = None,
    ) -> None:
        self._inputs = inputs
        self.provenance = provenance

    def load(self) -> SignalReportInputs:
        return self._inputs


class FixtureSignalReportInputProvider:
    def __init__(
        self,
        *,
        holdings_path: str | None = None,
        evidence_path: str | None = None,
        focus_etf_id: str | None = None,
    ) -> None:
        self._holdings_path = holdings_path
        self._evidence_path = evidence_path
        self._focus_etf_id = focus_etf_id

    def load(self) -> SignalReportInputs:
        return load_fixture_signal_report_inputs(
            holdings_path=self._holdings_path,
            evidence_path=self._evidence_path,
            focus_etf_id=self._focus_etf_id,
        )


async def run_signal_report(
    *,
    run_id: str,
    store: RunStore,
    context_manager: ContextManager,
    artifact_manager: WritableArtifactManager,
    model_client: ModelClient,
    provider: SignalReportInputProvider | None = None,
    markdown_renderer: SignalReportMarkdownRenderer | None = None,
    html_renderer: SignalReportHTMLRenderer | None = None,
    telegram_alert_renderer: SignalReportTelegramAlertRenderer | None = None,
    quality_gate: SignalReportQualityGate | None = None,
) -> RunResult:
    workflow = build_signal_report_workflow(
        artifact_manager=artifact_manager,
        model_client=model_client,
        provider=provider or FixtureSignalReportInputProvider(),
        markdown_renderer=markdown_renderer,
        html_renderer=html_renderer,
        telegram_alert_renderer=telegram_alert_renderer,
        quality_gate=quality_gate,
    )
    result = await workflow.run(
        run_id=run_id,
        run_input=RunInput(text="Generate the Agent TReport signal intelligence report."),
        store=store,
        context_manager=context_manager,
        artifact_manager=artifact_manager,
    )
    if result.status != "failed":
        return result

    classified = _classify_runtime_failure(result.output)
    await _record_failure_classified(
        store=store,
        run_id=run_id,
        reason=classified,
        failed_step=_failed_step(result.output),
    )
    return RunResult(
        run_id=result.run_id,
        status="failed",
        output={
            "reason": classified,
            "state": _failure_state(result.output),
            **_quality_failure_output(result.output),
            "runtime_failure": _runtime_failure(result.output),
        },
        artifacts=result.artifacts,
        diagnostics=result.diagnostics,
    )


def build_signal_report_workflow(
    *,
    artifact_manager: WritableArtifactManager,
    model_client: ModelClient,
    provider: SignalReportInputProvider,
    markdown_renderer: SignalReportMarkdownRenderer | None = None,
    html_renderer: SignalReportHTMLRenderer | None = None,
    telegram_alert_renderer: SignalReportTelegramAlertRenderer | None = None,
    quality_gate: SignalReportQualityGate | None = None,
) -> Workflow:
    markdown_renderer = markdown_renderer or MarkdownSignalReportRenderer()
    html_renderer = html_renderer or HTMLResearchReportRenderer()
    telegram_alert_renderer = telegram_alert_renderer or TelegramSignalAlertRenderer()
    quality_gate = quality_gate or ReportQualityGate()

    async def collect_data(context: RunContext) -> StepResult:
        try:
            inputs = provider.load()
            raw_provenance = getattr(provider, "provenance", None)
            provenance = dict(raw_provenance) if isinstance(raw_provenance, Mapping) else None
            payload = build_signal_report_payload(
                snapshots=inputs.snapshots,
                focus_etf_id=inputs.focus_etf_id,
                focus_etf_ids=inputs.focus_etf_ids,
                evidence=inputs.evidence,
                operational_provenance=provenance,
            )
            inputs_data = inputs.model_dump(mode="json")
            payload_data = payload.model_dump(mode="json")
        except Exception as exc:
            return _failed_domain_step(
                reason="data_preparation_failed",
                exc=exc,
                fallback_message="data preparation failed",
            )

        try:
            holdings_ref = await _store_json_artifact(
                artifact_manager=artifact_manager,
                artifact_id=HOLDINGS_ARTIFACT_ID,
                name="holdings.json",
                payload=inputs.snapshots.model_dump(mode="json"),
                metadata={"capability": "data_collection"},
            )
            await _append_artifact_reference(context=context, artifact=holdings_ref)
            inputs_ref = await _store_json_artifact(
                artifact_manager=artifact_manager,
                artifact_id=SIGNAL_REPORT_INPUTS_ARTIFACT_ID,
                name="signal_report_inputs.json",
                payload=inputs_data,
                metadata={"capability": "signal_report_inputs"},
            )
            await _append_artifact_reference(context=context, artifact=inputs_ref)
            provenance_ref = None
            if provenance is not None:
                provenance_ref = await _store_json_artifact(
                    artifact_manager=artifact_manager,
                    artifact_id=OPERATIONAL_HOLDINGS_PROVENANCE_ARTIFACT_ID,
                    name="operational_holdings_provenance.json",
                    payload=provenance,
                    metadata={"capability": "operational_holdings_provenance"},
                )
                await _append_artifact_reference(context=context, artifact=provenance_ref)
            readiness = _operational_readiness_projection(provenance)
            readiness_ref = None
            if readiness is not None:
                readiness_ref = await _store_json_artifact(
                    artifact_manager=artifact_manager,
                    artifact_id=OPERATIONAL_READINESS_ARTIFACT_ID,
                    name="operational_readiness.json",
                    payload=readiness,
                    metadata={"capability": "operational_readiness"},
                )
                await _append_artifact_reference(context=context, artifact=readiness_ref)
            changes_ref = await _store_json_artifact(
                artifact_manager=artifact_manager,
                artifact_id=CHANGES_ARTIFACT_ID,
                name="changes.json",
                payload={
                    "signal_board": payload_data["signal_board"],
                    "market_map": payload_data["market_map"],
                    "etf_follow_sheets": payload_data["etf_follow_sheets"],
                },
                metadata={"capability": "signal_analysis"},
            )
            await _append_artifact_reference(context=context, artifact=changes_ref)
            summary_ref = await _store_json_artifact(
                artifact_manager=artifact_manager,
                artifact_id=SUMMARY_ARTIFACT_ID,
                name="summary.json",
                payload=payload_data["executive_summary"],
                metadata={"capability": "signal_analysis"},
            )
            await _append_artifact_reference(context=context, artifact=summary_ref)
            payload_ref = await _store_json_artifact(
                artifact_manager=artifact_manager,
                artifact_id=SIGNAL_PAYLOAD_ARTIFACT_ID,
                name="signal_payload.json",
                payload=payload_data,
                metadata={"capability": "canonical_signal_report_payload"},
            )
            await _append_artifact_reference(context=context, artifact=payload_ref)
        except Exception as exc:
            return _failed_domain_step(
                reason="artifact_persistence_failed",
                exc=exc,
                fallback_message="artifact persistence failed",
            )

        await context.context_manager.append(
            run_id=context.run_id,
            kind="state_note",
            source="workflow",
            visibility="model",
            content=(
                TextBlock(
                    text=(
                        "The canonical SignalReportPayload is fixed. Explain the payload, "
                        "but do not change scores, review labels, evidence grades, or "
                        "data-quality findings."
                    )
                ),
                JsonBlock(
                    value={
                        "signal_payload_model_context": _signal_payload_model_context(
                            payload_data
                        )
                    }
                ),
            ),
            metadata={"capability": "signal_analysis"},
        )
        return StepResult(
            status="succeeded",
            state_update={
                "holdings_artifact_id": holdings_ref.artifact_id,
                "signal_report_inputs_artifact_id": inputs_ref.artifact_id,
                **(
                    {
                        "operational_holdings_provenance_artifact_id": (
                            provenance_ref.artifact_id
                        )
                    }
                    if provenance_ref is not None
                    else {}
                ),
                **(
                    {
                        "operational_readiness_artifact_id": readiness_ref.artifact_id,
                        "operational_readiness": readiness,
                    }
                    if readiness_ref is not None and readiness is not None
                    else {}
                ),
                "changes_artifact_id": changes_ref.artifact_id,
                "summary_artifact_id": summary_ref.artifact_id,
                "signal_payload_artifact_id": payload_ref.artifact_id,
            },
        )

    model_step = ModelStep(
        name="analyze-data",
        model_client=model_client,
        tool_registry=ToolRegistry(),
        max_tool_rounds=0,
    )

    async def analyze_data(context: RunContext) -> StepResult:
        result = await model_step.run(context)
        if result.status != "succeeded":
            return result
        return StepResult(
            status="succeeded",
            state_update={**context.state, **(result.state_update or {})},
        )

    async def render_report(context: RunContext) -> StepResult:
        try:
            payload = SignalReportPayload.model_validate_json(
                (await artifact_manager.read_bytes(SIGNAL_PAYLOAD_ARTIFACT_ID)).decode("utf-8")
            )
        except Exception as exc:
            return _failed_domain_step(
                reason="artifact_persistence_failed",
                exc=exc,
                fallback_message="artifact persistence failed",
            )
        model_commentary = await _latest_assistant_text(context)
        commentary_policy = commentary_policy_summary(
            evaluate_model_commentary(model_commentary)
        )
        try:
            markdown_report = markdown_renderer.render(
                payload=payload,
                model_commentary=model_commentary,
            )
            html_report = html_renderer.render(
                payload=payload,
                model_commentary=model_commentary,
            )
            telegram_alert = telegram_alert_renderer.render(
                payload=payload,
                full_report_reference=HTML_REPORT_ARTIFACT_ID,
            )
        except Exception as exc:
            return _failed_domain_step(
                reason="report_render_failed",
                exc=exc,
                fallback_message="report render failed",
            )

        try:
            quality = quality_gate.evaluate(
                payload=payload,
                markdown=markdown_report,
                html=html_report,
                telegram_alert=telegram_alert,
            )
        except Exception:
            return StepResult(
                status="failed",
                output={
                    "reason": "report_quality_failed",
                    "error": {
                        "code": "report_quality_evaluation_failed",
                        "type": "ReportQualityEvaluationFailed",
                        "message": "report quality evaluation failed",
                    },
                },
            )
        try:
            quality_ref = await _store_json_artifact(
                artifact_manager=artifact_manager,
                artifact_id=QUALITY_ARTIFACT_ID,
                name="quality.json",
                payload=quality.model_dump(mode="json"),
                metadata={
                    "capability": "report_quality_gate",
                    "report_quality_status": quality.status,
                },
            )
        except Exception as exc:
            return _failed_domain_step(
                reason="artifact_persistence_failed",
                exc=exc,
                fallback_message="artifact persistence failed",
            )
        await _append_artifact_reference(context=context, artifact=quality_ref)
        quality_state = {
            **context.state,
            "report_quality_artifact_id": quality_ref.artifact_id,
            "report_quality_status": quality.status,
            "report_quality_summary": quality.summary,
            "report_quality_evaluated_artifact_ids": (SIGNAL_PAYLOAD_ARTIFACT_ID,),
        }
        if quality.status == "failed":
            return StepResult(
                status="failed",
                output={
                    "reason": "report_quality_failed",
                    "state": quality_state,
                    "report_quality": quality.model_dump(mode="json"),
                },
            )

        try:
            report_ref = await artifact_manager.store_bytes(
                artifact_id=REPORT_ARTIFACT_ID,
                name="report.md",
                data=markdown_report.encode("utf-8"),
                media_type="text/markdown",
                metadata={
                    "capability": "document_generation",
                    "model_commentary_policy": commentary_policy,
                    "report_quality_status": quality.status,
                    "report_quality_summary": quality.summary,
                },
            )
        except Exception as exc:
            return _failed_domain_step(
                reason="artifact_persistence_failed",
                exc=exc,
                fallback_message="artifact persistence failed",
            )
        await _append_artifact_reference(context=context, artifact=report_ref)
        try:
            html_ref = await artifact_manager.store_bytes(
                artifact_id=HTML_REPORT_ARTIFACT_ID,
                name="report.html",
                data=html_report.encode("utf-8"),
                media_type="text/html",
                metadata={
                    "capability": "html_research_report",
                    "model_commentary_policy": commentary_policy,
                    "report_quality_status": quality.status,
                    "report_quality_summary": quality.summary,
                },
            )
        except Exception as exc:
            return _failed_domain_step(
                reason="artifact_persistence_failed",
                exc=exc,
                fallback_message="artifact persistence failed",
                state=quality_state,
            )
        await _append_artifact_reference(context=context, artifact=html_ref)
        try:
            telegram_alert_ref = await artifact_manager.store_bytes(
                artifact_id=TELEGRAM_ALERT_ARTIFACT_ID,
                name="telegram_alert.txt",
                data=telegram_alert.encode("utf-8"),
                media_type="text/plain",
                metadata={
                    "capability": "telegram_signal_alert",
                    "telegram_parse_mode": "HTML",
                    "full_report_artifact_id": HTML_REPORT_ARTIFACT_ID,
                    "report_quality_status": quality.status,
                    "report_quality_summary": quality.summary,
                },
            )
        except Exception as exc:
            return _failed_domain_step(
                reason="artifact_persistence_failed",
                exc=exc,
                fallback_message="artifact persistence failed",
                state=quality_state,
            )
        await _append_artifact_reference(context=context, artifact=telegram_alert_ref)
        return StepResult(
            status="succeeded",
            state_update={
                **quality_state,
                "report_artifact_id": report_ref.artifact_id,
                "html_report_artifact_id": html_ref.artifact_id,
                "telegram_alert_artifact_id": telegram_alert_ref.artifact_id,
                "model_commentary_policy": commentary_policy,
            },
        )

    async def evaluate_harness(context: RunContext) -> StepResult:
        review_summary = build_signal_report_evaluator_review_summary(
            run_id=context.run_id,
            state=context.state,
        )
        verdict = _review_verdict(review_summary)
        try:
            evaluator_ref = await _store_json_artifact(
                artifact_manager=artifact_manager,
                artifact_id=HARNESS_EVALUATOR_ARTIFACT_ID,
                name="harness_evaluator_review.json",
                payload={
                    "schema_version": "agent_treport.signal_report_evaluator_review_artifact.v1",
                    "review_summary": review_summary,
                },
                metadata={
                    "capability": "harness_evaluator_review",
                    "harness_evaluator_verdict": verdict,
                },
            )
        except Exception as exc:
            return _failed_domain_step(
                reason="artifact_persistence_failed",
                exc=exc,
                fallback_message="artifact persistence failed",
                state=dict(context.state),
            )
        await _append_artifact_reference(context=context, artifact=evaluator_ref)
        review_summaries = (
            *_existing_review_summaries(context.state.get("agent_pack_review_summaries")),
            review_summary,
        )
        return StepResult(
            status="succeeded",
            state_update={
                **context.state,
                "harness_evaluator_artifact_id": evaluator_ref.artifact_id,
                "harness_evaluator_verdict": verdict,
                "agent_pack_review_summaries": review_summaries,
            },
        )

    return Workflow(
        name="agent-treport-signal-report",
        steps=(
            FunctionStep(name="collect-data", function=collect_data),
            FunctionStep(name="analyze-data", function=analyze_data),
            FunctionStep(name="render-report", function=render_report),
            FunctionStep(name="evaluate-harness", function=evaluate_harness),
        ),
    )


def _operational_readiness_projection(
    provenance: Mapping[str, JsonValue] | None,
) -> dict[str, JsonValue] | None:
    if provenance is None:
        return None
    readiness = provenance.get("operational_readiness")
    return dict(readiness) if isinstance(readiness, Mapping) else None


def _signal_payload_model_context(
    payload_data: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    signal_board = _json_object_list(payload_data.get("signal_board"))
    etf_follow_sheets = _json_object_list(payload_data.get("etf_follow_sheets"))
    ticker_dossiers = _json_object_list(payload_data.get("ticker_dossiers"))
    evidence_ledger = _json_object_list(payload_data.get("evidence_ledger"))
    data_quality = _json_mapping(payload_data.get("data_quality"))
    return {
        "schema_version": "agent_treport.signal_payload_model_context.v1",
        "canonical_payload_artifact_id": SIGNAL_PAYLOAD_ARTIFACT_ID,
        "meta": _json_mapping(payload_data.get("meta")),
        "coverage": _json_mapping(payload_data.get("coverage")),
        "executive_summary": _json_mapping(payload_data.get("executive_summary")),
        "signal_board": [
            _signal_board_model_row(row)
            for row in signal_board[:MODEL_CONTEXT_SIGNAL_BOARD_LIMIT]
        ],
        "signal_board_truncated_count": max(
            0, len(signal_board) - MODEL_CONTEXT_SIGNAL_BOARD_LIMIT
        ),
        "market_map": payload_data.get("market_map"),
        "etf_follow_sheets": [
            _follow_sheet_model_row(row)
            for row in etf_follow_sheets[:MODEL_CONTEXT_FOLLOW_SHEET_LIMIT]
        ],
        "etf_follow_sheets_truncated_count": max(
            0, len(etf_follow_sheets) - MODEL_CONTEXT_FOLLOW_SHEET_LIMIT
        ),
        "ticker_dossiers": [
            _ticker_dossier_model_row(row)
            for row in ticker_dossiers[:MODEL_CONTEXT_DOSSIER_LIMIT]
        ],
        "ticker_dossiers_truncated_count": max(
            0, len(ticker_dossiers) - MODEL_CONTEXT_DOSSIER_LIMIT
        ),
        "evidence_ledger": [
            _evidence_model_row(row)
            for row in evidence_ledger[:MODEL_CONTEXT_EVIDENCE_LIMIT]
        ],
        "evidence_ledger_truncated_count": max(
            0, len(evidence_ledger) - MODEL_CONTEXT_EVIDENCE_LIMIT
        ),
        "data_quality": {
            "overall": data_quality.get("overall"),
            "issues": _json_list(data_quality.get("issues"))[
                :MODEL_CONTEXT_DATA_QUALITY_LIMIT
            ],
            "limitations": _json_list(data_quality.get("limitations"))[
                :MODEL_CONTEXT_DATA_QUALITY_LIMIT
            ],
            "coverage_notes": _json_list(data_quality.get("coverage_notes"))[
                :MODEL_CONTEXT_DATA_QUALITY_LIMIT
            ],
        },
        "methodology": _json_mapping(payload_data.get("methodology")),
    }


def _signal_board_model_row(row: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    return _select_json_fields(
        row,
        (
            "rank",
            "ticker",
            "name",
            "market",
            "sector",
            "theme",
            "signal_direction",
            "signal_type",
            "participating_etfs",
            "net_flow_estimate_krw",
            "weight_delta_pp",
            "new_or_exit",
            "signal_score",
            "confidence",
            "evidence_grade",
            "review_label",
            "primary_reason",
            "data_quality_warnings",
            "claim_scope",
        ),
    )


def _follow_sheet_model_row(row: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    return _select_json_fields(
        row,
        (
            "etf_id",
            "etf_name",
            "brand_id",
            "source_provider_id",
            "is_focus",
            "top_holdings",
            "new_positions",
            "exited_positions",
            "increased_positions",
            "decreased_positions",
            "cash_change_pp",
            "brand_behavior_read",
            "data_quality",
        ),
    )


def _ticker_dossier_model_row(row: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    return _select_json_fields(
        row,
        (
            "ticker",
            "name",
            "summary",
            "holding_facts",
            "why_now_hypothesis",
            "supporting_evidence",
            "counter_evidence",
            "invalidation_conditions",
            "final_label",
        ),
    )


def _evidence_model_row(row: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    return _select_json_fields(
        row,
        (
            "evidence_id",
            "ticker",
            "scope",
            "type",
            "source",
            "title",
            "published_at",
            "stance",
            "strength",
            "claim_scope",
            "evidence_role",
            "relevance",
            "novelty",
            "interpretation_basis",
            "observed_direction",
            "used_in",
        ),
    )


def _select_json_fields(
    row: Mapping[str, JsonValue],
    fields: tuple[str, ...],
) -> dict[str, JsonValue]:
    return {field: row[field] for field in fields if field in row}


def _json_mapping(value: JsonValue) -> Mapping[str, JsonValue]:
    return value if isinstance(value, Mapping) else {}


def _json_object_list(value: JsonValue) -> list[Mapping[str, JsonValue]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _json_list(value: JsonValue) -> list[JsonValue]:
    return value if isinstance(value, list) else []


def _existing_review_summaries(value: object) -> tuple[JsonValue, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _review_verdict(summary: Mapping[str, JsonValue]) -> str:
    details = summary.get("details")
    if not isinstance(details, Mapping):
        return "uncertain"
    verdict = details.get("verdict")
    return verdict if isinstance(verdict, str) and verdict else "uncertain"


async def _store_json_artifact(
    *,
    artifact_manager: WritableArtifactManager,
    artifact_id: str,
    name: str,
    payload: object,
    metadata: dict[str, object],
) -> ArtifactRef:
    return await artifact_manager.store_bytes(
        artifact_id=artifact_id,
        name=name,
        data=json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"),
        media_type="application/json",
        metadata=metadata,
    )


async def _append_artifact_reference(*, context: RunContext, artifact: ArtifactRef) -> None:
    await context.context_manager.append(
        run_id=context.run_id,
        kind="artifact_reference",
        source="workflow",
        visibility="internal",
        content=(ArtifactRefBlock(artifact=artifact),),
        metadata={"artifact_id": artifact.artifact_id},
    )


async def _latest_assistant_text(context: RunContext) -> str:
    items = await context.store.list_context_items(context.run_id)
    text_parts = [
        block.text
        for item in items
        if item.kind == "assistant_message"
        for block in item.content
        if isinstance(block, TextBlock)
    ]
    return text_parts[-1] if text_parts else ""


def _failed_domain_step(
    *,
    reason: DomainFailureReason,
    exc: Exception,
    fallback_message: str,
    state: dict[str, JsonValue] | None = None,
) -> StepResult:
    output: dict[str, JsonValue] = {
        "reason": reason,
        "error": exception_error(
            exc,
            code=reason,
            fallback_message=fallback_message,
        ),
    }
    if state is not None:
        output["state"] = state
    return StepResult(
        status="failed",
        output=output,
    )


def _classify_runtime_failure(output: object) -> DomainFailureReason:
    if not isinstance(output, dict):
        return "model_analysis_failed"
    step_output = output.get("step_output")
    if isinstance(step_output, dict):
        reason = step_output.get("reason")
        if isinstance(reason, str) and reason in DOMAIN_FAILURE_REASONS:
            return reason  # type: ignore[return-value]
    failed_step = output.get("failed_step")
    if failed_step == "collect-data":
        return "data_preparation_failed"
    if failed_step == "render-report":
        return "report_render_failed"
    return "model_analysis_failed"


def _runtime_failure(output: object) -> dict[str, JsonValue]:
    if not isinstance(output, dict):
        return {}
    failure: dict[str, JsonValue] = {}
    for key in ("failed_step", "step_output", "error"):
        value = output.get(key)
        if value is not None:
            failure[key] = value
    return failure


def _failure_state(output: object) -> dict[str, JsonValue]:
    if not isinstance(output, dict):
        return {}
    step_output = output.get("step_output")
    if isinstance(step_output, dict):
        step_state = step_output.get("state")
        if isinstance(step_state, dict):
            return step_state
    state = output.get("state")
    return state if isinstance(state, dict) else {}


def _quality_failure_output(output: object) -> dict[str, JsonValue]:
    if not isinstance(output, dict):
        return {}
    step_output = output.get("step_output")
    if not isinstance(step_output, dict) or step_output.get("reason") != "report_quality_failed":
        return {}
    quality_output: dict[str, JsonValue] = {}
    report_quality = step_output.get("report_quality")
    if report_quality is not None:
        quality_output["report_quality"] = report_quality
    error = step_output.get("error")
    if error is not None:
        quality_output["error"] = error
    return quality_output


def _failed_step(output: object) -> str | None:
    if not isinstance(output, dict):
        return None
    failed_step = output.get("failed_step")
    return failed_step if isinstance(failed_step, str) else None


async def _record_failure_classified(
    *,
    store: RunStore,
    run_id: str,
    reason: DomainFailureReason,
    failed_step: str | None,
) -> None:
    try:
        await store.append_event(
            RunEvent(
                run_id=run_id,
                type="agent_treport.failure_classified",
                payload={"reason": reason, "failed_step": failed_step or ""},
            )
        )
    except Exception:
        return
