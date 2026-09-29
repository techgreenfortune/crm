# Exotel Softphone — Call Data Persistence Plan

As of 2026-09-29. Evidence and background: [exotel-softphone-feasibility.md](exotel-softphone-feasibility.md). Everything still outstanding, with context: [exotel-softphone-open-items.md](exotel-softphone-open-items.md).

## Goal

Every Exotel softphone call — outbound or inbound, answered, missed or rejected — ends as exactly one complete `CRM Call Log`: type, status, from/to, agent, duration, start/end, recording and Lead/Deal link. This must hold when Exotel webhooks arrive late, duplicated, out of order or not at all.

Calling itself is proven (outbound and inbound browser calls, audio, hold, mute, call-log creation, inbound caller number). This plan covers only what happens to the data afterwards.

## Current state

| Area | Status |
| --- | --- |
| Outbound status, duration, recording, times via `callback` setting | Works |
| Outbound `to` number | Bug: `normalize_call_payload` prefers `VirtualNumber` over `ToNumber`, so logs store the Exophone instead of the customer |
| Inbound status, duration, recording | Not persisted: no inbound payload captured yet, and a simulation shows the current mapping returns invalid lowercase statuses (`completed`, `no-answer`, `in-progress`) for non-outbound directions, so `save()` fails |
| Missed inbound with no browser-created log | Never created (same invalid-status failure) |
| Webhook never arrives | Log stays `Ringing` (e.g. 2026-09-28 23:58 inbound; Calls API shows it `completed`, 34 s, recorded) |
| Duplicate / out-of-order webhooks | Terminal status is protected, but the guard drops the whole update, so late duration/recording is lost |

Verified Calls API facts (classic `/v1/Accounts/{sid}/Calls/{CallSid}.json`):

- Outbound browser call: `Direction: outbound-dial`, `From: sip:<SipId>`, `To: <customer>`.
- Inbound browser call: `Direction: inbound`, `From: <caller>`, `To: sip:<SipId>`.

App settings configured on the test app (`e8132c55-…`): `callback`, `popup`, `incomingCallHangup`, `missedCall` (all → local `handle_request` via ngrok), `record=true`.

## Phase A — Capture inbound payloads (≈ 1 h test session)

Encode the observed contract, not a guess.

`provision.py connect-flow` is **not** a substitute: it places an `outbound-api` call that runs the flow, so Exotel records it as outbound, not as a customer dialling the Exophone. Real inbound capture needs a temporary Exophone switch, done outside business hours:

1. In the copied test flow (110532) Connect applet: set "Create popup" to
   `https://integrationscore.mum1.exotel.com/v2/integrations/call/inbound_call/e8132c55-90a0-491e-8c84-212713060d07?type=popup`;
   add a Passthru → CRM `handle_request` URL on "After the call conversation ends" and on "If nobody answers", each followed by a Hangup (a Passthru with no next applet leaves the call hanging). Replace any production Passthru URL (e.g. OpsGate) in the copy. Save.
2. Record the production flow ID currently attached to the Exophone (Exotel `IncomingPhoneNumbers` API `voice_url`, or the dashboard).
3. With the CRM registered in Chrome, switch the Exophone to the test flow and call it from a phone: answered, missed (ring out), rejected — about a minute apart.
4. Switch the Exophone back to the production flow **immediately**, then verify via the `IncomingPhoneNumbers` API that `voice_url` ends with the production flow ID. Do not end the session until this is confirmed.
5. Export the resulting `Integration Request` payloads, strip the `key` parameter, and save them as test fixtures.

Done when: fixtures exist for answered, missed and rejected outcomes, and the Exophone is verified back on the production flow.

## Phase B — Payload normalizer and update logic (≈ 0.5 d)

File: `crm/integrations/exotel/handler.py`. Base every mapping on the Phase A fixtures.

1. **Direction:** normalize `inbound` / `incoming` and `outbound` / `outbound-dial` to one canonical value before any create/update logic.
2. **Numbers:** outbound `To` = `ToNumber`; inbound caller and Exophone fields as observed in fixtures.
3. **Status:** map observed `CallState` / `CallStatus` values to valid `CRM Call Log` options. An unknown value on update is logged and ignored; `Ringing` is used only when creating a new record.
4. **Terminal enrichment:** once a log is terminal, never change its status, but still fill missing `duration`, `start_time`, `end_time` and `recording_url`.
5. **Server-side create (e.g. missed inbound):** only when `AppUserID` resolves to an enabled `CRM Telephony Agent` with `exotel_softphone_enabled`; never trust an arbitrary agent email from the guest webhook.
6. **Tests** (`crm/tests/test_exotel_softphone.py`): one per fixture, classic click-to-call payload unchanged, late `answered` after `completed` ignored, terminal enrichment, unknown status ignored, untrusted `AppUserID` rejected.

Done when: replaying all fixtures yields the correct final log for each outcome, and the 2026-09-28 outbound payloads store the customer number.

## Phase C — Reconciliation job (≈ 0.5 d)

Safety net for missing webhooks.

1. `reconcile_stale_call_logs()` in `handler.py`: select Exotel logs in `Initiated` / `Ringing` / `In Progress` older than 5 minutes and younger than 7 days; fetch each from the classic Calls API (`?details=true`); update status, duration, times and recording using the same Phase B mapping; update each field independently.
2. Per-run lock (skip if a previous run is still active), request timeout, batch limit (~50), per-log error logging.
3. Schedule in `crm/hooks.py` under `cron` `*/5 * * * *` — worst-case recovery ≈ 10 minutes.
4. A log the Calls API still cannot find 60 minutes after creation is marked `Failed` with an error logged (≈ 11 attempts; also catches browser-registered `CallSid`s that never existed).
5. This job also confirms browser-registered logs, so no separate "unconfirmed" field is introduced.
6. Tests: mocked Calls API completes a stuck log; terminal logs untouched; lock prevents concurrent runs.

Done when: the stuck 2026-09-28 logs (11:28–11:39 outbound, 23:58 inbound) complete after one run.

## Phase D — Registration verification and Lead linking (≈ 0.5 d)

1. `register_softphone_call` keeps creating the log immediately (the frontend waits on it before enabling Accept). Verification against the Calls API happens asynchronously / via Phase C: outbound requires `From` = agent SipId and `To` ≈ dialled number; inbound requires `To` = agent SipId. Compare phone numbers after normalization, not as raw strings.
2. Lead linking: the empty `reference_doctype = CRM Lead` is partly the DocType default. Clear `reference_doctype` when no Lead/Deal matches. Test with a call from a known Lead's number before changing the lookup in `link()`.
3. Tests: mismatched `CallSid` flagged; known Lead number links correctly; no match leaves both reference fields empty.

## Phase E — Small technical fixes (≈ 0.5 d)

1. Keep `onMounted(setup)` (it fixed a parent-ref timing bug); make `setup()` fully idempotent (dispositions fetch, popup restore, socket listeners, stale timer, softphone init).
2. Stale check: never hang up a connected softphone call because webhook/socket events are quiet.
3. Safari: fall back to mobile calling for the pilot, with a toast.
4. Run `yarn build` once to validate the `vite.config.js` `transformMixedEsModules` change.

## Phase F — Open the caller's Lead/Deal on incoming calls (proposed, ≈ 0.5 d)

Already in place: the call popup looks up the caller via `crm.integrations.api.get_contact_by_phone_number` and shows a "Lead"/"Deal" button (`openDealOrLead` in `ExotelCallUI.vue`). The agent has to click it.

Proposed behaviour (awaiting product sign-off):

| Decision | Proposal | Reason |
| --- | --- | --- |
| When to open | On Accept, not on ring | Avoids pulling the agent off a half-edited form for a call they may not take |
| Where | Same tab (router navigation) | The softphone lives in the header layout and survives route changes; a new tab would start a second SIP registration for the same user |
| Deal and Lead both match | Deal first, then Lead | Same precedence as the existing button |
| Unknown number | Show "New Lead" in the popup, prefilled with the caller's number; don't auto-open a blank form | One deliberate click instead of a surprise form |
| Mobile (click-to-call) inbound | Same behaviour when the agent answers on the phone (socket "answered" event), optional | Consistent experience for both calling modes |

Constraints:

- Navigating away can drop unsaved edits on the current page; opening on Accept (not ring) limits this. Verify how the Lead page's unsaved-changes guard behaves on route change.
- Permissions are unchanged: if the caller's Lead is outside the agent's scope (`crm/overrides/crm_lead_permissions.py`), the page shows the normal not-permitted error; the lookup must not bypass role gates.
- Phone number is the dedup key, so at most one Lead should match; if several Contacts match, keep the existing lookup's choice.

Tests: accept with a known Lead number navigates to that Lead; Deal takes precedence; unknown number shows "New Lead" with the number prefilled; reject/missed never navigates.

## Separate tracks (not in this plan)

- **Product:** whether "Requested Callback" stays the default disposition for answered softphone calls (it currently blocks Close until a callback time is set).
- **Token scope — resolved in code (2026-09-29):** the app token now stays on the server; the browser registers with only the agent's own mapping. See [exotel-softphone-server-token-plan.md](exotel-softphone-server-token-plan.md).
- **Rollout config:** production flow Connect must dial Exotel users (not raw numbers; watch the empty-number green dot), Record enabled, production app settings pointing at the production CRM with a strong webhook key.

## Progress (2026-09-29)

- Phase A: done (2026-09-29 01:34–01:39, direct calls to the Exophone on the test flow). Inbound notifications need the flow's Connect "Create popup" URL. They only report agent state: `CallStatus` `busy` (agent being rung) then `free` (agent released) — identical for answered, missed and rejected, with no duration, times or recording. The classic Calls API decides the outcome: top-level `Status` is `completed` for all three; `Details.Leg2Status` is `completed` (answered, 12 s conversation, recorded) / `no-answer` (missed) / `busy` (rejected). Implemented: inbound `busy` → Ringing; `free` enqueues an immediate Calls API reconcile; inbound outcome from `Leg2Status` + `ConversationDuration`: answered → Completed, rejected (`busy`) → Busy (UI "Declined"), missed / never reached agent → Call Not Answered (UI "Missed Call").
- Phase B: outbound-proven parts done — canonical direction, outbound `To = ToNumber`, status via `CallLogStatus` with unknown-on-update ignored, terminal enrichment, `AppUserID` → enabled Telephony Agent check, call type on server-side create. Inbound status words beyond `completed`/active still to confirm against Phase A fixtures.
- Connect-applet Passthru (tested 2026-09-29 01:58–02:00, test flow only): gives the outcome immediately in classic format. Missed: `CallType=incomplete`, `DialCallStatus=no-answer`. Rejected: `incomplete` / `busy`, `Legs[0][CauseCode]=USER_BUSY`. Answered: `completed` / `completed`, `Legs[0][OnCallDuration]` = talk time, `RecordingUrl` (playable after `RecordingAvailableBy`). `DialCallDuration` includes ring time; `EndTime` is the epoch placeholder `1970-01-01 05:30:00`. Implemented: incomplete+busy → Busy, duration 0 for incomplete, talk time from `OnCallDuration`, epoch `EndTime` ignored (reconcile fills the real one). Each Passthru needs a Hangup after it, or the call hangs.
- Rollout requirement: production flow Connect needs a Passthru → production CRM `handle_request` on both "After the call conversation ends" and "If nobody answers", each followed by Hangup. The existing "conversation ends" Passthru goes to OpsGate and must stay; confirm with the OpsGate owner whether Exotel allows chaining, or have OpsGate forward.
- Phase C: done — `reconcile_stale_call_logs` on `*/5` cron, file lock, 10 s timeout, 50-log batch. Selects non-terminal logs **and** terminal logs still missing `end_time` (e.g. answered calls finished by a Passthru whose `EndTime` was the epoch placeholder); `Failed` logs are excluded. `Completed` logs without a `recording_url` also stay eligible for 2 hours (`RECONCILE_RECORDING_WINDOW_HOURS`), since Exotel publishes recordings a few minutes after the call and calls without recording never get one. Per Exotel's Call Details API docs, Duration/EndTime/leg details fill in asynchronously (~2 min after the call ends), so a record with a final status but no `EndTime` is skipped until a later run.
- Open with Exotel: their WebRTC notification docs describe `incoming_call` / `call_answered` / `call_missed` events, but the account sends `busy` / `free`; ask whether the `incomingcallhungup` / `missedcall` notification types carry the outcome.
- Phase D: done. Browser-registered logs are flagged `is_softphone_call` and require the agent's `exotel_sip_id`; a webhook-created log is flagged too when its own caller/receiver registers it from the browser. The reconcile job (`softphone_call_matches_log`) verifies flagged logs: outbound must come from the caller's SIP ID and reach the logged number (last 10 digits); inbound must ring the receiver's SIP ID; a flagged log with no matching SIP leg (e.g. a spoofed click-to-call `CallSid`) is marked Failed with an error log. Server-created logs are trusted. Reconcile makes at most 50 Calls API requests per run across both selections. Lead linking already worked — calls link through the `links` table (Dynamic Link), which `get_linked_calls` reads; verified with a real Lead number in four formats. The empty `reference_doctype = CRM Lead` was only the DocType default and is now cleared when nothing matches.
- Agent provisioning: enabling "Use Exotel Browser Softphone" on a CRM Telephony Agent now looks up (or creates) the agent's Integration Core user mapping and stores the SIP ID in the new read-only `exotel_sip_id` field; it fails with a clear message if the Exotel user has no SIP device. Verified read-only against Exotel for the test agent. Changing mobile/Exotel number re-checks the mapping but does not update it on Exotel's side (no update API used).
- Review fixes (2026-09-29): inbound call logs store the Exophone as `to` (`DialWhomNumber` is the agent's device on inbound and is used only for outbound); handler comment corrected — webhooks are authenticated only by the `key` query parameter, Exotel sends no signature.
- Phase E: done — idempotent `setup()` (kept `onMounted`), no stale hang-up of a connected softphone call, Safari falls back to mobile. Production `yarn build` passed with the `transformMixedEsModules` change (reported in review, 2026-09-29).

## Order and effort

| # | Phase | Effort | Depends on |
| --- | --- | --- | --- |
| 1 | A — Capture inbound payloads | 1 h | Colleague's phone, test flow popup URL |
| 2 | B — Normalizer + update logic | 0.5 d | A |
| 3 | C — Reconciliation job | 0.5 d | B mapping |
| 4 | D — Verification + Lead linking | 0.5 d | C |
| 5 | E — Small fixes | 0.5 d | — |
| 6 | F — Open caller's Lead/Deal on Accept (proposed) | 0.5 d | Product sign-off on the decisions table |
| | **Total** | **≈ 2.5–3 d** + test session | Real-call check of the server-side token change |
