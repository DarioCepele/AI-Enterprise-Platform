# Servizio dei processi

Processi durevoli: **le definizioni sono dati versionati**, le istanze sono
righe su Postgres, e un'istanza finisce con la versione con cui e' partita.

Un'istanza avanza dentro un workflow DBOS: **ogni passo e' uno step
checkpointato**, quindi un processo che muore e torna non rifa' quello che ha
gia' fatto. Attese lunghe, approvazioni e compensazioni arrivano dai blocchi
successivi. Un passo `agent` delega a un agente A2A e riparte quando arriva la
notifica; un passo `approval` si ferma davanti a una persona e riparte con la
sua decisione. In mezzo l'istanza e' una riga, non una richiesta appesa.

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
un passo `agent` senza `owner`, un `on_timeout` o un `goto` che non
esistono, un tipo sconosciuto. Una definizione che
fallisse alla prima istanza fallirebbe davanti a chi la sta usando.

**Un passo raggiunto da un ramo dice da se' dove va**, con `goto`: non puo'
essere aspettato con `depends_on`, perche' l'altro lato del ramo non arriverebbe
mai e la dipendenza non si chiuderebbe. `goto` e `branches` insieme si
rifiutano: da un passo si esce da una parte sola.

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
POST /instances/{id}/steps/{step}/answer     una persona risponde a un chiarimento
POST /instances/{id}/steps/{step}/decision   una persona approva o rifiuta
GET  /instances/{id}/events?after=         la storia, in ordine
GET  /instances/{id}/replay                che strada ha preso, e perche'
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

**Chi non risponde entro `timeout_seconds`** manda il lavoro dove dice
`on_timeout`: il passo resta `escalated` con il motivo scritto, e **il processo
continua da quel passo** -- un'escalation che scrivesse solo "scaduto" lascerebbe
l'istanza li' ad aspettare che qualcuno se ne accorga. Senza `on_timeout` il
passo fallisce, che e' l'altra scelta onesta.

L'attesa e' durevole: e' DBOS a tenerla, non un processo. Un servizio ucciso
mentre aspetta torna ad aspettare la stessa risposta, e la notifica arrivata a
una replica diversa trova comunque la sua istanza.

## Perche' e' passata di li'

Ogni cambiamento scrive **anche l'evento che lo spiega**, nella stessa
transazione: uno stato scritto senza il suo evento sarebbe uno stato che nessuno
sa raccontare. La storia si legge da `/instances/{id}/events`, e cresce solo in
fondo -- per seguirla si chiede `?after=<ultimo id letto>`.

`/instances/{id}/replay` **rigioca la storia senza fare niente**: nessun tool
chiamato, nessun agente interrogato, nessun modello. Quello che poteva andare in
due modi -- cosa ha risposto un tool, cosa ha detto l'agente -- si rilegge dagli
eventi; quello che e' una regola -- quale ramo prende una condizione, quale passo
viene dopo -- si ricalcola e **si confronta con quello che era stato registrato**.

Se le due cose non coincidono piu', e' un 409 e non un percorso: vuol dire che
la definizione o i dati sono cambiati sotto, e la storia ha smesso di spiegare
l'istanza. Rigiocare in silenzio con la regola nuova sarebbe peggio che
rifiutare.

La regola su cosa viene dopo un passo sta nella **definizione**, non nel motore,
proprio perche' il replay deve camminare il processo come lo ha camminato il
motore: due copie di quella regola sarebbero due processi.

## Log e tracce si trovano

Ogni riga scritta mentre un passo gira porta `instance_id`, senza che il tool
debba dirlo; e se c'e' un collector, l'istanza si tiene il `trace_id` del giro
in cui e' partita, come evento nella sua storia. Da una riga di log si arriva
all'istanza, e dall'istanza alle righe degli altri servizi.

## Quello che non si aspetta a vicenda parte insieme

**Ogni passo e' un workflow a se'**, con id `istanza:passo`. Da questo viene il
resto:

- i passi pronti nello stesso momento **partono insieme**, e il join aspetta
  tutti prima di decidere qualcosa -- anche quando uno ha gia' fallito, perche'
  fermarsi subito lascerebbe gli altri in giro senza nessuno che ne legga
  l'esito;
- se qualcuno non passa, l'istanza si ferma **dicendo chi**: lo stato e' sulla
  riga dell'istanza (`failed: paga_fornitore`), non nei log;
- due passi possono **aspettare contemporaneamente** -- due agenti, due
  approvazioni -- ognuno sul proprio workflow;
- riavviare lo stesso passo non lo riesegue: l'id e' derivato, non casuale, e un
  passo gia' concluso restituisce quello che aveva scritto.

Un passo che solleva un'eccezione **fallisce come passo**, con il messaggio
sulla riga: l'istanza si ferma in `failed` invece di sparire dentro uno stack
trace.

## Quanto costa davvero il ventaglio

Due passi `agent` indipendenti contro gli stessi due, uno in attesa dell'altro.
Stesse domande, agenti veri (`knowledge` e `analysis`), cinque giri alternati
perche' la latenza del modello e' rumorosa. Si rimisura con
`demo-infra/tools/measure_fan_out.py`, che si avvia un servizio dei processi suo
con due definizioni usa e getta.

| | insieme | uno dopo l'altro |
| --- | --- | --- |
| tempo totale | 14 s (9-24) | 22 s (14-32) |
| round del modello | 4 (4-4) | 4 (4-4) |
| token in | 2966 (2966-2968) | 2966 (2966-2968) |
| token out | 509 (384-611) | 524 (387-827) |

Mediana su cinque giri, fra parentesi minimo e massimo.

**Il ventaglio compra tempo, non lavoro.** Round e token in ingresso sono gli
stessi: nessuno dei due modi fa fare meno fatica al modello. E' un'informazione
che vale piu' del guadagno: la ragione per parallelizzare e' l'attesa, e se i
passi non aspettano niente di lungo non c'e' niente da guadagnare.

**Il rumore e' piu' grande della differenza, su un giro solo.** Un singolo
confronto ha dato 15 s contro 13 s -- il parallelo *piu' lento* -- e un altro
49 s contro 23 s, perche' il modello aveva deciso di fare un round in piu'.
Chiunque misuri due volte e scriva il numero che gli piace ha misurato le
proprie preferenze.

**Il costo sta nella storia dell'istanza**, non nei log: ogni passo `agent`
scrive un evento `step_usage` con l'agente, i round e i token. Da li' escono i
numeri qui sopra, e da li' escono anche quelli di un'istanza qualsiasi in
produzione.

## Disfare quello che si era gia' fatto

Un passo puo' dichiarare `compensate_with: <tool>`. Quando l'istanza si ferma
per un fallimento o per un rifiuto, i passi gia' conclusi che l'hanno dichiarato
vengono **disfatti in ordine inverso**: disfare prima quello che veniva dopo
significherebbe togliere qualcosa su cui il passo successivo si appoggia ancora.

- **una compensazione che fallisce non ferma le altre**: quel passo resta
  `compensation_failed` col motivo scritto, gli altri vengono comunque disfatti;
- **quello che il passo aveva fatto resta nella storia**: lo stato diventa
  `compensated`, l'output no;
- **un'istanza compensata lo dice**: stato `compensated`, e la nota e'
  `failed: notify; undone: apply` -- non un `failed` che lascia aperta la
  domanda se qualcosa sia stato ripreso;
- **si disfa una volta sola**: se il passo aveva una `idempotency_key`, la sua
  compensazione ne ha una propria. Chi non la dichiara puo' essere disfatto due
  volte, che e' il motivo per cui l'effetto che conta la dichiara.

## L'approvazione

Un passo `approval` mette l'istanza in `waiting_approval` e scrive sulla riga
chi puo' decidere. La decisione arriva da `.../decision` con `by`, `decision`
(`approved` o `rejected`) e una nota, e **finisce nell'output del passo**:
"il processo e' passato di li' e qualcuno lo ha lasciato passare" e' esattamente
la domanda a cui la storia deve rispondere.

- **decidere due volte non fa niente**: la seconda arriva quando il passo non
  aspetta piu' e viene rifiutata con 409, invece di restare nella cassetta e
  farsi leggere dalla prossima attesa;
- **decide solo chi e' negli `approvers`**: gli altri prendono 403 con la lista;
- **un rifiuto non e' un errore**: l'istanza si ferma in `rejected`, i passi a
  valle non partono, e lo stato lo dice;
- **l'attesa e' limitata**: scaduto `timeout_seconds` (di default una settimana)
  si va dove dice `on_timeout`; senza `on_timeout` il passo fallisce, perche'
  un'istanza in attesa di nessuno e' peggio di un fallimento dichiarato.

L'attesa non costa: nessun task che gira, nessuna connessione tenuta. Un
`docker compose restart` mentre il passo aspetta non si vede -- l'approvazione
puo' arrivare il giorno dopo, da un altro processo, e c'e' un test che uccide
quello che aspettava per dimostrarlo.

## Sviluppo

```bash
docker compose up -d postgres      # da demo-infra
uv run pytest                      # crea da se' il database dei test
uv run python -m process_service   # su 127.0.0.1:8300
```

I test dello store girano contro un Postgres vero: un'istanza e' una riga che
deve sopravvivere al processo che l'ha scritta, e un finto in memoria non
proverebbe niente al riguardo. Girano pero' su un **database a parte**
(`<nome>_test`, o `PROCESS_TEST_POSTGRES_DSN`): condividendolo col servizio, il
servizio prova a recuperare i workflow lasciati dai test -- processi di cui non
ha mai sentito parlare -- e lo scrive nel log di chi lo sta usando.

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
