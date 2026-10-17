"""Tavily client, percent extraction, and the market_check tool. Run: python tests/test_tavily.py

No network and no key needed. The live-request test swaps httpx.post for a fake to check the exact URL, auth
header, and body Till sends once TAVILY_API_KEY is set.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.pop("TAVILY_API_KEY", None)
os.environ["TILL_DB"] = str(Path(tempfile.mkdtemp()) / "test_state.db")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from till import engine, server, tavily  # noqa: E402

T, O = engine.load_csv(), engine.DEMO_OPENING_BALANCE


def _fresh():
    tavily._cache.clear()
    os.environ.pop("TAVILY_API_KEY", None)


def test_mock_mode_without_key():
    _fresh()
    assert not tavily.is_live() and tavily.source()["label"] == tavily.MOCK_NOTE
    body = tavily.search("wholesale flour price")
    assert body["query"] == "wholesale flour price" and len(body["results"]) == 4
    assert all(r["title"].startswith("SAMPLE") and "example.com" in r["url"] for r in body["results"])
    assert {"title", "url", "content", "score"} <= set(body["results"][0])


def test_query_required():
    try:
        tavily.search("  ")
    except ValueError:
        return
    raise AssertionError("empty query should raise")


def test_percent_extraction():
    assert tavily.percents("flour is up 9% since July") == [9.0]
    assert tavily.percents("invoices rose about 7 percent") == [7.0]
    assert tavily.percents("an 8% increase on bread flour and a 6% increase on pastry") == [8.0, 6.0]
    assert tavily.percents("coffee fell 4% this month") == [-4.0]
    assert tavily.percents("50 lb bags at $28") == []


def test_price_change_is_median_of_item_sources():
    _fresh()
    r = tavily.price_change(tavily.search("flour"), "flour")
    assert r["pct"] == 7.5, r                             # 9, 7, 8, 6 -> 7.5; the coffee result is ignored
    assert len(r["evidence"]) == 3 and all("flour" in e["title"].lower() or e["percents"] for e in r["evidence"])
    assert tavily.price_change({"results": []}, "flour") == {"pct": None, "evidence": []}


def test_live_request_shape():
    _fresh()
    seen = {}

    class Resp:
        status_code = 200
        text = ""

        def json(self):
            return json.loads(tavily.FIXTURE.read_text())

    def fake(url, json=None, headers=None, timeout=None):
        seen.update(url=url, json=json, headers=headers)
        return Resp()

    real = tavily.httpx.post
    os.environ["TAVILY_API_KEY"] = "tvly-test-not-real"
    tavily.httpx.post = fake
    try:
        tavily.search("wholesale flour price", max_results=5)
    finally:
        tavily.httpx.post = real
        _fresh()
    assert seen["url"] == "https://api.tavily.com/search"
    assert seen["headers"]["Authorization"] == "Bearer tvly-test-not-real"
    assert seen["json"]["query"] == "wholesale flour price" and seen["json"]["max_results"] == 5
    assert seen["json"]["search_depth"] == "basic" and seen["json"]["include_answer"] == "basic"


def test_live_errors_are_not_masked_by_mock():
    _fresh()

    class Resp:
        status_code = 401
        text = "Unauthorized: missing or invalid API key."

    real = tavily.httpx.post
    os.environ["TAVILY_API_KEY"] = "bad"
    tavily.httpx.post = lambda *a, **k: Resp()
    try:
        out = server.market_check("flour", business="test-tavily")
    finally:
        tavily.httpx.post = real
        _fresh()
    assert out.is_error and "401" in out.content[0].text


def test_market_check_feeds_the_engine():
    """Tavily changes the answer: the extracted percent goes into engine.price_shock, nothing else."""
    _fresh()
    out = server.market_check("flour", weeks=3, business="test-tavily")
    sc = out.structured_content
    spend = engine.weekly_spend(T, "flour")
    assert spend["vendors"] == ["Golden Mill Flour Co"]
    want = engine.price_shock(T, O, spend["weekly"], 7.5, weeks=3, floor=server.DEFAULT_FLOOR)
    want.pop("window")
    assert sc["shock"] == want and sc["market"]["pct"] == 7.5
    assert sc["card"]["badge"]["text"] == tavily.MOCK_NOTE
    assert "sample price data until the Tavily key is set" in out.content[0].text
    bigger = server.market_check("flour", weeks=3, pct_change=60, business="test-tavily").structured_content["shock"]
    assert bigger["low_after"] < sc["shock"]["low_after"] < sc["shock"]["low_before"]


def test_market_check_refuses_to_guess():
    _fresh()
    out = server.market_check("dairy", business="test-tavily")    # no dairy figure in the sample results
    assert not out.is_error and "won't guess" in out.content[0].text
    assert "shock" not in out.structured_content
    assert server.market_check("saffron", business="test-tavily").is_error   # not in the books


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
    print("ALL TAVILY CHECKS PASSED")
