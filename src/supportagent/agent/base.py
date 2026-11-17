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

from collections.abc import AsyncGenerator, Callable, Iterable, Mapping
from typing import Any, ClassVar, Literal

from livekit.agents import Agent, ModelSettings, RunContext, function_tool
from livekit.agents.llm import ChatContext, ChatMessage, StopResponse, Tool, Toolset, find_function_tools
from livekit.agents.llm.tool_context import get_fnc_tool_names
from livekit.agents.types import NOT_GIVEN

from supportagent.agent.context import CallState
from supportagent.agent.interrupts import handle_concern, note_misunderstanding, screen_turn
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
"""What `report_concern` accepts: the detector's `InterruptKind`s plus `misunderstanding`."""


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
    ) -> str | Agent | tuple[Agent, str]:
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

    async def on_concern(self, kind: ConcernKind) -> str | Agent | tuple[Agent, str]:
        """What a concern the model reported does: the same handler the detector uses (AGENT-R3).

        A hand-off is silent (the new state speaks first). A misunderstanding is only passed on to a person
        once it has happened `MISUNDERSTANDING_LIMIT` turns in a row; before that the model is told to
        rephrase.
        """
        if kind == "misunderstanding" and not note_misunderstanding(self.state):
            return "Noted. Ask the caller once more, in simpler words."
        next_agent = await handle_concern(self, kind)
        return "Noted." if next_agent is None else self.hand_off(next_agent)

    # ------------------------------------------------------------------ global interrupts

    async def _screen(self, message: ChatMessage) -> bool:
        """Check one caller turn for an interrupt, once. True when the call moved to another state.

        A hit runs its handler in code (flags, tickets, a spoken confirmation) and hands the call to the
        handler's state on this same turn; the caller's turn is recorded as an outcome intent either way.
        """
        state = self.state
        if message.id in state.screened_messages:
            return False
        state.screened_messages.add(message.id)
        kind = screen_turn(state, message.text_content or "")
        if kind is None:
            return False
        next_agent = await handle_concern(self, kind)
        if next_agent is None:
            return False  # stay in this state; the model replies
        self.session.update_agent(next_agent)
        return True

    async def on_user_turn_completed(self, turn_ctx: ChatContext, new_message: ChatMessage) -> None:
        """Check every spoken caller turn for an interrupt *before the model replies* (AGENT-R3).

        On a hit the model's reply for this turn is skipped (`StopResponse`) and the call is already on its
        way to the handler's state. A turn with no interrupt goes to the model as usual.
        """
        if await self._screen(new_message):
            raise StopResponse()

    async def llm_node(
        self, chat_ctx: ChatContext, tools: list[Tool], model_settings: ModelSettings
    ) -> AsyncGenerator[Any, None]:
        """The text-input twin of `on_user_turn_completed`.

        LiveKit only calls `on_user_turn_completed` for a finished *spoken* turn; a typed turn (the text-mode
        harness, a chat box in the playground) goes straight to the model. Screening the newest caller message
        here covers that path; `screened_messages` makes it a no-op for a turn the hook already looked at, and
        for the later model rounds of the same turn (after tool results).
        """
        last = chat_ctx.items[-1] if chat_ctx.items else None
        if isinstance(last, ChatMessage) and last.role == "user" and await self._screen(last):
            return  # handed off: this state says nothing more
        async for chunk in Agent.default.llm_node(self, chat_ctx, tools, model_settings):
            yield chunk


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
