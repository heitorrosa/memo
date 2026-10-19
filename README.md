<p align="center"><img src="assets/memo-ascii-text.png" width="240" alt="memo logo"></p>

<p align="center">Durable memory and reusable skills for every agent, with a terminal dashboard to see and edit it.</p>

## What it is

memo is an MCP server. One Python process owns one SQLite file (`memo.db`, WAL mode) and is the only writer to it. Agents call it over MCP and get eight tools: `memory_search`, `memory_save`, `memory_delete`, `skill_sync`, `skill_get`, `skill_put`, `skill_update`, `skill_install`. The image also ships a dashboard where you read and edit what it holds.

## Quickstart

The image ships the server and the dashboard. One command starts it:

```sh
docker run -d --name memo -p 8080:8080 -v memo-data:/data -e MEMO_TOKEN=change-me ghcr.io/heitorrosa/memo:latest
```

Then open <http://localhost:8080/ui/> and paste the token. The dashboard keeps it in the browser, so you type it once. The database lives in the `memo-data` volume and survives container restarts.

On Windows, `.\install.ps1` does the same thing with a generated token: it writes `.env`, pulls the image, starts the container on `./data` and prints the URL.

| command | what runs | published port |
| --- | --- | --- |
| `docker run <image>` | memo server and nginx | 8080 |
| `docker run <image> server` | memo server only | 5000 |
| `docker run <image> ui` | nginx only, proxying to `$MEMO_API` | 8080 |

In the default mode nginx is the public face. It serves `/ui/` and proxies `/api/`, `/mcp` and `/health` to the server on `127.0.0.1:5000`, so the dashboard and the API share one origin and need no CORS. The `ui` mode serves only the dashboard and proxies to another server, named by `MEMO_API` (default `http://127.0.0.1:5000`):

```sh
docker pull ghcr.io/heitorrosa/memo:latest
docker run -d --name memo --restart unless-stopped -p 8080:8080 -v ./data:/data -e MEMO_TOKEN=<from .env> ghcr.io/heitorrosa/memo:latest
```

Never run two server processes against the same database: memo is the single writer, and the server refuses to start when another process holds the file.

## Dashboard

The dashboard is a terminal-style app (Vite, React, TypeScript, Tailwind) served at `/ui/`. It has four panes: MEMORIES, SKILLS, AGENTS, APP. The band above them draws the memory graph: the edges you declared plus the correlations the server computes between memories.

| key | action |
| --- | --- |
| `j` `k` | move |
| `enter` | open |
| `n` | new memory |
| `d` | delete, with a confirmation |
| `e` | patch a skill |
| `/` | search |
| `?` | key map |

The app calls the server under `/api/*` with the bearer token: `/api/stats`, `/api/memories`, `/api/skills`, `/api/agents`, `/api/graph`. Read the source in [`ui/`](ui/README.md).

## MCP clients

Point an agent at the server with a bearer token. With the default container the endpoint is `http://localhost:8080/mcp`:

```json
{ "mcp": { "memo": { "type": "remote", "url": "http://localhost:8080/mcp",
  "headers": { "Authorization": "Bearer ${MEMO_TOKEN}" } } } }
```

The OpenCode plugin in [`opencode-memo/`](opencode-memo/README.md) reads `MEMO_ENDPOINT` (default `http://localhost:8000/mcp`) and `MEMO_TOKEN`. Clients that cannot set headers can pass `?access_token=<token>` on the `/mcp` URL; the header wins when both are present, and URL tokens leak into proxy and browser logs. Web harnesses (ChatGPT, Claude.ai) take [`INSTRUCTIONS.md`](INSTRUCTIONS.md) in their system prompt instead.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `MEMO_TOKEN` | required for HTTP mode | Bearer token for `/mcp` and `/api/*`. Send `Authorization: Bearer <token>`, or `?access_token=<token>` on the `/mcp` URL for clients that cannot set headers. The header wins when both are present. |
| `MEMO_DB` | `/data/memo.db` | Path of the SQLite file inside the container. |
| `MEMO_UI_DIR` | the dashboard baked in the image | Directory the server serves at `/ui/`. |
| `MEMO_API` | `http://127.0.0.1:5000` | Upstream server the `ui` mode proxies to. |
| `MEMO_ENDPOINT` | `http://localhost:8000/mcp` | Server URL used by the OpenCode plugin. |
| `MEMO_LOG` | unset | Set `debug` to log maintenance passes. |

On the host the CLI takes the same paths as flags: `memo --db <path>`, with `--http --port <port>`, `--health <url>`, `--backup <file>` and `--dump-tools`. The optional stdio transport is for offline or CI use.

## The model

Search embeds text with `sentence-transformers/all-MiniLM-L6-v2`: 384 dimensions, loaded by fastembed. The weights are baked into the image, so a plain `docker run` needs no mount. A `/models` bind mount still overrides them.

To pre-fetch the weights for a run outside Docker:

```sh
python scripts/fetch_model.py
```

It writes `./models`. Mount that directory at `/models` to use it in a container.

## Maintenance

`scripts/backfill_graph.py` fills in edges and aliases for memories saved before the graph writer existed. It reports and changes nothing unless you pass `--apply`:

```sh
python scripts/backfill_graph.py data/memo.db          # report only
python scripts/backfill_graph.py data/memo.db --apply  # write
```

Stop the memo container before you apply. On a Windows bind mount a host write while the container holds `memo.db` open breaks the container's SQLite handle, and every write endpoint then answers 500 `unable to open database file`. The script is idempotent: a second `--apply` adds nothing.

## Development

```sh
pip install -e ".[dev]"  # runtime and pytest
make ui                  # build the dashboard into src/memo/static/ui
make run                 # docker compose up -d --build, then logs
make docker-build        # build the image
pytest tests/ -q
```

### Layout

- `src/memo/server/api/` - the dashboard HTTP layer (FastAPI): `__init__` builds the app, `models` holds the pydantic models, and `memories`, `skills`, `agents`, `graph`, `stats` register their own routes.
- `src/memo/server/http.py` - the bearer wrapper, `/health`, the `/ui` mount and the MCP mount.
- `src/memo/memory/`, `src/memo/skills/`, `src/memo/models/` - the domain: writing, searching, the graph and the skill store.
- `ui/` - the dashboard (Vite, React, TypeScript, Tailwind); `ui/src/ascii/` draws the graph.
- `scripts/` - `backfill_graph.py` fills the edges and aliases of older memories, `fetch_model.py` pre-fetches the weights.

## License

GPL-3.0-only. See [`LICENSE`](LICENSE).
