# 08 · Telephony — Tasks

> Each task is one GitHub issue and one PR. The field bullets are parsed by `scripts/tasks.py`, so keep
> their exact format. Status is `todo` or `done`. Issue is `—` until `make issues-sync` fills it in.
> Ids are never reused. Order tasks by dependency.

## Tasks

### T-TEL-01 · LiveKit server and LiveKit SIP with Redis
- **Status:** todo
- **Milestone:** M3
- **Issue:** #60
- **Depends on:** T-VOICE-01
- **Requirements:** TEL-R1

Check in `infra/telephony/livekit.yaml` for the native server (keys from env, Redis
`localhost:6379`, ports 7880/7881, UDP 50000–50099) and `sip.yaml` for LiveKit SIP:
- Redis address
- `ws_url: ws://host.docker.internal:7880`
- SIP port 5070
- RTP 10000–10099
- `use_external_ip: false`, with the advertised IP from `TEL_ADVERTISED_IP`

Add `redis` (already present) and `livekit-sip` to the compose `telephony` profile with fixed UDP
publishing. `scripts/telephony/render_config.py` renders the `${VAR}` templates into `var/telephony/`.
Update `scripts/models/up.sh` so that, with `TELEPHONY=1`, LiveKit runs from the config instead of `--dev`.

**Acceptance criteria**
- [ ] `docker compose --profile telephony up -d` starts redis and livekit-sip; livekit-sip logs show it connected to the server.
- [ ] Port ranges are fixed and documented in `infra/telephony/README.md` (topology plus a ports table).
- [ ] No secret values are committed; rendered configs live in git-ignored `var/telephony/`.
- [ ] Image tags are pinned.

**Tests**
- `tests/telephony/test_config_files.py::test_livekit_and_sip_yaml` (unit): the YAML parses; ports match the spec table; the LiveKit, SIP and Asterisk RTP ranges do not overlap; Redis addresses agree.
- `tests/telephony/test_config_files.py::test_render_config` (unit): templates render from a fake env, and a missing variable fails loudly.
- CI `docker` job: `docker compose --profile telephony config --quiet` passes.

**Files:** `infra/telephony/livekit.yaml`, `infra/telephony/sip.yaml`, `infra/telephony/README.md`, `scripts/telephony/render_config.py`, `docker-compose.yml`, `scripts/models/up.sh`, `.env.example`, `tests/telephony/test_config_files.py`

### T-TEL-02 · Asterisk PBX container with extensions and trunk
- **Status:** todo
- **Milestone:** M3
- **Issue:** #61
- **Depends on:** T-TEL-01
- **Requirements:** TEL-R2

Add an `asterisk` service (telephony profile; UDP 5060 and 20000–20099 published) mounting
`infra/telephony/asterisk/`.
- `pjsip.conf`:
  - UDP transport with `external_media_address` and `external_signaling_address`
  - endpoints 200 and 301–312 (auth from env, `direct_media=no`, ulaw/opus)
  - a `livekit` endpoint and identify for LiveKit SIP at `livekit-sip:5070`
- `extensions.conf`:
  - `from-internal`: 100 → `Dial(PJSIP/100@livekit)`; 200 and 3xx ring the softphones
  - `from-livekit`: dial `PJSIP/${EXTEN}`, with a 25 s timeout and then `voicemail-sim`
  - `voicemail-sim`: answer, play the synthetic greeting and a beep, `Record`, hang up

Document the native fallback (`brew install asterisk`, same files).

**Acceptance criteria**
- [ ] Two desktop softphones registered as 301 and 200 can call each other through Asterisk with two-way audio.
- [ ] Calling 100 reaches LiveKit SIP. The dispatch comes in T-TEL-03; Asterisk logs show the INVITE accepted.
- [ ] The native fallback steps in `infra/telephony/README.md` were tested once and noted in the PR.

**Tests**
- `tests/telephony/test_config_files.py::test_pjsip_endpoints` (unit): endpoints 200 and 301–312 plus the livekit trunk exist; the RTP range is 20000–20099.
- `tests/telephony/test_config_files.py::test_dialplan_routes` (unit): 100 → livekit; 3xx/200 → PJSIP; the voicemail-sim context exists.
- `tests/telephony/test_live_calls.py::test_softphone_to_softphone` (live): manual, per `LIVE_CHECKLIST.md` §1.

**Files:** `infra/telephony/asterisk/pjsip.conf`, `infra/telephony/asterisk/extensions.conf`, `infra/telephony/asterisk/rtp.conf`, `infra/telephony/asterisk/sounds/`, `docker-compose.yml`, `infra/telephony/README.md`, `tests/telephony/test_config_files.py`

### T-TEL-03 · Inbound trunk and dispatch rule for extension 100
- **Status:** todo
- **Milestone:** M3
- **Issue:** #62
- **Depends on:** T-TEL-02, T-AGENT-05
- **Requirements:** TEL-R3

`scripts/telephony/provision.py` creates, idempotently by name, the inbound trunk (accepting from
Asterisk, `headers_to_attributes: {X-Call-Id: call_id}`) and a dispatch rule (individual, room prefix
`inbound-`, agent dispatch `support-agent`). It prints the trunk ids for `.env`.

The `from-internal` context for 100 generates a UUID `CALL_ID`, adds the `X-Call-Id` header, and
calls the trunk. The worker (T-AGENT-01) reads `call_id` and `sip.phoneNumber` from the participant
attributes.

**Acceptance criteria**
- [ ] `make telephony-provision` is idempotent: a second run creates nothing.
- [ ] Live: dialling 100 from persona softphone 302 starts the agent greeting within 3 s; the room name starts with `inbound-`; the attributes include `call_id` and the caller number.
- [ ] The caller number is passed only as a hint (the agent uses `lookup_by_phone`), with no data spoken before verification.

**Tests**
- `tests/telephony/test_provision.py` (unit, fake API): the first run creates the trunk and rule with the expected fields; the second run finds them by name and creates nothing.
- `tests/telephony/test_config_files.py::test_inbound_sets_call_id_header` (unit): the 100 route sets `CALL_ID` and `PJSIP_HEADER(add,X-Call-Id)`.
- `tests/telephony/test_live_calls.py::test_inbound_greeting` (live): checklist §2.

**Files:** `scripts/telephony/provision.py`, `src/supportagent/telephony/api.py`, `infra/telephony/asterisk/extensions.conf`, `Makefile`, `tests/telephony/test_provision.py`

### T-TEL-04 · Outbound dialling, call-result detection and SIP transfer helpers
- **Status:** todo
- **Milestone:** M3
- **Issue:** #63
- **Depends on:** T-TEL-02
- **Requirements:** TEL-R4, TEL-R5, TEL-R6

Build the `supportagent.telephony` package as specified in [plan.md](plan.md#data-model--contracts):
- `api.py`: the `LiveKitSipApi` Protocol and its `livekit.api.LiveKitAPI` adapter
- `dial.py`: `dial()` creates the room with `CallMetadata` metadata, then a SIP participant with
  `wait_until_answered` and the `X-Call-Id` header. SIP errors map to a `DialResult`.
- `results.py`: `map_sip_status()` and `detect_voicemail()`
- `transfer.py`: `warm_transfer()` (consult room, dial 200, `move_participant`) and `cold_transfer()` (REFER)

`provision.py` also creates the outbound trunk.

**Acceptance criteria**
- [ ] `dial()` never raises on SIP failures; it returns a `DialResult` with `contact_result` set.
- [ ] Every SIP status in the plan's mapping table is covered; unknown codes map to `no_answer` with detail `sip_<code>`.
- [ ] `warm_transfer` gives up after `TRANSFER_TIMEOUT_S` (default 20) and cleans up the consult room.
- [ ] Live: `python -m supportagent.telephony.dial 301` rings softphone 301 and the answered call joins `out-<call_id>`.

**Tests**
- `tests/telephony/test_dial.py` (unit, fake API): the room metadata JSON round-trips to `CallMetadata`; the header is set; answered and failed paths behave as specified.
- `tests/telephony/test_results.py` (unit): parametrised SIP codes and timeout → `ContactResult`; voicemail phrases detected; normal greetings ("hello?", "this is Frank") are not.
- `tests/telephony/test_transfer.py` (unit, fake API): the warm path calls the dial and move in order; a no-answer returns `ok=False, reason="no_answer"` and deletes the consult room; the cold path calls `transfer_sip_participant` with `sip:200@…`.
- `tests/telephony/test_live_calls.py::test_outbound_and_transfer` (live): checklist §3 and §5.

**Files:** `src/supportagent/telephony/__init__.py`, `src/supportagent/telephony/api.py`, `src/supportagent/telephony/dial.py`, `src/supportagent/telephony/results.py`, `src/supportagent/telephony/transfer.py`, `scripts/telephony/provision.py`, `pyproject.toml`, `uv.lock`, `tests/telephony/test_dial.py`, `tests/telephony/test_results.py`, `tests/telephony/test_transfer.py`

### T-TEL-05 · Softphone provisioning and live-call checklist
- **Status:** todo
- **Milestone:** M3
- **Issue:** #64
- **Depends on:** T-TEL-03, T-TEL-04, T-BANK-03
- **Requirements:** TEL-R7

`scripts/telephony/softphones.py` reads the persona seed (through the bank API or the seed module's
persona table export) and prints a Linphone account sheet: extension, password, domain and display
name, plus the human officer at 200. Write `infra/telephony/LIVE_CHECKLIST.md`:
1. softphone ↔ softphone
2. inbound 100 → agent greeting
3. outbound call to Forgetful Frank (301)
4. no-answer → voicemail-sim → `voicemail`
5. Hardship Hana → warm transfer to 200
6. recording file present

Run the checklist and tick it in the PR.

**Acceptance criteria**
- [ ] Every persona with a phone in the seed has an extension in `pjsip.conf`, and the script fails if the two disagree.
- [ ] The checklist has exact steps, expected results and where to look (logs, room attributes, files).
- [ ] The checklist was executed end to end once (date and results recorded at the bottom of the file).

**Tests**
- `tests/telephony/test_softphones.py` (unit): given a persona fixture and `pjsip.conf`, the sheet lists every persona; a missing extension raises.
- `tests/telephony/test_live_calls.py` (live): one test per checklist item, each `pytest.skip`-guarded unless `-m live` and the servers are reachable.

**Files:** `scripts/telephony/softphones.py`, `infra/telephony/LIVE_CHECKLIST.md`, `tests/telephony/test_softphones.py`, `tests/telephony/test_live_calls.py`

### T-TEL-06 · Call recording with call_id mapping
- **Status:** todo
- **Milestone:** M3
- **Issue:** #65
- **Depends on:** T-TEL-02
- **Requirements:** TEL-R8

Both `from-internal` (inbound 100) and `from-livekit` (outbound) start
`MixMonitor(/var/recordings/${CALL_ID}.wav,b)` before bridging. Outbound reads `CALL_ID` from the
`X-Call-Id` header. The recordings directory is mounted at `./var/recordings`. Add
`telephony.recording_uri(call_id) -> str` (`file://…/var/recordings/{call_id}.wav`), which the agent
uses for `CallOutcome.recording_uri`.

**Acceptance criteria**
- [ ] Inbound and outbound calls each produce `{call_id}.wav` with both legs audible.
- [ ] The file name matches the `call_id` in the room attributes/metadata and in the outcome.
- [ ] `var/recordings/` is git-ignored; retention is noted as a production item (architecture §7).

**Tests**
- `tests/telephony/test_config_files.py::test_mixmonitor_on_both_paths` (unit): both contexts call MixMonitor with `${CALL_ID}` before `Dial`.
- `tests/telephony/test_recording.py` (unit): `recording_uri` format and path safety (rejects a non-UUID `call_id`).
- `tests/telephony/test_live_calls.py::test_recording_exists` (live): checklist §6.

**Files:** `infra/telephony/asterisk/extensions.conf`, `docker-compose.yml`, `src/supportagent/telephony/recording.py`, `tests/telephony/test_recording.py`, `tests/telephony/test_config_files.py`
