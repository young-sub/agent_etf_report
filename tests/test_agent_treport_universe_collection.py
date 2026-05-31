from __future__ import annotations

import importlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest


def _collect_universe_fixture() -> Any:
    module = importlib.import_module(
        "agent_treport.signal_report.adapters.operational_universe"
    )
    return module.collect_universe_fixture


def _universe_brands() -> list[dict[str, object]]:
    return [
        {
            "brand_id": "brand_alpha",
            "brand_name": "Alpha Asset Management",
            "source_provider_id": "provider_fixture",
        }
    ]


def _universe_etfs() -> list[dict[str, object]]:
    return [
        {
            "etf_id": "etf_focus_ai",
            "etf_name": "AI Native Collection ETF",
            "brand_id": "brand_alpha",
            "source_provider_id": "provider_fixture",
        },
        {
            "etf_id": "etf_robotics",
            "etf_name": "Robotics Growth ETF",
            "brand_id": "brand_alpha",
            "source_provider_id": "provider_fixture",
        },
    ]


def _write_universe_fixture(
    tmp_path: Path,
    *,
    brands: list[dict[str, object]] | None = None,
    etfs: list[dict[str, object]] | None = None,
    filename: str = "native_universe_fixture.json",
) -> Path:
    fixture_path = tmp_path / filename
    fixture_path.write_text(
        json.dumps(
            {
                "schema_version": "agent_treport.native_universe.fixture.v1",
                "complete": True,
                "brands": brands or _universe_brands(),
                "etfs": etfs or _universe_etfs(),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return fixture_path


def _write_source_provider_fixture(
    tmp_path: Path,
    *,
    source_provider_id: str = "provider_kodex_fake",
    complete: bool = True,
    entries: list[dict[str, object]] | None = None,
    filename: str = "source_provider_fixture.json",
) -> Path:
    fixture_path = tmp_path / filename
    fixture_path.write_text(
        json.dumps(
            {
                "schema_version": "agent_treport.source_provider.fake.v1",
                "source_provider_id": source_provider_id,
                "catalog": {
                    "complete": complete,
                    "entries": entries
                    or [
                        {
                            "source_provider_id": source_provider_id,
                            "provider_etf_id": "2ETF35",
                            "etf_id": "etf_kodex_ai",
                            "etf_name": "KODEX AI ETF",
                            "brand_id": "brand_samsung",
                            "brand_name": "Samsung Asset Management",
                            "strategy_label": "active",
                            "locator": "https://provider.example/internal/2ETF35",
                        },
                        {
                            "source_provider_id": source_provider_id,
                            "provider_etf_id": "2ETF99",
                            "etf_id": "etf_kodex_robotics",
                            "etf_name": "KODEX Robotics ETF",
                            "brand_id": "brand_samsung",
                            "brand_name": "Samsung Asset Management",
                            "strategy_label": "passive",
                            "locator": "https://provider.example/internal/2ETF99",
                        },
                    ],
                },
                "holdings_results": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return fixture_path


def test_collect_source_catalog_complete_fake_catalog_updates_universe_state(
    tmp_path: Path,
) -> None:
    module = importlib.import_module(
        "agent_treport.signal_report.adapters.source_acquisition"
    )
    provider = module.FakeSourceProvider.from_fixture_path(
        _write_source_provider_fixture(tmp_path)
    )
    dest_dir = tmp_path / "source_catalog"

    summary = module.collect_source_catalog(
        provider=provider,
        dest_dir=dest_dir,
        now=lambda: datetime(2026, 5, 11, 0, 45, tzinfo=UTC),
    )

    source_catalog = json.loads(
        (dest_dir / "source_catalog.json").read_text(encoding="utf-8")
    )
    universe_state = json.loads(
        (dest_dir / "universe_state.json").read_text(encoding="utf-8")
    )
    persisted_summary = json.loads(
        (dest_dir / "source_acquisition_summary.json").read_text(encoding="utf-8")
    )

    assert summary == persisted_summary
    assert source_catalog["schema_version"] == "agent_treport.source_catalog.v1"
    assert source_catalog["source_provider_id"] == "provider_kodex_fake"
    assert source_catalog["complete"] is True
    assert [entry["provider_etf_id"] for entry in source_catalog["entries"]] == [
        "2ETF35",
        "2ETF99",
    ]
    assert source_catalog["entries"][0]["locator"] == (
        "https://provider.example/internal/2ETF35"
    )
    assert universe_state["schema_version"] == "agent_treport.native_universe.state.v1"
    assert universe_state["collection_source_type"] == "source_provider"
    assert universe_state["etfs"] == [
        {
            "brand_id": "brand_samsung",
            "etf_id": "etf_kodex_ai",
            "etf_name": "KODEX AI ETF",
            "source_provider_id": "provider_kodex_fake",
            "status": "active",
        },
    ]
    assert summary["schema_version"] == "agent_treport.source_acquisition.summary.v1"
    assert summary["source_provider_id"] == "provider_kodex_fake"
    assert summary["run_outcome"] == "succeeded"
    assert summary["catalog_entry_count"] == 2
    assert summary["catalog_entries"] == [
        {
            "brand_id": "brand_samsung",
            "etf_id": "etf_kodex_ai",
            "source_provider_id": "provider_kodex_fake",
            "is_active_strategy_etf": True,
            "active_strategy_source": "source_metadata",
            "active_strategy_confidence": "high",
        },
        {
            "brand_id": "brand_samsung",
            "etf_id": "etf_kodex_robotics",
            "source_provider_id": "provider_kodex_fake",
            "is_active_strategy_etf": False,
            "active_strategy_source": "source_metadata",
            "active_strategy_confidence": "high",
        },
    ]
    assert summary["active_strategy_classification_counts"] == {
        "active_strategy": 1,
        "passive_strategy": 1,
        "unknown_strategy": 0,
    }
    rendered_summary = json.dumps(summary, ensure_ascii=False)
    assert str(tmp_path) not in rendered_summary
    assert "https://" not in rendered_summary
    assert "endpoint" not in rendered_summary
    assert "locator" not in rendered_summary


def test_collect_source_catalog_classifies_active_strategy_and_filters_universe(
    tmp_path: Path,
) -> None:
    module = importlib.import_module(
        "agent_treport.signal_report.adapters.source_acquisition"
    )
    provider = module.FakeSourceProvider.from_fixture_path(
        _write_source_provider_fixture(
            tmp_path,
            entries=[
                {
                    "source_provider_id": "provider_kodex_fake",
                    "provider_etf_id": "2ETF35",
                    "etf_id": "etf_metadata_active",
                    "etf_name": "KODEX AI ETF",
                    "brand_id": "brand_samsung",
                    "brand_name": "Samsung Asset Management",
                    "strategy_label": "Active",
                    "locator": "https://provider.example/internal/2ETF35",
                },
                {
                    "source_provider_id": "provider_kodex_fake",
                    "provider_etf_id": "2ETF36",
                    "etf_id": "etf_passive_override",
                    "etf_name": "KODEX Bond Active ETF",
                    "brand_id": "brand_samsung",
                    "brand_name": "Samsung Asset Management",
                    "strategy_label": "Active",
                    "locator": "https://provider.example/internal/2ETF36",
                },
                {
                    "source_provider_id": "provider_kodex_fake",
                    "provider_etf_id": "2ETF37",
                    "etf_id": "etf_name_token_active",
                    "etf_name": "KODEX 액티브 Growth ETF",
                    "brand_id": "brand_samsung",
                    "brand_name": "Samsung Asset Management",
                    "locator": "https://provider.example/internal/2ETF37",
                },
                {
                    "source_provider_id": "provider_kodex_fake",
                    "provider_etf_id": "2ETF38",
                    "etf_id": "etf_unknown_strategy",
                    "etf_name": "KODEX Growth ETF",
                    "brand_id": "brand_samsung",
                    "brand_name": "Samsung Asset Management",
                    "locator": "https://provider.example/internal/2ETF38",
                },
                {
                    "source_provider_id": "provider_kodex_fake",
                    "provider_etf_id": "2ETF39",
                    "etf_id": "etf_government_bond_active",
                    "etf_name": "KODEX 국고채액티브 ETF",
                    "brand_id": "brand_samsung",
                    "brand_name": "Samsung Asset Management",
                    "strategy_label": "Active",
                    "locator": "https://provider.example/internal/2ETF39",
                },
                {
                    "source_provider_id": "provider_kodex_fake",
                    "provider_etf_id": "2ETF40",
                    "etf_id": "etf_money_market_active",
                    "etf_name": "KODEX CD금리&머니마켓액티브 ETF",
                    "brand_id": "brand_samsung",
                    "brand_name": "Samsung Asset Management",
                    "strategy_label": "Active",
                    "locator": "https://provider.example/internal/2ETF40",
                },
            ],
        )
    )
    dest_dir = tmp_path / "source_catalog"

    summary = module.collect_source_catalog(
        provider=provider,
        dest_dir=dest_dir,
        now=lambda: datetime(2026, 5, 11, 0, 45, tzinfo=UTC),
    )

    source_catalog = json.loads(
        (dest_dir / "source_catalog.json").read_text(encoding="utf-8")
    )
    universe_state = json.loads(
        (dest_dir / "universe_state.json").read_text(encoding="utf-8")
    )

    catalog_by_id = {
        entry["provider_etf_id"]: entry for entry in source_catalog["entries"]
    }
    assert source_catalog["entries"] == [
        catalog_by_id["2ETF35"],
        catalog_by_id["2ETF36"],
        catalog_by_id["2ETF37"],
        catalog_by_id["2ETF38"],
        catalog_by_id["2ETF39"],
        catalog_by_id["2ETF40"],
    ]
    assert catalog_by_id["2ETF35"]["is_active_strategy_etf"] is True
    assert catalog_by_id["2ETF35"]["active_strategy_source"] == "source_metadata"
    assert catalog_by_id["2ETF35"]["active_strategy_confidence"] == "high"
    assert catalog_by_id["2ETF36"]["is_active_strategy_etf"] is False
    assert catalog_by_id["2ETF36"]["active_strategy_source"] == "passive_keyword"
    assert catalog_by_id["2ETF36"]["active_strategy_confidence"] == "high"
    assert catalog_by_id["2ETF37"]["is_active_strategy_etf"] is True
    assert catalog_by_id["2ETF37"]["active_strategy_source"] == "name_token"
    assert catalog_by_id["2ETF37"]["active_strategy_confidence"] == "low"
    assert catalog_by_id["2ETF38"]["is_active_strategy_etf"] is None
    assert catalog_by_id["2ETF38"]["active_strategy_source"] == "unknown"
    assert catalog_by_id["2ETF38"]["active_strategy_confidence"] == "low"
    assert catalog_by_id["2ETF39"]["is_active_strategy_etf"] is False
    assert catalog_by_id["2ETF39"]["active_strategy_source"] == "passive_keyword"
    assert catalog_by_id["2ETF39"]["active_strategy_confidence"] == "high"
    assert catalog_by_id["2ETF40"]["is_active_strategy_etf"] is False
    assert catalog_by_id["2ETF40"]["active_strategy_source"] == "passive_keyword"
    assert catalog_by_id["2ETF40"]["active_strategy_confidence"] == "high"
    assert [entry["etf_id"] for entry in universe_state["etfs"]] == [
        "etf_metadata_active",
        "etf_name_token_active",
    ]
    assert summary["active_strategy_classification_counts"] == {
        "active_strategy": 2,
        "passive_strategy": 3,
        "unknown_strategy": 1,
    }
    assert summary["active_strategy_review_samples"] == [
        {
            "source_provider_id": "provider_kodex_fake",
            "brand_id": "brand_samsung",
            "etf_id": "etf_unknown_strategy",
            "active_strategy_source": "unknown",
            "active_strategy_confidence": "low",
        }
    ]
    assert summary["catalog_entries"][0]["active_strategy_source"] == (
        "source_metadata"
    )
    assert summary["catalog_entries"][1]["active_strategy_source"] == (
        "passive_keyword"
    )
    rendered_summary = json.dumps(summary, ensure_ascii=False)
    assert str(tmp_path) not in rendered_summary
    assert "https://" not in rendered_summary
    assert "locator" not in rendered_summary


def test_collect_source_catalog_applies_exact_match_seed_evidence(
    tmp_path: Path,
) -> None:
    module = importlib.import_module(
        "agent_treport.signal_report.adapters.source_acquisition"
    )
    provider = module.FakeSourceProvider.from_fixture_path(
        _write_source_provider_fixture(
            tmp_path,
            source_provider_id="sol",
            entries=[
                {
                    "source_provider_id": "sol",
                    "provider_etf_id": "211099",
                    "etf_id": "etf_sol_211099",
                    "etf_name": "SOL AI ETF",
                    "brand_id": "brand_shinhan_asset_management",
                    "brand_name": "Shinhan Asset Management",
                    "locator": "https://provider.example/internal/211099",
                },
                {
                    "source_provider_id": "sol",
                    "provider_etf_id": "210920",
                    "etf_id": "etf_sol_210920",
                    "etf_name": "SOL 한국형글로벌플랫폼&메타버스액티브",
                    "brand_id": "brand_shinhan_asset_management",
                    "brand_name": "Shinhan Asset Management",
                    "is_active_strategy_etf": True,
                    "active_strategy_source": "name_token",
                    "active_strategy_confidence": "low",
                    "locator": "https://provider.example/internal/210920",
                }
            ],
        )
    )
    dest_dir = tmp_path / "source_catalog"

    summary = module.collect_source_catalog(
        provider=provider,
        dest_dir=dest_dir,
        now=lambda: datetime(2026, 5, 11, 0, 45, tzinfo=UTC),
    )

    source_catalog = json.loads(
        (dest_dir / "source_catalog.json").read_text(encoding="utf-8")
    )
    universe_state = json.loads(
        (dest_dir / "universe_state.json").read_text(encoding="utf-8")
    )

    assert source_catalog["entries"][0]["is_active_strategy_etf"] is True
    assert source_catalog["entries"][0]["active_strategy_source"] == "reference_seed"
    assert source_catalog["entries"][0]["active_strategy_confidence"] == "high"
    assert source_catalog["entries"][1]["is_active_strategy_etf"] is False
    assert source_catalog["entries"][1]["active_strategy_source"] == "reference_seed"
    assert source_catalog["entries"][1]["active_strategy_confidence"] == "high"
    assert universe_state["etfs"] == [
        {
            "brand_id": "brand_shinhan_asset_management",
            "etf_id": "etf_sol_211099",
            "etf_name": "SOL AI ETF",
            "source_provider_id": "sol",
            "status": "active",
        }
    ]
    assert summary["active_strategy_evidence"]["seed_override_count"] == 2
    assert summary["catalog_entries"][0]["active_strategy_source"] == "reference_seed"


def test_collect_universe_fixture_first_run_writes_state_and_path_safe_summary(
    tmp_path: Path,
) -> None:
    fixture_path = _write_universe_fixture(tmp_path)
    dest_dir = tmp_path / "native_universe"

    summary = _collect_universe_fixture()(
        fixture_path=fixture_path,
        dest_dir=dest_dir,
        now=lambda: datetime(2026, 5, 11, 1, 0, tzinfo=UTC),
    )

    state_path = dest_dir / "universe_state.json"
    summary_path = dest_dir / "universe_summary.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    persisted_summary = json.loads(summary_path.read_text(encoding="utf-8"))

    assert summary == persisted_summary
    assert state == {
        "schema_version": "agent_treport.native_universe.state.v1",
        "collection_source_type": "fixture",
        "updated_at": "2026-05-11T01:00:00+00:00",
        "etfs": [
            {
                "etf_id": "etf_focus_ai",
                "etf_name": "AI Native Collection ETF",
                "brand_id": "brand_alpha",
                "source_provider_id": "provider_fixture",
                "status": "active",
            },
            {
                "etf_id": "etf_robotics",
                "etf_name": "Robotics Growth ETF",
                "brand_id": "brand_alpha",
                "source_provider_id": "provider_fixture",
                "status": "active",
            },
        ],
        "brands": [
            {
                "brand_id": "brand_alpha",
                "brand_name": "Alpha Asset Management",
                "source_provider_id": "provider_fixture",
                "status": "active",
            }
        ],
    }
    assert summary["schema_version"] == "agent_treport.native_universe.summary.v1"
    assert summary["collection_source_type"] == "fixture"
    assert summary["collected_at"] == "2026-05-11T01:00:00+00:00"
    assert summary["state_output"] == {"state_path": "universe_state.json"}
    assert summary["active_etf_count"] == 2
    assert summary["removed_etf_count"] == 0
    assert summary["active_brand_count"] == 1
    assert summary["removed_brand_count"] == 0
    assert summary["etf_change_counts"] == {
        "added": 2,
        "changed": 0,
        "removed": 0,
        "unchanged": 0,
    }
    assert summary["brand_change_counts"] == {
        "added": 1,
        "changed": 0,
        "removed": 0,
        "unchanged": 0,
    }
    assert summary["etf_changes"]["added"] == [
        {
            "etf_id": "etf_focus_ai",
            "etf_name": "AI Native Collection ETF",
            "brand_id": "brand_alpha",
            "source_provider_id": "provider_fixture",
        },
        {
            "etf_id": "etf_robotics",
            "etf_name": "Robotics Growth ETF",
            "brand_id": "brand_alpha",
            "source_provider_id": "provider_fixture",
        },
    ]
    assert summary["brand_changes"]["added"] == [
        {
            "brand_id": "brand_alpha",
            "brand_name": "Alpha Asset Management",
            "source_provider_id": "provider_fixture",
        }
    ]

    rendered_summary = json.dumps(summary, ensure_ascii=False)
    assert str(fixture_path) not in rendered_summary
    assert "http://" not in rendered_summary
    assert "https://" not in rendered_summary
    assert "\\Users\\" not in rendered_summary


def test_collect_universe_fixture_rerun_reports_unchanged_not_added(
    tmp_path: Path,
) -> None:
    fixture_path = _write_universe_fixture(tmp_path)
    dest_dir = tmp_path / "native_universe"
    collect_universe_fixture = _collect_universe_fixture()

    collect_universe_fixture(
        fixture_path=fixture_path,
        dest_dir=dest_dir,
        now=lambda: datetime(2026, 5, 11, 1, 0, tzinfo=UTC),
    )
    summary = collect_universe_fixture(
        fixture_path=fixture_path,
        dest_dir=dest_dir,
        now=lambda: datetime(2026, 5, 12, 1, 0, tzinfo=UTC),
    )

    assert summary["etf_change_counts"] == {
        "added": 0,
        "changed": 0,
        "removed": 0,
        "unchanged": 2,
    }
    assert summary["brand_change_counts"] == {
        "added": 0,
        "changed": 0,
        "removed": 0,
        "unchanged": 1,
    }
    assert summary["etf_changes"]["added"] == []
    assert summary["etf_changes"]["unchanged"] == [
        {
            "etf_id": "etf_focus_ai",
            "etf_name": "AI Native Collection ETF",
            "brand_id": "brand_alpha",
            "source_provider_id": "provider_fixture",
        },
        {
            "etf_id": "etf_robotics",
            "etf_name": "Robotics Growth ETF",
            "brand_id": "brand_alpha",
            "source_provider_id": "provider_fixture",
        },
    ]
    assert summary["brand_changes"]["added"] == []
    assert summary["brand_changes"]["unchanged"] == [
        {
            "brand_id": "brand_alpha",
            "brand_name": "Alpha Asset Management",
            "source_provider_id": "provider_fixture",
        }
    ]


def test_collect_universe_fixture_reports_tracked_field_changes_not_display_names(
    tmp_path: Path,
) -> None:
    fixture_path = _write_universe_fixture(tmp_path)
    dest_dir = tmp_path / "native_universe"
    collect_universe_fixture = _collect_universe_fixture()
    collect_universe_fixture(
        fixture_path=fixture_path,
        dest_dir=dest_dir,
        now=lambda: datetime(2026, 5, 11, 1, 0, tzinfo=UTC),
    )
    changed_fixture_path = _write_universe_fixture(
        tmp_path,
        filename="changed_universe_fixture.json",
        brands=[
            {
                "brand_id": "brand_alpha",
                "brand_name": "Alpha Asset Management Renamed",
                "source_provider_id": "provider_fixture_v2",
            },
            {
                "brand_id": "brand_beta",
                "brand_name": "Beta Asset Management",
                "source_provider_id": "provider_fixture",
            },
        ],
        etfs=[
            {
                "etf_id": "etf_focus_ai",
                "etf_name": "AI Native Collection ETF Renamed",
                "brand_id": "brand_alpha",
                "source_provider_id": "provider_fixture",
            },
            {
                "etf_id": "etf_robotics",
                "etf_name": "Robotics Growth ETF",
                "brand_id": "brand_beta",
                "source_provider_id": "provider_fixture_v2",
            },
        ],
    )

    summary = collect_universe_fixture(
        fixture_path=changed_fixture_path,
        dest_dir=dest_dir,
        now=lambda: datetime(2026, 5, 12, 1, 0, tzinfo=UTC),
    )
    state = json.loads((dest_dir / "universe_state.json").read_text(encoding="utf-8"))

    assert summary["etf_change_counts"] == {
        "added": 0,
        "changed": 1,
        "removed": 0,
        "unchanged": 1,
    }
    assert summary["etf_changes"]["unchanged"] == [
        {
            "etf_id": "etf_focus_ai",
            "etf_name": "AI Native Collection ETF Renamed",
            "brand_id": "brand_alpha",
            "source_provider_id": "provider_fixture",
        }
    ]
    assert summary["etf_changes"]["changed"] == [
        {
            "etf_id": "etf_robotics",
            "etf_name": "Robotics Growth ETF",
            "brand_id": "brand_beta",
            "source_provider_id": "provider_fixture_v2",
            "changed_fields": ["brand_id", "source_provider_id"],
        }
    ]
    assert summary["brand_change_counts"] == {
        "added": 1,
        "changed": 1,
        "removed": 0,
        "unchanged": 0,
    }
    assert summary["brand_changes"]["changed"] == [
        {
            "brand_id": "brand_alpha",
            "brand_name": "Alpha Asset Management Renamed",
            "source_provider_id": "provider_fixture_v2",
            "changed_fields": ["source_provider_id"],
        }
    ]
    assert state["etfs"][0]["etf_name"] == "AI Native Collection ETF Renamed"
    assert state["brands"][0]["brand_name"] == "Alpha Asset Management Renamed"


def test_collect_universe_fixture_marks_missing_active_records_removed(
    tmp_path: Path,
) -> None:
    fixture_path = _write_universe_fixture(
        tmp_path,
        brands=[
            {
                "brand_id": "brand_alpha",
                "brand_name": "Alpha Asset Management",
                "source_provider_id": "provider_fixture",
            },
            {
                "brand_id": "brand_beta",
                "brand_name": "Beta Asset Management",
                "source_provider_id": "provider_fixture",
            },
        ],
        etfs=[
            {
                "etf_id": "etf_focus_ai",
                "etf_name": "AI Native Collection ETF",
                "brand_id": "brand_alpha",
                "source_provider_id": "provider_fixture",
            },
            {
                "etf_id": "etf_robotics",
                "etf_name": "Robotics Growth ETF",
                "brand_id": "brand_beta",
                "source_provider_id": "provider_fixture",
            },
        ],
    )
    dest_dir = tmp_path / "native_universe"
    collect_universe_fixture = _collect_universe_fixture()
    collect_universe_fixture(
        fixture_path=fixture_path,
        dest_dir=dest_dir,
        now=lambda: datetime(2026, 5, 11, 1, 0, tzinfo=UTC),
    )
    removed_fixture_path = _write_universe_fixture(
        tmp_path,
        filename="removed_universe_fixture.json",
        brands=[
            {
                "brand_id": "brand_alpha",
                "brand_name": "Alpha Asset Management",
                "source_provider_id": "provider_fixture",
            }
        ],
        etfs=[
            {
                "etf_id": "etf_focus_ai",
                "etf_name": "AI Native Collection ETF",
                "brand_id": "brand_alpha",
                "source_provider_id": "provider_fixture",
            }
        ],
    )

    summary = collect_universe_fixture(
        fixture_path=removed_fixture_path,
        dest_dir=dest_dir,
        now=lambda: datetime(2026, 5, 12, 1, 0, tzinfo=UTC),
    )
    state = json.loads((dest_dir / "universe_state.json").read_text(encoding="utf-8"))

    assert summary["active_etf_count"] == 1
    assert summary["removed_etf_count"] == 1
    assert summary["active_brand_count"] == 1
    assert summary["removed_brand_count"] == 1
    assert summary["etf_change_counts"] == {
        "added": 0,
        "changed": 0,
        "removed": 1,
        "unchanged": 1,
    }
    assert summary["brand_change_counts"] == {
        "added": 0,
        "changed": 0,
        "removed": 1,
        "unchanged": 1,
    }
    assert summary["etf_changes"]["removed"] == [
        {
            "etf_id": "etf_robotics",
            "etf_name": "Robotics Growth ETF",
            "brand_id": "brand_beta",
            "source_provider_id": "provider_fixture",
        }
    ]
    assert summary["brand_changes"]["removed"] == [
        {
            "brand_id": "brand_beta",
            "brand_name": "Beta Asset Management",
            "source_provider_id": "provider_fixture",
        }
    ]
    assert state["etfs"][1]["status"] == "removed"
    assert state["brands"][1]["status"] == "removed"


def test_collect_universe_fixture_reactivates_removed_records_as_changed(
    tmp_path: Path,
) -> None:
    initial_fixture_path = _write_universe_fixture(
        tmp_path,
        brands=[
            {
                "brand_id": "brand_alpha",
                "brand_name": "Alpha Asset Management",
                "source_provider_id": "provider_fixture",
            },
            {
                "brand_id": "brand_beta",
                "brand_name": "Beta Asset Management",
                "source_provider_id": "provider_fixture",
            },
        ],
        etfs=[
            {
                "etf_id": "etf_focus_ai",
                "etf_name": "AI Native Collection ETF",
                "brand_id": "brand_alpha",
                "source_provider_id": "provider_fixture",
            },
            {
                "etf_id": "etf_robotics",
                "etf_name": "Robotics Growth ETF",
                "brand_id": "brand_beta",
                "source_provider_id": "provider_fixture",
            },
        ],
    )
    removed_fixture_path = _write_universe_fixture(
        tmp_path,
        filename="removed_universe_fixture.json",
        brands=[
            {
                "brand_id": "brand_alpha",
                "brand_name": "Alpha Asset Management",
                "source_provider_id": "provider_fixture",
            }
        ],
        etfs=[
            {
                "etf_id": "etf_focus_ai",
                "etf_name": "AI Native Collection ETF",
                "brand_id": "brand_alpha",
                "source_provider_id": "provider_fixture",
            }
        ],
    )
    reactivated_fixture_path = _write_universe_fixture(
        tmp_path,
        filename="reactivated_universe_fixture.json",
        brands=[
            {
                "brand_id": "brand_alpha",
                "brand_name": "Alpha Asset Management",
                "source_provider_id": "provider_fixture",
            },
            {
                "brand_id": "brand_beta",
                "brand_name": "Beta Asset Management",
                "source_provider_id": "provider_fixture",
            },
        ],
        etfs=[
            {
                "etf_id": "etf_focus_ai",
                "etf_name": "AI Native Collection ETF",
                "brand_id": "brand_alpha",
                "source_provider_id": "provider_fixture",
            },
            {
                "etf_id": "etf_robotics",
                "etf_name": "Robotics Growth ETF",
                "brand_id": "brand_beta",
                "source_provider_id": "provider_fixture",
            },
        ],
    )
    dest_dir = tmp_path / "native_universe"
    collect_universe_fixture = _collect_universe_fixture()
    collect_universe_fixture(
        fixture_path=initial_fixture_path,
        dest_dir=dest_dir,
        now=lambda: datetime(2026, 5, 11, 1, 0, tzinfo=UTC),
    )
    collect_universe_fixture(
        fixture_path=removed_fixture_path,
        dest_dir=dest_dir,
        now=lambda: datetime(2026, 5, 12, 1, 0, tzinfo=UTC),
    )

    summary = collect_universe_fixture(
        fixture_path=reactivated_fixture_path,
        dest_dir=dest_dir,
        now=lambda: datetime(2026, 5, 13, 1, 0, tzinfo=UTC),
    )
    state = json.loads((dest_dir / "universe_state.json").read_text(encoding="utf-8"))

    assert summary["etf_change_counts"] == {
        "added": 0,
        "changed": 1,
        "removed": 0,
        "unchanged": 1,
    }
    assert summary["brand_change_counts"] == {
        "added": 0,
        "changed": 1,
        "removed": 0,
        "unchanged": 1,
    }
    assert summary["etf_changes"]["changed"] == [
        {
            "etf_id": "etf_robotics",
            "etf_name": "Robotics Growth ETF",
            "brand_id": "brand_beta",
            "source_provider_id": "provider_fixture",
            "changed_fields": ["status"],
        }
    ]
    assert summary["brand_changes"]["changed"] == [
        {
            "brand_id": "brand_beta",
            "brand_name": "Beta Asset Management",
            "source_provider_id": "provider_fixture",
            "changed_fields": ["status"],
        }
    ]
    assert state["etfs"][1]["status"] == "active"
    assert state["brands"][1]["status"] == "active"


def test_collect_universe_fixture_rejects_invalid_input_without_updating_state(
    tmp_path: Path,
) -> None:
    fixture_path = _write_universe_fixture(tmp_path)
    dest_dir = tmp_path / "native_universe"
    module = importlib.import_module(
        "agent_treport.signal_report.adapters.operational_universe"
    )
    module.collect_universe_fixture(
        fixture_path=fixture_path,
        dest_dir=dest_dir,
        now=lambda: datetime(2026, 5, 11, 1, 0, tzinfo=UTC),
    )
    state_path = dest_dir / "universe_state.json"
    before_state = state_path.read_bytes()
    invalid_fixture_path = tmp_path / "invalid_universe_fixture.json"
    invalid_fixture_path.write_text(
        json.dumps(
            {
                "schema_version": "agent_treport.native_universe.fixture.v1",
                "complete": False,
                "brands": _universe_brands(),
                "etfs": _universe_etfs(),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    unknown_brand_fixture_path = _write_universe_fixture(
        tmp_path,
        filename="unknown_brand_universe_fixture.json",
        etfs=[
            {
                "etf_id": "etf_focus_ai",
                "etf_name": "AI Native Collection ETF",
                "brand_id": "brand_missing",
                "source_provider_id": "provider_fixture",
            }
        ],
    )
    path_unsafe_fixture_path = _write_universe_fixture(
        tmp_path,
        filename="path_unsafe_universe_fixture.json",
        brands=[
            {
                "brand_id": "brand_alpha",
                "brand_name": "https://provider.example/brand",
                "source_provider_id": "provider_fixture",
            }
        ],
    )

    for unsafe_fixture_path in (
        invalid_fixture_path,
        unknown_brand_fixture_path,
        path_unsafe_fixture_path,
    ):
        with pytest.raises(module.OperationalUniverseInputError):
            module.collect_universe_fixture(
                fixture_path=unsafe_fixture_path,
                dest_dir=dest_dir,
                now=lambda: datetime(2026, 5, 12, 1, 0, tzinfo=UTC),
            )

    assert state_path.read_bytes() == before_state


def test_collect_universe_fixture_summary_write_failure_leaves_state_unchanged(
    tmp_path: Path,
) -> None:
    fixture_path = _write_universe_fixture(tmp_path)
    dest_dir = tmp_path / "native_universe"
    collect_universe_fixture = _collect_universe_fixture()
    collect_universe_fixture(
        fixture_path=fixture_path,
        dest_dir=dest_dir,
        now=lambda: datetime(2026, 5, 11, 1, 0, tzinfo=UTC),
    )
    state_path = dest_dir / "universe_state.json"
    before_state = state_path.read_bytes()
    changed_fixture_path = _write_universe_fixture(
        tmp_path,
        filename="changed_universe_fixture.json",
        brands=[
            {
                "brand_id": "brand_alpha",
                "brand_name": "Alpha Asset Management",
                "source_provider_id": "provider_fixture_v2",
            }
        ],
    )
    summary_path = dest_dir / "universe_summary.json"
    summary_path.unlink()
    summary_path.mkdir()

    with pytest.raises(OSError):
        collect_universe_fixture(
            fixture_path=changed_fixture_path,
            dest_dir=dest_dir,
            now=lambda: datetime(2026, 5, 12, 1, 0, tzinfo=UTC),
        )

    assert state_path.read_bytes() == before_state
