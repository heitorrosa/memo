# Every target is a recipe, not a file. Without this, a directory named ui/
# makes `make ui` report "'ui' is up to date" and skip the build.
.PHONY: run r down d logs l ui ui-dev

run r:
	docker compose down --remove-orphans
	docker compose up -d --build
	docker logs -f memo-memo-1

down d:
	docker compose down --remove-orphans

logs l:
	docker logs -f memo-memo-1

# Build the dashboard into src/memo/static/ui (vite's outDir, gitignored).
ui:
	cd ui && (npm ci --no-audit --no-fund || npm install --no-audit --no-fund)
	cd ui && npm run build

ui-dev:
	cd ui && (npm ci --no-audit --no-fund || npm install --no-audit --no-fund)
	cd ui && npm run dev
