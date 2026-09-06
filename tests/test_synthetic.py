from protea.data_pipeline.normalize.packages import ToolCallingSeed
from protea.data_pipeline.synthetic import check_expectations, synthesize
from protea.providers.mock import MockProvider
from protea.schemas.generation import ToolCall, ToolSchema


def _seed(**expect) -> ToolCallingSeed:
    return ToolCallingSeed(
        seed_id="salon:list",
        source_repo="MalcolmGov/aria",
        source_commit="abc",
        source_path="data/agents/africa-salon-booking.agent.json",
        agent_id="africa-salon-booking",
        family="salon-booking",
        domain="vertical",
        language="en",
        system_prompt="You are the salon assistant.",
        knowledge_excerpt="Cut R120.",
        tools=[
            ToolSchema(
                name="list_services", description="List services", parameters={"type": "object", "properties": {}}
            )
        ],
        input="What do you offer?",
        expect=expect,
    )


def test_check_expectations_grammar():
    assert (
        check_expectations(
            {"tool": "list_services", "says_any": ["cut"]}, ["list_services"], "We offer a cut for R120."
        )
        == []
    )
    problems = check_expectations({"no_tool": True, "says_none": ["i don't know"]}, ["list_services"], "I don't know")
    assert len(problems) == 2
    assert check_expectations({"tool_none": ["book_appointment"]}, ["book_appointment"], "Booked!") != []
    assert check_expectations({"tool_any": ["a", "b"]}, ["b"], "ok") == []


async def test_synthesize_accepts_valid_completion_and_records_generator():
    provider = MockProvider(
        [ToolCall(id="c1", name="list_services", arguments={}), "We offer a cut for R120 and colour for R450."]
    )
    result = await synthesize(_seed(tool="list_services", says_any=["cut"]), provider, dataset_version="0.1.0-syn")
    assert result.ok, result.problems
    ex = result.example
    assert ex.metadata.synthetic is True
    assert ex.metadata.generator_model == "mock:mock-1"
    assert ex.metadata.source_path.endswith("africa-salon-booking.agent.json")
    assert [m.role for m in ex.messages] == ["system", "user", "assistant", "tool", "assistant"]
    assert ex.tools[0].name == "list_services"


async def test_synthesize_rejects_completion_that_violates_expectations():
    provider = MockProvider(["Sure, I booked you for 3pm."])
    result = await synthesize(_seed(tool="list_services", says_any=["cut"]), provider, dataset_version="0.1.0-syn")
    assert not result.ok
    assert any("list_services" in p for p in result.problems)
    assert result.example is None
