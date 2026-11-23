<!--
Fill in every section; write "none" where nothing applies.
The process (DEVELOP -> SANDBOX -> STAGING -> PRODUCTION) is in CONTRIBUTING.md.
Never paste keys, tokens, passwords or real customer data (names, plates,
VINs, phones, photos, IDs) into a PR: this repository is public.
-->

## What and why

<!-- What changes for users, and why. Link the plan or issue. -->

## How to test (owner trial steps)

<!-- Numbered steps the owner can follow on staging, and what they should see. -->

1.

## Risk

- [ ] touches AI/detection (benchmark gate needed)
- [ ] DB schema/data step
- [ ] infra/deploy
- [ ] personal data

<!-- personal data = collects, stores, shows or sends names, plates, VINs, phones, photos or IDs, or sends them to a new service. -->

## One-time data step (irreversible?)

<!-- "none", or: what it changes, roughly how many rows, and whether it can be undone. -->

## Flags: names, staging value, production value

<!-- New AI or risky behaviour ships switched OFF in production. -->

| Flag | Staging | Production |
|---|---|---|
| none | | |

## Gate results

- [ ] CI green
- [ ] Sandbox (local tests, fakes):
- [ ] Staging smoke:
- [ ] AI gate: PASS / FAIL, run id: <!-- or "not needed" (no AI/detection change) -->
- [ ] Owner "go" (date):
