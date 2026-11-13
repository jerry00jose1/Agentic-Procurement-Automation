export PATH := $(HOME)/.local/bin:/Applications/Docker.app/Contents/Resources/bin:$(PATH)

COMPOSE_PROJECT ?= agentic-procurement
ERPNEXT_COMPOSE := docker/erpnext/docker-compose.yml
APP_COMPOSE := docker/app/docker-compose.yml

.PHONY: help install lint typecheck test up-erpnext down-erpnext logs-erpnext wait-erpnext probe seed up-app down-app init-db slack demo

help:
	@echo "Agentic Procurement Automation"
	@echo ""
	@echo "  make install         Install Python deps with uv"
	@echo "  make up-erpnext      Start official ERPNext Docker stack (port 8080)"
	@echo "  make wait-erpnext    Wait until ERPNext HTTP responds"
	@echo "  make probe           Probe ERPNext REST API and document schemas"
	@echo "  make logs-erpnext    Tail ERPNext compose logs"
	@echo "  make down-erpnext    Stop ERPNext stack (keeps volumes)"
	@echo "  make up-app          Start app Postgres (pgvector)"
	@echo "  make down-app        Stop app Postgres"
	@echo "  make lint            Run ruff"
	@echo "  make typecheck       Run mypy"
	@echo "  make test            Run pytest"
	@echo "  make seed            Seed ERPNext from data/mizan"
	@echo "  make report          Print open orders, late suppliers, invoice mismatches"
	@echo "  make init-db         Create approval tables in app Postgres"
	@echo "  make slack           Listen for an item request in Slack"

install:
	uv sync --all-extras

lint:
	uv run ruff check src tests scripts

typecheck:
	uv run mypy src

test:
	uv run pytest

up-erpnext:
	docker compose -p $(COMPOSE_PROJECT) -f $(ERPNEXT_COMPOSE) up -d

down-erpnext:
	docker compose -p $(COMPOSE_PROJECT) -f $(ERPNEXT_COMPOSE) down

logs-erpnext:
	docker compose -p $(COMPOSE_PROJECT) -f $(ERPNEXT_COMPOSE) logs -f --tail=200

wait-erpnext:
	@echo "Waiting for ERPNext on http://localhost:8080 ..."
	@i=0; \
	until curl -sf -o /dev/null http://localhost:8080; do \
	  i=$$((i+1)); \
	  if [ $$i -ge 90 ]; then echo "ERPNext did not become ready in time"; exit 1; fi; \
	  sleep 5; \
	done; \
	echo "ERPNext is responding on http://localhost:8080"

probe:
	uv run python scripts/probe_erpnext.py

up-app:
	docker compose -p $(COMPOSE_PROJECT)-app -f $(APP_COMPOSE) up -d

down-app:
	docker compose -p $(COMPOSE_PROJECT)-app -f $(APP_COMPOSE) down

seed:
	docker cp scripts/seed_erpnext.py agentic-procurement-backend-1:/home/frappe/frappe-bench/apps/frappe/frappe/seed_mizan_loader.py
	docker cp data/mizan agentic-procurement-backend-1:/tmp/mizan-data
	docker exec -u frappe -w /home/frappe/frappe-bench agentic-procurement-backend-1 \
		bench --site frontend execute frappe.seed_mizan_loader.run --kwargs '{"limit": 0}'

report:
	uv run python scripts/report_exceptions.py

init-db:
	uv run python scripts/init_approval_db.py

slack:
	PYTHONUNBUFFERED=1 uv run python scripts/slack_listen.py

demo:
	@echo "Primary Slack → ERPNext demo lands after Stage 6."
