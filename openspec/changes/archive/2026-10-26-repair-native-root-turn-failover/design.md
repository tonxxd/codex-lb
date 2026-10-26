# Design

## Context

See proposal.md for the two native-continuation failures. The existing HTTP
recovery path already projects a complete visible transcript before moving
accounts, and model-source projection already adapts Abliteration history.

## Goals / Non-Goals

Keep both fixes within those existing projection seams. Account ownership,
visible-output retry limits, subscription selection and other sources retain
their existing contracts. No configuration or migration is required.

## Decisions

- Omit `root_turn_id` alongside existing client turn identifiers during replay
  classification. Expanding the general account-neutral metadata allowlist
  would affect unrelated routing decisions, so the exception stays local.
- For vision-capable Abliteration models, move tool images to labeled user
  messages. Dropping them would lose useful screenshot history. Buffering until
  the contiguous output group ends keeps parallel tool results together.
- Keep text-only image references and normal user image validation unchanged.

## Risks / Trade-offs

Reclassifying unknown account state could permit an invalid cross-account
replay; unknown metadata and stored-object regression cases remain required.
The provider sees a labeled image message in place of a tool-contained image;
the original pixels and relative output order are preserved.

## Migration Plan

Deploy the tested commit on the existing repair branch after an online SQLite
snapshot. Verify the deployed source revision, native Codex completions and raw
image-history replay. Rollback uses the preceding Railway deployment; these
changes introduce no schema migration.
