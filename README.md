# Analysis agent

Secondo sottoagente del laboratorio, esposto via **A2A** come il primo.

```powershell
uv sync
uv run python -m analysis      # http://127.0.0.1:8400
```

## Perche' due

Con un sottoagente solo l'instradamento non esiste: qualunque domanda va
all'unico che c'e', e "sistema multi-agente" e' un modo di dire. Il secondo
serve a rendere la scelta reale, e per farlo deve occuparsi di **un'altra cosa**.

| | knowledge | analysis |
| --- | --- | --- |
| Da dove prende i dati | un corpus che qualcuno ha scritto | la richiesta stessa |
| Cosa fa | legge e cita | misura e confronta |
| Quando e' quello giusto | «cosa dicono i documenti su X» | «questi numeri cosa dicono» |
| Artefatto | `briefing` | `assessment` |

Nessuno dei due ha un dominio: sono **esempi**, e sono la prima cosa da
sostituire quando questo template diventa un prodotto.

## Cosa calcola

`measure` su una serie: quanti valori, minimo, massimo, media, mediana,
dispersione, valori isolati. `compare` su piu' opzioni: punteggio pesato sui
criteri dati, classifica, e **di quanto** vince il primo.

Due scelte che valgono piu' della formula:

**I valori isolati si misurano sulla mediana, non sulla media.** Un valore molto
grande tira media e deviazione verso di se', e poi sta dentro l'intervallo che
ha appena allargato: su una serie corta si nasconde da solo. Con
`[12, 15, 11, 14, 98]` la regola classica non segnala niente; la distanza dalla
mediana segnala `98`.

**Una serie corta lo dice** (`thin`), e due opzioni a un soffio l'una dall'altra
si dichiarano tali (`too_close`) invece di far vincere qualcuno per 0,02. Chi
riceve la risposta ci agisce: la dimensione dell'evidenza fa parte della
risposta.

## L'artefatto porta i numeri, non solo la prosa

Alla fine parte **un** artefatto `assessment`, con una parte testo per il
modello e una parte dati per l'interfaccia:

```json
{
  "component": "assessment",
  "question": "...",
  "measurements": [
    {"tool": "measure", "arguments": {"values": "[12, 15, 11, 14, 98]"},
     "result": {"count": 5, "median": 14, "outliers": [98], "thin": false}}
  ],
  "summary": "..."
}
```

Senza `measurements` sarebbe prosa su un'aritmetica che nessuno puo'
controllare, ed e' esattamente il motivo per cui un artefatto e' strutturato.
Le chiamate arrivano a delta come nell'altro agente — il nome nel primo pezzo,
gli argomenti nei successivi — e si rimettono insieme per `call_id`.

## Le stesse regole dell'altro agente

Sono le stesse perche' sono state pagate una volta e valgono per entrambi:
executor scritto a mano contro l'SDK stabile, card che dichiara `1.0` e
`streaming=True`, task in coda prima di ogni aggiornamento, task vuoto che
fallisce invece di completarsi, notifiche push solo sui punti di svolta,
`[NEEDS-CLARIFICATION]` per fermarsi e chiedere.

La card estesa qui non nasconde un catalogo ma **come misura**: dove decide che
una serie e' troppo corta e due opzioni troppo vicine e' quello che serve a chi
deve fidarsi di un numero, e non a chi passa di li'.

```
GET /extendedAgentCard                    401  WWW-Authenticate: Bearer realm="service"
GET /extendedAgentCard  Bearer <giusto>   200  skills: numbers, methods
```

## Test

```powershell
uv run pytest
```

Offline: card, aritmetica, executor e contratto. I campioni di contratto stanno
in `demo-infra/contracts` e i test li caricano da li': se la cartella manca
**falliscono**, invece di saltarsi da soli.
