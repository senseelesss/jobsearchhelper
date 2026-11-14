# Run 5: validation failure investigation

The preserved outputs/acceptance-v31/live-ai-1/report.json confirms run_id=5,
failed=1, scored=0, Scoring: ValidationError and potentially unaccounted failed
request cost. No raw response or validation fields were retained. Exact historical
root cause is **unproven**. Do not equate this label with token exhaustion.

Trace: OpenAIProvider.score → responses.parse → SDK parse_response/parse_text →
Pydantic MatchEvaluation → BudgetedProvider return → finalize_evaluation →
model_validate → score persistence. Either SDK JSON/schema parsing or final policy
validation could previously collapse to the same label in errors.error_label.
Installed OpenAI SDK 2.54.0 parses output_text without checking response completion.
A mocked HTTP response with incomplete truncated JSON reproduces a ValidationError
on that SDK path. A completed response with missing fields reproduces another.
Neither reproduction identifies which occurred in run 5.

The fix keeps responses.parse and its strict schema, using with_raw_response to
inspect the response envelope before parsing. Non-completed responses fail safely
with an allowlisted incomplete reason, including max_output_tokens/content_filter.
Completed responses still use SDK/Pydantic validation. Diagnostics distinguish
sdk_parse and final_policy, retain at most eight allowlisted field paths/error types,
and omit input, message, context, raw JSON, descriptions, names and credentials.
Unexpected keys are replaced with ?. Missing output/refusals are explicit failures.
Existing failed score_error/report persistence receives these safe labels; no new
logging of third-party exception bodies and no automatic retry are introduced.

Schema review: root/nested objects are closed and all fields required, enums and
references are supported. Numeric bounds, maxLength and maxItems are retained;
official documentation lists their additional restriction for fine-tuned models,
not the standard gpt-5.4-nano used in this repository. Mocked SDK success verifies
local compatibility, not live server acceptance or account availability.
Sources: https://developers.openai.com/api/docs/guides/structured-outputs
and https://developers.openai.com/api/docs/models/gpt-5.4-nano .

Default output limit remains 1200, validated range 256–4096. Seven evidence dimensions
can consume substantial output, but there is no run-5 truncation evidence. If a future
safe diagnostic explicitly reports max_output_tokens, an operator can increase
OPENAI_MAX_OUTPUT_TOKENS within that range and re-evaluate the configured caps.
request_ceiling already reserves the configured full output maximum, input bytes
plus schema/system allowance, and all configured SDK retries. Failed requests keep
their reservation. Reported usage may still exclude failures: no fabricated cost
or retrospective usage is inserted. No schema/cache version, DB history, provider
model, pricing, CLI or Telegram contract changed. No paid calls or sends performed.

Validation: 473 pytest tests passed; Ruff passed after formatting correction;
policy fixtures TP=10 TN=6 FP=0 FN=0. New actual-SDK/mock-HTTP cases cover incomplete
output, malformed JSON, missing required fields, completed success/usage, schema
closure, maxLength validation and redaction of malicious extra field names.
