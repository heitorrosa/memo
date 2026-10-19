// Frame check for the ASCII stage: no test runner, no dependencies.
//   node --experimental-strip-types scripts/frame-check.mjs
// Live-shaped fixture (137 nodes: 21 P / 102 C / 10 F / 4 A, 245 similar,
// 4 declared edges) at 243x22. Asserts that the layout is a filled, frozen
// chart — phase only moves a brightness band — and prints the evidence.
import assert from 'node:assert/strict';
import { buildFrame, layout, seedPos } from '../src/ascii/grid.ts';

let checks = 0;
const ok = (name) => {
  checks++;
  console.log(`  ok ${checks} - ${name}`);
};

const COLS = 243;
const ROWS = 22;
const TOTAL = COLS * ROWS + ROWS - 1;
const NODE_GLYPHS = new Set(['●', '█', '•', '·', '.']);
const LABEL_DEGREE = 3;

/* ------------------------------------------------------------- fixture -- */

const nodes = [];
const members = { preference: [], context: [], feedback: [], analysis: [] };
for (const [type, m] of [
  ['preference', 21],
  ['context', 102],
  ['feedback', 10],
  ['analysis', 4],
]) {
  for (let i = 0; i < m; i++) {
    const key = `${type[0]}${nodes.length}`;
    members[type].push(key);
    nodes.push({
      key,
      type,
      status: type === 'analysis' && i === 0 ? 'superseded' : 'current',
      uses: (i * 5) % 40,
      score: 0.3 + ((i * 7) % 10) / 14,
      degree: 0,
    });
  }
}

// Similarity is block-local (blocks of 8 inside one type) plus two hubs per
// type, which is the shape a cosine-neighbour graph has. A ring per type would
// be a ring on screen — the fixture must not lie about the topology.
const similar = [];
const seen = new Set();
const link = (a, b, score) => {
  if (a === b) return;
  const pair = a < b ? `${a}>${b}` : `${b}>${a}`;
  if (seen.has(pair)) return;
  seen.add(pair);
  similar.push({ src_key: a, dst_key: b, score });
};
for (const [type, keys] of Object.entries(members)) {
  const hubs = [keys[0], keys[Math.floor(keys.length / 2)]];
  for (let i = 0; i < keys.length; i++) {
    const start = Math.floor(i / 8) * 8;
    const size = Math.min(8, keys.length - start);
    const a = (i * 5 + 3) % size;
    const b = (i * 3 + 1) % size;
    if (a !== i - start) link(keys[i], keys[start + a], 0.55 + ((i * 3) % 40) / 100);
    if (b !== i - start) link(keys[i], keys[start + b], 0.5 + ((i * 7) % 40) / 100);
    if (i % 4 === 0) link(keys[i], keys[start + ((i * 11 + 5) % size)], 0.45);
    if (i % 8 === 0) link(keys[i], hubs[i % hubs.length], 0.88);
  }
}
if (similar.length > 245) similar.length = 245; // live link count

const edges = [
  { src_key: members.context[0], dst_key: members.context[30], rel: 'supports', weight: 1 },
  { src_key: members.context[1], dst_key: members.context[60], rel: 'mentions', weight: 1 },
  { src_key: members.preference[0], dst_key: members.preference[8], rel: 'derived_from', weight: 1 },
  { src_key: members.context[2], dst_key: members.feedback[3], rel: 'contradicts', weight: 1 },
];

const degree = new Map(nodes.map((n) => [n.key, 0]));
for (const l of similar) {
  degree.set(l.src_key, degree.get(l.src_key) + 1);
  degree.set(l.dst_key, degree.get(l.dst_key) + 1);
}
for (const e of edges) {
  degree.set(e.src_key, degree.get(e.src_key) + 1);
  degree.set(e.dst_key, degree.get(e.dst_key) + 1);
}
for (const n of nodes) n.degree = degree.get(n.key);

const OPTS = { similar };
const lines = (frame) => frame.text.split('\n');
const at = (frame, row, col) => lines(frame)[row][col];
const cell = (frame, key) => frame.hits.find((h) => h.key === key);

/** Cells whose glyph differs between two frames — colour is not in `text`. */
function diffCells(a, b) {
  const A = lines(a);
  const B = lines(b);
  let d = 0;
  for (let row = 0; row < ROWS; row++) {
    for (let col = 0; col < COLS; col++) if (A[row][col] !== B[row][col]) d++;
  }
  return d;
}

/* ----------------------------------------------------------- the layout -- */

const points = layout(nodes, edges, similar, COLS, ROWS);
assert.deepEqual(layout(nodes, edges, similar, COLS, ROWS), points);
ok('layout is deterministic (same payload -> identical cells, no Math.random)');

const dist2 = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);
const mean = (xs) => xs.reduce((s, x) => s + x, 0) / xs.length;

const intra = [];
const cross = [];
const types = Object.keys(members);
for (const t of types) {
  for (let i = 0; i < members[t].length; i++) {
    for (let j = i + 1; j < members[t].length; j++) {
      intra.push(dist2(points.get(members[t][i]), points.get(members[t][j])));
    }
    for (const u of types) {
      if (u === t) continue;
      const other = members[u][Math.min(i, members[u].length - 1)];
      cross.push(dist2(points.get(members[t][i]), points.get(other)));
    }
  }
}
const intraMean = mean(intra);
const crossMean = mean(cross);
assert.ok(intraMean < crossMean * 0.75, `type clusters overlap: ${intraMean.toFixed(1)} vs ${crossMean.toFixed(1)}`);
ok(`same-type nodes gather (intra ${intraMean.toFixed(1)} vs cross ${crossMean.toFixed(1)} world cells)`);

/* --------------------------------------------------- layout is frozen -- */

const PHASES = [0.1, 1.4, 3.9];
const frames = PHASES.map((p) => buildFrame(nodes, edges, COLS, ROWS, p, OPTS));
for (let i = 1; i < frames.length; i++) {
  assert.equal(diffCells(frames[0], frames[i]), 0, `phase ${PHASES[i]} moved the layout`);
}
ok(`phase ${PHASES.join('/')} -> 0 differing cells after stripping colour`);

// ...while the brightness band really does move: the layers must differ.
const layerOf = (frame, role) => frame.overlays.find((o) => o.role === role)?.text ?? '';
assert.notEqual(layerOf(frames[0], 'faint'), layerOf(frames[1], 'faint'));
assert.notEqual(layerOf(frames[0], 'amber'), layerOf(frames[1], 'amber'));
ok('brightness band moves (faint + amber layers differ between phases)');

/* ------------------------------------------------------------ the frame -- */

const frame = frames[0];
assert.equal(frame.text.length, TOTAL);
for (const layer of frame.overlays) {
  assert.equal(layer.text.length, TOTAL);
  layer.text.split('\n').forEach((line, row) => {
    [...line].forEach((ch, col) => {
      if (ch !== ' ') assert.equal(at(frame, row, col), ch, `overlay ${layer.role} disagrees at ${row},${col}`);
    });
  });
}
ok(`frame + ${frame.overlays.length} overlays are exact ${COLS}x${ROWS} layers, cell for cell`);

/* ------------------------------------------------------------ node cells -- */

assert.equal(frame.hits.length, nodes.length, 'missing hit-boxes');
assert.equal(new Set(frame.hits.map((h) => `${h.col},${h.row}`)).size, frame.hits.length, 'two nodes on one cell');
let offGlyph = 0;
let offBounds = 0;
for (const h of frame.hits) {
  if (h.col < 0 || h.col >= COLS || h.row < 0 || h.row >= ROWS) offBounds++;
  if (!NODE_GLYPHS.has(at(frame, h.row, h.col))) offGlyph++;
}
assert.equal(offBounds, 0, `${offBounds} node cells out of bounds`);
assert.equal(offGlyph, 0, `${offGlyph} node cells do not hold a node glyph`);
ok(`137 node cells: all in bounds, all hold a node glyph, none shared`);

/* --------------------------------------------------------------- fills -- */

const cols = frame.hits.map((h) => h.col);
const rows = frame.hits.map((h) => h.row);
const fillX = (Math.max(...cols) - Math.min(...cols) + 1) / COLS;
const fillY = (Math.max(...rows) - Math.min(...rows) + 1) / ROWS;
assert.ok(fillX >= 0.8, `node bbox covers ${(fillX * 100).toFixed(0)}% of columns (< 80%)`);
assert.ok(fillY >= 0.7, `node bbox covers ${(fillY * 100).toFixed(0)}% of rows (< 70%)`);
ok(`nodes fill the pane: ${(fillX * 100).toFixed(0)}% of cols x ${(fillY * 100).toFixed(0)}% of rows`);

/* --------------------------------------------------------------- labels -- */

const glyphInk = (frame.text.match(/[●█•·]/g) ?? []).length;
const labelInk = (frame.text.match(/[A-Za-z0-9]/g) ?? []).length;
const tailOf = (key) => (key.includes('.') ? key.slice(key.lastIndexOf('.') + 1) : key);
const hubs = nodes.filter((n) => n.degree >= LABEL_DEGREE);
// A hub counts as labelled when the text the renderer would write (the tail of
// its key, truncated to the frame) sits right there, after the single space.
const labelled = hubs.filter((n) => {
  const h = cell(frame, n.key);
  if (!h) return false;
  const line = lines(frame)[h.row];
  const tail = tailOf(n.key);
  return [2, 3].some((d) => {
    const room = COLS - h.col - d;
    if (room < 2) return false;
    return line.slice(h.col + d, h.col + d + Math.min(room, tail.length)) === tail.slice(0, Math.min(room, tail.length));
  });
});
const labelPct = labelled.length / hubs.length;
assert.ok(labelPct >= 0.85, `only ${(labelPct * 100).toFixed(1)}% of degree>=3 nodes are labelled`);
ok(`${labelled.length}/${hubs.length} degree>=3 nodes labelled (${(labelPct * 100).toFixed(1)}%), tail of the key`);

/* --------------------------------------------------------------- ink mix -- */

let nonSpace = 0;
let longest = 0;
for (let row = 0; row < ROWS; row++) {
  let run = 0;
  for (let col = 0; col < COLS; col++) {
    const ch = at(frame, row, col);
    if (ch === ' ') {
      run = 0;
      continue;
    }
    nonSpace++;
    run++;
    longest = Math.max(longest, run);
  }
}
const nodeShare = (glyphInk + labelInk) / nonSpace;
const lineInk = nonSpace - glyphInk - labelInk;
assert.ok(nodeShare >= 0.45, `node+label ink ${(nodeShare * 100).toFixed(1)}% of non-space ink (< 45%)`);
ok(
  `ink: ${nonSpace}/${TOTAL} cells (${((nonSpace / TOTAL) * 100).toFixed(1)}% occupancy), ` +
    `nodes ${glyphInk}, labels ${labelInk}, lines ${lineInk} — node+label ${(nodeShare * 100).toFixed(1)}%, ` +
    `longest run ${longest}`,
);
assert.ok(nonSpace / TOTAL <= 0.35, `occupancy ${((nonSpace / TOTAL) * 100).toFixed(1)}% > 35%`);

/* -------------------------------------------- links are short on screen -- */

const linked = [];
for (const e of edges) linked.push([e.src_key, e.dst_key]);
for (const l of similar) linked.push([l.src_key, l.dst_key]);
const span = (a, b) => Math.hypot(a.col - b.col, a.row - b.row);
const linkedSpan = mean(linked.map(([a, b]) => span(cell(frame, a), cell(frame, b))));
const everyPair = [];
for (let i = 0; i < frame.hits.length; i++) {
  for (let j = i + 1; j < frame.hits.length; j++) everyPair.push(span(frame.hits[i], frame.hits[j]));
}
const pairMean = mean(everyPair);
assert.ok(linkedSpan < pairMean * 0.6, `linked pairs ${linkedSpan.toFixed(1)} vs any pair ${pairMean.toFixed(1)}`);
ok(`links are short: linked pairs ${linkedSpan.toFixed(1)} cells vs ${pairMean.toFixed(1)} for any pair`);

/* --------------------------------------------------------------- focus -- */

const focusKey = members.context[0];
const focused = buildFrame(nodes, edges, COLS, ROWS, 0.1, { ...OPTS, focus: focusKey });
const dimmed = cell(focused, members.preference[10]);
assert.equal(at(focused, dimmed.row, dimmed.col), '.', 'non-neighbour is not dimmed');
const faintLayer = focused.overlays.find((o) => o.role === 'faint');
assert.ok(faintLayer && /\S/.test(faintLayer.text), 'no faint ink when focused');
// The dim glyph may sit inside the brightness band, which lifts faint to
// amber; either way exactly one colour layer must carry it.
assert.ok(
  focused.overlays.some((o) => o.text.split('\n')[dimmed.row][dimmed.col] === '.'),
  'dimmed node carries no colour layer',
);
const lit = cell(focused, members.context[1]);
assert.notEqual(at(focused, lit.row, lit.col), '.');
ok('focus dims a non-neighbour to a faint dot and keeps a neighbour lit');

/* --------------------------------------------------------- sparse + old -- */

const bare = buildFrame(nodes, [], COLS, ROWS, 0);
assert.equal(bare.sparse, true, 'no strands, no edges -> backdrop must draw');
assert.ok(bare.text.includes(':'), 'backdrop hairlines missing');
ok('sparse mode: nothing survives -> dust + hairlines, honestly marked');

const legacy = buildFrame(nodes, edges, COLS, ROWS, 0);
const empty = buildFrame(nodes, edges, COLS, ROWS, 0, { similar: [] });
assert.equal(legacy.text, empty.text);
// no links at all -> the seeded field, untouched
const seeds = new Map(nodes.map((n) => [n.key, seedPos(n, COLS, ROWS)]));
const seeded = buildFrame(nodes, [], COLS, ROWS, 0, { similar: [], pos: seeds });
const noLinks = buildFrame(nodes, [], COLS, ROWS, 0, { similar: [] });
assert.equal(noLinks.text, seeded.text);
ok('absent similar falls back to the seeded field (no throw, identical frame)');

/* --------------------------------------------------------------- print -- */

console.log(`\n--- 243x22, phase 0.1 ---`);
console.log(frame.text);
console.log(`\n${checks} checks passed`);
