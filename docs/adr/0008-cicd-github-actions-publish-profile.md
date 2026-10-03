# ADR-0008: CI/CD with GitHub Actions: test, then deploy with a publish profile (SCM basic auth enabled)

- **Status:** Accepted, **to be revisited by Security**
- **Date:** 2026-09-17 (workflow), 2026-09-25 (test gate, `b7d4996`); recorded 2026-09-30
- **Requirements:** NFR-08, NFR-10

## Context
The operator wants every push to `main` deployed automatically, and only when the
offline tests pass. A publish profile is the simplest credential for
`Azure/functions-action`.

## Decision
`.github/workflows/deploy.yml` has two jobs. `test` installs `requirements-dev.txt` and
runs pytest. `deploy` (`needs: test`) installs `requirements.txt` into `.python_packages`
and deploys with the `AZURE_FUNCTIONAPP_PUBLISH_PROFILE` repository secret
(`deploy.yml:12-47`). New Function Apps disable publish-profile (SCM basic) auth, so it
was turned on (README.md:128-133).

## Consequences
- A red test blocks the deploy.
- **Security trade-off:** SCM basic-auth publishing credentials are long-lived and
  enabled on the Function App. Anyone holding the profile can deploy code. The
  alternative is OIDC federated credentials (`azure/login`) with basic auth turned off again.
- Actions are pinned to major tags (`@v7`, `@v1`), not commit SHAs.
