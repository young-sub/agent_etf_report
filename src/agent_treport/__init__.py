"""Extraction-era Agent TReport domain application package."""

from __future__ import annotations

DISTRIBUTION_NAME = "agent-treport"
PACKAGE_NAME = "agent_treport"
CLI_NAME = "agent-treport"
DATA_ROOT = "data/agent_treport"
SCHEMA_NAMESPACE = "agent_treport"
EVENT_NAMESPACE = "agent_treport"
__version__ = "0.1.0"

PATH_COMPATIBILITY_POLICY = {
    "default_data_root": DATA_ROOT,
    "default_paths": {
        "operational_holdings": (
            "data/agent_treport/operational-holdings/"
            "url_holdings_cumulative.json"
        ),
        "native_history": "data/agent_treport/live-source/holdings-history",
        "provider_operational_cache": (
            "data/agent_treport/live-source/source-provider-operational"
        ),
        "focus_etf_set": (
            "data/agent_treport/focus-etf-sets/default_focus_etf_set.json"
        ),
        "security_resolution": (
            "data/agent_treport/security-master/security_resolution.json"
        ),
    },
    "cli_name": CLI_NAME,
    "schema_namespace": SCHEMA_NAMESPACE,
    "event_namespace": EVENT_NAMESPACE,
    "rename_policy": "defer_agent_etf_report_rename_until_post_separation",
}

__all__ = [
    "CLI_NAME",
    "DATA_ROOT",
    "DISTRIBUTION_NAME",
    "EVENT_NAMESPACE",
    "PACKAGE_NAME",
    "PATH_COMPATIBILITY_POLICY",
    "SCHEMA_NAMESPACE",
    "__version__",
]
