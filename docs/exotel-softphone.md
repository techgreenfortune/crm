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
   - Connect → "Create popup" = `https://<softphone api host>/v2/integrations/call/inbound_call/<app id>?type=popup`, and Record on.
   - "After the call conversation ends" and "If nobody answers" → Passthru to the `handle_request` URL above, each followed by a **Hangup** applet. A Passthru with nothing after it leaves the call hanging.
5. **Network**: allow outbound TCP 443 to Exotel and UDP 10000–40000 for media. Signalling can work while media is blocked, which gives connected calls with no audio.

### CRM

1. `bench --site <site> migrate` (adds `CRM Telephony Agent.exotel_sip_id` and `CRM Call Log.is_softphone_call`).
2. Scheduler enabled and a worker running.
3. **CRM Exotel Settings**: integration enabled, account SID, API key/token, a strong random `webhook_verify_token`, "Enable Browser Softphone" on, App ID and App Secret. "Softphone API Host" defaults to `integrationscore.mum1.exotel.com` (India); change it only for an account in another Exotel region. The browser SDK connects to Exotel's India VoIP domain from inside the package, so another region also needs an SDK-side change.
4. **CRM Telephony Agent** per agent, set up by a manager (roles in `role_config.TELEPHONY_AGENT_MANAGER_ROLES`: System Manager, Sales Head, Sales Coordinator — not Management, which is read-only): Mobile No, Exotel Number, "Use Exotel Browser Softphone" on, then save. Agents see only their own record and can change only their default calling medium. Saving finds or creates the agent's Integration Core user mapping (App User ID = email) and fills the read-only **Exotel SIP ID**; it refuses to save if the Exotel user has no SIP device.
5. Agents use Chrome (or Edge) and allow microphone access. Safari connects calls without audio: softphone agents on Safari are blocked from calling with "Browser calling needs Chrome or Edge".
6. **Fallback:** an agent with "Use Exotel Browser Softphone" on never falls back to click-to-call (mobile). To move an agent back to click-to-call, switch that toggle off.

## Page load

1. `get_softphone_config` (server) checks the agent is set up, reads the agent's own user mapping from Integration Core with the app token, checks its `SipId` matches the stored SIP ID, and returns only `AppID`, `AppUserId`, `SipId`, `SipSecret` (encrypted), `ExotelUserName`, `ExotelAccountSid`.
2. The browser builds the SDK's `User` and SIP settings from that, and calls `new ExotelWebPhoneSDK(null, user).Initialize(...)`.
3. The SDK registers over the SIP WebSocket; the agent sees "Exotel browser softphone is ready". From then on, any ring for this agent's SIP device arrives in the tab.

**Header badge** (softphone agents only): green "Phone ready", orange "Phone connecting", red "Phone offline" or "Use Chrome for calls". Clicking a red "Phone offline" badge reconnects.

**Staying registered:**
- The SDK itself retries a dropped WebSocket every 5 s.
- If the browser is still not registered after 15 s, a watchdog (checked every 5 s) rebuilds the registration from scratch: unregister, fetch the config again, register. Retries back off (30 s, 60 s, … up to 5 min) and reset once registered.
- It never runs during a call or while the browser reports being offline, and it runs straight away when the browser comes back online.
- Background attempts show no error toasts; the badge shows the state.
- Each reconnect gets a new SDK instance, and events from the previous instance are ignored.

**One tab per agent:** only one CRM tab per agent (per browser) registers the phone, using the browser's Web Locks API (`frontend/src/utils/exotelSoftphoneTab.js`).
- Other tabs wait with a grey "Phone in another tab" badge. Calling from them shows "Calls are running in another CRM tab" with a **Use this tab** button.
- "Use this tab" or clicking the badge moves the phone: the old tab unregisters and waits in turn.
- When the active tab closes or crashes, the browser releases the lock, and the next waiting tab registers automatically.
- Browsers without Web Locks register every tab, as before.

## Outbound call

1. **Agent clicks Call** on a Lead/Deal. `chooseOutboundRoute` decides:

   | Agent | Browser state | Result |
   |---|---|---|
   | Not known yet (page still loading the softphone config) | — | Call blocked: "Your browser phone is still connecting" |
   | Softphone off | — | Click-to-call (Exotel rings the agent's mobile) |
   | Softphone on | Registered | Browser call (the steps below) |
   | Softphone on | Not registered, or setup failed | Call blocked: "Browser softphone is not connected… The call was not placed", with a **Reconnect** button |
   | Softphone on | Safari | Call blocked: "Browser calling needs Chrome or Edge" |

   Reconnect unregisters the SDK instance, fetches the config again and registers from scratch. For a browser call, the popup opens and the dial state machine goes to *pending*.
2. **Server dial** — `make_softphone_call(phone_number, reference_doctype, reference_docname)`:
   - checks the agent, the number (≥ 10 digits), read access to the Lead/Deal, and a limit of 10 dials per agent per minute
   - `POST /call/outbound_call` with `{app_id, to, user_id}` and the app token
   - requires `Status: Success` and a `CallSid`; `FromNumber` must equal the agent's SIP ID when present (a missing one isn't treated as unknown, since reconcile verifies the SIP leg later)
   - creates the CRM Call Log (Outgoing, Ringing, `is_softphone_call`, linked to the Lead/Deal) and returns only the CallSid
   - a connect timeout or an Exotel rejection is a definite failure; any other request error, a 5xx or an unreadable reply raises `ExotelDialOutcomeUnknown` ("the call may still ring")
   - once Exotel has accepted, nothing is reported as a definite failure: a `FromNumber` mismatch raises `ExotelDialOutcomeUnknown`, and a failed call-log insert is logged while the CallSid is still returned; the Integration Core callback creates the log when its first event arrives (reconcile only repairs logs that already exist)
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
6. **Hang-up** — SIP BYE from the SDK. The SDK's `callEnded` event only resets the popup; it saves nothing. The popup's final label comes from the terminal webhook when it arrives first ("No answer" for Call Not Answered/Busy/Failed/Canceled, "Call ended" for Completed), because the SDK sees the agent leg connect whether or not the customer answered.

## Inbound call

1. The customer calls the Exophone; the flow's Connect dials Exotel users, which rings the agent's SIP device.
2. Integration Core posts the `popup` webhook (`CallStatus=busy`, `AppUserID`); the CRM creates an Incoming, Ringing log for that agent.
3. The browser gets the ring and calls `register_softphone_call(CallSid, caller, "Incoming")`. If no log exists yet, the CRM looks the call up in Exotel (3 s timeout) and uses Exotel's caller number rather than the browser's (the SIP caller ID can be wrong or forged); if Exotel doesn't have it yet, the browser's number is used and reconcile corrects it later. The CRM creates the log or, if the webhook created it, checks the agent is its receiver and flags it `is_softphone_call`. A simultaneous insert of the same CallSid (duplicate entry or MariaDB error 1020) is caught and the existing log is used. The popup shows Accept / Reject and the caller's Lead; registration returns the verified caller (`CallFrom`, from the webhook-created log or Exotel), and the popup switches to it if it differs from the SIP caller ID.
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

`reconcile_stale_call_logs` runs every 5 minutes, one run at a time. It considers Exotel logs 5 minutes to 7 days old that are still Initiated / Ringing / In Progress / Queued or have no end time (up to 50 per run), plus Completed logs from the last 2 hours without a recording (a separate budget of 10). A log that is still eligible after a run is retried after 5, 10, 20, … minutes, capped at 6 hours (backoff kept in Redis), so stuck logs can't take the whole budget. For each call:

1. Not found in Exotel → Failed, once the log is over 60 minutes old.
2. Logs flagged `is_softphone_call` must match a SIP leg of their agent (outbound: `From` = caller's SIP ID and `To` = logged number; inbound: `To` = receiver's SIP ID). Otherwise → Failed with an error log. This stops a browser from registering an arbitrary CallSid.
3. For an inbound softphone log whose caller number doesn't match Exotel's `From`, the number is corrected, the Lead is re-linked, and an error log is written.
4. A final status without `EndTime` is skipped until a later run; Exotel fills it in about 2 minutes after the call.
5. Inbound `completed` is judged by the agent leg (answered → Completed, busy → Busy, otherwise Call Not Answered).

## Security model

- The app token stays on the server; the browser gets only its own agent's mapping, looked up by the session user's email.
- Dialling is server-side, rate-limited per agent, and checked against the agent's SIP ID.
- Only managers can create or change Telephony Agent records (they decide which phone Exotel rings); agents can read their own and change only their default medium.
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
| "Browser softphone is not connected" when calling | Registration dropped or setup failed; click Reconnect. If it keeps failing, check the Error Log and the agent's Exotel SIP device |
| Registers, but calls connect with no audio | UDP media blocked by the network |
| Inbound calls ring a mobile instead of the browser | The flow's Connect dials a number, not the Exotel user; or the user's active device isn't the SIP device |
| Call logs stuck in Ringing / In Progress | Webhooks not reaching the CRM (URL, `key`, firewall); the reconcile job completes them if the scheduler runs |
| Inbound call never ends | A Passthru without a Hangup after it |
| "Phone in another tab" badge | The agent has the CRM open in another tab of the same browser, which holds the phone. Click the badge (or "Use this tab" on the call toast) to move it here |
| Two devices ring | The same agent is logged in on another browser or machine; only tabs within one browser are coordinated |

## Known limitations

- Outbound rejected and unanswered calls can't be told apart (both Call Not Answered).
- A second INVITE during a call moves the SDK's active session; after that, mute / hold / hang-up from the CRM may not reach the live call.
- Only tabs within one browser are coordinated. The same agent logged in on two browsers or machines registers twice, and both ring.
- Safari is not supported.
