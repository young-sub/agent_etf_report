from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_agent_treport_cli_collects_fixture_backed_holdings(tmp_path: Path) -> None:
    fixture_path = tmp_path / "native_fixture.json"
    fixture_path.write_text(
        json.dumps(
            {
                "schema_version": "agent_treport.native_holdings.fixture.v1",
                "brands": [
                    {
                        "brand_id": "brand_alpha",
                        "name": "Alpha Asset Management",
                    }
                ],
                "etf_universe": [
                    {
                        "etf_id": "etf_focus_ai",
                        "etf_name": "AI Native Collection ETF",
                        "brand_id": "brand_alpha",
                        "source_provider_id": "provider_native_fixture",
                    }
                ],
                "snapshots": [
                    {
                        "as_of_date": "2026-05-11",
                        "holdings": [
                            {
                                "etf_id": "etf_focus_ai",
                                "security_id": "sec_nvda",
                                "ticker": "NVDA",
                                "name": "NVIDIA Corp.",
                                "market": "US",
                                "sector": "Information Technology",
                                "theme": "AI infrastructure",
                                "country": "US",
                                "weight_percent": 7.5,
                                "shares": 1500,
                                "market_value_krw": 240000000,
                                "price_krw": 160000,
                                "is_cash": False,
                                "security_classification": "ticker_candidate",
                            }
                        ],
                    },
                    {
                        "as_of_date": "2026-05-08",
                        "holdings": [
                            {
                                "etf_id": "etf_focus_ai",
                                "security_id": "sec_nvda",
                                "ticker": "NVDA",
                                "name": "NVIDIA Corp.",
                                "market": "US",
                                "sector": "Information Technology",
                                "theme": "AI infrastructure",
                                "country": "US",
                                "weight_percent": 6.0,
                                "shares": 1200,
                                "market_value_krw": 180000000,
                                "price_krw": 150000,
                                "is_cash": False,
                                "security_classification": "ticker_candidate",
                            }
                        ],
                    },
                ],
                "quality_warnings": [],
                "limitations": [
                    {
                        "code": "fixture_backed_collection",
                        "message": "Native collection used fixture holdings only.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    dest_dir = tmp_path / "native_collected"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent_treport",
            "collect-holdings-fixture",
            "--fixture-path",
            str(fixture_path),
            "--dest",
            str(dest_dir),
            "--observed-partitions",
            "2",
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert result.stdout.count("\n") == 1

    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "agent_treport.native_collection.summary.v1"
    assert payload["collection_source_type"] == "fixture"
    assert payload["observed_dates"] == ["2026-05-11", "2026-05-08"]
    assert payload["normalized_output"]["manifest_path"] == (
        "url_holdings_cumulative.json"
    )
    assert (dest_dir / "url_holdings_cumulative.json").is_file()
    assert (dest_dir / "collection_summary.json").is_file()
