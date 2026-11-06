# 07 · Voice pipeline and model servers — Tasks

> Each task is one GitHub issue and one PR. The field bullets are parsed by `scripts/tasks.py`, so keep
> their exact format. Status is `todo` or `done`. Issue is `—` until `make issues-sync` fills it in.
> Ids are never reused. Order tasks by dependency.

## Tasks

### T-VOICE-01 · Native model servers: setup scripts and health check
- **Status:** todo
- **Milestone:** M2
- **Issue:** #41
- **Depends on:** T-PLAT-06
- **Requirements:** VOICE-R1

Write idempotent shell scripts that install and run the native (Metal) model servers described in
[plan.md](plan.md#file-layout), plus a stdlib health probe. `make doctor` (T-PLAT-06) checks the
prerequisites; this task installs and runs the servers.

- `setup.sh`: `brew install ollama whisper-cpp livekit livekit-cli`; `ollama pull qwen3:8b nomic-embed-text`
  (and `qwen3:4b` with `--with-fallbacks`); download `ggml-large-v3-turbo.bin` and `ggml-small.en.bin`
  into `models/whisper/` (skip if the checksum matches); clone Kokoro-FastAPI into `vendor/`.
- `up.sh`: start `ollama serve` (if not running), `whisper-server -m models/whisper/ggml-$WHISPER_MODEL.bin
  --host 127.0.0.1 --port 8178 --inference-path /v1/audio/transcriptions`, Kokoro-FastAPI on :8880, and
  `livekit-server --dev --bind 127.0.0.1` on :7880. Pids go to `var/run/`, logs to `var/log/`.
- `down.sh` stops what `up.sh` started.
- `health.py` probes each server:
  - Ollama: `GET /api/tags` lists both models.
  - whisper: `GET /`.
  - Kokoro: `GET /v1/models`.
  - LiveKit: `GET /` on :7880.

**Acceptance criteria**
- [ ] `make models-setup` is idempotent; a second run downloads nothing.
- [ ] `make models-up` then `make models-health` reports four healthy servers; `make models-down` stops them.
- [ ] All servers bind to 127.0.0.1; URLs and ports come from `.env` (`OLLAMA_URL`, `STT_URL`, `TTS_URL`, `LIVEKIT_URL`).
- [ ] The memory footprint of each server is listed in `docs/dev-setup.md` (link from AGENTS.md "native services").
- [ ] `models/`, `vendor/` and `var/` are git-ignored.

**Tests**
- `tests/voice/test_health_script.py` (unit): runs `health.py` against stub `http.server` endpoints and asserts exit code 0 when all are healthy, and 1 with a named failure when one is down or a model is missing.
- `tests/voice/test_live_pipeline.py::test_servers_healthy` (live): real `health.py` returns 0.

**Files:** `scripts/models/setup.sh`, `scripts/models/up.sh`, `scripts/models/down.sh`, `scripts/models/health.py`, `Makefile`, `docs/dev-setup.md`, `.gitignore`, `tests/voice/test_health_script.py`

### T-VOICE-02 · Provider factory with local default and hosted switch
- **Status:** todo
- **Milestone:** M2
- **Issue:** #42
- **Depends on:** T-PLAT-02
- **Requirements:** VOICE-R2, VOICE-R3

Add `supportagent.voice.providers` with `build_stt`, `build_llm`, `build_tts`, `build_vad`,
`build_turn_detector` and `model_version`, all driven by `Settings` (new fields listed in
[plan.md](plan.md#data-model--contracts)). The local defaults are whisper.cpp through
`openai.STT` + `StreamAdapter`, Ollama `qwen3:8b` with thinking disabled, and Kokoro through
`openai.TTS`. `LLM_PROVIDER=anthropic` selects `anthropic.LLM(model="claude-haiku-5-5")`.

**Acceptance criteria**
- [ ] No URL, model name or voice is hard-coded outside `Settings` defaults.
- [ ] Every Ollama LLM request carries the thinking-off option.
- [ ] `LLM_PROVIDER=anthropic` without `ANTHROPIC_API_KEY` fails at settings validation with a clear message.
- [ ] `model_version(settings)` returns `"<provider>/<model>"` and is used by OBS version stamping.
- [ ] New settings are documented in `.env.example`.

**Tests**
- `tests/voice/test_providers.py` (unit): default settings build the OpenAI-compatible STT/LLM/TTS with the expected base URLs and model ids; the Ollama LLM has thinking disabled; the anthropic switch returns the Anthropic LLM; the missing key raises; `model_version` covers both providers. No network: construct objects only.

**Files:** `src/supportagent/voice/providers.py`, `src/supportagent/common/settings.py`, `.env.example`, `pyproject.toml`, `uv.lock`, `tests/voice/test_providers.py`

### T-VOICE-03 · Pipeline tuning: VAD, turn detection, barge-in, streaming TTS, silence policy
- **Status:** todo
- **Milestone:** M2
- **Issue:** #43
- **Depends on:** T-VOICE-02
- **Requirements:** VOICE-R4, VOICE-R5

Add `session.build_session_options(settings)` returning the `AgentSession` kwargs:
- VAD and turn detector
- interruptions enabled
- endpointing and interruption delays from settings
- sentence tokenizer for streaming TTS

Add the pure `silence.SilencePolicy(reprompt_after_s, end_after_s, max_reprompts=1, clock)` and
`attach_silence_policy(session, policy, on_reprompt, on_end)`, which subscribes to session state
events. The agent (T-AGENT-08) supplies the re-prompt and closing lines.

**Acceptance criteria**
- [ ] `build_session_options` enables interruptions and model-based turn detection, and passes STT/LLM/TTS from the factory.
- [ ] TTS is fed sentence by sentence (the first sentence is spoken before the LLM finishes).
- [ ] Silence: one re-prompt after `SILENCE_REPROMPT_S`; `on_end("silence")` after `SILENCE_END_S`; caller speech resets the timers; agent speech does not count as caller activity.
- [ ] Live checklist: barge-in stops TTS in < 300 ms (agent state `speaking → listening` delta in logs).

**Tests**
- `tests/voice/test_session.py` (unit): the options include VAD, turn detection, `allow_interruptions=True` and the configured delays.
- `tests/voice/test_silence.py` (unit, fake clock): the re-prompt fires once; the end fires at the threshold; speech resets the timers; there is no re-prompt while the agent is speaking.
- `tests/voice/test_live_pipeline.py::test_barge_in` (live): manual-assisted timing check, documented in the docstring.

**Files:** `src/supportagent/voice/session.py`, `src/supportagent/voice/silence.py`, `tests/voice/test_session.py`, `tests/voice/test_silence.py`

### T-VOICE-04 · Per-stage latency metrics
- **Status:** todo
- **Milestone:** M2
- **Issue:** #44
- **Depends on:** T-VOICE-03
- **Requirements:** VOICE-R6

Add `latency.LatencyTracker`. It subscribes to `metrics_collected`, groups EOU/STT/LLM/TTS metrics by
`speech_id`, and computes per-turn stage latencies and e2e (end of speech → first agent audio).
`summary()` returns p50/p90 per stage; `to_outcome()` fills `CallOutcome.latency_p50/p90` and adds
`latency_stages` for traces (OBS).

**Acceptance criteria**
- [ ] Turns with missing stages (e.g. a tool-only turn) are skipped, not counted as zero.
- [ ] Percentiles are correct for 1, 2 and 10 turns; an empty tracker returns `None` values, not an error.
- [ ] The tracker emits a structured log line per turn (no transcript text, so no PII).

**Tests**
- `tests/voice/test_latency.py` (unit): synthetic metric events give the known p50/p90; out-of-order events are matched by `speech_id`; incomplete turns are skipped; `to_outcome()` output validates against the `CallOutcome` model.

**Files:** `src/supportagent/voice/latency.py`, `tests/voice/test_latency.py`

### T-VOICE-05 · Browser playground dev mode
- **Status:** todo
- **Milestone:** M2
- **Issue:** #45
- **Depends on:** T-VOICE-03, T-AGENT-01
- **Requirements:** VOICE-R7

Make the agent reachable with no telephony.
- `make agent-dev` runs the worker (`python -m supportagent.agent.worker dev`) against the dev
  LiveKit server.
- `scripts/dev_token.py` mints a room token for the LiveKit Agents playground or `lk room join`.
- A room with no `call_id` metadata is treated as an inbound call with `campaign="playground"` and a
  fresh `call_id`. The worker builds that context in T-AGENT-01; this task wires the dev defaults and
  the docs.

**Acceptance criteria**
- [ ] `make models-up && make agent-dev`, then opening the playground with the printed token, gives a spoken greeting.
- [ ] The README / `docs/dev-setup.md` has a 5-step "talk to the agent" guide.
- [ ] The playground call produces a `CallOutcome` with `campaign="playground"` and latency fields.

**Tests**
- `tests/voice/test_dev_token.py` (unit): the token decodes (PyJWT) with the room name, identity and grants; the dev key and secret come from settings.
- `tests/voice/test_live_pipeline.py::test_playground_greeting` (live): connect a headless participant, assert the agent publishes an audio track within 5 s.

**Files:** `scripts/dev_token.py`, `Makefile`, `docs/dev-setup.md`, `tests/voice/test_dev_token.py`

### T-VOICE-06 · Latency benchmark: local vs hosted
- **Status:** todo
- **Milestone:** M4
- **Issue:** #79
- **Depends on:** T-VOICE-04, T-EVAL-01
- **Requirements:** VOICE-R8

Write `scripts/bench_latency.py`. For each LLM configuration (`ollama/qwen3:8b`, `ollama/qwen3:4b`,
`anthropic/claude-haiku-5-5`) and each whisper model, it:
- plays N pre-recorded caller WAVs (`tests/fixtures/audio/*.wav`) through the STT endpoint
- runs the matching text turns through the text-mode harness (T-EVAL-01) for LLM TTFT
- synthesises the first reply sentence for TTS TTFB

It also samples the peak RSS of each server with 1 and 2 concurrent sessions, then writes a dated
markdown report and a recommendation that tunes the defaults (delays, silence timings, model choice).

**Acceptance criteria**
- [ ] The report `docs/reports/latency-YYYY-MM-DD.md` has p50/p90 per stage per configuration, peak memory, and pass/fail against NFR-LAT.
- [ ] Recommended defaults are applied to `Settings` / `.env.example` in the same PR.
- [ ] The hosted run is skipped with a note when no `ANTHROPIC_API_KEY` is set.

**Tests**
- `tests/voice/test_bench_report.py` (unit): the report renderer produces the table and pass/fail from fixed numbers.
- `scripts/bench_latency.py` (eval): run manually; output committed as the report.

**Files:** `scripts/bench_latency.py`, `tests/fixtures/audio/`, `tests/voice/test_bench_report.py`, `docs/reports/`, `src/supportagent/common/settings.py`
