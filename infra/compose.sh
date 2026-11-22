#!/bin/bash
# By-hand docker compose commands for one Carlink stack, with its own clone,
# compose project and files (infra/deploy.sh --compose does the work):
#
#   bash infra/compose.sh staging ps
#   bash infra/compose.sh staging logs --tail 50 api
#   bash infra/compose.sh staging stop          # stays stopped (unless-stopped)
#   bash infra/compose.sh staging up -d         # start again, same commit
#   bash infra/compose.sh prod ps
#
# Never a bare `docker compose -f infra/docker-compose.prod.yml ...` in the
# staging clone: without -p and the staging override it is production's
# project "infra", and would recreate the live containers.
set -euo pipefail
[ "$#" -ge 1 ] || { sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2; }
env_name=$1
shift
exec bash "$(dirname "$0")/deploy.sh" --env "$env_name" --compose -- "$@"
