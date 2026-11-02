"""Extractor contract and tree-sitter helpers.

Extractors are deterministic: same bytes in, same facts out. They never guess business meaning;
they only record what the syntax tree proves (annotations, decorators, literal URLs, topic names...).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Protocol

from tree_sitter import Node

from ....model import CodeSymbol, Endpoint, Evidence, OutboundCall

URL_RE = re.compile(r"https?://[^\s\"'`]+")
HTTP_METHODS = {"get", "post", "put", "delete", "patch", "head", "options"}


@dataclass
class FileFacts:
    endpoints: list[Endpoint] = field(default_factory=list)
    outbound: list[OutboundCall] = field(default_factory=list)
    symbols: list[CodeSymbol] = field(default_factory=list)
    packages: set[str] = field(default_factory=set)
    frameworks: set[str] = field(default_factory=set)
    imports: set[str] = field(default_factory=set)


@dataclass
class FileContext:
    repo: str
    path: str  # repo-relative, posix
    source: bytes

    def evidence(self, line: int, snippet: str = "") -> Evidence:
        return Evidence(repo=self.repo, path=self.path, line=line, snippet=snippet.strip()[:160])

    def line_text(self, line: int) -> str:
        lines = self.source.decode("utf-8", "replace").splitlines()
        return lines[line - 1] if 0 < line <= len(lines) else ""


class Extractor(Protocol):
    language: str
    extensions: tuple[str, ...]

    def extract(self, ctx: FileContext) -> FileFacts: ...


# --- tree-sitter helpers -------------------------------------------------------------------------


def walk(node: Node) -> Iterator[Node]:
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(reversed(n.children))


def text(node: Node | None) -> str:
    return node.text.decode("utf-8", "replace") if node is not None and node.text is not None else ""


def line_of(node: Node) -> int:
    return node.start_point.row + 1


def unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] in "\"'`" and value[-1] == value[0]:
        return value[1:-1]
    return value


def join_paths(*parts: str) -> str:
    joined = "/".join(p.strip("/") for p in parts if p and p.strip("/"))
    return "/" + joined if joined else "/"


def first_child(node: Node, *types: str) -> Node | None:
    for c in node.children:
        if c.type in types:
            return c
    return None
