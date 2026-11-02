import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Container from "@cloudscape-design/components/container";
import ContentLayout from "@cloudscape-design/components/content-layout";
import FormField from "@cloudscape-design/components/form-field";
import Grid from "@cloudscape-design/components/grid";
import Header from "@cloudscape-design/components/header";
import KeyValuePairs from "@cloudscape-design/components/key-value-pairs";
import Link from "@cloudscape-design/components/link";
import Multiselect from "@cloudscape-design/components/multiselect";
import SegmentedControl from "@cloudscape-design/components/segmented-control";
import Slider from "@cloudscape-design/components/slider";
import SpaceBetween from "@cloudscape-design/components/space-between";
import cytoscape, { Core } from "cytoscape";
import dagre from "cytoscape-dagre";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, GraphEdge, GraphNode } from "../api";
import { Confidence, EvidenceList, Loadable, NodeType } from "../components/common";
import { useAsync } from "../hooks";

cytoscape.use(dagre);

// Cloudscape visual palette (data-vis categorical colors)
const COLORS = { Application: "#688ae8", DataStore: "#2ea597", Topic: "#c33d69", ExternalSystem: "#8456ce" } as const;
const SHAPES = { Application: "round-rectangle", DataStore: "barrel", Topic: "tag", ExternalSystem: "hexagon" } as const;
const TYPES = Object.keys(COLORS) as GraphNode["type"][];

type Selection = { kind: "node"; node: GraphNode } | { kind: "edge"; edge: GraphEdge } | null;

export default function DependencyGraphPage() {
  const { data, loading, error } = useAsync(api.graph);
  const ref = useRef<HTMLDivElement>(null);
  const cyRef = useRef<Core>();
  const [types, setTypes] = useState<string[]>(TYPES);
  const [domains, setDomains] = useState<string[]>([]);
  const [minConf, setMinConf] = useState(0);
  const [layout, setLayout] = useState("dagre");
  const [selected, setSelected] = useState<Selection>(null);

  const allDomains = useMemo(
    () => [...new Set((data?.nodes ?? []).map((n) => n.business_domain).filter(Boolean) as string[])].sort(),
    [data],
  );

  const elements = useMemo(() => {
    if (!data) return [];
    const appDomain = (n: GraphNode) => !domains.length || n.type !== "Application" || domains.includes(n.business_domain ?? "");
    const nodes = data.nodes.filter((n) => types.includes(n.type) && appDomain(n));
    const ids = new Set(nodes.map((n) => n.id));
    const edges = data.edges.filter((e) => ids.has(e.source) && ids.has(e.target) && e.confidence >= minConf);
    // drop isolated non-application nodes created by filtering (e.g. a topic whose apps were filtered out)
    const linked = new Set(edges.flatMap((e) => [e.source, e.target]));
    return [
      ...nodes
        .filter((n) => n.type === "Application" || linked.has(n.id))
        .map((n) => ({ data: { ...n, label: n.type === "Application" && n.service_domain ? `${n.name}\n${n.service_domain}` : n.name } })),
      ...edges.map((e) => ({ data: { ...e, label: e.protocol } })),
    ];
  }, [data, types, domains, minConf]);

  useEffect(() => {
    if (!ref.current || !data) return;
    const cy = cytoscape({
      container: ref.current,
      elements,
      wheelSensitivity: 0.2,
      style: [
        {
          selector: "node",
          style: {
            label: "data(label)", "text-wrap": "wrap", "text-valign": "center", "text-halign": "center",
            "font-size": 10, color: "#fff", "text-outline-width": 0, width: 150, height: 46,
            "background-color": (n: any) => COLORS[n.data("type") as keyof typeof COLORS],
            shape: (n: any) => SHAPES[n.data("type") as keyof typeof SHAPES],
            "border-width": (n: any) => (n.data("review") ? 3 : 0), "border-color": "#ff9900",
          } as any,
        },
        {
          selector: "edge",
          style: {
            width: 1.6, "curve-style": "bezier", "target-arrow-shape": "triangle", "arrow-scale": 0.9,
            label: "data(label)", "font-size": 8, "text-background-opacity": 1, "text-background-color": "#ffffff",
            "line-color": "#8d99a8", "target-arrow-color": "#8d99a8",
            "line-style": (e: any) => (e.data("confidence") < 0.8 ? "dashed" : e.data("kind") === "async" ? "dotted" : "solid"),
          } as any,
        },
        { selector: ".faded", style: { opacity: 0.15 } },
        { selector: ":selected", style: { "border-width": 4, "border-color": "#0972d3", "line-color": "#0972d3", "target-arrow-color": "#0972d3" } },
      ],
      layout: layout === "dagre"
        ? ({ name: "dagre", rankDir: "LR", nodeSep: 30, rankSep: 90 } as any)
        : { name: "cose", animate: false, nodeRepulsion: () => 9000 },
    });
    cy.on("tap", "node", (evt) => {
      const n = evt.target;
      cy.elements().addClass("faded");
      n.closedNeighborhood().removeClass("faded");
      setSelected({ kind: "node", node: n.data() as GraphNode });
    });
    cy.on("tap", "edge", (evt) => setSelected({ kind: "edge", edge: evt.target.data() as GraphEdge }));
    cy.on("tap", (evt) => {
      if (evt.target === cy) {
        cy.elements().removeClass("faded");
        setSelected(null);
      }
    });
    cyRef.current = cy;
    return () => cy.destroy();
  }, [elements, layout, data]);

  return (
    <ContentLayout header={<Header variant="h1" description="Projeção do manifesto CALM no grafo LadybugDB. Clique num nó para destacar sua vizinhança; arestas tracejadas têm confiança < 0.8, pontilhadas são assíncronas.">Mapa de dependências</Header>}>
      <Loadable loading={loading} error={error}>
        <SpaceBetween size="m">
          <Container>
            <Grid gridDefinition={[{ colspan: 4 }, { colspan: 4 }, { colspan: 2 }, { colspan: 2 }]}>
              <FormField label="Tipos de nó">
                <Multiselect
                  selectedOptions={types.map((t) => ({ value: t, label: t }))}
                  options={TYPES.map((t) => ({ value: t, label: t }))}
                  onChange={({ detail }) => setTypes(detail.selectedOptions.map((o) => o.value!))}
                  placeholder="Tipos"
                />
              </FormField>
              <FormField label="Business Domain (BIAN)">
                <Multiselect
                  selectedOptions={domains.map((d) => ({ value: d, label: d }))}
                  options={allDomains.map((d) => ({ value: d, label: d }))}
                  onChange={({ detail }) => setDomains(detail.selectedOptions.map((o) => o.value!))}
                  placeholder="Todos"
                />
              </FormField>
              <FormField label={`Confiança mínima: ${minConf.toFixed(2)}`}>
                <Slider value={minConf} min={0} max={1} step={0.05} onChange={({ detail }) => setMinConf(detail.value)} />
              </FormField>
              <FormField label="Layout">
                <SegmentedControl
                  selectedId={layout}
                  options={[{ id: "dagre", text: "Fluxo" }, { id: "cose", text: "Força" }]}
                  onChange={({ detail }) => setLayout(detail.selectedId)}
                />
              </FormField>
            </Grid>
          </Container>
          <Grid gridDefinition={[{ colspan: { default: 12, l: 9 } }, { colspan: { default: 12, l: 3 } }]}>
            <Container
              header={<Header variant="h2" actions={<Button iconName="zoom-to-fit" onClick={() => cyRef.current?.fit(undefined, 30)}>Ajustar</Button>}>Grafo</Header>}
              disableContentPaddings
            >
              <div ref={ref} style={{ height: "68vh", width: "100%" }} />
              <Box padding="s">
                {TYPES.map((t) => (
                  <span key={t} style={{ marginRight: 16 }}>
                    <span style={{ display: "inline-block", width: 10, height: 10, borderRadius: 2, background: COLORS[t], marginRight: 6 }} />
                    {t}
                  </span>
                ))}
                <span>· borda laranja = revisão pendente</span>
              </Box>
            </Container>
            <Container header={<Header variant="h2">Detalhes</Header>}>
              {!selected && <Box color="text-status-inactive">Selecione um nó ou aresta.</Box>}
              {selected?.kind === "node" && (
                <KeyValuePairs
                  columns={1}
                  items={[
                    {
                      label: "Nome",
                      value: selected.node.type === "Application"
                        ? <Link href={`#/applications/${selected.node.id}`}>{selected.node.name}</Link> : selected.node.name,
                    },
                    { label: "Tipo", value: <NodeType type={selected.node.type} /> },
                    ...(selected.node.type === "Application" ? [
                      { label: "Service Domain", value: selected.node.service_domain ?? "—" },
                      { label: "Business Domain", value: selected.node.business_domain ?? "—" },
                      { label: "Business Area", value: selected.node.business_area ?? "—" },
                      { label: "Confiança", value: <Confidence value={selected.node.confidence} review={selected.node.review} /> },
                    ] : []),
                  ]}
                />
              )}
              {selected?.kind === "edge" && (
                <KeyValuePairs
                  columns={1}
                  items={[
                    { label: "De → Para", value: `${selected.edge.source} → ${selected.edge.target}` },
                    { label: "Tipo / protocolo", value: `${selected.edge.kind} / ${selected.edge.protocol}` },
                    { label: "Detalhe", value: selected.edge.description },
                    { label: "Confiança", value: <Confidence value={selected.edge.confidence} /> },
                    { label: "Regra", value: <Box variant="code">{selected.edge.rule}</Box> },
                    { label: "Evidência", value: <EvidenceList refs={selected.edge.evidence} /> },
                  ]}
                />
              )}
            </Container>
          </Grid>
        </SpaceBetween>
      </Loadable>
    </ContentLayout>
  );
}
