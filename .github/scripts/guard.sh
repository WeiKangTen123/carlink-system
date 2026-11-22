#!/bin/bash
# CI guard: fails when a change ADDS (or renames/copies into) a file that must
# never be in this public repo: private case folders, databases, keys and .env
# files, benchmark outputs and work folders, scraper scratch.
#
#   bash .github/scripts/guard.sh <base> [<head>]   # files added in base..head (head defaults to HEAD)
#   bash .github/scripts/guard.sh --all             # every tracked file (to check the patterns)
#
# Existing tracked files are not judged: only what a change adds. If a file
# is blocked by mistake, rename it or narrow the pattern here in the same PR
# (a reviewer then sees the change to this list).
set -euo pipefail

# Extended regexes, matched against the full path from the repo root.
BLOCKED=(
    '^example_case/'                    # private case folders (photos, reports)
    '(^|/)scraper_test/'                # scraper scratch
    '\.(db|db-wal|db-shm|db-journal|sqlite|sqlite3)$'   # databases
    '(^|/)key[^/]*\.json$'              # answer keys / service-account keys
    '(^|/)\.env[^/]*$'                  # .env, .env.local, ... (.env.example is allowed below)
    '(^|/)[^/]+\.env$'                  # hmac.env, worker.env, backup.env ...
    '(^|/)out/([^/]+/)*[^/]+\.json$'    # benchmark / run outputs
    '\.gate\.txt$'                      # benchmark gate results
    '(^|/)work-[^/]*/'                  # benchmark work folders
    '(^|/)carlink-bench/'               # the benchmark home on the server
    '(^|/)bench-[^/]*/'                 # bench-<label>/ run folders
    '(^|/)bench/(out|work|inputs|cases)/'   # bench data next to the harness
    '\.(pem|p12|pfx|htpasswd|secret)$'  # certificates, private keys, password files, ai-keys.secret
)
ALLOWED=(
    '(^|/)\.env\.example$'
)

blocked_reason() {
    local path="$1" re
    for re in "${ALLOWED[@]}"; do
        [[ "$path" =~ $re ]] && return 1
    done
    for re in "${BLOCKED[@]}"; do
        if [[ "$path" =~ $re ]]; then
            printf '%s' "$re"
            return 0
        fi
    done
    return 1
}

if [ "${1:-}" = "--all" ]; then
    mapfile -d '' FILES < <(git ls-files -z)
    what="tracked file(s)"
elif [ -n "${1:-}" ]; then
    base="$1" head="${2:-HEAD}"
    mapfile -d '' FILES < <(git diff --name-only --no-renames --diff-filter=A -z "$base" "$head")
    what="file(s) added in ${base:0:12}..${head:0:12}"
else
    echo "usage: $0 <base> [<head>] | --all" >&2
    exit 2
fi

bad=0
for f in "${FILES[@]}"; do
    if reason=$(blocked_reason "$f"); then
        echo "::error file=$f::blocked path (pattern $reason): $f"
        bad=$((bad + 1))
    fi
done

echo "guard: checked ${#FILES[@]} $what, $bad blocked"
if [ "$bad" -gt 0 ]; then
    echo "guard: remove these files from the change (git rm --cached <file>) and add them to .gitignore." >&2
    echo "guard: private data, keys and benchmark outputs never go into this public repo." >&2
    exit 1
fi
