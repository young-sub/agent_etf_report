from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from agent_pack.models import ArtifactRef, JsonValue
from agent_pack.tools import ToolRegistry
from agent_pack_docs import (
    DOCUMENT_EVIDENCE_TOOL_NAME,
    DOCUMENT_INDEX_TOOL_NAME,
    DOCUMENT_PARSE_TOOL_NAME,
    DocumentEvidenceTool,
    DocumentIndexTool,
    DocumentParseTool,
    IndexArtifactStore,
    JsonArtifactWriter,
)

from agent_treport.signal_report.domain.evidence import (
    EvidenceEventType,
    EvidenceItemInput,
    EvidenceNovelty,
    EvidenceRelevance,
    EvidenceRole,
    EvidenceStance,
    EvidenceStrength,
)

ArtifactPathResolver = Callable[[ArtifactRef], Path]

PRIVATE_DOCUMENT_TOOL_NAMES = (
    DOCUMENT_PARSE_TOOL_NAME,
    DOCUMENT_INDEX_TOOL_NAME,
    DOCUMENT_EVIDENCE_TOOL_NAME,
)


@dataclass(frozen=True)
class DocumentEvidenceSource:
    artifact: ArtifactRef
    question: str
    source_label: str
    title: str
    ticker: str | None = None
    scope: str | None = None
    claim_scope: str | None = None
    type: EvidenceEventType = "analyst_report"
    published_at: str | None = None
    stance: EvidenceStance = "neutral"
    evidence_role: EvidenceRole = "context"
    relevance: EvidenceRelevance | None = "medium"
    novelty: EvidenceNovelty | None = "unknown"


@dataclass(frozen=True)
class DocumentEvidenceCompositionResult:
    evidence: tuple[EvidenceItemInput, ...]
    diagnostics: Mapping[str, JsonValue]


class DocumentEvidenceCompositionError(RuntimeError):
    pass


class DocumentEvidenceComposer:
    def __init__(
        self,
        *,
        artifact_resolver: ArtifactPathResolver,
        artifact_writer: JsonArtifactWriter,
        index_store: IndexArtifactStore,
        index_resolver: ArtifactPathResolver,
        registry_factory: Callable[[], ToolRegistry] | None = None,
    ) -> None:
        self._artifact_resolver = artifact_resolver
        self._artifact_writer = artifact_writer
        self._index_store = index_store
        self._index_resolver = index_resolver
        self._registry_factory = registry_factory or ToolRegistry

    async def compose(
        self,
        *,
        run_id: str,
        sources: Sequence[DocumentEvidenceSource],
    ) -> DocumentEvidenceCompositionResult:
        registry = self._build_private_registry()
        evidence: list[EvidenceItemInput] = []
        tool_call_counts = dict.fromkeys(PRIVATE_DOCUMENT_TOOL_NAMES, 0)
        source_formats: list[str] = []

        for source in sources:
            parse_payload = await self._execute_required(
                registry=registry,
                run_id=run_id,
                tool_name=DOCUMENT_PARSE_TOOL_NAME,
                arguments={
                    "source": source.artifact.model_dump(mode="json"),
                    "parse_mode": "best_effort",
                    "metadata": {"origin": "agent_treport_document_evidence"},
                },
            )
            tool_call_counts[DOCUMENT_PARSE_TOOL_NAME] += 1
            document_artifact = _first_artifact_ref(
                parse_payload,
                tool_name=DOCUMENT_PARSE_TOOL_NAME,
            )

            index_payload = await self._execute_required(
                registry=registry,
                run_id=run_id,
                tool_name=DOCUMENT_INDEX_TOOL_NAME,
                arguments={
                    "document": document_artifact.model_dump(mode="json"),
                    "metadata": {"origin": "agent_treport_document_evidence"},
                },
            )
            tool_call_counts[DOCUMENT_INDEX_TOOL_NAME] += 1
            source_format = _source_format(index_payload)
            if source_format is not None:
                source_formats.append(source_format)
            index_artifact = _first_artifact_ref(
                index_payload,
                tool_name=DOCUMENT_INDEX_TOOL_NAME,
            )

            evidence_payload = await self._execute_required(
                registry=registry,
                run_id=run_id,
                tool_name=DOCUMENT_EVIDENCE_TOOL_NAME,
                arguments={
                    "index": index_artifact.model_dump(mode="json"),
                    "question": source.question,
                    "document_ids": _document_ids(index_payload),
                    "metadata": {"origin": "agent_treport_document_evidence"},
                },
            )
            tool_call_counts[DOCUMENT_EVIDENCE_TOOL_NAME] += 1
            evidence.extend(_map_evidence_payload(source=source, payload=evidence_payload))

        diagnostics: dict[str, JsonValue] = {
            "schema_version": "agent_treport.document_evidence_composition.v1",
            "registered_tool_names": PRIVATE_DOCUMENT_TOOL_NAMES,
            "source_count": len(sources),
            "source_formats": tuple(source_formats),
            "evidence_count": len(evidence),
            "tool_call_counts": tool_call_counts,
        }
        return DocumentEvidenceCompositionResult(
            evidence=tuple(evidence),
            diagnostics=diagnostics,
        )

    def _build_private_registry(self) -> ToolRegistry:
        registry = self._registry_factory()
        registry.register(
            DocumentParseTool.with_doc_parser(
                artifact_resolver=self._artifact_resolver,
                artifact_writer=self._artifact_writer,
            )
        )
        registry.register(
            DocumentIndexTool.with_doc_parser(
                artifact_resolver=self._artifact_resolver,
                index_store=self._index_store,
            )
        )
        registry.register(
            DocumentEvidenceTool.with_doc_parser(index_resolver=self._index_resolver)
        )
        return registry

    async def _execute_required(
        self,
        *,
        registry: ToolRegistry,
        run_id: str,
        tool_name: str,
        arguments: Mapping[str, JsonValue],
    ) -> Mapping[str, JsonValue]:
        execution = await registry.execute(
            tool_name=tool_name,
            arguments=arguments,
            origin="workflow",
            run_id=run_id,
            recoverable=True,
        )
        result = execution.result
        if result.status != "succeeded":
            error_code = result.error.code if result.error is not None else "unknown"
            raise DocumentEvidenceCompositionError(
                f"{tool_name} failed during document evidence composition: {error_code}"
            )
        return result.result


def _first_artifact_ref(payload: Mapping[str, JsonValue], *, tool_name: str) -> ArtifactRef:
    artifact_refs = payload.get("artifact_refs")
    if not isinstance(artifact_refs, Sequence) or isinstance(
        artifact_refs, str | bytes | bytearray
    ):
        raise DocumentEvidenceCompositionError(f"{tool_name} returned no artifact refs")
    for item in artifact_refs:
        if isinstance(item, Mapping):
            return ArtifactRef.model_validate(item)
    raise DocumentEvidenceCompositionError(f"{tool_name} returned no artifact refs")


def _document_ids(payload: Mapping[str, JsonValue]) -> tuple[str, ...]:
    document = payload.get("document")
    if not isinstance(document, Mapping):
        return ()
    document_id = document.get("document_id")
    if not isinstance(document_id, str) or not document_id:
        return ()
    return (document_id,)


def _source_format(payload: Mapping[str, JsonValue]) -> str | None:
    document = payload.get("document")
    if not isinstance(document, Mapping):
        return None
    source_format = document.get("source_format")
    return source_format if isinstance(source_format, str) and source_format else None


def _map_evidence_payload(
    *,
    source: DocumentEvidenceSource,
    payload: Mapping[str, JsonValue],
) -> tuple[EvidenceItemInput, ...]:
    raw_items = payload.get("evidence")
    if not isinstance(raw_items, Sequence) or isinstance(raw_items, str | bytes | bytearray):
        return ()
    mapped: list[EvidenceItemInput] = []
    for raw_item in raw_items:
        if not isinstance(raw_item, Mapping):
            continue
        document_id = _safe_text(raw_item.get("document_id"), fallback="document")
        record_id = _safe_text(raw_item.get("record_id"), fallback="record")
        confidence = _confidence(raw_item.get("confidence"))
        mapped.append(
            EvidenceItemInput(
                evidence_id=_evidence_id(document_id=document_id, record_id=record_id),
                ticker=source.ticker,
                scope=source.scope or f"document:{document_id}",
                type=source.type,
                source=source.source_label,
                title=source.title,
                published_at=source.published_at,
                url=None,
                stance=source.stance,
                strength=_strength(confidence),
                claim_scope=source.claim_scope,
                evidence_role=source.evidence_role,
                relevance=source.relevance,
                novelty=source.novelty,
                interpretation_basis=(
                    "Citation-backed document evidence matched the configured question "
                    f"for record {record_id}."
                ),
            )
        )
    return tuple(mapped)


def _safe_text(value: object, *, fallback: str) -> str:
    if isinstance(value, str) and value:
        return value
    return fallback


def _confidence(value: object) -> float:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return max(0.0, min(1.0, float(value)))
    return 0.5


def _strength(confidence: float) -> EvidenceStrength:
    if confidence >= 0.85:
        return "strong"
    if confidence >= 0.6:
        return "moderate"
    return "weak"


def _evidence_id(*, document_id: str, record_id: str) -> str:
    digest = sha256(f"{document_id}\0{record_id}".encode()).hexdigest()[:16]
    return f"document_evidence_{digest}"
