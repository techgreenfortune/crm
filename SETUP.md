# CRM Dev Setup

Running environment: **Local bench** (native macOS), MariaDB + Redis via Homebrew.  
Site: `crm.localhost` | Bench: `~/frappe-bench`  
Project root: `~/projects/crm` (symlinked into bench as `~/frappe-bench/apps/crm`)

---

## Prerequisites

- Node.js + Yarn
- Homebrew

---

## One-Time Setup (already done)

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
bench use crm.localhost
```

---

## Daily Workflow

```bash
# Ensure Homebrew services are running
brew services start mariadb@11.8
brew services start redis

# Start bench (web + worker + scheduler)
make bench-start
```

Open `http://crm.localhost:8000/crm` — login: `Administrator` / `admin`

---

## Frontend Dev Server (hot-reload)

In a second terminal:

```bash
make bench-dev
```

Open `http://localhost:8080/crm` — Vite proxies API calls to bench at port 8000. Changes to `.vue` files under `frontend/src/` hot-reload instantly.

---

## Makefile Commands

| Command | What it does |
|---------|-------------|
| `make bench-start` | Start bench (web + workers + scheduler) |
| `make bench-migrate` | Run `bench migrate` on `crm.localhost` |
| `make bench-clear-cache` | Clear Frappe site cache |
| `make bench-dev` | Start Vite dev server locally (port 8080) |
| `make bench-build-frontend` | Build Vue bundle into `crm/public/frontend/` |

---

## Code Changes

### Backend (Python / DocType JSON)

`~/frappe-bench/apps/crm` is a symlink to `~/projects/crm` — edits are live immediately.

After changing a DocType JSON or adding migrations:
```bash
make bench-migrate
```

Python changes are picked up by Frappe's watchdog auto-reloader. If not, restart:
```bash
cd ~/frappe-bench && bench restart
```

### Frontend (Vue / JS)

Run `make bench-dev` and edit files under `frontend/src/` — HMR updates the browser instantly.

To build the production bundle:
```bash
make bench-build-frontend
```

---

## Resetting from Scratch

```bash
# Drop and recreate the site
cd ~/frappe-bench
bench drop-site crm.localhost --mariadb-root-password '' --force
echo "" | bench new-site crm.localhost --mariadb-root-password '' --admin-password admin
bench --site crm.localhost install-app crm
bench --site crm.localhost set-config developer_mode 1
bench --site crm.localhost set-config server_script_enabled 1
bench use crm.localhost
```

---

## Integrations

### Brevo (Transactional Email)

Routes transactional emails via Brevo's HTTP API. When disabled, falls back to `frappe.sendmail`.

**Setup:**
1. Get a v3 API key from Brevo → Profile → SMTP & API → API Keys (starts with `xkeysib-`)
2. Verify your sender domain in Brevo → Senders & IPs
3. In CRM: Settings → Integrations → Brevo → Enable
4. Enter API Key, Sender Email, Sender Name → **Update**
5. Click **Send Test Email** to verify delivery

**Gotchas:**
- `toast({ title, variant })` is wrong in this frappe-ui version — use `toast.success()`, `toast.error()`, `toast.warning()`
- `session.user` returns the login name, not an email — use `getUser()?.email` from `usersStore` for the recipient address
- `__()` (Frappe i18n) is a Vue template global only — use plain strings inside `<script setup>`
