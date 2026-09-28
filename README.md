# AI Enterprise Platform

Nove servizi che partono con un comando e ti danno un agente con cui parlare
a voce, che delega il lavoro ad altri agenti, ricorda le conversazioni di
settimana scorsa e guarda i video che gli carichi.

Serve per non ricominciare da zero. Si clona, si cambiano delle variabili, si
scrivono i propri agenti.

## Cosa puoi fare appena parte

Apri `localhost:3000` e scrivi. La risposta arriva in streaming, e accanto
vedi due cose che normalmente restano nascoste: il piano di lavoro che
l'agente si è dato, che si aggiorna mentre procede, e l'elenco degli eventi
grezzi che sta emettendo mentre li emette.

C'è un microfono, e funziona come ti aspetti: parli, risponde con la voce, e
se lo interrompi a metà di una frase smette invece di finire il discorso da
solo. Questa parte è work in progress: funziona, ma il modello che genera la
risposta parlata sta per cambiare (vedi sotto).

Un video caricato viene trascritto e descritto scena per scena. Le domande
successive su quel video trovano risposta senza rianalizzarlo.

Fagli una domanda fuori dal suo dominio e la gira a un sottoagente. Se la
domanda è ambigua quel sottoagente si ferma e chiede un chiarimento, che
risale fino a te, invece di tirare a indovinare.

Un indirizzo web lo apre in un browser vero, con JavaScript eseguito, per
leggerti la pagina.

Poi spegni tutto, riaccendi, e la conversazione è dove l'avevi lasciata.

## Com'è fatto

| Cartella | |
|---|---|
| `demo-master-agent` | L'agente principale. Espone uno stream SSE in protocollo AG-UI: testo, ragionamento, chiamate a tool e stato passano tutti da lì. |
| `demo-frontend` | Next.js. Chat, piano di lavoro ed event inspector sono tre letture dello stesso stream, non tre integrazioni. |
| `demo-knowledge-agent` | Sottoagente d'esempio raggiunto via A2A: risponde su un corpus locale e sa fermarsi a chiedere. |
| `demo-analysis-agent` | Il secondo sottoagente: misura e confronta numeri, e dice anche cosa quei numeri non dicono. |
| `demo-memory-service` | Transcript, riassunti e ricerca semantica su pgvector. |
| `demo-process-service` | Processi durevoli: le definizioni sono dati versionati, le istanze riprendono da dove erano rimaste. |
| `demo-voice-service` | Voce in tempo reale, interamente auto-ospitata: Silero per capire quando parli, faster-whisper per trascrivere, Kokoro per rispondere. Work in progress. |
| `demo-scraping-mcp` | Un server MCP che apre le pagine in un browser headless. Serve anche da esempio di come si collega un server MCP a un agente. |
| `demo-infra` | Il `compose.yaml` che tiene su tutto, i manifest Kubernetes, e i contratti condivisi fra i servizi. |
| `packages/platform-core` | Il codice che i servizi Python condividono: osservabilità, migrazioni, controllo degli URL, notifiche push, store A2A, MCP, CORS, il modello finto. |

Ogni cartella ha il suo README con i dettagli. [SECURITY.md](SECURITY.md) dice
cosa il template protegge e cosa lascia a chi lo adotta,
[CHANGELOG.md](CHANGELOG.md) cosa cambia fra una versione e l'altra. Licenza:
[Apache-2.0](LICENSE).

L'agente ha tre modi di allargarsi, e la differenza conta quando scrivi il
tuo: i tool nativi in Python per la logica che è tua, i sottoagenti A2A per
quando serve un altro agente e non un'altra funzione, i server MCP per
quello che qualcun altro ha già scritto.

## Farlo partire

Serve Docker e un `.env`, da copiare da `demo-infra/.env.example` riempiendo i
segreti che chiede (ognuno con `openssl rand -hex 24`).

```
cd demo-infra
docker compose up -d
```

L'interfaccia sta su `localhost:3000`, l'agente su `localhost:8000`. Gli
altri servizi restano sul loopback e si parlano sulla rete di compose.

Per guardarsi intorno senza una chiave API, `FAKE_MODEL=true` nel `.env`: ogni
agente risponde con un testo fisso, e tutto il resto -- piano, eventi, processi,
memoria -- funziona davvero.

Per lavorare su un servizio senza tirare su il resto: `uv sync && uv run
pytest` nelle cartelle Python, `npm ci && npm test` nel frontend. Il resto è in
[CONTRIBUTING.md](CONTRIBUTING.md).

## Perché è fatto così

Il modello è una variabile d'ambiente. Le chiamate passano da OpenRouter, e
cambiare cervello all'agente significa cambiare una riga di configurazione.
Lo stesso principio spiega la voce: una pipeline a cascata auto-ospitata
risponde più lentamente di un modello nativo speech-to-speech, ma quel
modello nativo avrebbe messo trasporto, riconoscimento e ragionamento dentro
un fornitore solo.

Il prossimo passo sulla voce mette alla prova quella scelta.
[PhoneLLM](https://www.cosmonet.info/phonellm-agenti-vocali-open-source-2026/)
è un modello del team di Pipecat, la stessa libreria che questa pipeline già
usa, ottimizzato per il tempo che passa prima della prima parola e per le
chiamate a tool. Sostituisce solo l'LLM dentro la cascata, non il
riconoscimento né la sintesi, che è precisamente il pezzo che la cascata
teneva sostituibile. Prima di adottarlo restano da verificare tre cose: è
alpha, i numeri di latenza dichiarati presuppongono una GPU NVIDIA B200, e
non è stato provato in italiano.

I dati stanno tutti in Postgres. Quando è servita la ricerca semantica non è
arrivato un database vettoriale nuovo: è arrivato pgvector nel servizio di
memoria che c'era già.

Le tabelle delle variabili d'ambiente nei README sono generate dai campi del
codice e un controllo in CI fallisce se divergono.

## Cosa manca

L'autenticazione, per scelta. Esiste il concetto di `scope` per separare i
dati, e il codice assume che davanti ci sia qualcosa che l'ha verificato, ma
quel qualcosa non sta qui dentro: nessun utente, nessun ruolo, nessun SSO. Dove
e come attaccarlo è in [SECURITY.md](SECURITY.md).

Un backoffice, cioè una superficie da cui rivedere le run passate o spegnere un
tool senza toccare il codice. Le azioni a rischio invece si approvano già: un
tool configurato per farlo si ferma, e la chat mostra cosa sta per fare.

Un registry di agenti e tool con ricerca semantica: la scoperta oggi è una
lista in configurazione.

## Una nota sulla storia

Questi nove progetti sono nati come repository separati e sono stati uniti,
conservando tutta la storia: i commit radice originali sono ancora antenati
di `main`. Per vedere la storia di un file prima dell'unione serve il
percorso che aveva allora, con `--full-history`. Il pacchetto del master, per
esempio, si chiamava `demo`:

```
git log --full-history -- src/demo/config.py
```
