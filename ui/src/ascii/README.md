# `ui/src/ascii` — animated ASCII memory graph

The screenshot-piece: the memory graph drawn as living ASCII inside a `<pre>`.

## Surface

```ts
export type AsciiNode = { key: string; type: string; status: string; uses: number; score: number; degree: number };
export type AsciiEdge = { src_key: string; dst_key: string; rel: string; weight: number };
export function AsciiStage(props: { nodes: AsciiNode[]; edges: AsciiEdge[]; cols?: number; rows?: number; fps?: number; className?: string; onSelect?: (key: string) => void }): ReactElement;
export function AsciiBanner(props: { text?: string; className?: string }): ReactElement;
```

That is the whole contract. Nothing here imports another package or another
repo file: `react` only, plus these files.

## Files

- `grid.ts` — dependency-free, pure. Hash → stable 3D point on a unit sphere,
  sphere → grid projection, Bresenham-ish edge sampling, glyph selection,
  `buildFrame(nodes, edges, cols, rows, angle)`. Same args, same frame.
- `Stage.tsx` — one `requestAnimationFrame` loop, redraws at `fps` (default 12),
  angle advances `0.21 rad/s`.
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

### Three rules that keep it legible

1. **Budget by cells, not by node count.** Edges may claim at most
   `cols * rows * 0.20` cells, spent in `weight` desc order. On 96×14 that is
   ~268 cells (~14 strands), which is all a 14-row grid can hold. Once the
   budget is gone the remaining edges are simply not drawn.
2. **Occupy-once.** An edge can never overwrite a written cell — first writer
   wins (`CellBuf.claim`) — and node cells are *reserved* before any edge is
   drawn, so no strand runs through a glyph. Edges shorter than 3 projected
   cells are skipped. This one rule is what turns a scribble into strands.
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
