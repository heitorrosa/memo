# Every target is a recipe, not a file. Without this, a directory named ui/
# makes `make ui` report "'ui' is up to date" and skip the build.
.PHONY: run r down d logs l ui ui-dev docker-build docker-run

# The published image (see .github/workflows/docker-publish.yml).
IMAGE ?= ghcr.io/heitorrosa/memo:latest

run r:
	docker compose down --remove-orphans
	docker compose up -d --build
	docker logs -f memo-memo-1

down d:
	docker compose down --remove-orphans

logs l:
	docker logs -f memo-memo-1

# Build the dashboard into ui/dist and copy it to src/memo/static/ui, the
# path src/memo/server/static_ui.py looks at (gitignored).
ui:
	cd ui && (npm ci --no-audit --no-fund || npm install --no-audit --no-fund)
	cd ui && npm run build
	mkdir -p src/memo/static/ui && cp -r ui/dist/. src/memo/static/ui/

ui-dev:
	cd ui && (npm ci --no-audit --no-fund || npm install --no-audit --no-fund)
	cd ui && npm run dev

docker-build:
	docker build -t $(IMAGE) .

# The one-liner for a fresh install: the dashboard and the API on :8080.
docker-run:
	docker run -d --name memo -p 8080:8080 -v memo-data:/data -e MEMO_TOKEN=$$(sed -n 's/^MEMO_TOKEN=//p' .env) $(IMAGE)
