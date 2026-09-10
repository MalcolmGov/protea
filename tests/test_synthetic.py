from protea.data_pipeline.normalize.packages import ToolCallingSeed
from protea.data_pipeline.synthetic import check_expectations, select_seeds, selection_summary, synthesize
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


def _sel_seed(seed_id: str, *, tool: str | None = None, family: str = "fam", contaminated: bool = False) -> ToolCallingSeed:
    return ToolCallingSeed(
        seed_id=seed_id,
        source_repo="MalcolmGov/aria",
        source_commit="abc",
        source_path="data/agents/x.agent.json",
        agent_id="x",
        family=family,
        domain="vertical",
        language="en",
        system_prompt="s",
        knowledge_excerpt="",
        tools=[],
        input="hi",
        expect={"tool": tool} if tool else {},
        contaminated=contaminated,
    )


def test_select_seeds_default_is_first_n_and_skips_contaminated_and_held_out():
    seeds = [
        _sel_seed("a:happy", tool="book_appointment"),
        _sel_seed("b:bad", contaminated=True),
        _sel_seed("c:sealed", family="sealed-fam"),
        _sel_seed("d:happy", tool="handoff_to_human"),
        _sel_seed("e:happy", tool="log_tax_enquiry"),
    ]
    chosen, skipped = select_seeds(iter(seeds), limit=2, held_out=frozenset({"sealed-fam"}))
    assert [s.seed_id for s in chosen] == ["a:happy", "d:happy"]  # file order, contaminated + held-out removed
    assert skipped == 1


def test_select_seeds_targets_tools_and_scenarios():
    seeds = [
        _sel_seed("a:write-after-confirm", tool="book_appointment"),
        _sel_seed("b:explicit-human", tool="handoff_to_human"),
        _sel_seed("c:write-after-confirm", tool="log_tax_enquiry"),
        _sel_seed("d:grounded-happy", tool="list_services"),
    ]
    by_tool, _ = select_seeds(iter(seeds), limit=10, target_tools=frozenset({"book_appointment", "log_tax_enquiry"}))
    assert {s.seed_id for s in by_tool} == {"a:write-after-confirm", "c:write-after-confirm"}
    by_scenario, _ = select_seeds(iter(seeds), limit=10, scenarios=("write-after-confirm",))
    assert {s.seed_id for s in by_scenario} == {"a:write-after-confirm", "c:write-after-confirm"}


def test_select_seeds_balance_spreads_across_tools_instead_of_file_order():
    # File order is three book_appointment then one log_tax_enquiry; balanced selection must not return only the first tool.
    seeds = [
        _sel_seed("a", tool="book_appointment"),
        _sel_seed("b", tool="book_appointment"),
        _sel_seed("c", tool="book_appointment"),
        _sel_seed("d", tool="log_tax_enquiry"),
    ]
    chosen, _ = select_seeds(iter(seeds), limit=2, balance=True)
    assert {s.expect["tool"] for s in chosen} == {"book_appointment", "log_tax_enquiry"}
    # Without balance the same limit would take two book_appointments (file order).
    unbalanced, _ = select_seeds(iter(seeds), limit=2, balance=False)
    assert [s.expect["tool"] for s in unbalanced] == ["book_appointment", "book_appointment"]


def test_selection_summary_reports_tool_and_scenario_spread():
    seeds = [
        _sel_seed("a:write-after-confirm", tool="book_appointment"),
        _sel_seed("b:write-after-confirm", tool="log_tax_enquiry"),
        _sel_seed("c:explicit-human", tool="handoff_to_human"),
    ]
    summary = selection_summary(seeds)
    assert summary["seeds"] == 3
    assert summary["by_target_tool"] == {"book_appointment": 1, "log_tax_enquiry": 1, "handoff_to_human": 1}
    assert summary["by_scenario"]["write-after-confirm"] == 2


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
