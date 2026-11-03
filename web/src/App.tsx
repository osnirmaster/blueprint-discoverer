import { applyMode, Mode } from "@cloudscape-design/global-styles";
import AppLayout from "@cloudscape-design/components/app-layout";
import SideNavigation from "@cloudscape-design/components/side-navigation";
import TopNavigation from "@cloudscape-design/components/top-navigation";
import { useEffect, useState } from "react";
import { Route, Routes, useLocation, useNavigate } from "react-router-dom";
import ApplicationDetailPage from "./pages/ApplicationDetail";
import ApplicationsPage from "./pages/Applications";
import BianMapPage from "./pages/BianMap";
import CalmPage from "./pages/Calm";
import DependencyGraphPage from "./pages/DependencyGraph";
import ExplorerPage from "./pages/Explorer";
import OverviewPage from "./pages/Overview";
import SourcesPage from "./pages/Sources";
import WikiPage from "./pages/Wiki";

const NAV = [
  { type: "link" as const, text: "Visão geral", href: "/" },
  { type: "link" as const, text: "Aplicações", href: "/applications" },
  { type: "link" as const, text: "Mapa de dependências", href: "/graph" },
  { type: "link" as const, text: "Mapa BIAN", href: "/bian" },
  { type: "divider" as const },
  { type: "link" as const, text: "Fontes e scans", href: "/sources" },
  { type: "link" as const, text: "Manifesto CALM", href: "/calm" },
  { type: "link" as const, text: "Wiki", href: "/wiki" },
  { type: "link" as const, text: "Explorador Cypher", href: "/explorer" },
];

export default function App() {
  const navigate = useNavigate();
  const location = useLocation();
  const [dark, setDark] = useState(() => window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false);
  useEffect(() => applyMode(dark ? Mode.Dark : Mode.Light), [dark]);

  const active = "/" + (location.pathname.split("/")[1] ?? "");
  return (
    <>
      <div id="top-nav" style={{ position: "sticky", top: 0, zIndex: 1002 }}>
        <TopNavigation
          identity={{ href: "#/", title: "Blueprint Discoverer", onFollow: (e) => { e.preventDefault(); navigate("/"); } }}
          utilities={[
            { type: "button", text: dark ? "Tema claro" : "Tema escuro", iconName: "settings", onClick: () => setDark(!dark) },
            { type: "button", text: "FINOS CALM", href: "https://calm.finos.org/", external: true },
            { type: "button", text: "BIAN", href: "https://bian.org/servicelandscape-8-0/index.html", external: true },
          ]}
        />
      </div>
      <AppLayout
        headerSelector="#top-nav"
        toolsHide
        navigation={
          <SideNavigation
            header={{ text: "Arquitetura as-is", href: "/" }}
            activeHref={active}
            items={NAV}
            onFollow={(e) => {
              if (!e.detail.external) {
                e.preventDefault();
                navigate(e.detail.href);
              }
            }}
          />
        }
        content={
          <Routes>
            <Route path="/" element={<OverviewPage />} />
            <Route path="/applications" element={<ApplicationsPage />} />
            <Route path="/applications/:id" element={<ApplicationDetailPage />} />
            <Route path="/graph" element={<DependencyGraphPage />} />
            <Route path="/bian" element={<BianMapPage />} />
            <Route path="/calm" element={<CalmPage />} />
            <Route path="/wiki/*" element={<WikiPage />} />
            <Route path="/explorer" element={<ExplorerPage />} />
            <Route path="/sources" element={<SourcesPage />} />
          </Routes>
        }
      />
    </>
  );
}
