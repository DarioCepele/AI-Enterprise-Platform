# Servizio dei processi

Processi durevoli: **le definizioni sono dati versionati**, le istanze sono
righe su Postgres, e un'istanza finisce con la versione con cui e' partita.

Un'istanza avanza dentro un workflow DBOS: **ogni passo e' uno step
checkpointato**, quindi un processo che muore e torna non rifa' quello che ha
gia' fatto. Attese lunghe, approvazioni e compensazioni arrivano dai blocchi
successivi; un passo `approval` **sospende** l'istanza, che resta una riga e non
una richiesta appesa. Un passo `agent` invece e' completo: delega a un agente
A2A, sospende, e riparte quando arriva la notifica.

## Una definizione

Un file YAML in `processes/`, caricato all'avvio. `processes/example-approval.yaml`
e' un **esempio senza dominio**: esiste per esercitare ogni tipo di passo una
volta, ed e' la prima cosa da cancellare quando questo template diventa un
prodotto.

```yaml
id: example-approval
version: 1
steps:
  - id: collect
    type: tool
    tool: collect_request
  - id: check
    type: agent
    owner: knowledge          # chi lo esegue non e' un dettaglio
    depends_on: [collect]
    input:
      question: What should be known before deciding?
    timeout_seconds: 120
    on_timeout: approval      # scaduto il tempo, decide una persona
  - id: decide
    type: decision
    depends_on: [check]
    branches:
      - when: "amount > 10000"
        goto: approval
      - when: "true"
        goto: apply
```

I tipi di passo sono `tool`, `agent`, `approval`, `decision`, `open_goal`.

**Cosa si rifiuta all'avvio**, con il nome del passo o del file: un id
duplicato, una dipendenza che non esiste, un ramo che punta nel vuoto, un ciclo,
un passo `agent` senza `owner`, un `on_timeout` che non esiste, un tipo
sconosciuto. Una definizione che
fallisse alla prima istanza fallirebbe davanti a chi la sta usando.

**I passi iniziali sono quelli che non dipendono da niente e a cui nessuno
punta.** Un passo raggiungibile solo attraverso un ramo non parte da solo:
partirebbero entrambi i lati della decisione, che e' l'opposto di cio' a cui
serve un ramo.

## Le versioni

Una nuova istanza parte sull'ultima versione; una gia' avviata continua sulla
propria. Per questo la versione e' **copiata nella riga** dell'istanza invece di
essere risolta ogni volta: il catalogo va avanti, l'istanza no.

## API

```
GET  /processes                       cosa si puo' avviare
GET  /processes/{id}?version=         una definizione
POST /processes/{id}/instances        avvia, con `input` e `version` opzionale
GET  /instances/{id}                  una istanza con lo stato dei suoi passi
GET  /instances?status=&limit=        le istanze di questo scope, dalla piu' recente
POST /instances/{id}/steps/{step}/answer   una persona risponde a un chiarimento
POST /a2a/push/{scope}/{id}/{step}    la notifica dell'agente, firmata
GET  /health/live  /health/ready      processo vivo / puo' servire
```

Lo scope arriva dall'intestazione `X-Process-Scope` e vale come confine: le
istanze di uno scope non si leggono da un altro. E' la stessa giuntura degli
altri servizi, ed e' li' che si attacchera' l'autenticazione.

## Un passo delegato a un agente

Un passo `agent` puo' durare ore, e per tutto quel tempo l'istanza deve essere
una riga, non una richiesta aperta. Il giro e' questo:

1. il motore manda la domanda all'agente (`PROCESS_AGENTS` dice dove sta) con la
   propria `TaskPushNotificationConfig`: url `/a2a/push/{scope}/{istanza}/{passo}`
   e un token firmato;
2. scrive il `task_id` sulla riga del passo e **sospende**;
3. l'agente finisce e chiama il webhook; il servizio verifica il token e sveglia
   l'istanza mandandole un messaggio sul topic del passo.

Il token e' un HMAC su `istanza:passo`: **vale per un passo solo**, quindi uno
che trapelasse non si puo' puntare altrove. Le notifiche di avanzamento non
svegliano niente: solo gli stati terminali e la richiesta di chiarimento.

**La notifica e' un segnale, non la risposta.** Se arriva senza testo -- gli
artefatti erano notificati a parte, o filtrati per strada -- il passo rilegge il
task dall'agente prima di chiudersi.

**Se l'agente chiede un chiarimento** il passo va in `waiting_human` con la
domanda scritta sulla riga, e la risposta della persona (`.../answer`) rientra
**nello stesso task**: aprirne uno nuovo butterebbe via quello che l'agente
aveva gia' capito, e si farebbe rifare la stessa domanda. Dopo
`MAX_CLARIFICATIONS` giri il passo viene passato avanti invece di continuare a
chiedere.

**Chi non risponde entro `timeout_seconds`** finisce dove dice `on_timeout`: il
passo resta `escalated` con il motivo scritto, e chi guarda l'istanza vede
perche'. Senza `on_timeout` il passo fallisce, che e' l'altra scelta onesta.

L'attesa e' durevole: e' DBOS a tenerla, non un processo. Un servizio ucciso
mentre aspetta torna ad aspettare la stessa risposta, e la notifica arrivata a
una replica diversa trova comunque la sua istanza.

## Sviluppo

```bash
docker compose up -d postgres      # da demo-infra
uv run pytest
uv run python -m process_service   # su 127.0.0.1:8300
```

I test dello store girano contro un Postgres vero: un'istanza e' una riga che
deve sopravvivere al processo che l'ha scritta, e un finto in memoria non
proverebbe niente al riguardo.

**Su Windows** il driver async di psycopg non gira sul ProactorEventLoop di
default: il servizio e i test scelgono il SelectorEventLoop da soli. Nel
container non si presenta.

## Migrazioni

Numerate, idempotenti, registrate in `schema_migrations`, applicate all'avvio.
Come nel servizio di memoria, e per lo stesso motivo: un fork non deve dover
trovare uno script ed eseguirlo a mano prima che il servizio parta.

## Cosa rende durevole un'istanza

Tre regole, e tutte e tre hanno un test che le tiene.

**Il workflow deve rigiocarsi uguale.** Il corpo legge i propri dati da uno step
(`read_plan`) e non da righe che cambiano mentre gira: una versione che leggesse
lo stato corrente prenderebbe una strada diversa al secondo giro, e DBOS lo dice
con un errore invece di lasciarlo passare. E' la prima cosa che abbiamo sbagliato.

**Un effetto esterno ha una chiave.** I passi con `idempotency_key` scrivono
prima nella tabella `side_effects`, dove la chiave e' unica: due tentativi dello
stesso passo -- anche su due repliche -- lasciano un effetto solo.

**L'istanza porta la propria versione.** Il motore risolve la definizione con
`(process_id, process_version)` presi dalla riga: il catalogo puo' cambiare
mentre l'istanza gira, e lei finisce comunque il processo che ha iniziato.

Il workflow ha come id **l'id dell'istanza**: chiedere due volte di avviarla non
la avvia due volte.
