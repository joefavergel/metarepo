"""Pure-Python BM25 search over frontmatter and body (003/R12).

* Fields: title, description and tags are boosted; the body has weight 1 (BM25F-style weighted term counts).
* Tokenization is Unicode-aware and language-neutral: case-folded, diacritics removed (so ``cafe`` finds
  ``café``), letters and digits only, and runs of CJK characters are indexed as overlapping bigrams.
* No stop-word lists (003 section 5 proposal for v0.1); BM25's IDF already discounts frequent terms.
* The index is cached in the member's local cache and refreshed per file by ``(size, mtime_ns)``.
"""
from __future__ import annotations

import json
import math
import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import frontmatter
from .fsops import atomic_write_text, fs_path, local_cache_dir

SCOPES = {"wiki": ("30_wiki",), "extracts": ("20_extracts",), "all": ("30_wiki", "20_extracts")}
SCOPE_OF_DIR = {"30_wiki": "wiki", "20_extracts": "extracts"}
BOOST = {"title": 3.0, "description": 2.0, "tags": 2.0, "body": 1.0}
K1, B = 1.5, 0.75
CACHE_VERSION = 1
CACHE_NAME = "search-index.json"

_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
_CJK = re.compile(r"([぀-ヿ㐀-䶿一-鿿가-힯豈-﫿]+)")
_HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*#*\s*$")


def fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for word in _TOKEN.findall(fold(text)):
        for part in _CJK.split(word):
            if not part:
                continue
            if _CJK.fullmatch(part):
                tokens.extend([part] if len(part) == 1 else [part[i: i + 2] for i in range(len(part) - 1)])
            else:
                tokens.append(part)
    return tokens


@dataclass
class IndexedDoc:
    path: str
    scope: str
    size: int
    mtime_ns: int
    tf: dict[str, float]
    length: float
    title: str | None
    type: str | None
    description: str | None


@dataclass
class IndexStats:
    documents: int = 0
    reused: int = 0
    rebuilt: int = 0
    removed: int = 0
    cache: str | None = None


def _index_file(root: Path, rel: str, scope: str, st: os.stat_result) -> IndexedDoc:
    raw = Path(root, rel).read_bytes().decode("utf-8", errors="replace")
    try:
        doc = frontmatter.parse(raw)
        front, body = doc.front, doc.body
    except frontmatter.FrontmatterError:
        front, body = {}, raw
    title = front.get("title") if isinstance(front.get("title"), str) else None
    if title is None:
        for line in body.splitlines():
            m = _HEADING.match(line)
            if m:
                title = m.group(1)
                break
    if title is None:
        title = Path(rel).stem
    description = front.get("description") if isinstance(front.get("description"), str) else None
    tags = front.get("tags")
    tag_text = " ".join(str(t) for t in tags) if isinstance(tags, list) else ""
    tf: dict[str, float] = {}
    length = 0.0
    for field_name, text in (("title", title), ("description", description or ""), ("tags", tag_text), ("body", body)):
        weight = BOOST[field_name]
        for tok in tokenize(text):
            tf[tok] = tf.get(tok, 0.0) + weight
            length += weight
    doc_type = front.get("type") if isinstance(front.get("type"), str) else None
    return IndexedDoc(rel, scope, st.st_size, st.st_mtime_ns, tf, length, title, doc_type, description)


def _walk(root: Path, scope_dir: str):
    base = Path(root) / scope_dir
    if not base.is_dir():
        return
    for dirpath, dirs, files in os.walk(str(base)):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for name in sorted(files):
            if name.endswith(".md") and not name.startswith("."):
                yield Path(dirpath) / name


def _load_cache(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("version") != CACHE_VERSION or not isinstance(data.get("docs"), dict):
        return {}
    return data["docs"]


def build_index(root: Path, project_id: str | None, scope: str) -> tuple[list[IndexedDoc], IndexStats]:
    """Documents of ``scope`` (current on disk) and cache statistics. Refreshes the cache file when needed."""
    root = Path(root)
    cache_path = (local_cache_dir(project_id) / CACHE_NAME) if project_id else None
    cached = _load_cache(cache_path)
    stats = IndexStats(cache=str(cache_path) if cache_path else None)
    docs: list[IndexedDoc] = []
    dirty = False
    seen: set[str] = set()
    for scope_dir in SCOPES[scope]:
        for file in _walk(root, scope_dir):
            rel = file.relative_to(root).as_posix()
            seen.add(rel)
            st = os.stat(fs_path(file))
            entry = cached.get(rel)
            if entry and entry.get("size") == st.st_size and entry.get("mtime_ns") == st.st_mtime_ns:
                docs.append(IndexedDoc(rel, SCOPE_OF_DIR[scope_dir], st.st_size, st.st_mtime_ns, entry["tf"],
                                       entry["length"], entry.get("title"), entry.get("type"),
                                       entry.get("description")))
                stats.reused += 1
                continue
            doc = _index_file(root, rel, SCOPE_OF_DIR[scope_dir], st)
            docs.append(doc)
            cached[rel] = {"size": doc.size, "mtime_ns": doc.mtime_ns, "tf": doc.tf, "length": doc.length,
                           "title": doc.title, "type": doc.type, "description": doc.description}
            stats.rebuilt += 1
            dirty = True
    for rel in [r for r in cached if r.split("/", 1)[0] in SCOPES[scope] and r not in seen]:
        del cached[rel]
        stats.removed += 1
        dirty = True
    if dirty and cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(cache_path, json.dumps({"version": CACHE_VERSION, "docs": cached}, ensure_ascii=False,
                                                 separators=(",", ":")))
    stats.documents = len(docs)
    return docs, stats


@dataclass
class Hit:
    path: str
    scope: str
    score: float
    title: str | None
    type: str | None
    snippet: str = ""
    section: str | None = None
    line: int | None = None


def rank(docs: list[IndexedDoc], query: str, *, doc_type: str | None = None, limit: int = 10) -> list[Hit]:
    terms = list(dict.fromkeys(tokenize(query)))
    if not terms or not docs:
        return []
    n_docs = len(docs)
    avgdl = (sum(d.length for d in docs) / n_docs) or 1.0
    df = {t: sum(1 for d in docs if t in d.tf) for t in terms}
    idf = {t: math.log(1.0 + (n_docs - df[t] + 0.5) / (df[t] + 0.5)) for t in terms}
    hits: list[Hit] = []
    wanted = doc_type.casefold() if doc_type else None
    for d in docs:
        if wanted is not None and (d.type or "").casefold() != wanted:
            continue
        score = 0.0
        norm = K1 * (1.0 - B + B * d.length / avgdl)
        for t in terms:
            f = d.tf.get(t)
            if f:
                score += idf[t] * f * (K1 + 1.0) / (f + norm)
        if score > 0:
            hits.append(Hit(d.path, d.scope, round(score, 6), d.title, d.type))
    hits.sort(key=lambda h: (-h.score, h.path))
    return hits[: max(limit, 0)]


def add_snippets(root: Path, hits: list[Hit], query: str, width: int = 200) -> None:
    """Fill in ``snippet``, ``section`` and ``line`` (1-based, in the file) for each hit."""
    terms = set(tokenize(query))
    for hit in hits:
        try:
            text = Path(root, hit.path).read_bytes().decode("utf-8", errors="replace")
            doc = frontmatter.parse(text)
            body = doc.body
            offset = text.count("\n", 0, len(text) - len(body))
        except (OSError, frontmatter.FrontmatterError):
            continue
        best, best_score, heading, best_heading, best_line = "", 0, None, None, None
        for i, line in enumerate(body.splitlines()):
            m = _HEADING.match(line)
            if m:
                heading = m.group(1)
            score = len(terms & set(tokenize(line)))
            if score > best_score:
                best, best_score, best_heading, best_line = line.strip(), score, heading, offset + i + 1
        if best_score:
            hit.snippet = best if len(best) <= width else best[: width - 1].rstrip() + "…"
            hit.section, hit.line = best_heading, best_line


def search(root: Path, project_id: str | None, query: str, *, scope: str = "all", doc_type: str | None = None,
           limit: int = 10) -> tuple[list[Hit], IndexStats]:
    docs, stats = build_index(root, project_id, scope)
    hits = rank(docs, query, doc_type=doc_type, limit=limit)
    add_snippets(root, hits, query)
    return hits, stats
