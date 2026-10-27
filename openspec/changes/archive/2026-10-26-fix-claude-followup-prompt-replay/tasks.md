## Implementation

- [x] Add a regression for a completed tool result followed by a compaction instruction.
- [x] Preserve trailing user content as the live native prompt.
- [x] Preserve pure result continuations, ordering, image blocks, and SDK cleanup.
- [x] Disable thinking only for explicitly classified Claude compaction without a key reasoning policy.

## Verification

- [x] Verify the emitted Codex compaction request shape.
- [x] Run 549 focused native, protocol, route, and other-provider checks.
- [x] Qualify large tool-result compaction with real Opus and record limits.
- [x] Validate and sync the verified OpenSpec change.
