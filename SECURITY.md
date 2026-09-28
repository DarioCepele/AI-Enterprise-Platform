# Sicurezza

## Segnalare una vulnerabilità

Non aprire una issue pubblica. Usa la segnalazione privata del repository su
GitHub (**Security → Report a vulnerability**), indicando:

- commit o versione, e il servizio coinvolto;
- i passi per riprodurre il problema;
- cosa permette di fare a chi lo sfrutta.

Chi forka questo template sostituisce questa sezione con il proprio contatto e
i propri tempi di risposta.

## Versioni supportate

Non ci sono rami di manutenzione: le correzioni arrivano su `main` e nel tag
successivo. Un fork le recupera facendo merge da upstream.

## Cosa protegge il template

| Rischio | Contromisura | Riferimento |
|---|---|---|
| SSRF da URL scelti dal modello o dall'utente | Video e pagine web solo verso indirizzi pubblici; ogni redirect ricontrollato; verificato l'IP a cui ci si è davvero connessi, contro il DNS rebinding | [OWASP SSRF Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html) |
| Istruzioni nascoste nei dati (prompt injection indiretta, memory poisoning) | Output dei tool, memorie, fatti e note dei sottoagenti arrivano al modello come dati, non come istruzioni; i fatti che hanno la forma di un ordine vengono scartati | [OWASP Top 10 for Agentic Applications](https://genai.owasp.org/2025/12/09/owasp-top-10-for-agentic-applications-the-benchmark-for-agentic-security-in-the-age-of-autonomous-ai/), ASI01 e ASI06 |
| Azioni con effetti decise dal modello | `start_process`, e i tool configurati in `MASTER_TOOLS_REQUIRING_APPROVAL`, si fermano per l'approvazione di una persona; se la risposta non si può applicare, non parte niente | ASI02; [AG-UI interrupts](https://docs.ag-ui.com/concepts/interrupts) |
| Messaggi fra agenti falsificati o dirottati | I sottoagenti notificano solo gli URL della piattaforma; token firmati con HMAC e con scadenza; carte estese dietro token di servizio | ASI07; [A2A, push notifications](https://a2a-protocol.org/) |
| Dati di un tenant letti da un altro | Ogni richiesta ha uno scope, e ogni tool legge e scrive in quello | — |
| Un servizio compromesso che legge i dati degli altri | Un ruolo Postgres e un database per servizio; container non-root senza capability, con il filesystem in sola lettura (tranne lo scraper, il cui browser scrive la propria cache); NetworkPolicy; lo scraper su una rete sua, senza accesso al resto | ASI03 |
| Codice iniettato nella pagina | Content Security Policy con nonce per richiesta e header di hardening; CORS con origini esplicite; cookie cross-origin solo se abilitati, e mai per `*` | [Next.js, CSP](https://nextjs.org/docs/app/guides/content-security-policy) |
| Esaurimento di risorse | Limiti sulla dimensione delle richieste; tetto alle chiamate al modello e ai tool per ogni run | — |

## Cosa resta a chi lo adotta

Fuori perimetro per scelta, perché dipende dall'organizzazione:

- **Autenticazione e autorizzazione.** Il template non ne ha. Metti davanti a
  tutti gli host pubblici un proxy di identità, o l'autenticazione del tuo
  gateway. Lo scope del tenant va derivato da un'identità verificata: un header
  impostato dal proxy (`MASTER_SCOPE_HEADER`, `PROCESS_SCOPE_HEADER`,
  `PROCESS_IDENTITY_HEADER`), mai uno che il browser possa scrivere da solo.
  Con un proxy a cookie servono `API_CREDENTIALS=include` nel frontend e
  `*_CORS_CREDENTIALS=true` nei servizi.
- **Segreti.** Vault o external-secrets, e la loro rotazione.
- **TLS e HSTS**, sul terminatore TLS.
- **Rate limiting e WAF**, sul gateway.
- **Conservazione dei dati** secondo le tue policy: il servizio di memoria
  offre retention, e la cancellazione di thread e fatti.

## Limiti noti

- Un'approvazione in attesa vive nella memoria della replica del master che
  l'ha chiesta, per scelta di Microsoft Agent Framework. Con più repliche la
  risposta deve tornare alla stessa: altrimenti fallisce chiusa e non esegue
  niente. Vedi `demo-infra/deploy/README.md`.
- Un turno vocale non può mostrare una richiesta di approvazione: la annulla,
  senza eseguire niente, e lo dice.
