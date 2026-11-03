// Thin typed client for the Blueprint Discoverer REST API (FastAPI, see src/blueprint_discoverer/api/app.py).

export interface Bian {
  "service-domain-id"?: string;
  "service-domain"?: string;
  "business-domain"?: string;
  "business-area"?: string;
  evidence?: string[];
  secondary?: { "service-domain-id": string; "service-domain": string; score: number }[];
}

export interface Semantics {
  purpose?: string;
  objectives?: string[];
  "business-capabilities"?: string[];
  "business-functions"?: string[];
  confidence?: number;
  method?: string;
  "review-required"?: boolean;
  rationale?: string;
}

export interface Application {
  id: string;
  name: string;
  type: string;
  description: string;
  repository: string;
  languages: string[];
  frameworks: string[];
  bian: Bian;
  semantics: Semantics;
  endpoints: number;
}

export interface CalmNode {
  "unique-id": string;
  "node-type": string;
  name: string;
  description: string;
  interfaces?: { "unique-id": string; method?: string; path?: string; handler?: string; evidence?: string; host?: string; protocol?: string }[];
  metadata?: Record<string, any>;
}

export interface CalmRelationship {
  "unique-id": string;
  description?: string;
  protocol?: string;
  "relationship-type": Record<string, any>;
  metadata?: {
    confidence?: number; rule?: string; evidence?: string[]; "dependency-kind"?: string; via?: string;
    direction?: { downstream: string; upstream: string };
  };
  other?: CalmNode;
}

export interface Issue { level: string; layer: string; path: string; message: string }
export interface Validation { valid?: boolean; errors?: Issue[]; warnings?: Issue[]; stats?: Record<string, number> }

export interface Summary {
  name: string;
  description: string;
  validation: Validation;
  applications: number;
  review_required: string[];
  business_domains: string[];
  metadata: Record<string, unknown>[];
}

export interface GraphNode {
  id: string;
  name: string;
  type: "Application" | "DataStore" | "Topic" | "ExternalSystem";
  sd?: string;
  service_domain?: string;
  business_domain?: string;
  business_area?: string;
  confidence?: number;
  review?: boolean;
  technology?: string;
  kind?: string;
  languages?: string[];
  node_type?: string;
  repo?: string;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  kind: string;
  protocol: string;
  description: string;
  confidence: number;
  rule: string;
  evidence: string[];
}

export interface ApplicationDetail {
  node: CalmNode;
  outbound: CalmRelationship[];
  inbound: CalmRelationship[];
  impact: { id: string; name: string; type: string }[];
  dependencies: { id: string; name: string; type: string }[];
}

export interface CoverageRow {
  business_area: string;
  business_domain: string;
  service_domain_id: string;
  service_domain: string;
  pattern: string;
  primary_apps: string[];
  secondary_apps: string[];
}

export interface SourceRepo {
  name: string;
  path?: string | null;
  url?: string | null;
  ref?: string | null;
  scanned: boolean;
  commit: string;
  components: string[];
  infrastructure: number;
  files_scanned: number;
  location: string;
}

export interface Sources {
  config: string | null;
  config_exists: boolean;
  base: string;
  workspace: string;
  fs_root: string;
  repositories: SourceRepo[];
}

export interface FolderEntry { name: string; path: string; git: boolean; manifest: boolean }
export interface Folder { path: string; parent: string | null; git: boolean; entries: FolderEntry[] }
export interface Discovered { name: string; path: string; git: boolean; configured: boolean }
export interface RepoRequest { name?: string; path?: string; url?: string; ref?: string }

export interface ScanJob {
  id: string;
  repositories: string[];
  mode: "full" | "incremental";
  status: "queued" | "running" | "succeeded" | "failed";
  started: number;
  finished: number | null;
  log: string[];
  result: { valid: boolean; errors: number; warnings: number; stats: Record<string, number> } | null;
  error: string | null;
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail ?? res.statusText);
  return data as T;
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`/api${path}`);
  if (!res.ok) throw new Error(`${res.status} ${(await res.text()) || res.statusText}`);
  return res.json();
}

export const api = {
  summary: () => get<Summary>("/summary"),
  applications: () => get<Application[]>("/applications"),
  application: (id: string) => get<ApplicationDetail>(`/applications/${encodeURIComponent(id)}`),
  graph: () => get<{ nodes: GraphNode[]; edges: GraphEdge[] }>("/graph"),
  architecture: () => get<Record<string, any>>("/architecture"),
  validation: () => get<Validation>("/validation"),
  coverage: () => get<CoverageRow[]>("/bian/coverage"),
  wikiTree: () => get<string[]>("/wiki/tree"),
  wikiPage: (path: string) => get<{ path: string; markdown: string }>(`/wiki/page?path=${encodeURIComponent(path)}`),
  sources: () => get<Sources>("/sources"),
  browse: (path?: string) => get<Folder>(`/fs${path ? `?path=${encodeURIComponent(path)}` : ""}`),
  discover: (path: string, depth = 3) => post<Discovered[]>("/sources/discover", { path, depth }),
  scans: () => get<ScanJob[]>("/scans"),
  startScan: (body: { repositories: RepoRequest[]; full?: boolean; save?: boolean }) => post<ScanJob>("/scans", body),
  cypher: async (query: string): Promise<Record<string, unknown>[]> => {
    const res = await fetch("/api/cypher", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query }),
    });
    const body = await res.json();
    if (!res.ok) throw new Error(body.detail ?? res.statusText);
    return body;
  },
};
