"""Policy knowledge base: markdown docs split by '## ' section and searched with BM25."""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from rank_bm25 import BM25Okapi

_TOKEN = re.compile(r"[a-z0-9$]+")
_STOP = {"the", "a", "an", "and", "or", "of", "to", "is", "are", "in", "on", "for", "it", "i", "my", "me", "do",
         "you", "your", "we", "our", "can", "be", "with", "what", "how", "if", "at", "by", "this", "that", "will"}


def tokenize(text: str) -> list[str]:
    tokens = [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]
    # light stemming so "refunds"/"refund", "shipping"/"ship" match
    out = []
    for t in tokens:
        for suffix in ("ing", "es", "s"):
            if len(t) > 4 and t.endswith(suffix):
                t = t[: -len(suffix)]
                break
        out.append(t)
    return out


@dataclass
class Chunk:
    source: str
    section: str
    text: str


class KnowledgeBase:
    def __init__(self, directory: Path):
        self.chunks: list[Chunk] = []
        for path in sorted(Path(directory).glob("*.md")):
            title, section, buf = path.stem, None, []
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.startswith("# "):
                    title = line[2:].strip()
                elif line.startswith("## "):
                    if section and buf:
                        self.chunks.append(Chunk(title, section, " ".join(buf).strip()))
                    section, buf = line[3:].strip(), []
                elif line.strip():
                    buf.append(line.strip())
            if section and buf:
                self.chunks.append(Chunk(title, section, " ".join(buf).strip()))
        corpus = [tokenize(f"{c.source} {c.section} {c.section} {c.text}") for c in self.chunks]
        self._bm25 = BM25Okapi(corpus) if corpus else None

    def search(self, query: str, k: int = 3) -> list[dict]:
        if not self._bm25 or not query.strip():
            return []
        scores = self._bm25.get_scores(tokenize(query))
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:k]
        return [
            {"source": self.chunks[i].source, "section": self.chunks[i].section,
             "text": self.chunks[i].text, "score": round(float(scores[i]), 3)}
            for i in ranked if scores[i] > 0
        ]


@lru_cache(maxsize=4)
def get_knowledge_base(directory: str) -> KnowledgeBase:
    return KnowledgeBase(Path(directory))
