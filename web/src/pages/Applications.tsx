import { useCollection } from "@cloudscape-design/collection-hooks";
import Box from "@cloudscape-design/components/box";
import Header from "@cloudscape-design/components/header";
import Link from "@cloudscape-design/components/link";
import Pagination from "@cloudscape-design/components/pagination";
import Table from "@cloudscape-design/components/table";
import TextFilter from "@cloudscape-design/components/text-filter";
import { useNavigate } from "react-router-dom";
import { api, Application } from "../api";
import { Confidence, Loadable, NodeType } from "../components/common";
import { useAsync } from "../hooks";

export default function ApplicationsPage() {
  const navigate = useNavigate();
  const { data, loading, error } = useAsync<Application[]>(api.applications);
  const { items, filterProps, paginationProps, collectionProps, filteredItemsCount } = useCollection(data ?? [], {
    filtering: {
      empty: <Box textAlign="center">Nenhuma aplicação</Box>,
      noMatch: <Box textAlign="center">Nenhum resultado</Box>,
      filteringFunction: (a, text) =>
        [a.name, a.repository, a.bian["service-domain"], a.bian["business-domain"], ...a.languages, ...a.frameworks]
          .join(" ")
          .toLowerCase()
          .includes(text.toLowerCase()),
    },
    sorting: { defaultState: { sortingColumn: { sortingField: "name" } } },
    pagination: { pageSize: 25 },
  });

  return (
    <Loadable loading={loading} error={error}>
      <Table
        {...collectionProps}
        variant="full-page"
        stickyHeader
        header={<Header variant="awsui-h1-sticky" counter={`(${data?.length ?? 0})`} description="Componentes implantáveis descobertos via AST nos repositórios">Aplicações</Header>}
        items={items}
        filter={<TextFilter {...filterProps} filteringPlaceholder="Filtrar por nome, domínio, linguagem..." countText={`${filteredItemsCount} resultados`} />}
        pagination={<Pagination {...paginationProps} />}
        columnDefinitions={[
          {
            id: "name", header: "Nome", sortingField: "name",
            cell: (a) => <Link href={`#/applications/${a.id}`} onFollow={(e) => { e.preventDefault(); navigate(`/applications/${a.id}`); }}>{a.name}</Link>,
          },
          { id: "type", header: "Tipo", cell: (a) => <NodeType type={a.type} /> },
          { id: "sd", header: "Service Domain (BIAN)", cell: (a) => a.bian["service-domain"] ?? "—", sortingComparator: (x, y) => (x.bian["service-domain"] ?? "").localeCompare(y.bian["service-domain"] ?? "") },
          { id: "bd", header: "Business Domain", cell: (a) => a.bian["business-domain"] ?? "—" },
          { id: "conf", header: "Confiança", cell: (a) => <Confidence value={a.semantics.confidence} review={a.semantics["review-required"]} />, sortingComparator: (x, y) => (x.semantics.confidence ?? 0) - (y.semantics.confidence ?? 0) },
          { id: "method", header: "Método", cell: (a) => a.semantics.method },
          { id: "lang", header: "Tecnologia", cell: (a) => [...a.languages, ...a.frameworks.slice(0, 3)].join(", ") },
          { id: "endpoints", header: "APIs", cell: (a) => a.endpoints, sortingField: "endpoints" },
          { id: "repo", header: "Repositório", cell: (a) => a.repository },
        ]}
      />
    </Loadable>
  );
}
