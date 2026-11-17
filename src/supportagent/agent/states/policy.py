"""`PolicyAgent`: general "how does this work" questions, answered only from retrieved policy (AGENT-R6, U4).

Policy is general information, so no verification is needed. The router hands off silently; this state
answers the question already in the chat history (`on_state_enter` asks the model for the reply).

The grounding rule is code, not prompt text:

- **No answer without a search in the same turn.** `llm_node` sets `tool_choice="required"` on the first model
  step after every caller turn, so the model must call a tool (`search_policy`, `offer_follow_up` or `finish`)
  before it can speak. Once a tool result is in, the choice is `"none"`: the model speaks, and cannot go
  looking elsewhere. A policy claim therefore only ever follows a `search_policy` result in the same turn.
- **Only retrieved text reaches the model.** A confident result is rendered as excerpts with their citations.
  Each chunk's `doc_id@version#section` goes to `CallState.kb_sources` (once each; the gateway records the
  same strings, so the outcome merges them without duplicates).
- **Low confidence says nothing about policy.** When the gateway reports `low_confidence`, or returns no
  chunks, or the search fails, the model never sees the retrieved text at all. The state speaks a fixed line
  ("I'm not sure ... would you like a colleague to call you back?") itself and raises `StopResponse`, so the
  model is not asked to say anything more. If the caller says yes, the model calls `offer_follow_up`, which
  books a callback through `request_callback`.

The product is not chosen here: the gateway defaults it to the verified caller's loan product and searches all
products otherwise. A product the model supplies is passed on only if it is a known `ProductCode`.

Two exits are code too (found on the first real call, where "how much do I owe the bank" was deflected with
the policy fallback): a caller turn that is an account question (`phrases.is_account_question`) moves to
the account route before the model sees it (verification first, then the account state), and so does a
farewell (`ClosingAgent(farewell=True)`). The model has the same exits as tools: `route_to_account`, `finish`.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import AsyncGenerator, Sequence
from typing import Any, Literal, get_args

from livekit.agents import Agent, ModelSettings, RunContext, function_tool
from livekit.agents.llm import ChatContext, ChatMessage, FunctionCallOutput, StopResponse, Tool
from livekit.agents.llm.chat_context import ChatItem

from supportagent.agent.base import BaseStateAgent, register_state
from supportagent.agent.context import CallState
from supportagent.agent.states import flow
from supportagent.common import clock
from supportagent.common.logging import get_logger
from supportagent.contracts.bank import ProductCode
from supportagent.contracts.gateway import PolicySource
from supportagent.contracts.outcome import Intent

log = get_logger(__name__)

# What the state says itself (never the model) when it cannot answer from policy.
LOW_CONFIDENCE_OFFER = (
    "I'm not sure about that, and I don't want to guess. "
    "Would you like me to arrange a call back from a colleague who can confirm it for you?"
)
UNAVAILABLE_OFFER = (
    "I can't look that up right now. "
    "Would you like me to arrange a call back from a colleague who can help with it?"
)
FOLLOW_UP_DELAY = dt.timedelta(days=1)  # a colleague calls back within a day
TOPIC_MAX = 80  # the callback reason is "policy question: <topic>", at most 200 characters in all

CALLBACK_BOOKED = (
    "A colleague will call the caller back tomorrow. Tell them so in one short sentence, say nothing about "
    "policy, and ask if there is anything else."
)
CALLBACK_FAILED = (
    "The call back could not be arranged just now. Apologise briefly, say nothing about policy, and ask "
    "whether they have another question."
)

ToolChoiceValue = Literal["required", "none"]
_PRODUCTS: frozenset[str] = frozenset(get_args(ProductCode))


def valid_product(product: str | None) -> ProductCode | None:
    """The product the model named, only when it is a known `ProductCode`; else `None` (gateway default)."""
    return product if product in _PRODUCTS else None  # type: ignore[return-value]


def render_excerpts(chunks: Sequence[PolicySource]) -> str:
    """What the model is shown after a confident search: the retrieved text and where it came from."""
    lines = [
        "Policy excerpts for this question. Answer only from them; if they do not state the answer, say "
        "you are not sure.",
    ]
    for number, chunk in enumerate(chunks, start=1):
        lines.append(f"[{number}] ({chunk.citation})\n{chunk.text}")
    return "\n\n".join(lines)


def tool_choice_for(items: Sequence[ChatItem]) -> ToolChoiceValue:
    """`required` on the first model step after a caller turn, `none` once a tool result is in.

    "First step" means the newest chat item is not a tool result: it is the caller's message (or, after
    a hand-off, the question the router left in the history). After any tool result the model must speak.
    """
    last = items[-1] if items else None
    if isinstance(last, FunctionCallOutput):
        return "none"
    return "required"


@register_state
class PolicyAgent(BaseStateAgent):
    state_name = flow.POLICY

    async def on_state_enter(self) -> None:
        # The router's hand-off is silent and the caller's question is already in the chat history.
        self.session.generate_reply()

    async def _screen(self, message: ChatMessage) -> bool:
        """Interrupts first (AGENT-R3), then an account question or a farewell in code (before the model)."""
        if await super()._screen(message):
            return True
        return await flow.route_on_phrases(self, message, farewell=True, account=True)

    async def llm_node(
        self, chat_ctx: ChatContext, tools: list[Tool], model_settings: ModelSettings
    ) -> AsyncGenerator[Any, None]:
        model_settings.tool_choice = tool_choice_for(chat_ctx.items)
        async for chunk in super().llm_node(chat_ctx, tools, model_settings):
            yield chunk

    # ------------------------------------------------------------------ tools

    @function_tool
    async def search_policy(
        self, context: RunContext[CallState], query: str, product: str | None = None
    ) -> str:
        """Look up how something works in the bank's policies. Call this before answering any such question.

        Args:
            query: The caller's question in plain words, with no names, numbers or account details.
            product: A product code (PERSONAL, AUTO, MORTGAGE, SME_WC, SALARY_ADV), only if the caller named
                a product; otherwise leave it out.
        """
        state = context.userdata
        result = await state.gateway.search_policy(query, valid_product(product))
        if not result.ok or result.data is None:
            code = result.error.code if result.error else "unknown"
            log.warning("policy search failed", extra={"state": self.state_name, "error_code": code})
            return await self._say_and_stop(UNAVAILABLE_OFFER)
        found = result.data
        if found.low_confidence or not found.chunks:
            log.info(
                "policy search not confident",
                extra={"state": self.state_name, "chunks": len(found.chunks)},
            )
            return await self._say_and_stop(LOW_CONFIDENCE_OFFER)
        for chunk in found.chunks:
            state.add_kb_source(chunk.citation)
        log.info(
            "policy search answered",
            extra={"state": self.state_name, "sources": [c.citation for c in found.chunks]},
        )
        return render_excerpts(found.chunks)

    @function_tool
    async def offer_follow_up(self, context: RunContext[CallState], topic: str) -> str:
        """Arrange a call back from a colleague. Only after the caller has said yes to it.

        Args:
            topic: Two or three words about the question, such as "early payoff". No names or numbers.
        """
        state = context.userdata
        when = clock.now() + FOLLOW_UP_DELAY
        reason = f"policy question: {topic.strip()[:TOPIC_MAX]}"
        result = await state.gateway.request_callback(when, reason)
        if not result.ok:
            code = result.error.code if result.error else "unknown"
            log.warning("follow-up callback failed", extra={"state": self.state_name, "error_code": code})
            return CALLBACK_FAILED
        state.record_intent(Intent.callback_request)
        return CALLBACK_BOOKED

    @function_tool
    async def route_to_account(self, context: RunContext[CallState]) -> str | Agent:
        """The caller asks about their own loan: what they owe, a balance, an amount or date due, a payment.

        Never answer that from policy; this moves the call to identity verification, then their account.
        """
        next_agent = flow.account_route(self)
        if next_agent is None:  # the account state is always registered; keep the call alive regardless
            return "Say you cannot look at their account on this call, and ask if there is anything else."
        return self.hand_off(next_agent)  # type: ignore[return-value]

    @function_tool
    async def finish(self, context: RunContext[CallState]) -> str | Agent:
        """The caller has no more questions about how things work, or wants something else.

        Use it when they say they are done, or ask for a person. Account questions use `route_to_account`.
        """
        router = flow.resolve(flow.ROUTER)
        if router is None:  # the router always exists on an inbound call; keep the call alive regardless
            return "Ask whether there is anything else you can help with."
        return self.hand_off(self.next_state(router))  # type: ignore[return-value]

    # ------------------------------------------------------------------ code-spoken lines

    async def _say_and_stop(self, line: str) -> str:
        """Speak `line` ourselves and skip the model's next reply, so it cannot add a policy claim."""
        self.session.say(line)  # queued, not awaited: this runs inside the reply being generated
        raise StopResponse()
