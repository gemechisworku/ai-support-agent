"""T-AGENT-13 · AGENT-R1: the call's AgentSession wired to the states, silence policy and outcome recorder.

No audio, no network, no model: the session is a text-mode `AgentSession` with the scripted LLM, or a tiny
fake session that records `say` / `shutdown` and lets the test emit events. Silence runs on a fake clock.
"""

import asyncio
import datetime as dt
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from livekit.agents import AgentSession
from livekit.plugins import openai
from pydantic import SecretStr

import supportagent.agent.states  # noqa: F401  (importing it registers every state)
from supportagent.agent.context import CallState
from supportagent.agent.finalize import finalize
from supportagent.agent.session_factory import (
    REPROMPT_LINE,
    build_agent_session,
    entry_agent,
    silence_policy,
    wire_call,
)
from supportagent.agent.states.closing import goodbye_line
from supportagent.agent.states.greeting import GreetingAgent
from supportagent.agent.testing import FakeGateway, ScriptedLLM, call, say
from supportagent.common.settings import ModelsSettings, Settings
from supportagent.compliance.disclosures import load_disclosures
from supportagent.contracts.bank import PersonaCard
from supportagent.contracts.call import CallMetadata
from supportagent.contracts.gateway import LoanStatusOutput, LoanSummary, ToolResult
from supportagent.contracts.outcome import Campaign, ContactResult, Direction, Versions
from supportagent.observability.transcripts import TranscriptStore
from supportagent.voice.silence import SilencePolicy

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = load_disclosures(ROOT / "data" / "config" / "disclosures.yaml")
VERSIONS = Versions(prompt="0.1.0+abc", policy="p-1", model="llm:llamacpp/test")
FRANK = PersonaCard(
    customer_id=9001,
    full_name="Frank Alvarez",
    phone="+15550100001",
    extension="1001",
    dob=dt.date(1980, 1, 2),
    loan_id=1,
    loan_last4="4417",
    product_code="PERSONAL",
    situation="synthetic test persona",
    expected_outcome="n/a",
)
SPEAKABLE = "$200.72 overdue since 4 November 2026; next payment $200.72 due on 4 December 2026"
PERSONAL = LoanSummary(
    loan_masked="****4417",
    product_code="PERSONAL",
    product_name="Personal Loan",
    outstanding_balance=Decimal("3450.25"),
    overdue_amount=Decimal("200.72"),
    oldest_unpaid_due_date=dt.date(2026, 11, 4),
    next_due_date=dt.date(2026, 12, 4),
    next_due_amount=Decimal("200.72"),
    days_past_due=5,
    fees_accrued=Decimal("0.00"),
    status="active",
    speakable=SPEAKABLE,
)
VERIFY_FRANK = call("verify_identity", full_name="Frank Alvarez", dob="1980-01-02", loan_last4="4417")


class _FakeVad:
    capabilities = None


class _FakeTurnDetector:
    pass


VAD = _FakeVad()
TURN = _FakeTurnDetector()


def _state(gateway: FakeGateway | None = None, *, direction: Direction = Direction.inbound) -> CallState:
    meta = CallMetadata(
        call_id="11111111-2222-4333-8444-555555555555",
        direction=direction,
        campaign=Campaign.playground,
        customer_id=FRANK.customer_id,
        loan_id=FRANK.loan_id,
    )
    state = CallState(
        metadata=meta, gateway=gateway or FakeGateway(FRANK), session_token=SecretStr("fake-session-token")
    )
    state.disclosures = REGISTRY
    return state


# --------------------------------------------------------------------------- the pure builders


async def test_build_agent_session_carries_the_call_state_and_the_session_options(
    settings: Settings,
) -> None:
    state = _state()
    session = build_agent_session(settings, VAD, state, turn_detector=TURN)  # type: ignore[arg-type]
    assert isinstance(session, AgentSession)
    assert session.userdata is state
    assert isinstance(session.llm, openai.LLM)  # the local default, from the T-VOICE-02 factory
    assert session.vad is VAD


def test_the_entry_state_is_the_greeting_with_the_persona_variables(settings: Settings) -> None:
    agent = entry_agent(settings)
    assert isinstance(agent, GreetingAgent)
    assert agent.variables == {
        "bank_name": settings.persona.bank_name,
        "agent_name": settings.persona.agent_name,
    }


def test_the_silence_policy_uses_the_configured_thresholds(settings: Settings) -> None:
    models = ModelsSettings(
        _env_file=None, **{**settings.models.model_dump(), "silence_reprompt_s": 4.0, "silence_end_s": 9.0}
    )  # type: ignore[arg-type]
    policy = silence_policy(settings.model_copy(update={"models": models}))
    assert (policy.reprompt_after_s, policy.end_after_s, policy.max_reprompts) == (4.0, 9.0, 1)


# ------------------------------------------------------------ a fake session for the timing tests


class FakeSession:
    """Just enough of `AgentSession` for the wiring: events in, `say` / `shutdown` out."""

    def __init__(self, llm: object | None = None) -> None:
        self.handlers: dict[str, list[Callable[..., Any]]] = {}
        self.said: list[tuple[str, dict[str, Any]]] = []
        self.shutdowns: list[bool] = []
        self.llm = llm if llm is not None else ScriptedLLM([])

    def on(self, event: str, callback: Callable[..., Any] | None = None) -> Any:
        assert callback is not None
        self.handlers.setdefault(event, []).append(callback)
        return callback

    def off(self, event: str, callback: Callable[..., Any]) -> Any:
        self.handlers[event].remove(callback)

    def emit(self, event: str, **fields: Any) -> None:
        for handler in list(self.handlers.get(event, [])):
            handler(SimpleNamespace(**fields))

    def say(self, text: str, **kwargs: Any) -> None:
        self.said.append((text, kwargs))

    def shutdown(self, *, drain: bool = True) -> None:
        self.shutdowns.append(drain)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


async def _poll(session: FakeSession, until: Callable[[], bool]) -> None:
    for _ in range(200):
        if until():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("the silence poller did not act in time")


async def test_silence_reprompts_once_then_says_goodbye_and_ends_with_the_reason(settings: Settings) -> None:
    clock = Clock()
    session = FakeSession()
    state = _state()
    policy = SilencePolicy(6, 15, clock=clock)
    wiring = wire_call(session, state, settings, policy=policy, tick_s=0.001)  # type: ignore[arg-type]
    try:
        session.emit("agent_state_changed", new_state="speaking")
        session.emit("agent_state_changed", new_state="listening")  # the greeting is done: silence starts

        clock.now = 7  # past the re-prompt threshold
        await _poll(session, lambda: bool(session.said))
        assert session.said == [(REPROMPT_LINE, {})]
        assert session.shutdowns == []

        clock.now = 16  # past the end threshold
        await _poll(session, lambda: bool(session.shutdowns))
        goodbye = goodbye_line(settings.persona.bank_name)
        assert session.said[-1] == (goodbye, {"allow_interruptions": False})
        assert session.shutdowns == [True]  # drain: the goodbye is spoken first
        assert state.end_reason == "silence"
        assert len(session.said) == 2  # one re-prompt and the goodbye, nothing else
    finally:
        wiring.stop()


async def test_caller_speech_resets_the_silence_timers(settings: Settings) -> None:
    clock = Clock()
    session = FakeSession()
    wiring = wire_call(  # type: ignore[arg-type]
        session, _state(), settings, policy=SilencePolicy(6, 15, clock=clock), tick_s=0.001
    )
    try:
        session.emit("agent_state_changed", new_state="listening")
        clock.now = 5
        session.emit("user_state_changed", new_state="speaking")
        session.emit("user_state_changed", new_state="listening")
        clock.now = 10  # 5 s of quiet since the caller stopped: still under the threshold
        await asyncio.sleep(0.05)
        assert session.said == [] and session.shutdowns == []
    finally:
        wiring.stop()


async def test_closing_the_session_calls_back_so_the_worker_can_end_the_job(settings: Settings) -> None:
    session = FakeSession()
    closed: list[str] = []
    wiring = wire_call(session, _state(), settings, on_close=lambda: closed.append("closed"))  # type: ignore[arg-type]
    try:
        session.emit("close", reason="x")
        assert closed == ["closed"]
    finally:
        wiring.stop()


async def test_stop_detaches_the_poller_and_the_handlers(settings: Settings) -> None:
    session = FakeSession()
    wiring = wire_call(session, _state(), settings)  # type: ignore[arg-type]
    wiring.stop()
    await asyncio.sleep(0)
    for event in ("user_state_changed", "agent_state_changed"):
        assert session.handlers[event] == []


async def test_the_sentiment_classifier_is_built_from_the_sessions_own_model(settings: Settings) -> None:
    session = FakeSession()
    wiring = wire_call(session, _state(), settings)  # type: ignore[arg-type]
    custom = wire_call(session, _state(), settings, classify=_never)  # type: ignore[arg-type]
    try:
        assert callable(wiring.classify)
        assert custom.classify is _never
    finally:
        wiring.stop()
        custom.stop()


async def _never(transcript: str) -> str:
    return "neutral"


# --------------------------------------------------------------------------- a whole call in text mode


async def test_a_text_mode_call_greets_verifies_answers_a_balance_and_writes_one_outcome(
    settings: Settings, tmp_path: Path
) -> None:
    gateway = FakeGateway(
        FRANK, results={"get_loan_status": ToolResult.success(LoanStatusOutput(loans=[PERSONAL]))}
    )
    state = _state(gateway)
    llm = ScriptedLLM(
        [
            call("route_account_question"),  # "What do I owe?"
            VERIFY_FRANK,
            call("get_loan_status"),  # the account state answers the question already in the history
            say(f"You have {SPEAKABLE}."),
        ]
    )
    sentiment_calls: list[str] = []

    async def classify(transcript: str) -> str:
        sentiment_calls.append(transcript)
        return "positive"

    closed: list[str] = []
    async with AgentSession[CallState](llm=llm, userdata=state) as session:
        wiring = wire_call(session, state, settings, classify=classify, on_close=lambda: closed.append("x"))
        started = await session.start(entry_agent(settings), capture_run=True)
        if started is not None:
            await started  # the greeting's disclosure is spoken and the call is handed to the router
        await session.run(user_input="What do I owe?")
        await session.run(user_input="Frank Alvarez, 2 January 1980, 4417")
        wiring.stop()
        store = TranscriptStore(tmp_path)
        first = await finalize(
            state,
            settings=settings,
            store=store,
            versions=VERSIONS,
            classify=wiring.classify,
            retry_delay_s=0,
        )
        second = await finalize(state, settings=settings, store=store, versions=VERSIONS, retry_delay_s=0)

    assert state.states_visited == ["greeting", "router", "verify", "account"]
    ai_tag = next(tag for tag in state.disclosures_given if tag.startswith("ai_recording@"))
    assert state.disclosures_given[0] == ai_tag  # the disclosure is the first thing said
    assert state.verified is True and state.first_name
    assert gateway.tools_called == ["verify_identity", "get_loan_status", "record_call_outcome"]
    assert gateway.tools_called.count("record_call_outcome") == 1
    assert first is not None and first.recorded and second is None  # exactly one outcome
    outcome = gateway.outcomes[0]
    assert outcome.contact_result is ContactResult.right_party and outcome.verified is True
    assert outcome.campaign is Campaign.playground
    assert ai_tag in outcome.disclosures_given
    assert sentiment_calls and len(sentiment_calls) == 1  # classified once, at the end
    assert outcome.sentiment.value == "positive"
    # the recorder fed the transcript store with what was said and done
    turns = store.read(state.call_id)
    assert [t.speaker for t in turns if t.speaker == "caller"] == ["caller", "caller"]
    stored = " ".join(t.text for t in turns)
    for pii in ("Frank", "Alvarez", "1980", "4417"):  # the stored transcript is redacted (T-OBS-03)
        assert pii not in stored


async def test_a_silent_call_ends_as_no_answer_and_the_marker_names_the_reason(
    settings: Settings, tmp_path: Path
) -> None:
    gateway = FakeGateway(FRANK)
    state = _state(gateway)
    session = FakeSession()
    clock = Clock()
    wiring = wire_call(session, state, settings, policy=SilencePolicy(6, 15, clock=clock), tick_s=0.001)  # type: ignore[arg-type]
    try:
        session.emit("agent_state_changed", new_state="speaking")
        session.emit("agent_state_changed", new_state="listening")  # the greeting is done: silence starts
        clock.now = 16
        await _poll(session, lambda: bool(session.shutdowns))
    finally:
        wiring.stop()
    store = TranscriptStore(tmp_path)
    result = await finalize(state, settings=settings, store=store, versions=VERSIONS, retry_delay_s=0)
    assert result is not None and result.outcome.contact_result is ContactResult.no_answer
    assert store.read(state.call_id)[-1].text.endswith("reason: silence")


# ------------------------------------------------------------ latency and the dev event log (T-VOICE-05)


def _metrics(speech_id: str, eou_s: float, ttft_s: float, ttfb_s: float) -> list[Any]:
    from livekit.agents import metrics as lk

    common = {"timestamp": 1.0, "speech_id": speech_id}
    return [
        lk.EOUMetrics(
            end_of_utterance_delay=eou_s, transcription_delay=0.2, on_user_turn_completed_delay=0.0, **common
        ),
        lk.LLMMetrics(
            label="llm",
            request_id="r",
            duration=ttft_s + 0.5,
            ttft=ttft_s,
            cancelled=False,
            completion_tokens=10,
            prompt_tokens=100,
            prompt_cached_tokens=0,
            total_tokens=110,
            tokens_per_second=20.0,
            **common,
        ),
        lk.TTSMetrics(
            label="tts",
            request_id="r",
            ttfb=ttfb_s,
            duration=ttfb_s + 0.3,
            audio_duration=1.0,
            cancelled=False,
            characters_count=40,
            streamed=True,
            **common,
        ),
    ]


async def test_a_playground_call_outcome_has_latency_figures(settings: Settings, tmp_path: Path) -> None:
    session = FakeSession()
    state = _state()
    wiring = wire_call(session, state, settings, tick_s=0.001)  # type: ignore[arg-type]
    try:
        for speech_id, (eou_s, ttft_s, ttfb_s) in {"a": (0.4, 0.5, 0.3), "b": (0.5, 0.7, 0.2)}.items():
            for metric in _metrics(speech_id, eou_s, ttft_s, ttfb_s):
                session.emit("metrics_collected", metrics=metric)
        wiring.apply_latency(state)
    finally:
        wiring.stop()

    assert state.outcome.latency is not None and state.outcome.latency.p50_ms == 1300  # (1.2 + 1.4) / 2
    result = await finalize(
        state, settings=settings, store=TranscriptStore(tmp_path), versions=VERSIONS, retry_delay_s=0
    )
    assert result is not None
    assert result.outcome.campaign is Campaign.playground
    assert result.outcome.latency_p50 == 1300 and result.outcome.latency_p90 is not None
    assert {"eou", "llm_ttft", "tts_ttfb", "e2e"} <= set(result.outcome.latency_stages)


async def test_a_call_with_no_completed_turn_has_no_latency_figures(settings: Settings) -> None:
    session = FakeSession()
    state = _state()
    wiring = wire_call(session, state, settings, tick_s=0.001)  # type: ignore[arg-type]
    try:
        for metric in _metrics("a", 0.4, 0.5, 0.3)[:2]:  # the reply was never spoken: no TTS metric
            session.emit("metrics_collected", metrics=metric)
        wiring.apply_latency(state)
    finally:
        wiring.stop()
    assert state.outcome.latency is None


async def test_the_event_log_is_attached_only_when_configured(settings: Settings, tmp_path: Path) -> None:
    log = tmp_path / "events.jsonl"
    off = FakeSession()
    wiring = wire_call(off, _state(), settings, tick_s=0.001)  # type: ignore[arg-type]
    wiring.stop()
    off.emit("agent_state_changed", new_state="speaking", created_at=1.0)
    assert not log.exists()

    models = ModelsSettings(_env_file=None, **{**settings.models.model_dump(), "session_event_log": str(log)})  # type: ignore[arg-type]
    on = FakeSession()
    wiring = wire_call(on, _state(), settings.model_copy(update={"models": models}), tick_s=0.001)  # type: ignore[arg-type]
    wiring.stop()
    on.emit("agent_state_changed", new_state="speaking", created_at=2.0)
    assert '"new_state": "speaking"' in log.read_text()
