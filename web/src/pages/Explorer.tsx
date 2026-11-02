import Alert from "@cloudscape-design/components/alert";
import Button from "@cloudscape-design/components/button";
import Container from "@cloudscape-design/components/container";
import ContentLayout from "@cloudscape-design/components/content-layout";
import Header from "@cloudscape-design/components/header";
import Select from "@cloudscape-design/components/select";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Table from "@cloudscape-design/components/table";
import Textarea from "@cloudscape-design/components/textarea";
import { useState } from "react";
import { api } from "../api";

const EXAMPLES = [
  { label: "Quem depende de accounts-service (transitivo)", value: "MATCH (a)-[:DEPENDS_ON*1..6]->(b:Application {id: 'accounts-service'}) RETURN DISTINCT a.id, label(a)" },
  { label: "Aplicações por Business Domain", value: "MATCH (a:Application)-[r:REALIZES {is_primary: true}]->(s:ServiceDomain)-[:IN_BUSINESS_DOMAIN]->(d:BusinessDomain) RETURN d.name AS domain, collect(a.name) AS apps ORDER BY domain" },
  { label: "Fluxos assíncronos produtor → consumidor", value: "MATCH (p:Application)-[:DEPENDS_ON]->(t:Topic)-[:DEPENDS_ON]->(c:Application) RETURN p.name AS producer, t.name AS topic, c.name AS consumer" },
  { label: "Bancos compartilhados", value: "MATCH (a:Application)-[:DEPENDS_ON]->(d:DataStore) WITH d, collect(a.id) AS apps WHERE size(apps) > 1 RETURN d.name, apps" },
  { label: "Dependências de baixa confiança", value: "MATCH (a)-[e:DEPENDS_ON]->(b) WHERE e.confidence < 0.8 RETURN a.id, b.id, e.rule, e.confidence" },
];

export default function ExplorerPage() {
  const [query, setQuery] = useState(EXAMPLES[0].value);
  const [rows, setRows] = useState<Record<string, unknown>[]>([]);
  const [error, setError] = useState<string>();
  const [running, setRunning] = useState(false);
  const run = async () => {
    setRunning(true);
    setError(undefined);
    try {
      setRows(await api.cypher(query));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRunning(false);
    }
  };
  const cols = rows.length ? Object.keys(rows[0]) : [];

  return (
    <ContentLayout header={<Header variant="h1" description="Consultas Cypher somente-leitura sobre o grafo LadybugDB (Application, DataStore, Topic, ExternalSystem, Endpoint, ServiceDomain, BusinessDomain, BusinessArea).">Explorador Cypher</Header>}>
      <SpaceBetween size="l">
        <Container>
          <SpaceBetween size="s">
            <Select
              selectedOption={null}
              placeholder="Exemplos"
              options={EXAMPLES}
              onChange={({ detail }) => setQuery(detail.selectedOption.value!)}
            />
            <Textarea value={query} rows={4} onChange={({ detail }) => setQuery(detail.value)} spellcheck={false} />
            <Button variant="primary" loading={running} onClick={run}>Executar</Button>
            {error && <Alert type="error">{error}</Alert>}
          </SpaceBetween>
        </Container>
        <Table
          header={<Header variant="h2" counter={`(${rows.length})`}>Resultado</Header>}
          items={rows}
          wrapLines
          columnDefinitions={cols.map((c) => ({
            id: c, header: c,
            cell: (r: Record<string, unknown>) => (typeof r[c] === "object" ? JSON.stringify(r[c]) : String(r[c])),
          }))}
        />
      </SpaceBetween>
    </ContentLayout>
  );
}
