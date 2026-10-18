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

- `grid.ts` — dependency-free, pure. `layout()` (similarity-driven positions on
  the unit sphere), hash seeds, sphere → grid projection, Bresenham-ish edge
  sampling, glyph selection, `buildFrame(nodes, edges, cols, rows, angle, opts)`.
  Same args, same frame.
- `Stage.tsx` — one `requestAnimationFrame` loop, redraws at `fps` (default 12),
  angle advances `0.21 rad/s`. `layout()` is memoised on the payload signature
  (node count, edge count, link count, key hash), never on `angle` or `focus`.
- `Banner.tsx` — a self-contained 5-row block font, typewriter reveal then a
  single shimmer sweep, then it stops.

## How it draws

`buildFrame` returns `{ text, overlays, hits, dim }`.
`text` is the **entire** grid, written into one `<pre>` with `textContent` —
never a React node per cell. `overlays` are same-size layers holding only the
cells of one colour role (spaces elsewhere); each is written into its own
absolutely-positioned `<pre>`, so a cell is painted twice at most and colour
costs no per-cell DOM. `hits` lists the screen cell of each real node, used for
the invisible focusable hit-boxes when `onSelect` is passed.

- Node glyph: `P/A/F/C` by memory type at degree ≥ 3, else `o` / `+` / `.` by
  prominence (below). Colour by type (amber/violet/coral/blue); superseded
  nodes go faint.
- Edge glyph: `-` `|` `/` `\` by slope; `contradicts` edges mark every 4th cell
  `x` in coral, `supports` green, `derived_from` violet, `mentions` faint.
- Depth sorting: far edges and far nodes are drawn first.

### Where nodes sit

`layout(nodes, edges, similar)` returns a 3D point per key on the unit sphere,
seeded from `seedPos(key)` and relaxed with Fruchterman-Reingold: every pair
repels (`k²/d`), every link pulls (`d²/k · w · PULL`), each step is capped by a
cooling temperature, and every point is renormalised to the sphere. Link weight
is `score` for a `similar` pair and `1` for a declared edge, so declared edges
stay the strongest relation. `PULL` exists because n² repulsion otherwise outguns
a unit pull and clusters smear across the frame.

Two calibrations keep the 96×14 grid readable:

- `SIMILAR_RANGE` (0.25 rad): strands longer than this are not drawn. They cross
  the frame and read as noise. Focus-incident strands are exempt.
- Neighbour pairs therefore land a few cells apart, and clusters separate
  (fixture: mean intra 0.30 vs cross 1.15 in 3D).

No `Math.random`, no time input. `hits`, `dim` and the overlay contract are
unchanged. `Stage.tsx` memoises the result on the payload signature.

### Focus

`buildFrame(..., { focus })`: the focus node, its `similar` neighbours and its
declared neighbours draw at full strength, the focus node's own strands draw
solid, and every other node becomes a `faint` `.` with no strands. Unset or
unknown focus draws everything. This is what turns a dot field into "this
memory, and what it is close to".

### Three rules that keep it legible

1. **Budget by cells, not by node count.** Strands may claim at most
   `cols * rows * 0.20` cells, spent in `weight` desc order for declared edges
   and then in `score` desc order for `similar` strands, from one shared counter.
   On 96×14 that is ~268 cells (~14 strands), which is all a 14-row grid can
   hold. Once the budget is gone the remaining strands are simply not drawn.
2. **Occupy-once.** An edge can never overwrite a written cell (first writer
   wins) and node cells are *reserved* before any edge is drawn, so no strand
   runs through a glyph. Edges shorter than 3 projected cells are skipped. Node
   cells themselves are de-duplicated: a node that lands on a taken cell walks a
   spiral of up to 3 rings to the nearest free one, so a cluster never hides a
   node, and `hits` stays unique per key.
3. **Prominence grading.** `prominence = 0.55·degree + 0.30·log(uses) + 0.15·score`
   gives the sub-letter tier (`o` at ≥ 0.55, `+` at ≥ 0.30, `.` below) so a
   sparse graph of low-degree nodes still reads as a *graded* field rather than
   flat dust.

## Degrading honestly

- **Sparse mode (fewer than 15 real edges survive, including 0 nodes).** When
  there are too few relations to be a picture — the live database holds ~137
  memories and ~12 edges — the frame gets a faint **static backdrop texture**:
  hash-seeded `:` hairlines every 12 columns plus scattered `.` dust. It is
  **texture, not data**: it never varies with `edges` or `angle`, it does not
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
- Hit-boxes refresh every 3rd redraw (~4/s) — they can lag a fast rotation by
  one beat but never jitter.
- `bannerRows` covers `A–Z 0–9 space - . : /`; anything else renders blank.
