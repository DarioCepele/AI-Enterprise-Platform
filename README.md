# demo-scraping-mcp

A self-hosted MCP server: fetches a URL, renders it in a real headless
browser, and returns its content as clean Markdown -- one tool, `fetch_url`.

Built first-party rather than adopting one of the handful of community MCP
wrappers around [Crawl4AI](https://github.com/unclecode/crawl4ai) (the
library itself, not those wrappers, is mature and widely used) -- see this
repo's own `src/scraping_mcp/server.py` and `scraper.py` for why. Speaks
**streamable HTTP**, the transport `demo-master-agent`, `demo-knowledge-agent`
and `demo-analysis-agent`'s MCP clients (`MCPStreamableHTTPTool`) already use
for every other MCP server they connect to.

## Run locally

```
uv sync
uv run python -m scraping_mcp
```

Then point any MCP client at `http://127.0.0.1:8600/mcp`.

## Test

```
uv run pytest
```

The tests substitute a fake crawler (`tests/test_scraper.py`'s
`FakeCrawler`) -- no real browser or network involved. Nothing here exercises
Crawl4AI's own browser-automation code; that is Crawl4AI's own test suite's
job.

## Wiring it into an agent

Any agent in this platform whose `*_MCP_SERVERS` variable is set will pick
up this server's `fetch_url` tool automatically (see each agent's own
`mcp_tools.py` / `config.py`). For example, for `demo-knowledge-agent`:

```
KNOWLEDGE_MCP_SERVERS=[{"name":"scraping","url":"http://scraping-mcp:8600/mcp"}]
```

## Environment

| Variable | Default | What it decides |
|---|---|---|
| `PORT` | `8600` | Where the service listens. |
