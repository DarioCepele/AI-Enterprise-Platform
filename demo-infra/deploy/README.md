# Manifest Kubernetes

Il compose resta il percorso di sviluppo. Questi manifest rispondono a una
domanda diversa: **quello che abbiamo scritto regge due repliche?**

Per questo il default è `replicas: 2` sul master agent, sul servizio di
memoria, su quello dei processi e sul frontend. Non è dimensionamento: è la
sonda che fa emergere subito una regressione dello stato di processo -- log
che si vedono a metà, compattazioni doppie, lock presi in RAM, un'istanza che
sopravvive solo al processo che l'ha avviata.

## Cosa c'è

| File | |
|---|---|
| `00-configmap.yaml` | Gli indirizzi che il browser usa e le origini ammesse: da adattare al proprio dominio. |
| `10-master-agent.yaml` | Deployment, Service, PodDisruptionBudget. |
| `20-memory-service.yaml` | Idem. |
| `30-knowledge-agent.yaml`, `35-analysis-agent.yaml` | I due sottoagenti A2A. |
| `40-frontend.yaml` | Il frontend: legge la configurazione a runtime, la stessa immagine serve ogni ambiente. |
| `50-process-service.yaml` | Uno **StatefulSet**: DBOS vuole un identificativo stabile per ogni esecutore, per riprendere i workflow di un pod che se n'è andato. Il nome del pod lo è. |
| `60-voice-service.yaml`, `65-scraping-mcp.yaml` | La voce e lo scraper. |
| `80-network-policies.yaml` | Chi può parlare con chi. |
| `90-ingress.yaml` | I quattro host pubblici, su un Ingress standard. |
| `secret.example.yaml` | Le chiavi del Secret, **non** applicato: documenta cosa serve. |

Ogni container gira come utente non-root numerico, senza capability e con il
profilo seccomp di default; tutti tranne lo scraper, il cui Chromium scrive
profilo e cache, con il filesystem in sola lettura e `/tmp` in un `emptyDir`. I segreti arrivano chiave per chiave (`secretKeyRef`), non come
blocco intero: un servizio vede solo i propri.

## Cosa non c'è, e perché

**Postgres.** Un database dentro un `Deployment` senza operator sembra
funzionare finché non serve davvero: niente backup, niente failover ordinato,
un `kubectl rollout restart` che diventa una perdita di dati. Qui si punta a un
servizio gestito o a un operator vero. Serve l'estensione `pgvector`.

**Autenticazione.** Fuori perimetro per scelta. Va davanti a tutti gli host
pubblici: il proxy di identità o l'autenticazione del gateway. Cosa serve
perché funzioni con un proxy a cookie è in [SECURITY.md](../../SECURITY.md).

**La gestione dei segreti.** Il Secret si crea a mano o, meglio, con External
Secrets o Sealed Secrets: `secret.example.yaml` elenca le chiavi, non i valori.

**I certificati.** L'Ingress usa il secret `agent-platform-tls`, creato da
cert-manager o a mano. HSTS va sul terminatore TLS.

## Come si applica

I comandi partono da `demo-infra/`.

1. **I database.** Un ruolo e un database per servizio, sul Postgres che usi:
   lo stesso script che il compose esegue a ogni `up`, idempotente.

   ```bash
   POSTGRES_HOST=db.example POSTGRES_USER=admin POSTGRES_PASSWORD=... \
   MASTER_DB_PASSWORD=... MEMORY_DB_PASSWORD=... \
   PROCESS_DB_PASSWORD=... AGENTS_DB_PASSWORD=... \
     sh postgres/provision.sh
   ```

2. **Il Secret**, con le chiavi di `deploy/secret.example.yaml`:

   ```bash
   kubectl create namespace agent-platform
   kubectl -n agent-platform create secret generic agent-platform \
     --from-literal=OPENAI_API_KEY=... \
     --from-literal=PUSH_SECRET="$(openssl rand -hex 24)" \
     --from-literal=KNOWLEDGE_SERVICE_TOKEN="$(openssl rand -hex 24)" \
     --from-literal=ANALYSIS_SERVICE_TOKEN="$(openssl rand -hex 24)" \
     --from-literal=MASTER_POSTGRES_DSN=postgresql://master_svc:...@db.example:5432/master \
     --from-literal=MEMORY_POSTGRES_DSN=postgresql://memory_svc:...@db.example:5432/memory \
     --from-literal=PROCESS_POSTGRES_DSN=postgresql://process_svc:...@db.example:5432/processes \
     --from-literal=AGENTS_POSTGRES_DSN=postgresql://agents_svc:...@db.example:5432/agents
   ```

3. **Il dominio.** `example.com` va sostituito in `00-configmap.yaml` e in
   `90-ingress.yaml`.

4. **I manifest:**

   ```bash
   kubectl -n agent-platform apply -k deploy/
   ```

Le immagini nei manifest (`ghcr.io/your-org/<servizio>:0.1.0`) sono quelle che
il workflow `release` pubblica a ogni tag: chi forka mette il proprio registry,
con un overlay di kustomize o sostituendo `your-org`.

## Approvazioni e repliche

Un'azione che aspetta l'approvazione di una persona è tenuta dalla replica del
master che l'ha chiesta: Microsoft Agent Framework conserva quello stato nella
memoria del processo, e non offre un modo di spostarlo altrove. È l'unico stato
del master che non sta in Postgres.

Se la risposta arriva all'altra replica, **fallisce chiusa**: non esegue
niente, e l'interfaccia chiede di ripetere la richiesta
(`demo-master-agent/tests/test_approvals.py` lo verifica). Per non farla
fallire, le richieste di una conversazione devono arrivare alla stessa replica:

- **Una replica sola** del master: `replicas: 1`, e il PodDisruptionBudget con
  `maxUnavailable: 1` invece di `minAvailable: 1`, che con una replica
  bloccherebbe lo svuotamento dei nodi.
- **Affinità al gateway, con un cookie di sessione del load balancer.** Il
  frontend sta su un'altra origine: il browser manda quel cookie solo con
  `API_CREDENTIALS=include` nel frontend e `MASTER_CORS_CREDENTIALS=true` nel
  master, con le origini ammesse scritte per esteso. In Gateway API è
  `sessionPersistence` ([GEP-1619](https://gateway-api.sigs.k8s.io/geps/gep-1619/),
  ancora sperimentale); altrimenti è la configurazione del proprio controller.
- **Affinità per indirizzo di origine** al gateway: nessun cambiamento nel
  frontend, ma dietro un NAT molti utenti finiscono sulla stessa replica.

Non serve invece `sessionAffinity: ClientIP` sul Service: i controller di
ingress mandano il traffico direttamente agli endpoint dei pod, e l'indirizzo
che il Service vedrebbe sarebbe quello del controller, non dell'utente.

## Ingress

Un `Ingress` standard, senza annotazioni di un controller particolare: quattro
host invece di riscritture dei percorsi, perché riscrivere i percorsi è la
parte che ogni controller fa a modo suo. Il webhook dei sottoagenti non è
esposto: lo raggiungono dentro il cluster.

ingress-nginx non riceve più rilasci da marzo 2026
([annuncio](https://kubernetes.io/blog/2025/11/11/ingress-nginx-retirement/)):
serve un controller mantenuto, oppure la traduzione in Gateway API
(`HTTPRoute`), che è la direzione indicata dal progetto Kubernetes.

## NetworkPolicy

Tutto l'ingresso è negato, poi aperto servizio per servizio: il servizio di
memoria accetta solo il master; i sottoagenti solo il master e il servizio dei
processi; lo scraper solo il knowledge agent, ed esce solo verso Internet e il
DNS, non verso il cluster né verso le reti private intorno. I servizi che il
browser raggiunge accettano qualunque namespace, perché il controller di
ingress sta in uno che questo file non può nominare: in un overlay si
restringe `namespaceSelector` al suo.

Sono oggetti standard: li applica ogni CNI che li implementa (Calico, Cilium,
Antrea...), e li ignora in silenzio uno che non lo fa.

## Provarli davvero, su k3s

Un manifest che nessuno ha mai applicato è una dichiarazione di intenti. k3d
(k3s dentro Docker) costa un minuto, e si butta con un comando.

```bash
docker compose up -d postgres postgres-init   # il database, già con i ruoli
docker compose build
k3d cluster create agent-platform --agents 1 --network agent-platform_default
```

La rete non è un dettaglio: mettendo il cluster **sulla rete del compose**, i
pod raggiungono `postgres` per nome. È la stessa forma della produzione -- il
database sta fuori dal cluster -- senza installare niente in più.

Su Docker Desktop per Windows il kubeconfig scritto da k3d punta a
`host.docker.internal`, che dall'host può risolvere sull'IP della LAN e non
rispondere; in quel caso:

```bash
kubectl config set-cluster k3d-agent-platform --server=https://127.0.0.1:<porta del serverlb>
```

Poi le immagini appena costruite, il Secret (con i DSN verso `postgres:5432`),
e i manifest:

```bash
k3d image import -c agent-platform \
  agent-platform-master-agent:latest agent-platform-memory-service:latest \
  agent-platform-knowledge-agent:latest agent-platform-analysis-agent:latest \
  agent-platform-frontend:latest agent-platform-process-service:latest \
  agent-platform-voice-service:latest agent-platform-scraping-mcp:latest

kubectl create namespace agent-platform
kubectl -n agent-platform create secret generic agent-platform ...   # come sopra
kubectl apply -k k3s
```

L'overlay `k3s/` (fuori da `deploy/`, perché kustomize non ammette una base che
contiene il proprio overlay) cambia le immagini e gli indirizzi del browser,
che diventano i port-forward locali. Quello che si applica sono questi
manifest, non una copia scritta per l'occasione. Un `port-forward` a un Service
sceglie un pod solo: le approvazioni funzionano anche con due repliche.

Alla fine:

```bash
k3d cluster delete agent-platform
```

### Cosa ha detto il cluster, e i manifest non sapevano

Due cose sono venute fuori applicandoli, e nessuna delle due si vede leggendo.

**`runAsNonRoot` da solo non basta.** Con un utente per nome nel Dockerfile,
kubelet non sa dire se quell'utente è root e rifiuta di avviare il container:
`container has runAsNonRoot and image has non-numeric user`. Serve il numero --
`runAsUser: 10001` per i servizi Python, `1000` per il frontend, `1001` per lo
scraper. Il compose non se ne era mai accorto, perché quel controllo lo fa
Kubernetes.

**Il server di Next si lega a `HOSTNAME`**, che in un pod è il nome del pod: il
Service continua a funzionare, ma un `kubectl port-forward` riceve connessione
rifiutata. `HOSTNAME: "0.0.0.0"` nel Deployment, e ora anche nell'immagine.

### La promessa, verificata

Con due repliche del servizio dei processi, su k3s:

1. si avvia un'istanza di `example-approval`, che si ferma sul chiarimento
   dell'agente;
2. si **cancella il pod** che la sta aspettando (`kubectl delete pod`);
3. si risponde al chiarimento sul Service, che ora bilancia sull'altro pod;
4. l'istanza riprende, arriva all'approvazione, viene approvata e **finisce**.

`GET /instances/<id>/replay` restituisce il percorso completo:
`collect, check, decide, approval, apply, notify`. Un'istanza che sopravvive al
processo che l'ha avviata non è più una frase nel README.

## Le probe

`livenessProbe` su `/health/live`, `readinessProbe` su `/health/ready`. La
differenza conta qui più che altrove: una liveness che interrogasse i database
riavvierebbe processi sani ogni volta che un database ha un singhiozzo, mentre
una readiness che non lo facesse manderebbe traffico a un processo che non può
servirlo.

## Validarli senza un cluster

```bash
kubectl kustomize deploy | kubeconform -strict -summary -kubernetes-version 1.31.0 -
kubectl kustomize k3s | kubeconform -strict -summary -kubernetes-version 1.31.0 -
```

La CI fa lo stesso a ogni modifica.
