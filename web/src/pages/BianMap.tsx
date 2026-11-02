import Badge from "@cloudscape-design/components/badge";
import Box from "@cloudscape-design/components/box";
import ColumnLayout from "@cloudscape-design/components/column-layout";
import Container from "@cloudscape-design/components/container";
import ContentLayout from "@cloudscape-design/components/content-layout";
import ExpandableSection from "@cloudscape-design/components/expandable-section";
import Header from "@cloudscape-design/components/header";
import Link from "@cloudscape-design/components/link";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Toggle from "@cloudscape-design/components/toggle";
import { useMemo, useState } from "react";
import { api, CoverageRow } from "../api";
import { Loadable } from "../components/common";
import { useAsync } from "../hooks";

export default function BianMapPage() {
  const { data, loading, error } = useAsync<CoverageRow[]>(api.coverage);
  const [onlyCovered, setOnlyCovered] = useState(true);
  const tree = useMemo(() => {
    const t = new Map<string, Map<string, CoverageRow[]>>();
    for (const r of data ?? []) {
      if (onlyCovered && !r.primary_apps.length && !r.secondary_apps.length) continue;
      if (!t.has(r.business_area)) t.set(r.business_area, new Map());
      const d = t.get(r.business_area)!;
      d.set(r.business_domain, [...(d.get(r.business_domain) ?? []), r]);
    }
    return t;
  }, [data, onlyCovered]);
  const covered = (data ?? []).filter((r) => r.primary_apps.length).length;

  return (
    <ContentLayout
      header={
        <Header variant="h1" description={`Cobertura da paisagem BIAN pelas aplicações descobertas: ${covered} de ${data?.length ?? 0} service domains realizados como domínio primário.`}
          actions={<Toggle checked={onlyCovered} onChange={({ detail }) => setOnlyCovered(detail.checked)}>Somente cobertos</Toggle>}>
          Mapa BIAN
        </Header>
      }
    >
      <Loadable loading={loading} error={error}>
        <SpaceBetween size="l">
          {[...tree.entries()].map(([area, domains]) => (
            <Container key={area} header={<Header variant="h2">{area}</Header>}>
              <ColumnLayout columns={3} borders="vertical">
                {[...domains.entries()].map(([dom, rows]) => (
                  <ExpandableSection key={dom} defaultExpanded headerText={dom} headerCounter={`(${rows.filter((r) => r.primary_apps.length).length}/${rows.length})`}>
                    <SpaceBetween size="xs">
                      {rows.map((r) => (
                        <Box key={r.service_domain_id}>
                          <Box variant="strong">{r.service_domain}</Box> <Badge>{r.pattern}</Badge>
                          <div>
                            {r.primary_apps.map((a) => <Link key={a} href={`#/applications/${a}`}><Badge color="blue">{a}</Badge></Link>)}{" "}
                            {r.secondary_apps.map((a) => <Link key={a} href={`#/applications/${a}`}><Badge color="grey">{a}</Badge></Link>)}
                            {!r.primary_apps.length && !r.secondary_apps.length && <Box color="text-status-inactive" fontSize="body-s">sem aplicação</Box>}
                          </div>
                        </Box>
                      ))}
                    </SpaceBetween>
                  </ExpandableSection>
                ))}
              </ColumnLayout>
            </Container>
          ))}
        </SpaceBetween>
      </Loadable>
    </ContentLayout>
  );
}
