"""PoeClient: payload shape, message flattening, stop-reason mapping, tool
warning, bot routing. All HTTP mocked via httpx.MockTransport."""
from __future__ import annotations

import json

import httpx
import pytest

from scout.llm.poe import PoeClient


def _make_transport(captured: dict, response_body: dict) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content.decode())
        return httpx.Response(200, json=response_body)

    return httpx.MockTransport(handler)


def _poe_response(text: str = "draft body", *, prompt: int = 1000, completion: int = 200, finish: str = "stop") -> dict:
    return {
        "id": "resp_x",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": finish,
            }
        ],
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion},
    }


def _client(captured: dict, response_body: dict) -> PoeClient:
    transport = _make_transport(captured, response_body)
    http = httpx.Client(transport=transport, timeout=10.0)
    return PoeClient(
        api_key="sk-poe-test",
        drafting_bot="Claude-Opus-4.7",
        scout_bot="Claude-Opus-4.7-Search",
        http_client=http,
    )


def test_drafting_request_shape() -> None:
    captured: dict = {}
    client = _client(captured, _poe_response("hello"))
    resp = client.create_message(
        system="You are a helpful drafter.",
        messages=[{"role": "user", "content": "draft this"}],
        max_tokens=2000,
        prompt_template="drafting/artist_statement.md",
    )

    assert captured["url"].endswith("/v1/chat/completions")
    assert captured["headers"]["authorization"] == "Bearer sk-poe-test"
    body = captured["body"]
    assert body["model"] == "Claude-Opus-4.7"
    assert body["max_tokens"] == 2000
    assert body["messages"][0] == {"role": "system", "content": "You are a helpful drafter."}
    assert body["messages"][1] == {"role": "user", "content": "draft this"}
    assert resp.text == "hello"
    assert resp.stop_reason == "end_turn"
    assert resp.usage.input_tokens == 1000
    assert resp.usage.output_tokens == 200
    assert resp.usage.cost_usd == 0  # Poe is points-based


def test_scout_request_routes_to_search_bot() -> None:
    captured: dict = {}
    client = _client(captured, _poe_response("scout output"))
    client.create_message(
        system="scout sys",
        messages=[{"role": "user", "content": "find me grants"}],
        max_tokens=10000,
        prompt_template="scout_agent",
    )
    assert captured["body"]["model"] == "Claude-Opus-4.7-Search"


def test_system_list_blocks_are_flattened() -> None:
    captured: dict = {}
    client = _client(captured, _poe_response())
    client.create_message(
        system=[
            {"type": "text", "text": "frozen-system-A"},
            {"type": "text", "text": "frozen-system-B", "cache_control": {"type": "ephemeral"}},
        ],
        messages=[{"role": "user", "content": "x"}],
    )
    sys_msg = captured["body"]["messages"][0]
    assert sys_msg["role"] == "system"
    assert "frozen-system-A" in sys_msg["content"]
    assert "frozen-system-B" in sys_msg["content"]
    # cache_control silently dropped — no key leakage into the payload
    assert "cache_control" not in json.dumps(captured["body"])


def test_anthropic_block_content_is_flattened_in_user_message() -> None:
    captured: dict = {}
    client = _client(captured, _poe_response())
    client.create_message(
        system="sys",
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "first part"},
                    {"type": "text", "text": "second part"},
                ],
            }
        ],
    )
    user_msg = captured["body"]["messages"][1]
    assert "first part" in user_msg["content"]
    assert "second part" in user_msg["content"]


def test_tools_are_ignored_with_one_warning(caplog: pytest.LogCaptureFixture) -> None:
    captured: dict = {}
    client = _client(captured, _poe_response())
    tool = {"type": "web_search_20260209", "name": "web_search", "max_uses": 25}
    client.create_message(system="s", messages=[{"role": "user", "content": "x"}], tools=[tool])
    # tools field must not be sent to Poe
    assert "tools" not in captured["body"]
    # second call with the same client should not re-warn
    client.create_message(system="s", messages=[{"role": "user", "content": "y"}], tools=[tool])


def test_stop_reason_mapping() -> None:
    cases = {
        "stop": "end_turn",
        "length": "max_tokens",
        "tool_calls": "tool_use",
        "content_filter": "refusal",
        "weird_thing": "weird_thing",
    }
    for finish, expected in cases.items():
        captured: dict = {}
        client = _client(captured, _poe_response(finish=finish))
        resp = client.create_message(system="", messages=[{"role": "user", "content": "x"}])
        assert resp.stop_reason == expected, finish


def test_missing_api_key_raises() -> None:
    with pytest.raises(ValueError):
        PoeClient(api_key="")


def test_thinking_and_effort_are_silently_ignored() -> None:
    captured: dict = {}
    client = _client(captured, _poe_response())
    client.create_message(
        system="s",
        messages=[{"role": "user", "content": "x"}],
        thinking={"type": "adaptive"},
        effort="high",
    )
    body = captured["body"]
    assert "thinking" not in body
    assert "effort" not in body
    assert "output_config" not in body
