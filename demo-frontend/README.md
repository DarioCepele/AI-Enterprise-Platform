# Il frontend

Next.js. Parla con il master agent via **AG-UI su SSE**, col servizio dei
processi via HTTP e con quello della voce via WebSocket.

```bash
npm ci
npm run dev      # http://localhost:3000
npm test         # vitest
npm run lint && npx tsc --noEmit && npm run build
```

Serve un master agent in ascolto: `AGUI_URL` (default
`http://127.0.0.1:8000/agui`). Con `PROCESS_URL` compare anche il tab delle
istanze, con `VOICE_URL` il microfono; senza, lo dicono.

## La configurazione si legge quando la pagina gira, non quando è stata costruita

`next build` inlinea le `NEXT_PUBLIC_*` nel bundle: un'immagine costruita
contro staging punta a staging per sempre, e promuoverla in produzione
vorrebbe dire ricostruirla -- cioè non promuoverla. Qui il server legge
l'ambiente a ogni richiesta e passa i valori alla pagina in uno `<script>`; le
variabili di build restano solo come ripiego per `next dev`.

Sta in `lib/runtime-config.ts`. L'elenco delle variabili, con default e
significato, sta in `lib/runtime-env.json`, da cui si genera la tabella del
[README dell'infrastruttura](../demo-infra/README.md#frontend); i test
controllano che il JSON e il codice dicano la stessa cosa. Nome, testi e badge
sono tutti variabili: il frontend di un fork non si modifica per cambiarli.

## La sicurezza della pagina

`proxy.ts` mette su ogni pagina una Content Security Policy costruita per
richiesta, con un nonce nuovo ogni volta, e gli header di hardening
(`nosniff`, `frame-ancestors 'none'`, il microfono solo per questa pagina). Per
richiesta perché due dei suoi ingredienti esistono solo allora: il nonce, e
gli indirizzi dei servizi, che arrivano dall'ambiente come il resto della
configurazione. Dove si discosta dalla guida di Next.js, e perché, è scritto
in `lib/security-headers.ts`.

Il modello risponde in Markdown, e il Markdown viene reso senza script né URL
pericolosi: la CSP è la seconda linea, non la prima.

I servizi stanno su altre origini, quindi ogni chiamata è cross-origin e di
default non porta cookie. Con un proxy di identità a cookie davanti serve
`API_CREDENTIALS=include`, e che i servizi accettino le credenziali per questa
origine (`MASTER_CORS_CREDENTIALS`, `PROCESS_CORS_CREDENTIALS`).

## Le approvazioni

Quando l'agente sta per usare un tool che chiede l'approvazione di una
persona, il run si ferma su un *interrupt* di AG-UI e la timeline mostra una
card: la domanda, il tool, gli argomenti esatti. **Approve** o **Reject**, e il
run riprende con la risposta. Se un run si ferma su più azioni, le risposte
partono insieme, come vuole il protocollo.

Finché c'è una domanda aperta non si può scrivere altro: il protocollo non lo
permette, e il campo lo dice. Se la risposta non si può applicare -- è scaduta,
l'agente è ripartito, è arrivata a un'altra replica -- la card spiega che non è
stato eseguito niente e che conviene chiedere di nuovo.

## Il tab Instances

Il terzo tab dell'ispettore guarda il **servizio dei processi**, non l'agente:
un'istanza va avanti da sola -- un agente risponde, qualcuno approva -- anche
quando in questa pagina non sta girando niente. Per questo il pannello continua
a chiedere a conversazione ferma, più piano (5 s invece di 1,5 s), e **solo
mentre lo si guarda**: un tab chiuso non ha ragione di interrogare nessuno.

Cosa mostra, in ordine di quello che serve sapere: quante istanze aspettano
**una persona**, poi la lista con lo stato in parole, poi -- aprendo una riga --
i passi con chi li ha in carico. Dove un passo si è fermato a chiedere, il
pannello è anche il posto dove si risponde o si decide: è una persona che deve
farlo, e l'agente ha i tool per avviare e leggere un processo ma non per
rispondere al posto suo.

## Contratti fra i servizi

I campioni di ciò che il frontend legge dagli altri servizi stanno in
`demo-infra/contracts`, versionati e in copia unica. I test di contratto li
caricano da lì: se manca la cartella **falliscono**, invece di saltarsi da
soli. Un test di contratto silenzioso quando la controparte non c'è è
esattamente il silenzio che i contratti tolgono. Fuori dal repository, la
cartella si indica con `CONTRACTS_DIR`.
