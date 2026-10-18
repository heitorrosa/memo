# memo dashboard (`ui/`)

Vite + React 19 + TypeScript + Tailwind v4 + `motion`, built as a TUI: mono only,
box-drawing, hairlines, no icon library, no web fonts, no CDN.

The built bundle lands in `../src/memo/static/ui` (gitignored) and the memo
server serves it at `/ui/`.

## Run

```sh
npm install --no-audit --no-fund
npm run dev        # http://localhost:5173/ui/ , /api proxied to :5000
npm run build      # tsc -b && vite build  ->  ../src/memo/static/ui
npm run preview    # serves the build on :4173 (also proxies /api)
```

## Auth

Every `/api/*` route needs `Authorization: Bearer $MEMO_TOKEN`. The dashboard
boots into a token gate; the token is stored in `localStorage["memo.token"]` and
can be handed over once as `?token=...` (it is then stripped from the address
bar). A 401 during the session drops the token and brings the gate back.

## Layout

```
┌ memo v3 · vec · 232 memories · 83 skills · 3 agents · ● connected   12:04:31  / search ? keys
├────────────────────────────────────────────────────────────────────────────────
│ ▌ ├─ MEMORIES  232 │  list ........................ │  detail / composer
│   ├─ SKILLS     83 │  ...
│   └─ APP         3 │
│ mouse optional     │
└────────────────────────────────────────────────────────────────────────────────
```

Rail (fixed 200px) / ascii stage band fed by `/api/graph` / the active pane.
Every pane renders exactly one of `loading ...` / `error: ...` / an empty line /
content — never a spinner.

`ui/src/ascii/**` is owned by another lane: this shell imports only `AsciiStage`
from `ui/src/ascii/index.ts`.

## Keys

`1..4` pane · `/` search · `j k` move · `g G` first/last · `enter` open ·
`esc` back · `r` refresh · `d` delete (inline `[y/N]`) · `n` new memory ·
`e` patch skill · `?` keymap. Typing inside a field never triggers them.
