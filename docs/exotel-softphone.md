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

A runbook for a new environment (local, UAT, production). Follow the steps in order: each one needs values from the one before. Every environment gets **its own** Integration Core app, webhook key and call flow, because an app sends all its webhooks to one CRM.

Keep every value below (secrets, keys, App ID) out of Git, chat and tickets. Hold them in a local, git-ignored env file or a password manager.

### Before you start

| Need | From |
|---|---|
| Exotel account SID, API key, API token | Exotel dashboard → API settings (the same ones classic click-to-call uses) |
| Integration Core **customer ID and secret** | Issued by Exotel for the account; required to create an app |
| Each softphone agent as an Exotel dashboard user with a **SIP device**, same email as their CRM user | Exotel administrator |
| The CRM deployed at `https://<site>` | Deployment |

Integration Core base URL (India): `https://integrationscore.mum1.exotel.com/v2/integrations`. The examples use:

```bash
IC=https://integrationscore.mum1.exotel.com/v2/integrations
```

Tokens go in the `Authorization` header as the raw token (no `Bearer`).

### 1. Generate the webhook key

```bash
openssl rand -hex 24
```

This key goes in three places: CRM Exotel Settings (step 3), the app's webhook URLs (step 2c) and the flow's Passthru URLs (step 5). Store it on its own line: appending with `echo … >> file` to a file with no trailing newline glues it onto the previous line.

### 2. Create the Integration Core app (Exotel side, API only)

The app is not visible anywhere in the Exotel dashboard; it exists only through this API. It holds the App ID/Secret, the agent SIP mappings and the webhook URLs.

a. Customer token:

```bash
curl -s -X POST "$IC/token" -H 'Content-Type: application/json' \
  -d '{"Id":"<customer id>","Secret":"<customer secret>","Entity":"customer"}'
# → Data = customer token
```

b. Create the app. Use a name that says which environment it serves (e.g. "CRM Softphone UAT"); it is only a label.

```bash
curl -s -X POST "$IC/app" -H 'Content-Type: application/json' -H "Authorization: <customer token>" \
  -d '{"AppName":"CRM Softphone <env>","ExotelAccountSid":"<account sid>","ExotelApiKey":"<api key>",
       "ExotelApiToken":"<api token>","ExotelDomain":"mumbai","IsActive":true}'
# → Data.AppID and Data.AppSecret: save both now
```

The app stores the account API key and token given here and uses them to place calls. If the account API token is ever rotated, check with Exotel whether each app needs updating.

List apps later with `GET $IC/app?entity=customer` (customer token).

c. App settings, with an **app** token (`POST $IC/token` with `"Entity":"app"`, AppID and AppSecret):

```bash
URL="https://<site>/api/method/crm.integrations.exotel.handler.handle_request?key=<webhook key>"
for KEY in callback popup missedCall incomingCallHangup; do
  curl -s -X POST "$IC/app_setting" -H 'Content-Type: application/json' -H "Authorization: <app token>" \
    -d "{\"Key\":\"$KEY\",\"Value\":\"$URL\"}"
done
curl -s -X POST "$IC/app_setting" -H 'Content-Type: application/json' -H "Authorization: <app token>" \
  -d '{"Key":"record","Value":"true"}'
```

| Setting | What Exotel sends there |
|---|---|
| `callback` | Outbound browser call events (answered, terminal status, recording) |
| `popup` | Inbound call is ringing an agent (`busy`) |
| `missedCall`, `incomingCallHangup` | Inbound call ended (`free`) |
| `record` | Record softphone calls |

Posting a key again replaces its value. At least one setting must exist or the SDK can't initialise. Check with `GET $IC/app_setting`, and mask the `key=` part before pasting the output anywhere.

d. **Agent mappings** are created by the CRM when an agent is saved (step 4), and only for existing Exotel users.

   **Why the guard:** if no Exotel coworker exists with that email, `POST /usermapping` does not fail: it **creates a new coworker** (a possibly billable seat). So before mapping, the CRM looks the email up in the account's users (`ccm-api.<region>/v2/accounts/<sid>/users`, read-only). With no such user, or a user without a SIP device, it refuses to save and creates nothing. On every save, for a new or an existing mapping, the mapping's `SipId` must be one of that user's own SIP devices; a rejected mapping stays in Exotel, so it is rejected again until fixed there.

   The mapping uses the user's phone device from Exotel (0-prefixed, 11 digits) as `AgentNumber` and the agent's Exotel Number as `VirtualNumber`. A user without a phone device is not mapped.

   Only enable test users on UAT/test apps whose SIP devices no live flow rings: while a browser is registered on a UAT app as that device, live calls to it ring in UAT.

### 3. CRM Exotel Settings

As System Manager: **Settings → Telephony → Exotel**.

| Field | Value |
|---|---|
| Enabled | on |
| Account SID, API Key, API Token | from "Before you start" |
| Webhook Verify Token | the key from step 1 |
| Subdomain | the classic API host, e.g. `api.in.exotel.com` for India |
| Record Calls | as needed (click-to-call) |
| Browser Softphone | on (global switch; agents still need their own toggle) |
| Softphone App ID, Softphone App Secret | from step 2b |
| Softphone API Host | leave empty for India; set only for an account in another Exotel region |

The browser SDK connects to Exotel's India VoIP domain from inside the package, so another region also needs an SDK-side change.

### 4. CRM Telephony Agents

A manager (roles in `role_config.TELEPHONY_AGENT_MANAGER_ROLES`: System Manager, Sales Head, Sales Coordinator — not Management, which is read-only) first makes sure the agent exists as an Exotel dashboard user with a SIP device, under the same email as their CRM user. Adding users there is a deliberate, possibly billable, decision the CRM never makes.

Then the manager creates a record per agent in Desk at `/app/crm-telephony-agent/new`: User, Mobile No, Exotel Number, **Use Exotel Browser Softphone** on, then save. (The CRM's **Settings → Telephony** page edits only the signed-in user's own record.) Every save of an agent with the softphone on reads the agent's mapping in the current app, creating it for an existing Exotel user if missing (step 2d), and fills the read-only **Exotel SIP ID**. A failed read stops the save; it is never treated as a missing mapping. It refuses to save when the Exotel user or their SIP device is missing. Agents see only their own record and can change only their default calling medium.

Agents on click-to-call need none of this: it only rings their mobile.

### 5. Call flow (Exotel dashboard, inbound)

Inbound calls always run the flow attached to the Exophone; the Integration Core app does not replace it.

- **Connect** → Exotel **users or groups**, not phone numbers. Check that an empty "numbers" option is not the selected one; it still dials a default number.
- Connect → **Create popup** = `https://<softphone api host>/v2/integrations/call/inbound_call/<AppID>?type=popup`, and Record on.
- **After the call conversation ends** and **If nobody answers** → Passthru to `https://<site>/api/method/crm.integrations.exotel.handler.handle_request?key=<webhook key>`, each followed by a **Hangup** applet. A Passthru with nothing after it leaves the call hanging.
- Keep any other Passthrus the production flow already has (other systems may depend on them), and remove them from test copies of the flow, or test calls are posted to those systems.
- Test environments use a separate test flow. Never move the production Exophone to a test flow; to test inbound, start a call into the test flow through the Calls API (`Calls/connect` with `Url=http://my.exotel.com/<account sid>/exoml/start_voice/<flow id>`).

Anyone with dashboard access can edit or delete flows, so give dashboard access per person and keep a written copy of the production flow's steps.

### 6. Server and network

1. `bench --site <site> migrate` (adds `CRM Telephony Agent.exotel_sip_id`, `CRM Call Log.is_softphone_call`, `CRM Exotel Settings.softphone_api_host`).
2. Scheduler enabled and a worker running (reconcile job).
3. Agents' networks allow outbound TCP 443 to Exotel and UDP 10000–40000 for media. Signalling can work while media is blocked, which gives connected calls with no audio.
4. Agents use Chrome or Edge and allow microphone access. Safari connects calls without audio, so softphone agents on Safari are blocked with "Browser calling needs Chrome or Edge".

### 7. Verify

1. `curl -s -X POST "https://<site>/api/method/crm.integrations.exotel.handler.handle_request"` without a key → 403 (the webhook refuses unkeyed requests).
2. An agent opens the CRM: the header badge shows **Phone ready**.
3. Outbound: answered, not answered, rejected → Call Log `Completed` (with recording), `Call Not Answered`, `Call Not Answered`.
4. Inbound through the flow: answered, missed, rejected → `Completed`, `Call Not Answered`, `Busy`.
5. Error Log has no "Exotel softphone" entries.

### Changing things later

| Change | Do |
|---|---|
| Rotate the webhook key | Update CRM Exotel Settings, all four app settings (step 2c) and the flow Passthrus together; webhooks with the old key are refused |
| Point an app at another CRM URL | Re-post the four app settings |
| Replace the Integration Core app | Put the new App ID/Secret in CRM Exotel Settings, then save each softphone agent again; every save re-checks the agent's mapping in the current app and creates it if missing |
| Move an agent back to click-to-call | Turn off their **Use Exotel Browser Softphone**; softphone agents never fall back to the mobile on their own |
| Turn the softphone off for everyone | Turn off **Browser Softphone** in CRM Exotel Settings |

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
   | `terminal` / `terminal` | `from_leg_cancelled` (agent hung up while the customer rang) | Canceled |

   Exotel reports a customer rejection as `to_leg_unanswered` as well, so outbound rejected and unanswered calls are both Call Not Answered.
6. **Hang-up** — SIP BYE from the SDK. The SDK's `callEnded` event only resets the popup; it saves nothing. The popup's final label comes from the terminal webhook when it arrives first ("No answer" for Call Not Answered/Busy/Failed, "Call canceled" for Canceled, "Call ended" for Completed), because the SDK sees the agent leg connect whether or not the customer answered.

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
| "No Exotel user exists for …" on agent save | No Exotel dashboard user has that email; create one with a SIP device first |
| "Exotel mapped … to a SIP device that is not theirs" | Exotel created or picked another device; check the user in the dashboard (Error Log has both SIP IDs) |
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
