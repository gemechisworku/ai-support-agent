# 08 · Telephony — Spec

> **Code:** `TEL` · **Location:** `infra/telephony/`, `src/supportagent/telephony/`, `scripts/telephony/` · **Milestones:** M3
> **PRD:** F2, F3, F9, U6, NFR-PORT, NFR-AUDIT, AC-DEMO · **Depends on:** VOICE (LiveKit server), AGENT (worker), BANK (persona phones) · **Status:** see [tasks.md](tasks.md)

## Purpose
Provides a local "phone network" so real calls can reach the agent and the agent can call out and hand
off. Asterisk plays the carrier and PBX, Linphone softphones play the customer and the human officer,
and LiveKit SIP bridges SIP calls into LiveKit rooms. This module owns the configuration, the
provisioning scripts, and the small Python helpers that other modules use to dial, classify call
results and transfer calls.

## Scope
**In:**
- LiveKit server (production-like config), LiveKit SIP and Redis
- Asterisk container: extensions, trunk to LiveKit SIP, recording
- inbound trunk and dispatch rule; outbound trunk
- dial, call-result and transfer helpers
- softphone provisioning for the personas and the human officer
- the live-call checklist; the native-Asterisk fallback

**Out:**
- when and whom to call: the compliance gate and pacing (`09-scheduler`, `05-compliance`)
- what the agent says during a transfer (`06-agent`, T-AGENT-11)
- model servers (`07-voice`)
- storing outcomes (`02-mock-bank` via `04-gateway`)

## Requirements
- **TEL-R1** (P0 · NFR-PORT, F2, F3) — `docker compose --profile telephony up` shall start Redis and LiveKit SIP. LiveKit server shall run natively from a checked-in `livekit.yaml` that uses the same Redis. SIP and RTP ports shall be fixed ranges and the advertised IP pinned, all configuration-only.
- **TEL-R2** (P0 · F2, F3, NFR-PORT) — An Asterisk container shall provide extension 100 (support line), extension 200 (human officer) and extensions 301–312 (persona softphones), and a SIP trunk to LiveKit SIP in both directions. Asterisk shall also run natively from the same config as a fallback.
- **TEL-R3** (P0 · F2) — A call to extension 100 shall create a new LiveKit room through an inbound trunk and dispatch rule. The dispatch rule shall dispatch the `support-agent` worker and pass the caller number and a generated `call_id` to the agent as participant attributes.
- **TEL-R4** (P0 · F3) — A Python helper shall place an outbound call to a number through the outbound trunk (`CreateSIPParticipant`) into a room whose metadata carries `call_id`, `campaign`, `customer_id` and `loan_id`. It shall send `call_id` to Asterisk as an `X-Call-Id` SIP header.
- **TEL-R5** (P0 · F3) — Dial failures and voicemail shall map deterministically to `contact_result`: busy → `no_answer` (with `busy` detail), no answer or timeout → `no_answer`, unknown number → `wrong_number`, voicemail greeting detected → `voicemail`.
- **TEL-R6** (P0 · F9, U6) — Helpers shall support a warm transfer to the human officer: dial extension 200 into a consult room, then move the officer into the customer's room. They shall also support a cold SIP transfer (REFER) fallback, and report failure or no-answer so the agent can create a callback ticket.
- **TEL-R7** (P0 · AC-DEMO, F2, F3) — Each persona and the human officer shall have a provisioned Linphone account whose extension matches the persona's phone number in the seed. A documented live-call checklist shall cover inbound, outbound, voicemail and warm transfer.
- **TEL-R8** (P0 · NFR-AUDIT) — Every bridged call shall be recorded by Asterisk (MixMonitor) to `var/recordings/{call_id}.wav`, so `recording_uri` in the call outcome resolves to the audio.

## Acceptance criteria
- **TEL-R1:** `docker compose --profile telephony config` is valid. In `livekit.yaml` and `sip.yaml`, the RTP ranges do not overlap the Asterisk RTP range, and the advertised IP comes from `TEL_ADVERTISED_IP`.
- **TEL-R2:** `pjsip.conf` defines endpoints 100, 200 and 301–312 plus the `livekit` trunk endpoint. In `extensions.conf`, 100 routes to the trunk and 3xx/200 ring the softphones; config parse tests pass in CI.
- **TEL-R3:** Live: dialling 100 from a persona softphone gets the agent's greeting within 3 s. The room's participant attributes include `sip.phoneNumber` and `call_id`.
- **TEL-R4:** Given a fake LiveKit API, `dial(number, meta)` creates a room with JSON metadata, then a SIP participant with `wait_until_answered=True` and the `X-Call-Id` header.
- **TEL-R5:** The mapping table is unit-tested for SIP 486, 480, 408, 404, 603 and a timeout, and for voicemail phrases ("leave a message", "not available", "after the tone").
- **TEL-R6:** Given a fake API, `warm_transfer` dials 200 into `consult-{call_id}` and moves the officer into the call room. If 200 does not answer within 20 s it returns `TransferResult(ok=False, reason="no_answer")`.
- **TEL-R7:** `scripts/telephony/softphones.py` prints a Linphone account (user, password, domain) for each persona from the seed. The checklist in `infra/telephony/LIVE_CHECKLIST.md` is executed and ticked in the PR.
- **TEL-R8:** Live: after a call, `var/recordings/{call_id}.wav` exists and plays both legs; in tests, the dialplan contains `MixMonitor(${CALL_ID}.wav)`.

## Interfaces
**Provides** (`supportagent.telephony`, importable by agent and scheduler):
- `dial(api, number: str, meta: CallMetadata, *, timeout_s=30) -> DialResult` (`DialResult.contact_result`, `sip_status`, `room_name`)
- `map_sip_status(code: int | None, timed_out: bool) -> ContactResult`; `detect_voicemail(first_utterance: str) -> bool`
- `warm_transfer(api, call_room, call_id, *, officer=HUMAN_AGENT_EXTENSION) -> TransferResult`; `cold_transfer(api, call_room, participant_identity, to_uri) -> TransferResult`
- `LiveKitSipApi` Protocol: create_room, create_sip_participant, transfer_sip_participant, move_participant, remove_participant (production: `livekit.api.LiveKitAPI`; tests: a fake)
- Config: `infra/telephony/{livekit.yaml,sip.yaml,asterisk/pjsip.conf,asterisk/extensions.conf,asterisk/rtp.conf}`
- Scripts: `scripts/telephony/provision.py` (idempotent: trunks + dispatch rule), `scripts/telephony/softphones.py`
- Settings: `SIP_OUTBOUND_TRUNK_ID`, `SIP_INBOUND_TRUNK_ID`, `HUMAN_AGENT_EXTENSION=200`, `SUPPORT_EXTENSION=100`, `TEL_ADVERTISED_IP`, `ASTERISK_HOST`.

**Consumes:**
- `ContactResult` from `contracts/outcome.py` (PLAT)
- persona phone numbers in the bank seed (T-BANK-03): persona phones are `301`…`312` in a dialable form
- the LiveKit server binary from T-VOICE-01
- the worker name `support-agent` (T-AGENT-01)

## Non-functional
- NFR-PORT: replacing Asterisk with a carrier trunk changes `sip.yaml` / trunk provisioning only; helpers are carrier-agnostic.
- Ports (fixed):

  | Service | SIP | RTP / media |
  |---|---|---|
  | Asterisk | 5060/udp | 20000–20099 |
  | LiveKit SIP | 5070/udp | 10000–10099 |
  | LiveKit server | — | 7880 (HTTP/WS), 7881 (TCP), 50000–50099/udp |

- NFR-CAP: one or two concurrent calls; the RTP ranges are sized for roughly 10.
- Secrets (SIP passwords, LiveKit keys) come from `.env`; the checked-in configs use `${VAR}` templating rendered by `provision.py`.

## Open questions
- Whether a voicemail should leave a neutral message (FDCPA limited-content message) or hang up. POC default: hang up and record `voicemail`, decided in T-AGENT-09/10.
