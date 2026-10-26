The bundled Codex engine (0.164.0-alpha.11.1) emits `root_turn_id` alongside
session, thread and turn identifiers. A local capture of a synthetic request
failed replay classification only on that metadata key; removing it made the
same normalized request replayable. API normalization must choose the same
OpenAI-compatible path as the route when top-level instructions are omitted.

Root-turn identity is request telemetry and does not name a stored response,
file, or other account-owned upstream object. The existing recovery projection
already omits its sibling session/thread/turn metadata before classification.

Live synthetic requests confirmed that the vision-capable `abliterated-model`
accepts a valid PNG in user message content but rejects the same PNG inside a
tool output. Image projection must retain those pixels in a labeled user
message after contiguous tool outputs, keeping parallel call results together.
Text-only models retain their existing historical-image placeholder behavior.
