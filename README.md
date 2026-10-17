# Till on Nemotron: flour went up, can I still make payroll?

> "Flour went up. Can I still make payroll in three weeks?"
>
> "Yes, comfortably. Flour is up about 7.5 percent (the middle figure across 3 sources Tavily found), which adds
> about 75 dollars a week on a flour bill of about 1 thousand dollars a week. Payroll of about 6.7 thousand dollars
> lands October 16 and October 30, and it's covered. Your lowest cash in the next 3 weeks goes from 11.2 thousand
> dollars to 11 thousand dollars."

(That answer is from mock mode, on sample data. Live numbers depend on what Tavily finds that day.)

Small business owners get hit by price changes they can't plan for: a supplier raises flour, and the real question
is not "how much?" but "do I still make payroll?". Till answers that in one spoken sentence. Three parts, each with
one job:

- **NVIDIA Nemotron on Nebius Token Factory** plans: it reads the owner's question, picks Till's tools, chains
  them, and phrases the answer for the ear.
- **Tavily** looks up today's price change for the item on the web.
- **Till's deterministic cash engine** does every bit of arithmetic: the owner's real weekly spend on that item,
  the extra cost, the payroll dates in the window, and the 13 week forecast.

The model never computes a number. Tavily changes the answer instead of decorating it: the percent it finds is the
input to the forecast, and a different figure gives a different verdict.

Till is an **MCP server** (spec 2025-11-25, Streamable HTTP) with a web client that has a microphone, speech
output, a tool trace, and an MCP Apps card host. Any MCP client can use the tools.

## How Nemotron and Token Factory are used

- `till/nebius.py` is an OpenAI-compatible Chat Completions client for Token Factory:
  `POST https://api.tokenfactory.nebius.com/v1/chat/completions` with `Authorization: Bearer $NEBIUS_API_KEY`,
  OpenAI `tools` and `tool_choice: "auto"`.
- The web page is the MCP client. Each agent step goes to the server's `/agent/llm` route, which converts the MCP
  `tools/list` entries to OpenAI function format (`nebius.to_openai_tools`) and makes the runtime call to Token
  Factory. The page then runs every tool call itself over `/mcp` and sends the result back as an OpenAI `tool`
  message.
- Model routing: the first step of a turn (planning which tools to chain) runs on **Nemotron 3 Super**
  (`nvidia/nemotron-3-super-120b-a12b`). Steps after tool results, which are mostly phrasing the answer, run on
  **Nemotron 3 Nano** (`nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`) for speed. Both are set by `TILL_AGENT_MODEL` and
  `TILL_FAST_MODEL`; an empty `TILL_FAST_MODEL` uses Super for every step. Any `<think>` block in the reply is
  stripped before it is spoken.
- The page header shows which model answered each step, and the trace shows every tool call.

## How Tavily is used

`market_check(item, weeks, pct_change?)`:

1. Finds what the business really spends on the item each week (vendors and memos in its books, last 8 weeks).
2. Calls `POST https://api.tavily.com/search` (`Authorization: Bearer $TAVILY_API_KEY`, `search_depth: basic`,
   `max_results: 5`, `include_answer: basic`) for the item's current price change.
3. Pulls stated percent changes out of the result text with fixed patterns ("up 9%", "rose about 7 percent",
   "an 8% increase") and takes the median, keeping only results that mention the item. Same results, same number.
4. Runs `engine.price_shock`: the extra weekly cost through the forecast for the next N weeks, with the payroll
   dates from the books. The answer is comfortable, tight, close, or short, and the card lists the sources used.

If Tavily finds no clear figure, Till says so and changes nothing rather than guessing. If the owner states the
change ("my supplier says 12 percent"), that figure replaces the web one and the answer says so.

## Run it (2 minutes)

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m till.server
# MCP endpoint  http://127.0.0.1:8765/mcp
# Demo page     http://127.0.0.1:8765/      (Chrome for the microphone)
```

Copy `env.example` to `.env` (gitignored) and set:

- `NEBIUS_API_KEY`: a Token Factory key. **Without it a mock planner stands in for Nemotron**: a deterministic
  keyword planner that returns the same OpenAI-shaped tool calls. The header says "mock planner (no Nebius key)",
  each trace line says "mock planner", and every `/agent/llm` response carries `"mock": true`.
- `TAVILY_API_KEY`: a Tavily key. **Without it `market_check` reads a sample fixture**
  (`till/fixtures/mock_tavily_search.json`, invented text on example.com links, every title marked SAMPLE), and
  every answer and card says "sample price data until the Tavily key is set".
- `ELEVENLABS_API_KEY` (optional): spoken answers in an ElevenLabs voice instead of the browser voice.

So the whole app runs and every test passes with no keys at all. `scripts/live_check.py` exercises both live APIs
once the keys exist: it lists Token Factory models and confirms the configured Nemotron ids, makes one plain chat
call on Super and on Nano, makes one tool-calling call with Till's real tool list to check Nemotron picks
`market_check` for the demo question, and runs the exact Tavily search plus the percent extraction. It prints
only, saves nothing, and skips any half whose key is missing.

Container: `docker build -t till-nebius . && docker run -p 8080:8080 -e NEBIUS_API_KEY=... -e TAVILY_API_KEY=... till-nebius`.
Hosting configs: `railway.json` (Dockerfile build, health check `/agent/status`) and `render.yaml` (Render free
web service), plus `.github/workflows/keepwarm.yml`, which pings the demo every 10 minutes once the repo variable
`DEMO_URL` is set.

## Tests

```bash
.venv/bin/python tests/test_engine.py      # cash engine, including weekly_spend and price_shock
.venv/bin/python tests/test_nebius.py      # tool conversion, mock planner, the agent loop, live request shape
.venv/bin/python tests/test_tavily.py      # Tavily client, percent extraction, market_check against the engine
.venv/bin/python tests/test_protocol.py http://127.0.0.1:8765/mcp   # raw JSON-RPC, every tool, agent loop over HTTP
```

`test_nebius.py` and `test_tavily.py` pin the exact live requests (URL, bearer header, model ids, body) with a
fake HTTP layer, and check that a live error is reported, never silently replaced by mock output.

## How it's built

```
voice / text > demo page = MCP client > Streamable HTTP > Till MCP server
               | agent loop: /agent/llm > Nebius Token Factory (Nemotron Super, then Nano)
               | MCP Apps host < ui://till/card.html     | nebius.py  Token Factory client, tool conversion, mock planner
                                                        | tavily.py  search client, percent extraction, mock fixture
                                                        | engine.py  cash math: forecast, affordability, price_shock
                                                        | memory.py  SQLite: alerts, reviews, briefs
```

Tools: `market_check` (Tavily), `morning_brief`, `cash_snapshot`, `cash_forecast`, `unusual_charges`,
`review_charge`, `can_i_afford`, `spending_change`, `set_cash_alert`, `import_books`, `forget_business`.

## Sources for the API facts in this repo

- Token Factory base URL `https://api.tokenfactory.nebius.com/v1/` and bearer `NEBIUS_API_KEY`:
  https://docs.tokenfactory.nebius.com/ (quickstart). Verified 2026-10-16.
- `GET /v1/models`: https://docs.tokenfactory.nebius.com/api-reference/examples/list-of-models. Verified 2026-10-16.
- OpenAI-format `tools`, `tool_choice`, and `tool` messages:
  https://docs.tokenfactory.nebius.com/ai-models-inference/function-calling. Verified 2026-10-16.
- Nemotron 3 Super id `nvidia/nemotron-3-super-120b-a12b`: Nebius's own announcement links its playground with
  this id (https://nebius.com/blog/posts/nemotron3-super-now-available). Not yet confirmed against `/v1/models`.
- Nemotron 3 Nano id `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`: **UNVERIFIED**, from a third-party model listing
  only. `scripts/live_check.py` checks both ids against `/v1/models` and names the right ones if they differ.
- Tavily `POST https://api.tavily.com/search`, bearer key, request and response fields:
  https://docs.tavily.com/documentation/api-reference/endpoint/search. Verified 2026-10-16.

## Data

All business data is synthetic (the fictional Sunrise Bakery, with planted problems). The Tavily fixture is
invented and labeled SAMPLE in every result. Live Tavily responses are cached in server memory only. Till is a tool
for owners, not a licensed advisor, and it does not give tax or investment advice.

## License

MIT, Copyright (c) 2026 Descieux Digital LLC. See `LICENSE`.
