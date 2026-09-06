import pytest
from pydantic import BaseModel

from protea.providers import InMemoryUsageSink, StructuredOutputError
from protea.providers.base import extract_json
from protea.providers.mock import MockProvider
from protea.schemas.generation import GenerationRequest, Message, RequestMeta, ToolCall


class Spec(BaseModel):
    name: str
    tools: list[str]


def _req(text="hi", **meta) -> GenerationRequest:
    return GenerationRequest(messages=[Message(role="user", content=text)], metadata=RequestMeta(**meta))


async def test_generate_records_usage_without_prompt_text():
    sink = InMemoryUsageSink()
    p = MockProvider(["hello"], usage_sink=sink)
    resp = await p.generate(_req("secret prompt", task_type="agent_generation", tenant_ref="t-hash"))
    assert resp.content == "hello"
    assert resp.provider == "mock"
    assert len(sink.events) == 1
    ev = sink.events[0]
    assert ev.task_type == "agent_generation"
    assert ev.tenant_ref == "t-hash"
    assert "secret" not in ev.model_dump_json()


async def test_structured_output_validates():
    p = MockProvider([{"name": "Lead Qualifier", "tools": ["crm.create_contact"]}])
    spec = await p.generate_structured(_req(), Spec)
    assert spec.tools == ["crm.create_contact"]
    assert p.requests[0].response_schema["title"] == "Spec"


async def test_structured_output_failure_carries_raw():
    p = MockProvider(["not json at all"])
    request = _req()
    with pytest.raises(StructuredOutputError) as exc:
        await p.generate_structured(request, Spec)
    assert exc.value.raw_text == "not json at all"
    assert exc.value.retryable


async def test_tool_call_and_stream_default():
    p = MockProvider([ToolCall(id="c1", name="lookup", arguments={"id": 1}), "streamed text"])
    resp = await p.generate(_req())
    assert resp.finish_reason == "tool_calls"
    assert resp.tool_calls[0].name == "lookup"
    chunks = [c async for c in p.stream(_req())]
    assert chunks[0].text == "streamed text"
    assert chunks[-1].type == "done"


async def test_health():
    assert (await MockProvider().health()).ok


def test_extract_json_handles_fences_and_prose():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Sure! {"a": [1, 2]} hope that helps') == {"a": [1, 2]}
    assert extract_json('Here you go:\n```json\n{"b": true}\n```\nDone.') == {"b": True}
    assert extract_json('```json\n{"c": 1}') == {"c": 1}  # unterminated fence falls back to brace scan
    with pytest.raises(ValueError):
        extract_json("")
