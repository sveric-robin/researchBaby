#!/usr/bin/env python3
"""
research_baby.py — Search a topic, list most-cited seed papers (by year cutoff),
and for each seed list the top-k most-cited papers that cite it.

Powered by Semantic Scholar Graph API:
  https://api.semanticscholar.org/graph/v1/

Usage:
  python research_baby.py --query "graph neural networks" --min-year 2021 --seeds 10 --children 5

Notes:
  - No CLI flags were added compared with your version.
  - The script is now much more resilient to rate limits:
      * honors Retry-After
      * exponential backoff + jitter
      * smaller page sizes
      * graceful fallbacks (continues instead of crashing)
  - If you have an API key, set it via env var:
      * S2_API_KEY or SEMANTIC_SCHOLAR_API_KEY
"""

import argparse
import os
import sys
import time
import random
from dataclasses import dataclass
from typing import List, Dict, Any, Optional

import requests

API_BASE = "https://api.semanticscholar.org/graph/v1"

# --- Politeness & paging defaults (tuned to avoid 429s without CLI args) ---
BASE_SLEEP = 1.5          # base delay between requests (seconds)
SEARCH_PAGE_SIZE = 25     # smaller pages reduce rate-limit hits
CITES_PAGE_SIZE = 25
SEARCH_MAX_PAGES = 12     # up to ~300 search results scanned
CITES_MAX_PAGES = 12      # up to ~300 citing items scanned
CITES_OVERSAMPLE = 4      # fetch ~top_k * this many before sorting


@dataclass
class Paper:
    paper_id: str
    title: str
    year: Optional[int]
    citation_count: Optional[int]
    url: Optional[str]
    external_ids: Dict[str, Any]


def _make_session() -> requests.Session:
    """Build a session with UA and optional API key from env."""
    s = requests.Session()
    s.headers.update({"User-Agent": "topic-tree/1.2 (+https://semanticscholar.org)"})
    api_key = os.getenv("S2_API_KEY") or os.getenv("SEMANTIC_SCHOLAR_API_KEY")
    if api_key:
        # S2 accepts x-api-key; Authorization: Bearer also works, but this is simpler
        s.headers.update({"x-api-key": api_key})
    return s


def _get(
    url: str,
    params: Dict[str, Any],
    max_retries: int = 10,
    timeout: int = 60,
    session: Optional[requests.Session] = None,
    base_sleep: float = BASE_SLEEP,
) -> Dict[str, Any]:
    """
    GET with Retry-After handling, exponential backoff, and jitter.
    Returns {"data": []} on hard/irrecoverable errors to keep pipeline going.
    """
    sess = session or _make_session()
    backoff = base_sleep

    for attempt in range(max_retries):
        try:
            resp = sess.get(url, params=params, timeout=timeout)
        except requests.RequestException:
            time.sleep(backoff + random.uniform(0, 0.5))
            backoff = min(backoff * 1.8, 12.0)
            continue

        if resp.status_code == 200:
            # Small jitter even on success to avoid burstiness
            time.sleep(base_sleep * 0.4 + random.uniform(0, 0.4))
            try:
                return resp.json()
            except Exception:
                return {"data": []}

        # Honor Retry-After on 429/503
        if resp.status_code in (429, 503):
            ra = resp.headers.get("Retry-After")
            if ra:
                try:
                    sleep_for = float(ra)
                except ValueError:
                    sleep_for = backoff
            else:
                sleep_for = backoff
            time.sleep(sleep_for + random.uniform(0, 0.6))
            backoff = min(backoff * 2.0, 15.0)
            continue

        # Transient server errors → exponential backoff
        if resp.status_code in (500, 502, 504):
            time.sleep(backoff + random.uniform(0, 0.6))
            backoff = min(backoff * 2.0, 15.0)
            continue

        # Non-retryable / not worth blocking the whole run
        if resp.status_code in (400, 401, 403, 404):
            return {"data": []}

        # Unknown status: brief wait then try again
        time.sleep(backoff + random.uniform(0, 0.5))
        backoff = min(backoff * 1.8, 12.0)

    # Final fallback: do not crash caller
    return {"data": []}


def _sanitize_query(q: str) -> str:
    """Fallback query: remove quotes and collapse whitespace."""
    return " ".join(q.replace('"', " ").split())


def search_top_papers(query: str, min_year: int, limit: int) -> List[Paper]:
    """
    Search papers for a topic, filter by min_year, sort by citationCount desc, and return top-N.
    Uses small pages, polite delays, and a sanitized-query fallback for page 0.
    """
    fields = "title,year,citationCount,externalIds,url"
    sess = _make_session()
    fetched: List[Paper] = []
    offset = 0

    for page in range(SEARCH_MAX_PAGES):
        if len(fetched) >= limit:
            break
        payload = {"query": query, "limit": SEARCH_PAGE_SIZE, "offset": offset, "fields": fields}
        data = _get(f"{API_BASE}/paper/search", payload, session=sess)
        results = data.get("data", []) or []

        # If page 0 was empty, try a sanitized version of the query once
        if not results and page == 0:
            q2 = _sanitize_query(query)
            if q2 != query:
                payload["query"] = q2
                data = _get(f"{API_BASE}/paper/search", payload, session=sess)
                results = data.get("data", []) or []

        if not results:
            break

        for r in results:
            year = r.get("year")
            if year is None or year < min_year:
                continue
            fetched.append(
                Paper(
                    paper_id=r.get("paperId") or "",
                    title=(r.get("title") or "").strip() or "(untitled)",
                    year=year,
                    citation_count=int(r.get("citationCount") or 0),
                    url=r.get("url") or "",
                    external_ids=r.get("externalIds") or {},
                )
            )

        offset += SEARCH_PAGE_SIZE
        time.sleep(BASE_SLEEP + random.uniform(0, 0.3))

    fetched.sort(key=lambda p: (p.citation_count or 0), reverse=True)
    return fetched[:limit]


def get_top_citing_papers(paper_id: str, top_k: int) -> List[Paper]:
    """
    For a given paper, fetch papers that cite it, sort by citationCount desc, return top_k.
    Uses small pages and oversampling to avoid needing all pages.
    """
    if not paper_id:
        return []

    sess = _make_session()
    base_fields = "title,year,citationCount,url,externalIds,paperId"
    fields = ",".join([f"citingPaper.{f}" for f in base_fields.split(",")])

    offset = 0
    citing: List[Paper] = []
    target_count = max(top_k * CITES_OVERSAMPLE, 80)

    for _ in range(CITES_MAX_PAGES):
        if len(citing) >= target_count:
            break

        payload = {"fields": fields, "limit": CITES_PAGE_SIZE, "offset": offset}
        data = _get(f"{API_BASE}/paper/{paper_id}/citations", payload, session=sess)
        items = data.get("data", []) or []
        if not items:
            break

        for item in items:
            cp = (item or {}).get("citingPaper") or {}
            citing.append(
                Paper(
                    paper_id=cp.get("paperId") or "",
                    title=(cp.get("title") or "").strip() or "(untitled)",
                    year=cp.get("year"),
                    citation_count=int(cp.get("citationCount") or 0),
                    url=cp.get("url") or "",
                    external_ids=cp.get("externalIds") or {},
                )
            )

        offset += CITES_PAGE_SIZE
        time.sleep(BASE_SLEEP * 0.8 + random.uniform(0, 0.4))

    citing.sort(key=lambda p: (p.citation_count or 0), reverse=True)
    return citing[:top_k]


def format_paper_line(p: Paper, prefix: str = "") -> str:
    year = p.year if p.year is not None else "n/a"
    cites = p.citation_count if p.citation_count is not None else 0
    doi = (p.external_ids or {}).get("DOI")
    link = f"https://doi.org/{doi}" if doi else (p.url or "")
    link_suffix = f"  <{link}>" if link else ""
    return f"{prefix}{p.title} — {year} — {cites} cites{link_suffix}"


def print_tree(seeds: List[Paper], children_map: Dict[str, List[Paper]], children_count: int):
    for i, seed in enumerate(seeds, 1):
        print(f"{i}. {format_paper_line(seed)}")
        kids = children_map.get(seed.paper_id, [])
        if not kids:
            print("   └─ (no citing papers found)")
            continue
        for j, child in enumerate(kids, 1):
            branch = "   ├─" if j < children_count else "   └─"
            print(f"{branch} {format_paper_line(child)}")


def main():
    parser = argparse.ArgumentParser(description="Topic → most-cited seeds → top citing papers (tree).")
    parser.add_argument("--query", required=True, help="Topic string to search (e.g., 'graph neural networks').")
    parser.add_argument("--min-year", type=int, default=2021, help="Minimum publication year to include (default: 2021).")
    parser.add_argument("--seeds", type=int, default=10, help="How many top seed papers to list (default: 10).")
    parser.add_argument("--children", type=int, default=5, help="How many top citing papers per seed (default: 5).")
    args = parser.parse_args()

    try:
        seeds = search_top_papers(args.query, args.min_year, args.seeds)
        if not seeds:
            print(f"No papers found for '{args.query}' with year ≥ {args.min_year}.")
            sys.exit(0)

        children_map: Dict[str, List[Paper]] = {}
        for seed in seeds:
            # If a seed causes trouble, we skip it gracefully
            try:
                kids = get_top_citing_papers(seed.paper_id, args.children)
            except Exception:
                kids = []
            children_map[seed.paper_id] = kids

        print(f"\nTOPIC: {args.query}")
        print(f"Year cutoff: ≥ {args.min_year}")
        print(f"Seeds: {len(seeds)} | Children per seed: {args.children}\n")
        print_tree(seeds, children_map, args.children)

    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
