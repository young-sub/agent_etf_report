from __future__ import annotations

import asyncio
import json
from io import StringIO
from pathlib import Path

from agent_treport.cli import run_cli_async
from agent_treport.signal_report.adapters.source_acquisition import (
    LIVE_SOURCE_PROVIDER_IDS,
)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _run_async(awaitable):
    return asyncio.run(awaitable)


def test_inspect_operational_source_cache_reports_registered_provider_layouts(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        cache_root = tmp_path / "source-provider-operational"
        stdout = StringIO()

        exit_code = await run_cli_async(
            [
                "inspect-operational-source-cache",
                "--cache-root",
                str(cache_root),
            ],
            stdout=stdout,
        )

        payload = json.loads(stdout.getvalue())
        assert exit_code == 0
        assert payload["schema_version"] == (
            "agent_treport.source_provider_operational_cache.layout.v1"
        )
        assert payload["status"] == "missing_artifacts"
        assert payload["registered_provider_ids"] == list(LIVE_SOURCE_PROVIDER_IDS)
        assert payload["provider_count"] == len(LIVE_SOURCE_PROVIDER_IDS)
        assert payload["expected_artifacts"] == [
            "catalog/source_catalog.json",
            "catalog/universe_state.json",
            "catalog/source_acquisition_summary.json",
            "focus_etf_set.json",
            "holdings-history/",
            "security-master/",
        ]
        assert [item["source_provider_id"] for item in payload["providers"]] == list(
            LIVE_SOURCE_PROVIDER_IDS
        )
        first_provider = payload["providers"][0]
        assert first_provider["cache_root"] == f"{LIVE_SOURCE_PROVIDER_IDS[0]}/"
        assert first_provider["status"] == "missing_artifacts"
        assert first_provider["artifacts"][0] == {
            "artifact": "catalog/source_catalog.json",
            "path": f"{LIVE_SOURCE_PROVIDER_IDS[0]}/catalog/source_catalog.json",
            "kind": "file",
            "status": "missing",
        }
        assert payload["missing_artifact_count"] == (
            len(LIVE_SOURCE_PROVIDER_IDS) * len(payload["expected_artifacts"])
        )
        assert {
            item["path"] for item in payload["missing_artifacts"][:2]
        } == {
            f"{LIVE_SOURCE_PROVIDER_IDS[0]}/catalog/source_catalog.json",
            f"{LIVE_SOURCE_PROVIDER_IDS[0]}/catalog/universe_state.json",
        }

    _run_async(scenario())


def test_inspect_operational_source_cache_rejects_unsafe_focus_etf_ids(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        cache_root = tmp_path / "source-provider-operational"
        ace_root = cache_root / "ace"
        _write_json(
            ace_root / "focus_etf_set.json",
            {
                "schema_version": "agent_treport.focus_etf_set.v1",
                "focus_etf_ids": ["../escape"],
            },
        )
        stdout = StringIO()

        exit_code = await run_cli_async(
            [
                "inspect-operational-source-cache",
                "--cache-root",
                str(cache_root),
            ],
            stdout=stdout,
        )

        payload = json.loads(stdout.getvalue())
        assert exit_code == 0
        assert payload["status"] == "invalid_artifacts"
        assert payload["invalid_artifact_count"] == 1
        assert payload["invalid_artifacts"] == [
            {
                "source_provider_id": "ace",
                "artifact": "focus_etf_set.json",
                "path": "ace/focus_etf_set.json",
                "message": "focus_etf_id is not path-safe",
            }
        ]

    _run_async(scenario())
