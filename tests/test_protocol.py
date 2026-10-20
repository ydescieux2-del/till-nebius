"""Wire-level check: speak raw JSON-RPC over Streamable HTTP, exactly as the demo page does.

    python tests/test_protocol.py [http://127.0.0.1:8765/mcp]

Uses a throwaway business id so it never touches the demo's memory.
"""
import json
import sys
import uuid

import httpx

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765/mcp"
PROTOCOL = "2025-11-25"
BIZ = f"test-{uuid.uuid4().hex[:6]}"
HEADERS = {"content-type": "application/json", "accept": "application/json, text/event-stream"}
_id = 0


def rpc(c, method, params=None, notify=False):
    global _id
    msg = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if not notify:
        _id += 1
        msg["id"] = _id
    r = c.post(URL, json=msg, headers=HEADERS)
    r.raise_for_status()
    if notify:
        return None
    body = r.json()
    assert "error" not in body, body
    return r, body["result"]


def main():
    with httpx.Client(timeout=30) as c:
        r, init = rpc(c, "initialize", {"protocolVersion": PROTOCOL, "capabilities": {
            "extensions": {"io.modelcontextprotocol/ui": {"mimeTypes": ["text/html;profile=mcp-app"]}}},
            "clientInfo": {"name": "till-protocol-test", "version": "0"}})
        print("negotiated", init["protocolVersion"], "server", init["serverInfo"]["name"])
        assert init["protocolVersion"] >= PROTOCOL
        sid = r.headers.get("mcp-session-id")
        if sid:
            HEADERS["mcp-session-id"] = sid
        HEADERS["mcp-protocol-version"] = init["protocolVersion"]
        rpc(c, "notifications/initialized", notify=True)

        _, tools = rpc(c, "tools/list")
        names = [t["name"] for t in tools["tools"]]
        print("tools", names)
        assert all(t.get("_meta", {}).get("ui", {}).get("resourceUri") == "ui://till/card.html" for t in tools["tools"])

        _, res = rpc(c, "resources/read", {"uri": "ui://till/card.html"})
        assert res["contents"][0]["mimeType"] == "text/html;profile=mcp-app"
        print("card resource ok", len(res["contents"][0]["text"]), "chars")

        def call(name, **args):
            _, out = rpc(c, "tools/call", {"name": name, "arguments": {"business": BIZ, **args}})
            assert not out.get("isError"), out
            print(f"\n{name}: {out['content'][0]['text']}")
            return out

        brief = call("morning_brief")
        assert brief["structuredContent"]["card"]["big"].startswith("$")
        call("cash_snapshot")
        call("cash_forecast", weeks=13)
        charges = call("unusual_charges")["structuredContent"]["charges"]
        assert len(charges) == 4, len(charges)
        call("review_charge", charge_id=charges[0]["id"], verdict="dispute", note="double flour payment")
        assert len(call("unusual_charges")["structuredContent"]["charges"]) == 3
        call("can_i_afford", item="a part-time baker", monthly_cost=1800)
        call("spending_change")
        call("set_cash_alert", threshold=12000)
        second = call("morning_brief")
        assert "since your last brief" in second["content"][0]["text"] or "3 charges" in second["content"][0]["text"]
        market = call("market_check", item="flour", weeks=3)
        if "shock" in market["structuredContent"]:
            assert market["structuredContent"]["shock"]["verdict"] in ("comfortable", "tight", "not yet")
        else:   # live Tavily found no clear percent that day: Till must say so, never guess
            assert "won't guess" in market["content"][0]["text"], market["content"][0]["text"]
        assert market["structuredContent"]["card"]["kind"] == "market"
        stated = call("market_check", item="flour", weeks=3, pct_change=12)   # owner-stated: always computed
        assert stated["structuredContent"]["shock"]["verdict"] in ("comfortable", "tight", "not yet")

        # The agent loop exactly as the page runs it: /agent/llm (Nemotron, or the labeled mock) plans, the
        # client runs each tool call over /mcp, and the tool result goes back as an OpenAI tool message.
        base = URL.rsplit("/mcp", 1)[0]
        status = c.get(f"{base}/agent/status").json()
        print("\nagent status", status)
        mcp_tools = [{"name": t["name"], "description": t.get("description", ""), "inputSchema": t["inputSchema"]}
                     for t in tools["tools"]]
        history = [{"role": "user", "content": "Flour went up. Can I still make payroll in three weeks?"}]
        called = []
        for _ in range(6):
            r = c.post(f"{base}/agent/llm", json={"system": "You are Till.", "messages": history, "tools": mcp_tools})
            assert r.status_code == 200, r.text
            out = r.json()
            msg = out["message"]
            history.append(msg)
            if not msg.get("tool_calls"):
                break
            for tc in msg["tool_calls"]:
                res = call(tc["function"]["name"], **json.loads(tc["function"]["arguments"] or "{}"))
                called.append(tc["function"]["name"])
                history.append({"role": "tool", "tool_call_id": tc["id"], "name": tc["function"]["name"],
                                "content": "\n\n".join(x["text"] for x in res["content"])})
        print(f"agent ({out['model']}, mock={out['mock']}) called {called}: {msg['content']}")
        assert "market_check" in called and msg["content"]
        call("forget_business")
        print("\nALL PROTOCOL CHECKS PASSED")


if __name__ == "__main__":
    main()
