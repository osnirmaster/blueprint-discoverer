import Box from "@cloudscape-design/components/box";
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
import Toggle from "@cloudscape-design/components/toggle";
import { useMemo, useState } from "react";
import { api, GraphNode } from "../api";
import DependencyCanvas, { CATEGORIES, Direction, Selection } from "../components/DependencyCanvas";
import { Confidence, EvidenceList, Loadable, NodeType } from "../components/common";
import { useAsync } from "../hooks";

const TYPES = Object.keys(CATEGORIES) as GraphNode["type"][];
const KIND_LABEL: Record<string, string> = {
  http: "REST", grpc: "gRPC", graphql: "GraphQL", soap: "SOAP", async: "Assíncrona", data: "Dados", external: "Externa",
};

export default function DependencyGraphPage() {
  const { data, loading, error } = useAsync(api.graph);
  const [types, setTypes] = useState<string[]>(TYPES);
  const [domains, setDomains] = useState<string[]>([]);
  const [minConf, setMinConf] = useState(0);
  const [direction, setDirection] = useState<Direction>("LR");
  const [grouped, setGrouped] = useState(true);
  const [selected, setSelected] = useState<Selection>(null);

  const allDomains = useMemo(
    () => [...new Set((data?.nodes ?? []).map((n) => n.business_domain).filter(Boolean) as string[])].sort(),
    [data],
  );

  const view = useMemo(() => {
    if (!data) return { nodes: [], edges: [] };
    const appDomain = (n: GraphNode) => !domains.length || n.type !== "Application" || domains.includes(n.business_domain ?? "");
    const nodes = data.nodes.filter((n) => types.includes(n.type) && appDomain(n));
    const ids = new Set(nodes.map((n) => n.id));
    const edges = data.edges.filter((e) => ids.has(e.source) && ids.has(e.target) && e.confidence >= minConf);
    // drop isolated non-application nodes created by filtering (e.g. a queue whose apps were filtered out)
    const linked = new Set(edges.flatMap((e) => [e.source, e.target]));
    return { nodes: nodes.filter((n) => n.type === "Application" || linked.has(n.id)), edges };
  }, [data, types, domains, minConf]);

  const names = useMemo(() => new Map((data?.nodes ?? []).map((n) => [n.id, n.name])), [data]);

  return (
    <ContentLayout
      header={
        <Header variant="h1" description="Projeção do manifesto CALM no grafo LadybugDB, do downstream (quem chama) para o upstream (quem provê). Clique numa caixa para destacar suas dependências; linhas tracejadas têm confiança < 0.8 e pontilhadas são assíncronas.">
          Mapa de dependências
        </Header>
      }
    >
      <Loadable loading={loading} error={error}>
        <SpaceBetween size="m">
          <Container>
            <Grid gridDefinition={[{ colspan: { default: 12, m: 4 } }, { colspan: { default: 12, m: 3 } }, { colspan: { default: 6, m: 2 } }, { colspan: { default: 6, m: 3 } }]}>
              <FormField label="Tipos de nó">
                <Multiselect
                  selectedOptions={types.map((t) => ({ value: t, label: CATEGORIES[t as GraphNode["type"]].label }))}
                  options={TYPES.map((t) => ({ value: t, label: CATEGORIES[t].label }))}
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
              <FormField label="Disposição">
                <SpaceBetween size="xs">
                  <SegmentedControl
                    selectedId={direction}
                    options={[{ id: "LR", text: "Horizontal" }, { id: "TB", text: "Vertical" }]}
                    onChange={({ detail }) => setDirection(detail.selectedId as Direction)}
                  />
                  <Toggle checked={grouped} onChange={({ detail }) => setGrouped(detail.checked)}>Agrupar por domínio BIAN</Toggle>
                </SpaceBetween>
              </FormField>
            </Grid>
          </Container>
          <Grid gridDefinition={[{ colspan: { default: 12, l: 9 } }, { colspan: { default: 12, l: 3 } }]}>
            <Container
              header={<Header variant="h2" counter={`(${view.nodes.length} nós · ${view.edges.length} dependências)`}>Canvas</Header>}
              disableContentPaddings
              footer={
                <Box fontSize="body-s" color="text-body-secondary">
                  {TYPES.map((t) => (
                    <span key={t} style={{ marginRight: 16, whiteSpace: "nowrap" }}>
                      <span style={{ display: "inline-block", width: 10, height: 10, borderRadius: 3, background: CATEGORIES[t].color, marginRight: 6 }} />
                      {CATEGORIES[t].label}
                    </span>
                  ))}
                  <span>· borda âmbar = classificação BIAN a revisar</span>
                </Box>
              }
            >
              <div style={{ height: "70vh", width: "100%" }}>
                {data && (
                  <DependencyCanvas nodes={view.nodes} edges={view.edges} direction={direction} grouped={grouped}
                    selection={selected} onSelect={setSelected} />
                )}
              </div>
            </Container>
            <Container header={<Header variant="h2">Detalhes</Header>}>
              {!selected && <Box color="text-status-inactive">Selecione uma caixa ou uma dependência.</Box>}
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
                      { label: "Linguagens", value: selected.node.languages?.join(", ") || "—" },
                      { label: "Repositório", value: selected.node.repo || "—" },
                      { label: "Service Domain", value: selected.node.service_domain ?? "—" },
                      { label: "Business Domain", value: selected.node.business_domain ?? "—" },
                      { label: "Business Area", value: selected.node.business_area ?? "—" },
                      { label: "Confiança", value: <Confidence value={selected.node.confidence} review={selected.node.review} /> },
                    ] : [
                      { label: "Tecnologia", value: selected.node.technology || "—" },
                    ]),
                  ]}
                />
              )}
              {selected?.kind === "edge" && (
                <KeyValuePairs
                  columns={1}
                  items={[
                    { label: "Downstream (chama)", value: names.get(selected.edge.source) ?? selected.edge.source },
                    { label: "Upstream (provê)", value: names.get(selected.edge.target) ?? selected.edge.target },
                    { label: "Dependência / protocolo", value: `${KIND_LABEL[selected.edge.kind] ?? selected.edge.kind} / ${selected.edge.protocol}` },
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
