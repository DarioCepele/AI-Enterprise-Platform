# Interfaccia del laboratorio

Next.js. Parla con il master agent via **AG-UI su SSE** e col servizio dei
processi via HTTP.

```bash
npm install
npm run dev      # http://localhost:3000
npm test         # vitest
```

Serve un master agent in ascolto: `AGUI_URL` (default
`http://127.0.0.1:8000/agui`). Con `PROCESS_URL` compare anche il tab delle
istanze; senza, il tab lo dice.

## La configurazione si legge quando la pagina gira, non quando e' stata costruita

`next build` inlinea le `NEXT_PUBLIC_*` nel bundle: un'immagine costruita
contro staging punta a staging per sempre, e promuoverla in produzione
vorrebbe dire ricostruirla -- cioe' non promuoverla. Qui il server legge
l'ambiente a ogni richiesta e passa i valori alla pagina in uno `<script>`; le
variabili di build restano solo come ripiego per `next dev`.

Sta in `lib/runtime-config.ts`, e i getter di `lib/config.ts` esistono per lo
stesso motivo: leggere all'import congelerebbe quello che il build ha visto.

## Il tab Istanze

Il terzo tab dell'ispettore guarda il **servizio dei processi**, non l'agente:
un'istanza va avanti da sola -- un agente risponde, qualcuno approva -- anche
quando in questa pagina non sta girando niente. Per questo il pannello continua
a chiedere a conversazione ferma, piu' piano (5 s invece di 1,5 s), e **solo
mentre lo si guarda**: un tab chiuso non ha ragione di interrogare nessuno.

Cosa mostra, in ordine di quello che serve sapere: quante istanze aspettano
**una persona**, poi la lista con lo stato in parole, poi -- aprendo una riga --
i passi con chi li ha in carico. Dove un passo si e' fermato a chiedere, il
pannello e' anche il posto dove si risponde o si decide: e' una persona che
deve farlo, e l'agente ha i tool per avviare e leggere un processo ma non per
rispondere al posto suo.

## Contratti fra i repo

I campioni di cio' che questo repo legge dagli altri stanno in
`demo-infra/contracts`, versionati e in copia unica. I test di contratto li
caricano da li': se manca la cartella **falliscono**, invece di saltarsi da
soli. Un test di contratto silenzioso quando la controparte non c'e' e'
esattamente il silenzio che i contratti tolgono.

```bash
# i repo come cloni fratelli: nessuna configurazione
# altrove: AGUI_LAB_CONTRACTS=/percorso/a/demo-infra/contracts
```
