# `ui/src/ascii` — animated ASCII memory graph

The screenshot-piece: the memory graph drawn as living ASCII inside a `<pre>`.

## Surface

```ts
export type AsciiNode = { key: string; type: string; status: string; uses: number; score: number; degree: number };
export type AsciiEdge = { src_key: string; dst_key: string; rel: string; weight: number };
export type AsciiLink = { src_key: string; dst_key: string; score: number };
export function AsciiStage(props: { nodes: AsciiNode[]; edges: AsciiEdge[]; similar?: AsciiLink[]; focus?: string | null; cols?: number; rows?: number; fps?: number; className?: string; onSelect?: (key: string) => void }): ReactElement;
export function AsciiBanner(props: { text?: string; className?: string }): ReactElement;
```

That is the whole contract. Nothing here imports another package or another
repo file: `react` only, plus these files.

## Files

- `grid.ts` — dependency-free, pure. `layout()` (one 2D force-directed pass
  per payload), hash seeds, Bresenham-ish line sampling, node glyphs, labels,
  `buildFrame(nodes, edges, cols, rows, phase, opts)`. `phase` only moves a
  brightness band; same payload, same frame.
- `Stage.tsx` — one `requestAnimationFrame` loop, redraws at `fps` (default 12),
  `phase` advances with time. `layout()` is memoised on the payload signature
  (node count, edge count, link count, key hash), never on `phase` or `focus`.
- `Banner.tsx` — a self-contained 5-row block font, typewriter reveal then a
  single shimmer sweep, then it stops.

## The emblem, twice

`LOGO_MASK` (`logo-mask.ts`, 66x37) is the repo mark. It appears in two places.

- **Gate** — `LogoArt.tsx` animates it (`renderFrame`), full viewport.
- **Sidebar** — `components/SidebarEmblem.tsx` is a static `<pre>`, no props and
  no rAF, drawn at **28x10** (`text-blue/70`). At that size a nearest-neighbour
  sample loses the outline, so each output cell is the **block max** of the
  ~2.36 x 3.7 mask block it covers: `#` if any block cell is `#`, else `+` if
  any is `+`, else a space. The silhouette survives; the interior thins.

Change the mask and both follow — nothing is hand-copied per size.

## How it draws

`buildFrame` returns `{ text, overlays, hits, dim }`.
`text` is the **entire** grid, written into one `<pre>` with `textContent` —
never a React node per cell. `overlays` are same-size layers holding only the
cells of one colour role (spaces elsewhere); each is written into its own
absolutely-positioned `<pre>`, so a cell is painted twice at most and colour
costs no per-cell DOM. `hits` lists the screen cell of each real node, used for
the invisible focusable hit-boxes when `onSelect` is passed.

- Node glyph is **degree-sized**, coloured by type (amber/violet/coral/blue):
  a hub (degree ≥ 4) is a 2x2 block of `█` if all four cells are free, else `●`;
  degree 2-3 is `●`, degree 1 is `•`, degree 0 is `·`. Superseded and
  out-of-focus nodes drop to a faint `.`. A hub is obvious from three metres.
- **Labels on hubs** (degree ≥ 3), in `faint`, one row, one space to the right
  of the glyph: the tail of the key after the last dot, truncated to the frame.
  A label is claimed whole or skipped whole — and only against node cells, so
  labels never overwrite a glyph but do win over a strand.
- Declared edges: `-` `|` `/` `\` by slope, in the relation's role colour,
  dashed at `+` where they cross. Similarity strands: dotted (step 2) `-` in
  faint, at most 2 per node and only between nodes closer than `rows` cells.
- Drawing order: any backdrop, then declared edges, then similar strands, then
  nodes, then labels. Agency is explicit in that order: a dot always wins the
  cell, and a label only ever loses to a dot.

### Where nodes sit

`layout(nodes, edges, similar)` returns a 2D point per key in cell space,
seeded from `seedPos(key)` (deterministic hash → jittered type-seeded
quadrants) and relaxed once with Fruchterman-Reingold: every pair closer than
`CUTOFF · k` repels (`k²/d`), every link pulls (`d²/k · w · PULL`), each step is
capped by a cooling temperature, and every node is held near its seed
(`ANCHOR`). Link weight is `score · 0.9` for a `similar` pair and `1.6` for a
declared edge, so declared edges stay the strongest relation.

The pass is bounded — `ITERS` cooling steps then `SETTLE` at a fixed low
temperature — and then the settled cloud is **normalised onto the pane**: a
per-axis min/max fit to `[1, cols-2] x [0, rows-1]`, which is what fills the
frame instead of leaving a centred ellipse with dead margins. An axis where
every node sits on one line is centred rather than stretched.

Two calibrations keep a 243x22 frame readable (fixture: 137 nodes, 245
block-local links, 4 declared edges):

- `SIMILAR_PER_NODE` (2): every node keeps at most its top-2 links by score
  (undirected pairs count once), so at most ~n strands exist no matter how many
  links the server sends.
- `LINE_RATIO` (2.5) for declared edges, `rows` for similarity: a strand longer
  than that crosses the frame and reads as noise. Links are local by construction,
  so the mean linked span stays far below the mean span of an arbitrary pair
  (frame-check: 12 cells vs 73).

The layout is a **frozen chart**: `phase` (the 5th argument, named for the
signature it kept) drives only the brightness band, `layout()` never re-runs
per frame, and nothing is re-centred per redraw. No `Math.random`, no time
input: `frame-check` diffs the frame between phases and expects 0 changed
cells.

### The animation

Position is data, so position never moves. `phase` slides a soft diagonal band
across the frame (`BAND_HALF` cells either side): cells inside it are lifted one
step in the role ladder (faint → amber, blue → green), which reads as a slow
sweep over a still picture.

### Focus

`buildFrame(..., { focus })`: the focus node, its `similar` neighbours and its
declared neighbours draw at full strength, the focus node's own strands draw
solid, and every other node becomes a `faint` `.` with no strands. Unset or
unknown focus draws everything. This is what turns a dot field into "this
memory, and what it is close to".

### Three rules that keep it legible

1. **Strands are rare and short.** Candidate `similar` pairs are ranked by
   score, deduped undirected, and capped at 2 per node (`SIMILAR_PER_NODE`); a
   strand must be shorter than `rows` cells, a declared edge than
   `LINE_RATIO x rows`. The length caps and the per-node tally are what bound
   the ink — no shared cell budget.
2. **Occupy-once, then dots win.** Lines claim cells first-come-first-served;
   nodes are written after them, then labels. A label must own every cell it
   touches, so a hub next to another node simply goes unlabelled (frame-check:
   111/128 at 243x22). Node cells are de-duplicated: a node that lands on a
   taken cell walks a spiral of up to 3 rings to the nearest free one, so a
   cluster never hides a node, and `hits` stays unique per key.
3. **Ink hierarchy.** Lines ~40% of the ink, nodes and labels ≥ 45% (measured
   57% on the fixture), labels in `faint` so they never compete with the dots
   they describe.

## Degrading honestly

- **Sparse mode (only when nothing survives).** When neither a declared edge
  nor a similarity strand survives selection and the length cap — 0 nodes, no
  data, or everything filtered out — the frame gets a faint **static backdrop
  texture**:
  hash-seeded `:` hairlines every 12 columns plus scattered `.` dust. It is
  **texture, not data**: it never varies with `edges` or `phase`, it does not
  claim cells, and it invents no node-to-node links. A decorative graph would be
  a lie about the data, so it is drawn as something obviously not a relation,
  and the real nodes and strands read bright on top of it. `Frame.sparse` says
  whether it was on.
- 0 nodes → a deterministic generated field of 44 pseudo-nodes that still breathes.
- `similar` absent or empty, or fewer than 3 nodes → `layout` returns the hash
  seeds untouched, so an old server draws exactly the frame it drew before.
- > 200 nodes → the top 200 by degree, then uses; edges survive only if both ends do.
- `cols < 8` or `rows < 4` → a single dim text line, `dim: true`, no hits.
- `prefers-reduced-motion: reduce` → one static frame, no loop.
- `document.hidden` → the loop is cancelled and resumes on visibility.
- `aria-hidden="true"` on every `<pre>`; when `onSelect` is passed a keyboard-
  reachable (but invisible) hit-box overlay is rendered instead.

No emoji. ASCII and box-drawing glyphs only.

## Limits

- Node colour is layered, not per-cell inline CSS; stacking order is the
  `OVERLAY_ROLES` order in `grid.ts`.
- Hit-boxes refresh every 3rd redraw (~4/s) — they can lag a layout change by
  one beat but never jitter.
- `bannerRows` covers `A–Z 0–9 space - . : /`; anything else renders blank.

