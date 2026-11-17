"""T-AGENT-02 · AGENT-R2/AGENT-R13: every state keeps to its tool allow-list (constitution III).

At most 4 domain tools plus the shared `report_concern`, and `record_call_outcome` is never an LLM tool
(the worker writes the outcome). Checked statically over all registered states and again per instance.
"""

from pathlib import Path

import pytest
from livekit.agents import RunContext, function_tool

import supportagent.agent.states  # noqa: F401  (importing it registers every state)
from supportagent.agent.base import (
    FORBIDDEN_TOOLS,
    MAX_DOMAIN_TOOLS,
    SHARED_TOOLS,
    STATE_REGISTRY,
    BaseStateAgent,
    StateToolError,
    check_registered_states,
    check_tools,
    class_tool_names,
    register_state,
)
from supportagent.agent.context import CallState
from supportagent.agent.prompts.registry import DEFAULT_PROMPTS_DIR, PromptRegistry


def _prompts(tmp_path: Path, *states: str) -> PromptRegistry:
    d = tmp_path / "prompts"
    d.mkdir(exist_ok=True)
    (d / "shared.md").write_text("---\nversion: '1'\n---\nrules\n")
    for s in states:
        (d / f"{s}.md").write_text("---\nversion: '1'\n---\nbody\n")
    return PromptRegistry(d)


def _tool_class(name: str, *tools: str) -> type[BaseStateAgent]:
    """A state class with one `@function_tool` per name."""
    namespace: dict[str, object] = {"state_name": name}
    for tool in tools:

        async def fn(self: BaseStateAgent, context: RunContext[CallState]) -> str:
            """A tool."""
            return "ok"

        fn.__name__ = tool
        namespace[tool] = function_tool(fn)
    return type(f"{name.title()}Agent", (BaseStateAgent,), namespace)


# ------------------------------------------------------------------ the shipped states


def test_every_registered_state_passes_the_static_check() -> None:
    check_registered_states()  # raises StateToolError, naming every offender


def test_registered_states_have_a_prompt_and_stay_in_budget() -> None:
    reg = PromptRegistry(DEFAULT_PROMPTS_DIR)
    for name, cls in STATE_REGISTRY.items():  # empty until T-AGENT-04 adds the first real states
        assert cls.state_name == name
        assert reg.load(name).version  # prompts/<state>.md exists and has a version
        names = class_tool_names(cls)
        assert "report_concern" in names
        assert len(set(names) - SHARED_TOOLS) <= MAX_DOMAIN_TOOLS
        assert not set(names) & FORBIDDEN_TOOLS


def test_the_rules_are_what_the_plan_says() -> None:
    assert MAX_DOMAIN_TOOLS == 4
    assert set(SHARED_TOOLS) == {"report_concern"}
    assert set(FORBIDDEN_TOOLS) == {"record_call_outcome"}


# ------------------------------------------------------------------ the check itself


def test_the_base_state_has_only_the_shared_tool() -> None:
    assert class_tool_names(BaseStateAgent) == ["report_concern"]


def test_four_domain_tools_plus_report_concern_is_allowed() -> None:
    cls = _tool_class("busy", "a", "b", "c", "d")
    assert class_tool_names(cls) == ["a", "b", "c", "d", "report_concern"]
    check_registered_states({"busy": cls})


def test_five_domain_tools_fail_and_the_message_names_them() -> None:
    cls = _tool_class("greedy", "a", "b", "c", "d", "e")
    with pytest.raises(StateToolError, match=r"state 'greedy' has 5 domain tools \(max 4\): a, b, c, d, e"):
        check_registered_states({"greedy": cls})


def test_record_call_outcome_is_forbidden_even_alone() -> None:
    cls = _tool_class("sneaky", "record_call_outcome")
    with pytest.raises(StateToolError, match=r"state 'sneaky' registers forbidden tool.*record_call_outcome"):
        check_registered_states({"sneaky": cls})


def test_inherited_tools_count_towards_the_budget() -> None:
    parent = _tool_class("parent", "a", "b", "c", "d")
    fifth = _tool_class("donor", "e").__dict__["e"]
    child = type("ChildAgent", (parent,), {"state_name": "child", "e": fifth})
    assert class_tool_names(child) == ["a", "b", "c", "d", "e", "report_concern"]
    with pytest.raises(StateToolError, match="state 'child' has 5 domain tools"):
        check_registered_states({"child": child})


def test_the_static_check_reports_every_offender_at_once() -> None:
    states = {
        "greedy": _tool_class("greedy", "a", "b", "c", "d", "e"),
        "sneaky": _tool_class("sneaky", "record_call_outcome"),
        "fine": _tool_class("fine", "a"),
    }
    with pytest.raises(StateToolError) as err:
        check_registered_states(states)
    message = str(err.value)
    assert "greedy" in message and "sneaky" in message and "fine" not in message


def test_check_tools_ignores_duplicates_and_the_shared_tool() -> None:
    check_tools("ok", ["report_concern", "a", "a", "b", "c", "d"])
    with pytest.raises(StateToolError):
        check_tools("bad", ["report_concern", "a", "b", "c", "d", "e"])


# ------------------------------------------------------------------ registration and instances


def test_register_state_rejects_a_bad_state_at_decoration() -> None:
    with pytest.raises(StateToolError):
        register_state(_tool_class("greedy_registered", "a", "b", "c", "d", "e"))
    assert "greedy_registered" not in STATE_REGISTRY


def test_register_state_needs_a_name_and_refuses_duplicates() -> None:
    with pytest.raises(TypeError, match="state_name"):
        register_state(type("Nameless", (BaseStateAgent,), {}))
    first = register_state(_tool_class("dup_probe", "a"))
    try:
        assert STATE_REGISTRY["dup_probe"] is first
        assert register_state(first) is first  # the same class again is fine
        with pytest.raises(ValueError, match="registered twice"):
            register_state(_tool_class("dup_probe", "b"))
    finally:
        STATE_REGISTRY.pop("dup_probe", None)


def test_a_state_without_a_name_cannot_be_built(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="state_name"):
        BaseStateAgent(prompts=_prompts(tmp_path))


def test_an_instance_cannot_sneak_in_a_forbidden_tool(tmp_path: Path) -> None:
    async def record_call_outcome(summary: str) -> str:
        """Write the outcome."""
        return "written"

    cls = _tool_class("plain", "a")
    with pytest.raises(StateToolError, match="record_call_outcome"):
        cls(prompts=_prompts(tmp_path, "plain"), tools=[function_tool(record_call_outcome)])


def test_an_instance_cannot_exceed_the_budget_through_tools(tmp_path: Path) -> None:
    def extra(name: str):  # type: ignore[no-untyped-def]
        async def fn() -> str:
            """Extra tool."""
            return name

        fn.__name__ = name
        return function_tool(fn)

    cls = _tool_class("almost_full", "a", "b", "c")
    cls(prompts=_prompts(tmp_path, "almost_full"), tools=[extra("d")])  # the 4th: fine
    with pytest.raises(StateToolError, match="5 domain tools"):
        cls(prompts=_prompts(tmp_path, "almost_full"), tools=[extra("d"), extra("e")])
