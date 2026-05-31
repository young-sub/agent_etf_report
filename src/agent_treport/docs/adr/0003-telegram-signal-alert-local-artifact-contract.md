# Telegram Signal Alert Local Artifact Contract

## Status

Accepted.

## Context

`SignalReportWorkflow` already produces a canonical `SignalReportPayload`,
Markdown report, HTML report, and shared quality artifact. The next Telegram
slice needs a user-visible alert that tells a Korean-first user whether the
full `SignalIntelligenceReport` is worth opening, without adding Telegram Bot
API delivery, credentials, scheduling, retries, or publisher abstractions.

The alert must not become another scoring or interpretation layer. Scores,
labels, evidence grades, ranks, and data-quality findings remain canonical
payload facts.

## Decision

Store `TelegramSignalAlert` as a durable local artifact:

- Artifact id: `artifact_treport_telegram_alert`.
- File name: `telegram_alert.txt`.
- Media type: `text/plain`.
- Workflow state key: `telegram_alert_artifact_id`.
- CLI user-ready key: `telegram_alert`.
- Text content: only the Telegram `sendMessage.text` body.

The text is Telegram HTML parse-mode message text. The renderer may create only
`<b>` and `<code>` tags and must HTML-escape all payload-derived text. It
selects the canonical `payload.signal_board` sorted by `rank`, renders at most
the top five rows, and does not filter `defer` or `Unusable` rows.

Artifact metadata records:

- `capability="telegram_signal_alert"`.
- `telegram_parse_mode="HTML"`.
- `full_report_artifact_id="artifact_treport_html_report"`.
- `report_quality_status`.
- `report_quality_summary`.

`ReportQualityGate` owns Telegram alert validation through a `telegram_alert`
scope. `telegram_alert=None` skips Telegram checks with count `0`; supplied
alerts are checked for required sections, top-five row count, 4096-character
length, data-quality markers, full HTML report reference, forbidden fragments,
prohibited investment language, raw `claim_scope`, and raw `used_in` exposure.
Any Telegram alert quality error blocks Markdown, HTML, and Telegram user-ready
artifact storage after `quality.json` is persisted.

## Consequences

Local runs expose five user-ready artifacts on success: canonical payload,
Markdown report, HTML report, Telegram alert, and quality report. The alert is
inspectable and testable without live credentials or network access.

The workflow preserves existing non-atomic file behavior. If Telegram alert
storage fails after `report.md` and `report.html` were written, those files may
exist locally, but the failed run state does not expose `report_artifact_id`,
`html_report_artifact_id`, or `telegram_alert_artifact_id`.

## Alternatives Considered

- Plain text: simpler, but it loses Telegram HTML emphasis and the fixed
  artifact reference formatting that makes the message scannable.
- MarkdownV2: more escaping complexity and no current product need.
- Delivery-only adapter: would require credentials, API calls, retries, and
  operational semantics before the message contract is stable.
- Separate quality artifact: rejected because `quality.json` is already the
  shared evidence artifact for rendered report contracts.
