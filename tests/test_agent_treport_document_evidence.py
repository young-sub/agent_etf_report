from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from agent_pack.artifacts import LocalArtifactManager
from agent_pack.context import ContextManager
from agent_pack.inspection import RunInspectionService
from agent_pack.models import (
    ArtifactRef,
    JsonValue,
    Message,
    ModelResponse,
    TextBlock,
    ToolCall,
    ToolResult,
)
from agent_pack.models_client import FakeModelClient
from agent_pack.store import SQLiteRunStore
from agent_pack_docs import LocalIndexArtifactStore, LocalJsonArtifactWriter

from agent_treport.signal_report.adapters import document_evidence
from agent_treport.signal_report.adapters.document_evidence import (
    DocumentEvidenceComposer,
    DocumentEvidenceCompositionResult,
    DocumentEvidenceSource,
)
from agent_treport.signal_report.domain.evidence import EvidenceItemInput


def run_async(awaitable):
    return asyncio.run(awaitable)


def test_document_evidence_composer_uses_private_workflow_tracer_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, Mapping[str, JsonValue]]] = []
    document_artifact = ArtifactRef(
        artifact_id="artifact-document-ir",
        name="document-ir.json",
        uri="artifact://document-parse/artifact-document-ir",
        media_type="application/json",
    )
    index_artifact = ArtifactRef(
        artifact_id="artifact-document-index",
        name="document-index",
        uri="artifact://document-index/artifact-document-index",
        media_type="application/octet-stream",
    )

    class FakeTool:
        def __init__(self, name: str, result: Mapping[str, JsonValue]) -> None:
            self.name = name
            self._result = result

        async def execute(self, *, call: ToolCall, run_id: str) -> ToolResult:
            assert run_id == "run_document_evidence"
            calls.append((self.name, call.origin, call.arguments))
            return ToolResult(
                tool_call_id=call.id,
                status="succeeded",
                result=dict(self._result),
            )

    monkeypatch.setattr(
        document_evidence.DocumentParseTool,
        "with_doc_parser",
        staticmethod(
            lambda **_: FakeTool(
                "document.parse",
                {
                    "tool_name": "document.parse",
                    "parse_status": "succeeded",
                    "artifact_refs": (document_artifact.model_dump(mode="json"),),
                    "envelope": {
                        "raw_parser_envelope": "parser envelope must not leak"
                    },
                },
            )
        ),
    )
    monkeypatch.setattr(
        document_evidence.DocumentIndexTool,
        "with_doc_parser",
        staticmethod(
            lambda **_: FakeTool(
                "document.index",
                {
                    "tool_name": "document.index",
                    "index_status": "succeeded",
                    "document": {"document_id": "doc-alpha", "source_format": "docx"},
                    "artifact_refs": (index_artifact.model_dump(mode="json"),),
                },
            )
        ),
    )
    monkeypatch.setattr(
        document_evidence.DocumentEvidenceTool,
        "with_doc_parser",
        staticmethod(
            lambda **_: FakeTool(
                "document.evidence",
                {
                    "tool_name": "document.evidence",
                    "evidence_status": "succeeded",
                    "document_count": 1,
                    "evidence_count": 1,
                    "evidence": (
                        {
                            "document_id": "doc-alpha",
                            "record_id": "record-1",
                            "text": (
                                "RAW document text with C:/local/path and "
                                "credential=secret-token"
                            ),
                            "source_locations": ({"page": 1},),
                            "confidence": 0.91,
                            "warnings": (),
                        },
                    ),
                    "summary": {
                        "envelope": "parser envelope must not leak",
                        "source_refs": (),
                    },
                },
            )
        ),
    )

    class FakeJsonArtifactWriter:
        def write_json_artifact(
            self,
            *,
            artifact_id: str,
            name: str,
            payload: Mapping[str, Any],
            media_type: str,
            metadata: Mapping[str, JsonValue] | None = None,
        ) -> ArtifactRef:
            raise AssertionError("fake writer should not be used by patched tools")

    class FakeIndexArtifactStore:
        def create_index_artifact(
            self,
            *,
            artifact_id: str,
            name: str,
            metadata: Mapping[str, JsonValue] | None = None,
        ) -> tuple[ArtifactRef, Path]:
            raise AssertionError("fake index store should not be used by patched tools")

    composer = DocumentEvidenceComposer(
        artifact_resolver=lambda artifact: tmp_path / artifact.name,
        artifact_writer=FakeJsonArtifactWriter(),
        index_store=FakeIndexArtifactStore(),
        index_resolver=lambda artifact: tmp_path / artifact.name,
    )
    result = run_async(
        composer.compose(
            run_id="run_document_evidence",
            sources=(
                DocumentEvidenceSource(
                    artifact=ArtifactRef(
                        artifact_id="artifact-source-docx",
                        name="source.docx",
                        uri="artifact://inputs/source.docx",
                        media_type=(
                            "application/vnd.openxmlformats-officedocument."
                            "wordprocessingml.document"
                        ),
                    ),
                    question="What supports the NVDA accumulation signal?",
                    source_label="Document Fixture",
                    title="Document evidence fixture",
                    ticker="NVDA",
                    claim_scope="signal:security:sec_nvda:weight_increase",
                    type="analyst_report",
                    stance="supporting",
                    evidence_role="interpretation_support",
                    relevance="high",
                    novelty="new",
                ),
            ),
        )
    )

    assert [name for name, _, _ in calls] == [
        "document.parse",
        "document.index",
        "document.evidence",
    ]
    assert all(origin == "workflow" for _, origin, _ in calls)
    assert result.diagnostics["registered_tool_names"] == (
        "document.parse",
        "document.index",
        "document.evidence",
    )
    assert "document.search" not in result.diagnostics["registered_tool_names"]
    assert "document.read_excerpt" not in result.diagnostics["registered_tool_names"]
    assert len(result.evidence) == 1
    assert isinstance(result.evidence[0], EvidenceItemInput)
    assert result.evidence[0].claim_scope == "signal:security:sec_nvda:weight_increase"
    assert result.evidence[0].strength == "strong"

    serialized = json.dumps(
        {
            "evidence": [item.model_dump(mode="json") for item in result.evidence],
            "diagnostics": result.diagnostics,
        },
        ensure_ascii=False,
    )
    assert "RAW document text" not in serialized
    assert "C:/local/path" not in serialized
    assert "credential" not in serialized.lower()
    assert "parser envelope" not in serialized.lower()


def test_document_evidence_composer_smokes_docx_and_pdf_fixtures(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    workspace_root = repo_root.parent
    docx_path = workspace_root / "doc_parser/tests/fixtures/golden/docx/business-report.docx"
    pdf_path = workspace_root / "doc_parser/tests/fixtures/golden/pdf/business-report.pdf"
    source_paths = {
        "artifact-source-docx-business-report": docx_path,
        "artifact-source-pdf-business-report": pdf_path,
    }
    document_writer = LocalJsonArtifactWriter(tmp_path / "document-artifacts")
    index_store = LocalIndexArtifactStore(tmp_path / "document-indexes")

    def resolve_artifact(artifact: ArtifactRef) -> Path:
        if artifact.artifact_id in source_paths:
            return source_paths[artifact.artifact_id]
        return document_writer.path_for(artifact.artifact_id)

    composer = DocumentEvidenceComposer(
        artifact_resolver=resolve_artifact,
        artifact_writer=document_writer,
        index_store=index_store,
        index_resolver=lambda artifact: index_store.path_for(artifact.artifact_id),
    )

    result = run_async(
        composer.compose(
            run_id="run_document_evidence_smoke",
            sources=(
                DocumentEvidenceSource(
                    artifact=ArtifactRef(
                        artifact_id="artifact-source-docx-business-report",
                        name="business-report.docx",
                        uri="artifact://inputs/business-report.docx",
                        media_type=(
                            "application/vnd.openxmlformats-officedocument."
                            "wordprocessingml.document"
                        ),
                    ),
                    question="What happened to revenue?",
                    source_label="DOCX business report fixture",
                    title="Document evidence: DOCX business report",
                    ticker="NVDA",
                    claim_scope="signal:security:sec_nvda:weight_increase",
                    type="analyst_report",
                    stance="supporting",
                    evidence_role="interpretation_support",
                    relevance="high",
                    novelty="new",
                ),
                DocumentEvidenceSource(
                    artifact=ArtifactRef(
                        artifact_id="artifact-source-pdf-business-report",
                        name="business-report.pdf",
                        uri="artifact://inputs/business-report.pdf",
                        media_type="application/pdf",
                    ),
                    question="What is the status of Newark backlog?",
                    source_label="PDF business report fixture",
                    title="Document evidence: PDF business report",
                    ticker="PLTR",
                    claim_scope="signal:security:sec_pltr:weight_increase",
                    type="analyst_report",
                    stance="supporting",
                    evidence_role="interpretation_support",
                    relevance="high",
                    novelty="new",
                ),
            ),
        )
    )

    titles = {item.title for item in result.evidence}
    assert "Document evidence: DOCX business report" in titles
    assert "Document evidence: PDF business report" in titles
    assert result.diagnostics["source_formats"] == ("docx", "pdf")
    assert all(isinstance(item, EvidenceItemInput) for item in result.evidence)

    serialized = json.dumps(
        {
            "evidence": [item.model_dump(mode="json") for item in result.evidence],
            "diagnostics": result.diagnostics,
        },
        ensure_ascii=False,
    )
    assert "Revenue improved while support volume" not in serialized
    assert "Newark carries the largest backlog" not in serialized
    assert str(workspace_root) not in serialized
    assert str(docx_path) not in serialized
    assert str(pdf_path) not in serialized
    assert "DocumentIR" not in serialized


class FakeDocumentEvidenceComposer:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[DocumentEvidenceSource, ...]]] = []

    async def compose(
        self,
        *,
        run_id: str,
        sources: tuple[DocumentEvidenceSource, ...],
    ) -> DocumentEvidenceCompositionResult:
        self.calls.append((run_id, sources))
        return DocumentEvidenceCompositionResult(
            evidence=(
                EvidenceItemInput(
                    evidence_id="document_evidence_workflow",
                    ticker="NVDA",
                    scope="document:doc-workflow",
                    type="analyst_report",
                    source="Document Fixture",
                    title="Workflow document evidence",
                    stance="supporting",
                    strength="strong",
                    claim_scope="signal:security:sec_nvda:weight_increase",
                    evidence_role="interpretation_support",
                    relevance="high",
                    novelty="new",
                    interpretation_basis=(
                        "Citation-backed document evidence matched the configured question."
                    ),
                ),
            ),
            diagnostics={
                "schema_version": "agent_treport.document_evidence_composition.v1",
                "registered_tool_names": (
                    "document.parse",
                    "document.index",
                    "document.evidence",
                ),
                "source_count": 1,
                "source_formats": ("docx",),
                "evidence_count": 1,
            },
        )


def test_signal_report_workflow_adds_document_evidence_without_model_tool_access_or_leaks(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        sqlite_path = tmp_path / "treport.sqlite3"
        artifact_root = tmp_path / "artifacts"
        store = SQLiteRunStore(str(sqlite_path))
        artifacts = LocalArtifactManager(artifact_root)
        composer = FakeDocumentEvidenceComposer()
        source = DocumentEvidenceSource(
            artifact=ArtifactRef(
                artifact_id="artifact-source-docx-workflow",
                name="source.docx",
                uri="artifact://inputs/source.docx",
                media_type=(
                    "application/vnd.openxmlformats-officedocument."
                    "wordprocessingml.document"
                ),
            ),
            question="What supports the workflow signal?",
            source_label="Document Fixture",
            title="Workflow document evidence",
            ticker="NVDA",
            claim_scope="signal:security:sec_nvda:weight_increase",
            type="analyst_report",
            stance="supporting",
            evidence_role="interpretation_support",
            relevance="high",
            novelty="new",
        )
        model = FakeModelClient(
            [
                ModelResponse(
                    message=Message(
                        role="assistant",
                        content=(TextBlock(text="safe document evidence commentary"),),
                    )
                )
            ]
        )

        result = await run_signal_report(
            run_id="run_treport_signal_document_evidence",
            store=store,
            context_manager=ContextManager(store=store),
            artifact_manager=artifacts,
            model_client=model,
            document_evidence_composer=composer,
            document_evidence_sources=(source,),
        )
        events = await store.list_events("run_treport_signal_document_evidence")
        await store.close()

        assert result.status == "succeeded"
        assert composer.calls == [("run_treport_signal_document_evidence", (source,))]
        state = result.output["state"]
        assert state["document_evidence_artifact_id"] == "artifact_treport_document_evidence"
        assert state["document_evidence_count"] == 1
        assert state["document_evidence_tool_names"] == (
            "document.parse",
            "document.index",
            "document.evidence",
        )

        inputs = json.loads(
            (await artifacts.read_bytes("artifact_treport_signal_report_inputs")).decode("utf-8")
        )
        payload = json.loads(
            (await artifacts.read_bytes("artifact_treport_signal_payload")).decode("utf-8")
        )
        document_evidence = json.loads(
            (await artifacts.read_bytes("artifact_treport_document_evidence")).decode("utf-8")
        )
        report = (await artifacts.read_bytes("artifact_treport_report")).decode("utf-8")
        html = (await artifacts.read_bytes("artifact_treport_html_report")).decode("utf-8")
        telegram_alert = (await artifacts.read_bytes("artifact_treport_telegram_alert")).decode(
            "utf-8"
        )

        assert any(
            item["title"] == "Workflow document evidence" for item in inputs["evidence"]
        )
        assert any(
            item["title"] == "Workflow document evidence"
            for item in payload["evidence_ledger"]
        )
        assert document_evidence == {
            "schema_version": "agent_treport.document_evidence_artifact.v1",
            "diagnostics": {
                "schema_version": "agent_treport.document_evidence_composition.v1",
                "registered_tool_names": [
                    "document.parse",
                    "document.index",
                    "document.evidence",
                ],
                "source_count": 1,
                "source_formats": ["docx"],
                "evidence_count": 1,
            },
            "evidence_count": 1,
            "evidence_ids": ["document_evidence_workflow"],
        }

        model_request_dump = json.dumps(
            [request.model_dump(mode="json") for request in model.requests],
            ensure_ascii=False,
        )
        all_surfaces = json.dumps(
            {
                "result": result.model_dump(mode="json"),
                "events": [event.model_dump(mode="json") for event in events],
                "inputs": inputs,
                "payload": payload,
                "document_evidence": document_evidence,
                "report": report,
                "html": html,
                "telegram_alert": telegram_alert,
                "model_requests": model_request_dump,
            },
            ensure_ascii=False,
        )
        assert "document.search" not in model_request_dump
        assert "document.read_excerpt" not in model_request_dump
        assert "RAW document text" not in all_surfaces
        assert "credential" not in all_surfaces.lower()
        assert str(tmp_path) not in all_surfaces
        assert "parser envelope" not in all_surfaces.lower()

        reopened = SQLiteRunStore(str(sqlite_path))
        try:
            inspection = await RunInspectionService(reopened).build_snapshot(
                "run_treport_signal_document_evidence"
            )
        finally:
            await reopened.close()
        assert "artifact_treport_document_evidence" in {
            artifact.artifact_id for artifact in inspection.artifacts
        }

    from agent_treport.workflows.signal_report import run_signal_report

    run_async(scenario())