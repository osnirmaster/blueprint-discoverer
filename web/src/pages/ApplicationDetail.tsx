import Box from "@cloudscape-design/components/box";
import ColumnLayout from "@cloudscape-design/components/column-layout";
import Container from "@cloudscape-design/components/container";
import ContentLayout from "@cloudscape-design/components/content-layout";
import Header from "@cloudscape-design/components/header";
import KeyValuePairs from "@cloudscape-design/components/key-value-pairs";
import Link from "@cloudscape-design/components/link";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Table from "@cloudscape-design/components/table";
import Tabs from "@cloudscape-design/components/tabs";
import { CodeView } from "@cloudscape-design/code-view";
import jsonHighlight from "@cloudscape-design/code-view/highlight/json";
import { useParams } from "react-router-dom";
import { api, ApplicationDetail, CalmRelationship } from "../api";
import { Confidence, EvidenceList, Loadable, NodeType } from "../components/common";
import { useAsync } from "../hooks";

function RelTable({ title, rels }: { title: string; rels: CalmRelationship[] }) {
  return (
    <Table
      header={<Header variant="h3" counter={`(${rels.length})`}>{title}</Header>}
      items={rels}
      wrapLines
      empty={<Box textAlign="center">Nenhum</Box>}
      columnDefinitions={[
        {
          id: "other", header: "Componente",
          cell: (r) => r.other && ["service", "webclient"].includes(r.other["node-type"])
            ? <Link href={`#/applications/${r.other["unique-id"]}`}>{r.other.name}</Link> : r.other?.name,
        },
        { id: "type", header: "Tipo", cell: (r) => r.other && <NodeType type={r.other["node-type"]} /> },
        { id: "protocol", header: "Protocolo", cell: (r) => r.protocol },
        { id: "desc", header: "Detalhe", cell: (r) => r.description },
        { id: "conf", header: "Confiança", cell: (r) => <Confidence value={r.metadata?.confidence} /> },
        { id: "rule", header: "Regra", cell: (r) => <Box variant="code">{r.metadata?.rule}</Box> },
        { id: "ev", header: "Evidência", cell: (r) => <EvidenceList refs={r.metadata?.evidence} /> },
      ]}
    />
  );
}

export default function ApplicationDetailPage() {
  const { id = "" } = useParams();
  const { data, loading, error } = useAsync<ApplicationDetail>(() => api.application(id), [id]);
  const node = data?.node;
  const meta = node?.metadata ?? {};
  const sem = meta.semantics ?? {};
  const bian = meta.bian ?? {};
  const endpoints = node?.interfaces?.filter((i) => i.path) ?? [];

  return (
    <Loadable loading={loading} error={error}>
      {node && (
        <ContentLayout header={<Header variant="h1" description={sem.purpose ?? node.description} actions={<NodeType type={node["node-type"]} />}>{node.name}</Header>}>
          <Tabs
            tabs={[
              {
                id: "overview", label: "Visão geral",
                content: (
                  <SpaceBetween size="l">
                    <Container header={<Header variant="h2">Classificação BIAN</Header>}>
                      <KeyValuePairs
                        columns={4}
                        items={[
                          { label: "Business Area", value: bian["business-area"] ?? "—" },
                          { label: "Business Domain", value: bian["business-domain"] ?? "—" },
                          { label: "Service Domain", value: bian["service-domain"] ?? "—" },
                          { label: "Confiança", value: <Confidence value={sem.confidence} review={sem["review-required"]} /> },
                          { label: "Método", value: sem.method },
                          { label: "Secundários", value: (bian.secondary ?? []).map((s: any) => `${s["service-domain"]} (${s.score.toFixed(2)})`).join(", ") || "—" },
                          { label: "Racional", value: sem.rationale },
                        ]}
                      />
                    </Container>
                    <ColumnLayout columns={3}>
                      <Container header={<Header variant="h3">Objetivos</Header>}>
                        {(sem.objectives ?? []).length ? <ul>{sem.objectives.map((o: string) => <li key={o}>{o}</li>)}</ul> : <Box color="text-status-inactive">Habilite a camada LLM para inferir objetivos.</Box>}
                      </Container>
                      <Container header={<Header variant="h3">Capacidades de negócio</Header>}>
                        <ul>{(sem["business-capabilities"] ?? []).map((c: string) => <li key={c}>{c}</li>)}</ul>
                      </Container>
                      <Container header={<Header variant="h3">Funcionalidades</Header>}>
                        <ul>{(sem["business-functions"] ?? []).map((f: string) => <li key={f}>{f}</li>)}</ul>
                      </Container>
                    </ColumnLayout>
                    <Container header={<Header variant="h2">Origem</Header>}>
                      <KeyValuePairs
                        columns={3}
                        items={[
                          { label: "Repositório", value: meta.source?.repository },
                          { label: "Caminho", value: meta.source?.path },
                          { label: "Commit", value: (meta.source?.commit ?? "—").slice(0, 12) },
                          { label: "Linguagens", value: (meta.technology?.languages ?? []).join(", ") },
                          { label: "Frameworks", value: (meta.technology?.frameworks ?? []).join(", ") },
                          { label: "Aliases de rede", value: (meta.aliases ?? []).join(", ") },
                        ]}
                      />
                    </Container>
                    <Container header={<Header variant="h3">Evidências da classificação</Header>}>
                      <EvidenceList refs={bian.evidence} />
                    </Container>
                  </SpaceBetween>
                ),
              },
              {
                id: "apis", label: `APIs (${endpoints.length})`,
                content: (
                  <Table
                    items={endpoints}
                    columnDefinitions={[
                      { id: "m", header: "Método", cell: (e) => e.method },
                      { id: "p", header: "Caminho", cell: (e) => <Box variant="code">{e.path}</Box> },
                      { id: "h", header: "Handler", cell: (e) => e.handler || "—" },
                      { id: "ev", header: "Evidência", cell: (e) => <EvidenceList refs={e.evidence ? [e.evidence] : []} /> },
                    ]}
                  />
                ),
              },
              {
                id: "deps", label: "Dependências",
                content: (
                  <SpaceBetween size="l">
                    <RelTable title="Depende de" rels={data.outbound} />
                    <RelTable title="Usado por" rels={data.inbound} />
                  </SpaceBetween>
                ),
              },
              {
                id: "impact", label: `Impacto (${data.impact.length})`,
                content: (
                  <ColumnLayout columns={2}>
                    <Table header={<Header variant="h3" description="Quem quebra se este componente mudar">Impactados (transitivo)</Header>} items={data.impact}
                      columnDefinitions={[{ id: "n", header: "Componente", cell: (i) => i.name }, { id: "t", header: "Tipo", cell: (i) => <NodeType type={i.type} /> }]} />
                    <Table header={<Header variant="h3" description="De quem este componente depende">Dependências (transitivo)</Header>} items={data.dependencies}
                      columnDefinitions={[{ id: "n", header: "Componente", cell: (i) => i.name }, { id: "t", header: "Tipo", cell: (i) => <NodeType type={i.type} /> }]} />
                  </ColumnLayout>
                ),
              },
              {
                id: "calm", label: "Nó CALM",
                content: <CodeView lineNumbers highlight={jsonHighlight} content={JSON.stringify(node, null, 2)} />,
              },
            ]}
          />
        </ContentLayout>
      )}
    </Loadable>
  );
}
