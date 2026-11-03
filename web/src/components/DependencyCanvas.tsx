// Composer-style dependency canvas: React Flow (xyflow) rendering cards built from Cloudscape design tokens,
// laid out with dagre. Cloudscape has no diagram component, so the cards follow the visual language of the
// AWS Infrastructure Composer / Step Functions Workflow Studio canvases using Cloudscape's own tokens.
import * as awsui from "@cloudscape-design/design-tokens";
import dagre from "@dagrejs/dagre";
import {
  Background,
  BackgroundVariant,
  BaseEdge,
  Controls,
  Edge,
  EdgeLabelRenderer,
  EdgeProps,
  getSmoothStepPath,
  Handle,
  MarkerType,
  MiniMap,
  Node,
  NodeProps,
  Position,
  ReactFlow,
  useNodesInitialized,
  useReactFlow,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { memo, useEffect, useMemo, useState } from "react";
import { GraphEdge, GraphNode } from "../api";

export type Direction = "LR" | "TB";

const NODE_W = 236;
const NODE_H = 74;
const GROUP_PAD = 28;

const CATEGORY = {
  Application: { color: awsui.colorChartsPaletteCategorical1, label: "Serviço" },
  DataStore: { color: awsui.colorChartsPaletteCategorical3, label: "Dados" },
  Topic: { color: awsui.colorChartsPaletteCategorical2, label: "Mensageria" },
  ExternalSystem: { color: awsui.colorChartsPaletteCategorical4, label: "Externo" },
} as const;

// SVG `fill` ignores CSS custom properties, so the minimap uses the tokens' resolved fallback colours
const MINIMAP = { Application: "#688ae8", DataStore: "#2ea597", Topic: "#c33d69", ExternalSystem: "#8456ce" } as const;

const LANG = { java: "JV", kotlin: "KT", csharp: "C#", go: "GO", typescript: "TS", python: "PY" } as Record<string, string>;
const TECH = {
  postgresql: "PG", postgres: "PG", mysql: "MY", oracle: "ORA", sqlserver: "SQL", mongodb: "MDB", redis: "RDS",
  dynamodb: "DDB", s3: "S3", sqs: "SQS", sns: "SNS", kinesis: "KDS", kafka: "KFK", rabbitmq: "MQ", jms: "JMS",
} as Record<string, string>;
const KIND_TEXT = {
  "object-storage": "Bucket", "messaging-queue": "Fila", "messaging-topic": "Tópico", "event-stream": "Stream",
  database: "Banco de dados",
} as Record<string, string>;
const EDGE_TEXT = { http: "REST", grpc: "gRPC", graphql: "GraphQL", soap: "SOAP", data: "", async: "", external: "" } as Record<string, string>;

export type CardData = { node: GraphNode; dimmed: boolean; selected: boolean };
export type GroupData = { label: string; area?: string };
type FlowEdgeData = { edge: GraphEdge; dimmed: boolean; selected: boolean; focused: boolean };

function glyph(n: GraphNode): string {
  if (n.type === "Application") return LANG[n.languages?.[0] ?? ""] ?? "APP";
  if (n.type === "ExternalSystem") return "EXT";
  return TECH[n.technology ?? ""] ?? (n.type === "Topic" ? "MSG" : "DB");
}

function subtitle(n: GraphNode): string {
  if (n.type === "Application") {
    const kind = n.node_type === "webclient" ? "Front-end" : "Serviço";
    return [kind, ...(n.languages ?? [])].join(" · ");
  }
  if (n.type === "ExternalSystem") return "Sistema externo";
  return [KIND_TEXT[n.kind ?? ""] ?? CATEGORY[n.type].label, n.technology].filter(Boolean).join(" · ");
}

const handleStyle = { width: 8, height: 8, background: awsui.colorBackgroundContainerContent, border: `2px solid ${awsui.colorBorderInputDefault}` };

const ServiceCard = memo(function ServiceCard({ data, targetPosition, sourcePosition }: NodeProps<Node<CardData>>) {
  const n = data.node;
  const color = CATEGORY[n.type].color;
  const review = n.type === "Application" && n.review;
  return (
    <div
      title={n.id}
      style={{
        width: NODE_W, height: NODE_H, boxSizing: "border-box", display: "flex", alignItems: "center", gap: 10,
        padding: "8px 12px", borderRadius: 12, fontFamily: awsui.fontFamilyBase,
        background: awsui.colorBackgroundContainerContent, color: awsui.colorTextBodyDefault,
        border: `${data.selected ? 2 : 1}px solid ${data.selected ? awsui.colorBorderItemFocused : review ? awsui.colorBorderStatusWarning : awsui.colorBorderDividerDefault}`,
        boxShadow: data.selected ? `0 0 0 3px color-mix(in srgb, ${awsui.colorBorderItemFocused} 25%, transparent)` : "0 1px 3px rgba(0, 7, 22, 0.12)",
        opacity: data.dimmed ? 0.25 : 1, transition: "opacity 120ms",
      }}
    >
      <Handle type="target" position={targetPosition ?? Position.Left} style={handleStyle} />
      <div
        aria-hidden
        style={{
          flex: "0 0 auto", width: 40, height: 40, borderRadius: 8, background: color, color: "#fff",
          display: "flex", alignItems: "center", justifyContent: "center", fontSize: 12, fontWeight: 700, letterSpacing: 0.3,
        }}
      >
        {glyph(n)}
      </div>
      <div style={{ minWidth: 0, flex: 1 }}>
        <div style={{ fontSize: 13, fontWeight: 700, lineHeight: "18px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{n.name}</div>
        <div style={{ fontSize: 11, lineHeight: "16px", color: awsui.colorTextBodySecondary, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{subtitle(n)}</div>
        {n.type === "Application" && n.service_domain && (
          <div style={{ marginTop: 2, fontSize: 11, lineHeight: "16px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: review ? awsui.colorTextStatusWarning : awsui.colorTextLinkDefault }}>
            {review ? "⚠ " : ""}BIAN · {n.service_domain}
          </div>
        )}
      </div>
      <Handle type="source" position={sourcePosition ?? Position.Right} style={handleStyle} />
    </div>
  );
});

const DomainGroup = memo(function DomainGroup({ data, width, height }: NodeProps<Node<GroupData>>) {
  return (
    <div
      style={{
        width, height, boxSizing: "border-box", borderRadius: 16, fontFamily: awsui.fontFamilyBase,
        border: `1px dashed ${awsui.colorBorderInputDefault}`,
        background: `color-mix(in srgb, ${awsui.colorChartsPaletteCategorical1} 6%, transparent)`,
      }}
    >
      <div style={{ padding: "6px 12px", fontSize: 12, fontWeight: 700, color: awsui.colorTextBodySecondary,
        whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
        BIAN · {data.label}
        {data.area && <span style={{ fontWeight: 400 }}> — {data.area}</span>}
      </div>
    </div>
  );
});

function DependencyEdge({ id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, markerEnd, data }: EdgeProps<Edge<FlowEdgeData>>) {
  const e = data!.edge;
  const [path, labelX, labelY] = getSmoothStepPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, borderRadius: 10 });
  const label = EDGE_TEXT[e.kind] ?? "";
  // REST/gRPC/GraphQL/SOAP are always labelled; transport-only protocols (HTTPS, TCP, JDBC) when in focus
  const text = label || (data!.focused ? e.protocol : "");
  const stroke = data!.selected ? awsui.colorBorderItemFocused : awsui.colorBorderInputDefault;
  return (
    <>
      <BaseEdge
        id={id}
        path={path}
        markerEnd={markerEnd}
        style={{
          stroke, strokeWidth: data!.selected ? 2.4 : 1.5, opacity: data!.dimmed ? 0.12 : 1,
          strokeDasharray: e.confidence < 0.8 ? "6 4" : e.kind === "async" ? "2 4" : undefined,
        }}
      />
      {text && !data!.dimmed && (
        <EdgeLabelRenderer>
          <div
            className="nodrag nopan"
            style={{
              position: "absolute", transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
              pointerEvents: "all", padding: "1px 6px", borderRadius: 8, fontSize: 10, fontWeight: 700,
              fontFamily: awsui.fontFamilyBase, background: awsui.colorBackgroundContainerContent,
              color: label ? awsui.colorTextLinkDefault : awsui.colorTextBodySecondary,
              border: `1px solid ${awsui.colorBorderDividerDefault}`,
            }}
          >
            {text}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

const nodeTypes = { card: ServiceCard, domain: DomainGroup };
const edgeTypes = { dependency: DependencyEdge };

/** dagre layout; with `grouped`, applications are nested in one compound node per BIAN business domain. */
function layout(nodes: GraphNode[], edges: GraphEdge[], direction: Direction, grouped: boolean) {
  const g = new dagre.graphlib.Graph({ compound: grouped });
  g.setGraph({ rankdir: direction, nodesep: 28, ranksep: 90, marginx: 20, marginy: 20 });
  g.setDefaultEdgeLabel(() => ({}));
  const groups = new Map<string, string | undefined>();
  for (const n of nodes) {
    g.setNode(n.id, { width: NODE_W, height: NODE_H });
    if (grouped && n.type === "Application" && n.business_domain) {
      const gid = `group:${n.business_domain}`;
      if (!groups.has(gid)) {
        groups.set(gid, n.business_area);
        g.setNode(gid, { paddingTop: GROUP_PAD + 8, paddingBottom: 12, paddingLeft: 12, paddingRight: 12 });
      }
      g.setParent(n.id, gid);
    }
  }
  for (const e of edges) g.setEdge(e.source, e.target);
  dagre.layout(g);

  const horizontal = direction === "LR";
  const out: Node[] = [];
  // dagre keeps members of a cluster together, but its cluster boxes are loose: draw tight ones around members
  const topLeft = new Map<string, { x: number; y: number }>();
  for (const [gid, area] of groups) {
    const members = g.children(gid).map((id) => g.node(id));
    const minX = Math.min(...members.map((m) => m.x - NODE_W / 2)) - 14;
    const minY = Math.min(...members.map((m) => m.y - NODE_H / 2)) - GROUP_PAD - 6;
    const maxX = Math.max(...members.map((m) => m.x + NODE_W / 2)) + 14;
    const maxY = Math.max(...members.map((m) => m.y + NODE_H / 2)) + 14;
    topLeft.set(gid, { x: minX, y: minY });
    out.push({
      id: gid, type: "domain", position: { x: minX, y: minY }, width: maxX - minX, height: maxY - minY,
      data: { label: gid.slice(6), area } satisfies GroupData, selectable: false, draggable: false, zIndex: -1,
    });
  }
  for (const n of nodes) {
    const p = g.node(n.id);
    const parent = g.parent(n.id) as string | undefined;
    const abs = { x: p.x - NODE_W / 2, y: p.y - NODE_H / 2 };
    const origin = parent ? topLeft.get(parent)! : { x: 0, y: 0 };
    out.push({
      id: n.id, type: "card", data: { node: n, dimmed: false, selected: false } satisfies CardData,
      width: NODE_W, height: NODE_H,
      position: { x: abs.x - origin.x, y: abs.y - origin.y }, ...(parent ? { parentId: parent, extent: "parent" as const } : {}),
      sourcePosition: horizontal ? Position.Right : Position.Bottom, targetPosition: horizontal ? Position.Left : Position.Top,
    });
  }
  return out;
}

export type Selection = { kind: "node"; node: GraphNode } | { kind: "edge"; edge: GraphEdge } | null;

/** Tracks Cloudscape's dark mode (applyMode toggles a class on <body>). */
function useCloudscapeDark() {
  const read = () => document.body.classList.contains("awsui-dark-mode") || document.documentElement.classList.contains("awsui-dark-mode");
  const [dark, setDark] = useState(read);
  useEffect(() => {
    const obs = new MutationObserver(() => setDark(read()));
    obs.observe(document.body, { attributes: true, attributeFilter: ["class"] });
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => obs.disconnect();
  }, []);
  return dark;
}

/** Fit the viewport once the cards are measured, and again whenever filters or layout change. */
function FitOnChange({ token }: { token: string }) {
  const { fitView } = useReactFlow();
  const initialized = useNodesInitialized();
  useEffect(() => {
    if (!initialized) return;
    const t = setTimeout(() => fitView({ padding: 0.08, duration: 200 }), 0);
    return () => clearTimeout(t);
  }, [token, initialized, fitView]);
  return null;
}

export default function DependencyCanvas({ nodes, edges, direction, grouped, selection, onSelect }: {
  nodes: GraphNode[];
  edges: GraphEdge[];
  direction: Direction;
  grouped: boolean;
  selection: Selection;
  onSelect: (s: Selection) => void;
}) {
  const dark = useCloudscapeDark();
  const laidOut = useMemo(() => layout(nodes, edges, direction, grouped), [nodes, edges, direction, grouped]);

  // neighbourhood highlight: the selected node, its edges and their other ends stay opaque
  const focus = useMemo(() => {
    if (!selection) return null;
    if (selection.kind === "edge") return { nodes: new Set([selection.edge.source, selection.edge.target]), edges: new Set([selection.edge.id]) };
    const ids = new Set([selection.node.id]);
    const es = new Set<string>();
    for (const e of edges) {
      if (e.source === selection.node.id || e.target === selection.node.id) {
        ids.add(e.source); ids.add(e.target); es.add(e.id);
      }
    }
    return { nodes: ids, edges: es };
  }, [selection, edges]);

  const flowNodes = useMemo(() => laidOut.map((n) => n.type !== "card" ? n : {
    ...n,
    data: {
      ...(n.data as CardData),
      dimmed: !!focus && !focus.nodes.has(n.id),
      selected: selection?.kind === "node" && selection.node.id === n.id,
    },
  }), [laidOut, focus, selection]);

  const flowEdges = useMemo<Edge<FlowEdgeData>[]>(() => edges.map((e) => {
    const selected = selection?.kind === "edge" && selection.edge.id === e.id;
    return {
      id: e.id, source: e.source, target: e.target, type: "dependency",
      data: { edge: e, dimmed: !!focus && !focus.edges.has(e.id), selected, focused: !!focus?.edges.has(e.id) },
      markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16, color: selected ? "#0972d3" : "#8c8c94" },
      animated: e.kind === "async" && !!focus?.edges.has(e.id),
    };
  }), [edges, focus, selection]);

  const byId = useMemo(() => new Map(nodes.map((n) => [n.id, n])), [nodes]);
  return (
    <ReactFlow
      nodes={flowNodes}
      edges={flowEdges}
      nodeTypes={nodeTypes}
      edgeTypes={edgeTypes}
      colorMode={dark ? "dark" : "light"}
      nodesConnectable={false}
      edgesReconnectable={false}
      elementsSelectable
      minZoom={0.15}
      maxZoom={2}
      fitView
      proOptions={{ hideAttribution: true }}
      onNodeClick={(_, n) => { const g = byId.get(n.id); if (g) onSelect({ kind: "node", node: g }); }}
      onEdgeClick={(_, e) => onSelect({ kind: "edge", edge: (e.data as FlowEdgeData).edge })}
      onPaneClick={() => onSelect(null)}
      style={{ background: awsui.colorBackgroundLayoutMain }}
    >
      <FitOnChange token={`${direction}-${grouped}-${nodes.length}-${edges.length}`} />
      <Background variant={BackgroundVariant.Dots} gap={18} size={1.2} color={awsui.colorBorderDividerDefault} />
      <MiniMap pannable zoomable nodeColor={(n) => n.type === "domain" ? "transparent" : MINIMAP[(n.data as CardData).node.type]}
        maskColor={dark ? "rgba(0, 7, 22, 0.45)" : "rgba(15, 20, 26, 0.08)"}
        style={{ background: awsui.colorBackgroundContainerContent, border: `1px solid ${awsui.colorBorderDividerDefault}`, borderRadius: 8 }} />
      <Controls showInteractive={false} />
    </ReactFlow>
  );
}

export const CATEGORIES = CATEGORY;
