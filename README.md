<<<<<<< HEAD
# eTenders digest — Azure Function

Fetches new eTenders OCDS releases, scores them against `pipeline.KEYWORDS`,
and emails the matches twice a day (08:00 and 20:00 SAST) using Azure
Communication Services Email.

## Files

- `pipeline.py` — fetch/score/filter logic (shared).
- `email_sender.py` — builds and sends the report email via Azure Communication Services.
- `function_app.py` — Azure Functions timer trigger (Python v2 model) that ties the two together.
- `TG.py` — manual/local CLI runner (writes `etenders_matches.xlsx`, no email).
- `host.json`, `requirements.txt`, `.funcignore` — Azure Functions project files.
- `local.settings.json.example` — copy to `local.settings.json` for local runs (never commit the real one).

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
  "RECIPIENT_EMAIL=vaxenxe@gmail.com"
```

(Add more comma-separated addresses to `RECIPIENT_EMAIL` if needed.)

## 2. Local testing (optional but recommended)

Prerequisites: [Azure Functions Core Tools v4](https://learn.microsoft.com/azure/azure-functions/functions-run-local), Python 3.11.

```powershell
copy local.settings.json.example local.settings.json
# edit local.settings.json with your real ACS connection string / addresses

python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

func start
# then in another terminal, trigger it manually:
# curl -X POST http://localhost:7071/admin/functions/etenders_digest
```

## 3. Deploy

Manual deploy (fastest first-time option):

```powershell
func azure functionapp publish $FUNCAPP
```

Or let GitHub Actions deploy on every push to `main` (see below).

## 4. Put this in a GitHub repository

1. Go to https://github.com/new, create a repository (e.g. `etenders-digest`), **don't** initialize it with a README/gitignore (you already have them here).
2. In this folder, run:
   ```powershell
   git init
   git add .
   git commit -m "Azure Function: eTenders digest with email delivery"
   git branch -M main
   git remote add origin https://github.com/<your-username>/etenders-digest.git
   git push -u origin main
   ```
3. **Enable automatic deploys (optional, via `.github/workflows/deploy.yml` already included):**
   - Download the Function App's publish profile:
     ```powershell
     az functionapp deployment list-publishing-profiles -g $RG -n $FUNCAPP --xml
     ```
   - In GitHub: repo → Settings → Secrets and variables → Actions → New repository secret →
     name it `AZURE_FUNCTIONAPP_PUBLISH_PROFILE`, paste the XML output.
   - Edit `.github/workflows/deploy.yml` and replace `<your-function-app-name>` with your
     `$FUNCAPP` value.
   - Push to `main` and the workflow will deploy automatically.

`local.settings.json` (with your real secrets) is already excluded via `.gitignore` — never commit it.

## Schedule

The timer trigger cron is `0 0 6,18 * * *` (NCRONTAB, UTC) = **08:00 and 20:00 SAST**
(South Africa has no DST, so this stays fixed year-round).
=======
# eTenders
Fetches new eTenders OCDS releases 
>>>>>>> cbafb2e991589fe065ccee380b41bdc68f909ca7
