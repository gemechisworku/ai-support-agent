# 07 · Voice pipeline and model servers — Spec

> **Code:** `VOICE` · **Location:** `src/supportagent/voice/`, `scripts/models/` · **Milestones:** M2 (M4 benchmark)
> **PRD:** F1, F14, NFR-LAT, NFR-CAP, AC-EXP · **Depends on:** PLAT (settings) · **Status:** see [tasks.md](tasks.md)

## Purpose
Turns audio into text and text into audio fast enough for a natural phone conversation, using local
model servers on the Mac's GPU by default and a hosted LLM switchable by configuration. It provides the
STT → LLM → TTS building blocks and session tuning (VAD, turn detection, barge-in, silence handling,
latency metrics) that the agent worker (`06-agent`) plugs its state machine into.

## Scope
**In:** native model-server setup and health checks (llama.cpp for the LLM and embeddings, whisper.cpp, Kokoro,
LiveKit server in dev mode); provider factories built from settings; voice-session configuration (Silero VAD, LiveKit turn
detector, barge-in, sentence-level TTS streaming, silence policy); per-stage latency metrics; browser
playground dev mode; latency benchmark.
**Out:** conversation states, prompts and tools (`06-agent`); SIP, Asterisk, LiveKit SIP and production
LiveKit config (`08-telephony`); tracing export (`10-observability`); text-mode scenario harness (`12-evals`).

## Requirements
- **VOICE-R1** (P0 · F1, NFR-CAP) — The project shall provide idempotent scripts that install, start and health-check the native model servers (llama.cpp `llama-server` with Llama 3.1 8B Instruct on :8090 and nomic-embed-text v1.5 on :8082, whisper.cpp server on :8178, Kokoro-FastAPI on :8880, LiveKit server in dev mode on :7880) within the 16 GB memory budget.
- **VOICE-R2** (P0 · F1) — The voice module shall build streaming STT, LLM and TTS components from `Settings` alone, with the local providers as the default and no endpoint or model name hard-coded outside settings.
- **VOICE-R3** (P1 · F14) — Setting `LLM_PROVIDER=anthropic` (model `claude-haiku-5-5`) shall switch the LLM to a hosted provider with no code change, and the active provider/model id shall be reported for the call's `versions.model`.
- **VOICE-R4** (P0 · F1, NFR-LAT) — The voice session shall use Silero VAD and model-based end-of-turn detection, stream TTS sentence by sentence while the LLM is still generating, and stop TTS within 300 ms of the caller barging in.
- **VOICE-R5** (P0 · F1) — On caller silence the session shall re-prompt once after `SILENCE_REPROMPT_S` and politely end the call after `SILENCE_END_S`, handing the closing line and the end reason to the agent.
- **VOICE-R6** (P0 · NFR-LAT, AC-EXP) — The voice module shall measure per-turn latency (end-of-utterance delay, STT, LLM time-to-first-token, TTS time-to-first-byte, end-of-speech → first agent audio) and report p50/p90 for the call outcome.
- **VOICE-R7** (P0 · F1) — A developer shall be able to talk to the agent from a browser (LiveKit Agents playground) with no telephony running, using one make target.
- **VOICE-R8** (P1 · NFR-LAT, NFR-CAP, AC-EXP) — A repeatable benchmark shall compare local and hosted LLM configurations against the NFR-LAT targets and record memory use with 1 and 2 concurrent sessions.

## Acceptance criteria
- **VOICE-R1:** Given a Mac with Homebrew, when `make models-setup && make models-up` runs, then `make models-health` reports all five servers healthy; running setup twice changes nothing.
- **VOICE-R2:** Given default settings, when `build_pipeline(settings)` is called, then it returns STT/LLM/TTS objects pointed at `STT_URL`, `LLM_URL` and `TTS_URL`; given a missing URL, settings validation fails at startup, not mid-call.
- **VOICE-R3:** Given `LLM_PROVIDER=anthropic` and `ANTHROPIC_API_KEY` set, the factory returns the Anthropic LLM; given the key missing, startup fails with a clear error; `model_version()` returns `anthropic/claude-haiku-5-5`.
- **VOICE-R4:** The local LLM is called through llama.cpp's OpenAI-compatible endpoint with tool calls enabled (`--jinja`); the TTS input is split at sentence boundaries; in the live checklist, barge-in stops agent audio in < 300 ms (measured from the `agent_state_changed` timestamps).
- **VOICE-R5:** With a fake clock: no speech for `SILENCE_REPROMPT_S` → exactly one re-prompt; still silent at `SILENCE_END_S` → `end_call(reason="silence")`; any caller speech resets the timers.
- **VOICE-R6:** Given a sequence of synthetic metric events for 10 turns, the tracker returns correct p50/p90 per stage and a `latency` dict that validates against `CallOutcome`.
- **VOICE-R7:** `make agent-dev` starts the worker in dev mode; joining the playground room starts an inbound-style conversation with `campaign="playground"`.
- **VOICE-R8:** `make bench-latency` writes `docs/reports/latency-YYYY-MM-DD.md` with p50/p90 per stage for local and hosted runs, and peak RSS per server.

## Interfaces
**Provides** (`supportagent.voice`):
- `build_stt(settings)`, `build_llm(settings)`, `build_tts(settings)`, `build_vad()`, `build_turn_detector()`
- `build_session_options(settings) -> SessionOptions`: kwargs for `livekit.agents.AgentSession`
  (vad, turn detection, stt, llm, tts, interruption and endpointing delays).
- `model_version(settings) -> str`, e.g. `llamacpp/llama-3.1-8b-instruct`, `anthropic/claude-haiku-5-5` (OBS version stamping).
- `SilencePolicy` (pure, clock-injected) and `attach_silence_policy(session, policy, on_end)`.
- `LatencyTracker`: `observe(event)`, `summary() -> LatencySummary` (p50/p90 per stage) and
  `to_outcome() -> dict` for the `latency_p50`/`latency_p90` fields of `CallOutcome` (`contracts/outcome.py`).
- Scripts: `scripts/models/{setup.sh,up.sh,down.sh,health.py}`, `scripts/dev_token.py`, `scripts/bench_latency.py`.
- Make targets: `models-setup`, `models-up`, `models-down`, `models-health`, `agent-dev`, `bench-latency`.

**Consumes:** `supportagent.common.settings` (PLAT); `CallOutcome` latency fields (PLAT contracts);
the agent worker entrypoint (`06-agent`, T-AGENT-01) for playground mode; the text-mode harness
(`12-evals`, T-EVAL-01) for the benchmark.

## Non-functional
- NFR-LAT: median end-of-speech → first audio ≤ 2.0 s local, ≤ 1.2 s hosted; barge-in < 300 ms.
- NFR-CAP: all native servers plus Docker stay within the ~13 GB budget (architecture §6); fallbacks are
  Llama 3.2 3B (`LLM_GGUF`) and whisper `small.en` (the default), selected by settings only.
- Model servers bind to `127.0.0.1` only. Hosted LLM use is synthetic data only (constitution VI).

## Open questions
- Is Kokoro voice `af_heart` acceptable for the persona? (Default for now; set in settings `TTS_VOICE`.)
- Exact silence timings: defaults `SILENCE_REPROMPT_S=7`, `SILENCE_END_S=20`. Tune in T-VOICE-06.
