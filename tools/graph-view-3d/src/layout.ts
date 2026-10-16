// Hand-rolled force layout: repulsion + spring edges + centering, fixed
// iterations, deterministic seed (same graph -> same picture every time).

export interface LayoutResult {
  pos: Float32Array; // xyz per node, index*3
  ms: number;
  iterations: number;
}

function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function forceLayout(
  nodeCount: number,
  edges: Array<[number, number]>,
  seed = 0x9e3779b9,
): LayoutResult {
  const t0 = performance.now();
  const pos = new Float32Array(nodeCount * 3);
  const vel = new Float32Array(nodeCount * 3);
  const rnd = mulberry32(seed);

  // Deterministic start: fibonacci sphere, radius scaled to the node count.
  const radius = 24 + Math.sqrt(Math.max(nodeCount, 1)) * 7;
  const golden = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < nodeCount; i++) {
    const y = 1 - (i / Math.max(nodeCount - 1, 1)) * 2;
    const r = Math.sqrt(Math.max(0, 1 - y * y));
    const th = golden * i;
    pos[i * 3] = Math.cos(th) * r * radius + (rnd() - 0.5) * 0.5;
    pos[i * 3 + 1] = y * radius + (rnd() - 0.5) * 0.5;
    pos[i * 3 + 2] = Math.sin(th) * r * radius + (rnd() - 0.5) * 0.5;
  }

  const iterations = nodeCount > 800 ? 40 : nodeCount > 300 ? 80 : 140;
  const repulsion = 900;
  const cutoff = 90;
  const cutoff2 = cutoff * cutoff;
  const spring = 0.08;
  const springLen = 22;
  const gravity = 0.012;
  const damping = 0.85;

  for (let it = 0; it < iterations; it++) {
    // pairwise repulsion (cutoff keeps the O(n^2) pass cheap for big graphs)
    for (let i = 0; i < nodeCount; i++) {
      const ix = i * 3;
      for (let j = i + 1; j < nodeCount; j++) {
        const jx = j * 3;
        const dx = pos[ix] - pos[jx];
        const dy = pos[ix + 1] - pos[jx + 1];
        const dz = pos[ix + 2] - pos[jx + 2];
        const d2 = dx * dx + dy * dy + dz * dz;
        if (d2 > cutoff2 || d2 < 1e-6) continue;
        const inv = repulsion / (d2 * Math.sqrt(d2));
        const fx = dx * inv;
        const fy = dy * inv;
        const fz = dz * inv;
        vel[ix] += fx;
        vel[ix + 1] += fy;
        vel[ix + 2] += fz;
        vel[jx] -= fx;
        vel[jx + 1] -= fy;
        vel[jx + 2] -= fz;
      }
    }
    // springs pull linked nodes toward springLen
    for (const [a, b] of edges) {
      const ax = a * 3;
      const bx = b * 3;
      const dx = pos[bx] - pos[ax];
      const dy = pos[bx + 1] - pos[ax + 1];
      const dz = pos[bx + 2] - pos[ax + 2];
      const d = Math.sqrt(dx * dx + dy * dy + dz * dz) || 1e-6;
      const f = (d - springLen) * spring;
      const fx = (dx / d) * f;
      const fy = (dy / d) * f;
      const fz = (dz / d) * f;
      vel[ax] += fx;
      vel[ax + 1] += fy;
      vel[ax + 2] += fz;
      vel[bx] -= fx;
      vel[bx + 1] -= fy;
      vel[bx + 2] -= fz;
    }
    // centering + integrate
    for (let i = 0; i < nodeCount; i++) {
      const ix = i * 3;
      vel[ix] -= pos[ix] * gravity;
      vel[ix + 1] -= pos[ix + 1] * gravity;
      vel[ix + 2] -= pos[ix + 2] * gravity;
      pos[ix] = (pos[ix] + vel[ix]) * damping;
      pos[ix + 1] = (pos[ix + 1] + vel[ix + 1]) * damping;
      pos[ix + 2] = (pos[ix + 2] + vel[ix + 2]) * damping;
      vel[ix] *= damping;
      vel[ix + 1] *= damping;
      vel[ix + 2] *= damping;
    }
  }

  return { pos, ms: performance.now() - t0, iterations };
}
