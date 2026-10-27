# Claude subscription model source

## Why

The hosted LB exposes GPT and OpenAI-compatible providers to Codex, while the
existing local claude-lb pool only speaks Anthropic Messages. Add a native
Claude source so an operator can import that pool and select Opus in Codex.

## What Changes

- Add an `anthropic` model-source kind with encrypted, ordered account credentials.
- Translate self-contained Responses requests and Messages SSE, including tools,
  images, reasoning summaries, usage, and terminal events.
- Refresh credentials under a database compare-and-set claim, persist rotated
  refresh tokens, and fail over before exposing content on auth/rate-limit errors.
- Reuse source assignment, model catalogs, admission, and usage settlement.
- Add a dashboard import action for the existing claude-lb account file.
- Run subscription inference through the pinned native Claude Agent SDK, with
  isolated configuration, replayed conversation history, and server tools blocked.

## Impact

Model sources, source forwarding, dashboard source settings. No schema migration:
the source's existing encrypted credential field stores its typed pool document.
OpenAI-compatible sources retain their existing credential and wire formats.
