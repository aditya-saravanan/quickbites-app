"""Embedding RAG index over policy_and_faq.md.

Chunks the markdown by ## headings (FAQ sub-split per **Q:**), embeds with
sentence-transformers locally, persists to a pickle. Loaded once at boot.

The full policy is also inlined in the system prompt — this index is the
agent's optional re-grounding tool, not the source of truth.
"""

from __future__ import annotations

import os
import pickle
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .settings import POLICY_FILE, POLICY_INDEX_PATH

_FAQ_Q_RE = re.compile(r"^\*\*Q:\s*(.+?)\*\*", re.MULTILINE)


@dataclass
class PolicyChunk:
    heading: str
    text: str


def chunk_policy(markdown: str) -> list[PolicyChunk]:
    """Split policy by ## heading; FAQ section is sub-split per Q."""
    chunks: list[PolicyChunk] = []
    sections = re.split(r"^##\s+", markdown, flags=re.MULTILINE)
    if not sections:
        return chunks
    for section in sections[1:]:
        lines = section.split("\n", 1)
        heading = lines[0].strip()
        body = lines[1].strip() if len(lines) > 1 else ""
        if heading.lower().startswith("common faqs") or heading.lower().startswith("faq"):
            for q_match in _FAQ_Q_RE.finditer(body):
                start = q_match.start()
                next_q = _FAQ_Q_RE.search(body, q_match.end())
                end = next_q.start() if next_q else len(body)
                chunk_text = body[start:end].strip()
                chunks.append(PolicyChunk(f"FAQ: {q_match.group(1).strip()}", chunk_text))
        else:
            chunks.append(PolicyChunk(heading, body))
    return chunks


class PolicyIndex:
    """Cosine-similarity top-k retrieval over policy chunks."""

    def __init__(self, chunks: list[PolicyChunk], embeddings: np.ndarray) -> None:
        if len(chunks) != embeddings.shape[0]:
            raise ValueError("chunks/embeddings length mismatch")
        self.chunks = chunks
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1.0, norms)
        self.embeddings = embeddings / norms

    def search(self, query_embedding: np.ndarray, k: int = 3) -> list[dict[str, Any]]:
        q = query_embedding.astype(np.float32)
        n = float(np.linalg.norm(q)) or 1.0
        q = q / n
        sims = self.embeddings @ q
        top = np.argsort(-sims)[:k]
        return [
            {
                "heading": self.chunks[int(i)].heading,
                "text": self.chunks[int(i)].text,
                "score": float(sims[int(i)]),
            }
            for i in top
        ]

    def save(self, path: str | Path) -> None:
        with open(path, "wb") as f:
            pickle.dump(
                {"chunks": [(c.heading, c.text) for c in self.chunks], "embeddings": self.embeddings},
                f,
            )

    @classmethod
    def load(cls, path: str | Path) -> "PolicyIndex":
        with open(path, "rb") as f:
            data = pickle.load(f)
        chunks = [PolicyChunk(h, t) for h, t in data["chunks"]]
        emb = data["embeddings"]
        idx = cls.__new__(cls)
        idx.chunks = chunks
        idx.embeddings = emb
        return idx


_MODEL: Any = None


def _get_embedder(model_name: str | None = None) -> Any:
    global _MODEL
    if _MODEL is None:
        from sentence_transformers import SentenceTransformer

        name = model_name or os.environ.get("EMBEDDINGS_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
        _MODEL = SentenceTransformer(name)
    return _MODEL


def embed_texts(texts: list[str]) -> np.ndarray:
    model = _get_embedder()
    return np.asarray(model.encode(texts, show_progress_bar=False, convert_to_numpy=True), dtype=np.float32)


def embed_query(text: str) -> np.ndarray:
    return embed_texts([text])[0]


def build_or_load_index(
    policy_path: str = POLICY_FILE, index_path: str = POLICY_INDEX_PATH
) -> PolicyIndex:
    """Load cached index if present and fresher than policy file; else rebuild."""
    pp = Path(policy_path)
    ip = Path(index_path)
    if ip.exists() and ip.stat().st_mtime >= pp.stat().st_mtime:
        return PolicyIndex.load(ip)
    md = pp.read_text(encoding="utf-8")
    chunks = chunk_policy(md)
    embeddings = embed_texts([c.text for c in chunks])
    idx = PolicyIndex(chunks, embeddings)
    idx.save(ip)
    return idx
