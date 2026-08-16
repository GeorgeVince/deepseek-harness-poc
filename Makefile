COMPOSE = docker compose
TEST_COMPOSE = $(COMPOSE) -p deepseek-harness-test -f compose.test.yml

.PHONY: start stop restart rebuild logs status test test-unit test-integration

start:
	$(COMPOSE) up -d --build

stop:
	$(COMPOSE) down

restart:
	$(COMPOSE) restart

rebuild:
	$(COMPOSE) up -d --build --force-recreate

logs:
	$(COMPOSE) logs -f

status:
	$(COMPOSE) ps

test: test-unit test-integration

test-unit:
	cd backend && uv run pytest -q tests/unit

test-integration:
	@trap '$(TEST_COMPOSE) down -v --remove-orphans' EXIT; \
		$(TEST_COMPOSE) up --build --abort-on-container-exit --exit-code-from integration-tests integration-tests
