#!/bin/sh
# Prints the environment CI runs the platform with: .env.example, with every
# secret replaced by a placeholder and the model replaced by the fake client.
#
#   tools/ci-env.sh > .env
#
# Derived, not copied: a second example file would drift from the first. The
# placeholders are not secrets -- the stack they configure lives and dies on
# the runner -- and they are long enough to pass the services' own checks.
set -eu

here=$(dirname "$0")
placeholder="ci-placeholder-not-a-secret-0123456789"

sed -E \
  -e "s/^(PUSH_SECRET|KNOWLEDGE_SERVICE_TOKEN|ANALYSIS_SERVICE_TOKEN)=.*/\1=${placeholder}/" \
  -e "s/^(POSTGRES_PASSWORD|MASTER_DB_PASSWORD|MEMORY_DB_PASSWORD|PROCESS_DB_PASSWORD|AGENTS_DB_PASSWORD)=.*/\1=${placeholder}/" \
  -e "s/^FAKE_MODEL=.*/FAKE_MODEL=true/" \
  "${here}/../.env.example"
