# Manifest Kubernetes

Il compose resta il percorso di sviluppo. Questi manifest servono a rispondere
a una domanda diversa: **quello che abbiamo scritto regge due repliche?**

Per questo il default e' `replicas: 2` sul master agent, sul servizio di
memoria e sul servizio dei processi. Non e' dimensionamento: e' la sonda che fa
emergere subito una regressione dello stato di processo -- log che si vedono a
meta', compattazioni doppie, lock presi in RAM, un'istanza che sopravvive solo
al processo che l'ha avviata.

## Cosa non c'e', e perche'

**Mongo, Redis e Postgres.** Un database dentro un `Deployment` senza operator
sembra funzionare finche' non serve davvero: niente backup, niente failover
ordinato, un `kubectl rollout restart` che diventa una perdita di dati. Qui si
punta a servizi gestiti (Atlas, ElastiCache, Azure Cache, RDS, Cloud SQL) o a
operator veri, e i loro indirizzi arrivano dal Secret.

Per Postgres la posta e' piu' alta che per gli altri due: **e' il libro mastro
delle istanze**. Redis perde una coda di log, Mongo perde una memoria che si
puo' ricostruire; Postgres perde processi a meta'. Se un fork ne mette uno solo
gestito, che sia questo.

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
  --from-literal=MEMORY_MONGO_URI=mongodb://... \
  --from-literal=MEMORY_REDIS_URI=redis://... \
  --from-literal=DEMO_REDIS_URI=redis://... \
  --from-literal=ANALYSIS_SERVICE_TOKEN=... \
  --from-literal=PROCESS_PUSH_SECRET=... \
  --from-literal=PROCESS_POSTGRES_DSN=postgresql://...
kubectl -n agui-lab apply -f deploy/
```

Le immagini nei manifest sono segnaposto (`ghcr.io/<owner>/<repo>:<tag>`): la
CI di ogni repo costruisce la propria, e chi forka mette il proprio registry.

## Le probe

`livenessProbe` su `/health/live`, `readinessProbe` su `/health/ready`. La
differenza conta qui piu' che altrove: una liveness che interrogasse i database
riavvierebbe processi sani ogni volta che un database ha un singhiozzo, mentre
una readiness che non lo facesse manderebbe traffico a un processo che non puo'
servirlo.
