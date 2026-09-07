# Laboratorio AG-UI — design

**Data:** 2026-09-07
**Stato:** approvato per la pianificazione
**Scopo:** ricostruire in locale, a fini di studio, un'interfaccia agentica equivalente a quella del "Laboratorio AG-UI" (Mind-X): chat a sinistra, piano di lavoro ed event inspector a destra, con agente multi-step e sottoagenti invocati in parallelo.

Il progetto è **esplorativo**: non ha scadenza né utenti finali. Si ottimizza per leggibilità, confini netti fra moduli e possibilità di spiegare ogni pezzo, non per velocità di consegna.

---

## 1. Cosa deve fare

Riprodurre questo flusso, osservato in una registrazione della piattaforma di riferimento:

1. L'utente chiede all'agente di interrogare due volte un agente di knowledge base su due temi diversi e di confrontare le risposte.
2. L'agente carica una skill pertinente, dichiara un piano a 3 step, e lo mostra in un pannello laterale.
3. Lancia le due interrogazioni **in parallelo** verso un sottoagente.
4. Alla chiusura delle due, produce una tabella comparativa e una sintesi finale.
5. Per tutta la durata, un event inspector mostra il flusso grezzo di eventi e un tab log ne dà la versione testuale con filtri.

Riferimento temporale della run originale: 80.69s, 11 tool, piano 3/3.

## 2. Decisioni di fondo

### 2.1 Protocolli

| Livello | Protocollo | Motivo |
|---|---|---|
| agente ↔ frontend | **AG-UI** su SSE | standard de facto; adottato da Google, Microsoft, AWS, Oracle |
| agente ↔ agente | **A2A** | Linux Foundation, confluito nella Agentic AI Foundation con MCP ad agosto 2026 |
| agente ↔ tool | **MCP** (opzionale, non nella v1) | complementare ad A2A, non alternativo |

### 2.2 Runtime

**Microsoft Agent Framework 1.17.0**, GA da aprile 2026, successore di Semantic Kernel e AutoGen (entrambi in maintenance mode).

Package usati:

- `agent-framework-core==1.17.0`
- `agent-framework-ag-ui` (≥1.2.2) — endpoint AG-UI e mapping eventi
- `agent-framework-a2a` — client e executor A2A
- `a2a-sdk[http-server]` + `sse-starlette`

**Non si scrive un mapper AG-UI a mano.** Il package Microsoft espone
`add_agent_framework_fastapi_endpoint(app, agent, "/")`, che copre endpoint SSE,
traduzione degli update in eventi AG-UI e protocollo interrupt/resume.

### 2.3 Modello

Un solo client OpenAI-compatible, due profili di configurazione:

| Profilo | `OPENAI_BASE_URL` | Note |
|---|---|---|
| OpenRouter (default) | `https://openrouter.ai/api/v1` | modello a scelta, tool-calling affidabile |
| LM Studio | `http://localhost:1234/v1` | api key fittizia; **richiede un modello con tool-calling reale** (Qwen3, Llama 3.3, Mistral recenti) |

MAF legge nativamente `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `OPENAI_CHAT_COMPLETION_MODEL`.

Su modelli locali piccoli il piano multi-step degrada. È un limite noto e accettato: LM Studio è il profilo alternativo, non il default.

### 2.4 Skill: formato standard

Le skill usano il formato **Agent Skills** (`SKILL.md` con frontmatter YAML, aperto come standard nel dicembre 2025 e adottato da OpenAI, Google, Cursor, JetBrains e altri entro marzo 2026), non un registry proprietario. Stesso costo implementativo, risultato portabile.

### 2.5 Frontend

**Next.js (App Router) + TypeScript + Tailwind**, con **client AG-UI custom** — un reader SSE più un reducer, senza CopilotKit.

Microsoft pubblica `agent-framework-devui`, ma la sua documentazione è esplicita: *"DevUI is a sample app… not intended for production use. For production… build your own custom interface and API server using the Agent Framework SDK."* Costruire la propria UI è la strada raccomandata. DevUI resta utile come riferimento visivo.

## 3. Architettura

```
Next.js  --POST /agui (RunAgentInput)-->  master agent (FastAPI + MAF)
         <--SSE eventi AG-UI-----------
                                           master --A2A message/stream--> knowledge agent
                                                  <--update incrementali--
```

```
C:\project\demo\                    cartella di lavoro, NON un repo

  demo-master-agent\                 REPO 1 -- agente principale
    Dockerfile
    src/demo/
      agents/master.py
      tools/          ui_tools.py, poi plan_tools.py, skill_tools.py, subagent_tools.py
      skills/         <nome>/SKILL.md
      chat_clients/   fake client per test e sviluppo offline
      server/app.py   endpoint AG-UI
      a2a/            client A2A (tappa 3): fetch card + factory
      config.py
    tests/

  demo-knowledge-agent\              REPO 2 -- sottoagente A2A (tappa 3)
    Dockerfile
    src/demo_kb/
      agent.py, executor.py, card.py
      server/app.py   mount A2A

  demo-frontend\                     REPO 3 -- interfaccia Next.js
    Dockerfile
    app/, components/{chat,plan,inspector,log}/
    lib/agui/         client SSE + reducer

  demo-infra\                        REPO 4 -- orchestrazione e documentazione
    compose.yaml
    .env.example
    README.md
    docs/specs, docs/plans, docs/prompts
```

**Un repo per unita' deployabile, piu' un repo infra.** E' la forma della
piattaforma di riferimento, dove ogni agente ha repo, immagine e pipeline
proprie, affiancati da repo di deployment e infrastruttura. Il costo e'
duplicazione fra i due repo agente; il beneficio, oltre alla fedelta', e' che
ogni agente si versiona e si rilascia da solo.

`compose.yaml` costruisce da percorsi fratelli (`../demo-master-agent`), quindi i
repo devono stare nella stessa cartella padre.

In sviluppo si gira nativi (`uv run`, `npm run dev`): i container servono a
verificare che tutto si alzi insieme, non a fare da ciclo di feedback.

**Un solo canale.** Chat, piano, inspector e log sono quattro riduzioni dello stesso stream SSE. Nessuna seconda API, nessun polling. È anche il motivo per cui l'inspector è didatticamente utile: mostra esattamente ciò che alimenta gli altri tre pannelli.

## 4. Contratti

### 4.1 Piano di lavoro — stato condiviso

I tool `todo_write` / `todo_set_status` non emettono testo: mutano lo stato condiviso AG-UI (`STATE_SNAPSHOT` iniziale, poi `STATE_DELTA`).

```jsonc
{
  "plan": {
    "status": "in_progress",        // idle | in_progress | completed | failed
    "steps": [
      {
        "id": 1,
        "title": "Interroga KnowledgeAgent sulle policy di sicurezza",
        "detail": "Richiede una prima risposta dalla knowledge base aziendale.",
        "source": "skill:knowledge-agent-request#1",
        "status": "pending",        // pending | in_progress | completed | failed
        "started_at": null,
        "ended_at": null
      }
    ]
  }
}
```

Il pannello "Piano di lavoro" è una funzione pura di questo oggetto. Nessuna logica di stato nel frontend.

### 4.2 Artefatti UI

Il tool `ui_table` restituisce un `state_update()` — helper di `agent_framework.ag_ui` che separa le tre destinazioni di un return di tool:

```python
return state_update(
    text="Ho prodotto il confronto in forma tabellare.",   # -> al modello
    tool_result={                                          # -> alla UI
        "component": "ui-table",
        "id": "art_1",
        "title": "Confronto tra le due risposte",
        "columns": ["Tema", "Policy sicurezza", "Lesson learned"],
        "rows": [["Copertura informativa", "…", "…"]],
    },
    state={"artifacts": [{"id": "art_1", "component": "ui-table"}]},  # -> stato condiviso
)
```

`component` è un'unione chiusa (`ui-table` in v1; `ui-chart`, `ui-image` a seguire). Il frontend ha un renderer per variante e un fallback esplicito sull'ignoto.

**Nessun sink di artefatti accumulati a fine run.** Gli artefatti sono emessi quando il tool li produce; è la condizione perché il pannello si popoli dal vivo.

**Non si usano Adaptive Cards.** Formato pesante e datato; un payload tipizzato reso in React è più semplice e più leggibile.

### 4.3 Sottoagenti

Eventi `SUBAGENT_STARTED` / `SUBAGENT_FINISHED` / `SUBAGENT_ERROR`, già presenti nel protocollo AG-UI. Due `SUBAGENT_STARTED` senza un `FINISHED` in mezzo rappresentano le due invocazioni parallele.

Il tool `call_agent_*` invoca il sottoagente via A2A in streaming e rilancia gli update nello stream del master mentre arrivano.

## 5. Vincoli verificati sperimentalmente

Uno spike eseguito il 2026-09-07 ha confermato che lo streaming A2A fra due agenti MAF funziona, e ha isolato **due default che lo disattivano in silenzio**. Entrambi sono obbligatori.

### 5.1 Server: lo streaming è opt-in

```python
A2AExecutor(agent=agent, stream=True)   # il default è stream=False
```

Senza `stream=True` il sottoagente accumula e restituisce un solo messaggio finale.

### 5.2 Client: la agent-card va fetchata a mano

`A2AAgent(url=...)` **non scarica la card**: costruisce una `minimal_agent_card(url, bindings)` con `capabilities.streaming = False` e degrada a non-streaming senza alcun warning.

```python
# SBAGLIATO — degrada a non-streaming senza dirlo
async with A2AAgent(name="kb", url=URL) as remote: ...

# CORRETTO
card = await fetch_agent_card(URL)   # GET {base}/.well-known/agent-card.json
async with A2AAgent(name="kb", agent_card=card) as remote: ...
```

Misure dello spike, stessa pipeline, sei chunk a 0.4s di distanza:

| Configurazione | Risultato |
|---|---|
| `A2AAgent(url=...)` | 1 update, payload `task` unico a +2.48s |
| `A2AAgent(agent_card=...)` | 6 update: +0.43 +0.83 +1.24 +1.64 +2.05 +2.45, spread 2.02s |

Due chiamate in parallelo: 2.50s contro ~4.90s in seriale — il parallelismo regge.

La agent-card del knowledge agent deve dichiarare `AgentCapabilities(streaming=True)`.

### 5.3 Modello dei contenuti cambiato in MAF 1.17

`TextContent`, `FunctionCallContent` e le altre classi per-variante **non esistono più**: sono unificate in `Content` con factory method.

```python
from agent_framework import Content
Content.from_text("ciao")
Content.from_function_call(...)
Content.from_text_reasoning(...)
```

Inoltre in 1.17 i package sono sotto namespace `agent_framework.*` (`agent_framework.openai`, `.a2a`, `.ag_ui`, `.anthropic`, `.ollama`), non più moduli top-level `agent_framework_openai` / `agent_framework_a2a`.

**Conseguenza pratica:** gli esempi che si trovano online scritti per MAF 1.0–1.9 non compilano. Ogni import va verificato contro 1.17.

## 6. Tappe

Ordinate per rischio decrescente, non per area funzionale. Le due cose che possono far buttare via lavoro — contratto FE↔BE e streaming A2A — si incontrano subito.

| # | Nome | Consegna | Stato |
|---|---|---|---|
| 0 | spike A2A streaming | risposta sì/no sullo streaming fra agenti MAF | **fatto**, vedi §5 |
| 1 | walking skeleton | prompt → LLM → `TEXT_MESSAGE_*` + un tool → UI a tre pannelli, piu' le immagini docker e il compose. Niente piano, niente skill, niente tabelle. | in corso |
| 2 | flusso del video | piano di lavoro, `SKILL.md` + `load_skill`, `ui_table`, filtri inspector, tab log | da fare |
| 3 | sottoagenti A2A | knowledge agent come processo separato, invocazione parallela, update rilanciati | da fare |

La tappa 1 esiste per validare che il frontend consumi correttamente ciò che il package AG-UI emette, quando cambiare idea costa poco.

## 7. Errori

- I fallimenti di run diventano `RUN_ERROR` sullo stream, non un 500: la chat resta viva e l'inspector mostra cosa è saltato.
- Un sottoagente che non risponde chiude il proprio step come `failed` senza abortire il piano.
- Ogni run ha un `run_id` presente su tutti i suoi eventi.

## 8. Test

**Backend.** Un fake chat client che emette chunk deterministici — nessuna chiamata LLM nei test. Copertura su: tool del piano (transizioni di stato), `ui_table` (forma del payload), sequenza di eventi di una run completa (`RUN_STARTED` … terminale). Un test di integrazione A2A che asserisce l'arrivo **incrementale** degli update, così le due trappole di §5 non possono tornare inosservate.

**Frontend.** Test del reducer su fixture di stream registrati. Il reducer rifiuta uno stream malformato invece di degradare in silenzio.

## 9. Fuori scopo (v1)

- Autenticazione: niente MSAL, niente OBO. Il codice lascia un punto d'innesto, la demo gira senza.
- Persistenza delle conversazioni.
- MCP: il pattern è supportato dal framework ma non serve al flusso del video.
- Deploy remoto: niente pipeline CI, niente chart helm, niente registry. `compose.yaml` alza tutto in locale e si ferma li'.

## 10. Note sulla piattaforma di riferimento

Osservazioni raccolte guardando il codice esistente, utili come contrasto. Non sono richieste di modifica a quel codice.

- Il template attuale usa `agent-framework-core==1.5.0`, dodici release indietro rispetto a 1.17.0.
- La `agent.json` dichiara `"streaming": false`. Alla luce di §5, vale la pena verificare se sia una scelta o l'effetto dei due default: nella registrazione si vedono ~26 secondi di interfaccia ferma in attesa dei sottoagenti, compatibili con un salto A2A non-streaming.
- Gli artefatti UI sono accumulati in un sink (`rc.cards`) e restituiti a fine run. È il pattern che impedisce lo streaming degli artefatti.
- Il server MCP di riferimento pinna `fastmcp==2.10.6`. Dalla 2.11.0 FastMCP inietta la chiave `_fastmcp` in `_meta`, che viola il formato delle chiavi dello spec MCP 2025-06-18 (devono iniziare con `[A-Za-z0-9]`) e fa fallire i client conformi. FastMCP 4.0 l'ha rinominata `fastmcp`. Se la demo esporrà un MCP, userà la 4.x.
