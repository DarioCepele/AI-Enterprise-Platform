# Servizio voce

Voce in tempo reale, con modelli che girano in locale. Una pipeline
[Pipecat](https://github.com/pipecat-ai/pipecat) a cascata su `/ws/voice`:
l'audio del browser entra dal WebSocket, Silero decide quando stai parlando,
faster-whisper trascrive, il turno va all'endpoint AG-UI del master come un
messaggio qualunque, Kokoro-82M legge la risposta frase per frase mentre
arriva. Se lo interrompi a metà, smette.

È un work in progress: funziona, ma il modello della risposta parlata sta per
cambiare (vedi il README principale).

## Avviarlo e provarlo

```bash
uv sync
uv run python -m voice_service      # :8500
curl http://127.0.0.1:8500/health/ready
uv run pytest
```

Metà dei test lavora su audio vero: al primo giro scarica i modelli di
riconoscimento e sintesi, qualche centinaio di MB, e alcuni avviano un master
agent dalla cartella accanto. La CI esegue quelli che non ne hanno bisogno
(`test_approvals.py`, `test_boundaries.py`, `test_health.py`).

`python -m voice_service` parte con `platform_core.runtime.serve`, come tutti
i servizi: carica il `.env` e, su Windows, usa il loop di eventi a selector,
che i socket asincroni della pipeline richiedono.

## Configurazione

La tabella completa, generata dal codice, sta nel
[README dell'infrastruttura](../demo-infra/README.md#voice-service). Le
variabili che contano per prime:

| Variabile | Cosa decide |
|---|---|
| `VOICE_MASTER_AGENT_URL` | Il master agent a cui va ogni turno. |
| `VOICE_ALLOWED_ORIGINS` | Le pagine che possono aprire il microfono (vedi sotto). |
| `VOICE_STT_MODEL`, `VOICE_STT_LANGUAGE` | Il modello di faster-whisper e la lingua di chi parla (vuota: riconosciuta a ogni turno). |
| `VOICE_TTS_LANG_CODE`, `VOICE_TTS_VOICE` | La lingua e la voce di Kokoro: `a`/`af_heart` per l'inglese, `i`/`if_sara` per l'italiano. |
| `VOICE_APPROVAL_NOTICE` | Cosa dice quando una richiesta ha bisogno di un'approvazione (vedi sotto), nella lingua della voce. |

## Cosa passa sul WebSocket

Dal browser: audio PCM a 16 bit, 16 kHz, mono, in frame binari. Verso il
browser, messaggi JSON distinti da `type`:

| `type` | Quando |
|---|---|
| `user_transcript` | la trascrizione del turno appena finito |
| `assistant_text_chunk` | una frase della risposta, mentre arriva |
| `assistant_audio_chunk` | l'annuncio di un frame binario di audio PCM che segue subito |
| `assistant_turn_cancelled` | il turno è stato interrotto: il browser smette di riprodurlo |
| `assistant_turn_failed` | il master non ha risposto; la causa resta nei log del servizio |

## L'origine, al posto di CORS

I browser non applicano CORS ai WebSocket: qualunque pagina aperta nello
stesso browser potrebbe parlare con l'agente attraverso il microfono. Il
servizio controlla l'`Origin` contro `VOICE_ALLOWED_ORIGINS` e chiude le altre
con il codice 1008. Un client che non è un browser non manda l'`Origin`, e
passa.

## Le approvazioni, a voce

Un tool che chiede l'approvazione di una persona ferma il run su una domanda
che la voce non può mostrare, e il protocollo non lascia andare avanti la
conversazione con una domanda aperta. Il servizio la annulla, quindi non
esegue niente, e dice `VOICE_APPROVAL_NOTICE`: l'azione si chiede in chat,
dove la card mostra cosa sta per succedere.

Se il turno è stato interrotto prima di leggere la fine del run, il servizio
non ha visto la domanda: il turno successivo viene rifiutato, legge le domande
aperte dal thread, le annulla e riprova. `tests/test_approvals.py` copre
entrambi i casi.

## Modelli e immagine

torch arriva dall'indice CPU di PyTorch: nessuna libreria CUDA nell'immagine,
che resta intorno ai 6 GB invece di 20. Il modello di spaCy che la sintesi usa
è una dipendenza fissata, non un download a runtime: con il filesystem in sola
lettura non ce ne sarebbe modo. I modelli di riconoscimento e sintesi si
scaricano al primo uso in `/models` (`HF_HOME`), che il compose monta sul
volume `voice-models`: un riavvio non li riscarica.

L'immagine gira come utente non-root (uid 10001), con il filesystem in sola
lettura.

## Quanta RAM, con tutto caricato

Un dato misurato su una macchina di sviluppo Windows, non un requisito. Con i
tre modelli residenti nello stesso processo (Silero VAD, faster-whisper
`small` int8 su CPU, Kokoro-82M, ognuno caricato chiamandolo una volta davvero)
e misurato da PowerShell con `Get-Process` sul processo ancora vivo
(`uv run python scripts/measure_resources.py`):

| | Misurato |
|---|---|
| Working set | ~1,46 GB (1455,6 MB) |
| Private bytes | ~4,1 GB (4211 MB) |

La GPU non entra in gioco: torch è la build solo CPU, e niente in questo
servizio la usa. Prima di fidarsi del numero su un'altra macchina conviene
rilanciare lo script lì.
