run r:
	docker compose down --remove-orphans
	docker compose up -d --build

down d:
	docker compose down --remove-orphans

logs l:
	docker logs -f memo-memo-1