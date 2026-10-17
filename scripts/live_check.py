"""Live check for Nebius Token Factory (Nemotron) and Tavily: run once the keys exist, before tuning anything.

    .venv/bin/python scripts/live_check.py              # both; keys from env or the gitignored .env
    .venv/bin/python scripts/live_check.py --only nebius
    .venv/bin/python scripts/live_check.py --only tavily

Nebius (about 4 small requests): GET /v1/models and confirm the configured Nemotron ids are listed; one plain chat
call on Super and on Nano; one tool-calling call with Till's real tool list, checking Nemotron picks market_check
for the demo question and sends valid JSON arguments.
Tavily (1 request, 1 credit): the exact market_check search, then the deterministic percent extraction on it.
Prints only; nothing is saved. A missing key skips that half and exits 0 without touching the network.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEMO_Q = "Flour went up. Can I still make payroll in three weeks?"
FLAGS: list[str] = []


def load_dotenv() -> None:
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


def flag(msg: str) -> None:
    FLAGS.append(msg)
    print(f"  MISMATCH: {msg}")


def check_nebius() -> None:
    import httpx
    from till import nebius, server
    if not nebius.is_live():
        print("NEBIUS_API_KEY is not set (checked the environment and ~/till-nebius/.env). Skipping Nebius.")
        return
    hdr = {"Authorization": f"Bearer {os.environ['NEBIUS_API_KEY'].strip()}"}
    print(f"\n=== Nebius Token Factory at {nebius.base_url()} (key from env, not printed) ===")

    print("\n1. GET /v1/models")
    r = httpx.get(f"{nebius.base_url()}/models", headers=hdr, timeout=30)
    print(f"  status {r.status_code}")
    if r.status_code != 200:
        flag(f"/models returned {r.status_code}: {r.text[:200]}")
        return
    ids = [m.get("id") for m in r.json().get("data", [])]
    nemo = sorted(i for i in ids if i and "nemotron" in i.lower())
    print(f"  {len(ids)} models; Nemotron ids: {nemo}")
    for want in {nebius.model(), nebius.fast_model()}:
        if want not in ids:
            flag(f"configured model {want!r} is not in /models; set TILL_AGENT_MODEL / TILL_FAST_MODEL to one of {nemo}")

    for label, m in (("Super", nebius.model()), ("Nano", nebius.fast_model())):
        print(f"\n2. plain chat on {label} ({m})")
        t0 = time.time()
        r = httpx.post(f"{nebius.base_url()}/chat/completions", headers=hdr, timeout=90,
                       json={"model": m, "max_tokens": 60,
                             "messages": [{"role": "user", "content": "Reply with exactly: ready"}]})
        print(f"  status {r.status_code} in {time.time() - t0:.1f}s")
        if r.status_code != 200:
            flag(f"chat on {m} returned {r.status_code}: {r.text[:200]}")
            continue
        b = r.json()
        msg = b["choices"][0]["message"]
        print(f"  content {str(msg.get('content'))[:120]!r}  usage {b.get('usage')}")
        if "<think>" in str(msg.get("content")) or msg.get("reasoning_content"):
            print("  note: the model returns reasoning; nebius.chat strips <think> blocks from content")

    print("\n3. tool calling with Till's tools (the demo question)")
    tools = nebius.to_openai_tools([t.model_dump(by_alias=True, exclude_none=True)
                                    for t in asyncio.run(server.mcp.list_tools())])
    t0 = time.time()
    try:
        out = nebius.chat([{"role": "user", "content": DEMO_Q}], tools,
                          "You are Till, a voice CFO for a small bakery. Use the tools for every number.")
    except nebius.NebiusError as exc:
        flag(f"tool-calling request failed: {exc}")
        return
    calls = out["message"].get("tool_calls") or []
    print(f"  {time.time() - t0:.1f}s model={out['model']} finish={out['finish_reason']} calls="
          f"{[(c['function']['name'], c['function']['arguments']) for c in calls]}")
    if not calls:
        flag(f"Nemotron answered without a tool call: {out['message'].get('content', '')[:200]!r}")
    else:
        if calls[0]["function"]["name"] != "market_check":
            flag(f"first tool was {calls[0]['function']['name']}, expected market_check (tune the system prompt)")
        try:
            json.loads(calls[0]["function"]["arguments"] or "{}")
        except ValueError:
            flag("tool arguments are not valid JSON")


def check_tavily() -> None:
    import httpx  # noqa: F401  (tavily uses it)
    from till import tavily
    if not tavily.is_live():
        print("\nTAVILY_API_KEY is not set (checked the environment and ~/till-nebius/.env). Skipping Tavily.")
        return
    q = "wholesale flour price increase percent this month"
    print(f"\n=== Tavily search (key from env, not printed): {q!r} ===")
    try:
        body = tavily.search(q)
    except tavily.TavilyError as exc:
        flag(str(exc))
        return
    res = body.get("results") or []
    print(f"  results {len(res)}; keys {sorted(res[0]) if res else []}; answer {str(body.get('answer'))[:160]!r}")
    for r in res:
        print(f"    {r.get('score', 0):.2f}  {r.get('title', '')[:70]!r}  {r.get('url')}")
        print(f"          percents found: {tavily.percents(r.get('title', '') + '. ' + r.get('content', ''))}")
    pc = tavily.price_change(body, "flour")
    print(f"  price_change -> {pc['pct']} from {len(pc['evidence'])} source(s)")
    if not res:
        flag("Tavily returned no results")
    elif pc["pct"] is None:
        flag("no flour percent change extracted from live results; market_check will refuse to guess. "
             "Try another default query in server.market_check or widen tavily._PATTERNS")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--only", choices=["nebius", "tavily"])
    args = ap.parse_args()
    load_dotenv()
    if args.only != "tavily":
        check_nebius()
    if args.only != "nebius":
        check_tavily()
    print("\n=== Summary ===")
    from till import nebius, tavily
    if not nebius.is_live() and not tavily.is_live():
        print("No keys set, so nothing was checked and nothing was sent. Add them with the add-key skill, then rerun.")
        return 0
    if FLAGS:
        print(f"{len(FLAGS)} mismatch(es) to tune:")
        for f in FLAGS:
            print(f"  - {f}")
        return 1
    print("Live checks passed for every key that is set. Rerun the demo story and update SUBMISSION_DRAFT.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
