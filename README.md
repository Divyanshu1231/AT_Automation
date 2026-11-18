# Airtable Bulk Form Filler

## Windows setup
```bat
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```
Open http://127.0.0.1:8000

## Form fields handled (matched by question label, not by position)
| Form question | CSV column(s) |
|---|---|
| Recruiter name (dropdown) | UI field "Recruiter name" (or CSV `Recruiter Name`) |
| Name of Candidates | Name / Name of Candidates / Candidate Name / Doctor Name |
| Edited Resume/CV (upload) | Edited CV / Edited Resume |
| Original CV (upload) | Original CV / Original Resume / Resume Path (full local path) |
| Notice Period | Notice Period |
| QUALIFICATION (multi-select) | Qualification / Education  (e.g. `BAMS, MBBS`) |
| Job title and Specialisation* (multi-select) | CSV `Job title and Specialisation` / `Specialisation`; if empty, the UI "Job title and Specialisation" default |
| Preferred Location applied for (City) | Preferred Location / City / Location |
| Preferred Location State applied for (multi-select) | Preferred Location State / State |
| Email ID | Email ID / Email |
| Mobile No.* | Mobile No / Mobile / Phone / Contact Number |
| Vacancy Reference Code | Vacancy Reference Code |

Any other question is filled if the CSV has a column with exactly the same name.

Dropdown values are typed into "Find an option" and the matching option is clicked
(exact match first, case-sensitive so `BAMS` and `bams` stay different; small typos are matched too).
If an option does not exist in the form, the log shows a warning and the rest of the row is still filled.

## Modes
* **Auto-submit OFF (untick):** every candidate opens in its own tab and the tabs stay open so you can review and press Submit yourself.
* **Auto-submit ON (default):** a row is submitted only if no required field is empty (e.g. Mobile No.). Otherwise the row is marked failed in the log and its tab is left open.

The log shows per row: fields filled, warnings, and "required still empty: ...".

Password is not stored. Run locally only. If Airtable changes its page layout the selectors in `filler.py` (top of file) may need a tweak.

## Deploy on Render
1. Put this folder in a GitHub repo (the `Dockerfile` and `render.yaml` are included).
2. Render -> New -> Blueprint (or Web Service -> Docker) -> pick the repo.
3. Set env var `APP_PASSWORD` (login for the site; username can be anything). `HEADLESS=1` is already set.
4. Use a paid instance (Starter or above): Chromium does not fit in the 512 MB free tier.
5. On the server your PC paths do not exist, so upload the CV files in the "CV files" field of the page; the CSV `Original CV` / `Edited CV` columns only need the file name (e.g. `dr_sharma.pdf`).
Jobs run inside the server (headless) and results appear in the Progress log. Uploaded files are lost on redeploy.

## Qualification selection
The page has a tick-list of all 64 Qualification options of the form (with search). Whatever you tick is selected in the form for **every** row (several ticks = several chips). If nothing is ticked, the CSV `Qualification` column is used. Matching is exact and case-sensitive, so `BAMS`, `bams`, `MBBS`, `Mbbs`, `mbbs` are different options. To add/remove options edit `static/qualification_options.json`.
