# memo graph viewer 3d

An immersive 3D view of a memo database. Read-only and fully local: the picked
`.db` file is parsed in the browser by sql.js (wasm shipped with the bundle, no
CDN), and nothing is uploaded or transmitted anywhere.

`tools/graph-view.html` remains the instant-load 2D view; this app is the
immersive one and shares the same data semantics (type colours, uses-scaled
sizes, relation colours, red dashed `contradicts`, dotted `same_as` aliases,
gray `superseded` nodes, diamond virtual entities).

## Run

```bash
npm install
npm run dev            # http://localhost:5173
npm run build          # tsc -b && vite build  -> dist/
npm run preview        # serve dist/
```

Load a database with the file picker in the left panel. Always point it at a
**copy** of `data/memo.db`, never the live file.

## Headless / scripted load

`?db=<relative path>` loads a copy the app serves, which is how the automated
screenshot pass drives it:

```bash
npm run build
npm run seed-demo -- ../data/memo.db dist/demo.db   # never the live file
npm run preview -- --port 4173
# then open http://localhost:4173/?db=demo.db
```

`scripts/seed-demo.mjs` copies the source database and adds a handful of edges
and aliases so the 3D scene has something to draw; it never writes to the file
you pass in.

## Stack

| Purpose | Choice |
| --- | --- |
| scaffold | vite + react + typescript |
| 3D | three, @react-three/fiber, @react-three/drei |
| db | sql.js (wasm emitted by vite as an asset) |
| layout | hand-rolled force layout in `src/layout.ts` (no d3) |
| hud | tailwind v4 + lucide-react + motion |
| command bar | @xterm/xterm + @xterm/addon-fit |

Deliberately not used: postprocessing, lenis (the page is a single viewport and
never scrolls), gsap, shadcn/magic-ui/aceternity registries.

## Command bar

```
help                        command list
focus <text>                focus the first node whose key or value contains <text>
filter rel:<rel> [on|off]   toggle a relation, e.g. filter rel:contradicts off
ns <name|*>                 namespace filter
clear                       drop the focus
info                        current counts
```

## Notes

- The force layout is deterministic (seeded PRNG), so the same graph always
  produces the same picture. Node count is capped at 1500 by top `uses`; larger
  graphs are sampled and the readout says so.
- Only the focused node gets a text label. Labelling every node would kill the
  frame rate.
- The queries are column-tolerant in the same way `tools/graph-view.html` is:
  missing `namespace` columns and missing `edges`/`aliases` tables fall back to
  sensible defaults instead of throwing.
