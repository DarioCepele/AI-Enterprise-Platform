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

## Snapshot dei thread: chi manda tutto, chi calcola il delta

Il master agent manda lo **stato completo** del thread a ogni run, non il
delta — è il contratto `AGUIThreadSnapshotStore` dell'adattatore AG-UI. Il
delta lo calcola questo servizio, che è l'unico posto ad avere sotto gli occhi
sia ciò che è già scritto sia ciò che arriva: farlo calcolare al chiamante
significherebbe riscriverlo in ogni chiamante.

| | | |
| --- | --- | --- |
| `PUT` | `/threads/{id}/snapshot` | assorbe lo stato completo, risponde `turni_nuovi` |
| `GET` | `/threads/{id}/snapshot` | ricompone il thread, `404` se sconosciuto |
| `DELETE` | `/scope` | dimentica tutti i thread di uno scope |

I messaggi non si sovrascrivono mai: la conversazione è append-only, e uno
snapshot che ripete turni già visti aggiunge zero. È questo che rende sicuro
rimandare tutto a ogni run.

Il riconoscimento dei turni già scritti usa l'**identificativo** quando c'è —
regge riordini e snapshot che ripartono da capo — e ripiega sulla **posizione**
per i messaggi che non ne hanno. Il ripiego è prudente di proposito: in caso di
dubbio scrive di meno, non di più. Un turno mancante si nota; un turno
duplicato nel contesto del modello no.

La forma originale di ogni messaggio viene conservata **verbatim** in `payload`
e restituita così com'è: un thread ricostruito da ruolo e testo perderebbe le
chiamate ai tool, cioè restituirebbe al modello una conversazione che non è mai
avvenuta. `role` e `content` restano accanto, per i riassunti che verranno.

Lo stato del thread che non sono messaggi — `state`, `interrupt`,
`session_state` — sta nel documento del thread, non nei bucket: è un valore
solo, sempre l'ultimo, e riscriverlo non deve toccare la conversazione.

## Conservare tutto, restituire il necessario

Sono due decisioni diverse, e tenerle separate è il punto: una memoria che pota
in scrittura ha buttato per sempre, una che pota in lettura può cambiare idea —
e il transcript integrale resta per i riassunti e per capire cosa è successo.

Misurato su due turni reali di questo laboratorio (40 messaggi, 12 KB salvati):

| ruolo | messaggi | byte | quota |
| --- | --- | --- | --- |
| user | 2 | 214 | **1,8%** |
| reasoning | 12 | 2.256 | 18,9% |
| assistant | 14 | 6.933 | 58,0% |
| tool | 12 | 2.558 | 21,4% |

Quello che l'utente ha davvero detto è il 2% del transcript. Il resto è
macchinario, e il macchinario vecchio non aiuta il turno nuovo.

Cosa fa `GET /threads/{id}/snapshot` prima di rispondere:

1. **Toglie il ragionamento dei turni passati.** Il modello lo rifà; rimandarlo
   costa token e non aggiunge nulla. Dentro una run resta intatto, perché quel
   ciclo non passa da qui.
2. **Svuota i risultati dei tool più vecchi**, tenendo gli ultimi 4 interi e
   sostituendo il contenuto degli altri con un segnaposto. La traccia della
   chiamata resta: il modello vede che quel passo è avvenuto. È la potatura a
   rischio più basso, perché quei risultati si riottengono richiamando il tool.
3. **Tiene una finestra di 60 messaggi**, tagliando su un confine di turno.
   Fra i due confini possibili si sceglie quello precedente al taglio ideale:
   meglio qualche messaggio oltre il tetto che un turno spezzato, dove
   resterebbe il risultato di un tool senza la chiamata che lo ha prodotto.

Effetto misurato sullo stesso thread, senza nessuna chiamata a un LLM:
**40 messaggi e 12.457 byte diventano 28 messaggi e 9.626 byte, il 22,7% in
meno.** Con `?raw=true` torna il transcript integrale.

Ogni risposta porta con sé il campo `curation` con il conto di ciò che manca, e
il master agent lo scrive nei propri log: una potatura silenziosa è
indistinguibile da una perdita di memoria.

| Variabile | Default | Cosa decide |
| --- | --- | --- |
| `MEMORY_DROP_REASONING` | `true` | togliere il ragionamento dei turni passati |
| `MEMORY_KEEP_TOOL_RESULTS` | `4` | quanti risultati di tool restano interi |
| `MEMORY_MAX_CONTEXT_MESSAGES` | `60` | tetto di messaggi restituiti |

## Riassunti: cosa resta quando i turni escono dalla finestra

Senza riassunto, i messaggi che superano il tetto **spariscono**. Con il
riassunto diventano dieci righe in testa al contesto. È l'unica potatura che
costa un'inferenza, e da questo discendono tutte le scelte qui sotto.

**Si compatta dopo aver risposto, mai dentro una richiesta.** Prima non era
così, e il difetto si è visto subito: la `PUT` dello snapshot restava aperta
per tutta la durata della chiamata al modello, il client dell'agente andava in
timeout a 5 secondi e la memoria di quel turno andava persa. Ora la
compattazione è un task di sfondo: la `PUT` torna in **0,14 s** e il riassunto
arriva dopo, per il turno successivo. Due run ravvicinate sullo stesso thread
non pagano due inferenze per lo stesso taglio.

**La finestra si sospende finché il riassunto non c'è.** Conseguenza del punto
precedente: al primo turno oltre la soglia il riassunto non è ancora pronto.
Tagliare comunque sarebbe amnesia — misurata, con l'agente che rispondeva *«non
ho informazioni»* su un fatto che aveva in memoria. Quindi finché manca il
riassunto il contesto resta intero; quando arriva, il taglio avviene. Se invece
nessun modello è configurato il riassunto non arriverà mai, e la finestra
applica il taglio: aspettare per sempre farebbe crescere il contesto senza
limite. Il campo `riassunti` distingue i due casi.

**Il riassunto non è un turno.** Entra nel contesto come messaggio di sistema
con un id riconoscibile (`memoria:riassunto:<seq>`) e viene scartato quando
torna indietro dentro lo snapshot successivo. Senza quel filtro il servizio
finirebbe per riassumere i propri riassunti, a ogni giro, per sempre.

Prova reale su un thread di 80 messaggi con la finestra a 60:

```
PUT /threads/…/snapshot          → 80 turni scritti in 0,14 s
GET subito dopo                  → 80 messaggi, 0 scartati   (finestra sospesa)
GET dopo la compattazione        → 61 messaggi, 20 scartati, riassunti: 1
```

Il riassunto prodotto dal modello, dai 20 messaggi usciti:

> Progetto GINESTRA-42, referente Marta, budget 18k. Sono state poste nove
> domande sul deploy. Le risposte riportate non contengono informazioni
> rilevanti. Non risultano altre richieste o decisioni aperte.

E la domanda che chiude il cerchio, fatta all'agente sul thread compattato —
il messaggio originale con quei dati era fra i 20 usciti:

> **Chi è il referente e qual è il budget?**
> Referente: Marta; budget: 18k (progetto GINESTRA-42).

I riassunti si accumulano invece di sostituirsi (`thread_summaries`, chiave
`covers_to_seq`): quando un riassunto perde qualcosa, è l'unico modo di
risalire a dove si è perso. Cancellare un thread cancella anche i suoi
riassunti — uno sopravvissuto racconterebbe una conversazione cancellata.

| Variabile | Cosa decide |
| --- | --- |
| `MEMORY_SUMMARY_MODEL` | il modello che riassume; vuoto = nessuna compattazione, detto all'avvio |
| `MEMORY_SUMMARY_BASE_URL` | endpoint Chat Completions del riassuntore |
| `MEMORY_SUMMARY_API_KEY` | credenziale del riassuntore |
| `MEMORY_MAX_FACTS` | quanti fatti duraturi entrano nel contesto (default 30) |
| `MEMORY_EMBEDDING_MODEL` | modello degli embedding; vuoto = nessuna ricerca semantica |

## Fatti duraturi: cosa resta vero fuori dalla conversazione

Il riassunto è legato al thread: racconta *quella* conversazione. Un fatto no.
«Preferisce Go», «il referente è Marta», «fuso Europe/Rome» valgono anche in un
thread aperto domani, dove non c'è nessuna conversazione da riassumere — ed è
lì che si vede la differenza fra un agente con memoria e uno che ricomincia
ogni volta.

Per questo i fatti stanno **sullo scope, non sul thread**: cancellare una
conversazione non cancella ciò che si è imparato dell'utente; cancellare lo
scope sì.

**Si estraggono nello stesso momento della compattazione**, e non è un caso:
quello è il punto in cui dei messaggi stanno per smettere di essere leggibili
dal modello. Se qualcosa lì dentro vale anche domani, va tirato fuori adesso o
mai più. Sono due chiamate distinte al modello e non una sola che restituisce
tutto: costano di più, ma un JSON malformato farebbe perdere anche il
riassunto, e i due lavori falliscono per ragioni diverse.

**La chiave è un'etichetta, non una frase** (`linguaggio_preferito`, non «il
linguaggio che preferisce è»). Serve a riconoscere lo stesso fatto quando viene
ridetto con un valore diverso, per **aggiornarlo invece di duplicarlo**: un
agente che crede due valori diversi della stessa cosa è peggio di uno che non
la sa. Una voce senza chiave o senza valore viene scartata, e un JSON
illeggibile non solleva: i fatti sono un di più, e non devono poter far fallire
una conversazione.

I fatti entrano nel contesto a **ogni** lettura, anche in un thread appena
aperto, con una riga che dice come comportarsi in caso di conflitto: *se
l'utente li contraddice, vale quello che dice adesso*. Senza, il modello
difende un fatto vecchio contro chi sta parlando.

Prova reale, di seguito. Prima una conversazione lunga in cui l'utente si
presenta; poi, in un **thread mai visto**, la domanda:

> **Che linguaggio preferisco e in che fuso orario sono?**
> Preferisci Go e sei nel fuso orario Europe/Rome.

Cosa era finito in `scope_facts`, estratto dai turni usciti dalla finestra:

```json
[{"chiave": "nome", "valore": "Dario"},
 {"chiave": "ruolo", "valore": "backend engineer"},
 {"chiave": "linguaggio_preferito", "valore": "Go"},
 {"chiave": "fuso_orario", "valore": "Europe/Rome"}]
```

| Variabile | Cosa decide |
| --- | --- |
| `MEMORY_MAX_FACTS` | quanti fatti entrano nel contesto (default 30) |

Il tetto non è un dettaglio: senza, il contesto di ogni run crescerebbe con
tutto ciò che si è mai saputo dell'utente. Manca ancora il **decadimento**: un
fatto vecchio e mai più confermato pesa quanto uno di ieri.

## Ricerca semantica: raggiungere quello che non è più nel contesto

Riassunto e fatti sono compressione: tengono il poco che vale sempre. La
ricerca serve al caso opposto — un dettaglio preciso di sei conversazioni fa,
che non merita di stare nel contesto di ogni run ma serve *adesso*.

**Su Redis, non su Mongo.** `$vectorSearch` esiste solo su Atlas; da Redis 8 il
Query Engine con i vector set sta nella distribuzione open source. Redis c'era
già per la coda calda, quindi la ricerca per significato non aggiunge un terzo
datastore — il *tech sprawl* è uno dei tranelli elencati da MongoDB stessa.

**Si indicizza solo ciò che esce dalla finestra.** Quello che è ancora nel
contesto il modello ce l'ha già davanti, e ritrovarglielo sarebbe ripetizione.
Fuori anche ragionamento e risultati di tool: il primo è il modello che parla
con sé stesso, i secondi si riottengono richiamando il tool.

**Un tool, non un'iniezione automatica.** Infilare a ogni run i ricordi
«probabilmente pertinenti» li paga sempre e li azzecca a volte, e più roba c'è
nel contesto meno il modello ne recupera con precisione. Con `cerca_nei_ricordi`
la memoria si raggiunge quando serve, e a decidere è il modello, che la domanda
ce l'ha davanti.

Misurato con `openai/text-embedding-3-small` su una conversazione reale:

| domanda | ricordo trovato | somiglianza |
| --- | --- | --- |
| «dove gira il cluster?» | «abbiamo scelto Kubernetes su Hetzner invece di ECS» | 0,697 |
| «quanto conserviamo i log?» | «la retention dei log la teniamo a 14 giorni» | 0,839 |
| «che vino beviamo?» | «Domanda 3 su un argomento qualunque» | 0,645 |

La prima riga è il punto: nessuna parola in comune fra domanda e ricordo.
La terza è il limite da conoscere — **la ricerca restituisce sempre qualcosa**.
Non c'è una soglia fissa perché il valore giusto cambia col modello di
embedding, e su questi dati taglierebbe la riga buona (0,697) insieme alla
spazzatura (0,645). Il segnale utile è il *distacco*: punteggi bassi e tutti
uguali significano «niente di pertinente». Per questo il tool restituisce le
somiglianze e dice al modello che sono frammenti da verificare, non certezze.

La ricerca è `POST /search` e non una GET con la domanda nell'URL: le domande
finiscono nei log di accesso dei proxy, e qui la domanda è contenuto di una
conversazione.

Cancellare un thread toglie i suoi ricordi dall'indice — le posizioni si
leggono prima di cancellare il durevole, perché dopo non ci sono più e un
ricordo rimasto sarebbe cercabile per sempre.

| Variabile | Cosa decide |
| --- | --- |
| `MEMORY_EMBEDDING_MODEL` | modello degli embedding; vuoto = nessuna ricerca semantica |

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
| `POST` | `/search` | cerca nei ricordi dello scope per significato |
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
| `MEMORY_DROP_REASONING` | togliere il ragionamento passato dal contesto (default true) |
| `MEMORY_KEEP_TOOL_RESULTS` | risultati di tool lasciati interi (default 4) |
| `MEMORY_MAX_CONTEXT_MESSAGES` | tetto di messaggi restituiti (default 60) |
| `MEMORY_SUMMARY_MODEL` | modello che riassume; vuoto = nessuna compattazione |
| `MEMORY_SUMMARY_BASE_URL` | endpoint Chat Completions del riassuntore |
| `MEMORY_SUMMARY_API_KEY` | credenziale del riassuntore |

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

- **Decadimento dei fatti**: un fatto vecchio e mai piu' confermato pesa quanto
  uno di ieri. L'articolo di riferimento suggerisce di abbassare una forza
  invece di cancellare.
- **Reindicizzazione**: se l'indice dei ricordi va perso si ricostruisce dai
  transcript, ma non c'e' ancora un comando che lo faccia.

## Contratti fra i repo

I campioni di ciò che questo repo mette sul filo — o legge da un altro — stanno
in `demo-infra/contracts`, versionati e in copia unica. I test di contratto li
caricano da lì: se manca la cartella **falliscono**, invece di saltarsi da soli.
Un test di contratto silenzioso quando la controparte non c'è è esattamente il
silenzio che i contratti tolgono.

```bash
# i quattro repo come cloni fratelli: nessuna configurazione
# altrove: AGUI_LAB_CONTRACTS=/percorso/a/demo-infra/contracts
```

Quando un campione cambia, cambia insieme in tutti i repo elencati nel suo
`produced_by` e `consumed_by`. Il messaggio di fallimento dice quali sono.

## Migrazioni

Lo schema cambia col codice, non con uno script che qualcuno deve trovare.
`memory_service/migrations.py` tiene le migrazioni numerate; il servizio le
applica all'avvio **prima** di creare gli indici, e registra in
`schema_migrations` quelle gia' fatte.

L'ordine non e' un dettaglio: un indice unico su un campo che i documenti
vecchi non hanno ammetterebbe **un documento per scope**. E' esattamente cosi'
che il servizio si e' rifiutato di partire dopo la rinomina `chiave`/`valore`,
e le due migrazioni presenti sono quella rinomina e la rimozione degli indici
coi vecchi nomi.

Se un indice non si crea e mancano migrazioni, l'errore dice **quali**, invece
di un `E11000` su un campo che nessuno riconosce.

## Ritenzione e reindicizzazione

Due operazioni di manutenzione, esposte come endpoint perche' le chiami un job
schedulato -- un CronJob, non il percorso di una richiesta.

```bash
curl -XPOST /admin/retention -H 'X-Memory-Scope: <scope>' -d '{"days": 90}'
curl -XPOST /admin/reindex   -H 'X-Memory-Scope: <scope>' -d '{"thread_id": null}'
```

**La ritenzione e' spenta di default** (`MEMORY_RETENTION_DAYS=0`): cancellare
conversazioni e' una decisione di prodotto, non un default. Quando e' accesa
dice nei log quali thread ha tolto, ed e' limitata allo scope che la chiede --
una manutenzione dentro un tenant non deve arrivare in un altro.

La **reindicizzazione** ricostruisce l'indice vettoriale dai transcript, che ne
sono la fonte. Era una proprieta' dichiarata del progetto e non aveva un
comando: perdere Redis deve costare una ricostruzione, non i ricordi.
