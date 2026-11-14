"""Split the written purchasing policy into citable sections."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PolicyChunk:
    id: str
    heading: str
    text: str


def load_policy() -> list[PolicyChunk]:
    raw = (Path(__file__).with_name("northwind.md")).read_text(encoding="utf-8")
    return chunk_policy(raw)


def chunk_policy(raw: str) -> list[PolicyChunk]:
    chunks: list[PolicyChunk] = []
    heading = ""
    body: list[str] = []
    for line in raw.splitlines():
        if line.startswith("## "):
            _store(chunks, heading, body)
            heading = line.removeprefix("## ").strip()
            body = []
        elif heading:
            body.append(line)
    _store(chunks, heading, body)
    return chunks


def _store(chunks: list[PolicyChunk], heading: str, body: list[str]) -> None:
    text = "\n".join(body).strip()
    if not heading or not text:
        return
    slug = re.sub(r"[^a-z0-9]+", "-", heading.casefold()).strip("-")
    chunks.append(PolicyChunk(slug, heading, f"{heading}\n{text}"))
