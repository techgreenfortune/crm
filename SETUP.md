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
