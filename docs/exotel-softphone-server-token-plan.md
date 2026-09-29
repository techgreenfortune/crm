# Exotel softphone: keep the app token on the server

Status (2026-09-29): Phases 1–3 built and unit-tested (backend 50/50, frontend 125/125, `yarn build` passes). Phase 4 (INVITE race) and the real Chrome regression run are next.

Deviations from the draft, found while building:

- `ExotelAccountSid` comes from the user mapping, so the server doesn't call `/app` at all.
- `register_softphone_call` now accepts only `Incoming`; the browser can no longer register an outbound CallSid of its choosing.
- Vitest can't load the SDK's WebRTC client (extensionless ESM imports only a bundler resolves). The contract test reads the package's `index.d.ts` and `Constants.js` instead and mocks the entry point.

Related: [feasibility](exotel-softphone-feasibility.md) · [persistence plan](exotel-softphone-persistence-plan.md) · [open items](exotel-softphone-open-items.md)

## Why

The browser currently gets the Integration Core **app token** from `get_softphone_config()` and passes it to `ExotelCRMWebSDK`. That token is account-wide. Anyone with it can:

- read every agent's user mapping, including their SIP credentials
- place calls as any mapped agent

We were waiting for Exotel to issue per-user scoped tokens. The SDK source shows we don't need them.

## What the SDK actually does

Checked in `@exotel-npm-dev/exotel-ip-calling-crm-websdk@1.2.2` (`output/`):

| Request | Where | Needs token |
|---|---|---|
| `GET /app` | `ExotelCRMWebSDK` init | yes |
| `GET /app_setting` | `ExotelCRMWebSDK` init (only a UI-widget preference, per the SDK's own comment) | yes |
| `GET /usermapping?user_id=` | `ExotelCRMWebSDK` init | yes |
| `POST /call/outbound_call` | `ExotelWebPhoneSDK.MakeCall` | yes |

Everything else is SIP over WSS, driven by `ExotelWebPhoneSDK.Initialize(sipInfo, …)`, which never touches the token.

The package entry point exports `ExotelWebPhoneSDK`, `User` and the constants. `User` decrypts `SipSecret` with the key shipped in the SDK. So the browser can register with only its own mapping, and the token never has to leave the server. No fork is needed.

Field names observed on real responses:

- `/usermapping` `Data`: `CustomerId`, `AppID`, `AppUserId`, `ExotelAccountSid`, `ExotelUserName`, `SipId`, `SipSecret`, `VirtualNumber`, `AgentNumber`, …
- `/call/outbound_call`: `{Status: "Success", Code: 200, Data: {CallSid, AppUserID, FromNumber: "sip:<id>", ToNumber, VirtualNumber, Direction: "outbound", …}}`
- The SDK's `User` reads `customer_id`, but the mapping sends `CustomerId`. So the SDK's own `MakeCall` has always sent `customer_id: undefined`, which `JSON.stringify` drops. The server call sends the same three fields the SDK really sends: `app_id`, `to`, `user_id`.

## What stays exposed

The browser still holds the agent's **own** SIP username and secret. Any WebRTC softphone needs that to register, including one built on Exotel's scoped tokens. The SDK's `User.sipSecret` getter logs the encrypted secret to the console. That is the agent's own credential in their own browser, which is acceptable, but don't claim "no secrets in the console".

## Phase 1 — Server-side init contract

`crm/integrations/exotel/handler.py`, `get_softphone_config()`:

1. Return `{enabled, AppID, AppUserId, SipId, SipSecret, ExotelUserName, ExotelAccountSid}` built from an allow-list of mapping fields. Never pass through the whole upstream response. No token, no app secret.
2. Fetch `/usermapping` on the server with the cached app token. `AppUserId` is always the session user's email; the browser never sends a `user_id`.
3. Take `ExotelAccountSid` from the mapping, so `/app` isn't needed. Skip `/app_setting`.
4. Require: the integration and softphone enabled, an opted-in Telephony Agent, a stored `exotel_sip_id`, and a returned `SipId` equal to it. Otherwise return `{enabled: false}` (not set up) or throw (set up but broken).

Tests: required fields present; no token or secret in the response; lookup uses the session user's email; disabled or unmapped agent; `SipId` mismatch rejected.

## Phase 2 — Lower-level browser init

`frontend/src/utils/exotelSoftphone.js`:

1. Import `ExotelWebPhoneSDK`, `User`, `voipDomain`, `voipDomainSIP` from the package entry point.
2. Build `new User(mapping)` and `sipInfo` exactly as `ExotelCRMWebSDK` does. That includes `domain: voipDomain + ":443"`, which yields `…:443:443`. It's the value proven on real calls, so keep it for now rather than "fixing" it inside a security change.
3. `new ExotelWebPhoneSDK(null, user).Initialize(sipInfo, onCallEvent, true, onRegistration, onSession)`.
4. Keep the singleton, the listeners, the controls and unregister. Remove `accessToken` everywhere.
5. Unit test: the package still exports `ExotelWebPhoneSDK` and `User`, and `voipDomain`/`voipDomainSIP` are unchanged, so an SDK upgrade fails CI instead of breaking calls.

## Phase 3 — Server-side outbound call

New `make_softphone_call(phone_number, reference_doctype=None, reference_docname=None)`, POST only:

1. Derive everything else on the server: agent, `AppUserId`, `app_id`, token.
2. Validate: softphone enabled, agent opted in with a SIP ID, number has 10+ digits, Lead/Deal reference readable (`_validate_softphone_reference`).
3. Per-user limit of 10 dials a minute. Frappe's `rate_limit` is IP-based, and one office shares an IP, so it's done per user.
4. `POST /call/outbound_call` with `{app_id, to, user_id}`.
5. The response must be `Status: Success` with a `CallSid`. `Data.FromNumber` must equal the agent's SIP ID, otherwise log an error and refuse.
6. Create the flagged `CRM Call Log` (`is_softphone_call=1`, agent, customer, reference) before returning. A `DuplicateEntryError` goes through `_claim_existing_softphone_call`.
7. Return only `{CallSid}`. Exotel errors raise a clear message; there is never a success-shaped fallback.
8. A timeout or network error on this request doesn't prove the call wasn't placed. Return a distinct error (`exotel_dial_unknown`) so the browser can guard against a late agent leg (Phase 4).

The frontend `MakeCall()` wrapper and the separate outbound `register_softphone_call()` call are removed. `register_softphone_call` stays for inbound.

## Phase 4 — Outbound INVITE race

The agent-leg INVITE can arrive before the dial response. That happens today too (seen with an ~8 s gap): the INVITE is logged as an Incoming call. The server hop makes it more likely.

Put the logic in a pure state machine in `frontend/src/utils/exotelSoftphoneCall.js`. The repo's Vitest only covers plain utilities, not components. `ExotelCallUI.vue` just calls it.

1. Dial starts → `pending`. Any `incoming` event while pending is buffered: one slot, dropped after 30 s.
2. Dial returns a CallSid → a buffered or later INVITE with that CallSid is auto-accepted. A different CallSid is a genuine inbound call and goes through the normal inbound path.
3. Dial fails with a clear Exotel error → hang up a buffered leg, reset, show the error.
4. Dial outcome unknown (timeout) → hang up any buffered leg and reject any new INVITE for 30 s, without logging it as inbound. The call log is created by the webhook or the reconcile job if the call did happen.
5. Never auto-accept just because a dial is pending; the CallSid must match exactly.

Tests: response first, INVITE first, other inbound during dial, dial failure, dial timeout, duplicate INVITE, buffered INVITE expiring.

## Phase 5 — Inbound path

Unchanged: SDK INVITE → CallSid and caller → `register_softphone_call(..., "Incoming")` → strict SIP verification, Passthru callbacks and reconcile finish it. A webhook-created log is still claimed and flagged when the browser registers it.

## Phase 6 — Clean-up and docs

1. Remove `ExotelCRMWebSDK` usage and the frontend `MakeCall()` wrapper.
2. Check the config response, Vue state, `localStorage` and network requests for the token. The browser must make no direct requests to `integrationscore…`.
3. Keep the server token cache and `_softphone_request`.
4. Docs: open-items §1 (per-user tokens) becomes resolved, and the feasibility summary changes. Open-items 3.4 stays: the browser no longer reads app settings, but Integration Core may still use them on its side (e.g. `record`), so don't delete them.

## Validation

1. Backend: `bench --site crm.localhost run-tests --app crm --module crm.tests.test_exotel_softphone`, plus `test_integrations`.
2. Frontend: `yarn test:run`, then `yarn build`.
3. Browser network tab: no token in the config response; no requests to `/app`, `/app_setting`, `/usermapping`, `/call/outbound_call`.
4. Chrome, real calls: registration; outbound with auto-accept; inbound accept and reject; mute, hold, hang-up; Completed, Missed and Declined logs with duration and recording; refresh and logout unregister.
5. Re-run the spoofed-CallSid verification tests.

## Effort

About 2–3 days, plus half a day for the timeout handling in Phase 4.
