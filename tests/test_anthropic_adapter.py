"""Tests for the Anthropic Messages ⇄ OpenAI translation (WF-DESIGN-0011).

Pure functions: no server, no network, no keys. The endpoint integration lives in
``test_gateway.py``; here we pin the translation tables and the streaming state machine.
"""

from __future__ import annotations

import asyncio
import json

from wayfinder_router import anthropic_adapter as A


# --- request: Anthropic -> OpenAI -------------------------------------------
def test_request_system_string_becomes_leading_system_message():
    out = A.anthropic_to_openai_request(
        {"model": "claude-x", "system": "be terse", "max_tokens": 64,
         "messages": [{"role": "user", "content": "hi"}]}
    )
    assert out["model"] == "claude-x"
    assert out["messages"][0] == {"role": "system", "content": "be terse"}
    assert out["messages"][1] == {"role": "user", "content": "hi"}
    assert out["max_tokens"] == 64


def test_request_system_as_text_blocks_is_flattened():
    out = A.anthropic_to_openai_request(
        {"system": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}],
         "messages": []}
    )
    assert out["messages"][0] == {"role": "system", "content": "a\nb"}


def test_request_content_blocks_join_to_text():
    out = A.anthropic_to_openai_request(
        {"messages": [{"role": "user", "content": [
            {"type": "text", "text": "one"}, {"type": "text", "text": "two"}]}]}
    )
    assert out["messages"] == [{"role": "user", "content": "one\ntwo"}]


def test_request_assistant_tool_use_becomes_tool_calls():
    out = A.anthropic_to_openai_request({"messages": [
        {"role": "assistant", "content": [
            {"type": "text", "text": "let me check"},
            {"type": "tool_use", "id": "tu_1", "name": "search", "input": {"q": "cats"}},
        ]},
    ]})
    msg = out["messages"][0]
    assert msg["role"] == "assistant" and msg["content"] == "let me check"
    assert msg["tool_calls"] == [{
        "id": "tu_1", "type": "function",
        "function": {"name": "search", "arguments": '{"q":"cats"}'},
    }]


def test_request_tool_result_becomes_tool_message_before_user_text():
    out = A.anthropic_to_openai_request({"messages": [
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu_1", "content": "42"},
            {"type": "text", "text": "thanks"},
        ]},
    ]})
    assert out["messages"] == [
        {"role": "tool", "tool_call_id": "tu_1", "content": "42"},
        {"role": "user", "content": "thanks"},
    ]


def test_request_tool_result_content_blocks_flatten():
    out = A.anthropic_to_openai_request({"messages": [
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t", "content": [{"type": "text", "text": "ok"}]},
        ]},
    ]})
    assert out["messages"] == [{"role": "tool", "tool_call_id": "t", "content": "ok"}]


def test_request_tools_and_tool_choice_and_sampling():
    out = A.anthropic_to_openai_request({
        "messages": [{"role": "user", "content": "x"}],
        "tools": [{"name": "f", "description": "d", "input_schema": {"type": "object"}}],
        "tool_choice": {"type": "tool", "name": "f"},
        "temperature": 0.5, "top_p": 0.9, "top_k": 10,
        "stop_sequences": ["STOP"], "stream": True,
    })
    assert out["tools"] == [{"type": "function", "function": {
        "name": "f", "parameters": {"type": "object"}, "description": "d"}}]
    assert out["tool_choice"] == {"type": "function", "function": {"name": "f"}}
    assert out["temperature"] == 0.5 and out["top_p"] == 0.9
    assert "top_k" not in out  # no OpenAI equivalent — dropped
    assert out["stop"] == ["STOP"] and out["stream"] is True


def test_request_tool_choice_aliases():
    assert A.anthropic_to_openai_request({"tool_choice": {"type": "auto"}})["tool_choice"] == "auto"
    assert A.anthropic_to_openai_request({"tool_choice": {"type": "any"}})["tool_choice"] == "required"
    assert A.anthropic_to_openai_request({"tool_choice": {"type": "none"}})["tool_choice"] == "none"


def test_request_image_block_is_skipped_not_crashed():
    out = A.anthropic_to_openai_request({"messages": [
        {"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "data": "..."}},
            {"type": "text", "text": "what is this"},
        ]},
    ]})
    assert out["messages"] == [{"role": "user", "content": "what is this"}]


# --- response: OpenAI -> Anthropic (buffered) -------------------------------
def test_response_text_only():
    resp = {"choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 2}}
    out = A.openai_to_anthropic_response(resp, model="claude-x", message_id="msg_1")
    assert out["id"] == "msg_1" and out["type"] == "message" and out["role"] == "assistant"
    assert out["model"] == "claude-x"
    assert out["content"] == [{"type": "text", "text": "hello"}]
    assert out["stop_reason"] == "end_turn"
    assert out["usage"] == {"input_tokens": 7, "output_tokens": 2}


def test_response_tool_use_and_stop_reason():
    resp = {"choices": [{"message": {"content": None, "tool_calls": [
        {"id": "call_1", "function": {"name": "f", "arguments": '{"a":1}'}}]},
        "finish_reason": "tool_calls"}]}
    out = A.openai_to_anthropic_response(resp, model="m", message_id="msg_1")
    assert out["stop_reason"] == "tool_use"
    assert out["content"] == [{"type": "tool_use", "id": "call_1", "name": "f", "input": {"a": 1}}]


def test_response_stop_reason_mapping():
    def reason(finish):
        return A.openai_to_anthropic_response(
            {"choices": [{"message": {"content": "x"}, "finish_reason": finish}]},
            model="m", message_id="i")["stop_reason"]
    assert reason("stop") == "end_turn"
    assert reason("length") == "max_tokens"
    assert reason("tool_calls") == "tool_use"
    assert reason("content_filter") == "end_turn"
    assert reason(None) == "end_turn"


def test_response_malformed_tool_arguments_become_empty_input():
    resp = {"choices": [{"message": {"tool_calls": [
        {"id": "c", "function": {"name": "f", "arguments": "not json"}}]}, "finish_reason": "tool_calls"}]}
    out = A.openai_to_anthropic_response(resp, model="m", message_id="i")
    assert out["content"][0]["input"] == {}


def test_response_empty_content_yields_one_empty_text_block():
    out = A.openai_to_anthropic_response(
        {"choices": [{"message": {"content": ""}, "finish_reason": "stop"}]},
        model="m", message_id="i")
    assert out["content"] == [{"type": "text", "text": ""}]


def test_response_usage_estimated_when_absent():
    out = A.openai_to_anthropic_response(
        {"choices": [{"message": {"content": "x" * 40}, "finish_reason": "stop"}]},
        model="m", message_id="i", prompt_text="y" * 80)
    assert out["usage"] == {"input_tokens": 20, "output_tokens": 10}  # ~4 chars/token


def test_anthropic_error_envelope():
    assert A.anthropic_error(429, "slow down") == {
        "type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}}
    assert A.anthropic_error(500, "boom")["error"]["type"] == "api_error"
    assert A.anthropic_error(402, "broke")["error"]["type"] == "invalid_request_error"


# --- streaming: OpenAI SSE -> Anthropic SSE ---------------------------------
def _events(byte_list):
    """Decode a list of SSE byte blobs into (event_type, data_dict) tuples."""
    out = []
    for blob in byte_list:
        text = blob.decode()
        etype = text.split("event: ", 1)[1].split("\n", 1)[0]
        data = json.loads(text.split("data: ", 1)[1].strip())
        out.append((etype, data))
    return out


def test_stream_text_sequence():
    chunks = [
        {"choices": [{"delta": {"content": "Hel"}}]},
        {"choices": [{"delta": {"content": "lo"}}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {"completion_tokens": 1}},
        "[DONE]",
    ]
    events = _events(A.translate_sse_chunks(chunks, model="m", message_id="msg_1"))
    types = [t for t, _ in events]
    assert types == [
        "message_start", "content_block_start", "content_block_delta",
        "content_block_delta", "content_block_stop", "message_delta", "message_stop",
    ]
    assert events[0][1]["message"]["id"] == "msg_1"
    assert events[2][1]["delta"]["text"] == "Hel"
    assert events[-2][1]["delta"]["stop_reason"] == "end_turn"
    assert events[-2][1]["usage"]["output_tokens"] == 1


def test_stream_text_then_tool_closes_text_block_first():
    chunks = [
        {"choices": [{"delta": {"content": "thinking"}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "call_1", "function": {"name": "f", "arguments": ""}}]}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": '{"a":1}'}}]}}]},
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        "[DONE]",
    ]
    events = _events(A.translate_sse_chunks(chunks, model="m", message_id="i"))
    types = [t for t, _ in events]
    assert types == [
        "message_start",
        "content_block_start", "content_block_delta",  # text block 0
        "content_block_stop",                           # text closed before the tool opens
        "content_block_start", "content_block_delta",  # tool block 1
        "content_block_stop",
        "message_delta", "message_stop",
    ]
    tool_start = events[4][1]
    assert tool_start["index"] == 1
    assert tool_start["content_block"] == {"type": "tool_use", "id": "call_1", "name": "f", "input": {}}
    assert events[5][1]["delta"] == {"type": "input_json_delta", "partial_json": '{"a":1}'}


def test_stream_interleaved_parallel_tool_calls_keep_ids():
    # OpenAI can interleave parallel tool-call deltas across indices (0 opens, 1 opens, 0 continues,
    # 1 continues). Each call must keep its real id/name and full arguments — exactly two tool_use
    # blocks, not a third synthetic one with a dropped id/name.
    chunks = [
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "call_a", "function": {"name": "alpha", "arguments": '{"x":'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 1, "id": "call_b", "function": {"name": "beta", "arguments": '{"y":'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": "1}"}}]}}]},  # continuation of index 0
        {"choices": [{"delta": {"tool_calls": [
            {"index": 1, "function": {"arguments": "2}"}}]}}]},  # continuation of index 1
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        "[DONE]",
    ]
    events = _events(A.translate_sse_chunks(chunks, model="m", message_id="i"))
    starts = [d for t, d in events if t == "content_block_start"]
    assert len(starts) == 2  # exactly two blocks, not three
    assert starts[0]["content_block"] == {"type": "tool_use", "id": "call_a", "name": "alpha", "input": {}}
    assert starts[1]["content_block"] == {"type": "tool_use", "id": "call_b", "name": "beta", "input": {}}
    # Each call's argument fragments are concatenated, in order, onto its own block.
    args = {d["index"]: d["delta"]["partial_json"] for t, d in events if t == "content_block_delta"}
    assert args[starts[0]["index"]] == '{"x":1}'
    assert args[starts[1]["index"]] == '{"y":2}'


def test_stream_two_sequential_tool_calls_keep_ids():
    # The non-interleaved case (index 0 fully, then index 1) is unchanged: two blocks, real ids/names.
    chunks = [
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "c0", "function": {"name": "f0", "arguments": "{}"}}]}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 1, "id": "c1", "function": {"name": "f1", "arguments": "{}"}}]}}]},
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        "[DONE]",
    ]
    events = _events(A.translate_sse_chunks(chunks, model="m", message_id="i"))
    starts = [d for t, d in events if t == "content_block_start"]
    assert [s["content_block"]["id"] for s in starts] == ["c0", "c1"]
    assert [s["content_block"]["name"] for s in starts] == ["f0", "f1"]
    assert [s["index"] for s in starts] == [0, 1]


def test_stream_empty_emits_one_empty_text_block():
    events = _events(A.translate_sse_chunks(["[DONE]"], model="m", message_id="i"))
    types = [t for t, _ in events]
    assert types == ["message_start", "content_block_start", "content_block_stop",
                     "message_delta", "message_stop"]


def test_stream_finish_without_done_still_closes():
    events = _events(A.translate_sse_chunks(
        [{"choices": [{"delta": {"content": "hi"}}]}], model="m", message_id="i"))
    assert [t for t, _ in events][-2:] == ["message_delta", "message_stop"]


def test_async_messages_stream_buffers_split_lines():
    # A data: line split across two byte chunks must still parse once whole.
    byte_chunks = [
        b'data: {"choices":[{"delta":{"content":"He',
        b'llo"}}]}\n\ndata: [DONE]\n\n',
    ]

    async def gen():
        for b in byte_chunks:
            yield b

    async def collect():
        return [x async for x in A.messages_stream(gen(), model="m", message_id="i")]

    events = _events(asyncio.run(collect()))
    deltas = [d["delta"]["text"] for t, d in events if t == "content_block_delta"]
    assert deltas == ["Hello"]  # reassembled across the chunk boundary
