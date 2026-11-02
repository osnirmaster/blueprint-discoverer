import { CodeView } from "@cloudscape-design/code-view";
import jsonHighlight from "@cloudscape-design/code-view/highlight/json";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import ContentLayout from "@cloudscape-design/components/content-layout";
import Header from "@cloudscape-design/components/header";
import SpaceBetween from "@cloudscape-design/components/space-between";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import Table from "@cloudscape-design/components/table";
import Tabs from "@cloudscape-design/components/tabs";
import { api, Issue, Validation } from "../api";
import { Loadable } from "../components/common";
import { useAsync } from "../hooks";

export default function CalmPage() {
  const doc = useAsync(api.architecture);
  const val = useAsync<Validation>(api.validation);
  const issues: Issue[] = [...(val.data?.errors ?? []), ...(val.data?.warnings ?? [])];
  const json = doc.data ? JSON.stringify(doc.data, null, 2) : "";
  const download = () => {
    const url = URL.createObjectURL(new Blob([json], { type: "application/json" }));
    Object.assign(document.createElement("a"), { href: url, download: "architecture.calm.json" }).click();
    URL.revokeObjectURL(url);
  };

  return (
    <ContentLayout
      header={
        <Header variant="h1" description="Fonte da verdade da arquitetura as-is (FINOS CALM 1.0). Grafo e wiki são projeções deste documento."
          actions={<Button iconName="download" onClick={download} disabled={!json}>Baixar JSON</Button>}>
          Manifesto CALM
        </Header>
      }
    >
      <Loadable loading={doc.loading || val.loading} error={doc.error ?? val.error}>
        <Tabs
          tabs={[
            {
              id: "validation", label: `Validação (${issues.length})`,
              content: (
                <SpaceBetween size="m">
                  <StatusIndicator type={val.data?.valid ? "success" : "error"}>
                    {val.data?.valid ? "Válido" : "Inválido"} — schema JSON CALM 1.0, integridade referencial e perfil Blueprint (RULES.md §5)
                  </StatusIndicator>
                  <Table
                    items={issues}
                    wrapLines
                    empty={<Box textAlign="center">Sem erros ou avisos</Box>}
                    columnDefinitions={[
                      { id: "level", header: "Nível", cell: (i) => <StatusIndicator type={i.level === "error" ? "error" : "warning"}>{i.level}</StatusIndicator> },
                      { id: "layer", header: "Camada", cell: (i) => i.layer },
                      { id: "path", header: "Caminho", cell: (i) => <Box variant="code">{i.path}</Box> },
                      { id: "msg", header: "Mensagem", cell: (i) => i.message },
                    ]}
                  />
                </SpaceBetween>
              ),
            },
            { id: "json", label: "Documento", content: <CodeView lineNumbers highlight={jsonHighlight} content={json} /> },
          ]}
        />
      </Loadable>
    </ContentLayout>
  );
}
