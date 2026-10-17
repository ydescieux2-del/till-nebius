"""Chat client for Nebius Token Factory (OpenAI-compatible Chat Completions), running NVIDIA Nemotron.

    NEBIUS_API_KEY set    -> live POST https://api.tokenfactory.nebius.com/v1/chat/completions
                             (header Authorization: Bearer <key>), OpenAI tool calling
    NEBIUS_API_KEY unset  -> MOCK mode: a deterministic keyword planner that returns the same OpenAI-shaped
                             message (tool_calls, then a final answer) so the whole app runs without a key.
                             Every mock response carries "mock": true and the model name "mock-nemotron".

Model routing: the first call of a turn (planning which tools to chain) goes to Nemotron Super; follow-up calls
after tool results (usually phrasing the answer) go to Nemotron Nano. Set TILL_FAST_MODEL to an empty string to
use Super for every call. The model never computes a number; every number comes from a tool.
"""
from __future__ import annotations

import json
import os
import re

import httpx

DEFAULT_BASE = "https://api.tokenfactory.nebius.com/v1"
# Model ids from Nebius pages and model listings (see README, "Sources"). scripts/live_check.py confirms them
# against GET /v1/models once a key exists.
DEFAULT_MODEL = "nvidia/nemotron-3-super-120b-a12b"
DEFAULT_FAST_MODEL = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"
MOCK_MODEL = "mock-nemotron"
MOCK_NOTE = "Mock planner until the Nebius key is set"


class NebiusError(RuntimeError):
    pass


def base_url() -> str:
    return os.environ.get("NEBIUS_BASE_URL", DEFAULT_BASE).rstrip("/")


def is_live() -> bool:
    return bool(os.environ.get("NEBIUS_API_KEY", "").strip())


def model() -> str:
    return os.environ.get("TILL_AGENT_MODEL", DEFAULT_MODEL)


def fast_model() -> str:
    return os.environ.get("TILL_FAST_MODEL", DEFAULT_FAST_MODEL) or model()


def source() -> dict:
    if is_live():
        return {"live": True, "label": f"Nemotron on Nebius Token Factory ({model()})",
                "model": model(), "fast_model": fast_model()}
    return {"live": False, "label": MOCK_NOTE, "model": MOCK_MODEL, "fast_model": MOCK_MODEL}


# ----------------------------------------------------------------------------- tool schema conversion

def to_openai_tools(mcp_tools: list[dict], hide: tuple[str, ...] = ("business",)) -> list[dict]:
    """MCP tools/list entries -> OpenAI `tools` (type function). The `business` argument is filled in by the
    client, so it is hidden from the model."""
    out = []
    for t in mcp_tools:
        schema = dict(t.get("inputSchema") or t.get("input_schema") or {"type": "object"})
        props = {k: v for k, v in (schema.get("properties") or {}).items() if k not in hide}
        params = {"type": "object", "properties": props}
        req = [k for k in schema.get("required") or [] if k not in hide]
        if req:
            params["required"] = req
        out.append({"type": "function", "function": {"name": t["name"],
                                                     "description": (t.get("description") or "")[:1024],
                                                     "parameters": params}})
    return out


# ----------------------------------------------------------------------------- chat

_THINK = re.compile(r"<think>.*?</think>", re.S)


def _is_followup(messages: list[dict]) -> bool:
    """True when the last message is a tool result: the model is reacting to tools it already called."""
    return bool(messages) and messages[-1].get("role") == "tool"


def chat(messages: list[dict], tools: list[dict] | None = None, system: str = "") -> dict:
    """One Chat Completions step. Returns {"message": {role, content, tool_calls?}, "finish_reason", "model",
    "mock"}. `messages` are OpenAI-format (user / assistant with tool_calls / tool)."""
    msgs = ([{"role": "system", "content": system}] if system else []) + list(messages)
    if not is_live():
        return _mock(msgs, tools or [])
    use = fast_model() if _is_followup(messages) else model()
    payload = {"model": use, "messages": msgs, "max_tokens": 1024, "temperature": 0.2}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    try:
        r = httpx.post(f"{base_url()}/chat/completions", json=payload, timeout=90,
                       headers={"Authorization": f"Bearer {os.environ['NEBIUS_API_KEY'].strip()}",
                                "content-type": "application/json"})
    except httpx.HTTPError as exc:
        raise NebiusError(f"Could not reach Nebius Token Factory: {exc}") from exc
    if r.status_code != 200:
        raise NebiusError(f"Nebius Token Factory returned {r.status_code}: {r.text[:300]}")
    body = r.json()
    choice = (body.get("choices") or [{}])[0]
    m = choice.get("message") or {}
    msg = {"role": "assistant", "content": _THINK.sub("", m.get("content") or "").strip()}
    if m.get("tool_calls"):
        msg["tool_calls"] = [{"id": c.get("id") or f"call_{i}", "type": "function",
                              "function": {"name": c["function"]["name"],
                                           "arguments": c["function"].get("arguments") or "{}"}}
                             for i, c in enumerate(m["tool_calls"])]
    return {"message": msg, "finish_reason": choice.get("finish_reason"), "model": body.get("model", use),
            "mock": False, "usage": body.get("usage")}


# ----------------------------------------------------------------------------- mock planner

def _dollars(text: str) -> float | None:
    m = re.search(r"\$?\s*(\d+(?:\.\d+)?)\s*(k|thousand)?", text.replace(",", ""), re.I)
    return float(m.group(1)) * (1000 if m.group(2) else 1) if m else None


_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}


def _plan(text: str, done: list[dict]) -> tuple[str, dict] | None:
    """Next (tool, arguments) for this turn, or None when the turn is answered. `done` is the tool messages
    already returned this turn, in order. Mirrors what Nemotron is asked to do in the system prompt."""
    t = text.lower()
    names = [d.get("name") for d in done]
    if re.search(r"\b(dispute|cancel|expected|that's fine|was fine|mark)\b", t):
        if "review_charge" in names:
            return None
        listing = next((d for d in done if d.get("name") == "unusual_charges"), None)
        if not listing:
            return "unusual_charges", {}
        # pick the flagged charge whose vendor the owner named, else the first one
        rows = re.findall(r"\[([0-9a-f]{8})\] \w+ ([^;]*)", listing.get("content") or "")
        if not rows:
            return None
        hit = next((cid for cid, why in rows
                    if any(w.lower() in t for w in re.findall(r"\b[A-Z][a-z]{3,}\b", why) if w not in ("Paid", "First"))),
                   rows[0][0])
        verdict = "cancel" if "cancel" in t else "dispute" if "dispute" in t else "expected"
        return "review_charge", {"charge_id": hit, "verdict": verdict}
    if re.search(r"(flour|sugar|butter|egg|dairy|supplier|price[s]? (went|go|are|is) up|went up|payroll|market)", t):
        if "market_check" in names:
            return None
        item = next((w for w in ("flour", "sugar", "butter", "eggs", "dairy") if w in t), "flour")
        m = re.search(r"(\d+|one|two|three|four|five|six)\s+weeks?", t)
        weeks = (int(m.group(1)) if m.group(1).isdigit() else _WORDS[m.group(1)]) if m else 3
        args = {"item": item, "weeks": weeks}
        if (pct := re.search(r"(\d+(?:\.\d+)?)\s*(%|percent)", t)):
            args["pct_change"] = float(pct.group(1))
        return "market_check", args
    if done:
        return None
    if re.search(r"afford|hire|lease|buy", t):
        amt = _dollars(t) or 0.0
        once = re.search(r"(one.time|up front|upfront|once)", t)
        item = (re.search(r"afford ((?:a |an |the )?[a-z\s-]+?)(?: at| for| \$|\?|$)", t) or [None, "it"])[1]
        return "can_i_afford", ({"item": item, "one_time_cost": amt} if once else {"item": item, "monthly_cost": amt})
    if re.search(r"(warn|alert|tell me if|let me know if)", t):
        return "set_cash_alert", {"threshold": _dollars(t) or 5000}
    if re.search(r"(weird|unusual|odd|strange|charge|duplicate)", t):
        return "unusual_charges", {}
    if re.search(r"(forecast|next|weeks|ahead|coming|run out|runway)", t):
        return "cash_forecast", {"weeks": 13}
    if re.search(r"(spend|changed|last month|why)", t):
        return "spending_change", {}
    if re.search(r"(how much|balance|cash on hand|in the bank)", t):
        return "cash_snapshot", {}
    return "morning_brief", {}


def _mock(msgs: list[dict], tools: list[dict]) -> dict:
    last_user = max((i for i, m in enumerate(msgs) if m.get("role") == "user"), default=-1)
    text = str(msgs[last_user].get("content") or "") if last_user >= 0 else ""
    done = [m for m in msgs[last_user + 1:] if m.get("role") == "tool"]
    offered = {t["function"]["name"] for t in tools}
    step = _plan(text, done)
    if step and step[0] in offered:
        name, args = step
        call = {"id": f"mock_{len(done) + 1}", "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)}}
        return {"message": {"role": "assistant", "content": "", "tool_calls": [call]},
                "finish_reason": "tool_calls", "model": MOCK_MODEL, "mock": True}
    # Final answer: the last tool's first paragraph, which every Till tool writes for the ear.
    reply = (str(done[-1].get("content") or "").split("\n\n")[0].strip() if done
             else "Ask me about your cash, your charges, or whether a price change still lets you make payroll.")
    return {"message": {"role": "assistant", "content": reply}, "finish_reason": "stop",
            "model": MOCK_MODEL, "mock": True}
