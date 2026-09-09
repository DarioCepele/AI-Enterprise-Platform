# Servizio dei processi

Processi durevoli: **le definizioni sono dati versionati**, le istanze sono
righe su Postgres, e un'istanza finisce con la versione con cui e' partita.

Questo repo, oggi, tiene definizioni e istanze. L'esecuzione dei passi -- con
ripresa dopo un crash, attese lunghe, approvazioni umane e compensazioni --
arriva dai blocchi successivi del piano.

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
un passo `agent` senza `owner`, un tipo sconosciuto. Una definizione che
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
GET  /health/live  /health/ready      processo vivo / puo' servire
```

Lo scope arriva dall'intestazione `X-Process-Scope` e vale come confine: le
istanze di uno scope non si leggono da un altro. E' la stessa giuntura degli
altri servizi, ed e' li' che si attacchera' l'autenticazione.

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
