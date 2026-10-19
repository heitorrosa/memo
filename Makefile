.PHONY: run r down d logs l ui ui-dev docker-build docker-run

IMAGE ?= ghcr.io/heitorrosa/memo:latest

run r:
	docker compose down --remove-orphans
	docker compose up -d --build
	docker logs -f memo-memo-1

down d:
	docker compose down --remove-orphans

logs l:
	docker logs -f memo-memo-1

ui:
	cd ui && (npm ci --no-audit --no-fund || npm install --no-audit --no-fund)
	cd ui && npm run build
	python -c "import shutil; shutil.copytree('ui/dist', 'src/memo/static/ui', dirs_exist_ok=True)"

ui-dev:
	cd ui && (npm ci --no-audit --no-fund || npm install --no-audit --no-fund)
	cd ui && npm run dev

docker-build:
	docker build -t $(IMAGE) .

docker-run:
	docker run -d --name memo -p 8080:8080 -v memo-data:/data -e MEMO_TOKEN=$$(sed -n 's/^MEMO_TOKEN=//p' .env) $(IMAGE)
