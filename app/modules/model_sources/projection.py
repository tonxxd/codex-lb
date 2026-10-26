"""Responses body projection for OpenAI-compatible model sources.

``strip_source_telemetry`` removes the Codex client telemetry a source must
never see -- whole fields where the field itself is Codex-only, a single key
inside the standard ``stream_options`` object -- and forwards everything else
verbatim: direct source routing never fails closed on an unknown field.
"""

from __future__ import annotations

import json

from app.core.types import JsonValue

type MutableJsonObject = dict[str, JsonValue]

# Codex client telemetry that an OpenAI-compatible source rejects or must not see.
# Whole top-level fields, because neither is a Responses API field:
# ``client_metadata`` (installation/session/thread/turn ids and turn metadata)
# and ``access_programs``.
STRIPPED_TELEMETRY_FIELDS: frozenset[str] = frozenset({"client_metadata", "access_programs"})

# ``stream_options`` is a standard Responses field (``include_obfuscation``)
# that Codex extends with ``reasoning_summary_delivery`` (its reasoning-summary
# delivery mode), so only that key is telemetry: it is removed from the object
# and the object is dropped once the removal leaves it empty -- the shape every
# Codex body has -- while an SDK client's ``include_obfuscation`` is forwarded
# unchanged.
STREAM_OPTIONS_FIELD = "stream_options"
STRIPPED_STREAM_OPTIONS_KEYS: frozenset[str] = frozenset({"reasoning_summary_delivery"})


def strip_source_telemetry(payload: MutableJsonObject) -> MutableJsonObject:
    """Remove exactly the Codex telemetry in place; returns ``payload``.

    ``STRIPPED_TELEMETRY_FIELDS`` are removed whole; from a ``stream_options``
    object only ``STRIPPED_STREAM_OPTIONS_KEYS`` are removed, and the object
    itself is dropped when that removal leaves nothing behind. Everything else
    -- ``prompt_cache_key``, ``tools``, ``include``, ``max_output_tokens``,
    ``prompt_cache_retention``, ``background``, ``max_tool_calls``, a
    ``stream_options.include_obfuscation``, a ``stream_options`` that is not an
    object, and any field this proxy has never seen -- is forwarded untouched,
    so direct source routing never fails closed on an unknown field.
    """

    for field in STRIPPED_TELEMETRY_FIELDS:
        payload.pop(field, None)
    stream_options = payload.get(STREAM_OPTIONS_FIELD)
    if isinstance(stream_options, dict):
        removed = False
        for key in STRIPPED_STREAM_OPTIONS_KEYS:
            if key in stream_options:
                del stream_options[key]
                removed = True
        if removed and not stream_options:
            del payload[STREAM_OPTIONS_FIELD]
    return payload


def project_abliteration_history(payload: MutableJsonObject, *, supports_vision: bool) -> None:
    """Translate prior Codex output into Abliteration's supported input vocabulary.

    Opaque reasoning belongs to its original provider. Completed custom calls
    retain their names, call IDs, input and output as function-call history.
    Tool-result images become labeled messages for vision models and text
    references for text models. Current user images retain normal validation.
    """
    items = payload.get("input")
    if not isinstance(items, list):
        return
    projected: list[JsonValue] = []
    pending_images: list[JsonValue] = []
    for original in items:
        kind = original.get("type") if isinstance(original, dict) else None
        if kind == "reasoning":
            continue
        if pending_images and kind not in ("function_call_output", "custom_tool_call_output"):
            projected.append(_abliteration_tool_image_message(pending_images))
            pending_images = []
        if not isinstance(original, dict):
            projected.append(original)
            continue
        item = dict(original)
        if kind == "custom_tool_call" and isinstance(item.get("input"), str):
            item["type"] = "function_call"
            item["arguments"] = json.dumps({"input": item.pop("input")}, ensure_ascii=False)
            item.pop("id", None)
        elif kind == "custom_tool_call_output":
            item["type"] = "function_call_output"
            item.pop("id", None)
        output = item.get("output")
        if kind in ("function_call_output", "custom_tool_call_output") and isinstance(output, list):
            if supports_vision:
                pending_images.extend(
                    part for part in output if isinstance(part, dict) and part.get("type") == "input_image"
                )
            image_reference = (
                "[Earlier tool image follows in a separate message.]"
                if supports_vision
                else "[Earlier tool image omitted: this model accepts text only.]"
            )
            item["output"] = [
                {"type": "input_text", "text": image_reference}
                if isinstance(part, dict) and part.get("type") == "input_image"
                else part
                for part in output
            ]
        projected.append(item)
    if pending_images:
        projected.append(_abliteration_tool_image_message(pending_images))
    payload["input"] = projected


def _abliteration_tool_image_message(images: list[JsonValue]) -> JsonValue:
    return {
        "role": "user",
        "content": [{"type": "input_text", "text": "Images from earlier tool results."}, *images],
    }
