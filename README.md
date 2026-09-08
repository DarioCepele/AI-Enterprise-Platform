# Knowledge agent

Sottoagente del laboratorio AG-UI, esposto via **A2A**. Il master agent lo
interroga come agente remoto, non come tool locale: è il pezzo che rende la
tappa 3 un sistema multi-agente e non un agente con più funzioni.

```powershell
uv sync
uv run python -m knowledge      # http://127.0.0.1:8200
```

Risponde leggendo un piccolo corpus di documenti Markdown (`src/knowledge/corpus/`).
Il contenuto non è il punto: serve che le risposte costino tempo vero, così
streaming e parallelismo si misurano invece di immaginarli.

## I due default che disattivano lo streaming in silenzio

La spec li dichiarava da uno spike precedente. Rifatti qui, contro questo
servizio, con `qwen/qwen3.8-27b`:

| Configurazione | Risultato |
| --- | --- |
| `A2AAgent(url=...)` | **1 update**, tutto insieme a +8,62 s |
| `A2AAgent(agent_card=card)` | **62 update**, il primo a +2,77 s, totale 4,16 s |

Chi costruisce il client con il solo `url` non riceve un errore né un warning:
riceve una risposta sola alla fine. La card va scaricata a mano da
`/.well-known/agent-card.json` e passata come `agent_card`.

Lato server vale l'altro default:

```python
A2AExecutor(agent=agent, stream=True)   # il default e' stream=False
```

E la card deve dichiarare `AgentCapabilities(streaming=True)`, altrimenti il
client non prova nemmeno.

**Parallelismo, misurato:** due interrogazioni insieme **6,97 s** contro
**13,77 s** in serie. È la ragione per cui la tappa 3 esiste.

## Una trappola in più, che la spec non aveva

Con `a2a-sdk` 1.x i tipi sono **protobuf**, non pydantic, e la card ha
`supported_interfaces` al posto di `url` + `preferred_transport`. La versione
dichiarata nell'interfaccia decide il trasporto del client: con `0.3.0` il
client sceglie il transport di compatibilità v0.3, che chiama `message/stream`,
mentre il server 1.x espone metodi in stile gRPC (`SendStreamingMessage`) — e
si ottiene `MethodNotFoundError: Method not found`. La card dichiara `1.0`.

## Test

```powershell
uv run pytest
```

Offline: verificano la carta d'identità (streaming dichiarato, versione non
legacy, url coerente) e il tool di lettura del corpus. La verifica dello
streaming vero richiede un modello e sta nei numeri qui sopra.
