import Alert from "@cloudscape-design/components/alert";
import Badge from "@cloudscape-design/components/badge";
import Box from "@cloudscape-design/components/box";
import Spinner from "@cloudscape-design/components/spinner";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import { ReactNode } from "react";

export function Loadable({ loading, error, children }: { loading: boolean; error?: string; children: ReactNode }) {
  if (loading) return <Box textAlign="center" padding="xxl"><Spinner size="large" /></Box>;
  if (error)
    return (
      <Alert type="error" header="Falha ao carregar dados">
        {error}. Rode <code>blueprint run</code> e <code>blueprint serve</code>.
      </Alert>
    );
  return <>{children}</>;
}

export function Confidence({ value, review }: { value?: number; review?: boolean }) {
  const v = value ?? 0;
  const type = review ? "warning" : v >= 0.75 ? "success" : v >= 0.5 ? "info" : "warning";
  return <StatusIndicator type={type}>{v.toFixed(2)}{review ? " · revisar" : ""}</StatusIndicator>;
}

const NODE_COLORS: Record<string, "blue" | "grey" | "green" | "red" | "severity-medium"> = {
  service: "blue", webclient: "blue", Application: "blue", database: "green", DataStore: "green",
  "data-asset": "severity-medium", Topic: "severity-medium", system: "red", ExternalSystem: "red",
};
const NODE_LABELS: Record<string, string> = {
  service: "Serviço", webclient: "Front-end", Application: "Aplicação", database: "Banco de dados",
  DataStore: "Banco de dados", "data-asset": "Tópico/Fila", Topic: "Tópico/Fila", system: "Externo",
  ExternalSystem: "Externo", ecosystem: "Domínio BIAN",
};

export function NodeType({ type }: { type: string }) {
  return <Badge color={NODE_COLORS[type] ?? "grey"}>{NODE_LABELS[type] ?? type}</Badge>;
}

export const nodeLabel = (t: string) => NODE_LABELS[t] ?? t;

/** Evidence refs look like `repo:path/to/file.ext:line`. */
export function EvidenceList({ refs }: { refs?: string[] }) {
  if (!refs?.length) return <Box color="text-status-inactive">—</Box>;
  return (
    <Box variant="code" fontSize="body-s">
      {refs.map((r) => (
        <div key={r}>{r}</div>
      ))}
    </Box>
  );
}
