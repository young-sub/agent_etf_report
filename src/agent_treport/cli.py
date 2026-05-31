from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import os
import shlex
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import TextIO
from urllib.parse import unquote, urlparse

from agent_pack.artifacts import LocalArtifactManager
from agent_pack.context import ContextManager
from agent_pack.failure_evidence import exception_error
from agent_pack.inspection import RunInspectionNotFoundError, RunInspectionService
from agent_pack.models import (
    ArtifactRef,
    JsonValue,
    Message,
    ModelRequest,
    Run,
    RunEvent,
    RunResult,
    RunSnapshot,
    TextBlock,
)
from agent_pack.models_client import ModelClient, ModelProviderConfig, create_model_client
from agent_pack.store import SQLiteRunStore

from agent_treport import CLI_NAME, __version__
from agent_treport.signal_report.adapters.errors import SignalReportInputError
from agent_treport.signal_report.adapters.live_source_replacement import (
    LiveBaselineProviderInput,
    apply_live_source_rolling_retention,
    run_live_baseline_backfill,
)
from agent_treport.signal_report.adapters.openfigi import (
    OpenFigiClient,
    create_openfigi_client_from_env,
    lookup_openfigi_tickers,
)
from agent_treport.signal_report.adapters.operational_holdings import (
    COLLECTION_SUMMARY_SCHEMA_VERSION,
    OPERATIONAL_EXPORT_FINGERPRINT_SCOPE,
    SYNC_METADATA_SCHEMA_VERSION,
    OperationalHoldingsInputError,
    OperationalSignalReportInputProvider,
    collect_holdings_fixture,
    compute_operational_export_fingerprint,
    export_latest_holdings_comparison,
    import_operational_holdings_export_to_history,
    load_security_mapping,
    merge_security_mapping_patch,
    sync_operational_holdings,
    update_holdings_history_fixture,
)
from agent_treport.signal_report.adapters.operational_readiness import (
    DEFAULT_OPERATOR_TIMEZONE,
    READINESS_SCHEMA_VERSION,
    OperationalReadinessInputError,
    check_operational_run_readiness,
)
from agent_treport.signal_report.adapters.operational_universe import (
    OperationalUniverseInputError,
    collect_universe_fixture,
)
from agent_treport.signal_report.adapters.source_acquisition import (
    LIVE_SOURCE_PROVIDER_IDS,
    SOURCE_ACQUISITION_SUMMARY_FILENAME,
    SOURCE_ACQUISITION_SUMMARY_SCHEMA_VERSION,
    FakeSourceProvider,
    SourceAcquisitionInputError,
    collect_source_catalog,
    create_live_source_provider,
    update_holdings_history_source,
)
from agent_treport.signal_report.approval import (
    DEFAULT_APPROVAL_PROFILE_PATH,
    REPORT_MODEL_EXPORT_SCOPE,
    build_daily_approval_boundary,
    default_preflight_path,
    evaluate_approval_profile,
    write_preflight_and_template,
)
from agent_treport.signal_report.approval_trace import (
    daily_external_data_approval_trace_state,
    daily_external_data_governance_records,
)
from agent_treport.signal_report.daily_publish_closure import (
    DailyPublishClosureInputError,
    verify_daily_publish_closure,
)
from agent_treport.signal_report.domain.evidence import EvidenceItemInput
from agent_treport.signal_report.domain.focus_etf_set import (
    FocusETFSetInputError,
    load_focus_etf_set_file,
)
from agent_treport.signal_report.domain.security_resolution import (
    SecurityResolutionInputError,
    build_security_resolution_export,
    has_structural_ticker_resolution,
    import_security_master_seed_rows,
    resolve_security_master_observations,
    validate_security_classification,
)
from agent_treport.signal_report.external_delivery_review import (
    build_external_delivery_review_summary,
)
from agent_treport.signal_report.external_evidence import (
    EXTERNAL_EVIDENCE_PROVIDER_IDS,
    CodexAlignmentClassifier,
    ExternalEvidenceCollectionError,
    ExternalEvidenceRequest,
    collect_external_evidence,
)
from agent_treport.signal_report.external_evidence.contracts import (
    ExternalEvidenceProvider,
)
from agent_treport.signal_report.external_evidence.providers import is_live_provider
from agent_treport.signal_report.pipeline.build_payload import build_signal_report_payload
from agent_treport.signal_report.telegram_delivery import (
    TelegramClientFactory,
    TelegramDeliveryInputError,
    run_telegram_delivery,
)
from agent_treport.workflows.signal_report import (
    CachedSignalReportInputProvider,
    FixtureSignalReportInputProvider,
    SignalReportInputProvider,
    run_signal_report,
)

DEFAULT_OPERATIONAL_HOLDINGS_PATH = (
    "data/agent_treport/operational-holdings/url_holdings_cumulative.json"
)
DEFAULT_NATIVE_HANDOFF_HISTORY_DIR = (
    "data/agent_treport/live-source/holdings-history"
)
DEFAULT_FOCUS_ETF_SET_PATH = (
    "data/agent_treport/focus-etf-sets/default_focus_etf_set.json"
)
DEFAULT_SECURITY_RESOLUTION_PATH = (
    "data/agent_treport/security-master/security_resolution.json"
)
NATIVE_OPERATIONAL_HANDOFF_SCHEMA_VERSION = (
    "agent_treport.native_operational_handoff.v1"
)
PROVIDER_ETF_EXCLUSION_SUMMARY_SCHEMA_VERSION = (
    "agent_treport.provider_etf_exclusion_summary.v1"
)
EXTERNAL_EVIDENCE_SUMMARY_SCHEMA_VERSION = (
    "agent_treport.external_evidence.summary.v1"
)
REGISTERED_COHORT_STATEMENT = (
    "This report evaluated the registered live provider cohort, disclosed "
    "excluded providers/ETFs with reasons, and judged user-ready status "
    "using the remaining eligible cohort."
)
DEFAULT_PRE_PUBLISH_EVIDENCE_PROVIDERS = (
    "finnhub",
    "yfinance",
    "dart",
    "alpha_vantage",
    "newsapi",
    "naver",
)
DEFAULT_PRE_PUBLISH_MAX_EVIDENCE_TARGETS = 25
PRE_PUBLISH_REQUIRED_EVIDENCE_CATEGORIES = {"financial", "disclosure", "news"}
PRE_PUBLISH_PROVIDER_SUCCESS_STATUSES = {"success", "no_data"}
PRE_PUBLISH_PROVIDER_FAILURE_STATUSES = {
    "credential_required",
    "blocked",
    "rate_limited_exhausted",
    "provider_unavailable",
    "invalid_provider_payload",
    "timeout_exhausted",
}
PRE_PUBLISH_KNOWN_UNVALIDATED_PROVIDER_EXCEPTIONS: tuple[dict[str, JsonValue], ...] = (
    {
        "provider_id": "sec_edgar",
        "category": "disclosure",
        "exception_type": "known_unvalidated_provider_exception",
        "required_for_user_ready_closure": False,
        "execution_status": "not_requested",
        "safe_reason": (
            "SEC EDGAR remains disclosed but outside the validated provider "
            "denominator for this pre-publish closure."
        ),
        "non_call_rule": "SEC EDGAR must not be called for this goal.",
        "promotion_condition": (
            "Promote only after a separate SEC-policy-compliant live smoke returns "
            "success or normal no_data with path-safe evidence and no active cooldown."
        ),
    },
)
DEFAULT_DAILY_SMOKE_SUMMARY_ROOT = (
    "data/agent_treport/live-source/daily-smoke-summaries"
)
RECOVERY_PROPOSAL_SCHEMA_VERSION = (
    "agent_treport.security_mapping.recovery_proposal.v1"
)
PATCH_APPLY_RESULT_SCHEMA_VERSION = (
    "agent_treport.security_mapping.patch_apply_result.v1"
)
RECOVERY_PROPOSAL_RESULT_SCHEMA_VERSION = (
    "agent_treport.security_mapping.recovery_proposal_result.v1"
)
_RECOVERY_SAMPLE_FIELDS = {
    "security_id",
    "name",
    "observed_row_count",
    "observed_etf_count",
    "observed_date_count",
    "name_alias_count",
}
_NATIVE_RECOVERY_SAMPLE_FIELDS = _RECOVERY_SAMPLE_FIELDS | {
    "security_classification"
}
_RECOVERY_PROPOSAL_FIELDS = {
    "security_id",
    "name",
    "proposed_ticker",
    "status",
    "confidence",
    "rationale",
}

type ModelClientFactory = Callable[[ModelProviderConfig], ModelClient]
type OpenFigiClientFactory = Callable[[], OpenFigiClient]
type ExternalEvidenceProviderOverrides = Mapping[str, ExternalEvidenceProvider]


class SecurityMappingRecoveryProposalError(RuntimeError):
    """Raised when untrusted model proposal output violates the contract."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=CLI_NAME)
    parser.add_argument(
        "--version",
        action="version",
        version=f"{CLI_NAME} {__version__}",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    run_report = subcommands.add_parser("run-report")
    run_report.add_argument("--run-id", default="run_treport")
    run_report.add_argument("--sqlite-path", required=True)
    run_report.add_argument("--artifact-root", required=True)
    run_report.add_argument("--model", choices=("codex",), required=True)
    run_report.add_argument("--codex-model")
    run_report.add_argument("--model-timeout-seconds", type=float, default=300)
    run_report.add_argument(
        "--holdings-source",
        choices=("fixture", "operational"),
        default="fixture",
    )
    run_report.add_argument("--holdings-path")
    run_report.add_argument("--evidence-path")
    run_report.add_argument("--evidence-summary-path")
    run_report.add_argument("--focus-etf-id")
    run_report.add_argument("--focus-etf-set-path")
    run_report.add_argument("--observed-partitions", type=int, default=30)
    run_report.add_argument("--readiness-path")
    run_report.add_argument("--allow-operator-review-output", action="store_true")
    run_report.add_argument(
        "--approval-path",
        default=DEFAULT_APPROVAL_PROFILE_PATH,
    )
    run_report.add_argument("--write-preflight", nargs="?", const="")

    native_handoff = subcommands.add_parser("run-native-operational-handoff")
    native_handoff.add_argument("--run-id", default="run_native_operational_handoff")
    native_handoff.add_argument(
        "--history-dir",
        default=DEFAULT_NATIVE_HANDOFF_HISTORY_DIR,
    )
    native_handoff.add_argument("--universe-state-path", required=True)
    native_handoff.add_argument("--dest", required=True)
    native_handoff.add_argument("--export-dir")
    native_handoff.add_argument("--resume-export-path")
    native_handoff.add_argument("--focus-etf-id")
    native_handoff.add_argument("--focus-etf-set-path", default=DEFAULT_FOCUS_ETF_SET_PATH)
    native_handoff.add_argument("--observed-partitions", type=int, default=30)
    native_handoff.add_argument("--security-resolution-path")
    native_handoff.add_argument("--use-default-security-resolution", action="store_true")
    native_handoff.add_argument("--evidence-path")
    native_handoff.add_argument("--evidence-summary-path")
    native_handoff.add_argument("--sqlite-path")
    native_handoff.add_argument("--artifact-root")
    native_handoff.add_argument("--output-path")
    native_handoff.add_argument("--model", choices=("codex",), required=True)
    native_handoff.add_argument("--codex-model")
    native_handoff.add_argument("--model-timeout-seconds", type=float, default=300)
    native_handoff.add_argument("--allow-operator-review-output", action="store_true")
    native_handoff.add_argument(
        "--approval-path",
        default=DEFAULT_APPROVAL_PROFILE_PATH,
    )
    native_handoff.add_argument("--write-preflight", nargs="?", const="")
    native_handoff.add_argument(
        "--require-verified-operational-flow-acceptance",
        action="store_true",
    )

    pre_publish = subcommands.add_parser("run-pre-publish-preview")
    pre_publish.add_argument("--run-id", default="run_pre_publish_preview")
    pre_publish.add_argument(
        "--history-dir",
        default=DEFAULT_NATIVE_HANDOFF_HISTORY_DIR,
    )
    pre_publish.add_argument("--universe-state-path", required=True)
    pre_publish.add_argument("--dest", required=True)
    pre_publish.add_argument("--export-dir")
    pre_publish.add_argument("--focus-etf-id")
    pre_publish.add_argument("--focus-etf-set-path", default=DEFAULT_FOCUS_ETF_SET_PATH)
    pre_publish.add_argument("--observed-partitions", type=int, default=30)
    pre_publish.add_argument("--security-resolution-path")
    pre_publish.add_argument("--use-default-security-resolution", action="store_true")
    pre_publish.add_argument("--evidence-path")
    pre_publish.add_argument("--evidence-summary-path")
    pre_publish.add_argument("--cached-evidence-path")
    pre_publish.add_argument("--cached-evidence-summary-path")
    pre_publish.add_argument("--evidence-providers", action="append")
    pre_publish.add_argument(
        "--max-evidence-targets",
        type=int,
        default=DEFAULT_PRE_PUBLISH_MAX_EVIDENCE_TARGETS,
    )
    pre_publish.add_argument("--sqlite-path")
    pre_publish.add_argument("--artifact-root")
    pre_publish.add_argument("--output-path")
    pre_publish.add_argument("--model", choices=("codex",), required=True)
    pre_publish.add_argument("--codex-model")
    pre_publish.add_argument("--model-timeout-seconds", type=float, default=300)
    pre_publish.add_argument("--preview-timeout-seconds", type=float, default=600)
    pre_publish.add_argument("--allow-preview-timeout-overrun", action="store_true")
    pre_publish.add_argument("--allow-operator-review-output", action="store_true")
    pre_publish.add_argument(
        "--approval-path",
        default=DEFAULT_APPROVAL_PROFILE_PATH,
    )
    pre_publish.add_argument(
        "--write-preflight",
        nargs="?",
        const="",
        help=(
            "write the daily external data preflight disclosure; optional value "
            "overrides the default path next to the destination"
        ),
    )

    telegram_delivery = subcommands.add_parser("deliver-telegram-alert")
    telegram_delivery.add_argument("--handoff-path", required=True)
    telegram_delivery.add_argument("--target-alias", default="default")
    telegram_delivery.add_argument("--approval-path")
    telegram_delivery.add_argument("--write-preflight", nargs="?", const="")
    telegram_delivery.add_argument("--live", action="store_true")
    telegram_delivery.add_argument("--retry-failed-delivery", action="store_true")

    daily_publish_closure = subcommands.add_parser("verify-daily-publish-closure")
    daily_publish_closure.add_argument("--package-path", required=True)

    delivery_closure_review = subcommands.add_parser(
        "project-delivery-closure-review"
    )
    delivery_closure_review.add_argument("--package-path", required=True)
    delivery_closure_review.add_argument("--sqlite-path", required=True)
    delivery_closure_review.add_argument("--run-id", required=True)
    delivery_closure_review.add_argument("--subject-id", required=True)

    sync_holdings = subcommands.add_parser("sync-operational-holdings")
    sync_holdings.add_argument("--source", required=True)
    sync_holdings.add_argument(
        "--dest",
        default="data/agent_treport/operational-holdings",
    )
    sync_holdings.add_argument("--observed-partitions", type=int, default=30)
    sync_holdings.add_argument("--security-mapping-path")
    sync_holdings.add_argument("--security-resolution-path")

    collect_fixture = subcommands.add_parser(
        "collect-holdings-fixture",
        description=(
            "Fixture-only Agent TReport native holdings collection. "
            "Does not read ETF Tracker source manifests or call live providers."
        ),
        help="run fixture-only native holdings collection",
    )
    collect_fixture.add_argument("--fixture-path", required=True)
    collect_fixture.add_argument("--dest", required=True)
    collect_fixture.add_argument("--observed-partitions", type=int, default=30)
    collect_fixture.add_argument("--universe-state-path")

    update_history_fixture = subcommands.add_parser("update-holdings-history-fixture")
    update_history_fixture.add_argument("--fixture-path", required=True)
    update_history_fixture.add_argument("--universe-state-path", required=True)
    update_history_fixture.add_argument("--history-dir", required=True)
    update_history_fixture.add_argument("--observed-partitions", type=int, default=30)
    update_history_fixture.add_argument(
        "--refresh-snapshot",
        action="append",
        default=[],
        help="explicit snapshot refresh as etf_id:YYYY-MM-DD",
    )

    export_comparison = subcommands.add_parser("export-holdings-comparison")
    export_comparison.add_argument("--history-dir", required=True)
    export_comparison.add_argument("--universe-state-path", required=True)
    export_comparison.add_argument("--dest", required=True)
    export_comparison.add_argument("--security-resolution-path")

    import_history = subcommands.add_parser("import-holdings-history")
    import_history.add_argument("--manifest-path", required=True)
    import_history.add_argument("--history-dir", required=True)
    import_history.add_argument(
        "--refresh-snapshot",
        action="append",
        default=[],
        help="explicit snapshot refresh as etf_id:YYYY-MM-DD",
    )

    collect_universe = subcommands.add_parser(
        "collect-universe-fixture",
        description=(
            "Fixture-only Agent TReport native ETF universe and brand "
            "metadata collection."
        ),
        help="run fixture-only native universe collection",
    )
    collect_universe.add_argument("--fixture-path", required=True)
    collect_universe.add_argument("--dest", required=True)

    collect_source_catalog_command = subcommands.add_parser(
        "collect-source-catalog",
        description=(
            "Explicit SourceProvider catalog acquisition. The default path uses "
            "a fake fixture provider and does not call live providers."
        ),
        help="collect and stage a SourceProvider catalog",
    )
    collect_source_catalog_command.add_argument(
        "--source-provider",
        choices=("fake", *LIVE_SOURCE_PROVIDER_IDS),
    )
    collect_source_catalog_command.add_argument("--live", action="store_true")
    collect_source_catalog_command.add_argument("--fixture-path")
    collect_source_catalog_command.add_argument("--dest", required=True)
    collect_source_catalog_command.add_argument(
        "--approval-path",
        default=DEFAULT_APPROVAL_PROFILE_PATH,
    )
    collect_source_catalog_command.add_argument("--write-preflight", nargs="?", const="")

    update_history_source = subcommands.add_parser(
        "update-holdings-history-source",
        description=(
            "Explicit SourceProvider holdings acquisition. The default path "
            "uses a fake fixture provider and does not call live providers."
        ),
        help="update native holdings history from a SourceProvider",
    )
    update_history_source.add_argument(
        "--source-provider",
        choices=("fake", *LIVE_SOURCE_PROVIDER_IDS),
    )
    update_history_source.add_argument("--live", action="store_true")
    update_history_source.add_argument("--fixture-path")
    update_history_source.add_argument("--source-catalog-path", required=True)
    update_history_source.add_argument("--universe-state-path", required=True)
    update_history_source.add_argument("--history-dir", required=True)
    update_history_source.add_argument("--requested-date")
    update_history_source.add_argument(
        "--provider-etf-id",
        action="append",
        default=[],
        help="provider ETF id to fetch; required for live holdings acquisition",
    )
    update_history_source.add_argument(
        "--refresh-snapshot",
        action="append",
        default=[],
        help="explicit snapshot refresh as etf_id:YYYY-MM-DD",
    )
    update_history_source.add_argument(
        "--approval-path",
        default=DEFAULT_APPROVAL_PROFILE_PATH,
    )
    update_history_source.add_argument("--write-preflight", nargs="?", const="")

    live_source_baseline = subcommands.add_parser("run-live-source-baseline")
    live_source_baseline.add_argument("--config-path", required=True)
    live_source_baseline.add_argument("--operational-holdings-path", required=True)
    live_source_baseline.add_argument(
        "--history-dir",
        default="data/agent_treport/live-source/holdings-history",
    )
    live_source_baseline.add_argument("--live", action="store_true")
    live_source_baseline.add_argument(
        "--approval-path",
        default=DEFAULT_APPROVAL_PROFILE_PATH,
    )
    live_source_baseline.add_argument("--write-preflight", nargs="?", const="")

    collect_external = subcommands.add_parser(
        "collect-external-evidence",
        description=(
            "Collect provider-neutral external evidence for SignalBoard targets. "
            "Live providers run only when --live is supplied."
        ),
    )
    collect_external.add_argument(
        "--holdings-source",
        choices=("fixture", "operational", "targets"),
        default="fixture",
    )
    collect_external.add_argument("--holdings-path")
    collect_external.add_argument("--target-candidates-path")
    collect_external.add_argument(
        "--providers",
        action="append",
        required=True,
        help=(
            "comma-separated provider IDs; supported: "
            + ",".join(EXTERNAL_EVIDENCE_PROVIDER_IDS)
        ),
    )
    collect_external.add_argument("--live", action="store_true")
    collect_external.add_argument("--max-targets", type=int, default=2)
    collect_external.add_argument("--evidence-path", required=True)
    collect_external.add_argument("--summary-path")
    collect_external.add_argument("--cooldown-path")
    collect_external.add_argument("--focus-etf-id")
    collect_external.add_argument("--focus-etf-set-path")
    collect_external.add_argument("--observed-partitions", type=int, default=30)
    collect_external.add_argument("--align-claims", action="store_true")
    collect_external.add_argument("--model", choices=("codex",))
    collect_external.add_argument("--codex-model")
    collect_external.add_argument("--model-timeout-seconds", type=float, default=300)
    collect_external.add_argument("--timeout-seconds", type=float, default=12)
    collect_external.add_argument("--min-interval-seconds", type=float, default=0.2)
    collect_external.add_argument(
        "--ignore-cooldown",
        action="store_true",
        help="run selected providers even when a cooldown file has an active entry",
    )
    collect_external.add_argument(
        "--approval-path",
        default=DEFAULT_APPROVAL_PROFILE_PATH,
    )
    collect_external.add_argument("--write-preflight", nargs="?", const="")

    import_security_master_seed = subcommands.add_parser("import-security-master-seed")
    import_security_master_seed.add_argument("--stock-mapping-csv", required=True)
    import_security_master_seed.add_argument("--workspace", required=True)
    import_security_master_seed.add_argument("--output-path", required=True)
    import_security_master_seed.add_argument("--review-queue-path")

    export_security_resolution = subcommands.add_parser("export-security-resolution")
    export_security_resolution.add_argument("--security-master-path", required=True)
    export_security_resolution.add_argument("--output-path", required=True)

    resolve_security_master = subcommands.add_parser("resolve-security-master")
    resolve_security_master.add_argument("--holdings-path", required=True)
    resolve_security_master.add_argument("--security-master-path", required=True)
    resolve_security_master.add_argument("--output-path", required=True)
    resolve_security_master.add_argument("--review-queue-path", required=True)
    resolve_security_master.add_argument("--observed-partitions", type=int, default=30)
    resolve_security_master.add_argument("--disable-openfigi-lookup", action="store_true")

    check_readiness = subcommands.add_parser("check-operational-readiness")
    check_readiness.add_argument("--holdings-path", required=True)
    check_readiness.add_argument("--focus-etf-id")
    check_readiness.add_argument("--focus-etf-set-path")
    check_readiness.add_argument("--observed-partitions", type=int, default=30)
    check_readiness.add_argument("--sync-metadata-path")
    check_readiness.add_argument("--max-observed-age-days", type=int, default=3)
    check_readiness.add_argument("--operator-timezone", default=DEFAULT_OPERATOR_TIMEZONE)

    apply_mapping_patch = subcommands.add_parser("apply-security-mapping-patch")
    apply_mapping_patch.add_argument("--security-mapping-path", required=True)
    apply_mapping_patch.add_argument("--patch-path", required=True)
    apply_mapping_patch.add_argument("--output-path", required=True)
    apply_mapping_patch.add_argument("--overwrite", action="store_true")
    apply_mapping_patch.add_argument("--allow-replacements", action="store_true")

    propose_mapping_recovery = subcommands.add_parser(
        "propose-security-mapping-recovery"
    )
    propose_mapping_recovery.add_argument("--sync-metadata-path")
    propose_mapping_recovery.add_argument("--collection-summary-path")
    propose_mapping_recovery.add_argument("--model", choices=("codex",), required=True)
    propose_mapping_recovery.add_argument("--codex-model")
    propose_mapping_recovery.add_argument("--model-timeout-seconds", type=float, default=300)
    propose_mapping_recovery.add_argument("--output-path", required=True)
    propose_mapping_recovery.add_argument("--overwrite", action="store_true")

    inspect = subcommands.add_parser("inspect")
    inspect.add_argument("--run-id", required=True)
    inspect.add_argument("--sqlite-path", required=True)

    return parser


async def run_cli_async(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    model_client_factory: ModelClientFactory | None = None,
    readiness_now: Callable[[], datetime] | None = None,
    collection_now: Callable[[], datetime] | None = None,
    openfigi_client_factory: OpenFigiClientFactory | None = None,
    external_evidence_provider_overrides: (
        ExternalEvidenceProviderOverrides | None
    ) = None,
    telegram_client_factory: TelegramClientFactory | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    output = stdout or sys.stdout
    error_output = stderr or sys.stderr

    if args.command == "run-report":
        return await _run_report_command(
            args,
            output=output,
            error_output=error_output,
            model_client_factory=model_client_factory,
        )

    if args.command == "run-native-operational-handoff":
        return await _run_native_operational_handoff_command(
            args,
            output=output,
            error_output=error_output,
            model_client_factory=model_client_factory,
            readiness_now=readiness_now,
            collection_now=collection_now,
        )

    if args.command == "run-pre-publish-preview":
        return await _run_pre_publish_preview_command(
            args,
            output=output,
            error_output=error_output,
            model_client_factory=model_client_factory,
            readiness_now=readiness_now,
            collection_now=collection_now,
            external_evidence_provider_overrides=external_evidence_provider_overrides,
        )

    if args.command == "deliver-telegram-alert":
        return _deliver_telegram_alert_command(
            args,
            output=output,
            error_output=error_output,
            telegram_client_factory=telegram_client_factory,
            now=collection_now,
        )

    if args.command == "verify-daily-publish-closure":
        return _verify_daily_publish_closure_command(
            args,
            output=output,
            error_output=error_output,
            now=collection_now,
        )

    if args.command == "project-delivery-closure-review":
        return await _project_delivery_closure_review_command(
            args,
            output=output,
            error_output=error_output,
        )

    if args.command == "sync-operational-holdings":
        return _sync_operational_holdings_command(
            args,
            output=output,
            error_output=error_output,
        )

    if args.command == "collect-holdings-fixture":
        return _collect_holdings_fixture_command(
            args,
            output=output,
            error_output=error_output,
            now=collection_now,
        )

    if args.command == "update-holdings-history-fixture":
        return _update_holdings_history_fixture_command(
            args,
            output=output,
            error_output=error_output,
            now=collection_now,
        )

    if args.command == "export-holdings-comparison":
        return _export_holdings_comparison_command(
            args,
            output=output,
            error_output=error_output,
            now=collection_now,
        )

    if args.command == "import-holdings-history":
        return _import_holdings_history_command(
            args,
            output=output,
            error_output=error_output,
            now=collection_now,
        )

    if args.command == "collect-universe-fixture":
        return _collect_universe_fixture_command(
            args,
            output=output,
            error_output=error_output,
            now=collection_now,
        )

    if args.command == "collect-source-catalog":
        return _collect_source_catalog_command(
            args,
            output=output,
            error_output=error_output,
            now=collection_now,
        )

    if args.command == "update-holdings-history-source":
        return _update_holdings_history_source_command(
            args,
            output=output,
            error_output=error_output,
            now=collection_now,
        )

    if args.command == "run-live-source-baseline":
        return _run_live_source_baseline_command(
            args,
            output=output,
            error_output=error_output,
            now=collection_now,
        )

    if args.command == "collect-external-evidence":
        return await _collect_external_evidence_command(
            args,
            output=output,
            error_output=error_output,
            model_client_factory=model_client_factory,
            external_evidence_provider_overrides=external_evidence_provider_overrides,
        )

    if args.command == "import-security-master-seed":
        return _import_security_master_seed_command(
            args,
            output=output,
            error_output=error_output,
        )

    if args.command == "export-security-resolution":
        return _export_security_resolution_command(
            args,
            output=output,
            error_output=error_output,
        )

    if args.command == "resolve-security-master":
        return _resolve_security_master_command(
            args,
            output=output,
            error_output=error_output,
            openfigi_client_factory=openfigi_client_factory,
        )

    if args.command == "check-operational-readiness":
        return _check_operational_readiness_command(
            args,
            output=output,
            error_output=error_output,
            now=readiness_now,
        )

    if args.command == "apply-security-mapping-patch":
        return _apply_security_mapping_patch_command(
            args,
            output=output,
            error_output=error_output,
        )

    if args.command == "propose-security-mapping-recovery":
        return await _propose_security_mapping_recovery_command(
            args,
            output=output,
            error_output=error_output,
            model_client_factory=model_client_factory,
        )

    if args.command == "inspect":
        return await _inspect_command(args, output=output, error_output=error_output)

    raise ValueError(f"unsupported CLI command: {args.command}")


def _sync_operational_holdings_command(
    args: argparse.Namespace,
    *,
    output: TextIO,
    error_output: TextIO,
) -> int:
    if args.observed_partitions <= 0:
        _write_cli_input_error(
            error_output,
            "observed-partitions must be a positive integer",
        )
        return 2
    if args.security_mapping_path is not None and args.security_resolution_path is not None:
        _write_cli_input_error(
            error_output,
            "--security-resolution-path and --security-mapping-path cannot both be supplied",
        )
        return 2
    try:
        metadata = sync_operational_holdings(
            source_manifest_path=args.source,
            dest_dir=args.dest,
            observed_partitions=args.observed_partitions,
            security_mapping_path=args.security_mapping_path,
            security_resolution_path=args.security_resolution_path,
        )
    except OperationalHoldingsInputError as exc:
        _write_cli_input_error(error_output, str(exc))
        return 2
    except Exception as exc:
        _write_operational_error(
            error_output,
            reason="sync_operational_holdings_failed",
            exc=exc,
        )
        return 1

    json.dump(metadata, output, ensure_ascii=False)
    output.write("\n")
    return 0


def _collect_holdings_fixture_command(
    args: argparse.Namespace,
    *,
    output: TextIO,
    error_output: TextIO,
    now: Callable[[], datetime] | None = None,
) -> int:
    if args.observed_partitions <= 0:
        _write_cli_input_error(
            error_output,
            "observed-partitions must be a positive integer",
        )
        return 2
    try:
        summary = collect_holdings_fixture(
            fixture_path=args.fixture_path,
            dest_dir=args.dest,
            observed_partitions=args.observed_partitions,
            universe_state_path=args.universe_state_path,
            now=now,
        )
    except OperationalHoldingsInputError as exc:
        _write_cli_input_error(error_output, str(exc))
        return 2
    except Exception as exc:
        _write_operational_error(
            error_output,
            reason="collect_holdings_fixture_failed",
            exc=exc,
        )
        return 1

    _write_compact_json(output, summary)
    return 0


def _update_holdings_history_fixture_command(
    args: argparse.Namespace,
    *,
    output: TextIO,
    error_output: TextIO,
    now: Callable[[], datetime] | None = None,
) -> int:
    if args.observed_partitions <= 0:
        _write_cli_input_error(
            error_output,
            "observed-partitions must be a positive integer",
        )
        return 2
    try:
        refresh_snapshots = _parse_refresh_snapshots(args.refresh_snapshot)
        summary = update_holdings_history_fixture(
            fixture_path=args.fixture_path,
            universe_state_path=args.universe_state_path,
            history_dir=args.history_dir,
            observed_partitions=args.observed_partitions,
            refresh_snapshots=refresh_snapshots,
            now=now,
        )
    except OperationalHoldingsInputError as exc:
        _write_cli_input_error(error_output, str(exc))
        return 2
    except Exception as exc:
        _write_operational_error(
            error_output,
            reason="update_holdings_history_fixture_failed",
            exc=exc,
        )
        return 1

    _write_compact_json(output, summary)
    return 0


def _export_holdings_comparison_command(
    args: argparse.Namespace,
    *,
    output: TextIO,
    error_output: TextIO,
    now: Callable[[], datetime] | None = None,
) -> int:
    try:
        summary = export_latest_holdings_comparison(
            history_dir=args.history_dir,
            universe_state_path=args.universe_state_path,
            dest_dir=args.dest,
            security_resolution_path=args.security_resolution_path,
            now=now,
        )
    except OperationalHoldingsInputError as exc:
        _write_cli_input_error(error_output, str(exc))
        return 2
    except Exception as exc:
        _write_operational_error(
            error_output,
            reason="export_holdings_comparison_failed",
            exc=exc,
        )
        return 1

    _write_compact_json(output, summary)
    return 0


def _import_holdings_history_command(
    args: argparse.Namespace,
    *,
    output: TextIO,
    error_output: TextIO,
    now: Callable[[], datetime] | None = None,
) -> int:
    try:
        refresh_snapshots = _parse_refresh_snapshots(args.refresh_snapshot)
        summary = import_operational_holdings_export_to_history(
            manifest_path=args.manifest_path,
            history_dir=args.history_dir,
            refresh_snapshots=refresh_snapshots,
            now=now,
        )
    except OperationalHoldingsInputError as exc:
        _write_cli_input_error(error_output, str(exc))
        return 2
    except Exception as exc:
        _write_operational_error(
            error_output,
            reason="import_holdings_history_failed",
            exc=exc,
        )
        return 1

    _write_compact_json(output, summary)
    return 0


def _parse_refresh_snapshots(values: Sequence[str]) -> tuple[tuple[str, str], ...]:
    snapshots: list[tuple[str, str]] = []
    for value in values:
        if ":" not in value:
            raise OperationalHoldingsInputError(
                "refresh-snapshot must use etf_id:YYYY-MM-DD"
            )
        etf_id, observed_date = value.split(":", 1)
        if not etf_id.strip() or not observed_date.strip():
            raise OperationalHoldingsInputError(
                "refresh-snapshot must use etf_id:YYYY-MM-DD"
            )
        try:
            parsed = datetime.strptime(observed_date.strip(), "%Y-%m-%d").date()
        except ValueError as exc:
            raise OperationalHoldingsInputError(
                "refresh-snapshot date must be YYYY-MM-DD"
            ) from exc
        snapshots.append((etf_id.strip(), parsed.isoformat()))
    return tuple(snapshots)


def _collect_universe_fixture_command(
    args: argparse.Namespace,
    *,
    output: TextIO,
    error_output: TextIO,
    now: Callable[[], datetime] | None = None,
) -> int:
    try:
        summary = collect_universe_fixture(
            fixture_path=args.fixture_path,
            dest_dir=args.dest,
            now=now,
        )
    except OperationalUniverseInputError as exc:
        _write_cli_input_error(error_output, str(exc))
        return 2
    except Exception as exc:
        _write_operational_error(
            error_output,
            reason="collect_universe_fixture_failed",
            exc=exc,
        )
        return 1

    _write_compact_json(output, summary)
    return 0


def _collect_source_catalog_command(
    args: argparse.Namespace,
    *,
    output: TextIO,
    error_output: TextIO,
    now: Callable[[], datetime] | None = None,
) -> int:
    try:
        source_provider_id = args.source_provider or "fake"
        if args.live and args.source_provider is None:
            raise SourceAcquisitionInputError(
                "live source acquisition requires --source-provider"
            )
        if not args.live and source_provider_id != "fake":
            raise SourceAcquisitionInputError("live source acquisition requires --live")
        if args.live:
            approval = _source_provider_approval_check(
                args=args,
                command="collect-source-catalog",
                required_scope="live_source_catalog",
                provider_ids=(source_provider_id,),
                preflight_base_dir=Path(args.dest),
                approved_max_target_count=0,
                data_classes=(
                    "live_source_catalog_identifiers",
                    "active_strategy_etf_catalog_metadata",
                ),
                now=now,
            )
            if not _approval_is_valid(approval):
                return _write_daily_approval_block(
                    output=output,
                    command="collect-source-catalog",
                    blocked_path=Path(args.dest) / "source_catalog_approval_block.json",
                    approval=approval,
                )
            provider = create_live_source_provider(source_provider_id)
        else:
            if not args.fixture_path:
                raise SourceAcquisitionInputError(
                    "--fixture-path is required for fake source acquisition"
                )
            provider = FakeSourceProvider.from_fixture_path(args.fixture_path)
        summary = collect_source_catalog(
            provider=provider,
            dest_dir=args.dest,
            now=now,
        )
    except SourceAcquisitionInputError as exc:
        _write_cli_input_error(error_output, str(exc))
        return 2
    except Exception as exc:
        _write_operational_error(
            error_output,
            reason="collect_source_catalog_failed",
            exc=exc,
        )
        return 1

    _write_compact_json(output, summary)
    return 0


def _update_holdings_history_source_command(
    args: argparse.Namespace,
    *,
    output: TextIO,
    error_output: TextIO,
    now: Callable[[], datetime] | None = None,
) -> int:
    try:
        source_provider_id = args.source_provider or "fake"
        if args.live and args.source_provider is None:
            raise SourceAcquisitionInputError(
                "live source acquisition requires --source-provider"
            )
        if not args.live and source_provider_id != "fake":
            raise SourceAcquisitionInputError("live source acquisition requires --live")
        if args.live:
            if not args.provider_etf_id:
                raise SourceAcquisitionInputError(
                    "live source holdings requires --provider-etf-id"
                )
            approval = _source_provider_approval_check(
                args=args,
                command="update-holdings-history-source",
                required_scope="live_holdings_acquisition",
                provider_ids=(source_provider_id,),
                preflight_base_dir=Path(args.history_dir),
                approved_max_target_count=len(set(args.provider_etf_id or [])),
                data_classes=(
                    "live_source_holdings_request_identifiers",
                    "safe_provider_etf_identifiers",
                    "normalized_holdings_snapshot_summary",
                ),
                now=now,
            )
            if not _approval_is_valid(approval):
                return _write_daily_approval_block(
                    output=output,
                    command="update-holdings-history-source",
                    blocked_path=Path(args.history_dir)
                    / "holdings_source_approval_block.json",
                    approval=approval,
                )
            provider = create_live_source_provider(source_provider_id)
        else:
            if not args.fixture_path:
                raise SourceAcquisitionInputError(
                    "--fixture-path is required for fake source acquisition"
                )
            provider = FakeSourceProvider.from_fixture_path(args.fixture_path)
        refresh_snapshots = set(_parse_refresh_snapshots(args.refresh_snapshot))
        summary = update_holdings_history_source(
            provider=provider,
            source_catalog_path=args.source_catalog_path,
            universe_state_path=args.universe_state_path,
            history_dir=args.history_dir,
            requested_date=args.requested_date,
            provider_etf_ids=set(args.provider_etf_id or []),
            refresh_snapshots=refresh_snapshots,
            now=now,
        )
    except (OperationalHoldingsInputError, SourceAcquisitionInputError) as exc:
        _write_cli_input_error(error_output, str(exc))
        return 2
    except Exception as exc:
        _write_operational_error(
            error_output,
            reason="update_holdings_history_source_failed",
            exc=exc,
        )
        return 1

    _write_compact_json(output, summary)
    return 0


def _run_live_source_baseline_command(
    args: argparse.Namespace,
    *,
    output: TextIO,
    error_output: TextIO,
    now: Callable[[], datetime] | None = None,
) -> int:
    try:
        if args.live:
            provider_ids = _live_baseline_config_provider_ids(Path(args.config_path))
            approval = _source_provider_approval_check(
                args=args,
                command="run-live-source-baseline",
                required_scope="live_source_baseline",
                provider_ids=provider_ids,
                preflight_base_dir=Path(args.history_dir),
                approved_max_target_count=len(provider_ids),
                data_classes=(
                    "live_source_baseline_representative_identifiers",
                    "live_source_baseline_bulk_provider_cohort",
                    "normalized_holdings_snapshot_summary",
                ),
                now=now,
            )
            if not _approval_is_valid(approval):
                return _write_daily_approval_block(
                    output=output,
                    command="run-live-source-baseline",
                    blocked_path=Path(args.history_dir)
                    / "live_source_baseline_approval_block.json",
                    approval=approval,
                )
        provider_inputs = _load_live_baseline_provider_inputs(
            config_path=Path(args.config_path),
            live=bool(args.live),
        )
        summary = run_live_baseline_backfill(
            provider_inputs=provider_inputs,
            operational_manifest_path=args.operational_holdings_path,
            history_dir=args.history_dir,
            now=now,
        )
    except (OperationalHoldingsInputError, SourceAcquisitionInputError) as exc:
        _write_cli_input_error(error_output, str(exc))
        return 2
    except Exception as exc:
        _write_operational_error(
            error_output,
            reason="run_live_source_baseline_failed",
            exc=exc,
        )
        return 1

    _write_compact_json(output, summary)
    return 0


def _live_baseline_config_provider_ids(config_path: Path) -> tuple[str, ...]:
    config = _read_cli_json_object(config_path, label="live source baseline config")
    if config.get("schema_version") != "agent_treport.live_source.baseline_config.v1":
        raise SourceAcquisitionInputError("invalid live source baseline config schema")
    providers = config.get("providers")
    if not isinstance(providers, list) or not providers:
        raise SourceAcquisitionInputError(
            "live source baseline config providers must be non-empty"
        )
    provider_ids: list[str] = []
    for item in providers:
        if not isinstance(item, Mapping):
            raise SourceAcquisitionInputError(
                "live source baseline provider must be an object"
            )
        provider_ids.append(_required_config_text(item.get("source_provider_id")))
    return tuple(dict.fromkeys(provider_ids))


def _load_live_baseline_provider_inputs(
    *,
    config_path: Path,
    live: bool,
) -> tuple[LiveBaselineProviderInput, ...]:
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SourceAcquisitionInputError("live source baseline config is unreadable") from exc
    except json.JSONDecodeError as exc:
        raise SourceAcquisitionInputError("live source baseline config is invalid JSON") from exc
    if not isinstance(config, Mapping):
        raise SourceAcquisitionInputError("live source baseline config must be an object")
    if config.get("schema_version") != "agent_treport.live_source.baseline_config.v1":
        raise SourceAcquisitionInputError("invalid live source baseline config schema")
    providers = config.get("providers")
    if not isinstance(providers, list) or not providers:
        raise SourceAcquisitionInputError("live source baseline config providers must be non-empty")
    provider_inputs: list[LiveBaselineProviderInput] = []
    for item in providers:
        if not isinstance(item, Mapping):
            raise SourceAcquisitionInputError("live source baseline provider must be an object")
        source_provider_id = _required_config_text(item.get("source_provider_id"))
        if live:
            provider = create_live_source_provider(source_provider_id)
        else:
            fixture_path = _required_config_text(item.get("fixture_path"))
            provider = FakeSourceProvider.from_fixture_path(fixture_path)
            if provider.source_provider_id != source_provider_id:
                raise SourceAcquisitionInputError(
                    "live source baseline fixture provider id mismatch"
                )
        provider_inputs.append(
            LiveBaselineProviderInput(
                provider=provider,
                source_catalog_path=_required_config_text(item.get("source_catalog_path")),
                universe_state_path=_required_config_text(item.get("universe_state_path")),
                representative_provider_etf_id=_required_config_text(
                    item.get("representative_provider_etf_id")
                ),
                representative_requested_date=_required_config_text(
                    item.get("representative_requested_date")
                ),
            )
        )
    return tuple(provider_inputs)


def _required_config_text(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise SourceAcquisitionInputError("live source baseline config field must be text")
    return value


def _import_security_master_seed_command(
    args: argparse.Namespace,
    *,
    output: TextIO,
    error_output: TextIO,
) -> int:
    workspace = Path(args.workspace)
    output_path = Path(args.output_path)
    review_queue_path = (
        Path(args.review_queue_path)
        if args.review_queue_path is not None
        else workspace / "review_queue.json"
    )
    try:
        workspace.mkdir(parents=True, exist_ok=True)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        review_queue_path.parent.mkdir(parents=True, exist_ok=True)
        stock_mapping_rows = _read_stock_mapping_csv(Path(args.stock_mapping_csv))
        existing_master = (
            _read_cli_json_object(output_path, label="security master")
            if output_path.is_file()
            else None
        )
        master, review_queue, summary = import_security_master_seed_rows(
            stock_mapping_rows=stock_mapping_rows,
            existing_master=existing_master,
        )
        _write_pretty_json(output_path, master)
        _write_pretty_json(review_queue_path, review_queue)
    except (OperationalHoldingsInputError, SecurityResolutionInputError) as exc:
        _write_cli_input_error(error_output, str(exc))
        return 2
    except Exception as exc:
        _write_operational_error(
            error_output,
            reason="security_master_seed_import_failed",
            exc=exc,
        )
        return 1

    _write_compact_json(
        output,
        {
            **summary,
            "output_path": str(output_path),
            "review_queue_path": str(review_queue_path),
        },
    )
    return 0


def _export_security_resolution_command(
    args: argparse.Namespace,
    *,
    output: TextIO,
    error_output: TextIO,
) -> int:
    output_path = Path(args.output_path)
    try:
        security_master = _read_cli_json_object(
            Path(args.security_master_path),
            label="security master",
        )
        resolution_export, summary = build_security_resolution_export(security_master)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        _write_pretty_json(output_path, resolution_export)
    except (OperationalHoldingsInputError, SecurityResolutionInputError) as exc:
        _write_cli_input_error(error_output, str(exc))
        return 2
    except Exception as exc:
        _write_operational_error(
            error_output,
            reason="security_resolution_export_failed",
            exc=exc,
        )
        return 1

    _write_compact_json(output, {**summary, "output_path": str(output_path)})
    return 0


def _resolve_security_master_command(
    args: argparse.Namespace,
    *,
    output: TextIO,
    error_output: TextIO,
    openfigi_client_factory: OpenFigiClientFactory | None,
) -> int:
    if args.observed_partitions <= 0:
        _write_cli_input_error(
            error_output,
            "observed-partitions must be a positive integer",
        )
        return 2
    output_path = Path(args.output_path)
    review_queue_path = Path(args.review_queue_path)
    try:
        security_master = _read_cli_json_object(
            Path(args.security_master_path),
            label="security master",
        )
        observations = _read_security_resolution_observations(
            Path(args.holdings_path),
            observed_partitions=int(args.observed_partitions),
        )
        openfigi_lookup_count = 0
        warnings: list[dict[str, JsonValue]] = []
        if not bool(args.disable_openfigi_lookup):
            factory = openfigi_client_factory or create_openfigi_client_from_env
            openfigi_result = _apply_openfigi_lookup(
                observations=observations,
                security_master=security_master,
                client=factory(),
            )
            observations = openfigi_result["observations"]
            warnings = openfigi_result["warnings"]
            openfigi_lookup_count = int(openfigi_result["lookup_count"])
        resolved_master, review_queue, summary = resolve_security_master_observations(
            security_master=security_master,
            observations=observations,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        review_queue_path.parent.mkdir(parents=True, exist_ok=True)
        _write_pretty_json(output_path, resolved_master)
        _write_pretty_json(review_queue_path, review_queue)
    except (OperationalHoldingsInputError, SecurityResolutionInputError) as exc:
        _write_cli_input_error(error_output, str(exc))
        return 2
    except Exception as exc:
        _write_operational_error(
            error_output,
            reason="security_master_resolve_failed",
            exc=exc,
        )
        return 1

    _write_compact_json(
        output,
        {
            **summary,
            "output_path": str(output_path),
            "review_queue_path": str(review_queue_path),
            "openfigi_lookup_enabled": not bool(args.disable_openfigi_lookup),
            "openfigi_lookup_count": openfigi_lookup_count,
            "warnings": warnings,
        },
    )
    return 0


def _check_operational_readiness_command(
    args: argparse.Namespace,
    *,
    output: TextIO,
    error_output: TextIO,
    now: Callable[[], datetime] | None = None,
) -> int:
    if args.observed_partitions <= 0:
        _write_cli_input_error(
            error_output,
            "observed-partitions must be a positive integer",
        )
        return 2
    if args.max_observed_age_days < 0:
        _write_cli_input_error(
            error_output,
            "max-observed-age-days must be a non-negative integer",
        )
        return 2
    try:
        focus_etf_ids = None
        if args.focus_etf_set_path is not None:
            focus_etf_ids = load_focus_etf_set_file(
                args.focus_etf_set_path
            ).focus_etf_ids
        result = check_operational_run_readiness(
            holdings_path=args.holdings_path,
            focus_etf_id=args.focus_etf_id,
            focus_etf_ids=focus_etf_ids,
            observed_partitions=args.observed_partitions,
            sync_metadata_path=args.sync_metadata_path,
            max_observed_age_days=args.max_observed_age_days,
            operator_timezone=args.operator_timezone,
            now=now,
        )
    except (FocusETFSetInputError, OperationalReadinessInputError) as exc:
        _write_cli_input_error(error_output, str(exc))
        return 2
    except Exception as exc:
        _write_operational_error(
            error_output,
            reason="check_operational_readiness_failed",
            exc=exc,
        )
        return 1

    _write_compact_json(output, result)
    return 0


def _apply_security_mapping_patch_command(
    args: argparse.Namespace,
    *,
    output: TextIO,
    error_output: TextIO,
) -> int:
    try:
        security_mapping_path = Path(args.security_mapping_path)
        patch_path = Path(args.patch_path)
        output_path = Path(args.output_path)
        _validate_security_mapping_patch_output_path(
            security_mapping_path=security_mapping_path,
            patch_path=patch_path,
            output_path=output_path,
            overwrite=bool(args.overwrite),
        )
        existing_mapping = load_security_mapping(security_mapping_path)
        patch = _read_cli_json_object(patch_path, label="security mapping patch")
        merged, summary = merge_security_mapping_patch(
            existing_mapping,
            patch,
            allow_replacements=bool(args.allow_replacements),
        )
    except OperationalHoldingsInputError as exc:
        _write_cli_input_error(error_output, str(exc))
        return 2
    except Exception as exc:
        _write_operational_error(
            error_output,
            reason="security_mapping_patch_apply_failed",
            exc=exc,
        )
        return 1

    try:
        _write_pretty_json(output_path, merged)
    except Exception as exc:
        _write_operational_error(
            error_output,
            reason="security_mapping_patch_apply_failed",
            exc=exc,
        )
        return 1

    _write_compact_json(
        output,
        _build_security_mapping_patch_apply_result(
            security_mapping_path=args.security_mapping_path,
            patch_path=args.patch_path,
            output_path=args.output_path,
            summary=summary,
        ),
    )
    return 0


def _build_security_mapping_patch_apply_result(
    *,
    security_mapping_path: str,
    patch_path: str,
    output_path: str,
    summary: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    return {
        "schema_version": PATCH_APPLY_RESULT_SCHEMA_VERSION,
        "status": "succeeded",
        "security_mapping_path": security_mapping_path,
        "patch_path": patch_path,
        "output_path": output_path,
        "added_mapping_count": summary["added_mapping_count"],
        "replaced_mapping_count": summary["replaced_mapping_count"],
        "unchanged_mapping_count": summary["unchanged_mapping_count"],
        "total_mapping_count": summary["total_mapping_count"],
    }


async def _propose_security_mapping_recovery_command(
    args: argparse.Namespace,
    *,
    output: TextIO,
    error_output: TextIO,
    model_client_factory: ModelClientFactory | None,
) -> int:
    output_path = Path(args.output_path)
    try:
        source_kind, source_path, source_argument = _recovery_evidence_source(args)
        _validate_recovery_proposal_output_path(
            source_path=source_path,
            source_label=(
                "sync metadata"
                if source_kind == "sync_metadata"
                else "collection summary"
            ),
            output_path=output_path,
            overwrite=bool(args.overwrite),
        )
        source_document = _read_cli_json_object(
            source_path,
            label=(
                "sync metadata"
                if source_kind == "sync_metadata"
                else "collection summary"
            ),
        )
        samples = (
            _validate_recovery_samples(source_document)
            if source_kind == "sync_metadata"
            else _validate_collection_summary_recovery_samples(source_document)
        )
    except OperationalHoldingsInputError as exc:
        _write_cli_input_error(error_output, str(exc))
        return 2

    try:
        model_called = False
        if samples:
            config = ModelProviderConfig(
                provider=args.model,
                model=args.codex_model,
                timeout_seconds=args.model_timeout_seconds,
            )
            factory = model_client_factory or create_model_client
            model_client = factory(config)
            response = await model_client.complete(
                _build_recovery_proposal_request(samples)
            )
            proposals = _validate_recovery_proposal_response(
                response=response,
                samples=samples,
            )
            model_called = True
        else:
            proposals = []
        proposal_document: dict[str, JsonValue] = {
            "schema_version": RECOVERY_PROPOSAL_SCHEMA_VERSION,
            "proposals": proposals,
        }
        if source_kind == "sync_metadata":
            proposal_document["source_sync_metadata_path"] = source_argument
        else:
            proposal_document["source_evidence_type"] = "native_collection_summary"
            proposal_document["source_collection_summary_path"] = Path(
                source_argument
            ).name
        _write_pretty_json(output_path, proposal_document)
    except Exception as exc:
        _write_operational_error(
            error_output,
            reason="security_mapping_recovery_proposal_failed",
            exc=exc,
        )
        return 1

    _write_compact_json(
        output,
        _build_security_mapping_recovery_proposal_result(
            source_kind=source_kind,
            source_path=source_argument,
            output_path=args.output_path,
            sample_count=len(samples),
            proposals=proposals,
            model_called=model_called,
        ),
    )
    return 0


def _recovery_evidence_source(args: argparse.Namespace) -> tuple[str, Path, str]:
    sync_metadata_path = args.sync_metadata_path
    collection_summary_path = args.collection_summary_path
    if (sync_metadata_path is None) == (collection_summary_path is None):
        raise OperationalHoldingsInputError(
            "supply exactly one of --sync-metadata-path or --collection-summary-path"
        )
    if sync_metadata_path is not None:
        return "sync_metadata", Path(sync_metadata_path), sync_metadata_path
    return "collection_summary", Path(collection_summary_path), collection_summary_path


def _build_security_mapping_recovery_proposal_result(
    *,
    source_kind: str,
    source_path: str,
    output_path: str,
    sample_count: int,
    proposals: Sequence[Mapping[str, JsonValue]],
    model_called: bool,
) -> dict[str, JsonValue]:
    proposed_count = sum(1 for proposal in proposals if proposal["status"] == "proposed")
    unresolved_count = sum(1 for proposal in proposals if proposal["status"] == "unresolved")
    result: dict[str, JsonValue] = {
        "schema_version": RECOVERY_PROPOSAL_RESULT_SCHEMA_VERSION,
        "status": "succeeded",
        "output_path": output_path,
        "sample_count": sample_count,
        "proposal_count": len(proposals),
        "proposed_count": proposed_count,
        "unresolved_count": unresolved_count,
        "model_called": model_called,
    }
    if source_kind == "sync_metadata":
        result["sync_metadata_path"] = source_path
    else:
        result["source_evidence_type"] = "native_collection_summary"
        result["collection_summary_path"] = Path(source_path).name
    return result


async def _collect_external_evidence_command(
    args: argparse.Namespace,
    *,
    output: TextIO,
    error_output: TextIO,
    model_client_factory: ModelClientFactory | None,
    external_evidence_provider_overrides: ExternalEvidenceProviderOverrides | None,
) -> int:
    try:
        focus_etf_ids = _focus_etf_ids_from_args(args)
        summary_path = (
            Path(args.summary_path)
            if args.summary_path
            else _default_evidence_summary_path(Path(args.evidence_path))
        )
        provider_ids = _external_evidence_provider_ids(args.providers)
        classifier = None
        if args.align_claims:
            if args.model != "codex":
                raise SignalReportInputError("--align-claims requires --model codex")
        approval = _external_evidence_approval_check(
            args=args,
            provider_ids=provider_ids,
            summary_path=summary_path,
            model_client_factory=model_client_factory,
            external_evidence_provider_overrides=external_evidence_provider_overrides,
        )
        if approval is not None and not _approval_is_valid(approval):
            return _write_daily_approval_block(
                output=output,
                command="collect-external-evidence",
                blocked_path=summary_path.parent / "external_evidence_approval_block.json",
                approval=approval,
            )
        if args.align_claims:
            try:
                factory = model_client_factory or create_model_client
                model_client = factory(
                    ModelProviderConfig(
                        provider=args.model,
                        model=args.codex_model,
                        timeout_seconds=args.model_timeout_seconds,
                    )
                )
            except Exception as exc:
                _write_operational_error(
                    error_output,
                    reason="model_client_failed",
                    exc=exc,
                )
                return 1
            classifier = CodexAlignmentClassifier(model_client)
        request = ExternalEvidenceRequest(
            holdings_source=args.holdings_source,
            holdings_path=args.holdings_path,
            target_candidates_path=args.target_candidates_path,
            provider_ids=provider_ids,
            live=bool(args.live),
            max_targets=args.max_targets,
            evidence_path=args.evidence_path,
            summary_path=summary_path,
            cooldown_path=args.cooldown_path,
            focus_etf_id=args.focus_etf_id,
            focus_etf_ids=focus_etf_ids,
            observed_partitions=args.observed_partitions,
            align_claims=bool(args.align_claims),
            classifier=classifier,
            timeout_seconds=args.timeout_seconds,
            min_interval_seconds=args.min_interval_seconds,
            ignore_cooldown=bool(args.ignore_cooldown),
            provider_overrides=external_evidence_provider_overrides or {},
        )
        result = await asyncio.to_thread(collect_external_evidence, request)
    except ExternalEvidenceCollectionError as exc:
        json.dump(
            {
                "reason": "external_evidence_collection_failed",
                "error_code": exc.error_code,
                "evidence_path": exc.evidence_path,
                "summary_path": exc.summary_path,
            },
            error_output,
            ensure_ascii=False,
        )
        error_output.write("\n")
        return 1
    except Exception as exc:
        _write_cli_input_error(error_output, str(exc))
        return 2

    json.dump(
        {
            "status": "succeeded",
            "evidence_count": result.evidence_count,
            "evidence_path": str(Path(args.evidence_path)),
            "summary_path": str(summary_path),
        },
        output,
        ensure_ascii=False,
    )
    output.write("\n")
    return 0


async def _run_pre_publish_preview_command(
    args: argparse.Namespace,
    *,
    output: TextIO,
    error_output: TextIO,
    model_client_factory: ModelClientFactory | None,
    readiness_now: Callable[[], datetime] | None,
    collection_now: Callable[[], datetime] | None,
    external_evidence_provider_overrides: ExternalEvidenceProviderOverrides | None,
) -> int:
    try:
        dest = Path(args.dest)
        dest.mkdir(parents=True, exist_ok=True)
        output_path = (
            Path(args.output_path)
            if args.output_path
            else dest / "pre_publish_handoff.json"
        )
        evidence_path, evidence_summary_path, cached_evidence = (
            _pre_publish_evidence_paths(args, dest=dest)
        )
        if args.max_evidence_targets <= 0:
            raise SignalReportInputError("max-evidence-targets must be a positive integer")
        if args.preview_timeout_seconds <= 0:
            raise SignalReportInputError(
                "preview-timeout-seconds must be a positive number"
            )
        export_dir = Path(args.export_dir) if args.export_dir else dest / "operational-holdings"
        security_resolution_path = _native_handoff_security_resolution_path(args)
        export_summary = export_latest_holdings_comparison(
            history_dir=args.history_dir,
            universe_state_path=args.universe_state_path,
            dest_dir=export_dir,
            security_resolution_path=security_resolution_path,
            now=collection_now,
        )
        manifest_path = export_dir / "url_holdings_cumulative.json"
        focus_etf_ids = _focus_etf_ids_from_args(args)
        readiness_path = dest / "readiness.json"
        readiness = check_operational_run_readiness(
            holdings_path=manifest_path,
            focus_etf_id=args.focus_etf_id,
            focus_etf_ids=focus_etf_ids,
            observed_partitions=args.observed_partitions,
            now=readiness_now,
        )
        _write_native_handoff_payload(readiness_path, readiness)
        if _pre_publish_readiness_blocks_report(args=args, readiness=readiness):
            external_evidence_summary_path = _pre_publish_existing_or_not_run_summary(
                evidence_summary_path
            )
            external_evidence_summary = _read_cli_json_object(
                external_evidence_summary_path,
                label="external evidence summary",
            )
            exclusion_summary_path = dest / "provider_etf_exclusion_summary.json"
            exclusion_summary = _provider_etf_exclusion_summary(
                universe_state_path=Path(args.universe_state_path),
                collection_summary=export_summary,
            )
            _write_native_handoff_payload(exclusion_summary_path, exclusion_summary)
            source_acquisition_summary_path = (
                Path(args.history_dir) / SOURCE_ACQUISITION_SUMMARY_FILENAME
            )
            source_acquisition_summary = (
                _read_cli_json_object(
                    source_acquisition_summary_path,
                    label="source acquisition summary",
                )
                if source_acquisition_summary_path.is_file()
                else None
            )
            blocked_context: dict[str, object] = {
                "dest": dest,
                "sqlite_path": Path(args.sqlite_path)
                if args.sqlite_path
                else dest / "runtime.sqlite3",
                "artifact_root": Path(args.artifact_root)
                if args.artifact_root
                else dest / "artifacts",
                "output_path": output_path,
                "manifest_path": manifest_path,
                "collection_summary_path": manifest_path.parent
                / "collection_summary.json",
                "readiness_path": readiness_path,
                "readiness": readiness,
                "export_summary": export_summary,
                "external_evidence_summary_path": external_evidence_summary_path,
                "external_evidence_summary": external_evidence_summary,
                "source_acquisition_summary_path": source_acquisition_summary_path,
                "source_acquisition_summary": source_acquisition_summary,
                "exclusion_summary_path": exclusion_summary_path,
                "exclusion_summary": exclusion_summary,
                "security_resolution_path": security_resolution_path,
            }
            reason = (
                "readiness_failed"
                if readiness.get("status") == "failed"
                else "readiness_hold"
            )
            failed_handoff = _build_failed_native_operational_handoff(
                args=args,
                context=blocked_context,
                reason=reason,
                run_payload=None,
            )
            preview_payload = _with_pre_publish_preview_metadata(
                payload=failed_handoff,
                evidence_path=evidence_path,
            )
            preview_payload = _write_pre_publish_preview_output(
                args=args,
                output_path=output_path,
                payload=preview_payload,
            )
            json.dump(preview_payload, output, ensure_ascii=False)
            output.write("\n")
            return 1
        approval = _pre_publish_approval_check(
            args=args,
            dest=dest,
            cached_evidence=cached_evidence,
            focus_etf_ids=focus_etf_ids,
            model_client_factory=model_client_factory,
            external_evidence_provider_overrides=external_evidence_provider_overrides,
            now=readiness_now,
        )
        if approval is not None and not _approval_is_valid(approval):
            external_evidence_summary_path = _pre_publish_existing_or_not_run_summary(
                evidence_summary_path
            )
            external_evidence_summary = _read_cli_json_object(
                external_evidence_summary_path,
                label="external evidence summary",
            )
            exclusion_summary_path = dest / "provider_etf_exclusion_summary.json"
            exclusion_summary = _provider_etf_exclusion_summary(
                universe_state_path=Path(args.universe_state_path),
                collection_summary=export_summary,
            )
            _write_native_handoff_payload(exclusion_summary_path, exclusion_summary)
            source_acquisition_summary_path = (
                Path(args.history_dir) / SOURCE_ACQUISITION_SUMMARY_FILENAME
            )
            source_acquisition_summary = (
                _read_cli_json_object(
                    source_acquisition_summary_path,
                    label="source acquisition summary",
                )
                if source_acquisition_summary_path.is_file()
                else None
            )
            blocked_context: dict[str, object] = {
                "dest": dest,
                "sqlite_path": Path(args.sqlite_path)
                if args.sqlite_path
                else dest / "runtime.sqlite3",
                "artifact_root": Path(args.artifact_root)
                if args.artifact_root
                else dest / "artifacts",
                "output_path": output_path,
                "manifest_path": manifest_path,
                "collection_summary_path": manifest_path.parent
                / "collection_summary.json",
                "readiness_path": readiness_path,
                "readiness": readiness,
                "export_summary": export_summary,
                "external_evidence_summary_path": external_evidence_summary_path,
                "external_evidence_summary": external_evidence_summary,
                "source_acquisition_summary_path": source_acquisition_summary_path,
                "source_acquisition_summary": source_acquisition_summary,
                "exclusion_summary_path": exclusion_summary_path,
                "exclusion_summary": exclusion_summary,
                "security_resolution_path": security_resolution_path,
            }
            failed_handoff = _build_failed_native_operational_handoff(
                args=args,
                context=blocked_context,
                reason="external_data_approval_required",
                run_payload=None,
            )
            preview_payload = _with_pre_publish_preview_metadata(
                payload=failed_handoff,
                evidence_path=evidence_path,
            )
            preview_payload = _with_daily_approval_metadata(
                payload=preview_payload,
                approval=approval,
            )
            preview_payload = _path_safe_pre_publish_handoff(preview_payload)
            await _record_blocked_daily_approval_governance(
                run_id=str(args.run_id),
                sqlite_path=_as_path(blocked_context["sqlite_path"]),
                approval=approval,
            )
            preview_payload = _write_pre_publish_preview_output(
                args=args,
                output_path=output_path,
                payload=preview_payload,
            )
            json.dump(preview_payload, output, ensure_ascii=False)
            output.write("\n")
            return 1
        if cached_evidence:
            _validate_external_evidence_input(evidence_path)
            summary = _read_cli_json_object(
                evidence_summary_path,
                label="external evidence summary",
            )
            _validate_external_evidence_summary(summary)
            _validate_cached_external_evidence_for_preview(
                evidence_path=evidence_path,
                summary=summary,
                allowed_claim_scopes=_pre_publish_signal_board_claim_scopes(
                    args=args,
                    manifest_path=manifest_path,
                ),
            )
            _annotate_pre_publish_external_evidence_summary(
                summary_path=evidence_summary_path,
                args=args,
                provider_ids=None,
                manifest_path=manifest_path,
                approval=approval,
                evidence_reuse={
                    "status": "not_applicable",
                    "scope": "explicit_cached_evidence",
                    "reason": "operator_supplied_cached_evidence",
                    "reused_provider_ids": [],
                },
            )
        else:
            provider_ids = _pre_publish_provider_ids(args)
            await _collect_or_reuse_pre_publish_external_evidence(
                args=args,
                manifest_path=manifest_path,
                evidence_path=evidence_path,
                summary_path=evidence_summary_path,
                provider_ids=provider_ids,
                focus_etf_ids=focus_etf_ids or (),
                approval=approval,
                collection_now=collection_now,
                external_evidence_provider_overrides=(
                    external_evidence_provider_overrides or {}
                ),
            )
        native_args = argparse.Namespace(
            command="run-native-operational-handoff",
            run_id=args.run_id,
            history_dir=args.history_dir,
            universe_state_path=args.universe_state_path,
            dest=args.dest,
            export_dir=str(export_dir),
            resume_export_path=str(manifest_path),
            focus_etf_id=args.focus_etf_id,
            focus_etf_set_path=args.focus_etf_set_path,
            observed_partitions=args.observed_partitions,
            security_resolution_path=args.security_resolution_path,
            use_default_security_resolution=args.use_default_security_resolution,
            evidence_path=str(evidence_path),
            evidence_summary_path=str(evidence_summary_path),
            sqlite_path=args.sqlite_path,
            artifact_root=args.artifact_root,
            output_path=str(output_path),
            model=args.model,
            codex_model=args.codex_model,
            model_timeout_seconds=args.model_timeout_seconds,
            allow_operator_review_output=(
                args.allow_operator_review_output or readiness.get("status") == "hold"
            ),
            approval_path=args.approval_path,
            write_preflight=args.write_preflight,
            require_verified_operational_flow_acceptance=False,
        )
    except Exception as exc:
        _write_cli_input_error(error_output, str(exc))
        return 2

    native_stdout = StringIO()
    native_call = _run_native_operational_handoff_command(
        native_args,
        output=native_stdout,
        error_output=error_output,
        model_client_factory=model_client_factory,
        readiness_now=readiness_now,
        collection_now=collection_now,
    )
    try:
        if bool(args.allow_preview_timeout_overrun):
            native_exit = await native_call
        else:
            native_exit = await asyncio.wait_for(
                native_call,
                timeout=float(args.preview_timeout_seconds),
            )
    except TimeoutError:
        timeout_payload = _pre_publish_timeout_handoff(
            args=args,
            dest=Path(args.dest),
            output_path=output_path,
            manifest_path=manifest_path,
            collection_summary_path=manifest_path.parent / "collection_summary.json",
            readiness_path=readiness_path,
            readiness=readiness,
            export_summary=export_summary,
            evidence_path=evidence_path,
            external_evidence_summary_path=evidence_summary_path,
            security_resolution_path=security_resolution_path,
        )
        timeout_payload = _write_pre_publish_preview_output(
            args=args,
            output_path=output_path,
            payload=timeout_payload,
        )
        json.dump(timeout_payload, output, ensure_ascii=False)
        output.write("\n")
        return 1
    payload = _json_object_from_text(native_stdout.getvalue())
    if payload is None:
        return native_exit
    preview_payload = _with_pre_publish_preview_metadata(
        payload=payload,
        evidence_path=evidence_path,
    )
    approval = _pre_publish_approval_check(
        args=args,
        dest=Path(args.dest),
        cached_evidence=bool(args.cached_evidence_path or args.evidence_path),
        focus_etf_ids=_focus_etf_ids_from_args(args) or (),
        model_client_factory=model_client_factory,
        external_evidence_provider_overrides=external_evidence_provider_overrides,
        now=readiness_now,
    )
    if approval is not None:
        preview_payload = _with_daily_approval_metadata(
            payload=preview_payload,
            approval=approval,
        )
        preview_payload = _path_safe_pre_publish_handoff(preview_payload)
    preview_payload = _write_pre_publish_preview_output(
        args=args,
        output_path=output_path,
        payload=preview_payload,
    )
    json.dump(preview_payload, output, ensure_ascii=False)
    output.write("\n")
    return int(preview_payload.get("exit_code", native_exit))


def _deliver_telegram_alert_command(
    args: argparse.Namespace,
    *,
    output: TextIO,
    error_output: TextIO,
    telegram_client_factory: TelegramClientFactory | None,
    now: Callable[[], datetime] | None,
) -> int:
    try:
        preflight_path = _telegram_delivery_preflight_path(args)
        approval_path = Path(args.approval_path) if args.approval_path else None
        exit_code, payload = run_telegram_delivery(
            handoff_path=Path(args.handoff_path),
            target_alias=str(args.target_alias),
            approval_path=approval_path,
            preflight_path=preflight_path,
            live=bool(args.live),
            retry_failed_delivery=bool(args.retry_failed_delivery),
            telegram_client_factory=telegram_client_factory,
            now=now,
        )
    except TelegramDeliveryInputError as exc:
        _write_cli_input_error(error_output, str(exc))
        return 2
    json.dump(payload, output, ensure_ascii=False)
    output.write("\n")
    return exit_code


def _verify_daily_publish_closure_command(
    args: argparse.Namespace,
    *,
    output: TextIO,
    error_output: TextIO,
    now: Callable[[], datetime] | None,
) -> int:
    try:
        payload = verify_daily_publish_closure(
            package_path=Path(args.package_path),
            now=now,
        )
    except DailyPublishClosureInputError as exc:
        _write_cli_input_error(error_output, str(exc))
        return 2
    json.dump(payload, output, ensure_ascii=False)
    output.write("\n")
    return 0


async def _project_delivery_closure_review_command(
    args: argparse.Namespace,
    *,
    output: TextIO,
    error_output: TextIO,
) -> int:
    try:
        payload = await _project_delivery_closure_review(
            package_path=Path(args.package_path),
            sqlite_path=Path(args.sqlite_path),
            run_id=args.run_id,
            subject_id=args.subject_id,
        )
    except (DailyPublishClosureInputError, ValueError) as exc:
        _write_cli_input_error(error_output, str(exc))
        return 2
    json.dump(payload, output, ensure_ascii=False)
    output.write("\n")
    return 0


async def _project_delivery_closure_review(
    *,
    package_path: Path,
    sqlite_path: Path,
    run_id: str,
    subject_id: str,
) -> dict[str, JsonValue]:
    delivery_summary = _read_delivery_closure_review_json(
        package_path / "telegram_delivery_summary.json",
        label="telegram delivery summary",
    )
    daily_publish_closure = _read_delivery_closure_review_json(
        package_path / "daily_publish_closure.json",
        label="daily publish closure",
    )
    review_summary = build_external_delivery_review_summary(
        delivery_summary=delivery_summary,
        daily_publish_closure=daily_publish_closure,
        subject_id=subject_id,
    )
    if review_summary["run_id"] != run_id:
        raise DailyPublishClosureInputError(
            "delivery closure review run_id does not match target run_id"
        )

    store = SQLiteRunStore(str(sqlite_path))
    try:
        if await store.get_run(run_id) is None:
            raise DailyPublishClosureInputError(f"run not found: {run_id}")
        latest_snapshot = await store.get_latest_snapshot(run_id)
        if latest_snapshot is None:
            raise DailyPublishClosureInputError(f"run snapshot not found: {run_id}")
        state = dict(latest_snapshot.state)
        summaries = _upsert_review_summary(
            state.get("agent_pack_review_summaries"),
            review_summary,
        )
        state["agent_pack_review_summaries"] = summaries
        await store.save_snapshot(
            RunSnapshot(
                run_id=latest_snapshot.run_id,
                step_index=latest_snapshot.step_index,
                state=state,
                pending_human_request_ids=latest_snapshot.pending_human_request_ids,
            )
        )
    finally:
        await store.close()

    return {
        "run_id": run_id,
        "subject_id": subject_id,
        "review_summary": review_summary,
        "review_summary_count": len(summaries),
    }


def _read_delivery_closure_review_json(
    path: Path,
    *,
    label: str,
) -> dict[str, JsonValue]:
    if not path.is_file():
        raise DailyPublishClosureInputError(f"{label} file not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DailyPublishClosureInputError(
            f"invalid {label} JSON input: {path}: {exc.msg}"
        ) from exc
    if not isinstance(payload, dict):
        raise DailyPublishClosureInputError(
            f"{label} input must be a JSON object: {path}"
        )
    return payload


def _upsert_review_summary(
    value: object,
    review_summary: dict[str, JsonValue],
) -> list[JsonValue]:
    if value is None:
        existing: list[JsonValue] = []
    elif isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        existing = list(value)
    else:
        raise DailyPublishClosureInputError(
            "agent_pack_review_summaries must be a JSON array"
        )

    def is_same_summary(item: object) -> bool:
        return (
            isinstance(item, Mapping)
            and item.get("id") == review_summary["id"]
            and item.get("operation_kind") == review_summary["operation_kind"]
            and item.get("review_surface") == review_summary["review_surface"]
            and item.get("subject_id") == review_summary["subject_id"]
        )

    return [
        *[item for item in existing if not is_same_summary(item)],
        review_summary,
    ]


def _telegram_delivery_preflight_path(args: argparse.Namespace) -> Path | None:
    value = getattr(args, "write_preflight", None)
    if isinstance(value, str) and value:
        return Path(value)
    return None


def _pre_publish_evidence_paths(
    args: argparse.Namespace,
    *,
    dest: Path,
) -> tuple[Path, Path, bool]:
    evidence_path = args.cached_evidence_path or args.evidence_path
    evidence_summary_path = args.cached_evidence_summary_path or args.evidence_summary_path
    cached_evidence = evidence_path is not None
    if evidence_path is None:
        evidence_path = str(dest / "external_evidence.json")
    if evidence_summary_path is None:
        evidence_summary_path = str(_default_evidence_summary_path(Path(evidence_path)))
    return Path(evidence_path), Path(evidence_summary_path), cached_evidence


async def _collect_or_reuse_pre_publish_external_evidence(
    *,
    args: argparse.Namespace,
    manifest_path: Path,
    evidence_path: Path,
    summary_path: Path,
    provider_ids: Sequence[str],
    focus_etf_ids: Sequence[str],
    approval: Mapping[str, object] | None,
    collection_now: Callable[[], datetime] | None,
    external_evidence_provider_overrides: ExternalEvidenceProviderOverrides,
) -> None:
    target_fingerprint = _pre_publish_target_identity_fingerprint(
        args=args,
        manifest_path=manifest_path,
    )
    smoke_boundary = _pre_publish_smoke_boundary(
        args=args,
        provider_ids=provider_ids,
        target_identity_fingerprint=target_fingerprint,
        approval=approval,
    )
    reuse_plan = _pre_publish_evidence_reuse_plan(
        evidence_path=evidence_path,
        summary_path=summary_path,
        smoke_boundary=smoke_boundary,
        provider_ids=provider_ids,
    )
    collect_provider_ids = tuple(_json_text_list(reuse_plan.get("collect_provider_ids")))
    reused_provider_ids = tuple(_json_text_list(reuse_plan.get("reused_provider_ids")))
    if not collect_provider_ids:
        _annotate_pre_publish_external_evidence_summary(
            summary_path=summary_path,
            args=args,
            provider_ids=provider_ids,
            manifest_path=manifest_path,
            approval=approval,
            evidence_reuse=_pre_publish_reuse_summary(
                status="reused",
                reason="matching_smoke_boundary",
                reused_provider_ids=reused_provider_ids,
                collected_provider_ids=(),
            ),
            smoke_boundary=smoke_boundary,
        )
        return

    target_evidence_path = evidence_path
    target_summary_path = summary_path
    previous_evidence: list[JsonValue] = []
    previous_summary: dict[str, JsonValue] | None = None
    if reused_provider_ids:
        previous_evidence = _read_json_array(evidence_path)
        previous_summary = _read_cli_json_object(
            summary_path,
            label="external evidence summary",
        )
        target_evidence_path = evidence_path.with_name(
            f"{evidence_path.stem}.incremental.json"
        )
        target_summary_path = summary_path.with_name(
            f"{summary_path.stem}.incremental.json"
        )
    try:
        await asyncio.to_thread(
            collect_external_evidence,
            ExternalEvidenceRequest(
                holdings_source="operational",
                holdings_path=manifest_path,
                provider_ids=collect_provider_ids,
                live=True,
                max_targets=args.max_evidence_targets,
                evidence_path=target_evidence_path,
                summary_path=target_summary_path,
                focus_etf_id=args.focus_etf_id,
                focus_etf_ids=focus_etf_ids,
                observed_partitions=args.observed_partitions,
                provider_overrides=external_evidence_provider_overrides,
                now=collection_now,
            ),
        )
    except ExternalEvidenceCollectionError:
        pass

    evidence_reuse = _pre_publish_reuse_summary(
        status="partially_reused" if reused_provider_ids else "not_reused",
        reason=(
            "matching_smoke_boundary"
            if reused_provider_ids
            else str(reuse_plan["reason"])
        ),
        reused_provider_ids=reused_provider_ids,
        collected_provider_ids=collect_provider_ids,
    )
    if previous_summary is not None and target_summary_path.is_file():
        new_summary = _read_cli_json_object(
            target_summary_path,
            label="incremental external evidence summary",
        )
        incremental_evidence = _read_json_array(target_evidence_path)
        _write_json_array(evidence_path, [*previous_evidence, *incremental_evidence])
        merged = _merge_pre_publish_external_evidence_summaries(
            previous_summary=previous_summary,
            new_summary=new_summary,
            provider_ids=provider_ids,
            reused_provider_ids=reused_provider_ids,
            evidence_path=evidence_path,
            evidence_reuse=evidence_reuse,
            smoke_boundary=smoke_boundary,
        )
        _write_native_handoff_payload(summary_path, merged)
        return

    if target_summary_path != summary_path and target_summary_path.is_file():
        target_summary_path.replace(summary_path)
    if target_evidence_path != evidence_path and target_evidence_path.is_file():
        target_evidence_path.replace(evidence_path)
    if summary_path.is_file():
        _annotate_pre_publish_external_evidence_summary(
            summary_path=summary_path,
            args=args,
            provider_ids=provider_ids,
            manifest_path=manifest_path,
            approval=approval,
            evidence_reuse=evidence_reuse,
            smoke_boundary=smoke_boundary,
        )


def _pre_publish_evidence_reuse_plan(
    *,
    evidence_path: Path,
    summary_path: Path,
    smoke_boundary: Mapping[str, JsonValue],
    provider_ids: Sequence[str],
) -> dict[str, object]:
    provider_id_tuple = tuple(provider_ids)
    base = {
        "reused_provider_ids": (),
        "collect_provider_ids": provider_id_tuple,
    }
    if not evidence_path.is_file() or not summary_path.is_file():
        return {**base, "reason": "no_existing_evidence"}
    summary = _read_cli_json_object(summary_path, label="external evidence summary")
    previous_boundary = summary.get("smoke_boundary")
    if not isinstance(previous_boundary, Mapping):
        return {**base, "reason": "no_smoke_boundary"}
    if dict(previous_boundary) != dict(smoke_boundary):
        return {**base, "reason": "smoke_boundary_mismatch"}
    reusable = tuple(
        provider_id
        for provider_id in provider_id_tuple
        if _pre_publish_provider_status(summary, provider_id)
        in PRE_PUBLISH_PROVIDER_SUCCESS_STATUSES
    )
    collect = tuple(provider_id for provider_id in provider_id_tuple if provider_id not in reusable)
    if not reusable:
        return {**base, "reason": "no_reusable_provider_outcomes"}
    return {
        "reason": "matching_smoke_boundary",
        "reused_provider_ids": reusable,
        "collect_provider_ids": collect,
    }


def _pre_publish_provider_status(
    summary: Mapping[str, JsonValue],
    provider_id: str,
) -> str | None:
    provider_outcomes = summary.get("provider_outcomes")
    if not isinstance(provider_outcomes, list):
        return None
    for outcome in provider_outcomes:
        if not isinstance(outcome, Mapping):
            continue
        if outcome.get("provider_id") != provider_id:
            continue
        status = outcome.get("status")
        return status if isinstance(status, str) else None
    return None


def _pre_publish_reuse_summary(
    *,
    status: str,
    reason: str,
    reused_provider_ids: Sequence[str],
    collected_provider_ids: Sequence[str],
) -> dict[str, JsonValue]:
    return {
        "status": status,
        "scope": "same_smoke",
        "reason": reason,
        "reused_provider_ids": list(reused_provider_ids),
        **(
            {"collected_provider_ids": list(collected_provider_ids)}
            if collected_provider_ids
            else {}
        ),
    }


def _merge_pre_publish_external_evidence_summaries(
    *,
    previous_summary: Mapping[str, JsonValue],
    new_summary: Mapping[str, JsonValue],
    provider_ids: Sequence[str],
    reused_provider_ids: Sequence[str],
    evidence_path: Path,
    evidence_reuse: Mapping[str, JsonValue],
    smoke_boundary: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    old_outcomes = _provider_outcome_map(previous_summary)
    new_outcomes = _provider_outcome_map(new_summary)
    merged_outcomes: list[JsonValue] = []
    for provider_id in provider_ids:
        source = old_outcomes if provider_id in set(reused_provider_ids) else new_outcomes
        outcome = source.get(provider_id)
        if isinstance(outcome, Mapping):
            merged_outcomes.append(dict(outcome))
    merged = dict(new_summary)
    merged["provider_outcomes"] = merged_outcomes
    merged["policy_failure"] = _pre_publish_first_policy_failure(merged_outcomes)
    merged["evidence_path"] = str(evidence_path)
    previous_limitations = previous_summary.get("provider_limitations")
    new_limitations = new_summary.get("provider_limitations")
    merged["provider_limitations"] = [
        *(previous_limitations if isinstance(previous_limitations, list) else []),
        *(new_limitations if isinstance(new_limitations, list) else []),
    ]
    _apply_pre_publish_external_summary_metadata(
        merged,
        provider_ids=provider_ids,
        evidence_reuse=evidence_reuse,
        smoke_boundary=smoke_boundary,
    )
    return merged


def _provider_outcome_map(
    summary: Mapping[str, JsonValue],
) -> dict[str, Mapping[str, JsonValue]]:
    outcomes = summary.get("provider_outcomes")
    if not isinstance(outcomes, list):
        return {}
    result: dict[str, Mapping[str, JsonValue]] = {}
    for outcome in outcomes:
        if not isinstance(outcome, Mapping):
            continue
        provider_id = outcome.get("provider_id")
        if isinstance(provider_id, str):
            result[provider_id] = outcome
    return result


def _pre_publish_first_policy_failure(
    outcomes: Sequence[JsonValue],
) -> dict[str, JsonValue] | None:
    for outcome in outcomes:
        if not isinstance(outcome, Mapping):
            continue
        status = outcome.get("status")
        if status not in PRE_PUBLISH_PROVIDER_FAILURE_STATUSES:
            continue
        provider_id = outcome.get("provider_id")
        category = outcome.get("category")
        if not isinstance(provider_id, str) or not isinstance(category, str):
            continue
        raw_error_code = outcome.get("error_code")
        return {
            "error_code": raw_error_code if isinstance(raw_error_code, str) else status,
            "provider_id": provider_id,
            "category": category,
            "safe_message": outcome.get("safe_message")
            if isinstance(outcome.get("safe_message"), str)
            else f"{provider_id} provider failed.",
        }
    return None


def _annotate_pre_publish_external_evidence_summary(
    *,
    summary_path: Path,
    args: argparse.Namespace,
    provider_ids: Sequence[str] | None,
    manifest_path: Path,
    approval: Mapping[str, object] | None,
    evidence_reuse: Mapping[str, JsonValue],
    smoke_boundary: Mapping[str, JsonValue] | None = None,
) -> None:
    summary = _read_cli_json_object(summary_path, label="external evidence summary")
    boundary = smoke_boundary or _pre_publish_smoke_boundary(
        args=args,
        provider_ids=provider_ids or _pre_publish_required_provider_ids_from_summary(summary),
        target_identity_fingerprint=_pre_publish_target_identity_fingerprint(
            args=args,
            manifest_path=manifest_path,
        ),
        approval=approval,
    )
    _apply_pre_publish_external_summary_metadata(
        summary,
        provider_ids=provider_ids or _pre_publish_required_provider_ids_from_summary(summary),
        evidence_reuse=evidence_reuse,
        smoke_boundary=boundary,
    )
    _write_native_handoff_payload(summary_path, summary)


def _apply_pre_publish_external_summary_metadata(
    summary: dict[str, JsonValue],
    *,
    provider_ids: Sequence[str],
    evidence_reuse: Mapping[str, JsonValue],
    smoke_boundary: Mapping[str, JsonValue],
) -> None:
    summary["required_provider_ids"] = list(dict.fromkeys(provider_ids))
    summary["known_unvalidated_provider_exceptions"] = [
        dict(item) for item in PRE_PUBLISH_KNOWN_UNVALIDATED_PROVIDER_EXCEPTIONS
    ]
    summary["provider_limitations"] = _pre_publish_provider_limitations(summary)
    summary["evidence_reuse"] = dict(evidence_reuse)
    summary["smoke_boundary"] = dict(smoke_boundary)


def _pre_publish_provider_limitations(
    summary: Mapping[str, JsonValue],
) -> list[dict[str, JsonValue]]:
    existing = summary.get("provider_limitations")
    if isinstance(existing, list):
        return [dict(item) for item in existing if isinstance(item, Mapping)]
    limitations: list[dict[str, JsonValue]] = []
    outcomes = summary.get("provider_outcomes")
    if not isinstance(outcomes, list):
        return limitations
    for outcome in outcomes:
        if not isinstance(outcome, Mapping):
            continue
        metadata = outcome.get("metadata")
        if not isinstance(metadata, Mapping):
            continue
        omitted = metadata.get("omitted_target_count")
        if not isinstance(omitted, int) or omitted <= 0:
            continue
        provider_id = outcome.get("provider_id")
        category = outcome.get("category")
        limitations.append(
            {
                "provider_id": provider_id if isinstance(provider_id, str) else "unknown",
                "category": category if isinstance(category, str) else "unknown",
                "limitation_type": "provider_target_cap",
                "provider_target_cap": metadata.get("provider_target_cap"),
                "requested_target_count": metadata.get("requested_target_count"),
                "queried_target_count": metadata.get("queried_target_count"),
                "omitted_target_count": omitted,
                "policy": metadata.get("policy"),
            }
        )
    return limitations


def _pre_publish_smoke_boundary(
    *,
    args: argparse.Namespace,
    provider_ids: Sequence[str],
    target_identity_fingerprint: str,
    approval: Mapping[str, object] | None,
) -> dict[str, JsonValue]:
    approval_summary = _approval_summary(approval or {})
    requested_fingerprint = approval_summary.get("requested_boundary_fingerprint")
    approved_fingerprint = approval_summary.get("approved_boundary_fingerprint")
    return {
        "run_id": str(args.run_id),
        "requested_provider_ids": list(dict.fromkeys(provider_ids)),
        "evidence_category": "pre_publish_external_evidence",
        "target_identity_fingerprint": target_identity_fingerprint,
        "approval_boundary_fingerprint": requested_fingerprint
        if isinstance(requested_fingerprint, str)
        else None,
        "approved_boundary_fingerprint": approved_fingerprint
        if isinstance(approved_fingerprint, str)
        else None,
    }


def _pre_publish_target_identity_fingerprint(
    *,
    args: argparse.Namespace,
    manifest_path: Path,
) -> str:
    claim_scopes = sorted(
        _pre_publish_signal_board_claim_scopes(args=args, manifest_path=manifest_path)
    )
    payload = {
        "claim_scopes": claim_scopes,
        "focus_etf_id": args.focus_etf_id,
        "focus_etf_ids": list(_focus_etf_ids_from_args(args) or ()),
        "max_evidence_targets": int(args.max_evidence_targets),
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _pre_publish_required_provider_ids_from_summary(
    summary: Mapping[str, JsonValue],
) -> tuple[str, ...]:
    explicit = summary.get("required_provider_ids")
    if isinstance(explicit, list):
        return tuple(item for item in explicit if isinstance(item, str))
    outcomes = summary.get("provider_outcomes")
    if not isinstance(outcomes, list):
        return ()
    exceptions = _pre_publish_known_exception_provider_ids()
    provider_ids: list[str] = []
    for outcome in outcomes:
        if not isinstance(outcome, Mapping):
            continue
        provider_id = outcome.get("provider_id")
        if isinstance(provider_id, str) and provider_id not in exceptions:
            provider_ids.append(provider_id)
    return tuple(dict.fromkeys(provider_ids))


def _pre_publish_known_exception_provider_ids() -> set[str]:
    return {
        str(item["provider_id"])
        for item in PRE_PUBLISH_KNOWN_UNVALIDATED_PROVIDER_EXCEPTIONS
        if isinstance(item.get("provider_id"), str)
    }


def _read_json_array(path: Path) -> list[JsonValue]:
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else []


def _write_json_array(path: Path, payload: Sequence[JsonValue]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(list(payload), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _pre_publish_timeout_handoff(
    *,
    args: argparse.Namespace,
    dest: Path,
    output_path: Path,
    manifest_path: Path,
    collection_summary_path: Path,
    readiness_path: Path,
    readiness: Mapping[str, JsonValue],
    export_summary: Mapping[str, JsonValue],
    evidence_path: Path,
    external_evidence_summary_path: Path,
    security_resolution_path: str | None,
) -> dict[str, JsonValue]:
    external_evidence_summary_path = _pre_publish_existing_or_not_run_summary(
        external_evidence_summary_path
    )
    external_evidence_summary = _read_cli_json_object(
        external_evidence_summary_path,
        label="external evidence summary",
    )
    exclusion_summary_path = dest / "provider_etf_exclusion_summary.json"
    exclusion_summary = _provider_etf_exclusion_summary(
        universe_state_path=Path(args.universe_state_path),
        collection_summary=export_summary,
    )
    _write_native_handoff_payload(exclusion_summary_path, exclusion_summary)
    source_acquisition_summary_path = (
        Path(args.history_dir) / SOURCE_ACQUISITION_SUMMARY_FILENAME
    )
    source_acquisition_summary = (
        _read_cli_json_object(
            source_acquisition_summary_path,
            label="source acquisition summary",
        )
        if source_acquisition_summary_path.is_file()
        else None
    )
    context: dict[str, object] = {
        "dest": dest,
        "sqlite_path": Path(args.sqlite_path) if args.sqlite_path else dest / "runtime.sqlite3",
        "artifact_root": Path(args.artifact_root) if args.artifact_root else dest / "artifacts",
        "output_path": output_path,
        "manifest_path": manifest_path,
        "collection_summary_path": collection_summary_path,
        "readiness_path": readiness_path,
        "readiness": readiness,
        "export_summary": export_summary,
        "external_evidence_summary_path": external_evidence_summary_path,
        "external_evidence_summary": external_evidence_summary,
        "source_acquisition_summary_path": source_acquisition_summary_path,
        "source_acquisition_summary": source_acquisition_summary,
        "exclusion_summary_path": exclusion_summary_path,
        "exclusion_summary": exclusion_summary,
        "security_resolution_path": security_resolution_path,
    }
    failed_handoff = _build_failed_native_operational_handoff(
        args=args,
        context=context,
        reason="preview_timeout",
        run_payload=None,
    )
    result = _with_pre_publish_preview_metadata(
        payload=failed_handoff,
        evidence_path=evidence_path,
    )
    result["timeout"] = {
        "status": "timed_out",
        "timeout_seconds": float(args.preview_timeout_seconds),
        "operator_override_available": True,
        "operator_override": "--allow-preview-timeout-overrun",
    }
    return result


def _pre_publish_provider_ids(args: argparse.Namespace) -> tuple[str, ...]:
    raw_values = args.evidence_providers or [",".join(DEFAULT_PRE_PUBLISH_EVIDENCE_PROVIDERS)]
    provider_ids: list[str] = []
    for value in raw_values:
        provider_ids.extend(part.strip() for part in value.split(",") if part.strip())
    result = tuple(
        provider_id
        for provider_id in dict.fromkeys(provider_ids)
        if provider_id not in _pre_publish_known_exception_provider_ids()
    )
    if not result:
        raise SignalReportInputError("at least one external evidence provider is required")
    unknown = sorted(set(provider_ids).difference(EXTERNAL_EVIDENCE_PROVIDER_IDS))
    if unknown:
        raise SignalReportInputError(
            f"unsupported external evidence provider: {unknown[0]}"
        )
    return result


def _external_evidence_provider_ids(raw_values: Sequence[str]) -> tuple[str, ...]:
    provider_ids: list[str] = []
    for value in raw_values:
        provider_ids.extend(part.strip() for part in value.split(",") if part.strip())
    result = tuple(dict.fromkeys(provider_ids))
    if not result:
        raise SignalReportInputError("at least one external evidence provider is required")
    unknown = sorted(set(result).difference(EXTERNAL_EVIDENCE_PROVIDER_IDS))
    if unknown:
        raise SignalReportInputError(
            f"unsupported external evidence provider: {unknown[0]}"
        )
    return result


def _external_evidence_approval_check(
    *,
    args: argparse.Namespace,
    provider_ids: Sequence[str],
    summary_path: Path,
    model_client_factory: ModelClientFactory | None,
    external_evidence_provider_overrides: ExternalEvidenceProviderOverrides | None,
) -> dict[str, object] | None:
    required_scopes: list[str] = []
    real_provider_ids: tuple[str, ...] = ()
    if bool(args.live):
        real_provider_ids = _real_external_evidence_provider_ids(
            provider_ids,
            provider_overrides=external_evidence_provider_overrides,
        )
        if real_provider_ids:
            required_scopes.append("live_external_evidence")
    model_exports: list[dict[str, JsonValue]] = []
    if bool(args.align_claims) and args.model == "codex" and model_client_factory is None:
        required_scopes.append("model_export")
        model_exports.append(
            {
                "provider": "codex",
                "model": args.codex_model or "default",
                "export_scope": REPORT_MODEL_EXPORT_SCOPE,
            }
        )
    if not required_scopes:
        return None
    boundary = build_daily_approval_boundary(
        required_scopes=required_scopes,
        external_evidence_provider_ids=real_provider_ids,
        model_exports=model_exports,
        approved_max_target_count=int(args.max_targets),
        data_classes=_external_evidence_data_classes(
            include_live_evidence=bool(real_provider_ids),
            include_model_export=bool(model_exports),
        ),
    )
    preflight_path = _daily_preflight_path(args, dest=summary_path.parent)
    _preflight, template_path, _template = write_preflight_and_template(
        preflight_path=preflight_path,
        command="collect-external-evidence",
        boundary=boundary,
        generated_at=None,
    )
    summary = evaluate_approval_profile(
        approval_path=Path(args.approval_path or DEFAULT_APPROVAL_PROFILE_PATH),
        boundary=boundary,
    )
    return {
        "summary": summary,
        "preflight_path": preflight_path,
        "template_path": template_path,
    }


def _external_evidence_data_classes(
    *,
    include_live_evidence: bool,
    include_model_export: bool,
) -> tuple[str, ...]:
    classes = [
        "external_evidence_target_identifiers",
        "safe_selected_security_identifiers",
        "safe_selected_ticker_identifiers",
        "evidence_category_requests",
    ]
    if include_live_evidence:
        classes.extend(
            [
                "live_external_evidence_queries",
                "provider_outcome_summary",
                "compiled_external_evidence",
            ]
        )
    if include_model_export:
        classes.extend(
            [
                "claim_alignment_candidates",
                "model_alignment_prompt_context",
            ]
        )
    return tuple(classes)


def _pre_publish_approval_check(
    *,
    args: argparse.Namespace,
    dest: Path,
    cached_evidence: bool,
    focus_etf_ids: Sequence[str] | None,
    model_client_factory: ModelClientFactory | None,
    external_evidence_provider_overrides: ExternalEvidenceProviderOverrides | None,
    now: Callable[[], datetime] | None,
) -> dict[str, object] | None:
    provider_ids = () if cached_evidence else _pre_publish_provider_ids(args)
    real_provider_ids = _real_external_evidence_provider_ids(
        provider_ids,
        provider_overrides=external_evidence_provider_overrides,
    )
    required_scopes: list[str] = []
    if real_provider_ids:
        required_scopes.append("live_external_evidence")
    model_exports: list[dict[str, JsonValue]] = []
    if args.model == "codex" and model_client_factory is None:
        required_scopes.append("model_export")
        model_exports.append(
            {
                "provider": "codex",
                "model": args.codex_model or "default",
                "export_scope": REPORT_MODEL_EXPORT_SCOPE,
            }
        )
    if not required_scopes:
        return None
    boundary = build_daily_approval_boundary(
        required_scopes=required_scopes,
        external_evidence_provider_ids=real_provider_ids,
        known_unvalidated_provider_exceptions=(
            PRE_PUBLISH_KNOWN_UNVALIDATED_PROVIDER_EXCEPTIONS
            if real_provider_ids
            else ()
        ),
        model_exports=model_exports,
        live_source_provider_ids=LIVE_SOURCE_PROVIDER_IDS if real_provider_ids else (),
        live_source_cohort=LIVE_SOURCE_PROVIDER_IDS if real_provider_ids else (),
        approved_max_target_count=int(args.max_evidence_targets),
        data_classes=_daily_pre_publish_data_classes(
            include_live_evidence=bool(real_provider_ids),
            include_model_export=bool(model_exports),
        ),
    )
    preflight_path = _daily_preflight_path(args, dest=dest)
    _preflight, template_path, _template = write_preflight_and_template(
        preflight_path=preflight_path,
        command="run-pre-publish-preview",
        boundary=boundary,
        focus_etf_ids=focus_etf_ids or (),
        generated_at=now() if now is not None else None,
    )
    approval_path = Path(args.approval_path or DEFAULT_APPROVAL_PROFILE_PATH)
    summary = evaluate_approval_profile(
        approval_path=approval_path,
        boundary=boundary,
        now=now() if now is not None else None,
    )
    return {
        "summary": summary,
        "preflight_path": preflight_path,
        "template_path": template_path,
    }


def _real_external_evidence_provider_ids(
    provider_ids: Sequence[str],
    *,
    provider_overrides: ExternalEvidenceProviderOverrides | None,
) -> tuple[str, ...]:
    overrides = set(provider_overrides or {})
    return tuple(
        provider_id
        for provider_id in provider_ids
        if provider_id not in overrides and is_live_provider(provider_id)
    )


def _daily_preflight_path(args: argparse.Namespace, *, dest: Path) -> Path:
    value = getattr(args, "write_preflight", None)
    if isinstance(value, str) and value:
        return Path(value)
    return default_preflight_path(dest)


def _daily_pre_publish_data_classes(
    *,
    include_live_evidence: bool,
    include_model_export: bool,
) -> tuple[str, ...]:
    classes = [
        "focus_etf_identifiers",
        "safe_selected_security_identifiers",
        "safe_selected_ticker_identifiers",
        "normalized_holdings_change_summary",
        "operational_readiness_summary",
    ]
    if include_live_evidence:
        classes.extend(
            [
                "external_evidence_target_identifiers",
                "external_evidence_category_requests",
                "provider_outcome_summary",
                "compiled_external_evidence",
            ]
        )
    if include_model_export:
        classes.extend(
            [
                "signal_board_claim_scopes",
                "compiled_external_evidence",
                "compiled_report_payload",
                "model_commentary_prompt_context",
            ]
        )
    return tuple(classes)


def _with_daily_approval_metadata(
    *,
    payload: Mapping[str, JsonValue],
    approval: Mapping[str, object],
) -> dict[str, JsonValue]:
    result = dict(payload)
    summary = approval.get("summary")
    if isinstance(summary, Mapping):
        result["approval"] = _path_safe_approval_summary(summary)
    references = result.get("references")
    if isinstance(references, Mapping):
        updated_references = dict(references)
        artifacts = updated_references.get("artifacts")
        if isinstance(artifacts, Mapping):
            updated_artifacts = dict(artifacts)
        else:
            updated_artifacts = {}
        preflight_path = approval.get("preflight_path")
        if isinstance(preflight_path, Path):
            updated_artifacts["approval_preflight"] = _file_artifact_entry(
                artifact_id="artifact_treport_daily_approval_preflight",
                name=preflight_path.name,
                path=preflight_path,
            )
        template_path = approval.get("template_path")
        if isinstance(template_path, Path):
            updated_artifacts["approval_template"] = _file_artifact_entry(
                artifact_id="artifact_treport_daily_approval_template",
                name=template_path.name,
                path=template_path,
            )
        updated_references["artifacts"] = updated_artifacts
        result["references"] = updated_references
    return result


def _path_safe_approval_summary(
    summary: Mapping[str, object],
) -> dict[str, JsonValue]:
    return {
        "valid": bool(summary.get("valid")),
        "status": str(summary.get("status")),
        "required_scopes": _json_text_list(summary.get("required_scopes")),
        "missing_scopes": _json_text_list(summary.get("missing_scopes")),
        "unapproved_scopes": _json_text_list(summary.get("unapproved_scopes")),
        "outside_approved_bounds": _json_text_list(
            summary.get("outside_approved_bounds")
        ),
        "approved_boundary_fingerprint": (
            summary.get("approved_boundary_fingerprint")
            if isinstance(summary.get("approved_boundary_fingerprint"), str)
            else None
        ),
        "requested_boundary_fingerprint": (
            summary.get("requested_boundary_fingerprint")
            if isinstance(summary.get("requested_boundary_fingerprint"), str)
            else None
        ),
    }


def _json_text_list(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [item for item in value if isinstance(item, str)]


def _source_provider_approval_check(
    *,
    args: argparse.Namespace,
    command: str,
    required_scope: str,
    provider_ids: Sequence[str],
    preflight_base_dir: Path,
    approved_max_target_count: int,
    data_classes: Sequence[str],
    now: Callable[[], datetime] | None,
) -> dict[str, object]:
    boundary = build_daily_approval_boundary(
        required_scopes=(required_scope,),
        live_source_provider_ids=provider_ids,
        live_source_cohort=provider_ids,
        approved_max_target_count=approved_max_target_count,
        data_classes=data_classes,
    )
    preflight_path = _daily_preflight_path(args, dest=preflight_base_dir)
    _preflight, template_path, _template = write_preflight_and_template(
        preflight_path=preflight_path,
        command=command,
        boundary=boundary,
        generated_at=now() if now is not None else None,
    )
    summary = evaluate_approval_profile(
        approval_path=Path(args.approval_path or DEFAULT_APPROVAL_PROFILE_PATH),
        boundary=boundary,
        now=now() if now is not None else None,
    )
    return {
        "summary": summary,
        "preflight_path": preflight_path,
        "template_path": template_path,
    }


def _model_export_approval_check(
    *,
    args: argparse.Namespace,
    command: str,
    preflight_base_dir: Path,
    model_client_factory: ModelClientFactory | None,
) -> dict[str, object] | None:
    if args.model != "codex" or model_client_factory is not None:
        return None
    boundary = build_daily_approval_boundary(
        required_scopes=("model_export",),
        model_exports=(
            {
                "provider": "codex",
                "model": args.codex_model or "default",
                "export_scope": REPORT_MODEL_EXPORT_SCOPE,
            },
        ),
        approved_max_target_count=0,
        data_classes=(
            "normalized_holdings_change_summary",
            "signal_board_claim_scopes",
            "compiled_external_evidence",
            "model_commentary_prompt_context",
        ),
    )
    preflight_path = _daily_preflight_path(args, dest=preflight_base_dir)
    _preflight, template_path, _template = write_preflight_and_template(
        preflight_path=preflight_path,
        command=command,
        boundary=boundary,
    )
    summary = evaluate_approval_profile(
        approval_path=Path(args.approval_path or DEFAULT_APPROVAL_PROFILE_PATH),
        boundary=boundary,
    )
    return {
        "summary": summary,
        "preflight_path": preflight_path,
        "template_path": template_path,
    }


def _approval_summary(approval: Mapping[str, object]) -> Mapping[str, object]:
    summary = approval.get("summary")
    return summary if isinstance(summary, Mapping) else {}


def _approval_is_valid(approval: Mapping[str, object]) -> bool:
    return bool(_approval_summary(approval).get("valid"))


def _write_daily_approval_block(
    *,
    output: TextIO,
    command: str,
    blocked_path: Path,
    approval: Mapping[str, object],
) -> int:
    payload: dict[str, JsonValue] = {
        "schema_version": "agent_treport.daily_operational_external_data_block.v1",
        "status": "failed",
        "command": command,
        "delivery_blocked": True,
        "reason": "external_data_approval_required",
        "approval": _path_safe_approval_summary(_approval_summary(approval)),
        "references": {"artifacts": {}},
    }
    payload = _with_daily_approval_metadata(payload=payload, approval=approval)
    _write_native_handoff_payload(blocked_path, payload)
    json.dump(payload, output, ensure_ascii=False)
    output.write("\n")
    return 1


def _with_pre_publish_preview_metadata(
    *,
    payload: Mapping[str, JsonValue],
    evidence_path: Path,
) -> dict[str, JsonValue]:
    result = dict(payload)
    external_evidence = result.get("external_evidence")
    review_only_reason = _pre_publish_external_evidence_review_reason(
        external_evidence
    )
    if result.get("status") == "user_ready" and review_only_reason is not None:
        user_ready = result.pop("user_ready", None)
        result["status"] = "operator_review_only"
        result["delivery_blocked"] = True
        result["reason"] = review_only_reason
        result["user_ready_status"] = "not user-ready"
        if isinstance(user_ready, Mapping):
            result["operator_review_only"] = {
                **dict(user_ready),
                "delivery_blocked": True,
                "reason": review_only_reason,
                "user_ready_status": "not user-ready",
            }
    preview: dict[str, JsonValue] = {
        "type": "pre_publish",
        "telegram_delivery": "not_sent",
    }
    references = result.get("references")
    if isinstance(references, Mapping):
        updated_references = dict(references)
        artifacts = updated_references.get("artifacts")
        if isinstance(artifacts, Mapping):
            updated_artifacts = dict(artifacts)
            if evidence_path.is_file():
                updated_artifacts["external_evidence"] = _file_artifact_entry(
                    artifact_id="artifact_treport_external_evidence",
                    name="external_evidence.json",
                    path=evidence_path,
                )
            updated_references["artifacts"] = updated_artifacts
        result["references"] = updated_references
    telegram_message = _pre_publish_telegram_message(result)
    if telegram_message is not None:
        preview["telegram_message"] = telegram_message
    result["preview"] = preview
    result["closure"] = _pre_publish_closure_summary(
        result,
        telegram_message=telegram_message,
    )
    if result.get("status") == "failed":
        result["missing_artifacts"] = _pre_publish_missing_artifacts(result)
    return _path_safe_pre_publish_handoff(result)


def _write_pre_publish_preview_output(
    *,
    args: argparse.Namespace,
    output_path: Path,
    payload: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    package_path = Path(DEFAULT_DAILY_SMOKE_SUMMARY_ROOT) / str(args.run_id)
    updated = dict(payload)
    preview = updated.get("preview")
    preview_mapping = dict(preview) if isinstance(preview, Mapping) else {}
    preview_mapping["result_package_path"] = _path_safe_path_text(package_path)
    updated["preview"] = preview_mapping
    updated = _path_safe_pre_publish_handoff(updated)
    _write_native_handoff_payload(output_path, updated)
    _write_pre_publish_result_package(
        args=args,
        package_path=package_path,
        payload=updated,
    )
    return updated


def _write_pre_publish_result_package(
    *,
    args: argparse.Namespace,
    package_path: Path,
    payload: Mapping[str, JsonValue],
) -> None:
    package_path.mkdir(parents=True, exist_ok=True)
    _write_native_handoff_payload(package_path / "pre_publish_handoff.json", payload)
    _write_native_handoff_payload(
        package_path / "smoke_summary.json",
        _pre_publish_smoke_summary(args=args, payload=payload),
    )
    _write_native_handoff_payload(
        package_path / "approval_preflight_summary.json",
        _pre_publish_approval_preflight_summary(payload),
    )
    _write_native_handoff_payload(
        package_path / "external_evidence_summary.json",
        _pre_publish_package_external_evidence_summary(payload),
    )
    _write_native_handoff_payload(
        package_path / "provider_exception_summary.json",
        _pre_publish_provider_exception_summary(payload),
    )
    _write_native_handoff_payload(
        package_path / "validation_command_results.json",
        {
            "schema_version": "agent_treport.pre_publish.validation_command_results.v1",
            "status": "not_run",
            "commands": [],
        },
    )
    _write_native_handoff_payload(
        package_path / "canonical_history_unchanged.json",
        _pre_publish_canonical_history_unchanged(args),
    )
    retention = apply_live_source_rolling_retention(
        live_root=Path(DEFAULT_DAILY_SMOKE_SUMMARY_ROOT).parent,
        protected_run_dir_names=(str(args.run_id),),
    )
    _write_native_handoff_payload(package_path / "retention_summary.json", retention)


def _pre_publish_smoke_summary(
    *,
    args: argparse.Namespace,
    payload: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    closure = payload.get("closure")
    closure_mapping = closure if isinstance(closure, Mapping) else {}
    artifact_closure = closure_mapping.get("full_live_pre_publish_artifact_closure")
    user_closure = closure_mapping.get("full_user_ready_closure")
    external_evidence = payload.get("external_evidence")
    external_mapping = external_evidence if isinstance(external_evidence, Mapping) else {}
    preview = payload.get("preview")
    preview_mapping = preview if isinstance(preview, Mapping) else {}
    required_outcomes = external_mapping.get("required_provider_outcomes")
    telegram_message = preview_mapping.get("telegram_message")
    result_package_path = preview_mapping.get("result_package_path")
    canonical_history = _pre_publish_canonical_history_unchanged(args)
    return {
        "schema_version": "agent_treport.full_live_pre_publish_smoke_summary.v1",
        "run_id": str(args.run_id),
        "pre_publish_preview_status": str(payload.get("status")),
        "full_live_pre_publish_artifact_closure": _closure_status(artifact_closure),
        "full_user_ready_closure": _closure_status(user_closure),
        "validated_provider_outcomes": (
            dict(required_outcomes)
            if isinstance(required_outcomes, Mapping)
            else {}
        ),
        "known_unvalidated_provider_exceptions": (
            _json_list(external_mapping.get("known_unvalidated_provider_exceptions"))
        ),
        "telegram_delivery_status": str(preview_mapping.get("telegram_delivery")),
        "telegram_message_body_in_handoff": isinstance(
            telegram_message,
            Mapping,
        )
        and isinstance(telegram_message.get("text"), str),
        "canonical_holdings_history_unchanged": (
            canonical_history["canonical_history_mutated"] is False
        ),
        "result_package_path": result_package_path
        if isinstance(result_package_path, str)
        else None,
    }


def _closure_status(value: object) -> str:
    if isinstance(value, Mapping) and isinstance(value.get("status"), str):
        return str(value["status"])
    return "unknown"


def _pre_publish_approval_preflight_summary(
    payload: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    approval = payload.get("approval")
    references = payload.get("references")
    artifacts = references.get("artifacts") if isinstance(references, Mapping) else None
    approval_preflight = (
        artifacts.get("approval_preflight") if isinstance(artifacts, Mapping) else None
    )
    approval_summary = dict(approval) if isinstance(approval, Mapping) else {"status": "not_run"}
    preflight_artifact = (
        dict(approval_preflight) if isinstance(approval_preflight, Mapping) else None
    )
    return {
        "schema_version": "agent_treport.pre_publish.approval_preflight_summary.v1",
        "approval": approval_summary,
        "approval_preflight_artifact": preflight_artifact,
    }


def _pre_publish_package_external_evidence_summary(
    payload: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    external_evidence = payload.get("external_evidence")
    if not isinstance(external_evidence, Mapping):
        return {"status": "not_run"}
    allowed = {
        "status",
        "category_coverage",
        "provider_outcomes",
        "policy_failure",
        "required_provider_ids",
        "required_provider_outcomes",
        "required_provider_failures",
        "known_unvalidated_provider_exceptions",
        "provider_exception_outcomes",
        "provider_limitations",
        "evidence_reuse",
    }
    return {
        key: value
        for key, value in external_evidence.items()
        if key in allowed and _is_json_value(value)
    }


def _pre_publish_provider_exception_summary(
    payload: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    external_evidence = payload.get("external_evidence")
    external_mapping = external_evidence if isinstance(external_evidence, Mapping) else {}
    exception_outcomes = external_mapping.get("provider_exception_outcomes")
    return {
        "schema_version": "agent_treport.pre_publish.provider_exception_summary.v1",
        "known_unvalidated_provider_exceptions": _json_list(
            external_mapping.get("known_unvalidated_provider_exceptions")
        ),
        "provider_exception_outcomes": (
            dict(exception_outcomes)
            if isinstance(exception_outcomes, Mapping)
            else {}
        ),
    }


def _pre_publish_canonical_history_unchanged(
    args: argparse.Namespace,
) -> dict[str, JsonValue]:
    canonical = Path(DEFAULT_NATIVE_HANDOFF_HISTORY_DIR)
    smoke_history = Path(args.history_dir)
    canonical_resolved = canonical.resolve()
    smoke_resolved = smoke_history.resolve()
    uses_canonical = canonical_resolved == smoke_resolved
    return {
        "schema_version": "agent_treport.pre_publish.canonical_history_unchanged.v1",
        "canonical_history_mutated": False if not uses_canonical else None,
        "status": "not_mutated_by_preview"
        if not uses_canonical
        else "not_verified_canonical_history_was_input",
        "canonical_history_path": _path_safe_path_text(canonical),
        "smoke_history_path": _path_safe_path_text(smoke_history),
    }


def _pre_publish_telegram_message(
    payload: Mapping[str, JsonValue],
) -> dict[str, JsonValue] | None:
    if payload.get("status") == "failed":
        return None
    artifacts = _pre_publish_reference_artifacts(payload)
    telegram_alert = artifacts.get("telegram_alert")
    if not isinstance(telegram_alert, Mapping):
        return None
    artifact_id = telegram_alert.get("artifact_id")
    path = telegram_alert.get("path")
    if not isinstance(artifact_id, str) or not isinstance(path, str):
        return None
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None
    return {
        "artifact_id": artifact_id,
        "parse_mode": "HTML",
        "send_method": "sendMessage",
        "delivery_status": "not_sent",
        "text": text,
    }


def _pre_publish_closure_summary(
    payload: Mapping[str, JsonValue],
    *,
    telegram_message: Mapping[str, JsonValue] | None,
) -> dict[str, JsonValue]:
    artifacts = _pre_publish_reference_artifacts(payload)
    required_artifacts = {
        "canonical_payload",
        "markdown_report",
        "html_report",
        "telegram_alert",
        "quality_report",
    }
    missing_artifacts = sorted(required_artifacts.difference(artifacts))
    artifact_met = (
        payload.get("status") != "failed"
        and telegram_message is not None
        and not missing_artifacts
    )
    user_ready_blockers = _pre_publish_user_ready_closure_blockers(
        payload=payload,
        artifact_met=artifact_met,
    )
    telegram_artifact = artifacts.get("telegram_alert")
    telegram_artifact_id = (
        telegram_artifact.get("artifact_id")
        if isinstance(telegram_artifact, Mapping)
        and isinstance(telegram_artifact.get("artifact_id"), str)
        else None
    )
    return {
        "full_live_pre_publish_artifact_closure": {
            "status": "met" if artifact_met else "not_met",
            "telegram_message_body_included": telegram_message is not None,
            "telegram_alert_artifact_id": telegram_artifact_id,
            "missing_artifacts": missing_artifacts,
        },
        "full_user_ready_closure": {
            "status": "met" if not user_ready_blockers else "blocked",
            "delivery_blocked": bool(payload.get("delivery_blocked")),
            "blocking_reasons": user_ready_blockers,
        },
    }


def _pre_publish_user_ready_closure_blockers(
    *,
    payload: Mapping[str, JsonValue],
    artifact_met: bool,
) -> list[str]:
    blockers: list[str] = []
    if not artifact_met:
        blockers.append("full_live_pre_publish_artifact_closure_not_met")
    if payload.get("status") != "user_ready":
        reason = payload.get("reason")
        blockers.append(reason if isinstance(reason, str) else "not_user_ready")
    if bool(payload.get("delivery_blocked")):
        blockers.append("delivery_blocked")
    readiness = payload.get("readiness")
    if isinstance(readiness, Mapping) and readiness.get("status") in {"hold", "failed"}:
        blockers.append(f"readiness_{readiness['status']}")
    external_evidence = payload.get("external_evidence")
    if isinstance(external_evidence, Mapping):
        if external_evidence.get("status") == "not_run":
            blockers.append("external_evidence_not_run")
        if _pre_publish_required_provider_failures(external_evidence):
            blockers.append("external_evidence_policy_failure")
    acceptance = payload.get("verified_operational_flow_acceptance")
    if isinstance(acceptance, Mapping) and acceptance.get("status") != "passed":
        blockers.append("verified_operational_flow_acceptance_not_met")
    return list(dict.fromkeys(blockers))


def _pre_publish_reference_artifacts(
    payload: Mapping[str, JsonValue],
) -> Mapping[str, JsonValue]:
    references = payload.get("references")
    artifacts = references.get("artifacts") if isinstance(references, Mapping) else None
    return artifacts if isinstance(artifacts, Mapping) else {}


def _pre_publish_missing_artifacts(
    payload: Mapping[str, JsonValue],
) -> list[dict[str, JsonValue]]:
    references = payload.get("references")
    artifacts = (
        references.get("artifacts")
        if isinstance(references, Mapping)
        else None
    )
    present = set(artifacts) if isinstance(artifacts, Mapping) else set()
    required = {
        "canonical_payload",
        "markdown_report",
        "html_report",
        "telegram_alert",
        "quality_report",
        "readiness",
        "collection_summary",
        "external_evidence",
        "external_evidence_summary",
        "provider_etf_exclusion_summary",
    }
    reason = payload.get("reason")
    reason_text = reason if isinstance(reason, str) and reason else "failed"
    return [
        {"artifact": artifact, "reason": reason_text}
        for artifact in sorted(required - present)
    ]


def _pre_publish_readiness_blocks_report(
    *,
    args: argparse.Namespace,
    readiness: Mapping[str, JsonValue],
) -> bool:
    _ = args
    return readiness.get("status") == "failed"


def _pre_publish_existing_or_not_run_summary(path: Path) -> Path:
    if not path.is_file():
        _write_native_handoff_payload(path, _not_run_external_evidence_summary())
    return path


def _pre_publish_signal_board_claim_scopes(
    *,
    args: argparse.Namespace,
    manifest_path: Path,
) -> set[str]:
    provider = OperationalSignalReportInputProvider(
        manifest_path=manifest_path,
        focus_etf_id=args.focus_etf_id,
        focus_etf_ids=_focus_etf_ids_from_args(args),
        observed_partitions=args.observed_partitions,
        evidence_path=None,
    )
    inputs = provider.load()
    payload = build_signal_report_payload(
        snapshots=inputs.snapshots,
        focus_etf_id=inputs.focus_etf_id,
        focus_etf_ids=inputs.focus_etf_ids,
        evidence=(),
    )
    return {row.claim_scope for row in payload.signal_board}


def _validate_cached_external_evidence_for_preview(
    *,
    evidence_path: Path,
    summary: Mapping[str, JsonValue],
    allowed_claim_scopes: set[str],
) -> None:
    raw = json.loads(evidence_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise SignalReportInputError("external evidence input must be a JSON array")
    for item in raw:
        evidence = EvidenceItemInput.model_validate(item)
        _validate_pre_publish_claim_scope(
            evidence.claim_scope,
            allowed_claim_scopes=allowed_claim_scopes,
        )
    target_selection = summary.get("target_selection")
    if not isinstance(target_selection, Mapping):
        return
    selected_targets = target_selection.get("selected_targets")
    if not isinstance(selected_targets, list):
        return
    for target in selected_targets:
        if not isinstance(target, Mapping):
            continue
        claim_scope = target.get("claim_scope")
        _validate_pre_publish_claim_scope(
            claim_scope if isinstance(claim_scope, str) else None,
            allowed_claim_scopes=allowed_claim_scopes,
        )


def _validate_pre_publish_claim_scope(
    claim_scope: str | None,
    *,
    allowed_claim_scopes: set[str],
) -> None:
    if claim_scope is None:
        return
    if claim_scope not in allowed_claim_scopes:
        raise SignalReportInputError(
            "cached external evidence claim_scope does not match "
            "current SignalBoard targets"
        )


def _pre_publish_external_evidence_review_reason(value: object) -> str | None:
    if not isinstance(value, Mapping):
        return "external_evidence_not_run"
    if value.get("status") == "not_run":
        return "external_evidence_not_run"
    if _pre_publish_required_provider_failures(value):
        return "external_evidence_policy_failure"
    if not _pre_publish_all_evidence_categories_attempted(value):
        return "external_evidence_not_run"
    return None


def _pre_publish_all_evidence_categories_attempted(
    summary: Mapping[str, JsonValue],
) -> bool:
    category_coverage = summary.get("category_coverage")
    if not isinstance(category_coverage, Mapping):
        return False
    for category in PRE_PUBLISH_REQUIRED_EVIDENCE_CATEGORIES:
        coverage = category_coverage.get(category)
        if not isinstance(coverage, Mapping):
            return False
        if coverage.get("status") == "not_run":
            return False
    provider_outcomes = summary.get("provider_outcomes")
    if not isinstance(provider_outcomes, list):
        return False
    attempted_categories: set[str] = set()
    for outcome in provider_outcomes:
        if not isinstance(outcome, Mapping):
            continue
        category = outcome.get("category")
        status = outcome.get("status")
        if (
            isinstance(category, str)
            and category in PRE_PUBLISH_REQUIRED_EVIDENCE_CATEGORIES
            and status not in {"skipped", "cooldown_active"}
        ):
            attempted_categories.add(category)
    return PRE_PUBLISH_REQUIRED_EVIDENCE_CATEGORIES.issubset(attempted_categories)


def _default_evidence_summary_path(evidence_path: Path) -> Path:
    return evidence_path.parent / "external_evidence_summary.json"


async def _run_native_operational_handoff_command(
    args: argparse.Namespace,
    *,
    output: TextIO,
    error_output: TextIO,
    model_client_factory: ModelClientFactory | None,
    readiness_now: Callable[[], datetime] | None,
    collection_now: Callable[[], datetime] | None,
) -> int:
    try:
        handoff_context = _prepare_native_operational_handoff_inputs(
            args,
            readiness_now=readiness_now,
            collection_now=collection_now,
        )
    except Exception as exc:
        _write_cli_input_error(error_output, str(exc))
        return 2

    readiness = handoff_context["readiness"]
    if isinstance(readiness, Mapping) and readiness.get("status") == "failed":
        handoff = _build_failed_native_operational_handoff(
            args=args,
            context=handoff_context,
            reason="readiness_failed",
            run_payload=None,
        )
        _write_native_handoff_payload(handoff_context["output_path"], handoff)
        json.dump(handoff, output, ensure_ascii=False)
        output.write("\n")
        return 1

    run_args = argparse.Namespace(
        command="run-report",
        run_id=args.run_id,
        sqlite_path=str(handoff_context["sqlite_path"]),
        artifact_root=str(handoff_context["artifact_root"]),
        model=args.model,
        codex_model=args.codex_model,
        model_timeout_seconds=args.model_timeout_seconds,
        holdings_source="operational",
        holdings_path=str(handoff_context["manifest_path"]),
        evidence_path=args.evidence_path,
        evidence_summary_path=str(handoff_context["external_evidence_summary_path"]),
        focus_etf_id=args.focus_etf_id,
        focus_etf_set_path=args.focus_etf_set_path,
        observed_partitions=args.observed_partitions,
        readiness_path=str(handoff_context["readiness_path"]),
        allow_operator_review_output=args.allow_operator_review_output,
        approval_path=args.approval_path,
        write_preflight=args.write_preflight,
    )
    run_stdout = StringIO()
    run_exit = await _run_report_command(
        run_args,
        output=run_stdout,
        error_output=error_output,
        model_client_factory=model_client_factory,
    )
    run_payload = _json_object_from_text(run_stdout.getvalue())
    handoff = _build_native_operational_handoff(
        args=args,
        context=handoff_context,
        run_payload=run_payload,
        run_exit=run_exit,
    )
    _write_native_handoff_payload(handoff_context["output_path"], handoff)
    json.dump(handoff, output, ensure_ascii=False)
    output.write("\n")
    if (
        bool(args.require_verified_operational_flow_acceptance)
        and handoff["verified_operational_flow_acceptance"]["status"] != "passed"
    ):
        return 1
    return int(handoff["exit_code"])


def _prepare_native_operational_handoff_inputs(
    args: argparse.Namespace,
    *,
    readiness_now: Callable[[], datetime] | None,
    collection_now: Callable[[], datetime] | None,
) -> dict[str, object]:
    if args.observed_partitions <= 0:
        raise SignalReportInputError("observed-partitions must be a positive integer")
    if args.security_resolution_path is not None and args.use_default_security_resolution:
        raise SignalReportInputError(
            "--security-resolution-path and --use-default-security-resolution "
            "cannot both be supplied"
        )
    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)
    sqlite_path = Path(args.sqlite_path) if args.sqlite_path else dest / "runtime.sqlite3"
    artifact_root = Path(args.artifact_root) if args.artifact_root else dest / "artifacts"
    output_path = (
        Path(args.output_path)
        if args.output_path
        else dest / "native_operational_handoff.json"
    )
    readiness_path = dest / "readiness.json"
    export_dir = Path(args.export_dir) if args.export_dir else dest / "operational-holdings"
    security_resolution_path = _native_handoff_security_resolution_path(args)

    if args.resume_export_path is not None:
        manifest_path = Path(args.resume_export_path)
        export_summary = _read_cli_json_object(
            manifest_path.parent / "collection_summary.json",
            label="collection summary",
        )
        _validate_resume_export_summary(manifest_path, export_summary)
    else:
        export_summary = export_latest_holdings_comparison(
            history_dir=args.history_dir,
            universe_state_path=args.universe_state_path,
            dest_dir=export_dir,
            security_resolution_path=security_resolution_path,
            now=collection_now,
        )
        manifest_path = export_dir / "url_holdings_cumulative.json"

    source_acquisition_summary_path = (
        Path(args.history_dir) / SOURCE_ACQUISITION_SUMMARY_FILENAME
    )
    source_acquisition_summary = (
        _read_cli_json_object(
            source_acquisition_summary_path,
            label="source acquisition summary",
        )
        if source_acquisition_summary_path.is_file()
        else None
    )
    focus_etf_ids = _focus_etf_ids_from_args(args)
    readiness = check_operational_run_readiness(
        holdings_path=manifest_path,
        focus_etf_id=args.focus_etf_id,
        focus_etf_ids=focus_etf_ids,
        observed_partitions=args.observed_partitions,
        now=readiness_now,
    )
    _write_native_handoff_payload(readiness_path, readiness)
    external_evidence_summary_path = _native_handoff_external_evidence_summary_path(
        args,
        dest=dest,
    )
    external_evidence_summary = _read_cli_json_object(
        external_evidence_summary_path,
        label="external evidence summary",
    )
    _validate_external_evidence_summary(external_evidence_summary)
    if args.evidence_path is not None:
        _validate_external_evidence_input(Path(args.evidence_path))
    exclusion_summary_path = dest / "provider_etf_exclusion_summary.json"
    exclusion_summary = _provider_etf_exclusion_summary(
        universe_state_path=Path(args.universe_state_path),
        collection_summary=export_summary,
    )
    _write_native_handoff_payload(exclusion_summary_path, exclusion_summary)
    return {
        "dest": dest,
        "sqlite_path": sqlite_path,
        "artifact_root": artifact_root,
        "output_path": output_path,
        "manifest_path": manifest_path,
        "collection_summary_path": manifest_path.parent / "collection_summary.json",
        "readiness_path": readiness_path,
        "readiness": readiness,
        "export_summary": export_summary,
        "external_evidence_summary_path": external_evidence_summary_path,
        "external_evidence_summary": external_evidence_summary,
        "source_acquisition_summary_path": source_acquisition_summary_path,
        "source_acquisition_summary": source_acquisition_summary,
        "exclusion_summary_path": exclusion_summary_path,
        "exclusion_summary": exclusion_summary,
        "security_resolution_path": security_resolution_path,
    }


def _native_handoff_security_resolution_path(args: argparse.Namespace) -> str | None:
    if args.security_resolution_path is not None:
        return str(args.security_resolution_path)
    if args.use_default_security_resolution:
        path = Path(DEFAULT_SECURITY_RESOLUTION_PATH)
        if not path.is_file():
            raise SignalReportInputError(
                f"default security resolution file not found: {path}"
            )
        return str(path)
    return None


def _validate_resume_export_summary(
    manifest_path: Path,
    collection_summary: Mapping[str, JsonValue],
) -> None:
    normalized_output = collection_summary.get("normalized_output")
    if not isinstance(normalized_output, Mapping):
        raise SignalReportInputError("resume export is missing normalized output evidence")
    recorded = normalized_output.get("fingerprint")
    current = compute_operational_export_fingerprint(manifest_path)
    if recorded != current:
        raise SignalReportInputError(
            "resume export collection summary fingerprint does not match export"
        )


def _native_handoff_external_evidence_summary_path(
    args: argparse.Namespace,
    *,
    dest: Path,
) -> Path:
    if args.evidence_summary_path is not None:
        return Path(args.evidence_summary_path)
    path = dest / "external_evidence_summary.json"
    _write_native_handoff_payload(path, _not_run_external_evidence_summary())
    return path


def _not_run_external_evidence_summary() -> dict[str, JsonValue]:
    return {
        "schema_version": EXTERNAL_EVIDENCE_SUMMARY_SCHEMA_VERSION,
        "status": "not_run",
        "target_selection": {
            "selected_targets": [],
            "excluded_targets": [],
            "max_targets": 0,
        },
        "provider_outcomes": [],
        "category_coverage": {
            "financial": {"coverage_ratio": None, "status": "not_run", "notes": ["not_run"]},
            "disclosure": {"coverage_ratio": None, "status": "not_run", "notes": ["not_run"]},
            "news": {"coverage_ratio": None, "status": "not_run", "notes": ["not_run"]},
        },
        "dedupe": {"deduped_count": 0},
        "policy_failure": None,
        "evidence_path": None,
        "cooldown_path": None,
        "required_provider_ids": [],
        "known_unvalidated_provider_exceptions": [
            dict(item) for item in PRE_PUBLISH_KNOWN_UNVALIDATED_PROVIDER_EXCEPTIONS
        ],
        "provider_limitations": [],
        "evidence_reuse": {},
        "smoke_boundary": {},
    }


def _validate_external_evidence_summary(summary: Mapping[str, JsonValue]) -> None:
    if summary.get("schema_version") != EXTERNAL_EVIDENCE_SUMMARY_SCHEMA_VERSION:
        raise SignalReportInputError("invalid external evidence summary schema")
    if not isinstance(summary.get("category_coverage"), Mapping):
        raise SignalReportInputError("external evidence summary missing category coverage")
    if not isinstance(summary.get("provider_outcomes"), list):
        raise SignalReportInputError("external evidence summary missing provider outcomes")


def _validate_external_evidence_input(path: Path) -> None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SignalReportInputError(f"invalid external evidence input: {path}") from exc
    if not isinstance(raw, list):
        raise SignalReportInputError("external evidence input must be a JSON array")
    for index, item in enumerate(raw, 1):
        if not isinstance(item, Mapping):
            raise SignalReportInputError(
                f"external evidence item must be an object: index={index}"
            )
        claim_scope = item.get("claim_scope")
        if claim_scope is None:
            continue
        if not isinstance(claim_scope, str) or not claim_scope.startswith(
            ("signal:security:", "signal:security_group:")
        ):
            raise SignalReportInputError(
                "external evidence claim_scope must use an identity-safe signal scope"
            )


def _provider_etf_exclusion_summary(
    *,
    universe_state_path: Path,
    collection_summary: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    universe_state = _read_cli_json_object(universe_state_path, label="native universe state")
    etfs = [
        item
        for item in universe_state.get("etfs", [])
        if isinstance(item, Mapping) and item.get("status") == "active"
    ]
    active_by_provider: dict[str, list[str]] = {}
    provider_by_etf: dict[str, str] = {}
    for item in etfs:
        etf_id = item.get("etf_id")
        provider_id = item.get("source_provider_id")
        if not isinstance(etf_id, str) or not isinstance(provider_id, str):
            continue
        provider_by_etf[etf_id] = provider_id
        active_by_provider.setdefault(provider_id, []).append(etf_id)

    active_coverage = collection_summary.get("active_etf_coverage")
    missing_ids: list[str] = []
    if isinstance(active_coverage, Mapping):
        raw_missing = active_coverage.get("missing_active_etf_ids")
        if isinstance(raw_missing, list):
            missing_ids = [item for item in raw_missing if isinstance(item, str)]
    eligible_ids = [
        etf_id
        for etf_id in sorted(provider_by_etf)
        if etf_id not in set(missing_ids)
    ]
    eligible_providers = [
        provider_id
        for provider_id in LIVE_SOURCE_PROVIDER_IDS
        if any(provider_by_etf.get(etf_id) == provider_id for etf_id in eligible_ids)
    ]
    excluded_etfs = [
        {
            "etf_id": etf_id,
            "source_provider_id": provider_by_etf[etf_id],
            "reason": "missing_comparison_window",
        }
        for etf_id in sorted(missing_ids)
        if etf_id in provider_by_etf
    ]
    excluded_providers: list[dict[str, JsonValue]] = []
    for provider_id in LIVE_SOURCE_PROVIDER_IDS:
        active_ids = active_by_provider.get(provider_id, [])
        if not active_ids:
            excluded_providers.append(
                {
                    "source_provider_id": provider_id,
                    "reason": "no_registered_active_etfs",
                }
            )
            continue
        if not any(provider_by_etf.get(etf_id) == provider_id for etf_id in eligible_ids):
            excluded_providers.append(
                {
                    "source_provider_id": provider_id,
                    "reason": "no_eligible_etfs",
                }
            )
    return {
        "schema_version": PROVIDER_ETF_EXCLUSION_SUMMARY_SCHEMA_VERSION,
        "registered_provider_ids": list(LIVE_SOURCE_PROVIDER_IDS),
        "registered_provider_count": len(LIVE_SOURCE_PROVIDER_IDS),
        "active_registered_etf_count": len(provider_by_etf),
        "eligible_analysis_cohort": {
            "provider_ids": eligible_providers,
            "provider_count": len(eligible_providers),
            "etf_ids": eligible_ids,
            "etf_count": len(eligible_ids),
        },
        "excluded_providers": excluded_providers,
        "excluded_etfs": excluded_etfs,
    }


def _build_native_operational_handoff(
    *,
    args: argparse.Namespace,
    context: Mapping[str, object],
    run_payload: Mapping[str, JsonValue] | None,
    run_exit: int,
) -> dict[str, JsonValue]:
    if run_payload is None:
        return _build_failed_native_operational_handoff(
            args=args,
            context=context,
            reason="run_report_output_missing",
            run_payload=None,
        )
    output_payload = run_payload.get("output")
    output_mapping = output_payload if isinstance(output_payload, Mapping) else {}
    if run_exit == 0 and isinstance(output_mapping.get("user_ready"), Mapping):
        status = "user_ready"
        delivery_blocked = False
        reason = None
        report_handoff = output_mapping["user_ready"]
        exit_code = 0
    elif run_exit == 0 and isinstance(output_mapping.get("operator_review_only"), Mapping):
        status = "operator_review_only"
        delivery_blocked = True
        report_handoff = output_mapping["operator_review_only"]
        reason = str(report_handoff.get("reason", "operator_review_only"))
        exit_code = 0
    else:
        return _build_failed_native_operational_handoff(
            args=args,
            context=context,
            reason=_failed_handoff_reason(run_payload),
            run_payload=run_payload,
        )

    references = _native_handoff_references(
        report_handoff=report_handoff,
        context=context,
        run_payload=run_payload,
    )
    acceptance = _verified_operational_flow_acceptance(
        context=context,
        references=references,
    )
    handoff: dict[str, JsonValue] = {
        "schema_version": NATIVE_OPERATIONAL_HANDOFF_SCHEMA_VERSION,
        "status": status,
        "delivery_blocked": delivery_blocked,
        "reason": reason,
        "user_ready_status": "not user-ready" if delivery_blocked else "user-ready",
        "run_id": args.run_id,
        "exit_code": exit_code,
        "registered_cohort_statement": REGISTERED_COHORT_STATEMENT,
        "verified_operational_flow_acceptance": acceptance,
        "readiness": _handoff_readiness_summary(context.get("readiness")),
        "external_evidence": _handoff_external_evidence_summary(
            context.get("external_evidence_summary")
        ),
        "references": references,
    }
    if status == "user_ready":
        handoff["user_ready"] = dict(report_handoff)
    else:
        handoff["operator_review_only"] = {
            **dict(report_handoff),
            "delivery_blocked": True,
            "reason": reason,
            "user_ready_status": "not user-ready",
        }
    return handoff


def _build_failed_native_operational_handoff(
    *,
    args: argparse.Namespace,
    context: Mapping[str, object],
    reason: str,
    run_payload: Mapping[str, JsonValue] | None,
) -> dict[str, JsonValue]:
    references = _failed_native_handoff_references(
        context=context,
        run_payload=run_payload,
    )
    result: dict[str, JsonValue] = {
        "schema_version": NATIVE_OPERATIONAL_HANDOFF_SCHEMA_VERSION,
        "status": "failed",
        "delivery_blocked": True,
        "reason": reason,
        "user_ready_status": "not user-ready",
        "run_id": args.run_id,
        "exit_code": 1,
        "registered_cohort_statement": REGISTERED_COHORT_STATEMENT,
        "verified_operational_flow_acceptance": _verified_operational_flow_acceptance(
            context=context,
            references=references,
        ),
        "readiness": _handoff_readiness_summary(context.get("readiness")),
        "external_evidence": _handoff_external_evidence_summary(
            context.get("external_evidence_summary")
        ),
        "references": references,
        "recovery": {
            "instructions": [
                "Inspect the failed run evidence before retrying.",
                "If readiness failed, rerun export and check-operational-readiness.",
                "If report quality failed, review quality_report and adjust report inputs.",
            ]
        },
        "run_report": dict(run_payload) if isinstance(run_payload, Mapping) else None,
    }
    approval = _approval_from_run_payload(run_payload)
    if approval is not None:
        result["approval"] = approval
    return result


def _native_handoff_references(
    *,
    report_handoff: Mapping[str, JsonValue],
    context: Mapping[str, object],
    run_payload: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    artifacts = dict(report_handoff.get("artifacts", {}))
    artifacts["collection_summary"] = _file_artifact_entry(
        artifact_id="artifact_treport_collection_summary",
        name="collection_summary.json",
        path=_as_path(context["collection_summary_path"]),
    )
    source_acquisition_summary_path = _as_path(
        context["source_acquisition_summary_path"]
    )
    if source_acquisition_summary_path.is_file():
        artifacts["source_acquisition_summary"] = _file_artifact_entry(
            artifact_id="artifact_treport_source_acquisition_summary",
            name="source_acquisition_summary.json",
            path=source_acquisition_summary_path,
        )
    artifacts["external_evidence_summary"] = _file_artifact_entry(
        artifact_id="artifact_treport_external_evidence_summary",
        name="external_evidence_summary.json",
        path=_as_path(context["external_evidence_summary_path"]),
    )
    artifacts["provider_etf_exclusion_summary"] = _file_artifact_entry(
        artifact_id="artifact_treport_provider_etf_exclusion_summary",
        name="provider_etf_exclusion_summary.json",
        path=_as_path(context["exclusion_summary_path"]),
    )
    commands = report_handoff.get("commands")
    follow_up = dict(commands) if isinstance(commands, Mapping) else {}
    return {
        "artifacts": artifacts,
        "follow_up": follow_up,
        "run_result_status": run_payload.get("status"),
    }


def _failed_native_handoff_references(
    *,
    context: Mapping[str, object],
    run_payload: Mapping[str, JsonValue] | None,
) -> dict[str, JsonValue]:
    artifacts: dict[str, JsonValue] = {
        "collection_summary": _file_artifact_entry(
            artifact_id="artifact_treport_collection_summary",
            name="collection_summary.json",
            path=_as_path(context["collection_summary_path"]),
        ),
        "external_evidence_summary": _file_artifact_entry(
            artifact_id="artifact_treport_external_evidence_summary",
            name="external_evidence_summary.json",
            path=_as_path(context["external_evidence_summary_path"]),
        ),
        "provider_etf_exclusion_summary": _file_artifact_entry(
            artifact_id="artifact_treport_provider_etf_exclusion_summary",
            name="provider_etf_exclusion_summary.json",
            path=_as_path(context["exclusion_summary_path"]),
        ),
    }
    readiness_path = _as_path(context["readiness_path"])
    if readiness_path.is_file():
        artifacts["readiness"] = _file_artifact_entry(
            artifact_id="artifact_treport_operational_readiness",
            name="operational_readiness.json",
            path=readiness_path,
        )
    source_acquisition_summary_path = _as_path(
        context["source_acquisition_summary_path"]
    )
    if source_acquisition_summary_path.is_file():
        artifacts["source_acquisition_summary"] = _file_artifact_entry(
            artifact_id="artifact_treport_source_acquisition_summary",
            name="source_acquisition_summary.json",
            path=source_acquisition_summary_path,
        )
    if isinstance(run_payload, Mapping):
        run_references = run_payload.get("references")
        run_artifacts = (
            run_references.get("artifacts")
            if isinstance(run_references, Mapping)
            else None
        )
        if isinstance(run_artifacts, Mapping):
            for key in ("approval_preflight", "approval_template"):
                value = run_artifacts.get(key)
                if _is_json_value(value):
                    artifacts[key] = value
        output_payload = run_payload.get("output")
        output_mapping = output_payload if isinstance(output_payload, Mapping) else {}
        state = output_mapping.get("state")
        if isinstance(state, Mapping):
            payload_id = state.get("signal_payload_artifact_id")
            if payload_id == "artifact_treport_signal_payload":
                payload_path = _as_path(context["artifact_root"]) / (
                    "artifact_treport_signal_payload.json"
                )
                if payload_path.is_file():
                    artifacts["canonical_payload"] = _file_artifact_entry(
                        artifact_id="artifact_treport_signal_payload",
                        name="signal_payload.json",
                        path=payload_path,
                    )
            readiness_id = state.get("operational_readiness_artifact_id")
            if readiness_id == "artifact_treport_operational_readiness":
                readiness_path = _as_path(context["artifact_root"]) / (
                    "artifact_treport_operational_readiness.json"
                )
                if readiness_path.is_file():
                    artifacts["readiness"] = _file_artifact_entry(
                        artifact_id="artifact_treport_operational_readiness",
                        name="operational_readiness.json",
                        path=readiness_path,
                    )
            quality_id = state.get("report_quality_artifact_id")
            if quality_id == "artifact_treport_quality":
                quality_path = _as_path(context["artifact_root"]) / "artifact_treport_quality.json"
                if quality_path.is_file():
                    artifacts["quality_report"] = _file_artifact_entry(
                        artifact_id="artifact_treport_quality",
                        name="quality.json",
                        path=quality_path,
                    )
    sqlite_path = str(_as_path(context["sqlite_path"]).resolve())
    run_id = str(run_payload.get("run_id")) if isinstance(run_payload, Mapping) else ""
    inspect_argv = [
        "agent-treport",
        "inspect",
        "--run-id",
        run_id,
        "--sqlite-path",
        sqlite_path,
    ]
    return {
        "artifacts": artifacts,
        "follow_up": {
            "inspect_argv": inspect_argv,
            "inspect": _render_command(inspect_argv),
        },
        "run_result_status": (
            run_payload.get("status") if isinstance(run_payload, Mapping) else None
        ),
    }


def _verified_operational_flow_acceptance(
    *,
    context: Mapping[str, object],
    references: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    unmet: list[str] = []
    export_summary = context.get("export_summary")
    if not _bounded_source_holdings_succeeded(
        context.get("source_acquisition_summary"),
        export_summary=export_summary,
        exclusion_summary=context.get("exclusion_summary"),
    ):
        unmet.append("bounded_live_source_holdings")
    security_coverage = (
        export_summary.get("security_coverage")
        if isinstance(export_summary, Mapping)
        else None
    )
    if not (
        isinstance(security_coverage, Mapping)
        and security_coverage.get("security_resolution_available") is True
    ):
        unmet.append("reviewed_security_identity")
    external_summary = context.get("external_evidence_summary")
    if not (
        isinstance(external_summary, Mapping)
        and external_summary.get("status") != "not_run"
    ):
        unmet.append("external_evidence_summary")
    artifacts = references.get("artifacts")
    artifact_keys = set(artifacts) if isinstance(artifacts, Mapping) else set()
    required = {
        "canonical_payload",
        "markdown_report",
        "html_report",
        "telegram_alert",
        "quality_report",
        "readiness",
        "collection_summary",
        "external_evidence_summary",
        "provider_etf_exclusion_summary",
    }
    missing = sorted(required - artifact_keys)
    if missing:
        unmet.append("canonical_report_artifacts")
    if "readiness" not in artifact_keys:
        unmet.append("readiness_evidence")
    if "quality_report" not in artifact_keys:
        unmet.append("report_quality_evidence")
    if "provider_etf_exclusion_summary" not in artifact_keys:
        unmet.append("path_safe_registered_cohort_accounting")
    follow_up = references.get("follow_up")
    if not (isinstance(follow_up, Mapping) and isinstance(follow_up.get("inspect"), str)):
        unmet.append("inspect_reference")
    return {
        "status": "passed" if not unmet else "not_met",
        "unmet_criteria": unmet,
    }


def _bounded_source_holdings_succeeded(
    summary: object,
    *,
    export_summary: object,
    exclusion_summary: object,
) -> bool:
    if not isinstance(summary, Mapping):
        return False
    if summary.get("schema_version") != SOURCE_ACQUISITION_SUMMARY_SCHEMA_VERSION:
        return False
    if summary.get("provider_rollout_status") != "supported":
        return False
    if summary.get("run_outcome") not in {"succeeded", "partial"}:
        return False
    aggregate_counts = summary.get("aggregate_counts")
    if not isinstance(aggregate_counts, Mapping):
        return False
    source_provider_id = summary.get("source_provider_id")
    if source_provider_id == "multiple":
        return _bounded_aggregated_source_holdings_succeeded(
            summary=summary,
            aggregate_counts=aggregate_counts,
            export_summary=export_summary,
            exclusion_summary=exclusion_summary,
        )
    if source_provider_id not in LIVE_SOURCE_PROVIDER_IDS:
        return False
    if aggregate_counts.get("target_count") != 1:
        return False
    target_outcomes = summary.get("target_outcomes")
    if not isinstance(target_outcomes, list) or len(target_outcomes) != 1:
        return False
    target = target_outcomes[0]
    return _bounded_source_target_succeeded(
        target,
        export_summary=export_summary,
        exclusion_summary=exclusion_summary,
    )


def _bounded_aggregated_source_holdings_succeeded(
    *,
    summary: Mapping[str, object],
    aggregate_counts: Mapping[str, object],
    export_summary: object,
    exclusion_summary: object,
) -> bool:
    provider_ids = summary.get("source_provider_ids")
    if not isinstance(provider_ids, list) or len(provider_ids) < 2:
        return False
    provider_id_set = {item for item in provider_ids if isinstance(item, str)}
    if len(provider_id_set) != len(provider_ids):
        return False
    if not provider_id_set.issubset(set(LIVE_SOURCE_PROVIDER_IDS)):
        return False
    target_count = aggregate_counts.get("target_count")
    target_outcomes = summary.get("target_outcomes")
    if (
        not isinstance(target_count, int)
        or isinstance(target_count, bool)
        or target_count < 1
        or not isinstance(target_outcomes, list)
        or len(target_outcomes) != target_count
    ):
        return False
    for target in target_outcomes:
        if not isinstance(target, Mapping):
            return False
        target_provider_id = target.get("source_provider_id")
        if target_provider_id not in provider_id_set:
            return False
        if not _bounded_source_target_succeeded(
            target,
            export_summary=export_summary,
            exclusion_summary=exclusion_summary,
        ):
            return False
    return True


def _bounded_source_target_succeeded(
    target: object,
    *,
    export_summary: object,
    exclusion_summary: object,
) -> bool:
    if not isinstance(target, Mapping):
        return False
    target_etf_id = target.get("etf_id")
    if not isinstance(target_etf_id, str):
        return False
    if target.get("outcome") not in {"fetched", "skipped_existing"}:
        return False
    if not _is_positive_int(target.get("row_count")):
        return False
    exported_etf_ids = _export_comparison_window_etf_ids(export_summary)
    eligible_etf_ids = _eligible_cohort_etf_ids(exclusion_summary)
    if target_etf_id not in exported_etf_ids and target_etf_id not in eligible_etf_ids:
        return False
    freshness = target.get("latest_upload_freshness")
    return isinstance(freshness, Mapping) and freshness.get("status") == "fresh_latest"


def _is_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _export_comparison_window_etf_ids(export_summary: object) -> set[str]:
    if not isinstance(export_summary, Mapping):
        return set()
    active_coverage = export_summary.get("active_etf_coverage")
    if not isinstance(active_coverage, Mapping):
        return set()
    windows = active_coverage.get("comparison_windows")
    if isinstance(windows, list):
        return {
            etf_id
            for item in windows
            if isinstance(item, Mapping)
            for etf_id in (item.get("etf_id"),)
            if isinstance(etf_id, str)
        }
    return set()


def _eligible_cohort_etf_ids(exclusion_summary: object) -> set[str]:
    if not isinstance(exclusion_summary, Mapping):
        return set()
    cohort = exclusion_summary.get("eligible_analysis_cohort")
    if not isinstance(cohort, Mapping):
        return set()
    raw_ids = cohort.get("etf_ids")
    if not isinstance(raw_ids, list):
        return set()
    return {item for item in raw_ids if isinstance(item, str)}


def _handoff_readiness_summary(readiness: object) -> dict[str, JsonValue]:
    if not isinstance(readiness, Mapping):
        return {"status": "unknown", "disclosures": []}
    return {
        "status": str(readiness.get("status")),
        "focus_etf_id": str(readiness.get("focus_etf_id")),
        "focus_etf_ids": [
            item for item in readiness.get("focus_etf_ids", []) if isinstance(item, str)
        ],
        "current_date": str(readiness.get("current_date")),
        "previous_date": str(readiness.get("previous_date")),
        "disclosures": _operational_readiness_disclosures(readiness),
    }


def _handoff_external_evidence_summary(summary: object) -> dict[str, JsonValue]:
    if not isinstance(summary, Mapping):
        return {
            "status": "not_run",
            "category_coverage": {},
            "provider_outcomes": [],
            "policy_failure": None,
            "required_provider_ids": [],
            "required_provider_outcomes": {},
            "required_provider_failures": [],
            "known_unvalidated_provider_exceptions": [
                dict(item) for item in PRE_PUBLISH_KNOWN_UNVALIDATED_PROVIDER_EXCEPTIONS
            ],
            "provider_exception_outcomes": {
                provider_id: "not_requested"
                for provider_id in _pre_publish_known_exception_provider_ids()
            },
            "provider_limitations": [],
            "evidence_reuse": {},
        }
    status = summary.get("status")
    if not isinstance(status, str):
        status = "provided"
    category_coverage = summary.get("category_coverage")
    provider_outcomes = summary.get("provider_outcomes")
    policy_failure = summary.get("policy_failure")
    required_provider_ids = _pre_publish_required_provider_ids_from_summary(summary)
    outcome_statuses = _pre_publish_provider_outcome_statuses(provider_outcomes)
    evidence_reuse = summary.get("evidence_reuse")
    return {
        "status": status,
        "category_coverage": (
            dict(category_coverage) if isinstance(category_coverage, Mapping) else {}
        ),
        "provider_outcomes": (
            provider_outcomes if isinstance(provider_outcomes, list) else []
        ),
        "policy_failure": policy_failure if _is_json_value(policy_failure) else None,
        "required_provider_ids": list(required_provider_ids),
        "required_provider_outcomes": {
            provider_id: outcome_statuses[provider_id]
            for provider_id in required_provider_ids
            if provider_id in outcome_statuses
        },
        "required_provider_failures": _pre_publish_required_provider_failures(summary),
        "known_unvalidated_provider_exceptions": (
            _pre_publish_known_provider_exceptions_from_summary(summary)
        ),
        "provider_exception_outcomes": _pre_publish_provider_exception_outcomes(summary),
        "provider_limitations": _json_list(summary.get("provider_limitations")),
        "evidence_reuse": (
            dict(evidence_reuse)
            if isinstance(evidence_reuse, Mapping)
            else {}
        ),
    }


def _pre_publish_provider_outcome_statuses(value: object) -> dict[str, str]:
    if not isinstance(value, list):
        return {}
    result: dict[str, str] = {}
    for outcome in value:
        if not isinstance(outcome, Mapping):
            continue
        provider_id = outcome.get("provider_id")
        status = outcome.get("status")
        if isinstance(provider_id, str) and isinstance(status, str):
            result[provider_id] = status
    return result


def _pre_publish_required_provider_failures(
    summary: Mapping[str, JsonValue],
) -> list[dict[str, JsonValue]]:
    required = set(_pre_publish_required_provider_ids_from_summary(summary))
    outcomes = summary.get("provider_outcomes")
    if not isinstance(outcomes, list):
        return []
    failures: list[dict[str, JsonValue]] = []
    for outcome in outcomes:
        if not isinstance(outcome, Mapping):
            continue
        provider_id = outcome.get("provider_id")
        status = outcome.get("status")
        if not isinstance(provider_id, str) or provider_id not in required:
            continue
        if not isinstance(status, str) or status not in PRE_PUBLISH_PROVIDER_FAILURE_STATUSES:
            continue
        category = outcome.get("category")
        error_code = outcome.get("error_code")
        failures.append(
            {
                "provider_id": provider_id,
                "category": category if isinstance(category, str) else "unknown",
                "status": status,
                "error_code": error_code if isinstance(error_code, str) else status,
            }
        )
    return failures


def _pre_publish_known_provider_exceptions_from_summary(
    summary: Mapping[str, JsonValue],
) -> list[dict[str, JsonValue]]:
    value = summary.get("known_unvalidated_provider_exceptions")
    if isinstance(value, list):
        result = [dict(item) for item in value if isinstance(item, Mapping)]
        if result:
            return result
    return [dict(item) for item in PRE_PUBLISH_KNOWN_UNVALIDATED_PROVIDER_EXCEPTIONS]


def _pre_publish_provider_exception_outcomes(
    summary: Mapping[str, JsonValue],
) -> dict[str, str]:
    exception_ids = _pre_publish_known_exception_provider_ids()
    result = {provider_id: "not_requested" for provider_id in exception_ids}
    outcomes = summary.get("provider_outcomes")
    if not isinstance(outcomes, list):
        return result
    for outcome in outcomes:
        if not isinstance(outcome, Mapping):
            continue
        provider_id = outcome.get("provider_id")
        status = outcome.get("status")
        if (
            isinstance(provider_id, str)
            and provider_id in exception_ids
            and isinstance(status, str)
        ):
            result[provider_id] = status
    return result


def _json_list(value: object) -> list[JsonValue]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [item for item in value if _is_json_value(item)]


def _file_artifact_entry(
    *,
    artifact_id: str,
    name: str,
    path: Path,
    media_type: str = "application/json",
) -> dict[str, JsonValue]:
    resolved = path.resolve()
    return {
        "artifact_id": artifact_id,
        "name": name,
        "media_type": media_type,
        "uri": resolved.as_uri(),
        "path": str(resolved),
    }


def _path_safe_file_artifact_entry(
    *,
    artifact_id: str,
    name: str,
    path: Path,
    media_type: str = "application/json",
) -> dict[str, JsonValue]:
    return {
        "artifact_id": artifact_id,
        "name": name,
        "media_type": media_type,
        "path": _path_safe_path_text(path),
    }


def _path_safe_pre_publish_handoff(payload: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    sanitized = _path_safe_pre_publish_value(payload)
    return sanitized if isinstance(sanitized, dict) else dict(payload)


def _path_safe_pre_publish_value(value: JsonValue) -> JsonValue:
    if isinstance(value, Mapping):
        sanitized: dict[str, JsonValue] = {}
        for key, item in value.items():
            if key == "uri":
                continue
            if key in {"path", "sqlite_path", "artifact_root"} and isinstance(item, str):
                sanitized[key] = _path_safe_path_text(item)
            else:
                sanitized[key] = _path_safe_pre_publish_value(item)
        _refresh_path_safe_command(sanitized, command_key="inspect", argv_key="inspect_argv")
        return sanitized
    if isinstance(value, list):
        return [
            _path_safe_path_text(item)
            if isinstance(item, str)
            else _path_safe_pre_publish_value(item)
            for item in value
        ]
    return value


def _refresh_path_safe_command(
    value: dict[str, JsonValue],
    *,
    command_key: str,
    argv_key: str,
) -> None:
    argv = value.get(argv_key)
    if isinstance(argv, list) and all(isinstance(item, str) for item in argv):
        value[command_key] = _render_command(argv)


def _path_safe_path_text(path: str | Path) -> str:
    path_value = Path(path)
    if not path_value.is_absolute():
        return str(path_value)
    try:
        return os.path.relpath(path_value.resolve(), Path.cwd().resolve())
    except ValueError:
        return path_value.name


def _failed_handoff_reason(run_payload: Mapping[str, JsonValue]) -> str:
    top_level_reason = run_payload.get("reason")
    if isinstance(top_level_reason, str):
        return top_level_reason
    output_payload = run_payload.get("output")
    if isinstance(output_payload, Mapping):
        reason = output_payload.get("reason")
        if isinstance(reason, str):
            return reason
    return "run_report_failed"


def _approval_from_run_payload(
    run_payload: Mapping[str, JsonValue] | None,
) -> JsonValue | None:
    if not isinstance(run_payload, Mapping):
        return None
    approval = run_payload.get("approval")
    return approval if _is_json_value(approval) else None


def _json_object_from_text(value: str) -> dict[str, JsonValue] | None:
    if not value.strip():
        return None
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _write_native_handoff_payload(path: object, payload: Mapping[str, JsonValue]) -> None:
    resolved = _as_path(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _as_path(value: object) -> Path:
    if isinstance(value, Path):
        return value
    if isinstance(value, str):
        return Path(value)
    raise TypeError("expected path value")


async def _with_daily_approval_trace_state(
    *,
    result: RunResult,
    store: SQLiteRunStore,
    approval: Mapping[str, object] | None,
) -> RunResult:
    trace_state = daily_external_data_approval_trace_state(approval)
    if not trace_state:
        return result

    output = dict(result.output)
    output["state"] = {**_result_state(result), **trace_state}
    latest_snapshot = await store.get_latest_snapshot(result.run_id)
    if latest_snapshot is not None:
        await store.save_snapshot(
            RunSnapshot(
                run_id=latest_snapshot.run_id,
                step_index=latest_snapshot.step_index,
                state={**latest_snapshot.state, **trace_state},
                pending_human_request_ids=latest_snapshot.pending_human_request_ids,
            )
        )
    await _append_daily_approval_governance(
        store=store,
        run_id=result.run_id,
        approval=approval,
    )
    return RunResult(
        run_id=result.run_id,
        status=result.status,
        output=output,
        artifacts=result.artifacts,
        diagnostics=result.diagnostics,
    )


async def _record_blocked_daily_approval_governance(
    *,
    run_id: str,
    sqlite_path: Path,
    approval: Mapping[str, object],
) -> None:
    store = SQLiteRunStore(str(sqlite_path))
    await store.initialize()
    try:
        if await store.get_run(run_id) is None:
            await store.create_run(
                Run(
                    id=run_id,
                    status="failed",
                    metadata={
                        "command": "run-pre-publish-preview",
                        "failure_reason": "external_data_approval_required",
                    },
                )
            )
        await _append_daily_approval_governance(
            store=store,
            run_id=run_id,
            approval=approval,
        )
    finally:
        await store.close()


async def _append_daily_approval_governance(
    *,
    store: SQLiteRunStore,
    run_id: str,
    approval: Mapping[str, object] | None,
) -> None:
    governance_records = daily_external_data_governance_records(
        run_id=run_id,
        approval=approval,
    )
    if governance_records:
        approval_record, permission_decision = governance_records
        async with store.transaction():
            await store.append_approval_record(approval_record)
            await store.append_event(
                RunEvent(
                    run_id=run_id,
                    type="approval_lifecycle_recorded",
                    payload={"approval_record_id": approval_record.id},
                )
            )
            await store.append_permission_decision(permission_decision)
            await store.append_event(
                RunEvent(
                    run_id=run_id,
                    type="permission_decision_recorded",
                    payload={"permission_decision_id": permission_decision.id},
                )
            )


async def _run_report_command(
    args: argparse.Namespace,
    *,
    output: TextIO,
    error_output: TextIO,
    model_client_factory: ModelClientFactory | None,
) -> int:
    readiness_projection: dict[str, JsonValue] | None = None
    operator_review_reason: str | None = None
    try:
        raw_provider = _build_run_report_input_provider(args)
        external_evidence_summary = _run_report_external_evidence_summary(args)
        readiness: dict[str, JsonValue] | None = None
        if args.holdings_source == "operational" and args.readiness_path is not None:
            readiness = _read_cli_json_object(
                Path(args.readiness_path),
                label="readiness",
            )
            _validate_operational_readiness_status(args=args, readiness=readiness)
        inputs = raw_provider.load()
        if (
            args.holdings_source == "operational"
            and args.readiness_path is None
            and not bool(args.allow_operator_review_output)
        ):
            raise SignalReportInputError(
                "operational readiness handoff is required; pass --readiness-path"
            )
        if (
            args.holdings_source == "operational"
            and args.readiness_path is None
            and bool(args.allow_operator_review_output)
        ):
            readiness_projection = _missing_operational_readiness_projection(
                args=args,
                current_date=inputs.snapshots.current_date,
                previous_date=inputs.snapshots.previous_date,
            )
            operator_review_reason = "readiness_not_provided"
        if args.holdings_source == "operational" and readiness is not None:
            _validate_operational_readiness_match(
                args=args,
                current_date=inputs.snapshots.current_date,
                previous_date=inputs.snapshots.previous_date,
                readiness=readiness,
            )
            readiness_projection = _project_operational_readiness(readiness)
            _validate_operational_readiness_delivery_contract(
                args=args,
                readiness=readiness_projection,
            )
            if readiness_projection.get("status") == "hold":
                operator_review_reason = "readiness_hold"
    except Exception as exc:
        _write_cli_input_error(error_output, str(exc))
        return 2

    provider = CachedSignalReportInputProvider(
        inputs=inputs,
        provenance=_run_report_provenance(
            getattr(raw_provider, "provenance", None),
            readiness_projection=readiness_projection,
            external_evidence_summary=external_evidence_summary,
        ),
    )

    approval = _model_export_approval_check(
        args=args,
        command="run-report",
        preflight_base_dir=Path(args.artifact_root),
        model_client_factory=model_client_factory,
    )
    if approval is not None and not _approval_is_valid(approval):
        return _write_daily_approval_block(
            output=output,
            command="run-report",
            blocked_path=Path(args.artifact_root) / "run_report_approval_block.json",
            approval=approval,
        )

    try:
        config = ModelProviderConfig(
            provider=args.model,
            model=args.codex_model,
            timeout_seconds=args.model_timeout_seconds,
        )
        factory = model_client_factory or create_model_client
        model_client = factory(config)
    except Exception as exc:
        _write_operational_error(error_output, reason="model_client_failed", exc=exc)
        return 1

    store: SQLiteRunStore | None = None
    try:
        sqlite_path = Path(args.sqlite_path)
        sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        store = SQLiteRunStore(args.sqlite_path)
        await store.initialize()
    except Exception as exc:
        if store is not None:
            await store.close()
        _write_operational_error(error_output, reason="run_store_failed", exc=exc)
        return 1

    try:
        artifact_manager = LocalArtifactManager(args.artifact_root)
    except Exception as exc:
        await store.close()
        _write_operational_error(error_output, reason="artifact_store_failed", exc=exc)
        return 1

    try:
        result = await run_signal_report(
            run_id=args.run_id,
            store=store,
            context_manager=ContextManager(store=store),
            artifact_manager=artifact_manager,
            model_client=model_client,
            provider=provider,
        )
        if result.status == "succeeded":
            result = await _with_user_ready_artifact_refs(
                result=result,
                artifact_manager=artifact_manager,
            )
            result = await _with_daily_approval_trace_state(
                result=result,
                store=store,
                approval=approval,
            )
            try:
                if operator_review_reason is not None:
                    operator_review_only = _build_operator_review_only(
                        result=result,
                        sqlite_path=Path(args.sqlite_path),
                        artifact_root=Path(args.artifact_root),
                        reason=operator_review_reason,
                    )
                    result = RunResult(
                        run_id=result.run_id,
                        status=result.status,
                        output={
                            **result.output,
                            "operator_review_only": operator_review_only,
                        },
                        artifacts=result.artifacts,
                        diagnostics=result.diagnostics,
                    )
                    json.dump(result.model_dump(mode="json"), output, ensure_ascii=False)
                    output.write("\n")
                    return 0
                user_ready = _build_user_ready(
                    result=result,
                    sqlite_path=Path(args.sqlite_path),
                    artifact_root=Path(args.artifact_root),
                )
            except Exception as exc:
                _write_operational_error(
                    error_output,
                    reason="user_ready_contract_failed",
                    exc=exc,
                )
                return 1
            result = RunResult(
                run_id=result.run_id,
                status=result.status,
                output={**result.output, "user_ready": user_ready},
                artifacts=result.artifacts,
                diagnostics=result.diagnostics,
            )
    finally:
        await store.close()

    json.dump(result.model_dump(mode="json"), output, ensure_ascii=False)
    output.write("\n")
    return 0 if result.status == "succeeded" else 1


def _build_run_report_input_provider(args: argparse.Namespace) -> SignalReportInputProvider:
    if args.observed_partitions <= 0:
        raise SignalReportInputError("observed-partitions must be a positive integer")
    focus_etf_ids = _focus_etf_ids_from_args(args)
    if args.holdings_source == "fixture":
        return FixtureSignalReportInputProvider(
            holdings_path=args.holdings_path,
            evidence_path=args.evidence_path,
            focus_etf_id=args.focus_etf_id,
        )
    if args.holdings_source == "operational":
        if not args.focus_etf_id and not focus_etf_ids:
            raise SignalReportInputError("--focus-etf-id or --focus-etf-set-path is required")
        return OperationalSignalReportInputProvider(
            manifest_path=args.holdings_path or DEFAULT_OPERATIONAL_HOLDINGS_PATH,
            focus_etf_id=args.focus_etf_id,
            focus_etf_ids=focus_etf_ids,
            observed_partitions=args.observed_partitions,
            evidence_path=args.evidence_path,
        )
    raise SignalReportInputError(f"unsupported holdings source: {args.holdings_source}")


def _focus_etf_ids_from_args(args: argparse.Namespace) -> tuple[str, ...] | None:
    focus_set_path = getattr(args, "focus_etf_set_path", None)
    if focus_set_path is None:
        return None
    try:
        return load_focus_etf_set_file(focus_set_path).focus_etf_ids
    except FocusETFSetInputError as exc:
        raise SignalReportInputError(str(exc)) from exc


def _validate_operational_readiness_match(
    *,
    args: argparse.Namespace,
    current_date: str,
    previous_date: str,
    readiness: Mapping[str, JsonValue],
) -> None:
    if readiness.get("schema_version") != READINESS_SCHEMA_VERSION:
        raise SignalReportInputError("invalid operational readiness schema")
    expected_holdings_path = args.holdings_path or DEFAULT_OPERATIONAL_HOLDINGS_PATH
    expected_fingerprint = _compute_current_operational_export_fingerprint(
        Path(expected_holdings_path)
    )
    mismatches: list[str] = []
    if not _readiness_paths_match(readiness.get("holdings_path"), expected_holdings_path):
        mismatches.append("holdings_path")
    expected_focus_etf_ids = _focus_etf_ids_from_args(args)
    expected_fields: dict[str, JsonValue] = {
        "requested_observed_partitions": int(args.observed_partitions),
        "current_date": current_date,
        "previous_date": previous_date,
    }
    if expected_focus_etf_ids is None:
        expected_fields["focus_etf_id"] = args.focus_etf_id
    else:
        expected_fields["focus_etf_id"] = expected_focus_etf_ids[0]
        if readiness.get("focus_etf_ids") != list(expected_focus_etf_ids):
            mismatches.append("focus_etf_ids")
    for field, expected in expected_fields.items():
        if readiness.get(field) != expected:
            mismatches.append(field)
    readiness_fingerprint = _validate_export_fingerprint_shape(
        readiness.get("export_fingerprint")
    )
    if readiness_fingerprint != expected_fingerprint:
        mismatches.append("export_fingerprint")
    if mismatches:
        raise SignalReportInputError(
            "readiness handoff does not match operational run: "
            + ", ".join(mismatches)
        )


def _compute_current_operational_export_fingerprint(
    manifest_path: Path,
) -> dict[str, JsonValue]:
    try:
        return compute_operational_export_fingerprint(manifest_path)
    except OperationalHoldingsInputError as exc:
        raise SignalReportInputError(
            "operational export fingerprint could not be computed: " + str(exc)
        ) from exc


def _validate_export_fingerprint_shape(value: JsonValue) -> dict[str, JsonValue] | None:
    if not isinstance(value, Mapping):
        return None
    if set(value) != {"algorithm", "scope", "value"}:
        return None
    fingerprint_value = value.get("value")
    if (
        value.get("algorithm") != "sha256"
        or value.get("scope") != OPERATIONAL_EXPORT_FINGERPRINT_SCOPE
        or not isinstance(fingerprint_value, str)
        or len(fingerprint_value) != 64
        or any(character not in "0123456789abcdef" for character in fingerprint_value)
    ):
        return None
    return {
        "algorithm": "sha256",
        "scope": OPERATIONAL_EXPORT_FINGERPRINT_SCOPE,
        "value": fingerprint_value,
    }


def _validate_operational_readiness_status(
    *,
    args: argparse.Namespace,
    readiness: Mapping[str, JsonValue],
) -> None:
    status = readiness.get("status")
    if status not in {"ready", "ready_with_warnings", "hold", "failed"}:
        raise SignalReportInputError("invalid operational readiness status")
    if status == "failed":
        raise SignalReportInputError("operational readiness status failed blocks run-report")
    if status == "hold" and not bool(args.allow_operator_review_output):
        raise SignalReportInputError(
            "operational readiness status hold requires --allow-operator-review-output"
        )


def _project_operational_readiness(
    readiness: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    projected: dict[str, JsonValue] = {}
    for field in (
        "schema_version",
        "status",
        "user_ready_allowed",
        "readiness_evidence_type",
        "focus_etf_id",
        "focus_etf_ids",
        "focus_eligibility",
        "requested_observed_partitions",
        "operator_timezone",
        "operator_date",
        "latest_observed_date",
        "latest_observed_age_days",
        "synced_at",
        "collected_at",
        "current_date",
        "previous_date",
        "export_fingerprint",
        "scanned_dates",
        "missing_partition_dates",
        "summary",
        "collection_summary",
        "active_etf_coverage",
        "security_coverage",
        "top_unmapped_security_samples",
        "final_user_ready_requirements",
    ):
        value = readiness.get(field)
        if _is_json_value(value):
            projected[field] = value
    projected["reasons"] = _project_readiness_items(readiness.get("reasons"))
    projected["warnings"] = _project_readiness_items(readiness.get("warnings"))
    projected["next_actions"] = _project_readiness_actions(readiness.get("next_actions"))
    projected["disclosures"] = _operational_readiness_disclosures(readiness)
    return projected


def _validate_operational_readiness_delivery_contract(
    *,
    args: argparse.Namespace,
    readiness: Mapping[str, JsonValue],
) -> None:
    status = readiness.get("status")
    if status == "ready_with_warnings":
        disclosures = readiness.get("disclosures")
        if not isinstance(disclosures, list) or not disclosures:
            raise SignalReportInputError(
                "ready_with_warnings readiness requires disclosure warnings"
            )
    if bool(args.allow_operator_review_output) and status in {
        "ready",
        "ready_with_warnings",
    }:
        raise SignalReportInputError(
            "operator-review override is only valid for hold or missing readiness"
        )


def _missing_operational_readiness_projection(
    *,
    args: argparse.Namespace,
    current_date: str,
    previous_date: str,
) -> dict[str, JsonValue]:
    return {
        "schema_version": READINESS_SCHEMA_VERSION,
        "status": "not_provided",
        "user_ready_allowed": False,
        "focus_etf_id": args.focus_etf_id,
        "focus_etf_ids": list(
            _focus_etf_ids_from_args(args)
            or (() if args.focus_etf_id is None else (args.focus_etf_id,))
        ),
        "requested_observed_partitions": int(args.observed_partitions),
        "current_date": current_date,
        "previous_date": previous_date,
        "reasons": [
            {
                "code": "readiness_not_provided",
                "severity": "hold",
                "message": "Operational readiness handoff was not provided.",
            }
        ],
        "warnings": [],
        "next_actions": [],
        "summary": {},
        "final_user_ready_requirements": {
            "readiness_user_ready_allowed": False,
            "run_report_status_required": "succeeded",
            "report_quality_status_required": "passed",
            "warning_disclosure_required": False,
        },
        "disclosures": [],
    }


def _project_readiness_items(value: JsonValue) -> list[dict[str, JsonValue]]:
    if not isinstance(value, list):
        return []
    items: list[dict[str, JsonValue]] = []
    for raw_item in value:
        if not isinstance(raw_item, Mapping):
            continue
        item: dict[str, JsonValue] = {}
        for field in ("code", "severity", "message", "metric", "value", "threshold"):
            if field not in raw_item:
                continue
            field_value = raw_item.get(field)
            if _is_json_value(field_value):
                item[field] = field_value
        details = raw_item.get("details")
        if isinstance(details, Mapping):
            projected_details: dict[str, JsonValue] = {}
            source_codes = details.get("source_codes")
            if isinstance(source_codes, list):
                projected_details["source_codes"] = [
                    code for code in source_codes if isinstance(code, str)
                ]
            source_items = details.get("source_items")
            if isinstance(source_items, list):
                projected_details["source_items"] = _project_readiness_items(source_items)
            if projected_details:
                item["details"] = projected_details
        if item:
            items.append(item)
    return items


def _project_readiness_actions(value: JsonValue) -> list[dict[str, JsonValue]]:
    if not isinstance(value, list):
        return []
    actions: list[dict[str, JsonValue]] = []
    for raw_action in value:
        if not isinstance(raw_action, Mapping):
            continue
        action: dict[str, JsonValue] = {}
        for field in ("code", "action_type", "required", "message", "command_hint", "for_codes"):
            field_value = raw_action.get(field)
            if _is_json_value(field_value):
                action[field] = field_value
        if action:
            actions.append(action)
    return actions


def _operational_readiness_disclosures(
    readiness: Mapping[str, JsonValue],
) -> list[dict[str, JsonValue]]:
    warnings = readiness.get("warnings")
    if not isinstance(warnings, list):
        return []
    disclosures: list[dict[str, JsonValue]] = []
    for warning in warnings:
        if not isinstance(warning, Mapping):
            continue
        source_items = _readiness_detail_source_items(warning)
        if source_items:
            disclosures.extend(
                disclosure
                for item in source_items
                if (disclosure := _readiness_disclosure(item)) is not None
            )
            continue
        disclosure = _readiness_disclosure(warning)
        if disclosure is not None:
            disclosures.append(disclosure)
    return disclosures


def _readiness_detail_source_items(
    item: Mapping[str, JsonValue],
) -> list[Mapping[str, JsonValue]]:
    details = item.get("details")
    if not isinstance(details, Mapping):
        return []
    source_items = details.get("source_items")
    if not isinstance(source_items, list):
        return []
    return [source for source in source_items if isinstance(source, Mapping)]


def _readiness_disclosure(
    item: Mapping[str, JsonValue],
) -> dict[str, JsonValue] | None:
    code = item.get("code")
    message = item.get("message")
    if not isinstance(code, str) or not isinstance(message, str) or not message.strip():
        return None
    disclosure: dict[str, JsonValue] = {
        "code": f"readiness_{code}",
        "severity": "medium",
        "message": message,
    }
    for field in ("metric", "value", "threshold"):
        if field not in item:
            continue
        value = item.get(field)
        if _is_json_value(value):
            disclosure[field] = value
    return disclosure


def _run_report_external_evidence_summary(
    args: argparse.Namespace,
) -> dict[str, JsonValue] | None:
    explicit_path = getattr(args, "evidence_summary_path", None)
    if explicit_path is not None:
        return _read_cli_json_object(Path(explicit_path), label="external evidence summary")
    evidence_path = getattr(args, "evidence_path", None)
    if evidence_path is None:
        return None
    adjacent = _default_evidence_summary_path(Path(evidence_path))
    if not adjacent.is_file():
        return None
    return _read_cli_json_object(adjacent, label="external evidence summary")


def _run_report_provenance(
    value: object,
    *,
    readiness_projection: Mapping[str, JsonValue] | None,
    external_evidence_summary: Mapping[str, JsonValue] | None = None,
) -> dict[str, JsonValue] | None:
    if isinstance(value, Mapping):
        provenance = dict(value)
    elif readiness_projection is not None or external_evidence_summary is not None:
        provenance = {}
    else:
        return None
    if readiness_projection is not None:
        provenance["operational_readiness"] = dict(readiness_projection)
    if external_evidence_summary is not None:
        provenance["external_evidence_summary"] = dict(external_evidence_summary)
    return provenance


def _is_json_value(value: object) -> bool:
    if value is None or isinstance(value, str | int | float | bool):
        return True
    if isinstance(value, list):
        return all(_is_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _is_json_value(item) for key, item in value.items())
    return False


def _readiness_paths_match(value: JsonValue, expected: str) -> bool:
    if not isinstance(value, str) or not value:
        return False
    return Path(value).resolve() == Path(expected).resolve()


async def _with_user_ready_artifact_refs(
    *, result: RunResult, artifact_manager: LocalArtifactManager
) -> RunResult:
    refs_by_id = {artifact.artifact_id: artifact for artifact in result.artifacts}
    state = _result_state(result)
    for state_key in (
        "signal_payload_artifact_id",
        "report_artifact_id",
        "html_report_artifact_id",
        "telegram_alert_artifact_id",
        "report_quality_artifact_id",
        "operational_readiness_artifact_id",
    ):
        artifact_id = state.get(state_key)
        if isinstance(artifact_id, str) and artifact_id not in refs_by_id:
            artifact = await artifact_manager.resolve(artifact_id)
            if artifact is not None:
                refs_by_id[artifact.artifact_id] = artifact
    return RunResult(
        run_id=result.run_id,
        status=result.status,
        output=result.output,
        artifacts=tuple(refs_by_id.values()),
        diagnostics=result.diagnostics,
    )


def _build_user_ready(
    *, result: RunResult, sqlite_path: Path, artifact_root: Path
) -> dict[str, JsonValue]:
    state = _result_state(result)
    canonical_payload_id = state.get("signal_payload_artifact_id")
    markdown_report_id = state.get("report_artifact_id")
    html_report_id = state.get("html_report_artifact_id")
    telegram_alert_id = state.get("telegram_alert_artifact_id")
    quality_report_id = state.get("report_quality_artifact_id")
    readiness_id = state.get("operational_readiness_artifact_id")
    refs_by_id = {artifact.artifact_id: artifact for artifact in result.artifacts}

    if not isinstance(canonical_payload_id, str) or canonical_payload_id not in refs_by_id:
        raise ValueError("missing expected artifact reference: canonical_payload")
    if not isinstance(markdown_report_id, str) or markdown_report_id not in refs_by_id:
        raise ValueError("missing expected artifact reference: markdown_report")
    if not isinstance(html_report_id, str) or html_report_id not in refs_by_id:
        raise ValueError("missing expected artifact reference: html_report")
    if not isinstance(telegram_alert_id, str) or telegram_alert_id not in refs_by_id:
        raise ValueError("missing expected artifact reference: telegram_alert")
    if not isinstance(quality_report_id, str) or quality_report_id not in refs_by_id:
        raise ValueError("missing expected artifact reference: quality_report")

    sqlite_path_text = str(sqlite_path.resolve())
    artifact_root_text = str(artifact_root.resolve())
    inspect_argv = [
        "agent-treport",
        "inspect",
        "--run-id",
        result.run_id,
        "--sqlite-path",
        sqlite_path_text,
    ]
    artifacts = {
        "canonical_payload": _artifact_entry(refs_by_id[canonical_payload_id]),
        "markdown_report": _artifact_entry(refs_by_id[markdown_report_id]),
        "html_report": _artifact_entry(refs_by_id[html_report_id]),
        "telegram_alert": _artifact_entry(refs_by_id[telegram_alert_id]),
        "quality_report": _artifact_entry(refs_by_id[quality_report_id]),
    }
    if isinstance(readiness_id, str) and readiness_id in refs_by_id:
        artifacts["readiness"] = _artifact_entry(refs_by_id[readiness_id])

    user_ready: dict[str, JsonValue] = {
        "run_id": result.run_id,
        "sqlite_path": sqlite_path_text,
        "artifact_root": artifact_root_text,
        "artifacts": artifacts,
        "commands": {
            "inspect_argv": inspect_argv,
            "inspect": _render_command(inspect_argv),
        },
    }
    readiness_source: JsonValue = state.get("operational_readiness")
    if isinstance(readiness_id, str) and readiness_id in refs_by_id:
        artifact_readiness = _read_json_artifact(refs_by_id[readiness_id])
        if artifact_readiness is not None:
            readiness_source = artifact_readiness
    readiness_summary = _user_ready_readiness_summary(
        readiness_source,
        readiness_artifact_id=readiness_id,
    )
    if readiness_summary is not None:
        user_ready["readiness"] = readiness_summary
    return user_ready


def _build_operator_review_only(
    *,
    result: RunResult,
    sqlite_path: Path,
    artifact_root: Path,
    reason: str,
) -> dict[str, JsonValue]:
    follow_up = _build_user_ready(
        result=result,
        sqlite_path=sqlite_path,
        artifact_root=artifact_root,
    )
    readiness = follow_up.get("readiness")
    readiness_artifact_id: JsonValue = None
    if isinstance(readiness, Mapping):
        readiness_artifact_id = readiness.get("readiness_artifact_id")
    return {
        "run_id": follow_up["run_id"],
        "sqlite_path": follow_up["sqlite_path"],
        "artifact_root": follow_up["artifact_root"],
        "reason": reason,
        "readiness_artifact_id": readiness_artifact_id,
        "artifacts": follow_up["artifacts"],
        "commands": follow_up["commands"],
    }


def _user_ready_readiness_summary(
    readiness: JsonValue,
    *,
    readiness_artifact_id: JsonValue,
) -> dict[str, JsonValue] | None:
    if not isinstance(readiness, Mapping) or not isinstance(readiness_artifact_id, str):
        return None
    disclosures = readiness.get("disclosures")
    if not isinstance(disclosures, list) or not disclosures:
        disclosures = _operational_readiness_disclosures(readiness)
    raw_focus_etf_ids = readiness.get("focus_etf_ids")
    focus_etf_ids = [
        item for item in raw_focus_etf_ids if isinstance(item, str)
    ] if isinstance(raw_focus_etf_ids, list) else []
    summary: dict[str, JsonValue] = {
        "status": str(readiness.get("status")),
        "focus_etf_id": str(readiness.get("focus_etf_id")),
        "current_date": str(readiness.get("current_date")),
        "previous_date": str(readiness.get("previous_date")),
        "disclosures": disclosures,
        "readiness_artifact_id": readiness_artifact_id,
    }
    if len(focus_etf_ids) > 1:
        summary["focus_etf_ids"] = focus_etf_ids
    return summary


def _result_state(result: RunResult) -> dict[str, JsonValue]:
    state = result.output.get("state")
    if isinstance(state, dict):
        return state
    return {}


def _artifact_entry(artifact: ArtifactRef) -> dict[str, JsonValue]:
    return {
        "artifact_id": artifact.artifact_id,
        "name": artifact.name,
        "media_type": artifact.media_type,
        "uri": artifact.uri,
        "path": _path_from_file_uri(artifact.uri),
    }


def _read_json_artifact(artifact: ArtifactRef) -> dict[str, JsonValue] | None:
    path = _path_from_file_uri(artifact.uri)
    if path is None:
        return None
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _path_from_file_uri(uri: str | None) -> str | None:
    if uri is None:
        return None
    parsed = urlparse(uri)
    if parsed.scheme != "file" or parsed.netloc not in ("", "localhost"):
        return None
    path = unquote(parsed.path)
    if len(path) >= 3 and path[0] == "/" and path[2] == ":":
        path = path[1:]
    return str(Path(path).resolve())


def _render_command(argv: Sequence[str]) -> str:
    return shlex.join(argv)


def _write_cli_input_error(output: TextIO, message: str) -> None:
    output.write(f"agent-treport: error: {message}\n")


def _read_cli_json_object(path: Path, *, label: str) -> dict[str, JsonValue]:
    if not path.is_file():
        raise OperationalHoldingsInputError(f"{label} file not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise OperationalHoldingsInputError(
            f"invalid JSON input: {path}: {exc.msg}"
        ) from exc
    if not isinstance(payload, dict):
        raise OperationalHoldingsInputError(f"{label} input must be a JSON object: {path}")
    return payload


def _read_stock_mapping_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise OperationalHoldingsInputError(f"stock mapping CSV file not found: {path}")
    required_fields = {"stock_code", "stock_name", "symbol", "exchange", "updated_at"}
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames is None or not required_fields.issubset(reader.fieldnames):
                raise OperationalHoldingsInputError(
                    "stock mapping CSV missing required columns"
                )
            rows: list[dict[str, str]] = []
            for row in reader:
                rows.append(
                    {
                        field: str(row.get(field) or "")
                        for field in required_fields
                    }
                )
    except UnicodeDecodeError as exc:
        raise OperationalHoldingsInputError(f"invalid stock mapping CSV encoding: {path}") from exc
    if not rows:
        raise OperationalHoldingsInputError("stock mapping CSV must contain at least one row")
    return rows


def _read_security_resolution_observations(
    holdings_path: Path, *, observed_partitions: int
) -> list[dict[str, JsonValue]]:
    manifest = _read_cli_json_object(holdings_path, label="holdings")
    if manifest.get("schema_version") != "agent_treport.operational_holdings.v1":
        raise OperationalHoldingsInputError("holdings manifest is not normalized")
    dates = manifest.get("dates")
    partitions = manifest.get("partitions")
    if not isinstance(dates, list):
        raise OperationalHoldingsInputError("holdings dates must be a list")
    if not isinstance(partitions, Mapping):
        raise OperationalHoldingsInputError("holdings partitions must be an object")
    observations: list[dict[str, JsonValue]] = []
    for raw_date in dates[:observed_partitions]:
        if not isinstance(raw_date, str):
            raise OperationalHoldingsInputError("holdings date must be a string")
        partition = partitions.get(raw_date)
        if not isinstance(partition, Mapping):
            raise OperationalHoldingsInputError(f"missing holdings partition: {raw_date}")
        partition_file = partition.get("file")
        if not isinstance(partition_file, str) or Path(partition_file).is_absolute():
            raise OperationalHoldingsInputError("holdings partition file must be relative")
        partition_path = (holdings_path.parent / partition_file).resolve()
        try:
            partition_path.relative_to(holdings_path.parent.resolve())
        except ValueError as exc:
            raise OperationalHoldingsInputError(
                "holdings partition file must stay inside holdings export"
            ) from exc
        observations.extend(_read_security_resolution_observation_partition(partition_path))
    return observations


def _apply_openfigi_lookup(
    *,
    observations: list[dict[str, JsonValue]],
    security_master: Mapping[str, JsonValue],
    client: OpenFigiClient,
) -> dict[str, JsonValue]:
    existing_non_retryable_ids = {
        str(entry.get("security_id"))
        for entry in security_master.get("entries", [])
        if (
            isinstance(entry, Mapping)
            and isinstance(entry.get("security_id"), str)
            and entry.get("status") != "unresolved"
        )
    }
    lookup_ids = [
        str(observation["security_id"])
        for observation in observations
        if (
            observation.get("security_classification") == "ticker_candidate"
            and observation.get("ticker") is None
            and str(observation.get("security_id")) not in existing_non_retryable_ids
            and _looks_like_isin(str(observation.get("security_id")))
            and not has_structural_ticker_resolution(
                security_id=str(observation.get("security_id")),
                security_classification="ticker_candidate",
            )
        )
    ]
    client_job_limit = getattr(client, "max_jobs_per_request", None)
    if isinstance(client_job_limit, int) and client_job_limit > 0:
        result = lookup_openfigi_tickers(
            security_ids=lookup_ids,
            client=client,
            batch_size=client_job_limit,
        )
    else:
        result = lookup_openfigi_tickers(security_ids=lookup_ids, client=client)
    enriched: list[dict[str, JsonValue]] = []
    for observation in observations:
        security_id = str(observation["security_id"])
        mapping = result.mappings.get(security_id)
        if mapping is None:
            enriched.append(observation)
            continue
        enriched.append(
            {
                **observation,
                "ticker": mapping["ticker"],
                "name": mapping["name"] or observation["name"],
                "exchange": mapping["exchange"],
            }
        )
    return {
        "observations": enriched,
        "warnings": result.warnings,
        "lookup_count": len(result.mappings),
    }


def _looks_like_isin(value: str) -> bool:
    normalized = value.strip().upper()
    return len(normalized) == 12 and normalized[:2].isalpha() and normalized[2:].isalnum()


def _read_security_resolution_observation_partition(
    partition_path: Path,
) -> list[dict[str, JsonValue]]:
    observations: list[dict[str, JsonValue]] = []
    for line_number, line in enumerate(partition_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise OperationalHoldingsInputError(
                f"malformed normalized holdings JSONL line {line_number}"
            ) from exc
        if not isinstance(row, Mapping):
            raise OperationalHoldingsInputError(
                f"normalized holdings row must be an object: line {line_number}"
            )
        try:
            classification = validate_security_classification(
                row.get("security_classification"),
                label=f"security_classification line {line_number}",
            )
        except ValueError as exc:
            raise OperationalHoldingsInputError(str(exc)) from exc
        security_id = _non_empty_string(
            row.get("security_id"),
            label=f"security_id line {line_number}",
        )
        name = _non_empty_string(row.get("name"), label=f"name line {line_number}")
        ticker_value = row.get("ticker")
        ticker = (
            _non_empty_string(ticker_value, label=f"ticker line {line_number}")
            if ticker_value is not None
            else None
        )
        observations.append(
            {
                "security_id": security_id,
                "name": name,
                "ticker": ticker,
                "security_classification": classification,
            }
        )
    return observations


def _validate_security_mapping_patch_output_path(
    *,
    security_mapping_path: Path,
    patch_path: Path,
    output_path: Path,
    overwrite: bool,
) -> None:
    if not output_path.parent.is_dir():
        raise OperationalHoldingsInputError(
            f"output parent directory does not exist: {output_path.parent}"
        )
    output_resolved = _resolve_existing_parent_path(output_path)
    patch_resolved = patch_path.resolve()
    if output_resolved == patch_resolved:
        raise OperationalHoldingsInputError("output path must not equal patch path")
    if output_path.exists() and not overwrite:
        raise OperationalHoldingsInputError(f"output path already exists: {output_path}")
    security_mapping_resolved = security_mapping_path.resolve()
    if output_resolved == security_mapping_resolved and not overwrite:
        raise OperationalHoldingsInputError(
            "output path equals security mapping path and requires --overwrite"
        )


def _validate_recovery_proposal_output_path(
    *,
    source_path: Path,
    source_label: str,
    output_path: Path,
    overwrite: bool,
) -> None:
    if not output_path.parent.is_dir():
        raise OperationalHoldingsInputError(
            f"output parent directory does not exist: {output_path.parent}"
        )
    output_resolved = _resolve_existing_parent_path(output_path)
    source_resolved = source_path.resolve()
    if output_resolved == source_resolved:
        raise OperationalHoldingsInputError(
            f"output path must not equal {source_label} path"
        )
    if output_path.exists() and not overwrite:
        raise OperationalHoldingsInputError(f"output path already exists: {output_path}")


def _resolve_existing_parent_path(path: Path) -> Path:
    if path.exists():
        return path.resolve()
    return path.parent.resolve() / path.name


def _write_pretty_json(path: Path, payload: Mapping[str, JsonValue]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_compact_json(output: TextIO, payload: Mapping[str, JsonValue]) -> None:
    json.dump(payload, output, ensure_ascii=False, separators=(",", ":"))
    output.write("\n")


def _validate_recovery_samples(
    sync_metadata: Mapping[str, JsonValue],
) -> list[dict[str, JsonValue]]:
    if sync_metadata.get("schema_version") != SYNC_METADATA_SCHEMA_VERSION:
        raise OperationalHoldingsInputError("invalid sync metadata schema")
    raw_samples = sync_metadata.get("unmapped_security_samples")
    if not isinstance(raw_samples, list):
        raise OperationalHoldingsInputError(
            "sync metadata field must be a list: unmapped_security_samples"
        )
    samples: list[dict[str, JsonValue]] = []
    seen_security_ids: set[str] = set()
    for index, item in enumerate(raw_samples, 1):
        if not isinstance(item, Mapping):
            raise OperationalHoldingsInputError(
                f"sync metadata sample must be an object: index={index}"
            )
        if set(item) != _RECOVERY_SAMPLE_FIELDS:
            raise OperationalHoldingsInputError(
                "sync metadata sample fields do not match the recovery contract"
            )
        security_id = _non_empty_string(
            item.get("security_id"),
            label=f"sync metadata sample security_id: index={index}",
        )
        if security_id in seen_security_ids:
            raise OperationalHoldingsInputError(
                f"duplicate sync metadata sample security_id: {security_id}"
            )
        seen_security_ids.add(security_id)
        sample = {
            "security_id": security_id,
            "name": _non_empty_string(
                item.get("name"),
                label=f"sync metadata sample name: index={index}",
            ),
            "observed_row_count": _positive_int(
                item.get("observed_row_count"),
                label=f"sync metadata sample observed_row_count: index={index}",
            ),
            "observed_etf_count": _positive_int(
                item.get("observed_etf_count"),
                label=f"sync metadata sample observed_etf_count: index={index}",
            ),
            "observed_date_count": _positive_int(
                item.get("observed_date_count"),
                label=f"sync metadata sample observed_date_count: index={index}",
            ),
            "name_alias_count": _non_negative_int(
                item.get("name_alias_count"),
                label=f"sync metadata sample name_alias_count: index={index}",
            ),
        }
        samples.append(sample)
    return samples


def _validate_collection_summary_recovery_samples(
    collection_summary: Mapping[str, JsonValue],
) -> list[dict[str, JsonValue]]:
    if collection_summary.get("schema_version") != COLLECTION_SUMMARY_SCHEMA_VERSION:
        raise OperationalHoldingsInputError("invalid collection summary schema")
    if collection_summary.get("collection_source_type") != "native_history":
        raise OperationalHoldingsInputError(
            "collection summary source type must be native_history"
        )
    security_coverage = collection_summary.get("security_coverage")
    if not isinstance(security_coverage, Mapping):
        raise OperationalHoldingsInputError(
            "collection summary field must be an object: security_coverage"
        )
    raw_samples = security_coverage.get("recovery_samples")
    if not isinstance(raw_samples, list):
        raise OperationalHoldingsInputError(
            "collection summary field must be a list: security_coverage.recovery_samples"
        )
    samples: list[dict[str, JsonValue]] = []
    seen_security_ids: set[str] = set()
    for index, item in enumerate(raw_samples, 1):
        if not isinstance(item, Mapping):
            raise OperationalHoldingsInputError(
                f"collection summary sample must be an object: index={index}"
            )
        fields = set(item)
        if not _RECOVERY_SAMPLE_FIELDS <= fields <= _NATIVE_RECOVERY_SAMPLE_FIELDS:
            raise OperationalHoldingsInputError(
                "collection summary sample fields do not match the recovery contract"
            )
        security_id = _non_empty_string(
            item.get("security_id"),
            label=f"collection summary sample security_id: index={index}",
        )
        if security_id in seen_security_ids:
            raise OperationalHoldingsInputError(
                f"duplicate collection summary sample security_id: {security_id}"
            )
        seen_security_ids.add(security_id)
        sample = {
            "security_id": security_id,
            "name": _non_empty_string(
                item.get("name"),
                label=f"collection summary sample name: index={index}",
            ),
            "observed_row_count": _positive_int(
                item.get("observed_row_count"),
                label=(
                    "collection summary sample observed_row_count: "
                    f"index={index}"
                ),
            ),
            "observed_etf_count": _positive_int(
                item.get("observed_etf_count"),
                label=(
                    "collection summary sample observed_etf_count: "
                    f"index={index}"
                ),
            ),
            "observed_date_count": _positive_int(
                item.get("observed_date_count"),
                label=(
                    "collection summary sample observed_date_count: "
                    f"index={index}"
                ),
            ),
            "name_alias_count": _non_negative_int(
                item.get("name_alias_count"),
                label=(
                    "collection summary sample name_alias_count: "
                    f"index={index}"
                ),
            ),
        }
        if "security_classification" in item:
            classification = item.get("security_classification")
            if classification != "unknown":
                raise OperationalHoldingsInputError(
                    "collection summary sample security_classification must be unknown"
                )
            sample["security_classification"] = "unknown"
        samples.append(sample)
    return samples


def _non_empty_string(value: JsonValue, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OperationalHoldingsInputError(f"{label} must be a non-empty string")
    return value


def _positive_int(value: JsonValue, *, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise OperationalHoldingsInputError(f"{label} must be a positive integer")
    return value


def _non_negative_int(value: JsonValue, *, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise OperationalHoldingsInputError(f"{label} must be a non-negative integer")
    return value


def _build_recovery_proposal_request(samples: Sequence[Mapping[str, JsonValue]]) -> ModelRequest:
    return ModelRequest(
        messages=(
            Message(
                role="system",
                content=(
                    TextBlock(
                        text=(
                            "You propose untrusted ticker resolutions for security "
                            "mapping recovery. Return exactly one JSON object with "
                            "one top-level field named proposals. Do not include "
                            "schema_version or source_sync_metadata_path. Each "
                            "proposal must contain exactly security_id, name, "
                            "proposed_ticker, status, confidence, and rationale. "
                            "Use status proposed with a non-empty proposed_ticker, "
                            "or status unresolved with proposed_ticker null. "
                            "Confidence must be high, medium, or low."
                        )
                    ),
                ),
            ),
            Message(
                role="user",
                content=(
                    TextBlock(
                        text=json.dumps(
                            {"unmapped_security_samples": samples},
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                    ),
                ),
            ),
        )
    )


def _validate_recovery_proposal_response(
    *,
    response: object,
    samples: Sequence[Mapping[str, JsonValue]],
) -> list[dict[str, JsonValue]]:
    message = getattr(response, "message", None)
    tool_calls = getattr(response, "tool_calls", ())
    if tool_calls:
        raise SecurityMappingRecoveryProposalError("tool calls are not allowed")
    if message is None or getattr(message, "role", None) != "assistant":
        raise SecurityMappingRecoveryProposalError("assistant message is required")
    content = getattr(message, "content", ())
    if len(content) != 1:
        raise SecurityMappingRecoveryProposalError("exactly one content block is required")
    block = content[0]
    if not isinstance(block, TextBlock):
        raise SecurityMappingRecoveryProposalError("assistant content must be text")
    try:
        parsed = json.loads(block.text.strip())
    except json.JSONDecodeError as exc:
        raise SecurityMappingRecoveryProposalError("assistant text must be JSON") from exc
    if not isinstance(parsed, dict) or set(parsed) != {"proposals"}:
        raise SecurityMappingRecoveryProposalError("model response fields are invalid")
    raw_proposals = parsed.get("proposals")
    if not isinstance(raw_proposals, list):
        raise SecurityMappingRecoveryProposalError("proposals must be a list")

    samples_by_id = {str(sample["security_id"]): sample for sample in samples}
    expected_ids = set(samples_by_id)
    proposals_by_id: dict[str, dict[str, JsonValue]] = {}
    for item in raw_proposals:
        proposal = _validate_recovery_proposal_item(item, samples_by_id=samples_by_id)
        security_id = str(proposal["security_id"])
        if security_id in proposals_by_id:
            raise SecurityMappingRecoveryProposalError("duplicate proposal security_id")
        proposals_by_id[security_id] = proposal
    if set(proposals_by_id) != expected_ids:
        raise SecurityMappingRecoveryProposalError("proposal security_id set mismatch")
    return [proposals_by_id[str(sample["security_id"])] for sample in samples]


def _validate_recovery_proposal_item(
    item: object,
    *,
    samples_by_id: Mapping[str, Mapping[str, JsonValue]],
) -> dict[str, JsonValue]:
    if not isinstance(item, Mapping):
        raise SecurityMappingRecoveryProposalError("proposal entry must be an object")
    if set(item) != _RECOVERY_PROPOSAL_FIELDS:
        raise SecurityMappingRecoveryProposalError("proposal fields are invalid")
    security_id = item.get("security_id")
    if not isinstance(security_id, str) or security_id not in samples_by_id:
        raise SecurityMappingRecoveryProposalError("proposal security_id is invalid")
    name = item.get("name")
    if name != samples_by_id[security_id]["name"]:
        raise SecurityMappingRecoveryProposalError("proposal name mismatch")
    status = item.get("status")
    if status not in {"proposed", "unresolved"}:
        raise SecurityMappingRecoveryProposalError("proposal status is invalid")
    confidence = item.get("confidence")
    if confidence not in {"high", "medium", "low"}:
        raise SecurityMappingRecoveryProposalError("proposal confidence is invalid")
    rationale = item.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        raise SecurityMappingRecoveryProposalError("proposal rationale is invalid")
    raw_ticker = item.get("proposed_ticker")
    if status == "proposed":
        if not isinstance(raw_ticker, str) or not raw_ticker.strip():
            raise SecurityMappingRecoveryProposalError("proposal ticker is invalid")
        proposed_ticker: JsonValue = raw_ticker.strip()
    else:
        if raw_ticker is not None:
            raise SecurityMappingRecoveryProposalError("unresolved proposal ticker is invalid")
        proposed_ticker = None
    return {
        "security_id": security_id,
        "name": name,
        "proposed_ticker": proposed_ticker,
        "status": status,
        "confidence": confidence,
        "rationale": rationale,
    }


async def _inspect_command(
    args: argparse.Namespace, *, output: TextIO, error_output: TextIO
) -> int:
    store: SQLiteRunStore | None = None
    try:
        store = SQLiteRunStore(args.sqlite_path)
        await store.initialize()
        try:
            snapshot = await RunInspectionService(store).build_snapshot(args.run_id)
        except RunInspectionNotFoundError as exc:
            error_output.write(f"{exc}\n")
            return 1
    except Exception as exc:
        _write_operational_error(error_output, reason="run_store_failed", exc=exc)
        return 1
    finally:
        if store is not None:
            await store.close()

    json.dump(snapshot.model_dump(mode="json"), output, ensure_ascii=False)
    output.write("\n")
    return 0


def _write_operational_error(output: TextIO, *, reason: str, exc: Exception) -> None:
    fallback_message = {
        "run_store_failed": "run store failed",
        "model_client_failed": "model client failed",
        "artifact_store_failed": "artifact store failed",
        "user_ready_contract_failed": "user ready contract failed",
    }.get(reason, "operation failed")
    json.dump(
        {
            "reason": reason,
            "error": exception_error(
                exc,
                code=reason,
                fallback_message=fallback_message,
                message_mode="fallback_only",
            ),
        },
        output,
        ensure_ascii=False,
    )
    output.write("\n")


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(run_cli_async(argv))


if __name__ == "__main__":
    raise SystemExit(main())
