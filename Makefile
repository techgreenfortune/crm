CONTAINER=crm-frappe-1

up:
	cd docker && docker compose up -d

stop:
	cd docker && docker compose stop

down:
	cd docker && docker compose down

reset:
	cd docker && docker compose down && docker compose up -d

logs:
	cd docker && docker compose logs -f frappe

shell:
	docker exec -it $(CONTAINER) bash

migrate:
	docker exec $(CONTAINER) bash -c "cd /home/frappe/frappe-bench && bench --site crm.localhost migrate"

clear-cache:
	docker exec $(CONTAINER) bash -c "cd /home/frappe/frappe-bench && bench --site crm.localhost clear-cache"

dev:
	docker exec -it $(CONTAINER) bash -c \
		"source /home/frappe/.nvm/nvm.sh && \
		 cd /home/frappe/frappe-bench/apps/crm/frontend && \
		 yarn install && yarn dev --host 0.0.0.0"

build-frontend:
	docker exec $(CONTAINER) bash -c \
		"source /home/frappe/.nvm/nvm.sh && \
		 cd /home/frappe/frappe-bench/apps/crm/frontend && \
		 yarn install && yarn build"

watch-frontend:
	docker exec -it $(CONTAINER) bash -c \
		"source /home/frappe/.nvm/nvm.sh && \
		 cd /home/frappe/frappe-bench/apps/crm/frontend && \
		 yarn build --watch"

# --- Local bench targets ---

BENCH_DIR ?= $(HOME)/frappe-bench
CRM_FRONTEND ?= $(HOME)/projects/crm/frontend

bench-start:
	cd $(BENCH_DIR) && bench start

bench-migrate:
	cd $(BENCH_DIR) && bench --site crm.localhost migrate

bench-clear-cache:
	cd $(BENCH_DIR) && bench --site crm.localhost clear-cache

bench-dev:
	cd $(CRM_FRONTEND) && yarn install && yarn dev

bench-build-frontend:
	cd $(BENCH_DIR) && bench build --app crm
