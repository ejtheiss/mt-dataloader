.PHONY: setup run test tunnel webhooks validate help docker-build docker-run docker-stop docker-update regen-tokens-preview

help: ## Show this help
	@grep -E '^[a-z][a-z_-]+:.*## ' $(MAKEFILE_LIST) | \
		awk -F ':.*## ' '{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

setup: ## Create venv and install dependencies
	python3 -m venv .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -r requirements.txt
	@echo "\n  Activate with:  source .venv/bin/activate"

run: ## Start the dataloader (auto-reload)
	.venv/bin/uvicorn main:app --reload --host 127.0.0.1 --port 8000

test: ## Run pytest (same as CI)
	.venv/bin/python -m pytest tests/ -q

tunnel: ## Start ngrok tunnel (or use /listen in the UI)
	@echo "Tip: You can now manage the tunnel from http://127.0.0.1:8000/listen"
	@echo ""
	@command -v ngrok >/dev/null 2>&1 || { echo "ngrok not found. Install: brew install ngrok"; exit 1; }
	ngrok http 8000

webhooks: ## How to set up webhooks
	@echo "Open http://127.0.0.1:8000/listen to manage tunnel + webhooks from the UI."
	@echo ""
	@echo "Or manually in two terminals:"
	@echo "  make run      # Terminal 1 — start the app"
	@echo "  make tunnel   # Terminal 2 — start ngrok"

regen-tokens-preview: ## Write static/css/tokens.regen-preview.css from Mint tailwind (needs Node + config path)
	@command -v node >/dev/null 2>&1 || { echo "node not found"; exit 1; }
	node scripts/regen-tokens.js

validate: ## Validate all example configs
	.venv/bin/python - <<'PY'
	import json
	from models import DataLoaderConfig
	from engine import dry_run
	for p in ("examples/marketplace_demo.json", "examples/psp_minimal.json", "examples/staged_demo.json"):
	    with open(p) as f:
	        dry_run(DataLoaderConfig(**json.load(f)))
	    print(p, "OK")
	PY

docker-build: ## Build the Docker image
	docker compose build

docker-run: ## Start the dataloader in Docker
	docker compose up -d
	@echo "\n  Open http://localhost:8000"

docker-stop: ## Stop the Docker container
	docker compose down

docker-update: ## Pull latest and rebuild (runs/ data preserved)
	git pull
	docker compose build
	docker compose down && docker compose up -d
	@echo "\n  Updated. Open http://localhost:8000"
