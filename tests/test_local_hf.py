import json

import pytest

from protea.providers.local_hf import (
    LocalHFProvider,
    parse_tool_calls,
    strip_reasoning,
    to_chat_messages,
    to_chat_tools,
)
from protea.schemas.generation import GenerationRequest, Message, ToolCall, ToolSchema


def test_strip_reasoning_drops_think_blocks_so_output_is_bare():
    # An (often empty) <think></think> prefix must not survive into the content, or a JSON task fails json_parsable.
    assert strip_reasoning('<think>\n\n</think>\n\n{"ok": true}') == '{"ok": true}'
    assert json.loads(strip_reasoning('<think>plan the graph</think>{"nodes": []}')) == {"nodes": []}
    # Untouched when there is no reasoning block.
    assert strip_reasoning("just an answer") == "just an answer"
    # An unterminated block (output cut off mid-reasoning) is dropped from the opener onward.
    assert strip_reasoning("done.<think>still thinking") == "done."


def test_parse_tool_calls_strips_reasoning_then_reads_content_and_calls():
    text = '<think>the user wants a lookup</think>Let me check.\n<tool_call>{"name": "lookup_order", "arguments": {}}</tool_call>'
    content, calls = parse_tool_calls(text)
    assert content == "Let me check."  # the reasoning trace is gone
    assert [c.name for c in calls] == ["lookup_order"]


def test_parse_tool_calls_and_content():
    text = 'Let me check.\n<tool_call>\n{"name": "lookup_order", "arguments": {"order_id": "4821"}}\n</tool_call>'
    content, calls = parse_tool_calls(text)
    assert content == "Let me check."
    assert [c.name for c in calls] == ["lookup_order"]
    assert calls[0].arguments == {"order_id": "4821"}
    assert calls[0].id == "call_1"
    content, calls = parse_tool_calls("<tool_call>not json</tool_call> plain answer")
    assert calls == []
    assert content == "plain answer"


def test_parse_tool_calls_handles_nested_json_and_unterminated_blocks():
    text = (
        'Sure.<tool_call>{"name": "book", "arguments": {"slot": {"day": "mon"}}}</tool_call> done <tool_call>{"name":'
    )
    content, calls = parse_tool_calls(text)
    assert content == "Sure. done"
    assert [c.name for c in calls] == ["book"]
    assert calls[0].arguments == {"slot": {"day": "mon"}}
    assert calls[0].id == "call_1"


def test_registry_metrics_keep_release_numbers_under_stable_names():
    from protea.cli_train import registry_metrics

    raw = {"train_loss": 0.75, "eval_loss": 0.55, "eval_mean_token_accuracy": 0.9, "eval_num_tokens": 207565.0}
    assert registry_metrics(raw) == {"train_loss": 0.75, "eval_loss": 0.55, "eval_accuracy": 0.9}


def test_chat_shape_carries_tool_calls_and_results():
    req = GenerationRequest(
        messages=[
            Message(role="system", content="Be brief."),
            Message(role="user", content="order 4821?"),
            Message(
                role="assistant", tool_calls=[ToolCall(id="c1", name="lookup_order", arguments={"order_id": "4821"})]
            ),
            Message(role="tool", name="lookup_order", tool_call_id="c1", content='{"status": "shipped"}'),
        ],
        tools=[ToolSchema(name="lookup_order", description="Look up", parameters={"type": "object", "properties": {}})],
    )
    msgs = to_chat_messages(req)
    assert msgs[2]["tool_calls"][0]["function"]["name"] == "lookup_order"
    assert msgs[3] == {"role": "tool", "name": "lookup_order", "content": '{"status": "shipped"}'}
    assert to_chat_tools(req)[0]["function"]["parameters"] == {"type": "object", "properties": {}}
    assert to_chat_tools(GenerationRequest(messages=[Message(role="user", content="hi")])) is None


async def test_generate_with_a_tiny_random_model(tmp_path):
    pytest.importorskip("torch")
    from protea.training.trainer import build_tiny_random

    records = [{"messages": [{"role": "user", "content": "hello there"}, {"role": "assistant", "content": "hi"}]}] * 4
    model, tok = build_tiny_random(records)
    model.save_pretrained(tmp_path / "tiny")
    tok.save_pretrained(tmp_path / "tiny")
    provider = LocalHFProvider(str(tmp_path / "tiny"), threads=1, served_as="protea-agent-0.0.1")
    # Device is auto-selected (None -> resolved at load): CPU here, CUDA on a GPU box like the eval pod. It must
    # not default to a pinned "cpu", which is what made the GPU eval load the 8B on CPU in fp32 and OOM.
    assert provider.device is None
    health = await provider.health()
    assert health.ok
    assert provider.model == "protea-agent-0.0.1"
    resp = await provider.generate(
        GenerationRequest(messages=[Message(role="user", content="hello there")], max_tokens=6, temperature=0.0)
    )
    assert resp.provider == "local"
    assert resp.model == "protea-agent-0.0.1"
    assert resp.usage.input_tokens > 0
    assert 0 < resp.usage.output_tokens <= 6
    assert resp.finish_reason in ("stop", "length")
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
    resp = await provider.generate(
        GenerationRequest(messages=[Message(role="user", content="say ok")], max_tokens=4, response_schema=schema)
    )
    assert resp.usage.output_tokens <= 4
    assert json.dumps(schema) not in (resp.content or "")  # the instruction is in the prompt, not echoed by contract
