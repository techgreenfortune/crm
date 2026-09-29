# Exotel browser softphone

Agents place and receive Exotel calls in the CRM tab (Chrome), using Exotel's WebRTC SDK. Call outcomes are written to CRM Call Log from Exotel webhooks, with a scheduled job as a fallback.

## Components

```
 Agent's browser (Vue CRM + Exotel SDK)
   │  (B) SIP over WSS: register, ring, answer, hang up ─────────▶ Exotel SIP server
   │  (C) WebRTC audio (UDP) ─────────────────────────────────────▶ Exotel media servers
   │  (F) CRM API + socket.io
   ▼
 CRM server (Frappe + worker)
   │  (A) app-token calls: user mapping, outbound dial ───────────▶ Exotel Integration Core
   │  (G) Calls API: reconcile ───────────────────────────────────▶ Exotel classic API
   ▲
   │  (D) Integration Core webhooks: callback, popup, free
   │  (E) Call flow Passthru: inbound outcome
 Exotel
```

| Link | Purpose | Credential |
|---|---|---|
| A | Read the agent's user mapping; place outbound calls | Integration Core app token — **server only**, cached for 1 hour |
| B | Register the browser as the agent's SIP device; call control | The agent's own SIP username and secret |
| C | Call audio | Negotiated per call |
| D, E | Call events and outcomes to `handle_request` | `?key=<webhook_verify_token>` |
| F | CRM requests; `exotel_call` real-time events to the popup | CRM session |
| G | Complete call logs whose webhooks never arrived; verify softphone calls | Account API key/token |

The browser never receives the app token and never calls Integration Core. The only Exotel credential it holds is its own SIP login, which any browser phone needs.

## Code map

| Piece | Where |
|---|---|
| Call popup, dialling, SDK event handling | `frontend/src/components/Telephony/ExotelCallUI.vue` |
| SDK wrapper (init, accept, hang up, mute, hold, unregister) | `frontend/src/utils/exotelSoftphone.js` |
| CallSid extraction, outbound dial state machine | `frontend/src/utils/exotelSoftphoneCall.js` |
| Config, dial, call registration, webhooks, reconcile | `crm/integrations/exotel/handler.py` |
| Exotel user mapping on agent save | `crm/fcrm/doctype/crm_telephony_agent/crm_telephony_agent.py` |
| Reconcile schedule | `crm/hooks.py` (every 5 minutes) |
| Tests | `crm/tests/test_exotel_softphone.py`, `frontend/tests/unit/exotelSoftphone.test.js`, `frontend/tests/unit/exotelOutboundDial.test.js` |

## Setup

### Exotel

1. **Integration Core app** (one per environment — an app has a single set of webhook URLs). Create it with the customer token and keep the App ID and Secret out of source control.
2. **App settings** on that app:
   - `callback`, `popup`, `missedCall`, `incomingCallHangup` → `https://<site>/api/method/crm.integrations.exotel.handler.handle_request?key=<webhook_verify_token>`
   - `record` → `true`
   - At least one setting must exist or the SDK can't initialise.
3. **Exotel users**: each agent needs a dashboard user with a SIP device and the same email as their CRM user.
4. **Call flow** attached to the Exophone:
   - Connect → Exotel **users or groups**, not phone numbers. Check that an empty "numbers" option is not the selected one; it still dials a default number.
   - Connect → "Create popup" = `https://integrationscore.mum1.exotel.com/v2/integrations/call/inbound_call/<app id>?type=popup`, and Record on.
   - "After the call conversation ends" and "If nobody answers" → Passthru to the `handle_request` URL above, each followed by a **Hangup** applet. A Passthru with nothing after it leaves the call hanging.
5. **Network**: allow outbound TCP 443 to Exotel and UDP 10000–40000 for media. Signalling can work while media is blocked, which gives connected calls with no audio.

### CRM

1. `bench --site <site> migrate` (adds `CRM Telephony Agent.exotel_sip_id` and `CRM Call Log.is_softphone_call`).
2. Scheduler enabled and a worker running.
3. **CRM Exotel Settings**: integration enabled, account SID, API key/token, a strong random `webhook_verify_token`, "Enable Browser Softphone" on, App ID and App Secret.
4. **CRM Telephony Agent** per agent: Mobile No, Exotel Number, "Use Exotel Browser Softphone" on, then save. Saving finds or creates the agent's Integration Core user mapping (App User ID = email) and fills the read-only **Exotel SIP ID**; it refuses to save if the Exotel user has no SIP device.
5. Agents use Chrome (or Edge) and allow microphone access. Safari connects calls without audio and is not supported.

## Page load

1. `get_softphone_config` (server) checks the agent is set up, reads the agent's own user mapping from Integration Core with the app token, checks its `SipId` matches the stored SIP ID, and returns only `AppID`, `AppUserId`, `SipId`, `SipSecret` (encrypted), `ExotelUserName`, `ExotelAccountSid`.
2. The browser builds the SDK's `User` and SIP settings from that, and calls `new ExotelWebPhoneSDK(null, user).Initialize(...)`.
3. The SDK registers over the SIP WebSocket; the agent sees "Exotel browser softphone is ready". From then on, any ring for this agent's SIP device arrives in the tab.

## Outbound call

1. **Agent clicks Call** on a Lead/Deal. The popup opens, and the dial state machine goes to *pending*.
2. **Server dial** — `make_softphone_call(phone_number, reference_doctype, reference_docname)`:
   - checks the agent, the number (≥ 10 digits), read access to the Lead/Deal, and a limit of 10 dials per agent per minute
   - `POST /call/outbound_call` with `{app_id, to, user_id}` and the app token
   - requires `Status: Success`, a `CallSid`, and `FromNumber` equal to the agent's SIP ID
   - creates the CRM Call Log (Outgoing, Ringing, `is_softphone_call`, linked to the Lead/Deal) and returns only the CallSid
   - a connect timeout or an Exotel rejection is a definite failure; any other request error, a 5xx or an unreadable reply raises `ExotelDialOutcomeUnknown` ("the call may still ring")
3. **Agent leg** — Exotel rings the agent's SIP device first, with the same CallSid:

   | Situation | Action |
   |---|---|
   | Ring after the dial reply, same CallSid | Auto-accept |
   | Ring before the dial reply | Held (30 s), accepted when the matching CallSid arrives |
   | Different CallSid during the dial or the call | Rejected, SIP 486 Busy |
   | Same ring again after accepting | Ignored |
   | Dial failed for certain, a ring was held | Handled as a real inbound call |
   | Dial outcome unknown | Held ring rejected; rings in the next 30 s rejected |
   | Our ring never arrives within 30 s of the reply | Stop holding the line |

4. **Customer leg** — Exotel dials the customer; audio flows browser ⇄ WebRTC ⇄ Exotel ⇄ phone network.
5. **Outcome** — Integration Core `callback` webhooks:

   | `CallDetail` / `CallState` | `CallStatus` | CRM status |
   |---|---|---|
   | `answered` / `active` | — | In Progress |
   | `terminal` / `terminal` | `completed` | Completed, with duration and recording |
   | `terminal` / `terminal` | `to_leg_unanswered` | Call Not Answered |

   Exotel reports a customer rejection as `to_leg_unanswered` as well, so outbound rejected and unanswered calls are both Call Not Answered.
6. **Hang-up** — SIP BYE from the SDK. The SDK's `callEnded` event only resets the popup; it saves nothing.

## Inbound call

1. The customer calls the Exophone; the flow's Connect dials Exotel users, which rings the agent's SIP device.
2. Integration Core posts the `popup` webhook (`CallStatus=busy`, `AppUserID`); the CRM creates an Incoming, Ringing log for that agent.
3. The browser gets the ring and calls `register_softphone_call(CallSid, caller, "Incoming")`. The CRM creates the log or, if the webhook created it, checks the agent is its receiver and flags it `is_softphone_call`. A simultaneous insert of the same CallSid (duplicate entry or MariaDB error 1020) is caught and the existing log is used. The popup shows Accept / Reject and the caller's Lead.
4. Accept → audio. Reject → SIP 486.
5. Outcome:
   - Integration Core posts `free` (agent released). It is identical for every outcome, so the CRM only queues a reconcile.
   - The flow Passthru posts the classic payload:

   | Outcome | `CallType` | `DialCallStatus` | Other | CRM status |
   |---|---|---|---|---|
   | Answered | `completed` | `completed` | `Legs[0][OnCallDuration]` = talk time, `RecordingUrl` | Completed |
   | Missed | `incomplete` | `no-answer` | | Call Not Answered |
   | Rejected | `incomplete` | `busy` | `Legs[0][CauseCode]=USER_BUSY` | Busy (UI: "Declined") |

   `DialCallDuration` includes ring time and is not used for talk time. The Passthru `EndTime` is a placeholder and is ignored.

## Webhook handling rules

- Every webhook is stored as an Integration Request.
- `AppUserID` is accepted only for a CRM user with the softphone enabled.
- Unknown status words are ignored and left for the reconcile job.
- Once a log is terminal (Completed, Busy, Call Not Answered, Failed, Canceled), later webhooks can only fill empty fields; a late "answered" can't undo "completed".
- Each update is pushed to the agent's popup as a socket.io `exotel_call` event.

## Reconcile job

`reconcile_stale_call_logs` runs every 5 minutes, one run at a time, with a budget of 50 Calls API requests per run. It picks Exotel logs 5 minutes to 7 days old that are still Initiated / Ringing / In Progress / Queued or have no end time, plus Completed logs from the last 2 hours without a recording. For each call:

1. Not found in Exotel → Failed, once the log is over 60 minutes old.
2. Logs flagged `is_softphone_call` must match a SIP leg of their agent (outbound: `From` = caller's SIP ID and `To` = logged number; inbound: `To` = receiver's SIP ID). Otherwise → Failed with an error log. This stops a browser from registering an arbitrary CallSid.
3. A final status without `EndTime` is skipped until a later run; Exotel fills it in about 2 minutes after the call.
4. Inbound `completed` is judged by the agent leg (answered → Completed, busy → Busy, otherwise Call Not Answered).

## Security model

- The app token stays on the server; the browser gets only its own agent's mapping, looked up by the session user's email.
- Dialling is server-side, rate-limited per agent, and checked against the agent's SIP ID.
- Webhooks are authenticated only by the shared `key` query parameter (Exotel sends no signature), so use a long random value and treat Integration Request records as sensitive.
- The agent's SIP secret is in their own browser; the SDK decrypts it with a key shipped in the SDK. This is inherent to any browser phone.

## Exotel SDK versions and upgrades

The CRM uses the SDK's lower-level `ExotelWebPhoneSDK` and `User` exports instead of `ExotelCRMWebSDK`, and re-creates two private parts of `ExotelCRMWebSDK` so the token can stay on the server.

| Package | Version | Pinned by |
|---|---|---|
| `@exotel-npm-dev/exotel-ip-calling-crm-websdk` | 1.2.2 | exact version in `frontend/package.json` |
| `@exotel-npm-dev/webrtc-client-sdk` | 2.0.5 | `resolutions` + `yarn.lock` |
| `@exotel-npm-dev/webrtc-core-sdk` | 1.0.24 | `resolutions` + `yarn.lock` |

What depends on SDK internals:

| SDK part | Our copy |
|---|---|
| `ExotelCRMWebSDK` private `getSIPInfo` | `buildExotelSipInfo` in `exotelSoftphone.js` (keeps the SDK's doubled `:443`) |
| `ExotelWebPhoneSDK.MakeCall` → `/call/outbound_call` | `make_softphone_call` in `handler.py` |
| `ExotelCRMWebSDK` `/usermapping` lookup | `get_softphone_config` in `handler.py` |
| `User` field names and SIP-secret decryption | fields returned by `get_softphone_config` |
| SDK events and control methods | `ExotelCallUI.vue`, `exotelSoftphone.js` |

To upgrade:

1. Diff the new version's `output/` folder against the pinned one, focusing on the parts above.
2. Update our copies in the same change if those parts moved.
3. Bump the version (and the `resolutions` if needed), then `cd frontend && yarn install`.
4. Run `yarn test:run` (includes the SDK contract tests), `yarn build`, and `bench --site <site> run-tests --app crm --module crm.tests.test_exotel_softphone`.
5. Before merging, make real calls in Chrome: registration, one outbound and one inbound answered call, and confirm the browser makes no requests to Integration Core.

Pinning can't protect against Exotel changing its servers (API fields, the SIP-secret key, the WebSocket host); those also break Exotel's own SDK. Watch for "Exotel returned incomplete softphone details" in the Error Log or agents failing to register.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| "Save your Telephony Agent again…" on load | The agent has no stored SIP ID, or the Exotel mapping's SIP ID changed |
| Registers, but calls connect with no audio | Safari, or UDP media blocked by the network |
| Inbound calls ring a mobile instead of the browser | The flow's Connect dials a number, not the Exotel user; or the user's active device isn't the SIP device |
| Call logs stuck in Ringing / In Progress | Webhooks not reaching the CRM (URL, `key`, firewall); the reconcile job completes them if the scheduler runs |
| Inbound call never ends | A Passthru without a Hangup after it |
| Both tabs ring | The same agent has the CRM open in two tabs; each tab registers the SIP device |

## Known limitations

- Outbound rejected and unanswered calls can't be told apart (both Call Not Answered).
- A second INVITE during a call moves the SDK's active session; after that, mute / hold / hang-up from the CRM may not reach the live call.
- Two tabs for the same agent both register and both ring.
- Safari is not supported.
