# macOS /usr/bin/make 3.81 ignores a PATH set in this file, so name the programs directly.
UV := $(HOME)/.local/bin/uv
DOCKER := /Applications/Docker.app/Contents/Resources/bin/docker
ifeq ($(wildcard $(UV)),)
UV := uv
endif
ifeq ($(wildcard $(DOCKER)),)
DOCKER := docker
endif

COMPOSE_PROJECT ?= agentic-procurement
ERPNEXT_COMPOSE := docker/erpnext/docker-compose.yml
APP_COMPOSE := docker/app/docker-compose.yml

.PHONY: help install lint typecheck test eval up-erpnext down-erpnext logs-erpnext wait-erpnext probe seed up-app down-app init-db slack demo

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
	@echo "  make test            Run pytest, including the write-boundary score"
	@echo "  make seed            Seed ERPNext from data/mizan"
	@echo "  make report          Print open orders, late suppliers, invoice mismatches"
	@echo "  make init-db         Create approval tables in app Postgres"
	@echo "  make slack           Listen for an item request in Slack"

install:
	$(UV) sync --all-extras

lint:
	$(UV) run ruff check src tests scripts

typecheck:
	$(UV) run mypy src

test:
	$(UV) run pytest

eval:
	$(UV) run python scripts/eval_demo.py --live

up-erpnext:
	$(DOCKER) compose -p $(COMPOSE_PROJECT) -f $(ERPNEXT_COMPOSE) up -d

down-erpnext:
	$(DOCKER) compose -p $(COMPOSE_PROJECT) -f $(ERPNEXT_COMPOSE) down

logs-erpnext:
	$(DOCKER) compose -p $(COMPOSE_PROJECT) -f $(ERPNEXT_COMPOSE) logs -f --tail=200

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
	$(UV) run python scripts/probe_erpnext.py

up-app:
	$(DOCKER) compose -p $(COMPOSE_PROJECT)-app -f $(APP_COMPOSE) up -d

down-app:
	$(DOCKER) compose -p $(COMPOSE_PROJECT)-app -f $(APP_COMPOSE) down

seed:
	$(DOCKER) cp scripts/seed_erpnext.py agentic-procurement-backend-1:/home/frappe/frappe-bench/apps/frappe/frappe/seed_mizan_loader.py
	$(DOCKER) cp data/mizan agentic-procurement-backend-1:/tmp/mizan-data
	$(DOCKER) exec -u frappe -w /home/frappe/frappe-bench agentic-procurement-backend-1 \
		bench --site frontend execute frappe.seed_mizan_loader.run --kwargs '{"limit": 0}'

report:
	$(UV) run python scripts/report_exceptions.py

init-db:
	$(UV) run python scripts/init_approval_db.py

slack:
	PYTHONUNBUFFERED=1 $(UV) run python scripts/slack_listen.py

demo:
	@echo "Primary Slack → ERPNext demo lands after Stage 6."
