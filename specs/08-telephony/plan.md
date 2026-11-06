# 08 · Telephony — Plan

> Implements [spec.md](spec.md). Tasks in [tasks.md](tasks.md).

## Approach
Follow architecture §1, §2 and §6.
- LiveKit server runs natively (brew) from `infra/telephony/livekit.yaml`, with its Redis at
  `localhost:6379`.
- LiveKit SIP runs in Docker (`livekit/sip` image), uses the same Redis and reaches the server at
  `ws://host.docker.internal:7880`.
- Asterisk runs in Docker (an Asterisk 20/22 image) with checked-in `pjsip.conf`, `extensions.conf`
  and `rtp.conf`.

SIP and RTP over Docker networking on macOS are the known risk. We publish fixed UDP ranges, set
`external_media_address` and `external_signaling_address` to `TEL_ADVERTISED_IP` (the Mac's LAN IP, so
a phone on Wi-Fi can register too), and keep a native-Asterisk fallback (`brew install asterisk`) that
reads the same files.

Call flows:
- **Inbound:** softphone 30x dials 100. Asterisk generates `CALL_ID` (a UUID) and starts
  `MixMonitor(/var/recordings/${CALL_ID}.wav)`. It then calls `Dial(PJSIP/100@livekit)` with an
  `X-Call-Id` header. The LiveKit SIP inbound trunk (with `headers_to_attributes: {X-Call-Id: call_id}`)
  feeds a dispatch rule of type `individual` (room prefix `inbound-`) with agent dispatch
  `support-agent`.
- **Outbound:** `telephony.dial` creates room `out-{call_id}` with metadata JSON, then calls
  `CreateSIPParticipant(trunk=SIP_OUTBOUND_TRUNK_ID, sip_call_to=number, headers={"X-Call-Id": call_id},
  wait_until_answered=True)`. Asterisk's `from-livekit` context reads the header, starts MixMonitor and
  rings `PJSIP/${EXTEN}`, with a 25 s timeout. On no answer it sends the call to a voicemail
  simulation (`Playback(vm-greeting)` and `Record`), so voicemail detection can be exercised.
- **Warm transfer:** create the consult room `consult-{call_id}` and dial 200 into it. The agent briefs
  the officer there (T-AGENT-11), then `MoveParticipant` moves the officer into the call room and the
  agent leaves. Fallback: `TransferSIPParticipant` (REFER to `sip:200@asterisk`).

## File layout
```
infra/telephony/
  livekit.yaml            # native LiveKit server config (keys from env, redis, ports)
  sip.yaml                # LiveKit SIP config (mounted into the container)
  asterisk/pjsip.conf     # transports, endpoints 100/200/301-312, livekit trunk
  asterisk/extensions.conf# from-internal, from-livekit, voicemail-sim
  asterisk/rtp.conf       # rtpstart=20000 rtpend=20099
  asterisk/sounds/        # vm-greeting.wav (synthetic)
  LIVE_CHECKLIST.md       # manual checks (marker `live`)
  README.md               # topology diagram, ports, native fallback
src/supportagent/telephony/
  __init__.py  api.py (LiveKitSipApi protocol + adapter)  dial.py  results.py  transfer.py
scripts/telephony/
  provision.py            # idempotent: inbound/outbound trunks + dispatch rule, writes IDs to .env hint
  softphones.py           # Linphone account sheet from the persona seed
  render_config.py        # ${VAR} → rendered configs in var/telephony/ (secrets never committed)
tests/telephony/
  test_config_files.py  test_dial.py  test_results.py  test_transfer.py  test_provision.py  test_live_calls.py
```
`supportagent.telephony` is a new package, also listed in the `specs/README.md` module table.
Import rules: the agent and scheduler may import it; it imports only common and contracts.

## Data model / contracts
- `CallMetadata` (pydantic, defined in `contracts/call.py` by T-AGENT-01; TEL reuses it): `call_id`, `direction`, `campaign`, `customer_id | None`, `loan_id | None`; JSON-encoded into room metadata.
- `DialResult`: `answered: bool`, `contact_result: ContactResult | None`, `sip_status: int | None`, `room_name`, `detail`.
- `TransferResult`: `ok: bool`, `mode: "warm" | "cold"`, `reason: str | None`.
- SIP status → `contact_result`: 486/600 → `no_answer` (detail `busy`); 480/408/487/timeout → `no_answer`; 404/484/604 → `wrong_number`; 603 → `no_answer` (detail `declined`); answered + `detect_voicemail` → `voicemail`.

## Key decisions
| Decision | Choice | Why |
|---|---|---|
| call_id on inbound | Generated in the Asterisk dialplan, passed as `X-Call-Id` | Recording file name and room attribute share one id |
| Warm transfer | Consult room + `MoveParticipant`; REFER fallback | Officer hears the summary privately; the customer stays connected |
| Voicemail detection | Keyword heuristic on the first utterance | POC has no AMD; carrier AMD comes later (architecture §7) |
| Config templating | `render_config.py` to `var/telephony/` | Secrets stay in `.env` |
| Phone numbers | Personas use extensions 301–312 | No carrier numbers; the seed maps the persona phone to an extension |

## Dependencies
Python: `livekit-api` (`uv add livekit-api`). Docker: `livekit/sip` and an Asterisk image (pin a digest),
added to `docker-compose.yml` under the `telephony` profile with fixed UDP port publishing. Native:
`livekit-server` (from T-VOICE-01), Linphone desktop/mobile (manual install).

## Test strategy
- **unit (CI):**
  - Config validation: parse `pjsip.conf`/`extensions.conf` with `configparser` (duplicate keys allowed) and the YAML files with `yaml.safe_load`; assert extensions, ports, non-overlapping RTP ranges and the MixMonitor line.
  - Helpers against a `FakeLiveKitSipApi` that records calls and can raise `TwirpError`-style SIP failures.
  - `provision.py` idempotency against the fake.
- **live (manual):** `tests/telephony/test_live_calls.py` is skipped unless `-m live`. It places an outbound call to a softphone with auto-answer, plus the checklist in `LIVE_CHECKLIST.md`.

## Risks
- RTP over Docker on macOS: fixed ranges plus the advertised IP; fall back to native Asterisk (documented in README).
- LiveKit SIP feature names (`headers_to_attributes`, `MoveParticipant`) may differ by version: pin versions and verify in T-TEL-01; REFER is the fallback for transfers.
- Softphone NAT on a physical phone: keep it on the same Wi-Fi as the Mac; the desktop Linphone is the default.
