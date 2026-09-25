# Servizio di memoria conversazionale

Possiede la memoria delle conversazioni del laboratorio AG-UI: i transcript per
thread, i riassunti, i fatti duraturi e i ricordi cercabili. Il master agent
non parla con il database: parla con questo.

```powershell
uv sync
uv run python -m memory_service      # http://127.0.0.1:8100
```

## Un database solo, e cosa ci sta dentro

| Cosa | Dove | Perche' li' |
| --- | --- | --- |
| trascritti, riassunti, fatti | tabelle di Postgres | devono sopravvivere a tutto |
| ricordi cercabili | `pgvector`, stesso database | l'indice sta accanto ai dati da cui si ricostruisce |
| lock di compattazione | `pg_try_advisory_lock` | muore con la connessione, senza lease da far scadere |

**C'era una coda calda in Redis**, gli ultimi N messaggi del thread con una
scadenza automatica, e la regola era che in cache non vivesse mai l'unica
copia di un dato. Reggeva, ma pagava due copie e un ordine di scrittura da
rispettare per una lettura che una query indicizzata su `(scope, thread_id,
seq)` fa in poche centinaia di microsecondi. Il datastore in meno vale piu'
della cache.

`GET /health` risponde su una cosa sola: Postgres giu' e' **guasto** (503).
Non c'e' piu' un modo parziale di funzionare, il che e' una semplificazione,
non una perdita.

## Una riga per turno, e il bucket che non c'e' piu'

Il durevole era MongoDB, e i messaggi stavano in **documenti bucket** da 50:
un array che cresce senza limite finisce contro il tetto di 16 MB, quindi si
impacchettava. Creare un bucket senza transazioni voleva l'indice unico
`(scope, thread_id, bucket)`, una `find_one_and_update` che cercava il bucket
non pieno, e un ritentativo per lo scrittore perdente.

Tutto questo esisteva per **far somigliare un database a documenti a una
tabella**. In Postgres una riga per messaggio e' gia' la forma economica, e
l'append e' una sola istruzione:

```sql
WITH position AS (
    INSERT INTO threads (scope, thread_id, next_seq) VALUES (%s, %s, 1)
    ON CONFLICT (scope, thread_id) DO UPDATE SET next_seq = threads.next_seq + 1
    RETURNING next_seq
)
INSERT INTO thread_turns (scope, thread_id, seq, message)
SELECT %s, %s, next_seq, %s FROM position
RETURNING seq, ts
```

Numero e riga nascono insieme: due scrittori sullo stesso thread non possono
prendere la stessa posizione, e un fallimento non lascia ne' l'uno ne' l'altra.
Verificato con dodici append simultanei, come prima -- la proprieta' e' la
stessa, il meccanismo e' una riga di SQL invece di trecento di Python.

**Cosa resta JSON:** il contenuto del turno. Ruoli, payload e campi specifici
del provider variano davvero, e quella varieta' e' reale. Cosa diventa colonna:
scope, thread e posizione — cioe' quello su cui si cerca.

| Tabella | Chiave | A cosa serve |
| --- | --- | --- |
| `threads` | `(scope, thread_id)` | contatore, stato non-messaggio, checkpoint dell'indice |
| `thread_turns` | `(scope, thread_id, seq)` | i turni, in ordine |
| `thread_summaries` | `(scope, thread_id, covers_to_seq)` | i riassunti, tutti |
| `scope_facts` | `(scope, key)` | i fatti duraturi di uno scope |

I turni e i riassunti se ne vanno con il thread per **chiave esterna**: una
dichiarazione al posto di tre cancellazioni da tenere allineate a mano.

### Perche' non Mongo

Il modello a documenti vince quando i dati sono entita' autocontenute lette
tutte insieme, o quando serve sharding nativo per scritture oltre un nodo. Un
turno di conversazione non e' nessuna delle due cose: e' **append-only,
uniforme, letto a coda** — una serie temporale, cioe' casa di SQL. La prova era
nel nostro stesso codice: il bucket pattern.

Tornerebbe la risposta giusta con scritture che superano quello che regge un
nodo, o con turni la cui forma cambia davvero da provider a provider e su cui
non si cerca mai. Nessuna delle due e' il caso, oggi.

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
`session_state` — sta nella riga del thread, non nei turni: è un valore
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

**Su Redis, per ora.** Da Redis 8 il Query Engine con i vector set sta nella
distribuzione open source, e Redis c'era gia' per la coda calda: la ricerca per
significato non ha aggiunto un datastore.

Il posto naturale, ora che il durevole e' Postgres, e' **pgvector**: l'indice
starebbe accanto ai trascritti da cui si ricostruisce, e i datastore
scenderebbero a uno. E' la seconda tappa di questo lavoro, non ancora fatta.

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
finisce nella chiave del durevole **e** in quella di Redis, cosi' due scope non si
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
| `GET` | `/threads/{id}/messages?limit=N` | ultimi N turni, in ordine |
| `DELETE` | `/threads/{id}` | dimentica il thread: turni, riassunti, fatti, ricordi |

Le cancellazioni sono a cascata: una chiave esterna con `ON DELETE CASCADE`
porta via turni, riassunti, fatti e ricordi insieme al thread, invece di
quattro cancellazioni che possono riuscire a meta'.

## Configurazione

Variabili con prefisso `MEMORY_`, dal `.env` locale non versionato:

| Variabile | Uso |
| --- | --- |
| `MEMORY_POSTGRES_DSN` | DSN completo, credenziali comprese: trascritti, riassunti, fatti, ricordi |
| `MEMORY_PORT` | porta di ascolto in locale (default 8100) |
| `MEMORY_POOL_MIN_SIZE` | connessioni tenute aperte (default 1) |
| `MEMORY_POOL_MAX_SIZE` | connessioni al massimo (default 10) |
| `MEMORY_RETENTION_DAYS` | giorni di inattivita' dopo cui un thread si dimentica; 0 = mai |
| `MEMORY_DROP_REASONING` | togliere il ragionamento passato dal contesto (default true) |
| `MEMORY_KEEP_TOOL_RESULTS` | risultati di tool lasciati interi (default 4) |
| `MEMORY_MAX_CONTEXT_MESSAGES` | tetto di messaggi restituiti (default 60) |
| `MEMORY_MAX_FACTS` | fatti durevoli iniettati nel contesto (default 30) |
| `MEMORY_SUMMARY_MODEL` | modello che riassume; vuoto = nessuna compattazione |
| `MEMORY_SUMMARY_BASE_URL` | endpoint Chat Completions del riassuntore |
| `MEMORY_SUMMARY_API_KEY` | credenziale del riassuntore |
| `MEMORY_EMBEDDING_MODEL` | modello degli embedding; vuoto = nessuna ricerca semantica |
| `MEMORY_JSON_LOGS` | log strutturati invece della riga leggibile |

Il database si alza dal compose di `demo-infra`:

```bash
cd ../demo-infra && docker compose up -d postgres
```

## Test

```powershell
uv run pytest
```

Girano contro un Postgres **vero**, non simulato: l'atomicita' senza
transazioni si regge su garanzie del server — `INSERT ... RETURNING` in una
sola istruzione, indice unico, advisory lock — che un finto non riproduce, e che sono esattamente la parte da
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
