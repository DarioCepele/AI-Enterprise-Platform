# Laboratorio AG-UI — design

**Data:** 2026-09-07
**Stato:** tappe 1-3 completate e verificate nel browser
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
- `agent-framework-a2a` — client e executor A2A. Ferma alla linea beta
  (`1.0.0b260821`) ma dichiara `agent-framework-core>=1.15,<2`: gira con 1.17.
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

Profili verificati nelle prove del laboratorio: `qwen/qwen3.8-27b` su OpenRouter
e `google/gemma-4-12b` su LM Studio. Gemma 4 12B ha chiamato `ui_table` con
`TOOL_CALL_START/ARGS/END/RESULT` e `STATE_SNAPSHOT` completi: non c'e' un divieto
generale di tool-calling per quel profilo. Il piano richiede comunque chiamate
reali ai tool; la sola descrizione testuale dei passi non modifica lo stato.

### 2.4 Skill: formato standard

Le skill usano il formato **Agent Skills** (`SKILL.md` con frontmatter YAML, aperto come standard nel dicembre 2025 e adottato da OpenAI, Google, Cursor, JetBrains e altri entro marzo 2026), non un registry proprietario. Stesso costo implementativo, risultato portabile.

### 2.5 Frontend

**Next.js (App Router) + TypeScript + Tailwind**, con **client AG-UI custom** — un reader SSE più un reducer, senza CopilotKit.

Microsoft pubblica `agent-framework-devui`, ma la sua documentazione è esplicita: *"DevUI is a sample app… not intended for production use. For production… build your own custom interface and API server using the Agent Framework SDK."* Costruire la propria UI è la strada raccomandata. DevUI resta utile come riferimento visivo.

## 3. Architettura

```
Next.js  --POST /agui (RunAgentInput)-->  master agent (FastAPI + MAF)
         <--SSE eventi AG-UI-----------      |        |
                                             |        +--A2A message/stream--> knowledge agent
                                             |        <--update incrementali--
                                             +--HTTP--> servizio di memoria --> MongoDB (durevole)
                                                                            --> Redis (coda calda, ricordi)
```

```
C:\project\demo\                    cartella di lavoro, NON un repo

  demo-master-agent\                 REPO 1 -- agente principale
    src/demo/
      agents/master.py
      tools/          ui_tools, plan_tools, skill_tools, memory_tools, subagent_tools
      skills/         <nome>/SKILL.md
      chat_clients/   fake client per test e sviluppo offline
      memory/         snapshot store che parla col servizio di memoria
      server/         endpoint AG-UI e relay degli eventi dei sottoagenti
      telemetry.py    dimensione del contesto a ogni chiamata al modello

  demo-knowledge-agent\              REPO 2 -- sottoagente A2A
    src/knowledge/
      agent.py        agente MAF con il corpus locale
      corpus/         i documenti della knowledge base
      server.py       card A2A e mount delle rotte

  demo-frontend\                     REPO 3 -- interfaccia Next.js
    app/, components/, lib/agui/     client SSE + reducer

  demo-memory-service\               REPO 4 -- memoria conversazionale
    src/memory_service/
      stores/         mongo (transcript), hot (coda calda), vectors (ricordi)
      curation.py     cosa torna nel contesto
      summarizer.py   riassunti e fatti duraturi

  demo-infra\                        REPO 5 -- orchestrazione e documentazione
    compose.yaml      6 servizi: master-agent, knowledge-agent, memory-service,
                      frontend, mongo, redis
    docs/specs, docs/plans, docs/prompts
```

**Un repo per unita' deployabile, piu' un repo infra.** E' la forma della
piattaforma di riferimento, dove ogni agente ha repo, immagine e pipeline
proprie, affiancati da repo di deployment e infrastruttura. Il costo e'
duplicazione fra i repo agente; il beneficio, oltre alla fedelta', e' che ogni
agente si versiona e si rilascia da solo.

`compose.yaml` costruisce da percorsi fratelli (`../demo-master-agent`), quindi i
repo devono stare nella stessa cartella padre.

In sviluppo si gira nativi (`uv run`, `npm run dev`): i container servono a
verificare che tutto si alzi insieme, non a fare da ciclo di feedback.

**Tre viste di uno stream, piu' un canale log.** Chat, piano e inspector derivano
dallo stream SSE AG-UI. Il tab LOG usa una seconda API HTTP a cursore, descritta
in §4.4; non e' una riduzione dello stream AG-UI.

## 4. Contratti

### 4.1 Piano di lavoro — stato condiviso

I tool `todo_write` / `todo_set_status` aggiornano `shared.plan` tramite
`state_update`: l'adattatore emette `STATE_SNAPSHOT`, anche per gli aggiornamenti.
I risultati `component: "plan"` non diventano artefatti nella timeline.

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
        "ended_at": null,
        "note": null                 // obbligatoria e non vuota per failed
      }
    ]
  }
}
```

Il pannello "Piano di lavoro" è una funzione pura di questo oggetto. Nessuna logica di stato nel frontend.
Il `PlanStore` appartiene all'agente singleton del processo: due schede condividono
il piano. `plan` e `artifacts` sono chiavi separate perche' `state_update` sostituisce
le chiavi di primo livello, senza fusione profonda.

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

Il master invoca il knowledge agent con il tool `interroga_knowledge`, che parla
**A2A** in streaming. Due interrogazioni nello stesso turno partono insieme:
MAF esegue le tool call di un turno con `asyncio.gather`, ognuna in un contesto
copiato.

Il flusso emette `SUBAGENT_STARTED` / `SUBAGENT_FINISHED` / `SUBAGENT_ERROR`.
Due `SUBAGENT_STARTED` senza un `FINISHED` in mezzo sono le due invocazioni
parallele — ed è così che il frontend mostra il parallelismo mentre accade,
invece di dedurlo alla fine.

**Correzione rispetto alla prima stesura.** Questi eventi esistono nel
protocollo (`ag_ui.core.events`) ma **l'adattatore non li emette**: zero
occorrenze di `SUBAGENT` in `agent_framework_ag_ui`. Chi si limita a montare
l'endpoint non li vedrà mai. Si iniettano estendendo `AgentFrameworkAgent`, il
cui `run()` è un async generator di eventi: il generator del framework gira in
un task che pubblica su una coda, e la stessa coda raccoglie gli eventi che i
tool emettono. La coda viaggia in una `ContextVar` e raggiunge i tool proprio
grazie al `contextvars.copy_context()` che rende parallele le chiamate.

Conseguenza da non perdere di vista: questo è anche il punto in cui si potrebbe
emettere qualunque altro evento applicativo sullo stream, `CUSTOM` compresi. Il
canale separato dei log (§4.4) resta perché funziona ed è già documentato, non
perché non ci fosse alternativa.

### 4.4 Log operativi

Nell'adattatore MAF AG-UI in uso gli eventi `CUSTOM` sono riservati al framework;
il codice applicativo non dispone di un'emissione custom per i propri log.
I log viaggiano quindi su `GET /logs?cursor=<int>`, secondo canale del sistema.
Il principio delle tre viste di uno stream vale per chat, piano e inspector,
non per il tab LOG.

Il buffer circolare contiene al massimo 500 righe. La risposta contiene `entries`,
`cursor` e `dropped`: le righe hanno sequenza `seq` maggiore del cursore richiesto,
orario `ts`, `level`, `source` e `message`. `dropped` conta le righe non piu'
disponibili rispetto a quel cursore; una pagina vuota conserva il cursore.

Il collettore e' agganciato al logger `demo` e raccoglie la famiglia `demo.*`,
escludendo i logger delle librerie: i loro diagnostici possono contenere la chiave
API. Non e' un redattore di segreti: il codice applicativo deve evitare di loggarli.
Il controllo operativo cerca `sk-` nella risposta senza stampare i payload.

Il frontend interroga l'endpoint durante la run e una volta alla fine, conserva
il cursore tra run e tra cambi di tab, annulla richieste obsolete e non effettua
polling a riposo. Piano e log sono condivisi nel processo, non isolati per thread.

### 4.5 Resa della timeline e dell'inspector

**Inspector: una riga per gruppo, non per evento.** Gli eventi consecutivi dello
stesso tipo si accorpano in una riga sola con il conteggio; il payload viene
serializzato solo quando la riga e' aperta, e l'array e' troncato a 50 elementi
per gruppo. Motivo misurato: una run reale produce oltre 2500 eventi, di cui
piu' di 2200 `REASONING_ENCRYPTED_VALUE`. Una riga per evento significa 2500
`<details>` nel DOM e altrettante `JSON.stringify` a ogni token che arriva.
Il conteggio totale in cima resta quello degli eventi, non dei gruppi: non si
nasconde nulla del flusso grezzo, si smette solo di ripeterlo.

**Chat: Markdown, non testo grezzo.** La risposta finale del modello e' Markdown
e arriva un token alla volta. Si rende con **Streamdown**, un renderer pensato
per lo streaming: completa da solo la sintassi ancora aperta — grassetto, link,
blocchi di codice a meta' — invece di mostrare gli asterischi finche' il token
di chiusura non arriva. Il markup pericoloso resta bloccato: niente `<script>`,
niente URL `javascript:`, immagini remote non caricate. I nomi di colore che le
sue classi si aspettano (`muted`, `primary`, `border`) sono mappati sui token del
laboratorio, cosi' il Markdown non porta una seconda tavolozza.

**Run interrompibile.** `runAgent` accetta un `AbortSignal` e la chat mostra
"interrompi" mentre lavora. Interrompere e' una scelta dell'utente, non un
errore: la run si chiude con quello che ha gia' prodotto e nessun riquadro rosso.

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

### 5.4 Ragionamento osservato nello stream Qwen

Nel flusso registrato con `qwen/qwen3.8-27b`, `REASONING_MESSAGE_CONTENT` non e'
emesso. La sequenza e' `REASONING_START`, `REASONING_MESSAGE_START`, i delta
`REASONING_ENCRYPTED_VALUE`, `REASONING_MESSAGE_END`, `REASONING_END`.
`encryptedValue` contiene una stringa JSON non cifrata di frammenti
`{"type":"reasoning.text","text":"..."}`. Nei delta l'id e' `entityId`;
i delimitatori usano `messageId`. Il reducer aggrega i frammenti per messaggio,
ignora le firme e rifiuta JSON malformato o payload non-array. Questa e' la forma
misurata per quel profilo, non una garanzia per qualsiasi provider.

### 5.5 I due default dello streaming A2A, rimisurati

Lo spike di §5.1 e §5.2 è stato rifatto contro il knowledge agent vero, con
`qwen/qwen3.8-27b`:

| Configurazione | Risultato |
|---|---|
| `A2AAgent(url=...)` | **1 update**, tutto insieme a +8,62 s |
| `A2AAgent(agent_card=card)` | **62 update**, il primo a +2,77 s, totale 4,16 s |

Due interrogazioni insieme: **6,97 s** contro **13,77 s** in serie.

Su una run completa attraverso il master agent, i numeri reggono anche in
mezzo al resto del lavoro: 158 e 166 aggiornamenti, terminate a 0,9 s di
distanza dove in serie sarebbero stati ~26 s.

### 5.6 In `a2a-sdk` 1.x i tipi sono protobuf, e la versione sceglie il trasporto

Trappola non prevista dalla prima stesura, costata un `MethodNotFoundError`
senza spiegazione apparente.

`AgentCard`, `AgentSkill` e `AgentCapabilities` non sono più modelli pydantic ma
messaggi **protobuf** (`a2a_pb2`): niente `model_fields`, niente costruzione dai
dizionari senza `ParseDict`. La card non ha più `url` e `preferred_transport` ma
una lista `supported_interfaces`.

Il campo che decide tutto è `protocol_version` dentro l'interfaccia:

- `0.3.0` → il client sceglie il transport di **compatibilità v0.3**, che chiama
  `message/stream`;
- il server 1.x espone metodi in stile gRPC (`SendStreamingMessage`);
- risultato: `MethodNotFoundError: Method not found`, senza che nulla indichi
  che il problema è una versione dichiarata nella card.

La card del laboratorio dichiara `1.0`.

### 5.7 L'adattatore AG-UI non emette gli eventi dei sottoagenti

`SUBAGENT_STARTED` / `SUBAGENT_FINISHED` / `SUBAGENT_ERROR` esistono in
`ag_ui.core.events`, ma in `agent_framework_ag_ui` non compaiono mai: chi monta
l'endpoint e si aspetta di vederli sullo stream non li vedrà. Vanno iniettati
estendendo `AgentFrameworkAgent` — vedi §4.3 per il come e per la conseguenza.

## 6. Tappe

Ordinate per rischio decrescente, non per area funzionale. Le due cose che possono far buttare via lavoro — contratto FE↔BE e streaming A2A — si incontrano subito.

| # | Nome | Consegna | Stato |
|---|---|---|---|
| 0 | spike A2A streaming | risposta sì/no sullo streaming fra agenti MAF | **fatto**, vedi §5 |
| 1 | walking skeleton | prompt → LLM → `TEXT_MESSAGE_*` + un tool → UI a tre pannelli, piu' le immagini docker e il compose. Niente piano, niente skill, niente tabelle. | **fatto** |
| 2 | flusso del video | piano di lavoro, `SKILL.md` + `load_skill`, `ui_table`, filtri inspector, tab log | **fatto**, verificato nel browser il 2026-09-08 |
| 3 | sottoagenti A2A | knowledge agent come processo separato, invocazione parallela, update rilanciati | **fatto**, verificato nel browser il 2026-09-08 |

La tappa 1 esiste per validare che il frontend consumi correttamente ciò che il package AG-UI emette, quando cambiare idea costa poco.

## 7. Errori

- I fallimenti di run diventano `RUN_ERROR` sullo stream, non un 500: la chat resta viva e l'inspector mostra cosa è saltato.
- Un sottoagente che non risponde chiude il proprio step come `failed` senza abortire il piano.
- Ogni run ha un `run_id` presente su tutti i suoi eventi.

## 8. Test

**Backend.** Un fake chat client che emette chunk deterministici — nessuna chiamata LLM nei test. Copertura su: tool del piano (transizioni di stato), `ui_table` (forma del payload), sequenza di eventi di una run completa (`RUN_STARTED` … terminale). Un test di integrazione A2A che asserisce l'arrivo **incrementale** degli update, così le due trappole di §5 non possono tornare inosservate.

**Frontend.** Test del reducer su fixture di stream registrati e test DOM dei
componenti e dell'integrazione. Il parser del ragionamento rifiuta JSON malformato
o payload non-array. Un tool result non JSON e' invece testo valido per il modello:
`parseArtifact` restituisce `null`; i risultati `component: "plan"` sono esclusi
dalla timeline e le varianti UI sconosciute o malformate hanno un fallback visibile.

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

## 11. Task futuri

Cose emerse costruendo, non nel piano iniziale. Nessuna blocca l'uso del
laboratorio; ognuna dice perché varrebbe la pena.

**Registry degli agenti con ricerca semantica.** Oggi il master conosce un solo
sottoagente, per URL. La documentazione A2A descrive proprio il pattern
alternativo: un registry che tiene una collezione di agent card, interrogabile
per skill, tag e capability — e le nostre card hanno già `skills` con
descrizione e tag, cioè materiale da embedding. La casa naturale è il servizio
di memoria, che ha già embedder e indice vettoriale.

Due vincoli da rispettare quando si farà:

- **la scoperta ordina, non autorizza.** Il registry deve essere una lista
  chiusa di URL curati; la somiglianza serve a ordinare i candidati, mai ad
  ammetterne di nuovi. Altrimenti un testo che entra nel contesto può sterzare
  quale agente viene chiamato. Non a caso la spec A2A dice che un registry
  restituisce card diverse secondo l'identità del client.
- **serve più di un agente.** Con un candidato la scelta non esiste, e fra
  agenti quasi identici la scoperta semantica rende il routing meno prevedibile,
  non più: se un ingegnere umano non sa dire quale agente usare, il modello
  nemmeno.

**Decadimento dei fatti duraturi.** Un fatto vecchio e mai più confermato pesa
quanto uno di ieri. La pratica consigliata è abbassare una forza nel tempo
invece di cancellare, così i pattern lunghi restano e la frequenza di recupero
scende.

**Reindicizzazione dei ricordi.** L'indice vettoriale è ricostruibile dai
transcript per costruzione, ma non c'è ancora un comando che lo faccia.

**Update dei sottoagenti rilanciati nello stream.** Oggi il tool consuma lo
stream A2A e restituisce la risposta completa; gli aggiornamenti intermedi si
contano nei log ma non arrivano al frontend. Rilanciarli darebbe la stessa
progressione che si vede per il testo del master.
