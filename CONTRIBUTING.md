# Contributing to Carlink

How a change gets from an idea to the live site. The owner decides and
approves; nobody ships to production without the owner's "go".

## The path of every change

```
 DEVELOP  ------>  SANDBOX  ------>  STAGING  ------>  PRODUCTION
 own branch,       local tests,      production-like   exactly the version
 pull request,     CI, fakes only;   copy; owner       that passed staging;
 CI green          may break         trial + "go"      backup first
```

1. **DEVELOP.** Every change is its own branch from `master` and reaches
   `master` only through a pull request with CI green. Never edit code on a
   live server.
2. **SANDBOX.** Test the change alone: local tests, CI, offline replays,
   throwaway containers or databases, fake data, test accounts and test
   keys. Breaking things here is fine. Nothing real is touched: no real
   customers, no real messages or Telegram users, no real payments.
3. **STAGING.** A production-like copy (same code, the live settings, its
   own address and database, password-protected, test data only). The
   release is rehearsed there and the owner tries it and writes "go".
   If staging is not available for a change, say so in the PR and let the
   owner decide. Never skip a phase silently.
4. **PRODUCTION.** Deploy exactly the version that passed staging:
   take a working backup first, run the health checks after, keep the
   previous version ready for a rollback, and record what was deployed.

Rules that always apply:

- **No "go", no production deploy.** A "go" for one change never covers
  another.
- **Fast lane** (urgent, small, no change in behaviour or AI): CI + a
  staging smoke check + the owner's OK. The backup is never skipped.
- **New AI or risky behaviour** ships switched OFF, is turned on in staging
  first, and in production only after it passes and the owner says so.
- **Shared server and keys:** staging and test runs use their own ports,
  names, volumes and quota caps, and never touch production or the other
  apps on the same machine.

Runbooks (deploy, backups, restore): [infra/README.md](infra/README.md).

## Every pull request

- Fill in the PR template: what and why, how to test, risk, one-time data
  step, flags (staging and production values), gate results.
- Add tests for the new behaviour. Risky changes get a fresh-eyes review
  before staging.
- CI must be green: `guard` (no private files), `backend` (the Python
  tests), `dashboard` (type check, build, check scripts).
- Lint new code (ESLint for the dashboard; ruff for Python is coming).
  No big-bang reformatting.
- Merged branches are deleted automatically. Never delete a branch that a
  test depends on: `fix/lag-heavy-downloads-on-signup` holds commit
  `f4b1a66`, which `test_case_access` reads.

## Run the checks locally

Backend (`apps/bot-service`, Python 3.11):

```bash
python -m pip install -r requirements.txt
python -m playwright install chromium     # the PDF tests render real PDFs
python scripts/run_ci_tests.py            # all tests, the way CI runs them
python scripts/run_ci_tests.py test_auth  # just one
```

The runner removes key variables and blocks outside traffic, so tests never
reach Gemini or Telegram.

Dashboard (`apps/dashboard`; the check scripts need Node 22.18+):

```bash
npm ci
npm run lint
npx tsc --noEmit
npx next build
for f in scripts/check-*.mjs; do node "$f" || echo "FAIL $f"; done
```

## Never commit

This repository is public. The `guard` job blocks these, but do not rely on
it:

- private case folders (`example_case/`), databases, `.env` files and other
  env files, `key*.json`, certificates and private keys;
- benchmark outputs, work folders and gate results;
- keys, tokens or passwords anywhere (code, logs, PRs, chat). Secrets live
  only in the server's env files. Never print them.
- **Real personal data** (names, plates, VINs, phones, photos, IDs) never
  goes into git, the sandbox, staging, tests or reports.

## Dependencies and security

- Third-party GitHub Actions are pinned to a full commit SHA, with the
  version as a comment.
- Dependabot opens weekly update PRs (`.github/dependabot.yml`). They follow
  the same path as any change. A `google-genai` update also needs the AI
  benchmark gate.
- Dependabot and code-scanning alerts are handled, never dismissed
  silently.
- CI holds no deploy secrets, and fork PRs need approval before their
  workflows run.

## AI features

- Behind a switch, default off.
- Measured on the benchmark gate before they are turned on; put the result
  (PASS/FAIL and run id) in the PR.
- Model, prompt and table versions are recorded per run.
- Quota guards keep test runs from using up production's allowance.

## Releases

Parts of this are still being set up (Nov 2026): the staging stack, the
release flow with tags, and numbered database migrations. Until each one
exists, say in the PR what was done instead.

- Release names are dates: `vYYYY.MM.DD`, plus `.2`, `.3` for more on the
  same day, with release notes.
- Database changes are numbered, repeatable steps, checked on a copy of
  the data before production. Say in the PR whether a data step can be
  undone.
