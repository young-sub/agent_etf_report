from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from agent_pack.models import JsonValue

from agent_treport.signal_report.adapters.errors import SignalReportInputError
from agent_treport.signal_report.adapters.source_acquisition import (
    LIVE_SOURCE_PROVIDER_IDS,
)
from agent_treport.signal_report.domain.focus_etf_set import (
    FocusETFSetInputError,
    load_focus_etf_set_file,
)

OPERATIONAL_SOURCE_CACHE_LAYOUT_SCHEMA_VERSION = (
    "agent_treport.source_provider_operational_cache.layout.v1"
)
DEFAULT_OPERATIONAL_SOURCE_CACHE_ROOT = Path(
    "data/agent_treport/live-source/source-provider-operational"
)
EXPECTED_OPERATIONAL_CACHE_ARTIFACTS = (
    "catalog/source_catalog.json",
    "catalog/universe_state.json",
    "catalog/source_acquisition_summary.json",
    "focus_etf_set.json",
    "holdings-history/",
    "security-master/",
)

_PROVIDER_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class OperationalSourceCacheInputError(SignalReportInputError):
    """Raised when provider-scoped operational cache layout input is unsafe."""


@dataclass(frozen=True)
class OperationalCacheArtifactSpec:
    artifact: str
    kind: str

    @property
    def path_value(self) -> str:
        return self.artifact.rstrip("/")


@dataclass(frozen=True)
class ProviderOperationalCacheLayout:
    source_provider_id: str
    cache_root: Path


_EXPECTED_ARTIFACT_SPECS = tuple(
    OperationalCacheArtifactSpec(
        artifact=artifact,
        kind="directory" if artifact.endswith("/") else "file",
    )
    for artifact in EXPECTED_OPERATIONAL_CACHE_ARTIFACTS
)


def registered_provider_operational_cache_layouts(
    *,
    cache_root: str | Path = DEFAULT_OPERATIONAL_SOURCE_CACHE_ROOT,
    provider_ids: Iterable[str] = LIVE_SOURCE_PROVIDER_IDS,
) -> tuple[ProviderOperationalCacheLayout, ...]:
    root = Path(cache_root)
    return tuple(
        ProviderOperationalCacheLayout(
            source_provider_id=_path_safe_provider_id(provider_id),
            cache_root=provider_operational_cache_path(
                cache_root=root,
                source_provider_id=provider_id,
            ),
        )
        for provider_id in provider_ids
    )


def inspect_provider_operational_cache_layouts(
    *,
    cache_root: str | Path = DEFAULT_OPERATIONAL_SOURCE_CACHE_ROOT,
    provider_ids: Iterable[str] = LIVE_SOURCE_PROVIDER_IDS,
) -> dict[str, JsonValue]:
    root = Path(cache_root)
    layouts = registered_provider_operational_cache_layouts(
        cache_root=root,
        provider_ids=provider_ids,
    )
    providers: list[dict[str, JsonValue]] = []
    missing_artifacts: list[dict[str, JsonValue]] = []
    invalid_artifacts: list[dict[str, JsonValue]] = []

    for layout in layouts:
        provider_item, provider_missing, provider_invalid = _provider_layout_item(
            root=root,
            layout=layout,
        )
        providers.append(provider_item)
        missing_artifacts.extend(provider_missing)
        invalid_artifacts.extend(provider_invalid)

    if invalid_artifacts:
        status = "invalid_artifacts"
    elif missing_artifacts:
        status = "missing_artifacts"
    else:
        status = "ready"

    return {
        "schema_version": OPERATIONAL_SOURCE_CACHE_LAYOUT_SCHEMA_VERSION,
        "status": status,
        "registered_provider_ids": [layout.source_provider_id for layout in layouts],
        "provider_count": len(layouts),
        "expected_artifacts": list(EXPECTED_OPERATIONAL_CACHE_ARTIFACTS),
        "providers": providers,
        "missing_artifact_count": len(missing_artifacts),
        "missing_artifacts": missing_artifacts,
        "invalid_artifact_count": len(invalid_artifacts),
        "invalid_artifacts": invalid_artifacts,
    }


def provider_operational_cache_path(
    *,
    cache_root: str | Path,
    source_provider_id: str,
) -> Path:
    provider_id = _path_safe_provider_id(source_provider_id)
    return _safe_child_path(
        base=Path(cache_root),
        relative_path=provider_id,
        label="source_provider_id",
    )


def provider_operational_cache_reference_path(
    *,
    provider_cache_root: str | Path,
    relative_path: str | Path,
    label: str,
) -> Path:
    return _safe_child_path(
        base=Path(provider_cache_root),
        relative_path=str(relative_path),
        label=label,
    )


def _provider_layout_item(
    *,
    root: Path,
    layout: ProviderOperationalCacheLayout,
) -> tuple[
    dict[str, JsonValue],
    list[dict[str, JsonValue]],
    list[dict[str, JsonValue]],
]:
    artifacts: list[dict[str, JsonValue]] = []
    missing_artifacts: list[dict[str, JsonValue]] = []
    invalid_artifacts: list[dict[str, JsonValue]] = []

    for spec in _EXPECTED_ARTIFACT_SPECS:
        path = provider_operational_cache_reference_path(
            provider_cache_root=layout.cache_root,
            relative_path=spec.path_value,
            label=spec.artifact,
        )
        exists = path.is_dir() if spec.kind == "directory" else path.is_file()
        status = "present" if exists else "missing"
        item: dict[str, JsonValue] = {
            "artifact": spec.artifact,
            "path": _display_path(path, root=root),
            "kind": spec.kind,
            "status": status,
        }
        artifacts.append(item)
        if not exists:
            missing_artifacts.append(
                {
                    "source_provider_id": layout.source_provider_id,
                    "artifact": spec.artifact,
                    "path": item["path"],
                    "kind": spec.kind,
                }
            )
            continue
        if spec.artifact == "focus_etf_set.json":
            try:
                focus_set = load_focus_etf_set_file(path)
                for focus_etf_id in focus_set.focus_etf_ids:
                    _ensure_cache_relative_identifier(
                        focus_etf_id,
                        label="focus_etf_id",
                    )
            except FocusETFSetInputError as exc:
                invalid_artifacts.append(
                    {
                        "source_provider_id": layout.source_provider_id,
                        "artifact": spec.artifact,
                        "path": item["path"],
                        "message": str(exc),
                    }
                )

    if invalid_artifacts:
        provider_status = "invalid_artifacts"
    elif missing_artifacts:
        provider_status = "missing_artifacts"
    else:
        provider_status = "ready"

    return (
        {
            "source_provider_id": layout.source_provider_id,
            "cache_root": _display_path(layout.cache_root, root=root) + "/",
            "status": provider_status,
            "artifacts": artifacts,
        },
        missing_artifacts,
        invalid_artifacts,
    )


def _path_safe_provider_id(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise OperationalSourceCacheInputError("source provider id must be text")
    if not _PROVIDER_ID_PATTERN.fullmatch(value):
        raise OperationalSourceCacheInputError(
            f"source provider id is not path-safe: {value}"
        )
    return value


def _ensure_cache_relative_identifier(value: str, *, label: str) -> None:
    path = Path(value)
    if path.is_absolute() or path.drive:
        raise FocusETFSetInputError(f"{label} is not path-safe")
    if len(path.parts) != 1 or any(part in {"", ".", ".."} for part in path.parts):
        raise FocusETFSetInputError(f"{label} is not path-safe")


def _safe_child_path(*, base: Path, relative_path: str, label: str) -> Path:
    relative = Path(relative_path)
    if (
        relative.is_absolute()
        or relative.drive
        or relative_path.startswith(("/", "\\"))
    ):
        raise OperationalSourceCacheInputError(f"{label} must be cache-root-relative")
    if any(part in {"", ".", ".."} for part in relative.parts):
        raise OperationalSourceCacheInputError(f"{label} is not path-safe")
    base_resolved = base.resolve()
    resolved = (base_resolved / relative).resolve()
    try:
        resolved.relative_to(base_resolved)
    except ValueError as exc:
        raise OperationalSourceCacheInputError(
            f"{label} must stay inside provider cache root"
        ) from exc
    return resolved


def _display_path(path: Path, *, root: Path) -> str:
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError:
        relative = path
    return relative.as_posix()
