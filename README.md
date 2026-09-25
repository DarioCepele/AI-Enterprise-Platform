# AI Enterprise Platform

Una piattaforma agentica che funziona, da cui partire per costruire la propria.
Non un esempio "hello world" e non un framework: nove servizi che girano insieme
con `docker compose up`, dove un agente conversa in streaming, delega a
sottoagenti remoti, ricorda le conversazioni passate, ascolta e risponde a voce,
guarda i video che gli carichi e usa strumenti esterni via MCP.

È pensata per essere **forkata**. Si clona, si cambiano delle variabili, si
scrivono i propri agenti — e l'ossatura (protocolli, contratti, migrazioni,
sonde di salute) resta quella.

## Cosa ci trovi dentro

| Cartella | Cosa fa |
|---|---|
| `demo-master-agent` | L'agente principale. Python, Microsoft Agent Framework, FastAPI. Espone `POST /agui`: uno stream SSE in protocollo AG-UI che porta testo, ragionamento, chiamate a tool e stato. |
| `demo-frontend` | Next.js. Chat, piano di lavoro ed event inspector, tutti e tre alimentati da quell'unico stream. |
| `demo-knowledge-agent` | Un sottoagente d'esempio, raggiunto via **A2A**: risponde su un corpus locale e sa fermarsi a chiedere chiarimenti invece di indovinare. |
| `demo-analysis-agent` | Il secondo sottoagente: misura e confronta numeri, e dice anche cosa quei numeri *non* dicono. |
| `demo-memory-service` | La memoria delle conversazioni: transcript, riassunti, ricerca semantica su pgvector. Sopravvive ai riavvii. |
| `demo-process-service` | Processi durevoli. Le definizioni sono dati versionati, le istanze riprendono da dove erano rimaste. |
| `demo-voice-service` | Voce in tempo reale, tutta auto-ospitata: Silero per capire quando parli, faster-whisper per trascrivere, Kokoro per rispondere. Con barge-in: se lo interrompi, smette. |
| `demo-scraping-mcp` | Un server MCP che apre una pagina in un browser vero e la restituisce in Markdown. Serve anche da esempio di come si collega un server MCP a un agente. |
| `demo-infra` | Il `compose.yaml` che tiene su tutto, e i contratti condivisi in `contracts/`: i payload che passano da un servizio all'altro, con scritto chi li produce e chi li consuma. |

Ogni cartella ha il suo README con i dettagli e le scelte che la riguardano.

## Come sta insieme

Il punto di tutto è **un solo stream**. La chat, il piano di lavoro e l'event
inspector non sono tre integrazioni: sono tre letture degli stessi eventi AG-UI.
Aggiungere una quarta vista significa consumare lo stesso stream, non ricablare
il backend — ed è esattamente così che è stata aggiunta la voce.

Da lì in fuori l'agente ha tre modi diversi di allargarsi, e la differenza conta:

- **Tool nativi**, scritti in Python dentro il master agent. Per quello che è
  logica tua.
- **Sottoagenti via A2A**, processi separati con la propria vita. Per quando
  serve un altro agente, non un'altra funzione.
- **Server MCP**, esterni e intercambiabili. Per quello che qualcun altro ha
  già scritto meglio di te.

I contratti in `demo-infra/contracts/` sono JSON con un campo `consumed_by`.
Servono a rendere rumoroso quello che altrimenti sarebbe silenzioso: se cambi
la forma di un payload, i test di contratto dell'altro lato si accorgono prima
che se ne accorga un utente.

## Farlo partire

Serve Docker e un file `.env` (parti da `demo-infra/.env.example`). Poi:

```
cd demo-infra
docker compose up -d
```

L'interfaccia è su `http://localhost:3000`, l'agente su `http://localhost:8000`.
Gli altri servizi stanno sul loopback, non esposti: si raggiungono fra loro
sulla rete di compose.

Per lavorare su un singolo servizio senza tirare su tutto, ogni cartella Python
usa `uv` (`uv sync && uv run pytest`) e il frontend `npm`.

## Le scelte che spiegano tutto il resto

Tre decisioni, prese controvoglia e per motivi concreti, che condizionano il
resto del codice più di qualsiasi altra cosa.

**Nessun fornitore obbligatorio.** I modelli passano da OpenRouter perché così
restano sostituibili: il "cervello" dell'agente è una variabile d'ambiente, non
una dipendenza. È anche il motivo per cui la voce è una pipeline a cascata
auto-ospitata invece di un servizio vocale hosted: un modello nativo
speech-to-speech avrebbe risposto più in fretta e con una prosodia migliore, ma
avrebbe fuso trasporto, riconoscimento e ragionamento dentro un fornitore solo.
Abbiamo scelto la latenza peggiore e la libertà.

**Un solo datastore.** Postgres, con più database dentro. Quando è servita la
ricerca semantica non è arrivato un database vettoriale nuovo: è arrivato
pgvector nel servizio di memoria che c'era già.

**La configurazione non mente.** Le tabelle delle variabili d'ambiente nei
README sono generate dai campi veri del codice, non scritte a mano — perché una
tabella scritta a mano inizia a mentire alla seconda modifica.

## Cosa non c'è, di proposito o non ancora

Vale la pena essere espliciti, perché una piattaforma "enterprise" senza queste
cose non è pronta per la produzione e far finta del contrario non aiuta nessuno.

**Non c'è autenticazione.** Esiste il concetto di `scope` per separare i dati, e
il codice assume che davanti ci sia qualcosa che l'ha verificato — ma quel
qualcosa non è in questo repository. Niente utenti, niente ruoli, niente SSO.

**Non c'è un backoffice.** Tutto quello che decide il comportamento della
piattaforma vive in variabili d'ambiente, e tutto quello che ha fatto scorre nei
log. Non c'è una superficie da cui un amministratore veda le run passate,
approvi un'azione a rischio o spenga un tool senza toccare il codice.

**Non c'è un registry di agenti e tool** con ricerca semantica: la scoperta
oggi è una lista in configurazione.

**La CI non gira.** I workflow esistono nelle sottocartelle, ma GitHub Actions
legge solo `.github/workflows/` alla radice: finché non vengono consolidati lì,
nessun controllo parte. È il primo lavoro utile per chi vuole contribuire.

## Nota sulla struttura

Questi nove progetti sono nati come repository separati e sono stati uniti in
uno solo, conservando tutta la storia: i commit radice originali sono ancora
antenati di `main`. Per vedere la storia di un file *prima* dell'unione serve
il percorso che aveva allora e `--full-history`:

```
git log --full-history -- src/demo/config.py
```
