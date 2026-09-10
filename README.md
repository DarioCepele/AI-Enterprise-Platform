This is a [Next.js](https://nextjs.org) project bootstrapped with [`create-next-app`](https://nextjs.org/docs/app/api-reference/cli/create-next-app).

## Getting Started

First, run the development server:

```bash
npm run dev
# or
yarn dev
# or
pnpm dev
# or
bun dev
```

Open [http://localhost:3000](http://localhost:3000) with your browser to see the result.

You can start editing the page by modifying `app/page.tsx`. The page auto-updates as you edit the file.

This project uses [`next/font`](https://nextjs.org/docs/app/building-your-application/optimizing/fonts) to automatically optimize and load [Geist](https://vercel.com/font), a new font family for Vercel.

## Learn More

To learn more about Next.js, take a look at the following resources:

- [Next.js Documentation](https://nextjs.org/docs) - learn about Next.js features and API.
- [Learn Next.js](https://nextjs.org/learn) - an interactive Next.js tutorial.

You can check out [the Next.js GitHub repository](https://github.com/vercel/next.js) - your feedback and contributions are welcome!

## Deploy on Vercel

The easiest way to deploy your Next.js app is to use the [Vercel Platform](https://vercel.com/new?utm_medium=default-template&filter=next.js&utm_source=create-next-app&utm_campaign=create-next-app-readme) from the creators of Next.js.

Check out our [Next.js deployment documentation](https://nextjs.org/docs/app/building-your-application/deploying) for more details.

## Il tab Istanze

Il terzo tab dell'ispettore guarda il **servizio dei processi**, non l'agente:
un'istanza va avanti da sola -- un agente risponde, qualcuno approva -- anche
quando in questa pagina non sta girando niente. Per questo il pannello continua
a chiedere anche a conversazione ferma, piu' piano (5 s invece di 1,5 s), e
**solo mentre lo si guarda**: un tab chiuso non ha ragione di interrogare
nessuno.

Cosa mostra, in ordine di quello che serve sapere: quante istanze aspettano
**una persona**, poi la lista con lo stato in parole, poi -- aprendo una riga --
i passi con chi li ha in carico. Dove un passo si e' fermato a chiedere, il
pannello e' anche il posto dove si risponde o si decide: e' una persona che
deve farlo, e l'agente ha i tool per avviare e leggere un processo ma non per
rispondere al posto suo.

Se `PROCESS_URL` e' vuoto il tab lo dice, invece di mostrare un errore: un
laboratorio senza servizio dei processi e' una configurazione, non un guasto.

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
