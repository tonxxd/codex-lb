# Failure evidence

The first Opus request in the reported analytics investigation completed its
chat-read call. Its usage was 938,225 input tokens and 259 output tokens. Codex
then triggered mid-turn compaction above its 900,000-token automatic threshold.
The compaction request used 54,465 input tokens and exhausted 32,768 output
tokens, matching the bridge default and the client's incomplete-response error.

An isolated actual Codex CLI wire probe confirmed that compaction uses the
ordinary Responses route with zero tools, inherited max reasoning effort, and
no explicit output limit. Its final projected user content is a tool_result
followed by the CONTEXT CHECKPOINT COMPACTION text. Native replay previously
replaced this active instruction with its generic continuation prompt.

This change preserves the caller's instruction structurally rather than
recognizing a particular compaction prompt string or raising output limits.
Replay correction alone did not qualify max-effort summarization: a real
reconstruction exceeded a 150-second observation budget. With thinking disabled
for the summary utility, that same 54,458-token input completed in 22.41 seconds,
using 2,376 output tokens and preserving the analytics context.

The Responses dispatcher reuses the existing explicit Codex turn-metadata
classification. Only Anthropic compaction requests without a key-level enforced
or allowed reasoning policy receive the summary policy. Ordinary max reasoning,
explicit key reasoning policies, and client output ceilings remain effective.
Client telemetry stays stripped. The analytics incident itself remains separate
from this provider failure.

Verification: 549 focused backend tests pass, including both HTTP routes,
normal-turn and malformed-metadata controls, explicit key policies, replay with
text/images, protocol lifecycle, source headers, and other-provider regressions.
