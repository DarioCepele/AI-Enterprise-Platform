# Knowledge agent

Sottoagente del laboratorio AG-UI, esposto via **A2A**. Il master agent lo
interroga come agente remoto, non come tool locale: è il pezzo che rende la
tappa 3 un sistema multi-agente e non un agente con più funzioni.

```powershell
uv sync
uv run python -m knowledge      # http://127.0.0.1:8200
```

Risponde leggendo un piccolo corpus di documenti Markdown (`src/knowledge/corpus/`).
Il contenuto non è il punto: serve che le risposte costino tempo vero, così
streaming e parallelismo si misurano invece di immaginarli.

## I due default che disattivano lo streaming in silenzio

La spec li dichiarava da uno spike precedente. Rifatti qui, contro questo
servizio, con `qwen/qwen3.8-27b`:

| Configurazione | Risultato |
| --- | --- |
| `A2AAgent(url=...)` | **1 update**, tutto insieme a +8,62 s |
| `A2AAgent(agent_card=card)` | **62 update**, il primo a +2,77 s, totale 4,16 s |

Chi costruisce il client con il solo `url` non riceve un errore né un warning:
riceve una risposta sola alla fine. La card va scaricata a mano da
`/.well-known/agent-card.json` e passata come `agent_card`.

Lato server vale l'altro default:

```python
A2AExecutor(agent=agent, stream=True)   # il default e' stream=False
```

E la card deve dichiarare `AgentCapabilities(streaming=True)`, altrimenti il
client non prova nemmeno.

**Parallelismo, misurato:** due interrogazioni insieme **6,97 s** contro
**13,77 s** in serie. È la ragione per cui la tappa 3 esiste.

## Una trappola in più, che la spec non aveva

Con `a2a-sdk` 1.x i tipi sono **protobuf**, non pydantic, e la card ha
`supported_interfaces` al posto di `url` + `preferred_transport`. La versione
dichiarata nell'interfaccia decide il trasporto del client: con `0.3.0` il
client sceglie il transport di compatibilità v0.3, che chiama `message/stream`,
mentre il server 1.x espone metodi in stile gRPC (`SendStreamingMessage`) — e
si ottiene `MethodNotFoundError: Method not found`. La card dichiara `1.0`.

## Test

```powershell
uv run pytest
```

Offline: verificano la carta d'identità (streaming dichiarato, versione non
legacy, url coerente) e il tool di lettura del corpus. La verifica dello
streaming vero richiede un modello e sta nei numeri qui sopra.

## L'executor è nostro, e l'artefatto ha un nome

`agent-framework-a2a` non è più una dipendenza: l'executor sta in
`knowledge/executor.py`, scritto contro l'SDK stabile. Due guadagni in un colpo
solo — via un pacchetto beta dal percorso portante, e il controllo su **cosa**
il sottoagente restituisce.

Prima ogni chunk di testo partiva come artefatto anonimo: 205 artefatti per una
risposta, nessuno con un nome, nessun dato. Ora il testo scorre come messaggio
di stato mentre il task è `WORKING`, e alla fine parte **un** artefatto
`briefing`, con una parte testo per il modello e una parte dati per l'interfaccia:

```json
{"component": "briefing", "question": "...", "documents": ["go"], "summary": "..."}
```

Il master lo rende come artefatto in timeline, con le fonti sotto — non come
testo indistinguibile dal resto.

### Tre cose che solo il campo ha detto

**Il Task va messo in coda prima di tutto.** `TaskUpdater.submit()` non basta:
senza `new_task_from_user_message()` enqueued per primo, il client riceve
`InvalidAgentResponseError: Agent should enqueue Task before TaskStatusUpdateEvent`.

**Gli argomenti di una tool call arrivano a delta.** Il primo pezzo porta il
*nome* del tool e argomenti vuoti; i pezzi dopo portano gli *argomenti* e
nessun nome:

```
name='read_document' call_id='ee79b6' args=''
name=''                call_id='ee79b6' args='{"name": "'
name=''                call_id='ee79b6' args='go'
name=''                call_id='ee79b6' args='"}'
```

Chi filtra per nome a ogni pezzo scarta proprio quelli che contengono la
risposta. Si tiene traccia dei `call_id` che ci interessano e si accumulano gli
argomenti finché non diventano JSON valido.

**Un task fallito è meglio di un task vuoto.** Se l'agente non produce testo, il
task va in `FAILED` invece di completarsi senza artefatto: chi lo ha chiesto
deve poter distinguere "non ho trovato nulla" da "è andato tutto bene".

## Fermarsi e chiedere

Se la domanda è ambigua al punto che rispondere sarebbe indovinare — «come
funziona la concorrenza?», col catalogo che ha Go, Python e Rust — l'agente
risponde con la sola riga `[NEEDS-CLARIFICATION] <domanda>`. L'executor la
riconosce e mette il task in `INPUT_REQUIRED` invece di completarlo: il task
resta **aperto**, e chi lo ha chiesto può riprenderlo mandando un messaggio con
lo stesso `task_id`.

È human-in-the-loop attraverso gli agenti: la domanda risale dal sottoagente al
master, dal master alla persona, e la risposta torna giù per lo stesso task
invece di aprirne uno nuovo che avrebbe perso il contesto.

Il marcatore è testuale di proposito: un tool `ask_for_clarification` sarebbe
sembrato più pulito, ma l'agente lo avrebbe chiamato *e poi* continuato a
rispondere, perché per il modello un tool è un passo intermedio. La riga sola è
un punto di uscita.

## Le notifiche push si mandano ai punti di svolta

`BasePushNotificationSender` notifica **ogni** evento della coda. In streaming
sono decine di aggiornamenti di stato per task: 196 POST verso il master per due
domande, tutti scartati da chi li riceveva. `EssentialNotifications` (in
`knowledge/push.py`) filtra sugli stati in cui il chiamante ha davvero qualcosa
da fare — fine del task (`COMPLETED`, `FAILED`, `CANCELED`, `REJECTED`) e
`INPUT_REQUIRED`. Stesso ciclo, 2 POST.

Gli artefatti non viaggiano nella notifica: dice che il task è finito, non cosa
ha prodotto. Il risultato si va a rileggere con `get_task`.

## La card estesa: cosa non si mette in vetrina

La card pubblica dice cosa l'agente sa fare. **Quali documenti abbia
indicizzato** no: l'elenco dice di cosa si occupa chi ci lavora, ed è
esattamente il dettaglio utile a chi deve usare l'agente e non a chi passa di
lì. Sta nella card estesa, servita solo a chi presenta un token di servizio.

```
GET /extendedAgentCard                       401  WWW-Authenticate: Bearer realm="servizio"
GET /extendedAgentCard  Bearer <sbagliato>   401
GET /extendedAgentCard  Bearer <giusto>      200  skills: language-comparison, catalogue
```

Tre scelte, tutte discutibili e tutte volute.

**A chi non è autenticato la card estesa non esiste.** Sul percorso REST il 401
serve a un client legittimo, che dall'header impara cosa mandare; sulla via
JSON-RPC la risposta è `ExtendedAgentCardNotConfiguredError` — la stessa che
darebbe un agente che non ne ha una. Negare l'esistenza invece dell'accesso non
conferma a un estraneo che qui ci sia qualcosa di più da chiedere.

**Un token non configurato chiude, non apre.** Se `KNOWLEDGE_SERVICE_TOKEN` è
vuoto nessuno passa: la dimenticanza deve costare una card in meno, non una
card in più.

**`security_requirements` resta vuoto.** Metterlo direbbe che *l'agente*
richiede autenticazione, il che è falso: interrogarlo è pubblico, è la vista
estesa a non esserlo. Il token viaggia come parametro della singola chiamata.
Conseguenza pratica: l'`AuthInterceptor` dell'SDK, che si attiva proprio su
`security_requirements`, qui non serve a niente.

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
