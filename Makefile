run r:
	docker compose down --remove-orphans
	docker compose up -d --build
	docker logs -f memo-memo-1

down d:
	docker compose down --remove-orphans

logs l:
	docker logs -f memo-memo-1