# Preserve follow-up instructions after Claude tool results

## Why

Codex appends its compaction instruction after completed tool results. The Claude
projection merges them into one user message. Native replay currently seeds that
whole message and sends a generic continuation instruction as the live prompt,
losing the distinction between continuing the original task and summarizing it.
The reported analytics chat read succeeded, but compaction then consumed the
32,768-token output ceiling and failed incomplete.

## What Changes

- Seed completed tool results while sending subsequent user content as the real
  native prompt, preserving its ordering and image/text blocks.
- Keep the existing continuation instruction only when no follow-up content
  follows the last tool result.
- Treat explicitly tagged Codex compaction as a summary utility without thinking
  when the key has no enforced reasoning policy; preserve normal-turn reasoning.
- Verify the actual Codex compaction wire contract, native replay, and a real
  large tool-result compaction before hosted qualification.

## Impact

Claude native transcript replay and Responses compaction dispatch. No new
settings, credentials, model limits, or schema changes. Other providers retain
their existing behavior.
