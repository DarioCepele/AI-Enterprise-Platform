# Manifest Kubernetes

Il compose resta il percorso di sviluppo. Questi manifest servono a rispondere
a una domanda diversa: **quello che abbiamo scritto regge due repliche?**

Per questo il default e' `replicas: 2` sul master agent, sul servizio di
memoria e sul servizio dei processi. Non e' dimensionamento: e' la sonda che fa
emergere subito una regressione dello stato di processo -- log che si vedono a
meta', compattazioni doppie, lock presi in RAM, un'istanza che sopravvive solo
al processo che l'ha avviata.

## Cosa non c'e', e perche'

**Postgres.** Un database dentro un `Deployment` senza operator sembra
funzionare finche' non serve davvero: niente backup, niente failover ordinato,
un `kubectl rollout restart` che diventa una perdita di dati. Qui si punta a un
servizio gestito (RDS, Cloud SQL, Azure Flexible Server) o a un operator vero,
e il suo indirizzo arriva dal Secret.

E' **l'unico** database del laboratorio, con tre database dentro: le istanze
dei processi, i trascritti della memoria, e il poco stato che il master
condivide fra le repliche. Serve l'estensione `pgvector` per la ricerca
semantica: i servizi gestiti la offrono, un cluster fatto in casa la deve
installare.

**Ingress e TLS.** Dipendono dal cluster: nginx, Traefik, il controller del
cloud. C'e' un `Service` per il frontend e per il master agent, e da li' si
attacca quello che c'e'.

**Autenticazione.** Fuori scopo per scelta, come nel resto del template.

## Come si applica

```bash
kubectl create namespace agui-lab
kubectl -n agui-lab create secret generic agui-lab \
  --from-literal=OPENAI_API_KEY=... \
  --from-literal=DEMO_PUSH_SECRET=... \
  --from-literal=KNOWLEDGE_SERVICE_TOKEN=... \
  --from-literal=MEMORY_POSTGRES_DSN=postgresql://... \
  --from-literal=DEMO_POSTGRES_DSN=postgresql://... \
  --from-literal=ANALYSIS_SERVICE_TOKEN=... \
  --from-literal=PROCESS_PUSH_SECRET=... \
  --from-literal=PROCESS_POSTGRES_DSN=postgresql://...
kubectl -n agui-lab apply -f deploy/
```

Le immagini nei manifest sono segnaposto (`ghcr.io/your-org/<repo>:latest`), ma
**riferimenti validi**: cosi' `kustomize` puo' sostituirle, ed e' quello che fa
l'overlay `k3s/` per provarli davvero. La CI di ogni repo costruisce la propria
immagine, e chi forka mette il proprio registry.

## Provarli davvero, su k3s

Un manifest che nessuno ha mai applicato e' una dichiarazione di intenti. k3d
(k3s dentro Docker) costa un minuto, e si butta con un comando.

```bash
k3d cluster create agui-lab --agents 1 --network demo-infra_default
```

La rete non e' un dettaglio: mettendo il cluster **sulla rete del compose**, i
pod raggiungono `postgres` per nome. E' la stessa forma della
produzione -- i database stanno fuori dal cluster -- senza dover installare
niente in piu'.

Su Docker Desktop per Windows il kubeconfig scritto da k3d punta a
`host.docker.internal`, che dall'host puo' risolvere sull'IP della LAN e non
rispondere; in quel caso:

```bash
kubectl config set-cluster k3d-agui-lab --server=https://127.0.0.1:<porta del serverlb>
```

Poi le immagini che il compose ha gia' costruito, il segreto, e i manifest:

```bash
docker compose build
k3d image import -c agui-lab demo-infra-master-agent:latest demo-infra-memory-service:latest \
  demo-infra-knowledge-agent:latest demo-infra-analysis-agent:latest \
  demo-infra-frontend:latest demo-infra-process-service:latest

kubectl create namespace agui-lab
kubectl -n agui-lab create secret generic agui-lab \
  --from-literal=OPENAI_API_KEY=... \
  --from-literal=DEMO_PUSH_SECRET=... --from-literal=PROCESS_PUSH_SECRET=... \
  --from-literal=KNOWLEDGE_SERVICE_TOKEN=... --from-literal=ANALYSIS_SERVICE_TOKEN=... \
  --from-literal=MEMORY_POSTGRES_DSN='postgresql://<utente>:<password>@postgres:5432/memoria' \
  --from-literal=DEMO_POSTGRES_DSN='postgresql://<utente>:<password>@postgres:5432/agente' \
  --from-literal=PROCESS_POSTGRES_DSN='postgresql://<utente>:<password>@postgres:5432/processi'

kubectl apply -k ../k3s        # gli stessi manifest, con le immagini locali
```

L'overlay `k3s/` (fuori da `deploy/`, perche' kustomize non ammette una base che
contiene il proprio overlay) cambia **solo le immagini**: quello che si applica
sono questi manifest, non una copia scritta per l'occasione.

Alla fine:

```bash
k3d cluster delete agui-lab
```

### Cosa ha detto il cluster, e i manifest non sapevano

Due cose sono venute fuori applicandoli, e nessuna delle due si vede leggendo.

**`runAsNonRoot` da solo non basta.** Con `USER service` nel Dockerfile, kubelet
non sa dire se quell'utente e' root e rifiuta di avviare il container:
`container has runAsNonRoot and image has non-numeric user`. Serve il numero --
`runAsUser: 10001` per i servizi Python, `1000` per il frontend. Dieci pod su
dieci in `CreateContainerConfigError`, e il compose non se ne era mai accorto
perche' quel controllo lo fa Kubernetes.

**Il server di Next si lega a `HOSTNAME`**, che in un pod e' il nome del pod:
il Service continua a funzionare, ma un `kubectl port-forward` riceve
connessione rifiutata. `HOSTNAME: "0.0.0.0"` nel Deployment.

### La promessa, verificata

Con due repliche del servizio dei processi, su k3s:

1. si avvia un'istanza di `example-approval`, che si ferma sul chiarimento
   dell'agente;
2. si **cancella il pod** che la sta aspettando (`kubectl delete pod`);
3. si risponde al chiarimento sul Service, che ora bilancia sull'altro pod;
4. l'istanza riprende, arriva all'approvazione, viene approvata e **finisce**.

`GET /instances/<id>/replay` restituisce il percorso completo:
`collect, check, decide, approval, apply, notify`. Un'istanza che sopravvive al
processo che l'ha avviata non e' piu' una frase nel README.

## Le probe

`livenessProbe` su `/health/live`, `readinessProbe` su `/health/ready`. La
differenza conta qui piu' che altrove: una liveness che interrogasse i database
riavvierebbe processi sani ogni volta che un database ha un singhiozzo, mentre
una readiness che non lo facesse manderebbe traffico a un processo che non puo'
servirlo.
