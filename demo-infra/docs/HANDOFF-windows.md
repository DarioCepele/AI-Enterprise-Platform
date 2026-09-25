# Prompt di passaggio — sessione Windows

Copia tutto quello che segue nella nuova sessione.

---

Lavoriamo in `C:\project\demo`. Tu giri su Windows con Docker Desktop e LM Studio;
la sessione precedente girava in WSL e non riusciva a raggiungere né il daemon
Docker né LM Studio. Per questo il testimone passa a te.

## Cos'è il progetto

Una demo di studio: un'interfaccia agentica in cui **chat, stato condiviso ed
event inspector sono tre viste dello stesso stream SSE** in protocollo AG-UI.
Ricostruisce in locale un laboratorio interno visto in una registrazione.

Non è codice di produzione. L'obiettivo è capire come funzionano AG-UI, A2A e
Microsoft Agent Framework, quindi si privilegiano leggibilità e confini netti
sulla velocità.

Documenti, entrambi da leggere prima di toccare qualsiasi cosa:

- `demo-infra\docs\specs\2026-09-07-agui-lab-design.md` — design e vincoli misurati
- `demo-infra\docs\plans\2026-09-07-tappa-1-walking-skeleton.md` — i 9 task, tutti completati

## Struttura: quattro repo git separati, fratelli

```
C:\project\demo\                 cartella di lavoro, NON un repo
  demo-master-agent\             agente principale, endpoint AG-UI su SSE (Python)
  demo-frontend\                 interfaccia Next.js
  demo-infra\                    compose.yaml, .env, documentazione
  demo-knowledge-agent\          sottoagente A2A — tappa 3, non esiste ancora
```

Ognuno ha la sua storia git. Non esiste un repo che li contiene: è voluto,
rispecchia la piattaforma di riferimento. `compose.yaml` costruisce da percorsi
fratelli (`../demo-master-agent`), quindi i repo devono restare dove sono.

## Stato attuale

La tappa 1 (walking skeleton) è **completa**: 9 task su 9, tutti committati,
working tree puliti.

Verificato:

- `demo-master-agent`: `uv run pytest` → 18 passed
- `demo-frontend`: `npm test` → 20 passed, `npx tsc --noEmit` pulito, `npm run build` ok
- stream SSE reale: `RUN_STARTED … TEXT_MESSAGE_CONTENT × n … RUN_FINISHED`
- preflight CORS da `localhost:3000` e `localhost:3001`

**Non** verificato, ed è il tuo compito:

- che le immagini Docker si costruiscano e girino insieme via compose
- che la pagina funzioni davvero in un browser
- che il backend parli con LM Studio

## Il tuo compito

Far girare la demo **su Docker**, con **LM Studio** come modello.

```powershell
cd C:\project\demo\demo-infra
copy .env.example .env
# poi scommenta e completa il blocco LM Studio in .env
docker compose up --build
# apri http://localhost:3000
```

### Tre ostacoli noti, in ordine di probabilità

1. **LM Studio ascolta solo su `127.0.0.1`.** Dentro un container `localhost` è il
   container stesso, e `host.docker.internal` punta all'host ma non a una porta
   legata al solo loopback. Va attivato **"Serve on Local Network"** nelle
   impostazioni del server di LM Studio (bind `0.0.0.0`), e Windows Firewall va
   autorizzato quando lo chiede. `compose.yaml` ha già
   `extra_hosts: host.docker.internal:host-gateway`.

2. **L'id del modello deve essere esatto.** LM Studio ha il caricamento
   just-in-time: un id sbagliato è un errore, non un fallback. Leggilo da
   `curl http://localhost:1234/v1/models` e mettilo in
   `OPENAI_CHAT_COMPLETION_MODEL`.

3. **Il modello caricato è Gemma 4 12B, che non ha tool-calling affidabile.**
   La chat in streaming funzionerà; il tool `ui_table` con ogni probabilità non
   verrà mai chiamato, quindi il pannello "Stato condiviso" resterà vuoto.
   **Non è un bug**: è il limite già scritto nella spec per il profilo LM Studio.
   Se vuoi vedere anche gli eventi `TOOL_CALL_*`, carica in LM Studio un modello
   con tool-calling reale (Qwen3, Llama 3.3, Mistral recenti) e riprova.

### Cosa deve succedere se funziona

Su `http://localhost:3000`, scrivendo un messaggio:

- la risposta compare **progressivamente**, non tutta insieme
- l'inspector si popola con `RUN_STARTED`, `TEXT_MESSAGE_*`, `MESSAGES_SNAPSHOT`,
  `RUN_FINISHED`
- i filtri `tutti` / `testo` / `tool` / `stato` restringono la lista
- **nessuna bolla vuota** in chat
- nessun errore CORS in console

## Vincoli da rispettare, non negoziabili

Sono stati tutti pagati con un errore. Li trovi anche in cima al piano, sezione
"Global Constraints".

- `agent-framework-core==1.17.0`. Import sotto namespace `agent_framework.*`
  (`agent_framework.openai`, `.ag_ui`, `.a2a`). I moduli top-level
  `agent_framework_openai` / `agent_framework_a2a` sono la forma vecchia.
- Le classi di contenuto per-variante **non esistono** in 1.17: si usa `Content`
  con le factory (`Content.from_text`, `Content.from_function_call`, …).
  Se un esempio online usa `TextContent`, è per una versione precedente.
- `Message` **non accetta** `text=`: si costruisce con
  `Message(role=..., contents=[Content.from_text("x")])`.
- L'endpoint AG-UI si monta con `add_agent_framework_fastapi_endpoint`.
  **Non scrivere un mapper di eventi a mano**: se compare un file che costruisce
  eventi AG-UI manualmente, è da buttare.
- `add_agent_framework_fastapi_endpoint(..., allow_origins=...)` accetta il
  parametro e **lo ignora** ("not yet implemented" nella sua docstring): il CORS
  è montato a mano con `CORSMiddleware`, e le origini vengono da
  `DEMO_ALLOWED_ORIGINS`.
- Un chat client che deve eseguire tool **deve** ereditare da
  `agent_framework._tools.FunctionInvocationLayer` oltre che da `BaseChatClient`,
  in quest'ordine.
- `state_update()` non espone i payload come attributi di `Content`: finiscono in
  `additional_properties` sotto `__ag_ui_tool_result_state__` (dict) e
  `__ag_ui_tool_result_display__` (**stringa JSON**).
- Il JSON di AG-UI è camelCase (`threadId`, `runId`, `messageId`, `delta`).
- `node_modules` contiene binari di piattaforma: se la stessa cartella è stata
  usata da WSL, rilancia `npm install` su Windows prima di eseguire i test.

## Come lavorare

Un commit per unità di lavoro, nel repo giusto. Se cambi il comportamento,
prima il test. Se un test fallisce, riporta l'output reale invece di riassumerlo.

Se scopri che un vincolo qui sopra è sbagliato, dillo: sono già stati corretti
tre errori del piano proprio così, eseguendolo.

## Se tutto gira

Il passo successivo è la tappa 2: piano di lavoro (`todo_write` /
`todo_set_status` come stato condiviso), skill in formato `SKILL.md`, e la
tabella comparativa resa in UI. Il design c'è già nella spec, il piano operativo
no. Non iniziarla senza averne parlato.
