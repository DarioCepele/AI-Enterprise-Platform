# Master agent del laboratorio AG-UI

Backend Python con Microsoft Agent Framework e FastAPI. Espone il master agent su `POST /agui` tramite `add_agent_framework_fastapi_endpoint`: lo stream SSE contiene gli eventi AG-UI per chat, piano e inspector. I log operativi usano un secondo canale HTTP, `GET /logs`. `GET /health` restituisce lo stato del servizio.

## Avvio e configurazione

Servono Python 3.12 e `uv`. Dalla radice di questo repository:

```powershell
uv sync --locked
uv run python -m demo
```

Il server locale ascolta su `http://127.0.0.1:8000`. Per avviare anche il frontend con Docker Compose, seguire il [README di demo-infra](../demo-infra/README.md).

La configurazione viene letta dall'ambiente; in locale viene caricato anche il file `.env`, non versionato. Le variabili sono:

| Variabile | Uso |
| --- | --- |
| `OPENAI_BASE_URL` | Endpoint del provider compatibile con Chat Completions. |
| `OPENAI_API_KEY` | Credenziale del provider. |
| `OPENAI_CHAT_COMPLETION_MODEL` | Identificativo del modello. |
| `DEMO_FAKE_CLIENT` | Attiva il client deterministico per verifiche locali senza LLM. |
| `DEMO_ALLOWED_ORIGINS` | Origini CORS del frontend, separate da virgole. |

Il flusso del piano richiede un modello che esegua davvero le chiamate ai tool. Il client finto serve ai test del protocollo, non riproduce l'intero flusso del modello reale.

## Gruppi di tool

| Gruppo | Tool | Contratto |
| --- | --- | --- |
| Piano | `todo_write`, `todo_set_status` | Scrivono e aggiornano `state.plan`. |
| Skill | `load_skill` | Restituisce al modello le istruzioni Markdown della skill richiesta. |
| Artefatti UI | `ui_table` | Produce il payload della tabella e aggiorna `state.artifacts`. |
| Memoria | `cerca_nei_ricordi` | Cerca per significato nelle conversazioni passate. Esiste solo se il servizio di memoria e' configurato. |

`todo_write(steps)` sostituisce il piano precedente. Ogni passo ha un `id` intero, `title`, `detail` e `source`; parte da `pending`. `todo_set_status(step_id, status, note)` accetta `pending`, `in_progress`, `completed` e `failed`. Per `failed` la nota deve essere non vuota. Gli aggiornamenti includono i tempi di inizio e fine e riemettono il piano intero. L'agente deve aggiornare i passi mentre lavora, per rendere visibile l'avanzamento.

Il `PlanStore` vive in memoria nell'istanza dell'agente. L'app costruisce un solo agente: **un piano per processo, condiviso anche fra due schede del browser**. Non esiste isolamento per thread e il piano non persiste al riavvio. Questa è una limitazione dichiarata della demo.

`ui_table(title, columns, rows)` restituisce un artefatto `ui-table` con un `id` numerato per processo, usato per collegare il risultato del tool al riepilogo nello stato. L'id non è stabile fra riavvii. Il risultato AG-UI contiene una stringa JSON con titolo, colonne e righe; `state.artifacts` contiene il riepilogo della tabella corrente. `plan` e `artifacts` sono chiavi separate: gli aggiornamenti sostituiscono le chiavi di primo livello, quindi ogni gruppo scrive solo la propria.

## Aggiungere una skill

Le skill sono cartelle sotto `src/demo/skills/`, ciascuna con un file `SKILL.md`. Esempio:

```markdown
---
name: comparison
description: Confronta elementi lungo dimensioni comuni e produce una tabella.
---

# Confronto strutturato

Individua le dimensioni del confronto, chiama ui_table e sintetizza le differenze.
```

Il frontmatter deve iniziare alla prima riga ed essere delimitato da `---`; `name` e `description` sono obbligatori. Il parser attuale supporta solo righe scalari `chiave: valore`, non YAML annidato o multilinea. Il corpo successivo è Markdown.

Per aggiungere una skill, creare `src/demo/skills/<nome>/SKILL.md` con un nome univoco e riavviare l'agente; se si usa Docker, ricostruire l'immagine. Non serve registrarla nel codice: il catalogo viene letto alla costruzione dei tool e incluso nella descrizione di `load_skill`. Un file malformato interrompe la costruzione del catalogo. Una richiesta a `load_skill` con nome sconosciuto restituisce invece un messaggio con i nomi disponibili, senza interrompere la run. Le skill sono incluse anche nel pacchetto Python.

## Log operativi

`GET /logs?cursor=0` legge le righe disponibili. Il client passa poi il `cursor` ricevuto per ottenere solo righe con `seq` maggiore:

```json
{
  "entries": [
    {
      "seq": 1,
      "ts": "2026-09-08T10:00:00.000+00:00",
      "level": "INFO",
      "source": "tools.plan_tools",
      "message": "Piano scritto: 3 passi."
    }
  ],
  "cursor": 1,
  "dropped": 0
}
```

Il buffer circolare conserva le ultime **500 righe** in memoria per processo. `cursor` è la sequenza dell'ultima riga restituita; senza nuove righe resta invariato. `dropped` conta le righe successive al cursore richiesto già uscite dal buffer. I cursori ripartono al riavvio del processo e non identificano una singola run.

Il collettore riceve i log da `demo` e dai suoi discendenti `demo.*`, da livello `INFO` in su; `source` omette il prefisso `demo.`. Registra scrittura e avanzamento del piano, caricamento delle skill e produzione delle tabelle. I logger delle librerie sono esclusi perché possono contenere URL e header con credenziali. Non è una redazione automatica dei messaggi applicativi: chi aggiunge log a `demo.*` deve evitare credenziali e dati sensibili. L'handler viene rimosso allo shutdown dell'app.

Nell'integrazione AG-UI adottata, gli eventi `CUSTOM` sono riservati al framework e non c'è una factory applicativa per emetterli arbitrariamente. Per questo il tab LOG legge `/logs`: il principio delle tre viste dello stesso stream vale per chat, piano e inspector, mentre i log hanno un canale separato.

## Test offline

```powershell
uv run pytest
```

I test usano client finti e dipendenze esplicite: non fanno chiamate a un LLM reale e non richiedono un `.env` o credenziali del provider. Coprono tool, skill, stato condiviso, protocollo AG-UI, raccolta dei log, cursori e CORS. Le verifiche con un modello reale restano separate dai test automatici.

## Memoria della conversazione

La storia del thread la possiede il server. Il client AG-UI manda solo il turno
nuovo; l'adattatore ricompone la conversazione dallo snapshot del thread e la
passa al modello. Senza questo, ogni run ripartiva da zero: stesso `threadId`,
seconda domanda, e il modello rispondeva "non me l'hai ancora chiesto".

Lo store e' `InMemoryAGUIThreadSnapshotStore`: un solo snapshot per
`(scope, thread_id)`, in memoria di processo, niente durata oltre il riavvio.
In produzione si sostituisce con uno store durevole senza toccare l'agente --
la firma da implementare e' il protocollo `AGUIThreadSnapshotStore`
(`save`, `get`, `delete`, `clear`).

**Lo scope e' un confine di autorizzazione, non un identificativo.** Il
framework rifiuta uno snapshot store senza `snapshot_scope_resolver`, e la
ragione e' che un thread id identifica un thread ma non autorizza a leggerlo.
Qui il laboratorio gira senza autenticazione e lo scope e' dichiarato uno solo
per tutto il processo (`SINGLE_TENANT_SCOPE` in `server/app.py`). In produzione
quella funzione restituisce l'identita' verificata della richiesta, presa da una
dependency di autenticazione sull'endpoint, mai da un header scelto dal client.

Conseguenza da tenere d'occhio: la storia ora cresce a ogni turno e nessuno la
pota. E' il prossimo passo -- tetto di contesto, compattazione dei tool result
e riassunto dei turni vecchi.

## Telemetria del contesto

Un middleware di chat registra la dimensione del contesto **a ogni chiamata al
modello**, non a ogni run: e' dentro la singola run, fra un tool e l'altro, che
il contesto si gonfia. Le righe finiscono su `demo.telemetry`, quindi nel tab
LOG del frontend:

```
Contesto: 1 messaggi, 58 caratteri (0 dai tool); 1167 token in, 1788 out.
Contesto: 3 messaggi, 1252 caratteri (659 dai tool); 1554 token in, 100 out.
Contesto: 9 messaggi, 2640 caratteri (801 dai tool); 2181 token in, 123 out.
```

I caratteri sono un proxy grossolano dei token, disponibile anche quando il
provider non riporta l'uso; la quota "dai tool" e' contata a parte perche' e' la
prima da svuotare quando serve fare spazio. Le righe contengono **solo
conteggi**: la conversazione non e' materiale da diagnostica, e questi log sono
leggibili dal frontend.

I client finti dei test ereditano da `ChatMiddlewareLayer` come il client
OpenAI vero. Senza quel livello il middleware non verrebbe eseguito nei test, e
la telemetria risulterebbe verde in laboratorio e assente in produzione.

## Dove vive la memoria dei thread

Con `DEMO_MEMORY_SERVICE_URL` impostata, gli snapshot dei thread stanno nel
[servizio di memoria](../demo-memory-service/README.md): la conversazione
sopravvive al riavvio dell'agente. Senza quella variabile si torna allo store
in memoria di processo, e il laboratorio resta avviabile senza Mongo e Redis.
Quale dei due sia attivo si legge nel tab LOG all'avvio — la differenza si
noterebbe altrimenti solo quando è troppo tardi.

Il contesto che torna dalla memoria e' **potato** dal servizio: fuori il
ragionamento dei turni passati, svuotati i risultati di tool piu' vecchi. Il
conto di cio' che manca arriva insieme allo snapshot e finisce nel tab LOG
(`Contesto dalla memoria: 28 messaggi (12 ragionamenti tolti, ...)`), perche'
una potatura silenziosa e' indistinguibile da una perdita di memoria.

L'agente non conosce Mongo né Redis: implementa il protocollo
`AGUIThreadSnapshotStore` chiamando il servizio in HTTP, e lo scope del
resolver diventa l'header `X-Memory-Scope` della chiamata.

**Politica di guasto, dichiarata perché non è ovvia.** Un servizio di memoria
irraggiungibile non fa fallire la conversazione: in lettura si degrada a
«thread sconosciuto» e la run riparte senza storia, in scrittura si registra
l'errore — sollevare a run conclusa romperebbe una risposta già consegnata. In
entrambi i casi la riga finisce su `demo.*`, quindi sotto gli occhi nel tab
LOG: un'amnesia silenziosa è il difetto peggiore che questo pezzo possa avere.

## Cercare nei ricordi

Con il servizio di memoria configurato, l'agente ha un quarto gruppo di tool:
`cerca_nei_ricordi(domanda)` interroga `POST /search` del servizio e riceve i
frammenti di conversazioni passate più vicini per significato.

È un tool e non un'iniezione automatica nel contesto: infilare a ogni run i
ricordi «probabilmente pertinenti» li paga sempre e li azzecca a volte, mentre
più roba c'è nel contesto meno il modello ne recupera con precisione. Così la
memoria si raggiunge quando serve, e a decidere se serve è il modello, che la
domanda ce l'ha davanti.

Senza `DEMO_MEMORY_SERVICE_URL` il tool **non esiste**, invece di esistere e
fallire: un tool che risponde sempre «non raggiungibile» insegna al modello a
non chiamarlo più. Quando la memoria è configurata ma irraggiungibile, l'errore
torna al modello come testo e la run continua.

Verificato dal vivo: informazione detta in una conversazione, poi in un thread
nuovo la domanda «quale alternativa avevamo scartato per il deploy?» → l'agente
chiama `cerca_nei_ricordi` e risponde «ECS», che nel contesto non c'era.

## Interrogare il sottoagente

Con `DEMO_KNOWLEDGE_AGENT_URL` impostata, l'agente ha `interroga_knowledge`:
gira una domanda al [knowledge agent](../demo-knowledge-agent/README.md) via
**A2A**, non come tool locale ma come agente remoto.

La card viene scaricata da `/.well-known/agent-card.json` e passata come
`agent_card`: con il solo `url` il client A2A degrada a non-streaming senza
dirlo. La card si scarica **una volta** e si riusa; se non dichiara `streaming`,
il tool lo scrive nei log invece di far finta di niente.

Le istruzioni chiedono al modello di fare le due interrogazioni **nello stesso
turno**: MAF esegue le tool call di un turno con `asyncio.gather`, quindi
partono insieme. Misurato su una run vera: 158 aggiornamenti in 11,67 s e 166
in 14,25 s, terminate a 0,9 s di distanza — in serie sarebbero stati ~26 s.

Sottoagente irraggiungibile: l'errore torna al modello come testo, che risponde
con quello che sa dichiarando che quella parte non è verificata. La run non
muore per un sottoagente giù.
