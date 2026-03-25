.PHONY: setup run tunnel webhooks validate help docker-build docker-run docker-stop

help: ## Show this help
	@grep -E '^[a-z][a-z_-]+:.*## ' $(MAKEFILE_LIST) | \
		awk -F ':.*## ' '{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

setup: ## Create venv and install dependencies
	python3 -m venv .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -r requirements.txt
	@echo "\n  Activate with:  source .venv/bin/activate"

run: ## Start the dataloader (auto-reload)
	.venv/bin/uvicorn main:app --reload --host 127.0.0.1 --port 8000

tunnel: ## Start ngrok tunnel to localhost:8000
	@command -v ngrok >/dev/null 2>&1 || { echo "ngrok not found. Install: brew install ngrok"; exit 1; }
	@echo "Starting tunnel — copy the https:// URL into MT Dashboard → Webhooks"
	@echo "Or just open http://127.0.0.1:8000/listen after the tunnel starts.\n"
	ngrok http 8000

webhooks: ## Start app + tunnel side-by-side (requires tmux or two terminals)
	@echo "Run these in two terminals:"
	@echo "  make run      # Terminal 1 — start the app"
	@echo "  make tunnel   # Terminal 2 — start ngrok"
	@echo ""
	@echo "Then open http://127.0.0.1:8000/listen"

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
