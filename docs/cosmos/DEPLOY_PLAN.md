# DEPLOY_PLAN (Stage 8, LITE)

Date checked: 2026-10-01. Cloud access was read-only. Nothing was applied. Every step below costs **R0** (no new resource, SKU or ingestion).

## 1. Pipeline (`.github/workflows/deploy.yml`, changed)
- `test` job (pytest) gates `deploy` (`needs: test`). Unchanged.
- Added `permissions: contents: read` (Security S-1).
- Added `concurrency: deploy-etenders`, `cancel-in-progress: false`, so deploys queue and never overlap.
- Added `workflow_dispatch` input `ref` (branch, tag or SHA). Both checkouts use `inputs.ref || github.sha`.
- YAML parses (checked with PyYAML). The workflow was not run.
- Not done (recommendation, free): pin `actions/checkout`, `setup-python`, `Azure/functions-action` to commit SHAs. I did not verify SHAs via `gh api`. Do this together with Dependabot for actions.
- Auth is still the publish-profile secret `AZURE_FUNCTIONAPP_PUBLISH_PROFILE`.

## 2. Operator steps for run memory (in order)
Read-only state found today: container `digest-state` **does not exist**; Function App has **no managed identity**; FTPS is `FtpsOnly`. Resources in `etenders-rg`: storage account, Function App, plan, App Insights, one action group.
Git Bash: prefix with `export MSYS_NO_PATHCONV=1` when passing `/subscriptions/...` scopes.

| # | Who | Command | Verify |
|---|---|---|---|
| 1 | Operator | `az storage container-rm create --storage-account etendersfuncstorage -g etenders-rg -n digest-state --public-access off` | `az storage container-rm show --storage-account etendersfuncstorage -g etenders-rg -n digest-state --query publicAccess` (expect `None`) |
| 2 | Operator | `az functionapp identity assign -n etenders-digest-func -g etenders-rg` (output has `principalId`; not secret) | `az functionapp identity show -n etenders-digest-func -g etenders-rg --query type` (expect `SystemAssigned`) |
| 3 | Operator | `SA=$(az storage account show -n etendersfuncstorage -g etenders-rg --query id -o tsv)`<br>`PID=$(az functionapp identity show -n etenders-digest-func -g etenders-rg --query principalId -o tsv)`<br>`az role assignment create --assignee-object-id $PID --assignee-principal-type ServicePrincipal --role "Storage Blob Data Contributor" --scope "$SA/blobServices/default/containers/digest-state"` | `az role assignment list --assignee $PID --all -o table` (exactly one row, scope ends `/containers/digest-state`) |
| 4 | Operator | Trigger one run (portal "Run" on the timer function, or wait for 06:00/18:00 UTC). | In App Insights Logs (section 5, query 3): no "memory unavailable" banner or warning, and `RUN_SUMMARY` shows memory read/written. In the blob container, a state blob appears: `az storage blob list --account-name etendersfuncstorage -c digest-state --auth-mode login -o table` |

Notes:
- Role assignment can take up to ~5 minutes to propagate. A first run during that window may show the banner. The digest still sends.
- Identity token endpoint on Linux Consumption: `IDENTITY_ENDPOINT` and `IDENTITY_HEADER` are injected only after the identity is enabled and the app restarts. Step 2 usually restarts it. If the logs show an identity or 401/403 error on the first run, restart the app (`az functionapp restart -n etenders-digest-func -g etenders-rg`) and run once more. Whether the token endpoint works on this plan is **unverified** until step 4.
- Does the app's storage connection already use a key? Leave `AzureWebJobsStorage` alone. Memory uses the identity, not the key.

## 3. Deploy and rollback
Deploy: merge/push to `main`. Tests run, then deploy. Watch the Actions run.
Rollback (fast, no history rewrite): Actions > "Deploy eTenders Azure Function" > Run workflow > `ref` = last good SHA or tag. Tests run again at that ref, so the old commit must be green.
Rollback (permanent): `git revert <bad-sha>` and push to `main`. The operator commits.
Verify after any deploy: `az functionapp function list -n etenders-digest-func -g etenders-rg -o table` shows the function, then check the next scheduled `RUN_SUMMARY`.

Runbook notes:
- Always end `az functionapp config appsettings set ...` with `-o none`. Without it the CLI prints every app setting, including keys, to the terminal.
- Do not deploy or change app settings between **05:45-06:15** and **17:45-18:15 UTC**. A restart kills a run in progress.
- A run lost to a restart: trigger the function manually once afterwards.

## 4. Security hand-offs (SECURITY_REPORT "For DevOps")
| Item | Status |
|---|---|
| S-1 `permissions: contents: read` | Done in `deploy.yml`. |
| Pin actions to SHAs | Recommended, not done (unverified SHAs). Free. |
| S-2 OIDC login instead of publish profile | Optional, free. Create a federated credential on an Entra app, grant it Website Contributor on the Function App only, then use `azure/login` with `id-token: write` on the deploy job. Needs operator (Entra permissions). Then delete the secret. |
| Disable FTP basic auth | Optional, free. `az resource update --ids <app-id>/basicPublishingCredentialsPolicies/ftp --set properties.allow=false` (`scm` stays on while the publish profile is used; disable it after OIDC). Verify with `az resource show --ids <same> --query properties.allow`. Today FTPS is already `FtpsOnly`. |
| Rotate publish profile | Operator, free. Portal reset, then update the GitHub secret. |
| Identity scope | Step 3 above gives only the one container. Verify with the role list. |
| Heartbeat URL | Set as an app setting only (`-o none`), never in the repo. |

## 5. Observability
Existing: App Insights `etenders-digest-func` (90-day retention, workspace-based, default workspace). Timer invocations: **2 requests/day** for September 2026 (metric `requests/count`, read-only).
`host.json` has sampling off. Actual GB ingested is **unverified**: a cheap `az` query needs the `log-analytics` extension and a Logs query. At 2 runs/day it is very likely far under the 5 GB/month free allowance. Check once in the portal: workspace > Usage and estimated costs. Free-tier figure is from memory, **unverified** today.

KQL (App Insights > Logs):
1. Last runs: `traces | where message has "RUN_SUMMARY" | order by timestamp desc | project timestamp, message | take 20`
2. Missing runs (expect 2 per day): `traces | where message has "RUN_SUMMARY" | summarize runs=count() by bin(timestamp,1d) | order by timestamp desc`
3. Memory problems after step 4: `traces | where timestamp > ago(1d) and (message has "memory unavailable" or severityLevel >= 3) | project timestamp, severityLevel, message`

Alerts: the existing action group has no verified rule. A paid log-search alert is **not** recommended. Optional heartbeat (the operator's choice): Healthchecks.io free plan (email alerts on free plan **unverified**) or none. The ping URL goes in an app setting only. Cost R0 if free plan; verify before relying on it.

## 6. Lifecycle (dates from DESIGN_SPEC, not re-verified)
- Linux Consumption retires **2028-09-30**. Python 3.11 end of life about **October 2027**.
- Suggested action: review in **2027-04** (Python bump to 3.12/3.13 and a plan decision, such as Flex Consumption). Any plan change must be re-priced first and needs operator approval. The venv is 3.12, so test there first.
- Free upkeep: monthly check of Dependabot / `pip list --outdated`.

## 7. Cost
All steps 1-4, the workflow change and the optional free steps: **R0**. A storage container and a managed identity are free. A few blobs per day cost a negligible amount (not priced here, **unverified**). Stays inside the ceiling of about R1.09/month. *Needs operator approval if ever added:* paid alert rules, always-ready instances, a higher plan or SKU, extra log ingestion.
