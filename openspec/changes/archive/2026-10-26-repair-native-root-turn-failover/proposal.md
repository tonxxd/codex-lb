## Why

The current Codex app includes `client_metadata.root_turn_id` in native HTTP
Responses requests. The revoked-account recovery projection rejects that client
telemetry, leaving complete conversations pinned to unavailable accounts.
The vision-capable Abliteration model also rejects images inside historical
tool outputs, while accepting the same images in normal user messages.

## What Changes

- Recognize `root_turn_id` as client-only turn telemetry during auth recovery.
- Verify the current instructions-omitted native request format can recover
  from a revoked account and subsequently bypass that unavailable owner.
- Keep unknown account state, file owners, and stored-response anchors guarded.
- Retain historical tool images for vision-capable Abliteration models in
  labeled messages after each contiguous tool-output group.

## Impact

Unanchored, pre-visible HTTP auth recovery and Abliteration history projection.
No schema, credential, or account-status changes. Other sources retain their
existing image payloads.
