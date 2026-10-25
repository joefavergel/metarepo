"""``search <query> [--scope wiki|extracts|all] [--type T] [-n N]``: BM25 search (003/R12)."""
from __future__ import annotations

import argparse
from dataclasses import asdict

from .. import search as engine_search
from ..command import Command, Context
from ..jsonout import EXIT_USAGE, EngineError, Result


def _args(p: argparse.ArgumentParser) -> None:
    p.add_argument("query", nargs="+", help="search terms")
    p.add_argument("--scope", choices=sorted(engine_search.SCOPES), default="all",
                   help="wiki = 30_wiki, extracts = 20_extracts, all = both (default)")
    p.add_argument("--type", dest="doc_type", default=None, help="only pages whose frontmatter type equals this")
    p.add_argument("-n", dest="limit", type=int, default=10, help="maximum number of results (default 10)")


def run(ctx: Context, args: argparse.Namespace) -> Result:
    project = ctx.require_project()
    query = " ".join(args.query)
    if not engine_search.tokenize(query):
        raise EngineError("empty_query", "the query contains no searchable terms", EXIT_USAGE)
    if args.limit < 1:
        raise EngineError("bad_limit", "-n must be at least 1", EXIT_USAGE)
    pid = None
    try:
        pid = project.project_id
    except Exception:  # noqa: BLE001 - search still works (without a disk cache) when project.yaml is unreadable
        ctx.warn("search_cache_disabled", "project.yaml has no readable id; the search index is not cached")
    hits, stats = engine_search.search(project.root, pid, query, scope=args.scope, doc_type=args.doc_type,
                                       limit=args.limit)
    results = [{k: v for k, v in asdict(h).items()} for h in hits]
    lines = [f"{h['score']:.3f}  {h['path']}" + (f"  [{h['section']}]" if h["section"] else "") for h in results]
    return Result(
        data={"query": query, "scope": args.scope, "type": args.doc_type, "count": len(results), "results": results,
              "index": {"documents": stats.documents, "reused": stats.reused, "rebuilt": stats.rebuilt,
                        "removed": stats.removed}},
        text="\n".join(lines) if lines else "no results",
    )


COMMAND = Command(
    name="search",
    help="BM25 search over wiki pages and extractions (search before reading)",
    run=run,
    add_arguments=_args,
    owner="003",
)
