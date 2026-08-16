TEST_COMPOSE = docker compose -p deepseek-harness-test -f compose.test.yml

.PHONY: test test-unit test-integration

test: test-unit test-integration

test-unit:
	cd backend && uv run pytest -q tests/unit

test-integration:
	@trap '$(TEST_COMPOSE) down -v --remove-orphans' EXIT; \
		$(TEST_COMPOSE) up --build --abort-on-container-exit --exit-code-from integration-tests integration-tests
