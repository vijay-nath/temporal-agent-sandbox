# Developer UX. Run inside WSL2 Ubuntu (see README). One command per lifecycle verb.
COMPOSE := docker compose -f deploy/docker-compose.yml
SANDBOX_IMAGE := sandbox-runner:pinned

.DEFAULT_GOAL := help
.PHONY: help setup-gvisor sandbox-image build up down logs ps test test-unit demo lint fmt lock

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

setup-gvisor: ## Install + register the pinned gVisor (runsc) runtime in the Docker daemon
	sudo bash deploy/gvisor/install-runsc.sh

sandbox-image: ## Build the minimal untrusted-code sandbox image
	docker build -t $(SANDBOX_IMAGE) app/sandbox/runtime

build: sandbox-image ## Build all images (app + sandbox)
	$(COMPOSE) build

up: sandbox-image ## Start the full local stack (api, worker, temporal, ui, otel, langfuse)
	$(COMPOSE) up -d --build

down: ## Stop the stack and remove volumes
	$(COMPOSE) down -v

logs: ## Tail logs for api + worker
	$(COMPOSE) logs -f api worker

ps: ## Show stack status
	$(COMPOSE) ps

test: ## Run the full test suite (needs Docker + sandbox image for integration tests)
	pytest

test-unit: ## Run unit tests only (no Docker required)
	pytest -m "not integration"

demo: ## Submit a sample run and stream its status
	bash scripts/demo.sh

lint: ## Lint
	ruff check app tests

fmt: ## Format / autofix
	ruff check --fix app tests
	ruff format app tests

lock: ## Regenerate the hash-pinned lockfile (requires uv + network)
	uv lock
