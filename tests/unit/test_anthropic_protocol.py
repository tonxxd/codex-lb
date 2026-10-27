from __future__ import annotations

import json

import pytest
from cryptography.fernet import Fernet

from app.core.crypto import TokenEncryptor
from app.modules.model_sources.anthropic_protocol import ClaudeEvents, ClaudeProtocolError, project_claude_request

pytestmark = pytest.mark.unit


def _encryptor():
    return TokenEncryptor(key=Fernet.generate_key())


@pytest.mark.parametrize("chunk_size", [1, 7, 4096])
def test_claude_unicode_sse_reasoning_and_signature_round_trip(chunk_size):
    encryptor = _encryptor()
    request = project_claude_request(
        {"model": "claude-opus-5-5", "input": "hello"}, source_id="claude", encryptor=encryptor
    )
    builder = ClaudeEvents(request, source_id="claude", encryptor=encryptor, created_at=123)
    events = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 8}}},
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "thinking", "thinking": "", "signature": ""},
        },
        {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "Think 🧠"}},
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "signature_delta", "signature": "provider-signature"},
        },
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "Hello 🧪"}},
        {"type": "content_block_stop", "index": 1},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 4}},
        {"type": "message_stop"},
    ]
    raw = "".join(f"data: {json.dumps(e, ensure_ascii=False)}\r\n\r\n" for e in events).encode()
    output = []
    for start in range(0, len(raw), chunk_size):
        output.extend(builder.feed(raw[start : start + chunk_size]))
    assert builder.terminal
    assert builder.response["output"][1]["content"][0]["text"] == "Hello 🧪"
    reasoning = builder.response["output"][0]
    assert "provider-signature" not in reasoning["encrypted_content"]
    replay = project_claude_request(
        {"model": "claude-opus-5-5", "input": [reasoning, {"role": "user", "content": "continue"}]},
        source_id="claude",
        encryptor=encryptor,
    )
    assert replay.payload["messages"][0]["content"][0]["signature"] == "provider-signature"
    foreign = project_claude_request(
        {"model": "claude-opus-5-5", "input": [reasoning, {"role": "user", "content": "continue"}]},
        source_id="other",
        encryptor=encryptor,
    )
    assert foreign.payload["messages"][0]["role"] == "user"
    serialized = b"".join(output)
    assert "🧪".encode() in serialized


@pytest.mark.parametrize(
    "input_item",
    [
        {"type": "item_reference", "id": "foreign"},
        {"role": "user", "content": [{"type": "input_image", "file_id": "file_foreign"}]},
    ],
)
def test_claude_rejects_unrecoverable_provider_references(input_item):
    with pytest.raises(ClaudeProtocolError):
        project_claude_request(
            {"model": "claude-opus-5-5", "input": [input_item]}, source_id="claude", encryptor=_encryptor()
        )


def test_claude_keeps_parallel_tool_results_together_and_drops_foreign_reasoning():
    payload = {
        "model": "claude-opus-5-5",
        "input": [
            {"type": "reasoning", "encrypted_content": "foreign-provider-data"},
            {"role": "user", "content": "Run both"},
            {"type": "function_call", "name": "first", "call_id": "one", "arguments": "{}"},
            {"type": "function_call", "name": "second", "call_id": "two", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "one", "output": "first result"},
            {"type": "function_call_output", "call_id": "two", "output": "second result"},
        ],
    }
    projected = project_claude_request(payload, source_id="claude", encryptor=_encryptor()).payload
    assert len(projected["messages"]) == 3
    assert [p["id"] for p in projected["messages"][1]["content"]] == ["one", "two"]
    assert [p["tool_use_id"] for p in projected["messages"][2]["content"]] == ["one", "two"]
    assert "foreign-provider-data" not in json.dumps(projected)
