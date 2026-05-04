#!/bin/bash

if [ -d "/home/frappe/frappe-bench/apps/frappe" ]; then
    echo "Bench already exists, checking setup..."
    cd /home/frappe/frappe-bench

    # Migrate: if apps/crm is not a symlink to /workspace, replace it
    if [ ! -L "apps/crm" ] || [ "$(readlink apps/crm)" != "/workspace" ]; then
        echo "Setting up apps/crm symlink to /workspace..."
        rm -rf apps/crm
        ln -s /workspace apps/crm
        /home/frappe/frappe-bench/env/bin/pip install -q -e /home/frappe/frappe-bench/apps/crm
    fi

    # Ensure crm is registered in bench apps.txt (preserve other installed apps)
    grep -qxF 'crm' sites/apps.txt 2>/dev/null || printf 'crm\n' >> sites/apps.txt

    # Install crm on the site if not already installed
    if ! bench --site crm.localhost list-apps 2>/dev/null | grep -q "^crm$"; then
        echo "CRM app not installed, installing..."
        bench --site crm.localhost install-app crm
        bench --site crm.localhost set-config developer_mode 1
        bench --site crm.localhost set-config mute_emails 1
        bench --site crm.localhost set-config server_script_enabled 1
        bench --site crm.localhost clear-cache
    fi

    echo "Starting bench..."
    bench start
    exit 0
fi

echo "Creating new bench..."

bench init --skip-redis-config-generation frappe-bench --version version-15
cd /home/frappe/frappe-bench

bench set-mariadb-host mariadb
bench set-redis-cache-host redis://redis:6379
bench set-redis-queue-host redis://redis:6379
bench set-redis-socketio-host redis://redis:6379

# Remove redis from Procfile (runs in separate container)
sed -i '/redis/d' ./Procfile
sed -i '/watch/d' ./Procfile

ln -s /workspace apps/crm
/home/frappe/frappe-bench/env/bin/pip install -e /home/frappe/frappe-bench/apps/crm

# Register crm in bench apps.txt (required by install-app)
printf 'frappe\ncrm\n' > sites/apps.txt

bench new-site crm.localhost \
    --force \
    --mariadb-root-password 123 \
    --admin-password admin \
    --no-mariadb-socket

bench --site crm.localhost install-app crm
bench --site crm.localhost set-config developer_mode 1
bench --site crm.localhost set-config mute_emails 1
bench --site crm.localhost set-config server_script_enabled 1
bench --site crm.localhost clear-cache
bench use crm.localhost

bench start
