# Servizio di memoria conversazionale

Possiede la memoria delle conversazioni del laboratorio AG-UI: i transcript per
thread, la coda calda per le letture frequenti e — piu' avanti — riassunti e
fatti duraturi. Il master agent non parla con i database: parla con questo.

```powershell
uv sync
uv run python -m memory_service      # http://127.0.0.1:8100
```

## Due memorie, requisiti opposti

| | Dove | Cosa tiene | Perche' li' |
| --- | --- | --- | --- |
| Durevole | MongoDB | transcript completi, per thread | deve sopravvivere a tutto; si legge a fette |
| Breve termine | Redis | ultimi N messaggi del thread | latenza bassa e **scadenza automatica** |

**Regola che tiene in piedi il disegno: in Redis non vive mai l'unica copia di
un dato.** La coda calda si ricostruisce da Mongo alla prima lettura dopo una
scadenza, e il container Redis non ha volume apposta. Se perdere Redis
perdesse dati, sarebbe il posto sbagliato dove tenerli. Le scritture vanno
prima sul durevole e poi in cache: l'ordine opposto lascerebbe in cache un
messaggio che non esiste altrove — un difetto che si cancella da solo alla
scadenza, quindi impossibile da diagnosticare dopo.

`GET /health` distingue i due casi: Mongo giu' e' **guasto** (503), Redis giu'
e' **degradato** (200, `status: degraded`) perche' si continua a rispondere
leggendo dal durevole.

## Schema: bucket, non un array che cresce

L'istanza MongoDB del laboratorio e' un **nodo singolo senza replica set**:
niente transazioni multi-documento. Da qui due scelte non negoziabili.

1. **Ogni scrittura atomica sta dentro un solo documento.** Un turno e' un
   `$push` in un bucket; non esiste una sequenza di scritture che, interrotta a
   meta', lasci uno stato illegale.
2. **La numerazione arriva da un `$inc` su un solo documento** (`threads`).
   Se la scrittura del messaggio fallisce dopo, resta un numero saltato: una
   lacuna nella numerazione, mai un messaggio perso o duplicato.

I messaggi stanno in documenti bucket da 50 (`thread_turns`), non in un unico
documento per thread: un array che cresce senza limite finisce contro il tetto
di 16 MB e rende costosa ogni lettura. Leggere la coda tocca uno o due
documenti invece di tutta la conversazione.

Il bucket nuovo si crea senza lock: l'indice unico `(scope, thread_id, bucket)`
fa passare una sola delle scritture in corsa, e la perdente rientra dalla via
normale, dove il posto ormai c'e'. Verificato con dodici append simultanei.

| Collezione | Indice | A cosa serve |
| --- | --- | --- |
| `thread_turns` | `(scope, thread_id, bucket)` unico | crea bucket senza transazioni |
| `thread_turns` | `(scope, thread_id, last_seq desc)` | leggere la coda |
| `threads` | `(scope, thread_id)` unico | contatore delle posizioni |

## Lo scope, e di chi ci si fida

Ogni chiamata dichiara `X-Memory-Scope`: e' il confine di autorizzazione, e
finisce nella chiave di Mongo **e** in quella di Redis, cosi' due scope non si
leggono a vicenda nemmeno per un errore di query.

**Questo e' un servizio interno.** Non va esposto al browser ne' a internet:
chi lo chiama dichiara lo scope e il servizio si fida, come farebbe un
database. In produzione sta su rete privata, con mTLS o un token di servizio
davanti, e lo scope resta l'identita' **verificata dal chiamante** — mai un
valore che arriva dall'utente finale.

## API

| | | |
| --- | --- | --- |
| `GET` | `/health` | stato delle due memorie |
| `POST` | `/threads/{id}/messages` | appende un turno, restituisce `seq` e `ts` |
| `GET` | `/threads/{id}/messages?limit=N` | ultimi N turni, in ordine, con `source` (`hot`/`durable`) |
| `DELETE` | `/threads/{id}` | dimentica il thread, durevole e cache |

`source` non e' decorazione: e' l'unico modo per accorgersi che la cache non
viene mai usata, o che copre sempre tutto.

## Configurazione

Variabili con prefisso `MEMORY_`, dal `.env` locale non versionato:

| Variabile | Uso |
| --- | --- |
| `MEMORY_MONGO_URI` | URI completo, credenziali comprese |
| `MEMORY_MONGO_DATABASE` | database dei transcript |
| `MEMORY_REDIS_URI` | URI di Redis |
| `MEMORY_BUCKET_SIZE` | messaggi per bucket (default 50) |
| `MEMORY_HOT_TAIL_SECONDS` | scadenza della coda calda (default 1800) |
| `MEMORY_HOT_TAIL_MESSAGES` | quanti messaggi tiene la coda calda (default 100) |

I due database si alzano dal compose di `demo-infra`:

```bash
cd ../demo-infra && docker compose up -d mongo redis
```

## Test

```powershell
uv run pytest
```

Girano contro Mongo e Redis **veri**, non simulati: il modello a bucket senza
transazioni si regge su garanzie del server — atomicita' del singolo documento
e indice unico — che un finto non riproduce, e che sono esattamente la parte da
verificare. Ogni test usa uno scope irripetibile, quindi non si disturbano fra
loro. Senza `.env` configurato i test si saltano invece di fallire.

## Cosa non c'e' ancora

- **Riassunti**: i turni vecchi non vengono compattati. La collezione dei
  riassunti sara' separata dai transcript, che restano integrali.
- **Fatti duraturi per utente** (preferenze, entita').
- **Ricerca semantica**: `$vectorSearch` e' solo su Atlas. Redis 8 include il
  Query Engine e i vector set (`FT.CREATE`, `VADD` verificati sull'istanza),
  quindi la ricerca per significato puo' vivere dove sta gia' la coda calda,
  senza aggiungere un terzo datastore.
