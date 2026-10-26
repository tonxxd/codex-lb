from __future__ import annotations

import copy

import pytest

import app.modules.proxy.api as proxy_api
from app.modules.model_sources.forwarding import SourceResponsesCompletion, SourceUsage
from tests.integration.model_source_helpers import _create_model_source

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("host", ["api.abliteration.ai", "other.example.test"])
async def test_source_replays_codex_history_without_leaking_provider_owned_reasoning(async_client, monkeypatch, host):
    model = "abliteration-replay-test"
    await _create_model_source(
        async_client, name="history", model=model, base_url=f"https://{host}/v1", supports_responses=True
    )
    captured = {}

    async def forward(source, payload):
        captured.update(payload)
        return SourceResponsesCompletion(
            payload={
                "id": "resp_replay",
                "object": "response",
                "status": "completed",
                "model": model,
                "output": [
                    {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "OK"}]}
                ],
                "usage": {"input_tokens": 4, "output_tokens": 2, "total_tokens": 6},
            },
            usage=SourceUsage(input_tokens=4, output_tokens=2),
            timings=None,
            upstream_status_code=200,
        )

    monkeypatch.setattr(proxy_api, "forward_source_responses", forward)
    history = [
        {"role": "user", "content": "Earlier prompt"},
        {"type": "reasoning", "summary": [], "encrypted_content": "other-provider-ciphertext"},
        {"type": "custom_tool_call", "call_id": "call_history", "name": "exec", "input": "synthetic code"},
        {
            "type": "custom_tool_call_output",
            "call_id": "call_history",
            "output": [
                {"type": "input_text", "text": "Retained tool result"},
                {"type": "input_image", "image_url": "data:image/png;base64,c3ludGhldGlj"},
            ],
        },
        {"role": "user", "content": [{"type": "input_text", "text": "Continue"}]},
    ]
    response = await async_client.post(
        "/v1/responses", json={"model": model, "input": copy.deepcopy(history), "stream": False}
    )
    assert response.status_code == 200
    items = captured["input"]
    if host != "api.abliteration.ai":
        assert any(item.get("type") == "reasoning" for item in items)
        assert any(item.get("type") == "custom_tool_call" for item in items)
        return
    assert not any(item.get("type") == "reasoning" for item in items)
    call = next(item for item in items if item.get("type") == "function_call")
    assert call["name"] == "exec" and call["call_id"] == "call_history"
    assert call["arguments"] == '{"input": "synthetic code"}'
    output = next(item for item in items if item.get("type") == "function_call_output")
    assert output["call_id"] == "call_history"
    assert output["output"][0]["text"] == "Retained tool result"
    assert output["output"][1]["type"] == "input_text"
    assert "image omitted" in output["output"][1]["text"]
    assert items[-1] == history[-1]
