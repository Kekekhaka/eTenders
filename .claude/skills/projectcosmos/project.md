# Project settings (edit me; `cosmos_agents.py install` never overwrites this)

- **Purpose:** eTenders digest. A personal alert for one South African data engineer / BI consultant: twice a day it fetches open government tenders from the eTenders OCDS API, filters and ranks them, and emails a digest (HTML + xlsx).
- **Stack:** Python 3.11 Azure Function (Linux Consumption, South Africa North), Azure Communication Services email, GitHub Actions deploy. The venv is 3.12, so use 3.11 syntax only.
- **Run the tests with:** `.venv/Scripts/python.exe -m pytest -q` (Git Bash). They must stay green; the suite should take under 15 s.
- **Shell / environment notes:** Windows, Git Bash, behind a corporate proxy that blocks some sites (for example DeepSeek). The folder is OneDrive-synced, so re-read files before editing.
- **Cost ceiling:** currently about US$0.068/month (about R1.09; the subscription bills in USD, Microsoft's rate is R16.00 per US$). Nothing that adds cost without the operator's approval.
- **Never call live:** the eTenders API (slow and failing), Azure, ACS, Healthchecks. Use fakes and tests.
- **Never touch:** `local.settings.json` (live keys). Never run commands that print keys or app settings.
- **Diagrams:** PlantUML (C4-PlantUML), sources in `docs/diagrams/`.
- **Decision log:** `docs/cosmos/TEAM_LEADER_DECISIONS.md` (read it instead of long briefs).
- **Where things stand:** stages 1-6 are done. Stage 7 (Security), 8 (DevOps), 9 (final docs) and sign-off remain. See the docs in `docs/cosmos/`.
