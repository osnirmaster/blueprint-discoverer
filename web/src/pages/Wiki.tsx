import Box from "@cloudscape-design/components/box";
import Container from "@cloudscape-design/components/container";
import Grid from "@cloudscape-design/components/grid";
import SideNavigation from "@cloudscape-design/components/side-navigation";
import mermaid from "mermaid";
import { useEffect, useRef } from "react";
import Markdown from "react-markdown";
import { useLocation, useNavigate } from "react-router-dom";
import remarkGfm from "remark-gfm";
import { api } from "../api";
import { Loadable } from "../components/common";
import { useAsync } from "../hooks";

mermaid.initialize({ startOnLoad: false, securityLevel: "strict", theme: "neutral" });

function Mermaid({ code }: { code: string }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const id = "m" + Math.random().toString(36).slice(2);
    mermaid
      .render(id, code)
      .then(({ svg }) => ref.current && (ref.current.innerHTML = svg))
      .catch((e) => ref.current && (ref.current.textContent = String(e)));
  }, [code]);
  return <div ref={ref} style={{ overflowX: "auto" }} />;
}

function resolve(current: string, href: string) {
  const parts = current.split("/").slice(0, -1);
  for (const seg of href.split("#")[0].split("/")) {
    if (seg === "..") parts.pop();
    else if (seg !== ".") parts.push(seg);
  }
  return parts.join("/");
}

export default function WikiPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const path = decodeURIComponent(location.pathname.replace(/^\/wiki\/?/, "")) || "index.md";
  const tree = useAsync(api.wikiTree);
  const page = useAsync(() => api.wikiPage(path), [path]);

  const groups = new Map<string, string[]>();
  (tree.data ?? []).forEach((p) => {
    const dir = p.includes("/") ? p.split("/")[0] : "";
    groups.set(dir, [...(groups.get(dir) ?? []), p]);
  });
  const label = (p: string) => p.split("/").pop()!.replace(/\.md$/, "").replace(/-/g, " ");

  return (
    <Grid gridDefinition={[{ colspan: 3 }, { colspan: 9 }]}>
      <Container>
        <SideNavigation
          header={{ text: "Wiki", href: "/wiki/index.md" }}
          activeHref={`/wiki/${path}`}
          onFollow={(e) => { e.preventDefault(); navigate(e.detail.href); }}
          items={[...groups.entries()].map(([dir, pages]) =>
            dir
              ? { type: "section" as const, text: dir, items: pages.map((p) => ({ type: "link" as const, text: label(p), href: `/wiki/${p}` })) }
              : { type: "section-group" as const, title: "Páginas", items: pages.filter((p) => !p.startsWith("_")).map((p) => ({ type: "link" as const, text: label(p), href: `/wiki/${p}` })) },
          )}
        />
      </Container>
      <Container>
        <Loadable loading={page.loading} error={page.error}>
          <Box>
            <Markdown
              remarkPlugins={[remarkGfm]}
              components={{
                code({ className, children }) {
                  const text = String(children);
                  return className === "language-mermaid" ? <Mermaid code={text} /> : <code className={className}>{children}</code>;
                },
                a({ href = "", children }) {
                  if (/^(https?|file):/.test(href)) return <a href={href} target="_blank" rel="noreferrer">{children}</a>;
                  return <a href={`#/wiki/${resolve(path, href)}`}>{children}</a>;
                },
                table: ({ children }) => <table className="bd-md-table">{children}</table>,
              }}
            >
              {(page.data?.markdown ?? "").replace(/^<!--.*?-->\n/, "")}
            </Markdown>
          </Box>
        </Loadable>
      </Container>
    </Grid>
  );
}
