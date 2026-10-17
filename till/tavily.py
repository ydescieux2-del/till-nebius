"""Tavily web search client, plus the deterministic step that turns search results into one number.

    TAVILY_API_KEY set    -> live POST https://api.tavily.com/search (header Authorization: Bearer <key>)
    TAVILY_API_KEY unset  -> MOCK mode: a synthetic fixture shaped like the documented search response, with
                             invented sources on example.com, every one marked SAMPLE.

The language model never reads a price off the web and does arithmetic with it. `price_change` pulls percent
changes out of the result text with fixed patterns and takes the median, so the same results always give the
same number, and the sources it used are returned with it.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from statistics import median

import httpx

SEARCH_URL = "https://api.tavily.com/search"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "mock_tavily_search.json"
MOCK_NOTE = "Sample price data until the Tavily key is set"
LIVE_NOTE = "Live web search by Tavily"

_cache: dict[tuple, dict] = {}


class TavilyError(RuntimeError):
    pass


def is_live() -> bool:
    return bool(os.environ.get("TAVILY_API_KEY", "").strip())


def source() -> dict:
    return {"live": is_live(), "label": LIVE_NOTE if is_live() else MOCK_NOTE}


def search(query: str, *, max_results: int = 5, topic: str = "general") -> dict:
    """Tavily /search. Returns the parsed body: {"query", "answer"?, "results": [{title, url, content, score}]}."""
    query = (query or "").strip()
    if not query:
        raise ValueError("query is required")
    payload = {"query": query, "search_depth": "basic", "max_results": max(1, min(int(max_results), 10)),
               "include_answer": "basic", "topic": topic}
    key = tuple(sorted((k, str(v)) for k, v in payload.items()))
    if key in _cache:
        return _cache[key]
    body = _live(payload) if is_live() else _mock(payload)
    _cache[key] = body
    return body


def _live(payload: dict) -> dict:
    try:
        r = httpx.post(SEARCH_URL, json=payload, timeout=30,
                       headers={"Authorization": f"Bearer {os.environ['TAVILY_API_KEY'].strip()}",
                                "content-type": "application/json"})
    except httpx.HTTPError as exc:
        raise TavilyError(f"Could not reach Tavily: {exc}") from exc
    if r.status_code != 200:
        raise TavilyError(f"Tavily returned {r.status_code}: {r.text[:200]}")
    return r.json()


def _mock(payload: dict) -> dict:
    body = json.loads(FIXTURE.read_text())
    body["query"] = payload["query"]
    body["results"] = body["results"][: payload["max_results"]]
    return body


# ----------------------------------------------------------------------------- deterministic extraction

_UP = r"(?:up|rose|risen|rising|increased?|increasing|jump(?:ed)?|climb(?:ed)?|higher|hike[sd]?|surge[sd]?)"
_DOWN = r"(?:down|fell|fallen|dropped|decreased?|declined?|lower|cut)"
_NUM = r"(\d{1,2}(?:\.\d+)?)\s?(?:%|percent)"
_PATTERNS = [
    (re.compile(_UP + r"[^.%\d]{0,30}?(?:by\s+)?(?:about\s+|roughly\s+|nearly\s+|some\s+)?" + _NUM, re.I), 1),
    (re.compile(_NUM + r"\s+(?:increase|rise|jump|hike|higher)", re.I), 1),
    (re.compile(_DOWN + r"[^.%\d]{0,30}?(?:by\s+)?(?:about\s+|roughly\s+|nearly\s+|some\s+)?" + _NUM, re.I), -1),
    (re.compile(_NUM + r"\s+(?:decrease|drop|decline|lower)", re.I), -1),
]


def percents(text: str) -> list[float]:
    """Signed percent changes stated in a piece of text ("up 8%", "a 6 percent increase", "fell 3%")."""
    found = []
    for rx, sign in _PATTERNS:
        for m in rx.finditer(text or ""):
            found.append((m.start(1), sign * float(m.group(1))))
    seen, out = set(), []
    for pos, v in sorted(found):
        if pos not in seen:
            seen.add(pos)
            out.append(v)
    return out


def price_change(body: dict, item: str) -> dict:
    """Median percent change across results that mention the item. Returns {"pct": float|None, "evidence": [...]}.
    Each evidence row is a source title, url, and the percents found in it."""
    item = item.lower().rstrip("s")
    evidence = []
    for r in body.get("results") or []:
        text = f"{r.get('title', '')}. {r.get('content', '')}"
        if item not in text.lower():
            continue
        vals = [v for v in percents(text) if -60 <= v <= 60]
        if vals:
            evidence.append({"title": r.get("title", ""), "url": r.get("url", ""), "percents": vals})
    allv = [v for e in evidence for v in e["percents"]]
    return {"pct": round(median(allv), 1) if allv else None, "evidence": evidence}
