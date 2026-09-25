# eTenders digest — Azure Function

Twice a day (08:00 and 20:00 SAST) this fetches the last few days of tenders
from the eTenders OCDS API, keeps the open ones in the wanted categories across
South Africa, ranks them by keyword score, and emails the list via Azure
Communication Services.

The eTenders API is slow and unreliable, so releases are fetched one day at a
time, newest first, under a time budget. If some days can't be fetched, the
email still goes out, marked **Partial digest** and naming the missing dates.

## Files

- `pipeline.py` — fetching, scoring and filtering.
- `email_sender.py` — builds and sends the email.
- `function_app.py` — the Azure Functions timer trigger that ties the two together.
- `TG.py` — local runner: writes `etenders_matches.xlsx` instead of emailing.
- `tests/` — offline unit tests; CI runs them before every deploy.
- `host.json`, `requirements.txt`, `.funcignore` — Azure Functions project files.
- `requirements-dev.txt`, `pytest.ini` — test tooling (never deployed).
- `local.settings.json.example` — copy to `local.settings.json` for local runs (never commit the real one).

## Tuning what you get

All in `pipeline.py`:

- `CATEGORIES` — the main volume control. Remove entries to get fewer tenders.
- `PROVINCES` — empty means nationwide. Use lowercase names as the API spells
  them (`"gauteng"`, `"western cape"`, …); a test checks the spelling.
- `KEYWORDS` — only orders the list, it doesn't filter it. Higher weight ranks higher.
- `LOOKBACK_DAYS` — how many days back each run looks.

## 1. One-time Azure setup

Prerequisites: [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli), an Azure subscription.

```powershell
az login

$RG       = "etenders-rg"
$LOCATION = "southafricanorth"
$STORAGE  = "etendersfuncstorage"      # must be globally unique, lowercase
$FUNCAPP  = "etenders-digest-func"     # must be globally unique
$ACS_NAME = "etenders-acs"

az group create -n $RG -l $LOCATION

# Storage account required by Functions
az storage account create -n $STORAGE -g $RG -l $LOCATION --sku Standard_LRS

# Function App (Linux, Consumption plan, Python 3.11)
az functionapp create -g $RG -n $FUNCAPP -s $STORAGE `
  --consumption-plan-location $LOCATION `
  --runtime python --runtime-version 3.11 --functions-version 4 --os-type Linux

# Azure Communication Services resource (for sending email)
az communication create -n $ACS_NAME -g $RG --data-location "Africa"

# Create an Email Communication Service + a free Azure-managed domain,
# then connect it to the ACS resource, OR use your own verified domain.
# Easiest path (Azure Portal): ACS resource -> Email -> Provision domains ->
# "Add domain" -> "Azure subdomain" -> then link it under ACS resource -> Domains.
```

After provisioning the email domain in the portal, grab:
- The **connection string** of the ACS resource (Keys blade).
- The **verified sender address**, e.g. `DoNotReply@xxxxxxxx.azurecomm.net`.

Set the app settings on the Function App:

```powershell
az functionapp config appsettings set -g $RG -n $FUNCAPP --settings `
  "ACS_CONNECTION_STRING=<paste connection string>" `
  "SENDER_EMAIL=<paste verified sender address>" `
  "RECIPIENT_EMAIL=you@example.com"
```

(Add more comma-separated addresses to `RECIPIENT_EMAIL` if needed.)

## 2. Local testing (optional but recommended)

Prerequisites: [Azure Functions Core Tools v4](https://learn.microsoft.com/azure/azure-functions/functions-run-local), Python 3.11.

```powershell
copy local.settings.json.example local.settings.json
# edit local.settings.json with your real ACS connection string / addresses

python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt

python -m pytest        # offline unit tests, a couple of seconds

func start
# then in another terminal, trigger it manually:
# curl.exe -X POST http://localhost:7071/admin/functions/etenders_digest -H "Content-Type: application/json" -d "{}"
```

## 3. Deploy

Manual deploy (fastest first-time option):

```powershell
func azure functionapp publish $FUNCAPP
```

Or let GitHub Actions deploy on every push to `main` (see below).

## 4. Put this in a GitHub repository

1. Go to https://github.com/new, create a repository (e.g. `eTenders`), **don't** initialize it with a README/gitignore (you already have them here).
2. In this folder, run:
   ```powershell
   git init
   git add .
   git commit -m "Azure Function: eTenders digest with email delivery"
   git branch -M main
   git remote add origin https://github.com/220404773/eTenders.git
   git push -u origin main
   ```
3. **Enable automatic deploys (optional, via `.github/workflows/deploy.yml` already included):**
   - Download the Function App's publish profile:
     ```powershell
     az functionapp deployment list-publishing-profiles -g $RG -n $FUNCAPP --xml
     ```
   - In GitHub: repo → Settings → Secrets and variables → Actions → New repository secret →
     name it `AZURE_FUNCTIONAPP_PUBLISH_PROFILE`, paste the XML output.
   - New Function Apps disable publish-profile auth by default, which makes the deploy
     fail with `401 Unauthorized`. Enable it once:
     ```powershell
     az resource update -g $RG --namespace Microsoft.Web --resource-type basicPublishingCredentialsPolicies `
       --name scm --parent sites/$FUNCAPP --set properties.allow=true
     ```
   - Push to `main`. The workflow runs the tests first and deploys only if they pass.

`local.settings.json` (with your real secrets) is already excluded via `.gitignore` — never commit it.

## Schedule

The timer trigger cron is `0 0 6,18 * * *` (NCRONTAB, UTC) = **08:00 and 20:00 SAST**
(South Africa has no DST, so this stays fixed year-round).
