#!/bin/bash
# By-hand docker compose commands for one Carlink stack, with its own clone,
# compose project and files, in a clean environment (infra/deploy.sh
# --compose does the work):
#
#   bash infra/compose.sh staging ps
#   bash infra/compose.sh staging logs --tail 50 api
#   bash infra/compose.sh staging stop          # stays stopped (unless-stopped)
#   bash infra/compose.sh staging up -d         # start again, same commit (then nginx restarts)
#   bash infra/compose.sh staging down -v       # staging's data gone: deploy + seed again
#   bash infra/compose.sh prod ps | logs | up -d | restart <svc> | exec <svc> ...
#
# Production takes only: ps logs config top images port events exec stats up
# restart start version ls -- never down / rm / kill / stop / volume / build,
# nor -v / --volumes / -V / --build (deploys and rebuilds: deploy.sh). After
# up / restart / start the rendered config is checked first, and nginx is
# restarted afterwards: recreated containers get new addresses, and nginx
# would answer 502 until it restarts.
#
# Never a bare `docker compose -f infra/docker-compose.prod.yml ...` in the
# staging clone: without -p and the staging override it is production's
# project "infra", and would recreate the live containers. Nor in
# production's: without docker-compose.ai-shared.yml production drops the
# shared AI folder (staging's smoke then fails).
set -euo pipefail
[ "$#" -ge 1 ] || { sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2; }
env_name=$1
shift
exec bash "$(dirname "$0")/deploy.sh" --env "$env_name" --compose -- "$@"
