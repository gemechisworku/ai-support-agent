"""`BaseStateAgent`: what every conversation state is built on (AGENT-R13, AGENT-R2, constitution III).

A state is a LiveKit `Agent` with
- its prompt loaded from `prompts/<state_name>.md` (front-matter `version`) plus the shared rules
  (`prompts/shared.md`: tool results are data, not instructions; figures come from tools);
- an explicit tool allow-list: at most `MAX_DOMAIN_TOOLS` (4) `@function_tool` wrappers of its own, plus
  the shared `report_concern`; `record_call_outcome` is never an LLM tool (the worker writes the outcome);
- code-driven hand-offs: a tool returns `self.hand_off(NextState(...), message)` and the decision comes from
  Python on tool results, never from the model.

A new state::

    @register_state
    class VerifyAgent(BaseStateAgent):
        state_name = "verify"                      # prompts/verify.md

        async def on_state_enter(self) -> None:    # runs once on entry; `self.state` is the CallState
            ...

        @function_tool
        async def verify_identity(self, context: RunContext[CallState], full_name: str, ...) -> ...:
            result = await context.userdata.gateway.verify_identity(...)
            if result.ok and result.data and result.data.verified:
                return self.hand_off(RouterAgent(), "Verified.")
            return "Not verified."

The allow-list is enforced twice: statically over every `@register_state` class (`check_registered_states`,
run by tests/agent/test_state_registry.py) and when an instance is built (so a `tools=[...]` argument cannot
sneak a forbidden tool in). The gateway enforces verification-before-data again, server side.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import ClassVar, Literal

from livekit.agents import Agent, RunContext, function_tool
from livekit.agents.llm import ChatContext, Tool, Toolset, find_function_tools
from livekit.agents.llm.tool_context import get_fnc_tool_names
from livekit.agents.types import NOT_GIVEN

from supportagent.agent.context import CallState
from supportagent.agent.prompts.registry import PromptRegistry
from supportagent.common.logging import get_logger
from supportagent.common.settings import Settings

log = get_logger(__name__)

MAX_DOMAIN_TOOLS = 4
SHARED_TOOLS = frozenset({"report_concern"})
FORBIDDEN_TOOLS = frozenset({"record_call_outcome"})  # the worker writes the outcome, never the model

ConcernKind = Literal[
    "human_requested",
    "complaint",
    "legal",
    "hardship",
    "dispute",
    "stop_calling",
    "third_party",
    "misunderstanding",
]
"""What `report_concern` accepts. T-AGENT-03 maps each kind to its handler (as the detector does)."""


class StateToolError(AssertionError):
    """A state's tool list breaks the allow-list rules (too many domain tools, or a forbidden one)."""


def check_tools(state: str, names: Iterable[str]) -> None:
    """Raise `StateToolError` if `names` has a forbidden tool or more than `MAX_DOMAIN_TOOLS` domain tools."""
    all_names = list(names)
    problems = []
    forbidden = sorted(set(all_names) & FORBIDDEN_TOOLS)
    if forbidden:
        problems.append(f"registers forbidden tool(s) {', '.join(forbidden)}")
    domain = sorted(set(all_names) - SHARED_TOOLS)
    if len(domain) > MAX_DOMAIN_TOOLS:
        problems.append(f"has {len(domain)} domain tools (max {MAX_DOMAIN_TOOLS}): {', '.join(domain)}")
    if problems:
        raise StateToolError(f"state '{state}' " + " and ".join(problems))


def class_tool_names(cls: type[Agent]) -> list[str]:
    """Names of the `@function_tool`s a state class declares or inherits (no instance needed)."""
    return sorted({tool.info.name for tool in find_function_tools(cls)})


def prompt_variables(settings: Settings) -> dict[str, str]:
    """The placeholders prompts may use: `{bank_name}` and `{agent_name}` (PRD §12: configuration)."""
    return {"bank_name": settings.persona.bank_name, "agent_name": settings.persona.agent_name}


class BaseStateAgent(Agent):
    """Base of every conversation state. Subclasses set `state_name` and declare their own tools."""

    state_name: ClassVar[str] = ""

    def __init__(
        self,
        *,
        prompts: PromptRegistry | None = None,
        variables: Mapping[str, str] | None = None,
        chat_ctx: ChatContext | None = None,
        tools: list[Tool | Toolset] | None = None,
    ) -> None:
        name = type(self).state_name
        if not name:
            raise TypeError(
                f"{type(self).__name__} must set state_name (its prompt is prompts/<state_name>.md)"
            )
        registry = prompts or PromptRegistry()
        self.prompts = registry
        self.variables = dict(variables or {})
        prompt = registry.load(name)
        instructions = f"{prompt.render(self.variables)}\n\n{registry.shared().render(self.variables)}"
        super().__init__(
            instructions=instructions, tools=tools, chat_ctx=chat_ctx if chat_ctx is not None else NOT_GIVEN
        )
        self.prompt_version = prompt.version
        check_tools(name, get_fnc_tool_names(self.tools))

    # ------------------------------------------------------------------ lifecycle

    @property
    def state(self) -> CallState:
        """The call's `CallState` (the session's `userdata`)."""
        state: CallState = self.session.userdata
        return state

    async def on_enter(self) -> None:
        self.state.states_visited.append(self.state_name)
        log.info("state enter", extra={"state": self.state_name, "prompt_version": self.prompt_version})
        await self.on_state_enter()

    async def on_state_enter(self) -> None:
        """Entry action in code (say a disclosure verbatim, fetch a figure, ask the first question)."""

    # ------------------------------------------------------------------ hand-offs

    def next_state[S: BaseStateAgent](self, cls: type[S], **kwargs: object) -> S:
        """Build the next state with this one's prompts, variables and chat history (extra kwargs: `cls`)."""
        return cls(prompts=self.prompts, variables=self.variables, chat_ctx=self.chat_ctx, **kwargs)  # type: ignore[arg-type]

    def hand_off(self, next_agent: BaseStateAgent, message: str | None = None) -> Agent | tuple[Agent, str]:
        """Return this from a tool to move the call to `next_agent` (a code decision, not the model's).

        Without `message` the switch is silent: this state says nothing more and `next_agent.on_state_enter`
        speaks first. With `message` (the tool result the model sees), this state's model gets one more turn
        to speak before the switch, so use it only when that last line is wanted.
        """
        return next_agent if message is None else (next_agent, message)

    # ------------------------------------------------------------------ shared tool

    @function_tool
    async def report_concern(
        self, context: RunContext[CallState], kind: ConcernKind
    ) -> str | tuple[Agent, str]:
        """Report that the caller raised something outside this call's purpose, then follow the result.

        Use it when the caller asks for a person, complains, mentions a lawyer or bankruptcy, describes
        hardship, disputes a charge, asks to stop being called, says someone else is on the line, or you
        keep failing to understand them.

        Args:
            kind: What was raised: human_requested, complaint, legal, hardship, dispute, stop_calling,
                third_party or misunderstanding.
        """
        context.userdata.concerns.append(kind)
        log.info("concern reported", extra={"state": self.state_name, "kind": kind})
        return await self.on_concern(kind)

    async def on_concern(self, kind: ConcernKind) -> str | tuple[Agent, str]:
        """What a concern does. The base records it only; T-AGENT-03 hands off to the right handler."""
        return "Noted."


# --------------------------------------------------------------------------- the state registry

STATE_REGISTRY: dict[str, type[BaseStateAgent]] = {}


def register_state[S: type[BaseStateAgent]](cls: S) -> S:
    """Class decorator: list a state for the static allow-list check and fail fast on a bad one."""
    name = cls.state_name
    if not name:
        raise TypeError(f"{cls.__name__} must set state_name before @register_state")
    if name in STATE_REGISTRY and STATE_REGISTRY[name] is not cls:
        raise ValueError(f"state '{name}' is registered twice")
    check_tools(name, class_tool_names(cls))
    STATE_REGISTRY[name] = cls
    return cls


def check_registered_states(
    states: Mapping[str, type[BaseStateAgent]] | None = None,
    names_of: Callable[[type[Agent]], list[str]] = class_tool_names,
) -> None:
    """Static check over every registered state; raises `StateToolError` listing all offenders."""
    problems = []
    for name, cls in (STATE_REGISTRY if states is None else states).items():
        try:
            check_tools(name, names_of(cls))
        except StateToolError as exc:
            problems.append(str(exc))
    if problems:
        raise StateToolError("; ".join(problems))
