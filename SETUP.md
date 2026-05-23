# CRM Dev Setup & Integration Log

Two environments are supported:

|                  | Local Bench                             | Docker                                                                          |
| ---------------- | --------------------------------------- | ------------------------------------------------------------------------------- |
| **Runtime**      | Native macOS, Homebrew MariaDB + Redis  | Docker (frappe/bench image), containerised MariaDB + Redis                      |
| **Site**         | `crm.localhost`                         | `crm.localhost`                                                                 |
| **Bench**        | `~/frappe-bench`                        | `/home/frappe/frappe-bench` (inside container)                                  |
| **Project root** | `~/projects/crm` (symlinked into bench) | `/Users/aadarsh/Desktop/crm` (host) — **not** mounted; must be `docker cp`'d in |
| **App URL**      | `http://crm.localhost:8000`             | `http://crm.localhost:8000`                                                     |

---

## Local Bench (macOS)

### Prerequisites

- Node.js + Yarn
- Homebrew

### One-Time Setup (already done)

```bash
# System deps
brew install mariadb@11.8 redis pkg-config
brew services start mariadb@11.8
brew services start redis

# Python 3.11
brew install pyenv
pyenv install 3.11.9 && pyenv global 3.11.9

# bench CLI
pip install frappe-bench

# Initialize bench with Frappe v15
cd ~ && bench init frappe-bench --version version-15 --python $(pyenv which python)

# Symlink project into bench
ln -s ~/projects/crm ~/frappe-bench/apps/crm
~/frappe-bench/env/bin/pip install -e ~/frappe-bench/apps/crm
printf 'frappe\ncrm\n' > ~/frappe-bench/sites/apps.txt

# Create site and install CRM
cd ~/frappe-bench
echo "" | bench new-site crm.localhost --mariadb-root-password '' --admin-password admin
bench --site crm.localhost install-app crm
bench --site crm.localhost set-config developer_mode 1
bench --site crm.localhost set-config server_script_enabled 1
bench --site crm.localhost set-config crm_sso_secret "crm-to-opsgate-sso-secret-2025"
bench --site crm.localhost set-config host_name 'http://localhost:8000'
bench use crm.localhost
```

> `crm_sso_secret` is required by `crm.api.settings.create_crm_user`, `disable_crm_user`, `get_crm_login_url`. Without it: `AuthenticationError: CRM SSO secret is not configured`. Value must match OpsGate backend `.env` `CRM_SSO_SECRET`.

### Daily Workflow

```bash
# Ensure Homebrew services are running
brew services start mariadb@11.8
brew services start redis

# Start bench (web + worker + scheduler)
cd ~/frappe-bench && bench start
```

Open `http://crm.localhost:8000/crm` — login: `Administrator` / `admin`

### Frontend Dev Server (hot-reload)

In a second terminal:

```bash
cd ~/projects/crm/frontend && yarn install && yarn dev
```

Open `http://localhost:8080/crm` — Vite proxies API calls to bench at port 8000. Changes to `.vue` files under `frontend/src/` hot-reload instantly.

### Makefile Shortcuts

| Command                     | What it does                                 |
| --------------------------- | -------------------------------------------- |
| `make bench-start`          | `cd ~/frappe-bench && bench start`           |
| `make bench-migrate`        | `bench migrate` on `crm.localhost`           |
| `make bench-clear-cache`    | Clear Frappe site cache                      |
| `make bench-dev`            | `yarn install && yarn dev` in `frontend/`    |
| `make bench-build-frontend` | Build Vue bundle into `crm/public/frontend/` |

### Code Changes

#### Backend (Python / DocType JSON)

`~/frappe-bench/apps/crm` is a symlink to `~/projects/crm` — edits are live immediately.

After changing a DocType JSON or adding migrations:

```bash
cd ~/frappe-bench && bench --site crm.localhost migrate
```

Python changes are picked up by Frappe's watchdog auto-reloader. If not, restart:

```bash
cd ~/frappe-bench && bench restart
```

#### Frontend (Vue / JS)

Run `yarn dev` in `frontend/` and edit files under `frontend/src/` — HMR updates the browser instantly.

To build the production bundle:

```bash
cd ~/frappe-bench && bench build --app crm
```

### Resetting from Scratch

```bash
cd ~/frappe-bench
bench drop-site crm.localhost --mariadb-root-password '' --force
echo "" | bench new-site crm.localhost --mariadb-root-password '' --admin-password admin
bench --site crm.localhost install-app crm
bench --site crm.localhost set-config developer_mode 1
bench --site crm.localhost set-config server_script_enabled 1
bench --site crm.localhost set-config crm_sso_secret "crm-to-opsgate-sso-secret-2025"
bench --site crm.localhost set-config host_name 'http://localhost:8000'
bench use crm.localhost
```

---

## Docker

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

> If local MariaDB (Homebrew) is running on port 3306, `docker-compose.yml` maps the container MariaDB to `3307:3306` to avoid the conflict. This only affects host-side access; containers communicate internally by name.

> Every `docker compose down && up` reinitialises the frappe container from scratch. After each fresh start: copy all modified `.py` and `.json` files back, run `bench migrate`, and re-run `set-config` for `crm_sso_secret`, `opsgate_api_url`, and `host_name`.

### Deploying Code Changes

The Docker container does **not** mount the host app directory — it runs a copy of the code baked into the image. Every change must be `docker cp`'d in.

#### Backend (Python / DocType JSON)

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

#### Frontend (Vue / JS)

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

**Gotchas:**

- `bench` must be run from inside the container and from the bench directory: `cd /home/frappe/frappe-bench && bench ...`
- `bench --site <site> <cmd>` syntax — the `--site` flag must come **before** the subcommand
- Files synced via `docker cp` from macOS are owned by uid 501 (host user), not `frappe` — if the build fails with `EACCES`, run `docker exec -u root crm-frappe-1 chown -R frappe:frappe <path>` to fix
- `localhost` inside the container refers to the container itself, not the Mac host — use `host.docker.internal:<port>` to reach services running on the host (e.g. OpsGate on port 4011)

---

## Integrations

### Brevo (Transactional Email)

**What it does:** Sends transactional emails (calendar event reminders) via Brevo's HTTP API. When disabled, falls back to `frappe.sendmail`. Invitation emails always go through `frappe.sendmail` regardless — Brevo was generating unreachable `127.0.0.1` links inside Docker.

**Files added:**

| Path                                                 | Description                                                                                       |
| ---------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| `crm/fcrm/doctype/crm_brevo_settings/`               | Single DocType storing enabled flag, API key, sender email, sender name                           |
| `crm/integrations/brevo/brevo_handler.py`            | HTTP sender using Brevo's `/v3/smtp/email` API                                                    |
| `crm/integrations/brevo/api.py`                      | Whitelisted endpoints: `is_brevo_enabled`, `send_test_email`                                      |
| `frontend/src/components/Settings/BrevoSettings.vue` | Settings UI — enable/disable, credentials form, Send Test Email                                   |
| `frontend/src/composables/settings.js`               | Added `brevoEnabled` reactive ref                                                                 |
| `frontend/src/components/Settings/Settings.vue`      | Added Brevo entry under Integrations tab                                                          |
| `crm/fcrm/doctype/crm_invitation/crm_invitation.py`  | Sends invitation emails via `frappe.sendmail` (Brevo removed from this path)                      |
| `crm/fcrm/doctype/crm_invitation/crm_invitation.py`  | Sends invitation emails via `frappe.sendmail` (Brevo removed from this path)                      |
| `crm/api/event.py`                                   | Routes calendar event reminder emails through Brevo when enabled; falls back to `frappe.sendmail` |

**Setup:**

1. Get a v3 API key from Brevo → Profile → SMTP & API → API Keys (starts with `xkeysib-`)
2. Verify your sender domain in Brevo → Senders & IPs
3. In CRM: Settings → Integrations → Brevo → Enable
4. Enter API Key, Sender Email, Sender Name → **Update**
5. Click **Send Test Email** — sends to the logged-in user's email address
6. Check inbox to confirm delivery

**Gotchas:**

- `toast({ title, variant })` is wrong in this frappe-ui version — use `toast.success()`, `toast.error()`, `toast.warning()`
- `session.user` returns the login name (e.g. `"Administrator"`), not an email — use `getUser()?.email` from `usersStore` for the recipient address
- `__()` (Frappe i18n) is a Vue template global only — use plain strings inside `<script setup>`
- Duplicate `import Email2Icon` in `Settings.vue` caused a silent build failure — remove the second import
- `frappe.db.get_single_value` caches results in Redis — pass `cache=False` for all integration enabled flags (`brevo_enabled`, `opsgate_enabled`, `aisensy_enabled`) to avoid stale data on refresh

**Emails silently not sending (Docker):**

> ⚠️ Frappe sets `mute_emails=1` on every fresh site init. This resets on every `docker compose down && up`.

```bash
# Unmute
docker exec crm-frappe-1 bash -c \
  "cd /home/frappe/frappe-bench && bench --site crm.localhost set-config mute_emails 0"

# Verify (must return: false)
docker exec crm-frappe-1 bash -c \
  "cd /home/frappe/frappe-bench && bench --site crm.localhost execute frappe.are_emails_muted"

# Flush stuck queue items
docker exec crm-frappe-1 bash -c \
  "cd /home/frappe/frappe-bench && bench --site crm.localhost execute frappe.email.queue.flush"
```

**Deploy commands (Docker):**

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

### OpsGate ↔ CRM Single Sign-On (bidirectional SSO)

**What it does:** Users move between CRM and OpsGate without a separate login in either direction.

- **CRM → OpsGate:** OpsGate link in the CRM sidebar logs the user straight into OpsGate via JWT SSO.
- **OpsGate → CRM:** CRM icon in the OpsGate sidebar logs the user straight into CRM via Frappe's one-time login key.

**Architecture — CRM → OpsGate:**

```
CRM sidebar click
  → Frappe backend (crm.api.settings.get_opsgate_redirect_url)
    → POST /api/user/sso-token to OpsGate backend (shared secret + user email)
      ← access_token + refresh_token
  → redirect to <opsgate_url>/auth/sso?token=...&refresh=...
    → OpsGate frontend verifies JWT, creates NextAuth session
      → lands on OpsGate dashboard, already logged in
```

**Architecture — OpsGate → CRM:**

```
OpsGate sidebar CRM icon click
  → OpsGate frontend calls GET /api/user/crm-login-url (authenticated)
    → OpsGate backend POST http://localhost:8000/api/method/crm.api.settings.get_crm_login_url
        (shared secret + logged-in user email, form-encoded)
      ← one-time login key (valid 2 min, stored in Redis)
  → browser opens http://localhost:8000/api/method/frappe.www.login.login_via_key?key=<key>
    → Frappe logs user in, sets session cookie, redirects to /crm
```

**Files changed:**

| Repo             | Path                                                   | Description                                                                                                                   |
| ---------------- | ------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------- |
| CRM              | `crm/api/settings.py`                                  | Added `get_opsgate_redirect_url`, `get_crm_login_url`, `create_crm_user`, `disable_crm_user` whitelisted endpoints            |
| CRM              | `crm/fcrm/doctype/fcrm_settings/fcrm_settings.json`    | Added `opsgate_enabled` (Check) and `opsgate_url` (Data) fields                                                               |
| CRM              | `frontend/src/components/Settings/GeneralSettings.vue` | Added Enable OpsGate toggle + URL input with Save button                                                                      |
| CRM              | `frontend/src/components/Layouts/AppSidebar.vue`       | Added OpsGate nav item with SSO click handler                                                                                 |
| CRM              | `frontend/src/components/SidebarLink.vue`              | Added `onClick` prop to allow custom click handlers                                                                           |
| CRM              | `frontend/src/composables/settings.js`                 | Added `opsGateEnabled` and `opsGateUrl` reactive refs                                                                         |
| OpsGate backend  | `src/controllers/user.controller.ts`                   | Added `ssoLogin`, `getCrmLoginUrl`, `provisionCrmUsers`, `deprovisionCrmUsers` controllers                                    |
| OpsGate backend  | `src/routes/user.routes.ts`                            | Registered `POST /user/sso-token`, `GET /user/crm-login-url`, `POST /user/crm/provision`, `POST /user/crm/deprovision` routes |
| OpsGate backend  | `.env`                                                 | Added `CRM_SSO_SECRET` and `CRM_API_URL`                                                                                      |
| OpsGate frontend | `src/lib/auth/authOptions.ts`                          | Added `sso-token` NextAuth credentials provider                                                                               |
| OpsGate frontend | `src/app/auth/sso/page.tsx`                            | New SSO landing page — reads token from URL, creates session                                                                  |
| OpsGate frontend | `src/lib/constants/routes.constants.ts`                | Added `/auth/sso` to `PUBLIC_PATHS`                                                                                           |
| OpsGate frontend | `src/lib/constants/navItems-role.tsx`                  | Added CRM icon nav item with `externalKey: "crm"`                                                                             |
| OpsGate frontend | `src/components/layouts/DashboardLayout.tsx`           | `handleNavigation` calls `/user/crm-login-url` for external SSO items                                                         |
| OpsGate frontend | `.env`                                                 | Added `OPSGATE_JWT_SECRET` and `NEXT_PUBLIC_CRM_URL`                                                                          |

**Setup — per environment (dev/staging/prod):**

**1. OpsGate backend `.env`**

```env
CRM_SSO_SECRET=crm-to-opsgate-sso-secret-2025
CRM_API_URL=http://localhost:8000/api      # dev; use https://crm.example.com/api for staging/prod
```

> `CRM_SSO_SECRET` must match `crm_sso_secret` in the CRM site config exactly.

**2. OpsGate frontend `.env`**

```env
OPSGATE_JWT_SECRET=greenfortunejwtsecret2025
NEXT_PUBLIC_CRM_URL=http://localhost:8000/crm   # dev; use https://crm.example.com/crm for staging/prod
```

> `OPSGATE_JWT_SECRET` must match `JWT_SECRET` in the OpsGate backend `.env`.

**3. CRM Frappe site config** (run once per site)

Local bench:

```bash
bench --site crm.localhost set-config crm_sso_secret "crm-to-opsgate-sso-secret-2025"
bench --site crm.localhost set-config opsgate_api_url "http://localhost:4011/api"
```

Docker (OpsGate runs on the Mac host at port 4011 — use `host.docker.internal`, not `localhost`):

```bash
docker exec crm-frappe-1 bash -c \
  "cd /home/frappe/frappe-bench && bench --site crm.localhost set-config crm_sso_secret 'crm-to-opsgate-sso-secret-2025'"
docker exec crm-frappe-1 bash -c \
  "cd /home/frappe/frappe-bench && bench --site crm.localhost set-config opsgate_api_url 'http://host.docker.internal:4011/api'"
```

Staging:

```bash
bench --site <site> set-config crm_sso_secret "crm-to-opsgate-sso-secret-2025"
bench --site <site> set-config opsgate_api_url "https://backend.thegreenfortune.com/api"
```

**4. Fix invitation / password reset links** (run once per site)

`frappe.utils.get_url()` returns `http://127.0.0.1:8000` if `host_name` is not set:

```bash
# Local bench
bench --site crm.localhost set-config host_name 'http://localhost:8000'

# Docker
docker exec crm-frappe-1 bash -c \
  "cd /home/frappe/frappe-bench && bench --site crm.localhost set-config host_name 'http://localhost:8000'"
```

**5. DB migration** (needed once — adds `opsgate_enabled` and `opsgate_url` columns)

```bash
# Local bench
bench --site crm.localhost migrate

# Docker
docker exec crm-frappe-1 bash -c \
  "cd /home/frappe/frappe-bench && bench --site crm.localhost migrate"
```

**6. Enable in CRM UI**

Settings → General Settings → Enable OpsGate toggle → enter OpsGate URL → Save

**7. User mapping**

Every CRM user who needs OpsGate access must have an account in OpsGate with the **same email address**. The SSO looks up by email — if no match is found, the redirect fails with a 404.

**8. CRM user provisioning from OpsGate**

New users created via the OpsGate `POST /user/create` API are automatically provisioned in CRM as `Sales User`.

For existing users created before this feature:

```bash
# Provision one or more users into CRM
POST /user/crm/provision
Authorization: Bearer <admin_token>
{ "user_ids": [1, 2, 3] }

# Deprovision (disable) one or more users in CRM
POST /user/crm/deprovision
Authorization: Bearer <admin_token>
{ "user_ids": [1, 2] }
```

Both endpoints return a per-user result with `status: "provisioned" | "deprovisioned" | "failed"`. Deprovisioning sets `enabled=0` — data is preserved.

**Gotchas:**

- `frappe.session.user` returns `"Administrator"` for the admin user — fetch email with `frappe.db.get_value("User", frappe.session.user, "email")`
- `frappe.client.set_value` response omits fields not in the DB query — patch `settings.doc` manually in Vue after save
- The `frappe-ui` Switch component uses `defineModel<boolean>` — bind with `:model-value="Boolean(...)"` not raw integer `0`/`1` values to prevent the switch snapping back
- New `@frappe.whitelist()` functions require `bench --site <site> clear-cache` before they appear (Frappe caches module imports)
- `call()` from `frappe-ui` handles CSRF automatically — prefer it over raw `fetch()` for Frappe API calls
- The OpsGate backend must send the CRM SSO request as `application/x-www-form-urlencoded`, not JSON — Frappe's `frappe.form_dict` auto-parses form-encoded bodies
- When OpsGate SSO fails, the CRM sidebar shows a toast error instead of silently redirecting to the OpsGate login page
- `frappe.www.login.login_via_key` is rate-limited to 5 calls/hour per IP by default — during heavy testing this triggers a `TypeError: 'NoneType' object is not callable` WSGI error. Two fixes:
  - Increase the limit (persists in DB): `bench --site crm.localhost execute frappe.db.set_single_value --args '["System Settings", "rate_limit_email_link_login", 100]'`
  - Clear current Redis rate limit counter: `bench --site crm.localhost execute frappe.cache.delete_keys --args '["rl:"]'`

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
