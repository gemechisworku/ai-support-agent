# 07 · Voice pipeline and model servers — Plan

> Implements [spec.md](spec.md). Tasks in [tasks.md](tasks.md).

## Approach
Use LiveKit Agents' cascaded pipeline (architecture §3, §6) with OpenAI-compatible local servers, so one
plugin (`livekit-plugins-openai`) covers STT, LLM and TTS. Swapping to the hosted LLM just picks a
different plugin.

- **STT:** whisper.cpp `whisper-server` started with `--inference-path /v1/audio/transcriptions`, used
  through `openai.STT(base_url=STT_URL/v1, model="whisper-1")`. Whisper is not truly streaming, so the
  STT is wrapped in `stt.StreamAdapter(stt, vad)` for VAD-segmented transcription (PRD §12 mitigation).
- **LLM:** llama.cpp `llama-server -m $LLM_GGUF --alias $LLM_MODEL --jinja` (Llama 3.1 8B Instruct
  Q4_K_M), used through `openai.LLM(model=LLM_MODEL, base_url=LLM_URL, api_key="local")`. `--jinja`
  applies the model's chat template, which gives OpenAI-style tool calls. Llama 3.1 has no thinking
  mode, so there is nothing to disable.
  The hosted option is `anthropic.LLM(model="claude-haiku-5-5")`.
- **TTS:** Kokoro-FastAPI through `openai.TTS(base_url=TTS_URL/v1, model="kokoro", voice=TTS_VOICE)`.
  The session's sentence tokenizer streams TTS per sentence.
- **VAD / turns:** `silero.VAD.load()` (prewarmed in the worker's `prewarm`) plus the LiveKit English
  turn detector model. Interruptions are enabled. The delays are settings (T-VOICE-03):
  `INTERRUPTION_MIN_S` (0.4), `ENDPOINTING_MIN_DELAY_S` (0.4) and `ENDPOINTING_MAX_DELAY_S` (3.0), tuned in
  T-VOICE-06. `session.build_session_options(settings, vad=…, turn_detector=…)` returns the `AgentSession`
  kwargs (livekit-agents 1.x `turn_handling` form, not the deprecated flat keywords) and sets
  `user_away_timeout=None`, because caller silence is `SilencePolicy`'s job. TTS is wrapped in
  `tts.StreamAdapter` with a sentence tokenizer (`MIN_SENTENCE_LEN = 20`) so Kokoro speaks each sentence as soon
  as the LLM has completed it.
- **Silence:** a pure `SilencePolicy` state machine (injected clock) driven by session
  `user_state_changed` / `agent_state_changed` events. It emits `reprompt` and `end` actions. The
  agent supplies the wording. Silence is accumulated quiet time: it counts only while the agent is not
  speaking or thinking and the caller is not speaking, and it starts when the agent finishes a turn or the
  caller stops. Caller speech resets it and the re-prompt budget; the agent's own speech (including the
  re-prompt) neither counts nor resets it, and the agent's reply to a caller turn restarts it, so
  `SILENCE_END_S` is total caller silence. `attach_silence_policy` polls every 0.25 s and calls
  `on_reprompt()` / `on_end("silence")` (sync or async; a raising callback is logged and ignored).
- **Latency:** `LatencyTracker.add(metrics)` consumes LiveKit EOU, LLM and TTS metrics and keys them by
  `speech_id` (LiveKit 1.8: STT metrics carry no `speech_id`, so the `stt` stage is the EOU metric's
  `transcription_delay`). A turn completes when its three metrics have arrived, in any order; incomplete turns
  (a tool-only turn, a barge-in before any audio) and cancelled calls are skipped, never counted as zero. Per turn:
  `e2e = eou + llm_ttft + tts_ttfb`, where `llm_ttft` is summed over the turn's model calls (a tool turn waits
  for each; tool execution time is not included) and `tts_ttfb` is the first spoken segment's. Percentiles use
  `statistics.quantiles(n=100, method="inclusive")` (one value gives p50 = p90 = that value). One structured
  `turn latency` log line per completed turn carries the figures only (no transcript text).
  `attach_latency_tracker(session, tracker)` subscribes to `metrics_collected`; the worker calls it
  (T-AGENT-13 / T-VOICE-05) and passes `summary.p50_ms`, `summary.p90_ms` and `summary.stage_latencies()` to
  `OutcomeBuilder.set_latency(LatencyFigures(...))`.

Everything LiveKit-specific sits behind thin factory functions, so unit tests never import model weights or
open sockets.

## File layout
```
src/supportagent/voice/
  __init__.py
  providers.py      # build_stt/llm/tts/vad/turn_detector, model_version
  session.py        # build_session_options(settings) -> SessionOptions
  silence.py        # SilencePolicy + attach_silence_policy
  latency.py        # LatencyTracker, LatencySummary
  event_log.py      # attach_event_log: turn-taking states + times as JSON lines, for the barge-in check (dev)
scripts/models/
  _env.sh           # sourced: settings from env / .env / defaults, port_of, abs_path
  setup.sh          # brew deps, missing GGUF/GGML downloads (sha256-checked) into ./models, Kokoro (idempotent)
  up.sh / down.sh   # start/stop native servers, pids in var/run, logs in var/log; ports already in use are left alone
  health.py         # probe each endpoint, exit 1 naming any unhealthy one (stdlib only)
scripts/agent_dev.py      # make agent-dev: preflight (servers, gateway, turn-detector weights once), LiveKit env from settings, worker dev
scripts/dev_token.py      # make dev-token: a room token for the playground that also dispatches `support-agent`
scripts/playground.sh     # make playground: LiveKit's open-source Agents Playground, pinned clone in vendor/, 127.0.0.1:3100
scripts/bench_latency.py  # local vs hosted benchmark -> docs/reports/
tests/voice/
  test_providers.py  test_session.py  test_silence.py  test_latency.py  test_health_script.py
  test_dev_token.py  test_agent_dev.py  test_event_log.py
  test_live_barge_in.py   # marker live (VOICE-R4 barge-in < 300 ms, from a session event log)
  test_live_pipeline.py   # marker live
```

## Data model / contracts
- New settings fields (added to `supportagent.common.settings` by T-VOICE-02): `STT_URL`, `TTS_URL`,
  `TTS_VOICE` (default `af_heart`), `LLM_PROVIDER` (`llamacpp|anthropic`), `LLM_MODEL`, `ANTHROPIC_MODEL` (default `claude-haiku-5-5`),
  `ANTHROPIC_API_KEY` (SecretStr), `STT_MODEL` (default `whisper-1`, the name the OpenAI client sends; whisper-server
  serves `WHISPER_MODEL` regardless), `STT_LANGUAGE` (`en`), `TTS_MODEL` (`kokoro`), `SILENCE_REPROMPT_S`, `SILENCE_END_S` (must exceed the re-prompt delay), `ENDPOINTING_MIN_DELAY_S`, `ENDPOINTING_MAX_DELAY_S`, `INTERRUPTION_MIN_S`, `SESSION_EVENT_LOG` (dev only, empty = off; `make agent-dev` sets `var/log/session_events.jsonl`), `WHISPER_MODEL` (`small.en|large-v3-turbo`, default `small.en`). `LLM_URL`, `EMBED_URL`,
  `LLM_GGUF` and `EMBED_GGUF` come from T-PLAT-07.
- `LatencySummary{turns, stages}`: `stages[name]` is `Percentiles(p50, p90)` in seconds (both `None` with no
  data) for `eou`, `stt`, `llm_ttft`, `tts_ttfb`, `e2e`. `p50_ms` / `p90_ms` are the e2e values in whole
  milliseconds, `stage_latencies()` gives `contracts.outcome.StageLatency` per stage, and `to_outcome()` returns the
  `CallOutcome` fields `latency_p50`, `latency_p90` and `latency_stages` (empty with no data).

## Key decisions
| Decision | Choice | Why |
|---|---|---|
| Plugin surface | `livekit-plugins-openai` for all three local servers | One plugin, OpenAI-compatible servers, easy swap |
| Browser client | LiveKit's open-source Agents Playground, cloned at a pinned commit into `vendor/` and run on 127.0.0.1:3100 by `make playground` | The hosted playground is being retired (2026-11-17); the open-source build has no cloud login and takes a URL plus a token. A page of our own would be a Next.js app under `web/` (constitution VIII), a possible later task |
| Streaming STT | VAD-segmented `StreamAdapter` over whisper.cpp | Whisper is not truly streaming (PRD §12) |
| Local model server | llama.cpp `llama-server` (one process for chat, one for embeddings) | Already installed, plain GGUF files reused across projects (disk budget), OpenAI-compatible; replaced Ollama on 2026-11-07 |
| Local LLM | Llama 3.1 8B Instruct Q4_K_M | Reliable tool calling via its chat template, no thinking mode to disable, the GGUF was already on disk |
| Hosted LLM | `livekit-plugins-anthropic`, `claude-haiku-5-5` | F14 benchmark; synthetic data only |
| Model files | `./models/` (git-ignored); an existing GGUF (`LLM_GGUF`/`EMBED_GGUF`) or Hugging Face cache copy is reused | Reproducible paths; not in Docker (no Metal GPU); disk budget |
| Model URLs | `127.0.0.1`, never `localhost` | macOS resolves `localhost` to `::1` first; a tunnel on `[::1]:8090` hijacked the LLM URL on the dev Mac (2026-11-09) |
| Kokoro-FastAPI | clone pinned to `KOKORO_REF` (default `v0.9.2`), started with its `start-gpu_mac.sh` and `HOST=127.0.0.1` | Upstream script handles MPS env; pinned ref keeps setup reproducible |
| Servers already running | `up.sh` leaves a port that already answers alone; `down.sh` stops only pids it wrote | Hand-started servers keep running |
| Playground dispatch | the room token carries a `RoomConfiguration` with `RoomAgentDispatch("support-agent")` | The worker registers with an explicit agent name, which turns off automatic dispatch: a plain token joins an empty room |
| Worker environment | `scripts/agent_dev.py` copies `LIVEKIT_*` from settings into the worker's environment | LiveKit's CLI reads only the process environment; the project reads `.env` through settings |
| Turn-detector weights | `download-files` once, remembered in `var/run/turn-detector.ok` | The weights cannot load in `prewarm` (T-AGENT-13) and later runs should not need the network |

## Dependencies
Runtime: `livekit-agents`, `livekit-plugins-openai`, `livekit-plugins-silero`,
`livekit-plugins-turn-detector`, `livekit-plugins-anthropic`, installed as one dependency
`livekit-agents[anthropic,openai,silero,turn-detector]~=1.2` and pinned in `uv.lock` (T-VOICE-02 checked the API names
against the installed version). `Settings.models.llm_id` is the single `provider/model` id: `providers.model_version`
and `observability.versions.model_version` both use it, and for `anthropic` it is `anthropic/<ANTHROPIC_MODEL>`. Native (brew): `llama.cpp`, `whisper-cpp`, `livekit`,
`livekit-cli`; Kokoro-FastAPI cloned to `vendor/kokoro-fastapi` (git-ignored) and started with its macOS
start script. No Docker services are added; Docker on a Mac has no GPU.

## Test strategy
- **unit:**
  - Provider factory with settings fixtures, asserting types, base URLs and model ids (no network).
  - `SilencePolicy` with a fake clock.
  - `LatencyTracker` fed synthetic metric objects.
  - `health.py` against a local `http.server` stub.
- **live:** `tests/voice/test_live_pipeline.py` needs the servers up. It sends a WAV fixture to STT,
  gets an LLM first token, gets the first TTS audio bytes, and asserts the budgets. The barge-in
  check is in the manual checklist in `tasks.md`.
- **eval:** benchmark script (T-VOICE-06) runs on demand, not in CI.

## Risks
- LiveKit plugin APIs move quickly: keep all imports in `providers.py`/`session.py` and pin versions.
- whisper large-v3-turbo may miss the 2 s budget: switch to `small.en` through settings (NFR-CAP fallback).
- Kokoro-FastAPI macOS GPU (MPS) support varies by release: CPU still meets first-byte under ~300 ms for short sentences.
