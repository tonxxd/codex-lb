## Why

Codex HTTP requests repeatedly select revoked access tokens, and existing GPT
conversations cannot continue on Abliteration because provider-owned reasoning,
custom tool transcripts, and historical tool images are rejected.

## What Changes

- Treat explicit `token_revoked` responses as proof the stored access token is
  unusable; exclude it until fresh credentials are imported.
- Retry pre-visible rejected, unanchored, self-contained HTTP requests on a
  different eligible account, preserving file and explicit continuation owners.
- Project historical input for Abliteration's supported Responses vocabulary.
  Keep visible messages and tool results; retain current user image validation.

## Impact

Account routing and model-source request projection. No schema or credential
changes, no change to other model sources, and no replay after visible output.
