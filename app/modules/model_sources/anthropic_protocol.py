"""Responses <-> Messages translation, independent of account selection and I/O."""

from __future__ import annotations

import codecs
import hashlib
import json
import re
from dataclasses import dataclass
from uuid import uuid4

from app.core.crypto import TokenEncryptor
from app.core.types import JsonValue

type Json = dict[str, JsonValue]
_REASONING_PREFIX = "claude-v1:"


class ClaudeProtocolError(ValueError):
    pass


def _object(value: JsonValue) -> Json:
    if not isinstance(value, dict):
        raise ClaudeProtocolError("Expected a JSON object")
    return value


def _text(value: JsonValue) -> str:
    if not isinstance(value, str):
        raise ClaudeProtocolError("Expected text")
    return value


def _items(value: JsonValue) -> list[JsonValue]:
    if not isinstance(value, list):
        raise ClaudeProtocolError("Expected a list")
    return value


def _tokens(value: JsonValue) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ClaudeProtocolError("Invalid Claude usage")
    return value


@dataclass(frozen=True)
class ClaudeTool:
    name: str
    namespace: str | None
    custom: bool
    wire_name: str


def _wire_name(name: str, namespace: str | None) -> str:
    if namespace is None and re.fullmatch(r"[a-zA-Z0-9_-]{1,110}", name):
        return name
    qualified = f"{namespace}.{name}" if namespace else name
    slug = re.sub(r"[^a-zA-Z0-9_-]", "_", qualified)[:90]
    return f"{slug}_{hashlib.sha256(qualified.encode()).hexdigest()[:16]}"


def _tool_declarations(payload: Json) -> tuple[list[JsonValue], dict[str, ClaudeTool]]:
    declarations: list[JsonValue] = []
    tools: dict[str, ClaudeTool] = {}

    def add(original: JsonValue, namespace: str | None = None) -> None:
        tool = _object(original)
        kind = tool.get("type", "function")
        if kind == "namespace":
            for nested in _items(tool.get("tools", [])):
                add(nested, _text(tool.get("name")))
            return
        if kind not in {"function", "custom"}:
            raise ClaudeProtocolError("This Claude source supports client function and custom tools only")
        name = _text(tool.get("name"))
        wire = _wire_name(name, namespace)
        if wire in tools:
            raise ClaudeProtocolError("Duplicate translated tool name")
        tools[wire] = ClaudeTool(name, namespace, kind == "custom", wire)
        schema: JsonValue = tool.get("parameters", {"type": "object", "properties": {}})
        if kind == "custom":
            schema = {"type": "object", "properties": {"input": {"type": "string"}}, "required": ["input"]}
        description = tool.get("description", "")
        if namespace:
            description = f"Tool {namespace}.{name}.\n{description}"
        tool_format = tool.get("format")
        if kind == "custom" and isinstance(tool_format, dict) and tool_format.get("type") == "grammar":
            definition = tool_format.get("definition")
            if isinstance(definition, str):
                description = f"{description}\nThe input string must follow this grammar:\n{definition}"
        declarations.append({"name": wire, "description": description, "input_schema": schema})

    for declaration in _items(payload.get("tools", [])):
        add(declaration)
    return declarations, tools


def _content(value: JsonValue) -> list[JsonValue]:
    if isinstance(value, str):
        return [{"type": "text", "text": value}]
    result: list[JsonValue] = []
    for raw in _items(value):
        part = _object(raw)
        kind = part.get("type")
        if kind in {"input_text", "output_text", "text"}:
            result.append({"type": "text", "text": _text(part.get("text"))})
        elif kind == "input_image":
            if part.get("file_id"):
                raise ClaudeProtocolError("Claude needs image bytes or a URL, not a provider-owned file ID")
            url = _text(part.get("image_url"))
            if url.startswith("data:"):
                match = re.fullmatch(r"data:(image/(?:png|jpeg|gif|webp));base64,(.+)", url, re.DOTALL)
                if not match:
                    raise ClaudeProtocolError("Unsupported image data URI")
                source: Json = {"type": "base64", "media_type": match[1], "data": match[2]}
            elif url.startswith(("https://", "http://")):
                source = {"type": "url", "url": url}
            else:
                raise ClaudeProtocolError("Unsupported image URL")
            result.append({"type": "image", "source": source})
        elif kind == "refusal":
            result.append({"type": "text", "text": _text(part.get("refusal"))})
        else:
            raise ClaudeProtocolError("Unsupported content for Claude; supply text or an image")
    return result


@dataclass
class ClaudeRequest:
    payload: Json
    tools: dict[str, ClaudeTool]
    original: Json


def project_claude_request(payload: Json, *, source_id: str, encryptor: TokenEncryptor) -> ClaudeRequest:
    if payload.get("previous_response_id") or payload.get("conversation"):
        raise ClaudeProtocolError(
            "Claude needs complete conversation history; provider-owned response IDs cannot replay"
        )
    declarations, tools = _tool_declarations(payload)
    system: list[JsonValue] = []
    if payload.get("instructions"):
        system.append({"type": "text", "text": _text(payload["instructions"])})
    messages: list[JsonValue] = []

    def append(role: str, parts: list[JsonValue]) -> None:
        if not parts:
            return
        if messages and _object(messages[-1]).get("role") == role:
            _items(_object(messages[-1])["content"]).extend(parts)
        else:
            messages.append({"role": role, "content": parts})

    raw_input = payload.get("input", [])
    input_items: list[JsonValue] = (
        [{"role": "user", "content": raw_input}] if isinstance(raw_input, str) else _items(raw_input)
    )
    for raw in input_items:
        item = _object(raw)
        kind = item.get("type", "message")
        if kind == "reasoning":
            encrypted = item.get("encrypted_content")
            if isinstance(encrypted, str) and encrypted.startswith(_REASONING_PREFIX):
                try:
                    saved = json.loads(encryptor.decrypt(encrypted[len(_REASONING_PREFIX) :].encode()))
                except Exception as exc:
                    raise ClaudeProtocolError("Claude reasoning could not be restored") from exc
                if saved.get("source_id") == source_id:
                    append("assistant", _items(saved["blocks"]))
            continue
        if kind in {"function_call", "custom_tool_call"}:
            name = _text(item.get("name"))
            namespace = item.get("namespace")
            qualified = f"{namespace}.{name}" if isinstance(namespace, str) else name
            spec = next(
                (t for t in tools.values() if (f"{t.namespace}.{t.name}" if t.namespace else t.name) == qualified), None
            )
            wire = spec.wire_name if spec else _wire_name(name, namespace if isinstance(namespace, str) else None)
            try:
                arguments: JsonValue = (
                    {"input": _text(item.get("input"))}
                    if kind == "custom_tool_call"
                    else json.loads(_text(item.get("arguments", "{}")))
                )
            except ValueError as exc:
                raise ClaudeProtocolError("Historical tool arguments must be valid JSON") from exc
            append(
                "assistant",
                [{"type": "tool_use", "id": _text(item.get("call_id")), "name": wire, "input": _object(arguments)}],
            )
        elif kind in {"function_call_output", "custom_tool_call_output"}:
            append(
                "user",
                [
                    {
                        "type": "tool_result",
                        "tool_use_id": _text(item.get("call_id")),
                        "content": _content(item.get("output", "")),
                    }
                ],
            )
        elif kind == "message":
            role = item.get("role", "user")
            parts = _content(item.get("content", ""))
            if role in {"system", "developer"}:
                if not messages:
                    system.extend(parts)
                else:
                    append("user", [{"type": "text", "text": "Developer instructions:"}, *parts])
            elif role in {"user", "assistant"}:
                append(_text(role), parts)
            else:
                raise ClaudeProtocolError("Unsupported message role")
        else:
            raise ClaudeProtocolError("Claude cannot replay this provider-owned history item")
    if not messages:
        raise ClaudeProtocolError("Claude requires conversation input")
    result: Json = {
        "model": payload.get("model"),
        "messages": messages,
        "max_tokens": payload.get("max_output_tokens", 32768),
        "stream": True,
    }
    if system:
        result["system"] = system
    if declarations:
        result["tools"] = declarations
    choice = payload.get("tool_choice", "auto")
    if choice == "required":
        result["tool_choice"] = {"type": "any"}
    elif choice == "none":
        result["tool_choice"] = {"type": "none"}
    elif isinstance(choice, dict):
        if choice.get("type") not in {"function", "custom"}:
            raise ClaudeProtocolError("Unsupported Claude tool choice")
        name = _text(choice.get("name"))
        matches = [
            t
            for t in tools.values()
            if t.name == name and (not choice.get("namespace") or t.namespace == choice.get("namespace"))
        ]
        if len(matches) != 1:
            raise ClaudeProtocolError("Forced Claude tool is missing or ambiguous")
        result["tool_choice"] = {"type": "tool", "name": matches[0].wire_name}
    elif choice != "auto":
        raise ClaudeProtocolError("Unsupported Claude tool choice")
    if payload.get("parallel_tool_calls") is False and declarations:
        selected = _object(result.setdefault("tool_choice", {"type": "auto"}))
        selected["disable_parallel_tool_use"] = True
    reasoning = payload.get("reasoning")
    if isinstance(reasoning, dict) and isinstance(reasoning.get("effort"), str):
        effort = reasoning["effort"]
        if effort not in {"none", "minimal"}:
            result["thinking"] = {"type": "adaptive"}
            result["output_config"] = {"effort": {"xhigh": "max", "ultra": "max"}.get(effort, effort)}
    for key in ("temperature", "top_p"):
        if key in payload and "thinking" not in result:
            result[key] = payload[key]
    text = payload.get("text")
    text_format = text.get("format") if isinstance(text, dict) else None
    if isinstance(text_format, dict) and text_format.get("type") not in {None, "text"}:
        raise ClaudeProtocolError("Structured output is not enabled for this Claude source")
    return ClaudeRequest(result, tools, payload)


class ClaudeEvents:
    """Incremental, bounded SSE decoder and Responses lifecycle builder."""

    def __init__(self, request: ClaudeRequest, *, source_id: str, encryptor: TokenEncryptor, created_at: int) -> None:
        self.request = request
        self.source_id = source_id
        self.encryptor = encryptor
        self.response: Json = {
            "id": f"resp_claude_{uuid4().hex}",
            "object": "response",
            "created_at": created_at,
            "status": "in_progress",
            "model": request.original.get("model"),
            "output": [],
            "error": None,
            "incomplete_details": None,
        }
        self.sequence = 0
        self.terminal = False
        self.stop_reason: str | None = None
        self.blocks: dict[int, Json] = {}
        self.output_indices: dict[int, int] = {}
        self.buffer = ""
        self.decoder = codecs.getincrementaldecoder("utf-8")()
        self.cr_pending = False
        self.usage: Json = {}

    def event(self, kind: str, **fields: JsonValue) -> bytes:
        value: Json = {"type": kind, "sequence_number": self.sequence, **fields}
        self.sequence += 1
        return f"event: {kind}\ndata: {json.dumps(value, ensure_ascii=False)}\n\n".encode()

    def feed(self, chunk: bytes) -> list[bytes]:
        text = self.decoder.decode(chunk)
        if self.cr_pending:
            text = text.removeprefix("\n")
        self.cr_pending = text.endswith("\r")
        self.buffer += text.replace("\r\n", "\n").replace("\r", "\n")
        result: list[bytes] = []
        while "\n\n" in self.buffer:
            frame, self.buffer = self.buffer.split("\n\n", 1)
            if len(frame) > 1_048_576:
                raise ClaudeProtocolError("Claude stream frame exceeds the size limit")
            data = "\n".join(line[5:].lstrip(" ") for line in frame.splitlines() if line.startswith("data:"))
            if data:
                try:
                    result.extend(self.accept(_object(json.loads(data))))
                except (ValueError, TypeError, KeyError) as exc:
                    raise ClaudeProtocolError("Invalid Claude stream event") from exc
        if len(self.buffer) > 1_048_576:
            raise ClaudeProtocolError("Claude stream frame exceeds the size limit")
        return result

    def accept(self, event: Json) -> list[bytes]:
        if self.terminal:
            return []
        kind = event.get("type")
        if kind == "ping":
            return []
        if kind == "error":
            raise ClaudeProtocolError("Claude ended the generation with an upstream error")
        if kind == "message_start":
            self.usage.update(_object(_object(event["message"]).get("usage", {})))
            return [
                self.event("response.created", response=self.response),
                self.event("response.in_progress", response=self.response),
            ]
        if kind == "message_delta":
            self.usage.update(_object(event.get("usage", {})))
            stop_reason = _object(event.get("delta", {})).get("stop_reason")
            if stop_reason is not None:
                self.stop_reason = _text(stop_reason)
            return []
        if kind == "message_stop":
            if any(_object(item).get("status") == "in_progress" for item in _items(self.response["output"])):
                raise ClaudeProtocolError("Claude stopped with unfinished output")
            if self.stop_reason not in {"end_turn", "tool_use", "stop_sequence", "max_tokens", "refusal"}:
                raise ClaudeProtocolError("Claude did not finish the turn")
            cached = _tokens(self.usage.get("cache_read_input_tokens", 0))
            inputs = (
                _tokens(self.usage.get("input_tokens", 0))
                + cached
                + _tokens(self.usage.get("cache_creation_input_tokens", 0))
            )
            outputs = _tokens(self.usage.get("output_tokens", 0))
            self.response["usage"] = {
                "input_tokens": inputs,
                "output_tokens": outputs,
                "total_tokens": inputs + outputs,
                "input_tokens_details": {"cached_tokens": cached},
            }
            status = "incomplete" if self.stop_reason == "max_tokens" else "completed"
            self.response["status"] = status
            if status == "incomplete":
                self.response["incomplete_details"] = {"reason": "max_output_tokens"}
            self.terminal = True
            return [self.event(f"response.{status}", response=self.response)]
        index_value = event.get("index")
        if not isinstance(index_value, int):
            raise ClaudeProtocolError("Unknown Claude event")
        index = index_value
        if kind == "content_block_start":
            block = dict(_object(event["content_block"]))
            self.blocks[index] = block
            block_type = block.get("type")
            if block_type == "text":
                item: Json = {
                    "id": f"msg_{uuid4().hex}",
                    "type": "message",
                    "role": "assistant",
                    "status": "in_progress",
                    "content": [],
                }
            elif block_type in {"thinking", "redacted_thinking"}:
                item = {"id": f"rs_{uuid4().hex}", "type": "reasoning", "summary": []}
            elif block_type == "tool_use":
                tool = self.request.tools.get(_text(block.get("name")))
                if tool is None:
                    raise ClaudeProtocolError("Claude called a tool that was not declared")
                item = {
                    "id": f"call_{uuid4().hex}",
                    "type": "custom_tool_call" if tool.custom else "function_call",
                    "name": tool.name,
                    "call_id": block.get("id"),
                    "status": "in_progress",
                    "input" if tool.custom else "arguments": "",
                }
                if tool.namespace:
                    item["namespace"] = tool.namespace
                block["partial_json"] = ""
            else:
                raise ClaudeProtocolError("Unsupported Claude output block")
            output = _items(self.response["output"])
            self.output_indices[index] = len(output)
            output.append(item)
            return [self.event("response.output_item.added", output_index=len(output) - 1, item=item)]
        block = self.blocks[index]
        out_index = self.output_indices[index]
        item = _object(_items(self.response["output"])[out_index])
        common: Json = {"item_id": item["id"], "output_index": out_index}
        if kind == "content_block_delta":
            delta = _object(event["delta"])
            delta_type = delta.get("type")
            if delta_type == "signature_delta":
                block["signature"] = _text(block.get("signature", "")) + _text(delta.get("signature"))
                return []
            if delta_type == "input_json_delta":
                fragment = _text(delta.get("partial_json"))
                block["partial_json"] = _text(block.get("partial_json", "")) + fragment
                if item["type"] == "custom_tool_call":
                    return []
                item["arguments"] = _text(item.get("arguments", "")) + fragment
                return [self.event("response.function_call_arguments.delta", **common, delta=fragment)]
            if delta_type not in {"text_delta", "thinking_delta"}:
                raise ClaudeProtocolError("Unsupported Claude output delta")
            thinking = delta_type == "thinking_delta"
            field = "thinking" if thinking else "text"
            fragment = _text(delta.get(field))
            block[field] = _text(block.get(field, "")) + fragment
            parts = _items(item["summary" if thinking else "content"])
            events: list[bytes] = []
            if not parts:
                part: Json = {"type": "summary_text" if thinking else "output_text", "text": ""}
                if not thinking:
                    part["annotations"] = []
                parts.append(part)
                events.append(
                    self.event(
                        "response.reasoning_summary_part.added" if thinking else "response.content_part.added",
                        **common,
                        **{"summary_index" if thinking else "content_index": 0},
                        part=part,
                    )
                )
            _object(parts[0])["text"] = block[field]
            events.append(
                self.event(
                    "response.reasoning_summary_text.delta" if thinking else "response.output_text.delta",
                    **common,
                    **{"summary_index" if thinking else "content_index": 0},
                    delta=fragment,
                )
            )
            return events
        if kind != "content_block_stop":
            raise ClaudeProtocolError("Unknown Claude block event")
        events = []
        if block["type"] == "tool_use":
            try:
                arguments = json.loads(_text(block.get("partial_json")) or json.dumps(block.get("input", {})))
            except ValueError as exc:
                raise ClaudeProtocolError("Claude emitted invalid tool arguments") from exc
            if item["type"] == "custom_tool_call":
                item["input"] = _text(_object(arguments).get("input"))
                events.append(self.event("response.custom_tool_call_input.delta", **common, delta=item["input"]))
                events.append(self.event("response.custom_tool_call_input.done", **common, input=item["input"]))
            else:
                item["arguments"] = json.dumps(_object(arguments), ensure_ascii=False, separators=(",", ":"))
                events.append(
                    self.event("response.function_call_arguments.done", **common, arguments=item["arguments"])
                )
            item["status"] = "completed"
        elif block["type"] == "text":
            item["status"] = "completed"
            parts = _items(item["content"])
            if parts:
                events.extend(
                    [
                        self.event("response.output_text.done", **common, content_index=0, text=block.get("text", "")),
                        self.event("response.content_part.done", **common, content_index=0, part=parts[0]),
                    ]
                )
        else:
            item["encrypted_content"] = (
                _REASONING_PREFIX
                + self.encryptor.encrypt(json.dumps({"source_id": self.source_id, "blocks": [block]})).decode()
            )
            parts = _items(item["summary"])
            if parts:
                events.extend(
                    [
                        self.event(
                            "response.reasoning_summary_text.done",
                            **common,
                            summary_index=0,
                            text=block.get("thinking", ""),
                        ),
                        self.event("response.reasoning_summary_part.done", **common, summary_index=0, part=parts[0]),
                    ]
                )
        events.append(self.event("response.output_item.done", output_index=out_index, item=item))
        return events
