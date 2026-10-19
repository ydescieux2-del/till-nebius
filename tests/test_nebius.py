"""Nebius Token Factory client, tool-schema conversion, and the agent loop in mock mode.
Run: python tests/test_nebius.py

No network and no key needed. The live-request test swaps httpx.post for a fake to check the exact URL, auth
header, model routing, and OpenAI tool format Till sends once NEBIUS_API_KEY is set.
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

for k in ("NEBIUS_API_KEY", "TAVILY_API_KEY", "TILL_AGENT_MODEL", "TILL_FAST_MODEL", "NEBIUS_BASE_URL"):
    os.environ.pop(k, None)
os.environ["TILL_DB"] = str(Path(tempfile.mkdtemp()) / "test_state.db")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from till import memory, nebius, server, tavily  # noqa: E402

BIZ = "test-nebius"
SYSTEM = "You are Till."


def _mcp_tools() -> list[dict]:
    return [t.model_dump(by_alias=True, exclude_none=True) for t in asyncio.run(server.mcp.list_tools())]


def _openai_tools() -> list[dict]:
    return nebius.to_openai_tools(_mcp_tools())


def run_turn(history: list[dict], text: str) -> tuple[str, list[str]]:
    """The browser's agent loop (web/index.html agentTurn), in Python: chat step, run tool calls, repeat."""
    tools = _openai_tools()
    history.append({"role": "user", "content": text})
    called = []
    for _ in range(6):
        out = nebius.chat(history, tools, SYSTEM)
        msg = out["message"]
        history.append(msg)
        if not msg.get("tool_calls"):
            return msg["content"], called
        for c in msg["tool_calls"]:
            name, args = c["function"]["name"], json.loads(c["function"]["arguments"])
            res = getattr(server, name)(**args, business=BIZ)
            called.append(name)
            history.append({"role": "tool", "tool_call_id": c["id"], "name": name,
                            "content": "\n\n".join(x.text for x in res.content)})
    raise AssertionError("ran out of steps")


# ---------------------------------------------------------------- schema conversion

def test_tools_convert_to_openai_function_format():
    tools = _openai_tools()
    assert len(tools) == 11
    for t in tools:
        assert t["type"] == "function" and set(t["function"]) == {"name", "description", "parameters"}
        p = t["function"]["parameters"]
        assert p["type"] == "object" and "business" not in p["properties"]
        assert "business" not in p.get("required", [])
    review = next(t for t in tools if t["function"]["name"] == "review_charge")["function"]["parameters"]
    assert review["required"] == ["charge_id", "verdict"]
    mc = next(t for t in tools if t["function"]["name"] == "market_check")["function"]["parameters"]
    assert {"item", "weeks", "pct_change"} <= set(mc["properties"])
    # import_books requires business in MCP; the model never sees it
    ib = next(t for t in tools if t["function"]["name"] == "import_books")["function"]["parameters"]
    assert ib["required"] == ["csv_text", "opening_balance"]


# ---------------------------------------------------------------- mock mode

def test_mock_mode_is_labeled():
    assert not nebius.is_live()
    src = nebius.source()
    assert src["live"] is False and src["label"] == nebius.MOCK_NOTE and src["model"] == nebius.MOCK_MODEL
    out = nebius.chat([{"role": "user", "content": "morning brief"}], _openai_tools())
    assert out["mock"] is True and out["model"] == "mock-nemotron"


def test_demo_story_flour_payroll():
    """The demo story end to end: Nemotron (mock) plans, Tavily (mock) finds the price, the engine does the math."""
    memory.forget(BIZ)
    tavily._cache.clear()
    reply, called = run_turn([], "Flour went up. Can I still make payroll in three weeks?")
    assert called == ["market_check"], called
    assert reply.startswith("Yes, comfortably.") and "Payroll" in reply and "7.5 percent" in reply, reply
    assert "sample price data until the Tavily key is set" in reply


def test_chained_calls_dispute_the_flour_charge():
    memory.forget(BIZ)
    reply, called = run_turn([], "Mark the flour charge as a dispute")
    assert called == ["unusual_charges", "review_charge"], called
    assert "Golden Mill Flour Co" in reply and "dispute" in reply, reply
    assert "155e2782" in memory.reviews(BIZ)
    memory.forget(BIZ)


def test_owner_stated_percent_passes_through():
    reply, called = run_turn([], "My dairy supplier says prices are up 12 percent. Are we OK for the next four weeks?")
    assert called == ["market_check"] and "up about 12 percent (as you said)" in reply, reply
    assert "next 4 weeks" in reply


def test_plain_questions_route_to_one_tool():
    for q, tool in [("Give me my morning brief", "morning_brief"), ("Any weird charges?", "unusual_charges"),
                    ("Can I afford a part-time baker at $1,800 a month?", "can_i_afford"),
                    ("What changed in spending last month?", "spending_change"),
                    ("How much is in the bank?", "cash_snapshot")]:
        _, called = run_turn([], q)
        assert called == [tool], (q, called)
    memory.forget(BIZ)


# ---------------------------------------------------------------- live request shape (fake HTTP)

class _Resp:
    status_code = 200
    text = ""

    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


def _live(fake):
    real = nebius.httpx.post
    os.environ["NEBIUS_API_KEY"] = "test-key-not-real"
    nebius.httpx.post = fake
    return real


def _restore(real):
    nebius.httpx.post = real
    os.environ.pop("NEBIUS_API_KEY", None)


def test_live_request_shape_and_model_routing():
    seen = []

    def fake(url, json=None, headers=None, timeout=None):
        seen.append({"url": url, "json": json, "headers": headers})
        return _Resp({"model": json["model"], "choices": [{"finish_reason": "tool_calls", "message": {
            "role": "assistant", "content": "<think>plan it</think>",
            "tool_calls": [{"id": "call_1", "type": "function",
                            "function": {"name": "market_check", "arguments": "{\"item\": \"flour\"}"}}]}}]})

    real = _live(fake)
    try:
        tools = _openai_tools()
        first = nebius.chat([{"role": "user", "content": "flour went up"}], tools, SYSTEM)
        follow = nebius.chat([{"role": "user", "content": "flour went up"}, first["message"],
                              {"role": "tool", "tool_call_id": "call_1", "name": "market_check", "content": "ok"}],
                             tools, SYSTEM)
    finally:
        _restore(real)
    a, b = seen
    assert a["url"] == "https://api.tokenfactory.nebius.com/v1/chat/completions", a["url"]
    assert a["headers"]["Authorization"] == "Bearer test-key-not-real"
    assert a["json"]["model"] == nebius.DEFAULT_MODEL and b["json"]["model"] == nebius.DEFAULT_FAST_MODEL
    assert a["json"]["model"].startswith("nvidia/") and "nemotron" in a["json"]["model"].lower()
    assert a["json"]["messages"][0] == {"role": "system", "content": SYSTEM}
    assert a["json"]["tools"] == tools and a["json"]["tool_choice"] == "auto"
    assert a["json"]["max_tokens"] == b["json"]["max_tokens"] == 4096           # room for reasoning plus the reply
    assert first["mock"] is False and first["message"]["content"] == ""          # <think> stripped
    assert first["message"]["tool_calls"][0]["function"]["name"] == "market_check"


def test_fast_model_can_be_disabled():
    os.environ["TILL_FAST_MODEL"] = ""
    try:
        assert nebius.fast_model() == nebius.model() == nebius.DEFAULT_MODEL
    finally:
        os.environ.pop("TILL_FAST_MODEL", None)


def test_live_errors_are_not_masked_by_mock():
    class Bad:
        status_code = 401
        text = "invalid api key"

    real = _live(lambda *a, **k: Bad())
    try:
        nebius.chat([{"role": "user", "content": "hi"}], [])
    except nebius.NebiusError as exc:
        assert "401" in str(exc)
        return
    finally:
        _restore(real)
    raise AssertionError("a live 401 must raise, not fall back to the mock")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
    print("ALL NEBIUS CHECKS PASSED")
