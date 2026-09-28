# Server MCP di scraping

Un server MCP auto-ospitato con un tool solo, `fetch_url`: apre un indirizzo in
un browser headless vero, con JavaScript eseguito, e restituisce il contenuto
come Markdown pulito. Parla **streamable HTTP**, il trasporto che gli agenti
della piattaforma usano per ogni server MCP.

Costruito in casa sopra [Crawl4AI](https://github.com/unclecode/crawl4ai) --
la libreria è matura e molto usata, i wrapper MCP della comunità intorno a lei
meno -- e serve anche da esempio di come si scrive e si collega un server MCP.

```bash
uv sync
uv run python -m scraping_mcp      # http://127.0.0.1:8600/mcp
uv run pytest
```

I test usano un crawler finto (`FakeCrawler` in `tests/test_scraper.py`):
nessun browser, nessuna rete. L'automazione del browser è compito della suite
di Crawl4AI.

## Collegarlo a un agente

Ogni agente della piattaforma legge i propri server MCP da una variabile
(`MASTER_MCP_SERVERS`, `KNOWLEDGE_MCP_SERVERS`, ...), con lo stesso codice
(`platform_core.mcp`). Nel compose è già collegato al knowledge agent:

```
KNOWLEDGE_MCP_SERVERS=[{"name":"scraping","url":"http://scraping-mcp:8600/mcp"}]
```

Per server si possono limitare i tool (`allowed_tools`), chiedere
un'approvazione prima di ogni chiamata (`approval`) e fissare un timeout
(`timeout_seconds`).

## Perché non può essere usato contro la piattaforma

Uno scraper apre pagine che sceglie qualcun altro: il modello, e dietro il
modello chiunque riesca a fargli leggere un testo. Tre difese indipendenti:

1. **L'indirizzo richiesto** si controlla prima di aprirlo: solo `http` e
   `https`, e solo verso indirizzi pubblici. Loopback, reti private e
   link-local -- il cluster, i nodi, l'endpoint dei metadati del cloud -- sono
   rifiutati, a meno di `SCRAPING_ALLOW_PRIVATE_TARGETS=true`.
   `SCRAPING_TARGET_HOSTS` restringe ancora, a un elenco di host.
2. **Ogni richiesta del browser**, non solo la prima: redirect, script,
   immagini, WebSocket passano dallo stesso controllo (le route di
   Playwright), perché una pagina pubblica può chiedere al browser di caricare
   un indirizzo interno.
3. **La rete**: nel compose sta su una rete dove c'è solo il knowledge agent,
   e da lì non risolve nemmeno Postgres o gli altri servizi; su Kubernetes la
   NetworkPolicy gli lascia solo Internet e il DNS.

In più il server risponde solo agli host in `SCRAPING_SERVER_HOSTS`: è la
protezione dal DNS rebinding dell'SDK MCP, che impedisce a una pagina aperta
nel browser di qualcuno di parlare con il server attraverso un nome che punta
a lui.

## Configurazione

La tabella completa, generata dal codice, sta nel
[README di demo-infra](../demo-infra/README.md#scraping-mcp-server).

## L'immagine

Parte dall'immagine ufficiale di Playwright, fissata per digest, e gira come
`pwuser` (uid 1001), senza capability e senza possibilità di acquisire
privilegi. È l'unico servizio con il filesystem scrivibile: Chromium scrive
profilo e cache nella home. Per questo il suo confinamento sta soprattutto
nella rete.
