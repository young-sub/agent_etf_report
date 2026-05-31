from __future__ import annotations

import ast
import importlib.metadata
import importlib.resources
import subprocess
import sys
import tomllib
from pathlib import Path

from agent_treport import cli as treport_cli

REPO_ROOT = Path(__file__).resolve().parents[1]


def load_pyproject() -> dict[str, object]:
    with (REPO_ROOT / "pyproject.toml").open("rb") as pyproject:
        return tomllib.load(pyproject)


def test_distribution_import_and_cli_names_stay_agent_treport() -> None:
    pyproject = load_pyproject()

    assert pyproject["project"]["name"] == "agent-treport"  # type: ignore[index]
    assert pyproject["project"]["requires-python"] >= ">=3.11"  # type: ignore[index]
    assert pyproject["project"]["scripts"]["agent-treport"] == "agent_treport.cli:main"  # type: ignore[index]
    assert "agent-etf-report" not in pyproject["project"].get("scripts", {})  # type: ignore[index]

    distribution = importlib.metadata.distribution("agent-treport")
    assert distribution.metadata["Name"] == "agent-treport"


def test_agent_etf_report_runtime_package_is_not_introduced() -> None:
    pyproject = load_pyproject()
    hatch_build = pyproject["tool"]["hatch"]["build"]["targets"]  # type: ignore[index]

    assert not (REPO_ROOT / "src" / "agent_etf_report").exists()
    assert "src/agent_etf_report" not in hatch_build["wheel"]["packages"]
    assert "src/agent_etf_report" not in hatch_build["sdist"]["only-include"]


def test_package_import_and_module_cli_smoke() -> None:
    import agent_treport

    imported_from = Path(agent_treport.__file__).resolve()
    assert imported_from.is_relative_to(REPO_ROOT / "src" / "agent_treport")
    assert agent_treport.PACKAGE_NAME == "agent_treport"
    assert agent_treport.CLI_NAME == "agent-treport"
    assert agent_treport.DATA_ROOT == "data/agent_treport"
    assert agent_treport.SCHEMA_NAMESPACE == "agent_treport"
    assert agent_treport.EVENT_NAMESPACE == "agent_treport"

    result = subprocess.run(
        [sys.executable, "-m", "agent_treport", "--version"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )

    assert result.stdout.strip().startswith("agent-treport ")


def test_path_and_cli_compatibility_policy_is_explicit() -> None:
    import agent_treport

    assert agent_treport.PATH_COMPATIBILITY_POLICY == {
        "default_data_root": "data/agent_treport",
        "default_paths": {
            "operational_holdings": (
                "data/agent_treport/operational-holdings/"
                "url_holdings_cumulative.json"
            ),
            "native_history": "data/agent_treport/live-source/holdings-history",
            "focus_etf_set": (
                "data/agent_treport/focus-etf-sets/default_focus_etf_set.json"
            ),
            "security_resolution": (
                "data/agent_treport/security-master/security_resolution.json"
            ),
        },
        "cli_name": "agent-treport",
        "schema_namespace": "agent_treport",
        "event_namespace": "agent_treport",
        "rename_policy": "defer_agent_etf_report_rename_until_post_separation",
    }


def test_cli_default_paths_follow_compatibility_policy() -> None:
    import agent_treport

    policy = agent_treport.PATH_COMPATIBILITY_POLICY
    defaults = policy["default_paths"]

    assert treport_cli.DEFAULT_OPERATIONAL_HOLDINGS_PATH == defaults[
        "operational_holdings"
    ]
    assert treport_cli.DEFAULT_NATIVE_HANDOFF_HISTORY_DIR == defaults[
        "native_history"
    ]
    assert treport_cli.DEFAULT_FOCUS_ETF_SET_PATH == defaults["focus_etf_set"]
    assert treport_cli.DEFAULT_SECURITY_RESOLUTION_PATH == defaults[
        "security_resolution"
    ]

    parser = treport_cli.build_parser()
    native_handoff = parser.parse_args(
        [
            "run-native-operational-handoff",
            "--universe-state-path",
            "universe_state.json",
            "--dest",
            "handoff",
            "--model",
            "codex",
        ]
    )
    pre_publish = parser.parse_args(
        [
            "run-pre-publish-preview",
            "--universe-state-path",
            "universe_state.json",
            "--dest",
            "handoff",
            "--model",
            "codex",
        ]
    )
    run_report = parser.parse_args(
        [
            "run-report",
            "--sqlite-path",
            "runtime.sqlite3",
            "--artifact-root",
            "artifacts",
            "--model",
            "codex",
        ]
    )

    assert run_report.holdings_path is None
    assert native_handoff.history_dir == defaults["native_history"]
    assert native_handoff.focus_etf_set_path == defaults["focus_etf_set"]
    assert pre_publish.history_dir == defaults["native_history"]
    assert pre_publish.focus_etf_set_path == defaults["focus_etf_set"]


def test_compatibility_policy_paths_are_relative_and_safe() -> None:
    import agent_treport

    policy = agent_treport.PATH_COMPATIBILITY_POLICY
    rendered = repr(policy)
    assert "credential" not in rendered.lower()
    assert "secret" not in rendered.lower()

    extraction_surface = (
        policy["default_data_root"],
        policy["cli_name"],
        policy["schema_namespace"],
        policy["event_namespace"],
        *policy["default_paths"].values(),
    )
    for value in extraction_surface:
        assert "agent_etf_report" not in value
        assert "agent-etf-report" not in value

    for value in policy["default_paths"].values():
        path = Path(value)
        assert not path.is_absolute()
        assert path.parts[:2] == ("data", "agent_treport")


def test_dependency_boundary_excludes_direct_doc_parser_dependency() -> None:
    pyproject = load_pyproject()

    dependencies = set(pyproject["project"]["dependencies"])  # type: ignore[index]
    normalized_dependencies = {dependency.split(" ", 1)[0].lower() for dependency in dependencies}
    assert {"agent-pack", "agent-pack-docs"} <= normalized_dependencies
    assert "doc-parser" not in normalized_dependencies
    assert "doc_parser" not in normalized_dependencies


def test_agent_treport_source_does_not_import_doc_parser() -> None:
    source_root = REPO_ROOT / "src" / "agent_treport"

    source_files = list(source_root.rglob("*.py"))
    assert source_files, "expected repo-local src/agent_treport Python files"

    for path in source_files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported = {alias.name.split(".", 1)[0] for alias in node.names}
                assert "doc_parser" not in imported, path
            elif isinstance(node, ast.ImportFrom):
                imported_from = (node.module or "").split(".", 1)[0]
                assert imported_from != "doc_parser", path


def test_typed_package_marker_is_included() -> None:
    marker = importlib.resources.files("agent_treport").joinpath("py.typed")
    assert marker.is_file()


def test_package_data_policy_marker_is_included() -> None:
    marker = importlib.resources.files("agent_treport").joinpath(
        "resources",
        "README.md",
    )

    assert marker.is_file()
    assert "Package data policy marker" in marker.read_text(encoding="utf-8")
