import { Canvas, useFrame, useThree, type ThreeEvent } from "@react-three/fiber";
import { Html, OrbitControls } from "@react-three/drei";
import { useLayoutEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import { REL_COLORS, nodeColor, type GraphEdge, type GraphModel, type GraphNode } from "../data";

interface SceneProps {
  model: GraphModel;
  positions: Float32Array;
  visibleNodes: Set<string>;
  visibleEdges: GraphEdge[];
  focusId: string | null;
  onFocus: (id: string | null) => void;
}

function nodeRadius(n: GraphNode): number {
  if (n.kind === "entity") return 1.4;
  return 1.8 + Math.min(3.6, Math.sqrt(Math.max(n.uses, 0)) * 0.6);
}

function Nodes({
  model,
  positions,
  visibleNodes,
  onFocus,
}: Pick<SceneProps, "model" | "positions" | "visibleNodes" | "onFocus">) {
  const ref = useRef<THREE.InstancedMesh>(null);
  const dummy = useMemo(() => new THREE.Object3D(), []);
  const tint = useMemo(() => new THREE.Color(), []);

  useLayoutEffect(() => {
    const mesh = ref.current;
    if (!mesh) return;
    model.nodes.forEach((n, i) => {
      const shown = visibleNodes.has(n.id);
      dummy.position.set(positions[i * 3], positions[i * 3 + 1], positions[i * 3 + 2]);
      dummy.scale.setScalar(shown ? nodeRadius(n) : 0);
      dummy.updateMatrix();
      mesh.setMatrixAt(i, dummy.matrix);
      tint.set(nodeColor(n));
      mesh.setColorAt(i, tint);
    });
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
  }, [model, positions, visibleNodes, dummy, tint]);

  const click = (e: ThreeEvent<MouseEvent>) => {
    e.stopPropagation();
    if (e.instanceId == null) return;
    const n = model.nodes[e.instanceId];
    if (n && visibleNodes.has(n.id)) onFocus(n.id);
  };

  return (
    <instancedMesh ref={ref} args={[undefined, undefined, model.nodes.length]} onClick={click}>
      <sphereGeometry args={[1, 16, 16]} />
      <meshStandardMaterial roughness={0.45} metalness={0.15} />
    </instancedMesh>
  );
}

function buildLines(
  edges: GraphEdge[],
  positions: Float32Array,
  index: Map<string, number>,
  colored: boolean,
) {
  const n = edges.length * 2;
  const p = new Float32Array(n * 3);
  const c = colored ? new Float32Array(n * 3) : null;
  const col = new THREE.Color();
  edges.forEach((e, k) => {
    const a = index.get(e.from);
    const b = index.get(e.to);
    if (a == null || b == null) return;
    const o = k * 6;
    p[o] = positions[a * 3];
    p[o + 1] = positions[a * 3 + 1];
    p[o + 2] = positions[a * 3 + 2];
    p[o + 3] = positions[b * 3];
    p[o + 4] = positions[b * 3 + 1];
    p[o + 5] = positions[b * 3 + 2];
    if (c) {
      col.set(REL_COLORS[e.rel] ?? "#8a94a6");
      c[o] = col.r;
      c[o + 1] = col.g;
      c[o + 2] = col.b;
      c[o + 3] = col.r;
      c[o + 4] = col.g;
      c[o + 5] = col.b;
    }
  });
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.BufferAttribute(p, 3));
  if (c) g.setAttribute("color", new THREE.BufferAttribute(c, 3));
  return g;
}

function Edges({
  model,
  positions,
  visibleEdges,
}: Pick<SceneProps, "model" | "positions" | "visibleEdges">) {
  const index = useMemo(() => {
    const m = new Map<string, number>();
    model.nodes.forEach((n, i) => m.set(n.id, i));
    return m;
  }, [model]);

  const solid = useMemo(
    () => buildLines(visibleEdges.filter((e) => e.rel !== "contradicts" && e.rel !== "same_as"), positions, index, true),
    [visibleEdges, positions, index],
  );
  const contradicted = useMemo(
    () => buildLines(visibleEdges.filter((e) => e.rel === "contradicts"), positions, index, false),
    [visibleEdges, positions, index],
  );
  const aliases = useMemo(
    () => buildLines(visibleEdges.filter((e) => e.rel === "same_as"), positions, index, false),
    [visibleEdges, positions, index],
  );

  return (
    <>
      <lineSegments geometry={solid}>
        <lineBasicMaterial vertexColors transparent opacity={0.55} />
      </lineSegments>
      <lineSegments geometry={contradicted} ref={(el) => void el?.computeLineDistances()}>
        <lineDashedMaterial color={REL_COLORS.contradicts} dashSize={2.4} gapSize={1.8} transparent opacity={0.9} />
      </lineSegments>
      <lineSegments geometry={aliases} ref={(el) => void el?.computeLineDistances()}>
        <lineDashedMaterial color={REL_COLORS.same_as} dashSize={0.35} gapSize={0.9} transparent opacity={0.8} />
      </lineSegments>
    </>
  );
}

function FocusMarker({
  model,
  positions,
  visibleNodes,
  focusId,
  onFocus,
}: Pick<SceneProps, "model" | "positions" | "visibleNodes" | "focusId" | "onFocus">) {
  const controls = useThree((s) => s.controls) as { target: THREE.Vector3; update: () => void } | null;
  const { camera } = useThree();
  const goal = useRef(new THREE.Vector3());
  const aim = useRef<THREE.Vector3 | null>(null);
  // stop steering the camera once we have arrived, otherwise the user cannot orbit
  const chasing = useRef(false);

  const idx = focusId ? model.nodes.findIndex((n) => n.id === focusId) : -1;
  const node = idx >= 0 && visibleNodes.has(model.nodes[idx].id) ? model.nodes[idx] : null;
  const at = useMemo(
    () => (node ? new THREE.Vector3(positions[idx * 3], positions[idx * 3 + 1], positions[idx * 3 + 2]) : null),
    [node, idx, positions],
  );

  useLayoutEffect(() => {
    if (!at) {
      aim.current = null;
      chasing.current = false;
      return;
    }
    goal.current.copy(at).add(new THREE.Vector3(0, 0, 72));
    aim.current = at.clone();
    chasing.current = true;
  }, [at]);

  useFrame(() => {
    if (!chasing.current || !aim.current) return;
    camera.position.lerp(goal.current, 0.1);
    if (controls) {
      controls.target.lerp(aim.current, 0.15);
      controls.update();
    }
    if (camera.position.distanceTo(goal.current) < 0.6) chasing.current = false;
  });

  if (!node || !at) return null;
  return (
    <group position={at}>
      <mesh>
        <sphereGeometry args={[nodeRadius(node) * 1.9, 20, 20]} />
        <meshBasicMaterial color={nodeColor(node)} wireframe transparent opacity={0.45} />
      </mesh>
      <Html center distanceFactor={90} zIndexRange={[20, 0]}>
        <div className="pointer-events-none max-w-[240px] -translate-y-8 rounded border border-cyan-300/40 bg-slate-950/90 px-2 py-1 font-mono text-[11px] leading-tight text-cyan-100 shadow-lg">
          <div className="font-semibold text-cyan-50">{node.key}</div>
          <div className="text-cyan-300/80">
            {node.kind === "entity" ? "virtual entity" : `${node.type} - ${node.status}`}
          </div>
        </div>
      </Html>
      <mesh onClick={(e) => { e.stopPropagation(); onFocus(null); }}>
        <sphereGeometry args={[nodeRadius(node) * 1.9, 20, 20]} />
        <meshBasicMaterial transparent opacity={0} />
      </mesh>
    </group>
  );
}

export default function Graph3D(props: SceneProps) {
  const { model, focusId, onFocus } = props;
  return (
    <Canvas
      dpr={[1, 2]}
      camera={{ position: [0, 0, 170], fov: 50, near: 1, far: 4000 }}
      onPointerMissed={() => onFocus(null)}
      gl={{ antialias: true }}
    >
      <color attach="background" args={["#060b16"]} />
      <fog attach="fog" args={["#060b16", 300, 1100]} />
      <ambientLight intensity={1.0} />
      <hemisphereLight args={["#9fb8ff", "#0b1220", 1.1]} />
      <directionalLight position={[120, 180, 140]} intensity={2.2} />
      <pointLight position={[-160, -120, -120]} intensity={1.4} color="#3b82f6" />
      <Nodes
        model={model}
        positions={props.positions}
        visibleNodes={props.visibleNodes}
        onFocus={onFocus}
      />
      <Edges model={model} positions={props.positions} visibleEdges={props.visibleEdges} />
      <FocusMarker
        model={model}
        positions={props.positions}
        visibleNodes={props.visibleNodes}
        focusId={focusId}
        onFocus={onFocus}
      />
      <OrbitControls makeDefault enableDamping dampingFactor={0.08} minDistance={20} maxDistance={1400} />
    </Canvas>
  );
}
