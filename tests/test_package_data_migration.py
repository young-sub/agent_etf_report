from __future__ import annotations

import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_built_artifacts_include_migrated_domain_resources(tmp_path: Path) -> None:
    output_dir = tmp_path / "dist"

    subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--no-isolation",
            "--wheel",
            "--sdist",
            "--outdir",
            str(output_dir),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )

    wheel_path = next(output_dir.glob("agent_treport-*.whl"))
    sdist_path = next(output_dir.glob("agent_treport-*.tar.gz"))

    with zipfile.ZipFile(wheel_path) as wheel:
        wheel_names = set(wheel.namelist())

    assert "agent_treport/fixtures/signal_report/holdings.json" in wheel_names
    assert "agent_treport/fixtures/signal_report/evidence.json" in wheel_names
    assert "agent_treport/docs/README.md" in wheel_names
    assert "agent_treport/py.typed" in wheel_names

    with tarfile.open(sdist_path, "r:gz") as sdist:
        sdist_names = set(sdist.getnames())

    assert any(
        name.endswith("/data/agent_treport/focus-etf-sets/default_focus_etf_set.json")
        for name in sdist_names
    )
    assert any(
        name.endswith("/src/agent_treport/docs/README.md") for name in sdist_names
    )
