# Tappa 5 — Templatizzazione — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Portare il laboratorio da "funziona sulla mia macchina, in italiano, con una replica" a **repo di riferimento da forkare**: un progetto nuovo si ottiene clonando, cambiando un file di configurazione e scrivendo i propri agenti — e quello che ne esce regge due repliche dietro un ingress, non una sola.

**Architecture:** Restano i quattro repo fratelli. Cambia cosa ciascuno assume: il master agent smette di conoscere un solo scope e un solo sottoagente per costante, il servizio di memoria smette di tenere in RAM cose che due repliche non condividerebbero, il frontend smette di cuocere il proprio URL dentro l'immagine, e `demo-infra` guadagna i manifest Kubernetes accanto al compose — che resta il percorso di sviluppo. Il knowledge agent resta nel template come **esempio** di sottoagente A2A, dichiarato tale.

**Tech Stack:** invariato — Python 3.12, MAF 1.17.0, `agent-framework-ag-ui` 1.2.2, `a2a-sdk` 1.1.2, FastAPI, uv, pytest; Next.js 16 + TypeScript + Tailwind 4, vitest; Mongo 8, Redis 8. Si aggiungono `pydantic-settings` lato master agent, OpenTelemetry SDK sui tre servizi Python, Helm (o kustomize) in `demo-infra`.

**Spec:** `docs/specs/2026-09-07-agui-lab-design.md`

## Decisioni prese prima di scrivere il piano

| Domanda | Scelta | Conseguenza sul piano |
|---|---|---|
| Forma del template | **repo di riferimento da forkare** | niente livello di templating nei file; il costo si sposta su *una* superficie di configurazione e su un README che dice cosa toccare (Blocco A) |
| Bersaglio di deploy | **compose + manifest Kubernetes** | lo stato di processo residuo diventa un bug vero, non una nota: due repliche lo mostrano (Blocco B) |
| Lingua del prodotto | **inglese, con la lingua come parametro** | copy e istruzioni in inglese, `PRODUCT_LANGUAGE` in configurazione (Blocco A) |

**Fuori scopo, per scelta:** l'autenticazione. Il piano non la implementa ma **lascia le giunture aperte** dove servono — lo scope risolto per richiesta invece che per costante, gli `security_schemes` gia' dichiarati nella card, il token di servizio isolato dietro un'interfaccia. Chi aggiunge OIDC dopo non deve riaprire questi file.

## Global Constraints

- Ogni task committa nel proprio repo. Non esiste un repo che li contiene tutti.
- I commenti nel codice restano fuori: il "perche'" va nei messaggi di commit e nei README. Il codice si scrive perche' si spieghi da solo.
- Codice, log, prompt e chiavi sul filo in inglese. Il testo che l'utente legge e' inglese e parametrizzato per lingua.
- Nessuna chiamata LLM reale nei test.
- Ogni task che tocca un contratto fra repo (stato AG-UI, artefatto A2A, API della memoria) aggiorna **le due parti nello stesso task**, piu' la fixture condivisa del Blocco F.
- Le modifiche allo schema dei dati passano dal runner di migrazioni del Blocco D. La rinomina `chiave`/`valore` -> `key`/`value` e' stata fatta a mano l'8 settembre 2026 con uno script usa-e-getta: e' il precedente che giustifica il runner.
- Nessun `latest` nelle immagini, nessun container che gira come root.

---

## Blocco A — La superficie che si tocca quando si forka

Oggi chi forka deve cercare le stringhe. `SINGLE_TENANT_SCOPE = "local-laboratory"` sta in `config.py`, il titolo sta in `LabHeader.tsx`, la lingua delle risposte sta dentro il prompt del master, il nome del sottoagente sta dentro `subagent_tools.py`. Sono cinque posti per un'informazione sola.

### Task A1 — Un modulo di configurazione unico per il master agent

**Files:** `demo-master-agent/src/demo/config.py`, `demo-master-agent/tests/test_config.py`, `.env.example`

**Perche':** il servizio di memoria usa gia' `pydantic-settings` e fallisce all'avvio se manca qualcosa; il master agent usa `os.getenv` con default silenziosi, quindi un URL sbagliato si scopre al primo turno invece che al boot.

- [ ] **Step 1:** test che una configurazione senza `OPENAI_API_KEY` (e senza fake client) fallisca all'avvio con un messaggio che nomina la variabile.
- [ ] **Step 2:** test che `PRODUCT_NAME`, `PRODUCT_LANGUAGE` e `DEFAULT_SCOPE` abbiano default e siano leggibili da una sola struttura.
- [ ] **Step 3:** portare `Settings` su `pydantic-settings`, mantenendo `get_settings()` senza cache (i test cambiano l'ambiente).
- [ ] **Step 4:** aggiornare `.env.example` con le nuove variabili e una riga di commento per ciascuna.

**Fatto quando:** avviare il master senza una variabile obbligatoria produce un errore che dice quale, e nessun modulo legge piu' `os.getenv` direttamente.

### Task A2 — Il prodotto si chiama come dice la configurazione

**Files:** `demo-master-agent/src/demo/agents/master.py`, `demo-frontend/lib/config.ts` (nuovo), `demo-frontend/components/LabHeader.tsx`, `Chat.tsx`, `Lab.tsx`, `app/layout.tsx`

**Perche':** "Laboratorio AG-UI", "Studio 02", "Esercizio di laboratorio" sono il nome di *questo* progetto dentro il codice di *qualsiasi* progetto.

- [ ] **Step 1:** test frontend: il titolo e il sottotitolo vengono da `lib/config.ts` e non da JSX letterale.
- [ ] **Step 2:** estrarre nome, sottotitolo, disclaimer e badge tecnologici in `lib/config.ts`, alimentato da variabili `NEXT_PUBLIC_*` con default.
- [ ] **Step 3:** portare il copy in inglese; le stringhe italiane restano solo nei test che verificano quel copy, quindi si aggiornano insieme.
- [ ] **Step 4:** master agent: `INSTRUCTIONS` diventa una funzione di `settings.product_language`, con la riga "Answer in {language}" al posto di "Answer in Italian".

**Fatto quando:** cambiare due variabili d'ambiente cambia nome del prodotto e lingua delle risposte, senza toccare un file `.tsx` o un prompt.

### Task A3 — Lo scope arriva dalla richiesta, non da una costante

**Files:** `demo-master-agent/src/demo/server/app.py`, `demo-master-agent/src/demo/config.py`, `tests/test_scope.py` (nuovo)

**Perche':** `_resolve_snapshot_scope` ignora la richiesta e restituisce una costante. E' corretto per un laboratorio a tenant singolo, ed e' esattamente la funzione che l'autenticazione dovra' rimpiazzare. Se resta una costante, chi aggiunge l'autenticazione deve riscrivere la catena; se diventa una funzione con un default a tenant singolo, deve solo sostituire l'implementazione.

- [ ] **Step 1:** test: senza intestazioni la risoluzione restituisce `settings.default_scope`.
- [ ] **Step 2:** test: con l'intestazione di scope configurata la risoluzione la usa, e uno scope vuoto o malformato ricade sul default invece di propagare stringhe arbitrarie.
- [ ] **Step 3:** implementare `scope_resolver` iniettabile in `create_app`, con il resolver a tenant singolo come default.
- [ ] **Step 4:** README: una sezione "dove si aggancia l'autenticazione" che nomina questa funzione, gli `security_schemes` della card e il token di servizio.

**Fatto quando:** esiste un punto solo da cambiare per passare da un tenant a molti, ed e' documentato.

### Task A4 — Il sottoagente e' un elenco, non una costante

**Files:** `demo-master-agent/src/demo/tools/subagent_tools.py`, `demo-master-agent/src/demo/agents/master.py`, `config.py`

**Perche':** oggi il master conosce un URL e chiama il tool `ask_knowledge`. Un template deve permettere zero o due sottoagenti senza modificare il codice dei tool.

- [ ] **Step 1:** test: con due sottoagenti configurati vengono costruiti due tool distinti, con nomi e descrizioni derivati dalla card di ciascuno.
- [ ] **Step 2:** test: con nessun sottoagente configurato l'agente parte lo stesso e non espone tool di sottoagente.
- [ ] **Step 3:** configurazione: da `DEMO_KNOWLEDGE_AGENT_URL` a una lista `SUBAGENTS` (nome + url + token opzionale).
- [ ] **Step 4:** `answer_subagent` risolve il sottoagente da riprendere leggendo `subagent_pending["agent"]`, che gia' scriviamo e finora non leggevamo.

**Fatto quando:** aggiungere un sottoagente e' una riga di configurazione, e `subagent_pending` porta abbastanza informazione per riprendere quello giusto.

---

## Blocco B — Lo stato che due repliche non condividono

Con Kubernetes nel bersaglio, questi smettono di essere note nel README e diventano difetti osservabili.

**Chiuso il 2026-09-09.** Tre note emerse facendolo, che il piano non prevedeva:

- il frontend **non** trattava il cursore dei log come opaco (`cursor: number`, confrontato con `>`): il contratto `agui/logs-page` ora lo dichiara e lo verifica dalle due parti;
- la lettura dei log fa `flush` prima di leggere, altrimenti il tab resta indietro di un intervallo di drain e una riga prodotta mentre si risponde alla richiesta che la chiede arriva al giro dopo;
- il lock rilasciato senza confrontare il token e' peggio del lock assente: rilascerebbe quello che un altro ha preso dopo la scadenza del proprio.

### Task B1 — I log operativi escono dal processo

**Files:** `demo-master-agent/src/demo/logging_bridge.py`, `demo-master-agent/src/demo/server/app.py`, `tests/test_logging_bridge.py`

**Perche':** `LogCollector` e' un `deque` in RAM con un cursore per processo. Con due repliche il tab LOG mostra meta' delle righe, e quali meta' dipende da chi ha risposto alla `GET /logs`.

- [x] **Step 1:** test: due collector che scrivono sullo stesso backend condiviso vengono letti da un terzo con un cursore solo.
- [x] **Step 2:** implementare il backend su Redis Streams (`XADD` con maxlen approssimato, `XRANGE` dal cursore), tenendo il `deque` come ripiego quando Redis non e' configurato.
- [x] **Step 3:** il cursore diventa l'id dello stream, non un intero per processo; il frontend **non** lo trattava come opaco (`cursor: number`): ora si', e il contratto `agui/logs-page` lo dichiara.
- [x] **Step 4:** documentare il ripiego: senza Redis, i log restano locali e con piu' repliche sono parziali.

**Fatto quando:** con due repliche il tab LOG mostra le righe di entrambe, in ordine. **Fatto** il 2026-09-09: verificato con un secondo processo locale, le cui righe compaiono nel `/logs` del container.

### Task B2 — La compattazione prende un lock, non un set in RAM

**Files:** `demo-memory-service/src/memory_service/service.py`, `stores/hot.py`, `tests/test_service.py`

**Perche':** `self._compacting: set[tuple[str, str]]` impedisce due compattazioni contemporanee **nello stesso processo**. Con due repliche lo stesso thread viene riassunto due volte, si pagano due chiamate al modello e vince l'ultima scrittura.

- [x] **Step 1:** test: due istanze di `ThreadMemory` sullo stesso Redis, invocate insieme sullo stesso thread, producono una sola chiamata al summarizer.
- [x] **Step 2:** implementare un lock su Redis con `SET key value NX EX <ttl>` e rilascio condizionato al valore (nessun `DEL` cieco: rilasciare il lock di un altro e' peggio che non prenderlo).
- [x] **Step 3:** TTL piu' lungo della compattazione piu' lenta osservata, e log esplicito quando il lock scade prima della fine.
- [x] **Step 4:** senza Redis si ricade sul set in RAM, dichiarato nel log all'avvio.

**Fatto quando:** il test con due istanze passa e il conteggio delle chiamate al modello non dipende dal numero di repliche. **Fatto** il 2026-09-09.

### Task B3 — La agent card si rilegge, e non solo al riavvio

**Files:** `demo-master-agent/src/demo/tools/subagent_tools.py`, `demo/a2a/client.py`

**Perche':** `cached["client"]` non scade mai. Un sottoagente che cambia URL, capability o skill resta invisibile finche' non si riavvia il master — e con la card estesa questo significa anche un catalogo fermo a ieri.

- [x] **Step 1:** test: passato il TTL la card viene richiesta di nuovo; entro il TTL no.
- [x] **Step 2:** test: se la rilettura fallisce si continua con la card in cache invece di far fallire il turno.
- [x] **Step 3:** implementare TTL configurabile (default dieci minuti) sulla cache di card e client.

**Fatto quando:** cambiare la card del knowledge agent si riflette nel master senza riavviarlo. **Fatto** il 2026-09-09.

---

## Blocco C — I confini reggono il traffico vero

### Task C1 — Timeout, ritentativi e un limite alle richieste

**Files:** `demo-master-agent/src/demo/a2a/client.py`, `memory/remote_store.py`, `tools/memory_tools.py`, `server/app.py`

- [ ] **Step 1:** test: una chiamata alla memoria che fallisce con 503 viene ritentata con backoff e poi degrada, senza far fallire il turno.
- [ ] **Step 2:** test: oltre N fallimenti consecutivi il client smette di provare per un intervallo (circuit breaker) e lo dice nei log una volta sola, non a ogni richiesta.
- [ ] **Step 3:** limite di dimensione sul corpo di `POST /agui` e sul webhook push; oltre il limite, 413 con messaggio.
- [ ] **Step 4:** timeout espliciti su ogni client HTTP, nessuno lasciato al default della libreria.

**Fatto quando:** un servizio a valle giu' produce una degradazione dichiarata e limitata nel tempo, non una raffica di stack trace.

### Task C2 — Readiness diverso da liveness

**Files:** `demo-*/src/**/app.py` (o `server.py`), `demo-infra/compose.yaml`, manifest k8s

**Perche':** oggi `/health` risponde `ok` anche se Mongo e' irraggiungibile. Un orchestratore che ci crede manda traffico a un processo che non puo' servirlo.

- [ ] **Step 1:** test: `/health/ready` risponde 503 quando la dipendenza obbligatoria non risponde, e 200 quando risponde.
- [ ] **Step 2:** `/health/live` resta una risposta senza dipendenze: serve a dire "il processo non e' bloccato", non "il sistema funziona".
- [ ] **Step 3:** compose e manifest puntano alle due sonde giuste.

**Fatto quando:** spegnere Mongo rende il servizio di memoria *not ready* senza farlo riavviare in loop.

### Task C3 — Il webhook push non accetta ripetizioni all'infinito

**Files:** `demo-master-agent/src/demo/a2a/push.py`, `server/app.py`, `tests/test_push.py`

**Perche':** il token firma il thread e non scade. Chi lo intercetta una volta puo' riusarlo per sempre su quel thread.

- [ ] **Step 1:** test: una notifica con timestamp piu' vecchio della finestra viene rifiutata.
- [ ] **Step 2:** test: la stessa notifica consegnata due volte viene annotata in memoria una volta sola.
- [ ] **Step 3:** firmare `thread_id + finestra temporale`, e deduplicare per `task_id + stato` con una chiave a TTL su Redis.

**Fatto quando:** replay e duplicati sono coperti da test, e il README dice perche' la firma include il tempo.

### Task C4 — Log strutturati e tracce che attraversano i tre servizi

**Files:** i tre servizi Python, `demo-infra/compose.yaml`

**Perche':** oggi una domanda che tocca master, knowledge e memoria lascia tre serie di righe che nessuno puo' ricucire.

- [ ] **Step 1:** log in JSON con `service`, `thread_id`, `run_id`, `trace_id`.
- [ ] **Step 2:** propagazione W3C `traceparent` sulle chiamate A2A e verso la memoria.
- [ ] **Step 3:** OpenTelemetry con esportatore OTLP configurabile; senza endpoint configurato non si esporta e non si rompe niente.
- [ ] **Step 4:** una traccia di esempio nel README: un turno con sottoagente, dall'ingresso all'artefatto.

**Fatto quando:** da un `trace_id` si ricostruisce il giro completo di un turno.

---

## Blocco D — I dati hanno un ciclo di vita

### Task D1 — Un runner di migrazioni

**Files:** `demo-memory-service/src/memory_service/migrations/` (nuovo), `api.py`

**Perche':** la rinomina dei campi dei fatti ha richiesto uno script a mano e ha impedito l'avvio del servizio finche' non e' stato eseguito. Un template non puo' consegnare quel problema a chi lo forka.

- [ ] **Step 1:** test: una migrazione applicata due volte non cambia nulla la seconda; il numero di versione e' registrato in Mongo.
- [ ] **Step 2:** runner che applica in ordine le migrazioni mancanti all'avvio, prima della creazione degli indici.
- [ ] **Step 3:** prima migrazione: `chiave`/`valore` -> `key`/`value`, cioe' quella gia' fatta a mano, cosi' un clone vecchio si aggiorna da solo.
- [ ] **Step 4:** la creazione degli indici fallisce con un messaggio che nomina la migrazione mancante, invece di un `E11000` grezzo.

**Fatto quando:** un database scritto dalla versione precedente si avvia senza intervento manuale.

### Task D2 — Ritenzione e reindicizzazione

**Files:** `demo-memory-service/src/memory_service/stores/mongo.py`, `service.py`, `api.py`

- [ ] **Step 1:** test: un thread piu' vecchio della ritenzione configurata non compare piu' nelle letture ed e' rimosso dai bucket.
- [ ] **Step 2:** comando `POST /admin/reindex` che ricostruisce l'indice vettoriale dai transcript, a scope o a thread.
- [ ] **Step 3:** ritenzione disattivata di default, con la variabile documentata: cancellare conversazioni e' una decisione di prodotto, non un default.

**Fatto quando:** perdere Redis non significa perdere la ricerca semantica, e la crescita di Mongo ha un limite dichiarato.

---

## Blocco E — Confezione e consegna

### Task E1 — Immagini che si possono mettere in produzione

**Files:** i quattro `Dockerfile`, `demo-infra/compose.yaml`

- [ ] **Step 1:** utente non root in tutte e quattro le immagini.
- [ ] **Step 2:** immagini di base pinnate per digest, non per tag.
- [ ] **Step 3:** `mongo` e `redis` con limiti di risorse nel compose; Redis con `appendonly` deciso e dichiarato.
- [ ] **Step 4:** `docker compose config` senza segreti in chiaro: `.env` resta per lo sviluppo, i manifest usano i secret dell'orchestratore.

**Fatto quando:** `docker scout` (o equivalente) non segnala vulnerabilita' alte sulle immagini costruite, e nessun processo gira come root.

### Task E2 — Il frontend legge il proprio URL a runtime

**Files:** `demo-frontend/lib/agui/client.ts`, `lib/agui/logs.ts`, `app/layout.tsx`, `Dockerfile`

**Perche':** `NEXT_PUBLIC_AGUI_URL` viene inlinato nel bundle a build time. La stessa immagine non puo' passare da staging a produzione: bisogna ricostruirla, il che vanifica la promozione dell'artefatto.

- [ ] **Step 1:** test: il client legge la configurazione iniettata dal server invece della costante di build.
- [ ] **Step 2:** esporre la configurazione pubblica da un endpoint o da uno script nel layout, letto una volta all'avvio.
- [ ] **Step 3:** togliere l'`ARG` dal Dockerfile.

**Fatto quando:** la stessa immagine del frontend gira contro due backend diversi cambiando solo una variabile d'ambiente del container.

### Task E3 — CI per repo

**Files:** `.github/workflows/ci.yml` in ciascuno dei quattro repo

- [ ] **Step 1:** test + lint su push e pull request; per il servizio di memoria, Mongo e Redis come servizi del job.
- [ ] **Step 2:** build dell'immagine e scansione delle vulnerabilita'.
- [ ] **Step 3:** i test di contratto del Blocco F girano in ognuno dei repo che partecipano al contratto.

**Fatto quando:** un push che rompe un contratto fra repo fallisce in CI nel repo che l'ha rotto.

### Task E4 — Manifest Kubernetes

**Files:** `demo-infra/deploy/` (nuovo)

- [ ] **Step 1:** Deployment per i quattro servizi con probe di liveness e readiness, richieste e limiti, `securityContext` non root.
- [ ] **Step 2:** Secret e ConfigMap separati; nessuna variabile sensibile in un ConfigMap.
- [ ] **Step 3:** due repliche di master agent e servizio di memoria nel manifest di default — cosi' una regressione del Blocco B si vede subito.
- [ ] **Step 4:** Mongo e Redis **fuori** dai manifest, con un README che dice di usare servizi gestiti e perche' un database in un Deployment senza operator e' una trappola.

**Fatto quando:** `kubectl apply` porta su lo stack contro un Mongo e un Redis esterni, e il giro completo funziona con due repliche.

---

## Blocco F — I contratti fra repo non si rompono in silenzio

### Task F1 — Fixture condivise e test di contratto

**Files:** `demo-infra/contracts/` (nuovo), test in `demo-master-agent`, `demo-knowledge-agent`, `demo-frontend`

**Perche':** la rinomina dell'artefatto (`scheda` -> `briefing`, con i suoi campi) ha toccato tre repo. E' passata perche' erano aperti insieme nella stessa sessione. In un fork, chi cambia il produttore non vede il consumatore.

- [x] **Step 1:** portare in `demo-infra/contracts/` i campioni JSON gia' esistenti come fixture versionate: artefatto A2A, stato AG-UI (`plan`, `artifacts`, `subagent_pending`), risposte dell'API di memoria.
- [x] **Step 2:** ogni repo carica le fixture e verifica di saperle produrre o consumare; il frontend le carica in vitest.
- [x] **Step 3:** un numero di versione dentro le fixture, e un test che fallisce con un messaggio che dice *quale* contratto e' cambiato e *chi* lo consuma.

**Fatto quando:** rinominare un campo dell'artefatto fa fallire il test nel repo che lo ha rinominato, con il nome del consumatore nel messaggio.

### Task F2 — Il README del template

**Files:** `demo-infra/README.md`, README dei quattro repo

- [ ] **Step 1:** "Come si forka": cosa si cambia (configurazione, branding, agenti, skill), cosa si tiene, cosa si butta (il knowledge agent e' un esempio).
- [ ] **Step 2:** "Cosa manca di proposito": autenticazione, con il rimando alle giunture del Task A3.
- [ ] **Step 3:** "Limiti dichiarati": ogni ripiego che degrada senza Redis, ogni default che vale per un tenant solo.
- [ ] **Step 4:** una tabella delle variabili d'ambiente, generata dal modulo di configurazione, non scritta a mano.

**Fatto quando:** una persona che non ha visto il progetto arriva a un giro completo seguendo solo il README.

---

## Ordine e rischio

L'ordine e' per rischio decrescente, non per comodita'.

1. **Blocco F prima di tutto il resto** — Task F1. Senza i test di contratto, ogni blocco successivo puo' rompere un altro repo senza accorgersene. E' il lavoro che rende sicuro il lavoro dopo.
2. **Blocco B** — lo stato di processo. Se emerge che qualcosa non si puo' condividere, cambia la forma del deploy, e va scoperto prima di scrivere i manifest.
3. **Blocco A** — la superficie di configurazione. Tocca molti file ma poca logica; conviene farlo quando i contratti sono coperti.
4. **Blocco D** — le migrazioni, prima che nascano altri fork con dati vecchi.
5. **Blocco C** — robustezza dei confini.
6. **Blocco E** — confezione, per ultima: e' l'unica che dipende da tutte le altre.

## Cosa non e' in questo piano

- **Autenticazione e autorizzazione.** Rimandate per scelta; il piano prepara i punti di aggancio (A3) e non altro.
- **Pianificazione multi-agente.** E' il tema che ti interessa e resta un lavoro a se': il template deve reggerla, non contenerla.
- **Registry degli agenti con ricerca semantica**, decadimento dei fatti. Restano in `docs/specs/2026-09-07-agui-lab-design.md` §11 come task futuri: nessuno dei due e' un prerequisito per usare il template.
