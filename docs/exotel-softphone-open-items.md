# Exotel Softphone — Open Items

As of 2026-09-29. Background: [exotel-softphone-feasibility.md](exotel-softphone-feasibility.md) (what was proven) and [exotel-softphone-persistence-plan.md](exotel-softphone-persistence-plan.md) (the implementation plan and its progress).

The browser softphone works end to end on the local CRM: outbound and inbound calls, audio, mute/hold, and call logs for answered, missed and rejected calls. The items below are what remains before a production pilot, grouped by who has to act.

## Reference values

| Thing | Value |
| --- | --- |
| Exotel account SID | `thegreenfortune1m` |
| Exophone (only one on the account) | `04069326588` |
| Production flow (App Bazaar) | `110314` — must stay attached to the Exophone |
| Test flow (copy) | `110532` — never leave it attached to the Exophone |
| Softphone app (Integration Core) | `e8132c55-90a0-491e-8c84-212713060d07` ("CRM Softphone Spike 2") |
| Test agent | `cx@indiframe.com`, SIP `cxuseri6957fa8e` |
| Spike tooling | `~/projects/exotel-websdk-spike/spike/provision.py` (reads `.env`, never commit it). `connect-flow` places an `outbound-api` call that runs a flow — useful for SIP routing checks, not a real inbound test |

## 1. Blockers — decide go/no-go

### 1.1 App-level token in the browser — resolved in code, needs a real-call check

- **What was wrong:** the browser got the app-level Integration Core token. It is valid for 90 days for the whole app: anyone could copy it from devtools and read every agent's mapping (including SIP secrets), place calls as another user, or delete the app.
- **Fix (2026-09-29):** the SDK only uses the token for `/app`, `/app_setting`, `/usermapping` and `/call/outbound_call`. The CRM now makes those calls on the server. The browser gets only the agent's own mapping and uses the SDK's exported `ExotelWebPhoneSDK` and `User` classes. The dial goes through `make_softphone_call`. No fork, and no per-user tokens from Exotel needed. Details: [exotel-softphone-server-token-plan.md](exotel-softphone-server-token-plan.md).
- **Still exposed, by design:** each agent's own SIP username and secret in their own browser. Any WebRTC softphone needs them to register.
- **Left to do:** the outbound INVITE race (Phase 4 of that plan) and a real Chrome regression run (outbound, inbound, controls, logs).
- **Owner:** us. Scoped tokens from Exotel are now nice to have, not a blocker.

## 2. Code still to do

### 2.1 Verify browser-registered calls against Exotel (Phase D) — done

- Logs created by `register_softphone_call` carry a read-only "Softphone Call" flag (`is_softphone_call` on CRM Call Log); registration also requires the agent's stored SIP ID. If a webhook created the log first, registering it by its own caller/receiver sets the same flag (logs without a handler are not flagged).
- The reconcile job verifies every flagged log against Exotel's call record: outbound must come from the caller's SIP ID and reach the logged number (last 10 digits); inbound must ring the receiver's SIP ID. A flagged log whose call has no matching SIP leg — e.g. a spoofed `CallSid` of an unrelated click-to-call — or whose agent has no SIP ID is marked Failed with an error log. Server-created logs (webhooks, click-to-call) are trusted. Calls the Calls API never finds are marked Failed after 60 minutes.

### 2.2 Lead linking on call logs (Phase D) — done

- Linking already worked: calls link to the caller's Lead through the call log's `links` table, and the Lead timeline (`get_linked_calls` in `crm/api/activities.py`) reads both that table and `reference_docname`. Verified with a real Lead number in four formats (`9…`, `09…`, `+91…`, `91…`).
- The empty `reference_doctype = CRM Lead` was the DocType default; it is now cleared when no Lead/Deal matches.

### 2.3 Open the caller's Lead/Deal on Accept (Phase F, proposed)

- **What:** the popup already finds the caller's Lead/Deal and shows a button; this would open it automatically.
- **Proposal:** open on Accept (not on ring), same tab, Deal before Lead, "New Lead" button for unknown numbers. Details and constraints in the plan doc.
- **Blocked on:** product sign-off (see 5.2).
- **Effort:** ≈ 0.5 d.

### 2.4 Production bundle check — done

- Production `yarn build` passed with `build.commonjsOptions.transformMixedEsModules` in `frontend/vite.config.js` (reported in review, 2026-09-29).

### 2.5 Verify the reconcile job on real data

- **What:** `reconcile_stale_call_logs` (every 5 minutes via `crm/hooks.py`) completes call logs whose webhooks never arrived, using the Calls API.
- **Why:** it is unit-tested only. The local site has the scheduler disabled, so it has never run against real Exotel data.
- **Next step:** watch it on staging/production after deploy. Logs stuck in `Ringing` / `In Progress` for > 5 min should complete, terminal logs missing `end_time` (answered calls finished by the Passthru) should gain it, and `Completed` logs without a recording should pick one up within 2 hours if recording is enabled. Exotel fills `EndTime`/duration ~2 min after a call ends, so the job skips records without `EndTime` until a later run.

### 2.6 Commit scope

- **What:** nothing is committed. The softphone work is staged, with further unstaged edits on top.
- **Decision needed:** whether `docs/exotel-softphone-*.md` go into the commit. The senior dev wants the feasibility doc committed (it holds the verified evidence); earlier the intent was to keep docs local.

## 3. Production configuration at rollout

All of these are Exotel dashboard / CRM settings, not code. Change the production flow only outside business hours, and note its ID (`110314`) first.

### 3.1 Production flow Passthrus

- **What:** in flow `110314`'s Connect applet:
  - "After the call conversation ends" → add a Passthru to the production CRM (`/api/method/crm.integrations.exotel.handler.handle_request?key=<strong key>`), then **Hangup**.
  - "If nobody answers" → Passthru to the same URL, then **Hangup**. ("If we didn't dial anyone" falls back to this branch.)
- **Why:** these give the CRM the call outcome the moment the call ends (tested 2026-09-29). Missed = `CallType=incomplete` + `DialCallStatus=no-answer`; rejected = `incomplete` + `busy` (`USER_BUSY`); answered = `completed` + talk time in `Legs[0][OnCallDuration]` + `RecordingUrl`. Without them the reconcile job still completes logs, but only after ~5 minutes.
- **Gotchas:**
  - A Passthru with nothing after it leaves the call hanging indefinitely — always add Hangup.
  - The existing "conversation ends" Passthru posts to **OpsGate** and must stay. Check whether Exotel allows two Passthrus in sequence, or have OpsGate forward to the CRM (see 4.2).

### 3.2 Connect applet dials users, not numbers

- **What:** the Connect step must reference Exotel **users** (or groups of users), not raw phone numbers.
- **Why:** a user entry rings that user's active device, so the per-agent browser/mobile choice works. A raw number always rings the mobile. In the flow editor, an empty "numbers" option still dials a default number if its green dot is selected — make sure it is off.

### 3.3 Integration Core popup URL and recording on the flow

- **What:** in the Connect applet, set "Create popup" to `https://integrationscore.mum1.exotel.com/v2/integrations/call/inbound_call/<app id>?type=popup`, and turn Record on.
- **Why:** without the popup URL, Exotel never notifies the app about inbound calls. Flow calls use the flow's own Record setting, not the app's `record` setting.

### 3.4 Production app settings

- **What:** on the production app: `callback`, `popup`, `missedCall`, `incomingCallHangup` → production CRM `handle_request` URL; `record = true`.
- **Why:** `callback` drives outbound softphone call logs; `popup` etc. drive inbound Ringing state.
- **Security:** use a strong random `webhook_verify_token` in CRM Exotel Settings. The local test used a placeholder; the key is stored in every Integration Request.

### 3.5 Per-agent setup

- **What:** for each pilot agent:
  1. Exotel dashboard user with a SIP device, email = CRM email.
  2. CRM Telephony Agent: Mobile No and Exotel Number set, then "Use Exotel Browser Softphone" on. Saving creates or finds the Integration Core user mapping (App User ID = email) and fills the read-only "Exotel SIP ID" field; it refuses to save if the Exotel user has no SIP device.
  3. CRM Exotel Settings: "Enable Browser Softphone" on, App ID/Secret filled (needed before step 2).
- **Why:** the SDK looks the agent up by email; a user without a SIP device has nothing to register as.

### 3.6 Network

- **What:** office/VPN firewalls must allow TCP 443, UDP 10000–40000, and Exotel's media IPs (see the feasibility doc).
- **Why:** signalling over 443 can work while media is blocked, which gives connected calls with no audio.

### 3.7 Browser

- **What:** agents already use Chrome for the CRM, which is the browser the softphone was tested on (two-way audio, 2026-09-24 onwards). Edge is Chromium-based and listed by Exotel as supported, but untested here.
- **Why it still matters:** in Safari calls connect with no audio either way. During the pilot the CRM detects Safari and routes Exotel calls to the agent's mobile with a message; at go-live (no mobile fallback, section 6) Safari is blocked with "Use Chrome or Edge".
- **Check per agent:** microphone permission granted for the CRM site in Chrome, and no second tab or old test page registered as the same SIP user.

## 4. Other people to inform

### 4.1 Exotel

- Per-user scoped tokens (1.1): no longer a blocker; ask only as a nice-to-have.
- Production click-to-call now rings the customer before the agent; it used to ring the agent (`From`) first. The CRM code (`make_a_call`) is unchanged since June. Send one `CallSid` from before and one from after the API key change and ask whether the account moved to the new calling platform when softphone was enabled.
- Their WebRTC notification docs describe `incoming_call` / `call_answered` / `call_missed` events, but the account sends `busy` / `free`. Ask whether the `incomingcallhungup` / `missedcall` notification types carry the call outcome.

### 4.2 OpsGate owner

- The copied test flow still carried the production OpsGate Passthru, so four test calls on 2026-09-29 (≈ 01:31–01:39, from the test agent's own number) were posted to production OpsGate. They may want to remove any records those created.
- The OpsGate webhook URL (including its secret path segment) was shared in a chat during testing; ask whether it should be rotated.
- Needed for 3.1: agree how the production flow reports call-end data to both OpsGate and the CRM.

## 5. Product decisions

### 5.1 Default disposition

- **What:** when a call ends, the popup pre-selects "Requested Callback", which requires a callback time before Close is enabled.
- **Why it matters:** this now applies to every answered softphone call. Decide whether it stays the default.

### 5.2 Auto-open Lead/Deal

- Sign off the Phase F decisions table in the plan doc (when, where, precedence, unknown numbers, mobile inbound).

## 6. Decision: click-to-call is removed at go-live (no mobile fallback)

Decided 2026-09-29: once the softphone goes live, classic click-to-call (Exotel rings the agent's mobile, then the customer) is removed. The browser is the only calling device. During the pilot the current mobile fallback stays in place.

What changes at go-live, and why:

| Area | Today (pilot) | At go-live | Why it matters |
| --- | --- | --- | --- |
| Outbound when the softphone isn't registered | Falls back to `make_a_call` (mobile) with a toast | Block the call with a clear error and a "Reconnect softphone" action | Without fallback, a silent failure means the agent cannot call at all |
| Safari | Routes Exotel calls to the mobile | Block with "Use Chrome or Edge" | Safari connects with no audio |
| Registration reliability | Nice to have | Must-have: auto re-register after network loss, visible registration status in the header | Every call depends on the browser being registered |
| Inbound routing | Connect can include agent mobiles | Connect dials Exotel users whose active device is SIP; use a group of several agents so a closed tab doesn't mean a missed call | If an agent's tab is closed, their leg fails and the call goes to "If nobody answers" |
| Agent mobile numbers | Used as the first leg of click-to-call | Only needed if Exotel still requires a phone device per user; `mobile_no` on CRM Telephony Agent loses its calling role | Avoid calls reaching personal phones by accident |
| `make_a_call`, per-agent "Use Exotel Browser Softphone" toggle, global softphone switch | In use | Remove `make_a_call` and the mobile path from `ExotelCallUI.vue`; keep the global switch as an emergency kill switch only if a fallback plan exists | Dead paths confuse agents and reviewers |
| Webhook handler (`handle_request`) | Serves click-to-call and softphone | Keep — the flow Passthru uses the same classic payload format | Inbound outcome still arrives this way |
| Production leg-order issue (4.1) | Affects click-to-call | Irrelevant after removal | Still worth reporting to Exotel for the pilot period |

Consequences for the rollout order:

- The network requirements (3.6) and browser policy (3.7) must be in place for every agent before go-live, not just pilot agents.
- Keep a documented emergency procedure: if the softphone breaks company-wide, agents need a way to reach customers (e.g. the Exotel dashboard or app) until it is fixed.

## 7. Done (for context)

- Outbound and inbound browser calling in Chrome, with auto-accept of the agent leg on outbound calls.
- Mute, hold, reject, hang-up in the CRM popup.
- Call logs: outbound via the `callback` setting; inbound via flow Passthru — answered → Completed (talk time, recording), missed → Call Not Answered ("Missed Call"), rejected → Busy ("Declined").
- Reconcile job for logs whose webhooks never arrive (Calls API, waits for Exotel to finalise the record).
- Webhook hardening: unknown statuses ignored on update, finished logs only enriched (never overwritten), server-side creates only for enabled softphone agents, epoch `EndTime` ignored.
- Frontend: idempotent setup, no stale-check hang-up of a connected call, Safari fallback.
- Tests: `crm/tests/test_exotel_softphone.py` (50), `crm/tests/test_integrations.py` (23), `crm/fcrm/doctype/crm_call_log/test_crm_call_log.py` (21), frontend Vitest suite (125) passing.
