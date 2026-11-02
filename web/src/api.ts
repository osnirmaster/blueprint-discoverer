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
  interfaces?: { "unique-id": string; method?: string; path?: string; handler?: string; evidence?: string; host?: string }[];
  metadata?: Record<string, any>;
}

export interface CalmRelationship {
  "unique-id": string;
  description?: string;
  protocol?: string;
  "relationship-type": Record<string, any>;
  metadata?: { confidence?: number; rule?: string; evidence?: string[]; "dependency-kind"?: string; via?: string };
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
