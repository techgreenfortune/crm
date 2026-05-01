# CRM Dev Setup & Integration Log

Running environment: **Docker** (frappe/bench image), MariaDB + Redis as separate containers.  
Site name: `crm.localhost`  
App directory (host): `/Users/aadarsh/Desktop/crm`  
Bench inside container: `/home/frappe/frappe-bench`

---

## Environment Setup

### Starting Docker

Docker Desktop must be running before the containers can start.

```bash
open -a Docker
# Wait ~30s for daemon to be ready, then:
cd /Users/aadarsh/Desktop/crm/docker
docker compose -f docker-compose.yml up -d
```

Containers started:
- `crm-frappe-1` — Frappe app server (ports 8000, 9000)
- `crm-mariadb-1` — MariaDB 10.8
- `crm-redis-1` — Redis

App is accessible at: `http://crm.localhost:8000`

---

## Deploying Code Changes

The Docker container does **not** mount the host app directory — it runs a copy of the code baked into the image. Any changes made on the host must be manually synced into the container.

### Backend changes (Python / DocType JSON)

```bash
# Copy a new doctype folder
docker cp /Users/aadarsh/Desktop/crm/crm/fcrm/doctype/<doctype_folder> \
  crm-frappe-1:/home/frappe/frappe-bench/apps/crm/crm/fcrm/doctype/

# Copy a single Python file
docker cp /path/to/file.py \
  crm-frappe-1:/home/frappe/frappe-bench/apps/crm/path/to/file.py

# Run migration to register new/changed DocTypes in the DB
docker exec crm-frappe-1 bash -c \
  "cd /home/frappe/frappe-bench && bench --site crm.localhost migrate"
```

### Frontend changes (Vue / JS)

```bash
# Sync entire frontend src (safest when many files changed)
docker cp /Users/aadarsh/Desktop/crm/frontend/src \
  crm-frappe-1:/home/frappe/frappe-bench/apps/crm/frontend/

# Rebuild the frontend bundle
docker exec crm-frappe-1 bash -c \
  "cd /home/frappe/frappe-bench/apps/crm && \
   /home/frappe/.nvm/versions/node/v24.13.0/bin/node \
   /home/frappe/.nvm/versions/node/v24.13.0/bin/yarn build"
```

After rebuild, hard-reload the browser: **Cmd+Shift+R**

---

## Integrations

### Brevo (Transactional Email)

**What it does:** Sends all transactional emails via Brevo's HTTP API instead of relying on Frappe's built-in SMTP mail queue. When Brevo is enabled it handles all email triggers; when disabled each trigger falls back to `frappe.sendmail`.

**Files added:**

| Path | Description |
|------|-------------|
| `crm/fcrm/doctype/crm_brevo_settings/` | Single DocType storing enabled flag, API key, sender email, sender name |
| `crm/integrations/brevo/brevo_handler.py` | HTTP sender using Brevo's `/v3/smtp/email` API |
| `crm/integrations/brevo/api.py` | Whitelisted endpoints: `is_brevo_enabled`, `send_test_email` |
| `frontend/src/components/Settings/BrevoSettings.vue` | Settings UI — enable/disable, credentials form, Send Test Email |
| `frontend/src/composables/settings.js` | Added `brevoEnabled` reactive ref |
| `frontend/src/components/Settings/Settings.vue` | Added Brevo entry under Integrations tab |
| `crm/fcrm/doctype/crm_invitation/crm_invitation.py` | Routes invitation emails through Brevo when enabled; falls back to `frappe.sendmail` |
| `crm/api/event.py` | Routes calendar event reminder emails through Brevo when enabled; falls back to `frappe.sendmail` |

**Setup steps:**

1. Get your API key from Brevo → top-right profile → SMTP & API → API Keys (v3 key, starts with `xkeysib-`)
2. Verify your sender email domain in Brevo → Senders & IPs
3. In CRM: Settings → Integrations → Brevo → Enable
4. Enter API Key, Sender Email, Sender Name → **Update**
5. Click **Send Test Email** — sends to the logged-in user's email address
6. Check inbox for the test email to confirm delivery

**Gotchas encountered:**

- `bench` must be run from inside the container and from the bench directory: `cd /home/frappe/frappe-bench && bench ...`
- `bench --site <site> <cmd>` syntax — the `--site` flag must come before the subcommand
- The Docker container does not mount host source files; every change must be `docker cp`'d in and the frontend rebuilt
- Duplicate `import Email2Icon` in `Settings.vue` caused a silent build failure — removed the second import
- `__()` (Frappe translation function) is only available as a Vue template global, not in `<script setup>` — use plain strings in JS callbacks
- `toast({ title, variant })` is not the correct API in this frappe-ui version — use `toast.success()`, `toast.error()`, `toast.warning()`
- `session.user` returns the login name (e.g. `"Administrator"`), not an email — use `getUser()?.email` from `usersStore` to get a valid recipient address for the test email

**Deploy commands used:**

```bash
# Backend
docker cp /Users/aadarsh/Desktop/crm/crm/fcrm/doctype/crm_brevo_settings \
  crm-frappe-1:/home/frappe/frappe-bench/apps/crm/crm/fcrm/doctype/

docker cp /Users/aadarsh/Desktop/crm/crm/integrations/brevo \
  crm-frappe-1:/home/frappe/frappe-bench/apps/crm/crm/integrations/

docker cp /Users/aadarsh/Desktop/crm/crm/fcrm/doctype/crm_invitation/crm_invitation.py \
  crm-frappe-1:/home/frappe/frappe-bench/apps/crm/crm/fcrm/doctype/crm_invitation/crm_invitation.py

docker exec crm-frappe-1 bash -c \
  "cd /home/frappe/frappe-bench && bench --site crm.localhost migrate"

# Frontend
docker cp /Users/aadarsh/Desktop/crm/frontend/src \
  crm-frappe-1:/home/frappe/frappe-bench/apps/crm/frontend/

docker exec crm-frappe-1 bash -c \
  "cd /home/frappe/frappe-bench/apps/crm && \
   /home/frappe/.nvm/versions/node/v24.13.0/bin/node \
   /home/frappe/.nvm/versions/node/v24.13.0/bin/yarn build"
```

---

### OpsGate SSO (Single Sign-On)

**What it does:** Adds an OpsGate link in the CRM sidebar. Clicking it automatically logs the user into OpsGate — no separate login screen. Tokens are exchanged server-to-server between Frappe and the OpsGate backend using a shared secret. The logged-in CRM user's email is used to look up their OpsGate account.

**Architecture:**

```
CRM sidebar click
  → Frappe backend (crm.api.settings.get_opsgate_redirect_url)
    → POST /api/user/sso-token to OpsGate backend (shared secret + user email)
      ← access_token + refresh_token
  → redirect to <opsgate_url>/auth/sso?token=...&refresh=...
    → OpsGate frontend verifies JWT, creates NextAuth session
      → lands on OpsGate dashboard, already logged in
```

**Files changed:**

| Repo | Path | Description |
|------|------|-------------|
| CRM | `crm/api/settings.py` | Added `get_opsgate_redirect_url` whitelisted endpoint |
| CRM | `crm/fcrm/doctype/fcrm_settings/fcrm_settings.json` | Added `opsgate_enabled` (Check) and `opsgate_url` (Data) fields |
| CRM | `frontend/src/components/Settings/GeneralSettings.vue` | Added Enable OpsGate toggle + URL input with Save button |
| CRM | `frontend/src/components/Layouts/AppSidebar.vue` | Added OpsGate nav item with SSO click handler |
| CRM | `frontend/src/components/SidebarLink.vue` | Added `onClick` prop to allow custom click handlers |
| CRM | `frontend/src/composables/settings.js` | Added `opsGateEnabled` and `opsGateUrl` reactive refs |
| OpsGate backend | `src/controllers/user.controller.ts` | Added `ssoLogin` controller |
| OpsGate backend | `src/routes/user.routes.ts` | Registered `POST /user/sso-token` route |
| OpsGate backend | `.env` / `sample.env` | Added `CRM_SSO_SECRET` |
| OpsGate frontend | `src/lib/auth/authOptions.ts` | Added `sso-token` NextAuth credentials provider |
| OpsGate frontend | `src/app/auth/sso/page.tsx` | New SSO landing page — reads token from URL, creates session |
| OpsGate frontend | `src/lib/constants/routes.constants.ts` | Added `/auth/sso` to `PUBLIC_PATHS` |
| OpsGate frontend | `.env` | Added `OPSGATE_JWT_SECRET` |

**Setup — per environment (dev/staging/prod):**

**1. OpsGate backend `.env`**
```env
CRM_SSO_SECRET=crm-to-opsgate-sso-secret-2025
```
> The value must match `crm_sso_secret` in the CRM site config exactly.

**2. OpsGate frontend `.env`**
```env
OPSGATE_JWT_SECRET=greenfortunejwtsecret2025
```
> Must match `JWT_SECRET` in the OpsGate backend `.env`.

**3. CRM Frappe site config** (run once per site)

For local Docker dev (OpsGate running on host port 4011 — use `host.docker.internal`, not `localhost`, because `localhost` inside the container refers to the container itself):
```bash
docker exec crm-frappe-1 bash -c \
  "cd /home/frappe/frappe-bench && bench --site crm.localhost set-config crm_sso_secret 'crm-to-opsgate-sso-secret-2025'"
docker exec crm-frappe-1 bash -c \
  "cd /home/frappe/frappe-bench && bench --site crm.localhost set-config opsgate_api_url 'http://host.docker.internal:4011/api'"
```
For staging:
```bash
bench --site <site> set-config crm_sso_secret "crm-to-opsgate-sso-secret-2025"
bench --site <site> set-config opsgate_api_url "https://backend.thegreenfortune.com/api"
```

**4. Run DB migration** (needed once — adds `opsgate_enabled` and `opsgate_url` columns)
```bash
bench --site crm.localhost migrate
```

**5. Enable in CRM UI**

Settings → General Settings → Enable OpsGate toggle → enter OpsGate URL → Save

**6. User mapping**

Every CRM user who needs OpsGate access must have an account in OpsGate with the **same email address** as their Frappe account. The SSO looks up by email — if no match is found, the redirect will fail with a 404.

**Gotchas encountered:**

- `frappe.session.user` returns `"Administrator"` for the admin user, not their email — fixed by fetching with `frappe.db.get_value("User", frappe.session.user, "email")`
- The bench at `frappe-bench/apps/crm/` is a **separate copy** from `Desktop/crm/` — changes must be made in the bench copy (or synced via `cp`) for the running server to pick them up
- `frappe.client.set_value` response omits fields not returned by the DB query (including `opsgate_enabled`, `opsgate_url`) — fixed by patching `settings.doc` manually after save in the Vue component
- The `frappe-ui` Switch component uses `defineModel<boolean>` — binding directly to integer values (`0`/`1`) from Frappe causes the switch to snap back; fixed by using `:model-value="Boolean(...)"` 
- New `@frappe.whitelist()` functions require `bench --site <site> clear-cache` before they appear (Frappe caches module imports)
- `call()` from `frappe-ui` handles CSRF automatically — use it instead of raw `fetch()` for Frappe API calls
- In Docker, `localhost` inside the container refers to the container itself, not the Mac host — use `host.docker.internal:<port>` to reach services running on the host (e.g. OpsGate on port 4011)
- Files synced via `docker cp` from macOS are owned by uid 501 (host user), not `frappe` — if the build fails with `EACCES`, run `docker exec -u root crm-frappe-1 chown -R frappe:frappe <path>` to fix

**Deploy commands (Docker):**

```bash
# Backend
docker cp /Users/aadarsh/Desktop/crm/crm/api/settings.py \
  crm-frappe-1:/home/frappe/frappe-bench/apps/crm/crm/api/settings.py

docker cp /Users/aadarsh/Desktop/crm/crm/fcrm/doctype/fcrm_settings \
  crm-frappe-1:/home/frappe/frappe-bench/apps/crm/crm/fcrm/doctype/

docker exec crm-frappe-1 bash -c \
  "cd /home/frappe/frappe-bench && bench --site crm.localhost migrate"

docker exec crm-frappe-1 bash -c \
  "cd /home/frappe/frappe-bench && bench --site crm.localhost set-config crm_sso_secret 'crm-to-opsgate-sso-secret-2025'"

docker exec crm-frappe-1 bash -c \
  "cd /home/frappe/frappe-bench && bench --site crm.localhost set-config opsgate_api_url 'https://backend.thegreenfortune.com/api'"

# Frontend
docker cp /Users/aadarsh/Desktop/crm/frontend/src \
  crm-frappe-1:/home/frappe/frappe-bench/apps/crm/frontend/

docker exec crm-frappe-1 bash -c \
  "cd /home/frappe/frappe-bench/apps/crm && \
   /home/frappe/.nvm/versions/node/v24.13.0/bin/node \
   /home/frappe/.nvm/versions/node/v24.13.0/bin/yarn build"
```
