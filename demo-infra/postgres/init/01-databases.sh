#!/bin/sh
# Tre database sullo stesso server: i processi, la memoria, e il poco stato
# che il master agent condivide fra le proprie repliche.
#
# Stessa tecnologia, dati separati. Chi vuole due server cambia una variabile:
# ogni servizio ha il proprio DSN e non sa niente dell'altro.
#
# Gira solo alla prima inizializzazione del volume, come tutti gli script di
# questa cartella. Su un volume che esiste gia', il database si crea a mano:
#   docker compose exec postgres createdb -U "$POSTGRES_USER" memoria
#   docker compose exec postgres createdb -U "$POSTGRES_USER" agente
set -e

for database in memoria agente; do
    psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-SQL
        SELECT 'CREATE DATABASE $database'
        WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '$database')\gexec
SQL
done
