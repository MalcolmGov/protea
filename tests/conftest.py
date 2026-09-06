import pytest

from protea.schemas.examples import ExampleMetadata, TaskType, TrainingExample
from protea.schemas.generation import Message, ToolCall, ToolSchema


@pytest.fixture
def lookup_tool() -> ToolSchema:
    return ToolSchema(
        name="get_order_status",
        description="Look up an order",
        parameters={"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]},
    )


@pytest.fixture
def meta_kwargs() -> dict:
    return dict(
        dataset_version="0.1.0",
        source_type="agent_definition",
        source_repo="MalcolmGov/aria",
        source_commit="c22c31b",
        source_path="data/agents/africa-salon-booking.agent.json",
        source_id="africa-salon-booking",
        family="salon-booking",
        domain="hospitality",
        task_type=TaskType.TOOL_CALLING,
        language="en-ZA",
    )


@pytest.fixture
def tool_example(lookup_tool, meta_kwargs) -> TrainingExample:
    return TrainingExample(
        metadata=ExampleMetadata(**meta_kwargs),
        tools=[lookup_tool],
        messages=[
            Message(role="system", content="You are the order desk."),
            Message(role="user", content="Where is order 4821?"),
            Message(
                role="assistant",
                tool_calls=[ToolCall(id="c1", name="get_order_status", arguments={"order_id": "4821"})],
            ),
            Message(role="tool", tool_call_id="c1", name="get_order_status", content='{"status": "out_for_delivery"}'),
            Message(role="assistant", content="Order 4821 is out for delivery."),
        ],
    )
