# ETFBrand And SourceProvider Terminology Plan

## Status

Completed on 2026-05-15.

## Problem

Agent TReport previously used `ETFManager`, `manager_id`, and `manager_name` for the ETF brand or asset-management-company identity attached to an ETF. That language is ambiguous because "manager" can mean an individual portfolio manager. The same surfaces also use `source_provider_id` for data-source identity, which is too easy to confuse with an ETF brand when the source website and brand happen to be the same organization.

## Chosen Direction

- Use **ETFBrand** as the canonical business term for the ETF brand or asset-management-company identity associated with one or more ETFs.
- Rename domain contract fields from `manager_id` and `manager_name` to `brand_id` and `brand_name`.
- Use `source_provider_id` for the external data-source/provider identity.
- Keep source-specific raw field handling only where needed to preserve existing operational import compatibility; normalize internal Agent TReport outputs to brand/source-provider language.

## Scope

- Agent TReport domain models, adapters, payload contracts, fixtures, tests, and active docs.
- Native universe state and summary evidence.
- Normalized holdings export rows and collection/readiness summary projections.
- SignalReportPayload coverage and ETF follow sheet fields.

## Out Of Scope

- Generic `agent_pack` runtime provider terminology.
- External reference project code or field names.
- Large collection architecture changes beyond terminology and compatibility normalization.
- Live source acquisition implementation; resume that grill after terminology is consistent.

## Validation Plan

- Focused Agent TReport tests covering universe collection, operational holdings, readiness, CLI, and signal report workflow/rendering.
- Full pytest if focused tests pass.
- `ruff check` and `pyright`.

## Validation Evidence

- `../.venv/Scripts/python.exe -m pytest`: 373 passed.
- `../.venv/Scripts/python.exe -m ruff check .`: all checks passed.
- `../.venv/Scripts/python.exe -m pyright`: 0 errors, 0 warnings, 0 informations.

## Stop Conditions

- Stop if the rename requires changing `agent_pack` domain-free runtime contracts.
- Stop if existing legacy operational imports cannot be normalized without broad bridge rewrites.
- Stop if live provider implementation starts mixing into this terminology-only slice.
