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
- Every node claims a **halo**: the first 2 free orthogonal cells around its
  glyph (`HALO_MAX`), written `:` in faint, after the glyphs and before any
  strand, so a cluster of related nodes reads as a blob and strands can never
  cross a glyph or a halo.
- Similarity strands: dotted (step 2) `.` dust in faint; focus-incident strands
  draw solid. Declared edges: `-` `|` `/` `\` by slope; `contradicts` marks
  every 4th cell `x` in coral, `supports` green, `derived_from` violet,
  `mentions` faint.
- Drawing order: any backdrop, then node cells (reserved, then glyphs, then
  halos), then declared edges, then similarity strands. Strand ink ends up
  clearly subordinate to node ink (frame-check measures it per angle).

### Where nodes sit

`layout(nodes, edges, similar)` returns a 3D point per key on the unit sphere,
seeded from `seedPos(key)` and relaxed with Fruchterman-Reingold: every pair
repels (`k²/d · REPULSION`), every link pulls (`d²/k · w · PULL`), each step is
capped by a cooling temperature, and every point is renormalised to the sphere.
Link weight is `score` for a `similar` pair and `1` for a declared edge, so
declared edges stay the strongest relation. `PULL` (16) vs `REPULSION` (0.3) are
the two levers that keep clusters 3-8 cells wide instead of 20.

Two calibrations keep a 243x22 frame readable (fixture: 137 nodes, per-type
ring+skip links, 4 declared edges):

- `SIMILAR_PER_NODE` (2): every node keeps at most its top-2 links by score
  (undirected pairs count once), so at most ~n strands exist no matter how many
  links the server sends.
- `STRAND_RATIO` (1.2): a strand longer than the row height x 1.2 is dropped —
  it crosses the frame and reads as noise (frame-check: intra 0.83 vs cross
  1.39 in 3D; neighbours project closer at all 16 rotations, worst 3.86x).

The layout is a **rotating** field, not a stable chart: only the caller's
`angle` moves, `layout()` never re-runs per frame, and the frame is re-centred
every redraw by two deterministic translations — centre of mass to the origin,
then a median-cell translation so the mass sits in the middle of the pane (the
median is robust against clamped outliers).

No `Math.random`, no time input. `hits`, `dim` and the overlay contract are
unchanged. `Stage.tsx` memoises the result on the payload signature.

### Focus

`buildFrame(..., { focus })`: the focus node, its `similar` neighbours and its
declared neighbours draw at full strength, the focus node's own strands draw
solid, and every other node becomes a `faint` `.` with no strands. Unset or
unknown focus draws everything. This is what turns a dot field into "this
memory, and what it is close to".

### Three rules that keep it legible

1. **Strands are rare and short.** Candidate `similar` pairs are ranked by
   score, deduped undirected, and capped at 2 per node (`SIMILAR_PER_NODE`); a
   strand longer than `STRAND_RATIO x rows` is dropped. Declared edges draw
   first; no shared cell budget — the length cap and the per-node tally are
   what bound the ink.
2. **Occupy-once.** An edge can never overwrite a written cell (first writer
   wins) and node cells are *claimed* — glyph then halo — before any strand is
   drawn, so no strand runs through a glyph or halo. Edges shorter than 3
   projected cells are skipped. Node cells themselves are de-duplicated: a node
   that lands on a taken cell walks a spiral of up to 3 rings to the nearest
   free one, so a cluster never hides a node, and `hits` stays unique per key.
3. **Prominence grading.** `prominence = 0.55·degree + 0.30·log(uses) + 0.15·score`
   gives the sub-letter tier (`o` at ≥ 0.55, `+` at ≥ 0.30, `.` below) so a
   sparse graph of low-degree nodes still reads as a *graded* field rather than
   flat dust.

## Degrading honestly

- **Sparse mode (only when nothing survives).** When neither a declared edge
  nor a similarity strand survives selection and the length cap — 0 nodes, no
  data, or everything filtered out — the frame gets a faint **static backdrop
  texture**:
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
