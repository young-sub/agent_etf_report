from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_treport.signal_report.domain.focus_etf_set import (
    FOCUS_ETF_SET_SCHEMA_VERSION,
    FocusETFSetInputError,
    load_focus_etf_set_file,
)


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_focus_etf_set_file_loads_path_safe_ids_and_optional_metadata(
    tmp_path: Path,
) -> None:
    focus_set_path = tmp_path / "focus_set.json"
    _write_json(
        focus_set_path,
        {
            "schema_version": FOCUS_ETF_SET_SCHEMA_VERSION,
            "focus_etf_ids": ["etf_alpha", "etf_beta", "etf_gamma"],
            "label": "Equity active focus",
            "notes": "Default operational handoff set.",
        },
    )

    focus_set = load_focus_etf_set_file(focus_set_path)

    assert focus_set.schema_version == FOCUS_ETF_SET_SCHEMA_VERSION
    assert focus_set.focus_etf_ids == ("etf_alpha", "etf_beta", "etf_gamma")
    assert focus_set.label == "Equity active focus"
    assert focus_set.notes == "Default operational handoff set."


def test_default_focus_etf_set_file_loads_expected_live_handoff_ids() -> None:
    focus_set_path = (
        Path(__file__).parents[1]
        / "data"
        / "agent_treport"
        / "focus-etf-sets"
        / "default_focus_etf_set.json"
    )

    focus_set = load_focus_etf_set_file(focus_set_path)

    assert len(focus_set.focus_etf_ids) == 13
    assert focus_set.focus_etf_ids[:3] == (
        "etf_ace_k55101ep9626",
        "etf_ace_k55101ep9634",
        "etf_rise_44k0",
    )
    rendered = focus_set_path.read_text(encoding="utf-8")
    assert "provider_etf_id" not in rendered
    assert "://" not in rendered


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ("not-json", "invalid JSON input"),
        ([], "must be a JSON object"),
        (
            {"schema_version": "wrong", "focus_etf_ids": ["etf_alpha"]},
            "invalid focus ETF set schema",
        ),
        (
            {
                "schema_version": FOCUS_ETF_SET_SCHEMA_VERSION,
                "focus_etf_ids": "etf_alpha",
            },
            "focus_etf_ids must be a non-empty list",
        ),
        (
            {"schema_version": FOCUS_ETF_SET_SCHEMA_VERSION, "focus_etf_ids": []},
            "focus_etf_ids must be a non-empty list",
        ),
        (
            {
                "schema_version": FOCUS_ETF_SET_SCHEMA_VERSION,
                "focus_etf_ids": ["etf_alpha", "etf_alpha"],
            },
            "duplicate focus_etf_id",
        ),
        (
            {
                "schema_version": FOCUS_ETF_SET_SCHEMA_VERSION,
                "focus_etf_ids": ["https://provider.example/etf"],
            },
            "focus_etf_id is not path-safe",
        ),
        (
            {
                "schema_version": FOCUS_ETF_SET_SCHEMA_VERSION,
                "focus_etf_ids": ["etf_alpha"],
                "provider_etf_id": "2ETF35",
            },
            "unsupported focus ETF set field",
        ),
    ],
)
def test_focus_etf_set_file_rejects_malformed_or_unsafe_payloads(
    tmp_path: Path,
    payload: object,
    message: str,
) -> None:
    focus_set_path = tmp_path / "focus_set.json"
    if isinstance(payload, str):
        focus_set_path.write_text(payload, encoding="utf-8")
    else:
        _write_json(focus_set_path, payload)

    with pytest.raises(FocusETFSetInputError, match=message):
        load_focus_etf_set_file(focus_set_path)
