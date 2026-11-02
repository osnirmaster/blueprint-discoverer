import Box from "@cloudscape-design/components/box";
import ColumnLayout from "@cloudscape-design/components/column-layout";
import Container from "@cloudscape-design/components/container";
import ContentLayout from "@cloudscape-design/components/content-layout";
import Header from "@cloudscape-design/components/header";
import Link from "@cloudscape-design/components/link";
import PieChart from "@cloudscape-design/components/pie-chart";
import SpaceBetween from "@cloudscape-design/components/space-between";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import Table from "@cloudscape-design/components/table";
import { useNavigate } from "react-router-dom";
import { api, Application, Summary } from "../api";
import { Confidence, Loadable } from "../components/common";
import { useAsync } from "../hooks";

function Counter({ label, value }: { label: string; value?: number }) {
  return (
    <div>
      <Box variant="awsui-key-label">{label}</Box>
      <Box variant="awsui-value-large">{value ?? 0}</Box>
    </div>
  );
}

export default function OverviewPage() {
  const navigate = useNavigate();
  const summary = useAsync<Summary>(api.summary);
  const apps = useAsync<Application[]>(api.applications);
  const stats = summary.data?.validation?.stats ?? {};
  const byDomain = new Map<string, number>();
  apps.data?.forEach((a) => {
    const d = a.bian["business-domain"] ?? "Não classificado";
    byDomain.set(d, (byDomain.get(d) ?? 0) + 1);
  });
  const review = apps.data?.filter((a) => a.semantics["review-required"]) ?? [];

  return (
    <ContentLayout header={<Header variant="h1" description={summary.data?.description}>{summary.data?.name ?? "Visão geral"}</Header>}>
      <Loadable loading={summary.loading || apps.loading} error={summary.error ?? apps.error}>
        <SpaceBetween size="l">
          <Container header={<Header variant="h2">Paisagem</Header>}>
            <ColumnLayout columns={5} variant="text-grid">
              <Counter label="Aplicações" value={stats.services} />
              <Counter label="Relacionamentos" value={stats.relationships} />
              <Counter label="Bancos de dados" value={stats.databases} />
              <Counter label="Tópicos/filas" value={stats.topics} />
              <Counter label="Sistemas externos" value={stats["external-systems"]} />
            </ColumnLayout>
          </Container>
          <ColumnLayout columns={2}>
            <Container header={<Header variant="h2">Manifesto CALM</Header>}>
              <SpaceBetween size="s">
                <StatusIndicator type={summary.data?.validation?.valid ? "success" : "error"}>
                  {summary.data?.validation?.valid ? "Válido (schema + referências + perfil)" : "Inválido"}
                </StatusIndicator>
                <Box>
                  {summary.data?.validation?.errors?.length ?? 0} erros ·{" "}
                  {summary.data?.validation?.warnings?.length ?? 0} avisos ·{" "}
                  <Link onFollow={(e) => { e.preventDefault(); navigate("/calm"); }} href="#/calm">ver manifesto</Link>
                </Box>
              </SpaceBetween>
            </Container>
            <Container header={<Header variant="h2">Aplicações por Business Domain (BIAN)</Header>}>
              <PieChart
                size="small"
                variant="donut"
                hideFilter
                data={[...byDomain.entries()].map(([title, value]) => ({ title, value }))}
                innerMetricValue={String(apps.data?.length ?? 0)}
                innerMetricDescription="apps"
                empty={<Box textAlign="center">Sem dados</Box>}
              />
            </Container>
          </ColumnLayout>
          <Table
            header={<Header variant="h2" counter={`(${review.length})`} description="Classificações com baixa confiança ou sem suporte determinístico">Fila de revisão</Header>}
            items={review}
            empty={<Box textAlign="center">Nada pendente de revisão</Box>}
            columnDefinitions={[
              { id: "name", header: "Aplicação", cell: (a) => <Link href={`#/applications/${a.id}`}>{a.name}</Link> },
              { id: "sd", header: "Service Domain", cell: (a) => a.bian["service-domain"] ?? "—" },
              { id: "conf", header: "Confiança", cell: (a) => <Confidence value={a.semantics.confidence} review /> },
              { id: "why", header: "Racional", cell: (a) => a.semantics.rationale },
            ]}
          />
        </SpaceBetween>
      </Loadable>
    </ContentLayout>
  );
}
