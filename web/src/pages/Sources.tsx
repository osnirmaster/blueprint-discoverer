import Alert from "@cloudscape-design/components/alert";
import Badge from "@cloudscape-design/components/badge";
import Box from "@cloudscape-design/components/box";
import BreadcrumbGroup from "@cloudscape-design/components/breadcrumb-group";
import Button from "@cloudscape-design/components/button";
import Checkbox from "@cloudscape-design/components/checkbox";
import ColumnLayout from "@cloudscape-design/components/column-layout";
import Container from "@cloudscape-design/components/container";
import ExpandableSection from "@cloudscape-design/components/expandable-section";
import FormField from "@cloudscape-design/components/form-field";
import Header from "@cloudscape-design/components/header";
import Input from "@cloudscape-design/components/input";
import Modal from "@cloudscape-design/components/modal";
import SpaceBetween from "@cloudscape-design/components/space-between";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import Table from "@cloudscape-design/components/table";
import Tabs from "@cloudscape-design/components/tabs";
import { useCallback, useEffect, useState } from "react";
import { api, Discovered, Folder, RepoRequest, ScanJob, SourceRepo, Sources } from "../api";
import { Loadable } from "../components/common";

const STATUS: Record<ScanJob["status"], "pending" | "in-progress" | "success" | "error"> = {
  queued: "pending", running: "in-progress", succeeded: "success", failed: "error",
};
const STATUS_TEXT: Record<ScanJob["status"], string> = {
  queued: "Na fila", running: "Escaneando", succeeded: "Concluído", failed: "Falhou",
};

/** Folder picker limited to the server's browsable root (directories only). */
function FolderBrowser({ visible, onClose, onSelect }: {
  visible: boolean; onClose: () => void; onSelect: (path: string) => void;
}) {
  const [folder, setFolder] = useState<Folder>();
  const [error, setError] = useState<string>();
  const open = useCallback((path?: string) => {
    setError(undefined);
    api.browse(path).then(setFolder).catch((e: Error) => setError(e.message));
  }, []);
  useEffect(() => { if (visible) open(folder?.path); }, [visible]); // eslint-disable-line react-hooks/exhaustive-deps

  const crumbs = (folder?.path ?? "").split("/").filter(Boolean)
    .map((part, i, all) => ({ text: part, href: "/" + all.slice(0, i + 1).join("/") }));
  return (
    <Modal
      visible={visible}
      onDismiss={onClose}
      size="large"
      header="Selecionar pasta"
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button variant="link" onClick={onClose}>Cancelar</Button>
            <Button variant="primary" disabled={!folder} onClick={() => { if (folder) onSelect(folder.path); onClose(); }}>
              Usar esta pasta
            </Button>
          </SpaceBetween>
        </Box>
      }
    >
      <SpaceBetween size="s">
        {error && <Alert type="error">{error}</Alert>}
        <BreadcrumbGroup items={crumbs} onFollow={(e) => { e.preventDefault(); open(e.detail.href); }} />
        <Table
          variant="embedded"
          items={folder?.entries ?? []}
          empty={<Box textAlign="center" color="inherit">Nenhuma subpasta</Box>}
          header={
            <Header actions={<Button iconName="arrow-left" disabled={!folder?.parent} onClick={() => open(folder?.parent ?? undefined)}>Subir</Button>}>
              {folder?.path}
            </Header>
          }
          columnDefinitions={[
            { id: "name", header: "Pasta", cell: (e) => <Button variant="inline-link" onClick={() => open(e.path)}>{e.name}</Button> },
            {
              id: "kind", header: "Tipo",
              cell: (e) => (
                <SpaceBetween direction="horizontal" size="xxs">
                  {e.git && <Badge color="blue">git</Badge>}
                  {e.manifest && <Badge color="green">projeto</Badge>}
                </SpaceBetween>
              ),
            },
          ]}
        />
      </SpaceBetween>
    </Modal>
  );
}

function PathInput({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder: string }) {
  const [browsing, setBrowsing] = useState(false);
  return (
    <>
      <SpaceBetween direction="horizontal" size="xs">
        <div style={{ minWidth: 420 }}>
          <Input value={value} onChange={(e) => onChange(e.detail.value)} placeholder={placeholder} />
        </div>
        <Button iconName="folder-open" onClick={() => setBrowsing(true)}>Procurar…</Button>
      </SpaceBetween>
      <FolderBrowser visible={browsing} onClose={() => setBrowsing(false)} onSelect={onChange} />
    </>
  );
}

function origin(r: SourceRepo) {
  return r.url ? `${r.url}${r.ref ? ` @ ${r.ref}` : ""}` : r.path ?? "—";
}

export default function SourcesPage() {
  const [sources, setSources] = useState<Sources>();
  const [jobs, setJobs] = useState<ScanJob[]>([]);
  const [loadError, setLoadError] = useState<string>();
  const [actionError, setActionError] = useState<string>();
  const [selected, setSelected] = useState<SourceRepo[]>([]);
  const [save, setSave] = useState(true);
  const [tab, setTab] = useState("folder");
  const [folderPath, setFolderPath] = useState("");
  const [folderName, setFolderName] = useState("");
  const [discoverPath, setDiscoverPath] = useState("");
  const [discovered, setDiscovered] = useState<Discovered[]>([]);
  const [picked, setPicked] = useState<Discovered[]>([]);
  const [gitUrl, setGitUrl] = useState("");
  const [gitRef, setGitRef] = useState("");
  const [gitName, setGitName] = useState("");

  const refresh = useCallback(() => {
    api.sources().then(setSources).catch((e: Error) => setLoadError(e.message));
    api.scans().then(setJobs).catch(() => undefined);
  }, []);
  useEffect(refresh, [refresh]);

  const running = jobs.some((j) => j.status === "queued" || j.status === "running");
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(refresh, 1500);
    return () => clearInterval(timer);
  }, [running, refresh]);

  const scan = (repositories: RepoRequest[], full = false) => {
    setActionError(undefined);
    api.startScan({ repositories, full, save: save && repositories.length > 0 })
      .then(() => { setSelected([]); setPicked([]); refresh(); })
      .catch((e: Error) => setActionError(e.message));
  };

  const addRequests = (): RepoRequest[] => {
    if (tab === "folder") return folderPath ? [{ path: folderPath, name: folderName || undefined }] : [];
    if (tab === "discover") return picked.map((d) => ({ path: d.path, name: d.name }));
    return gitUrl ? [{ url: gitUrl, ref: gitRef || undefined, name: gitName || undefined }] : [];
  };

  return (
    <Loadable loading={!sources && !loadError} error={loadError}>
      <SpaceBetween size="l">
        <Header
          variant="h1"
          description="Escolha repositórios e pastas para escanear. Escanear só os selecionados reaproveita os fatos já coletados dos demais."
          actions={<Button iconName="refresh" disabled={running} onClick={() => scan([], true)}>Escanear tudo</Button>}
        >
          Fontes e scans
        </Header>
        {actionError && <Alert type="error" dismissible onDismiss={() => setActionError(undefined)}>{actionError}</Alert>}
        {sources && (
          <Container>
            <ColumnLayout columns={3} variant="text-grid">
              <div><Box variant="awsui-key-label">Arquivo do landscape</Box>{sources.config ?? "—"}{sources.config && !sources.config_exists && " (será criado)"}</div>
              <div><Box variant="awsui-key-label">Workspace</Box>{sources.workspace}</div>
              <div><Box variant="awsui-key-label">Pastas navegáveis</Box>{sources.fs_root}</div>
            </ColumnLayout>
          </Container>
        )}

        <Table
          selectionType="multi"
          selectedItems={selected}
          onSelectionChange={(e) => setSelected(e.detail.selectedItems)}
          trackBy="name"
          items={sources?.repositories ?? []}
          empty={<Box textAlign="center">Nenhum repositório ainda. Adicione uma pasta ou URL abaixo.</Box>}
          header={
            <Header
              counter={`(${sources?.repositories.length ?? 0})`}
              description="Repositórios que compõem o landscape"
              actions={
                <Button variant="primary" disabled={!selected.length || running}
                  onClick={() => scan(selected.map((r) => ({ name: r.name })))}>
                  Escanear selecionados
                </Button>
              }
            >
              Repositórios
            </Header>
          }
          columnDefinitions={[
            { id: "name", header: "Nome", cell: (r) => r.name },
            { id: "origin", header: "Origem", cell: (r) => <Box variant="code" fontSize="body-s">{origin(r)}</Box> },
            {
              id: "status", header: "Status",
              cell: (r) => r.scanned
                ? <StatusIndicator type="success">Escaneado{r.commit ? ` · ${r.commit}` : ""}</StatusIndicator>
                : <StatusIndicator type="pending">Pendente</StatusIndicator>,
            },
            { id: "components", header: "Aplicações", cell: (r) => r.components.join(", ") || "—" },
            { id: "iac", header: "Recursos IaC", cell: (r) => r.infrastructure || "—" },
            { id: "files", header: "Arquivos", cell: (r) => r.files_scanned || "—" },
          ]}
        />

        <Container header={<Header description="Novos repositórios entram no landscape e são escaneados em seguida">Adicionar e escanear</Header>}>
          <SpaceBetween size="m">
            <Tabs
              activeTabId={tab}
              onChange={(e) => setTab(e.detail.activeTabId)}
              tabs={[
                {
                  id: "folder", label: "Pasta local",
                  content: (
                    <SpaceBetween size="m">
                      <FormField label="Pasta do repositório" description="Um repositório (com ou sem git) ou uma aplicação">
                        <PathInput value={folderPath} onChange={setFolderPath} placeholder="/caminho/para/repositorio" />
                      </FormField>
                      <FormField label="Nome" description="Opcional; padrão: nome da pasta">
                        <Input value={folderName} onChange={(e) => setFolderName(e.detail.value)} />
                      </FormField>
                    </SpaceBetween>
                  ),
                },
                {
                  id: "discover", label: "Descobrir em uma pasta",
                  content: (
                    <SpaceBetween size="m">
                      <FormField label="Pasta com vários repositórios" description="Cada subpasta com .git vira um repositório; sem git, cada subpasta com manifesto de build ou IaC">
                        <SpaceBetween direction="horizontal" size="xs">
                          <PathInput value={discoverPath} onChange={setDiscoverPath} placeholder="/caminho/para/workspace" />
                          <Button disabled={!discoverPath}
                            onClick={() => api.discover(discoverPath).then((d) => { setDiscovered(d); setPicked(d.filter((x) => !x.configured)); })
                              .catch((e: Error) => setActionError(e.message))}>
                            Descobrir
                          </Button>
                        </SpaceBetween>
                      </FormField>
                      {discovered.length > 0 && (
                        <Table
                          variant="embedded"
                          selectionType="multi"
                          trackBy="path"
                          selectedItems={picked}
                          onSelectionChange={(e) => setPicked(e.detail.selectedItems)}
                          items={discovered}
                          header={<Header counter={`(${picked.length}/${discovered.length})`}>Encontrados</Header>}
                          columnDefinitions={[
                            { id: "name", header: "Nome", cell: (d) => d.name },
                            { id: "path", header: "Caminho", cell: (d) => <Box variant="code" fontSize="body-s">{d.path}</Box> },
                            {
                              id: "flags", header: "",
                              cell: (d) => (
                                <SpaceBetween direction="horizontal" size="xxs">
                                  {d.git && <Badge color="blue">git</Badge>}
                                  {d.configured && <Badge>já no landscape</Badge>}
                                </SpaceBetween>
                              ),
                            },
                          ]}
                        />
                      )}
                    </SpaceBetween>
                  ),
                },
                {
                  id: "git", label: "Repositório git",
                  content: (
                    <ColumnLayout columns={3}>
                      <FormField label="URL"><Input value={gitUrl} onChange={(e) => setGitUrl(e.detail.value)} placeholder="https://github.com/org/repo.git" /></FormField>
                      <FormField label="Branch ou tag" description="Opcional"><Input value={gitRef} onChange={(e) => setGitRef(e.detail.value)} placeholder="main" /></FormField>
                      <FormField label="Nome" description="Opcional"><Input value={gitName} onChange={(e) => setGitName(e.detail.value)} /></FormField>
                    </ColumnLayout>
                  ),
                },
              ]}
            />
            <Checkbox checked={save} onChange={(e) => setSave(e.detail.checked)}
              description="Sem salvar, o repositório entra só neste scan">
              Salvar no blueprint.yaml
            </Checkbox>
            <Button variant="primary" disabled={!addRequests().length || running} onClick={() => scan(addRequests())}>
              Adicionar e escanear
            </Button>
          </SpaceBetween>
        </Container>

        <Table
          items={jobs}
          trackBy="id"
          empty={<Box textAlign="center">Nenhum scan nesta sessão do servidor</Box>}
          header={<Header counter={`(${jobs.length})`} description="Scans disparados por esta console">Execuções</Header>}
          columnDefinitions={[
            { id: "status", header: "Status", cell: (j) => <StatusIndicator type={STATUS[j.status]}>{STATUS_TEXT[j.status]}</StatusIndicator> },
            { id: "mode", header: "Tipo", cell: (j) => (j.mode === "full" ? "Completo" : "Incremental") },
            { id: "repos", header: "Repositórios", cell: (j) => j.repositories.join(", ") },
            { id: "started", header: "Início", cell: (j) => new Date(j.started * 1000).toLocaleTimeString() },
            {
              id: "result", header: "Resultado",
              cell: (j) => j.result
                ? <StatusIndicator type={j.result.valid ? "success" : "error"}>
                    {j.result.valid ? "CALM válido" : "CALM inválido"} · {j.result.stats.services ?? 0} apps · {j.result.warnings} avisos
                  </StatusIndicator>
                : j.error ?? "—",
            },
            {
              id: "log", header: "Log",
              cell: (j) => (
                <ExpandableSection headerText={`${j.log.length} linhas`}>
                  <Box variant="code" fontSize="body-s">{j.log.map((l, i) => <div key={i}>{l}</div>)}</Box>
                </ExpandableSection>
              ),
            },
          ]}
        />
      </SpaceBetween>
    </Loadable>
  );
}
