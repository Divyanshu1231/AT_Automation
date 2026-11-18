from typing import List
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from playwright.async_api import async_playwright
import pandas as pd, asyncio, uuid, re, shutil, os, base64, secrets
from fastapi import Request, Response

from filler import val, unlock, fill_form, missing_required, click_submit, CONTAINER

BASE = Path(__file__).parent
(BASE / "uploads").mkdir(exist_ok=True)
app = FastAPI(title="Airtable Bulk Form Filler")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
HEADLESS = os.getenv("HEADLESS", "0") == "1"        # Render/Docker: HEADLESS=1
APP_PASSWORD = os.getenv("APP_PASSWORD", "")        # protects the public URL (any username)
JOBS = {}
KEEP_OPEN = []   # browsers left open in "review" mode so the filled tabs are not closed


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    if APP_PASSWORD and request.url.path != "/healthz":
        ok = False
        h = request.headers.get("authorization", "")
        if h.lower().startswith("basic "):
            try:
                pw = base64.b64decode(h[6:]).decode().split(":", 1)[-1]
                ok = secrets.compare_digest(pw, APP_PASSWORD)
            except Exception:
                ok = False
        if not ok:
            return Response("Login required", 401, headers={"WWW-Authenticate": 'Basic realm="Bulk Form Filler"'})
    return await call_next(request)


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
async def home():
    return (BASE / "templates/index.html").read_text(encoding="utf-8")


def read_csv(path):
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return pd.read_csv(path, dtype=str, encoding=enc).fillna("")
        except UnicodeDecodeError:
            continue
    raise ValueError("Could not decode CSV (save it as UTF-8)")


async def run_job(jid, path, url, password, special, recname, submit, cv_dir=None, qual=""):
    job = JOBS[jid]
    job["status"] = "running"
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=HEADLESS, args=["--no-sandbox", "--disable-dev-shm-usage"])
    context = await browser.new_context(viewport={"width": 1280, "height": 900})
    try:
        df = read_csv(path)
        job["logs"].append("Mode: AUTO-SUBMIT ON" if submit else "Mode: AUTO-SUBMIT OFF -> only fills, tabs stay open (tick the checkbox to submit)")
        ui = {"job": special, "recruiter": recname, "qualification": qual, "cv_dir": str(cv_dir) if cv_dir else ""}
        for i, row in df.iterrows():
            name = val(row, ["Name of Candidates", "Candidate Name", "Doctor Name", "Full Name", "Name"], r"recruiter") or f"Row {i + 1}"
            page = await context.new_page()          # one tab per candidate
            keep_tab = not submit
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                await unlock(page, password)
                filled, warns = await fill_form(page, row, ui)
                missing = await missing_required(page)
                note = f"{len(filled)} fields filled"
                if warns:
                    note += " | ⚠ " + "; ".join(warns)
                if missing:
                    note += " | required still empty: " + ", ".join(missing)

                if submit:
                    if missing:
                        keep_tab = True
                        raise RuntimeError(note)
                    try:
                        how = await click_submit(page, str(BASE / 'debug'))
                    except Exception as e:
                        keep_tab = True
                        raise RuntimeError(f"{e} | " + note)
                    job["logs"].append(f"✓ {name}: submitted (via {how}) | {note}")
                    job["success"] += 1
                else:
                    job["logs"].append(f"✓ {name}: ready for review (tab left open) | {note}")
                    job["success"] += 1
            except Exception as e:
                job["failed"] += 1
                job["logs"].append(f"✗ {name}: {str(e)[:300]}")
            finally:
                if not keep_tab:
                    await page.close()
            job["done"] = i + 1
        job["status"] = "completed"
    except Exception as e:
        job["status"] = "error"
        job["logs"].append(str(e))
    finally:
        if submit:
            await browser.close()
            await pw.stop()
        else:                       # review mode: keep window + tabs alive for manual checking
            KEEP_OPEN.append((pw, browser))


@app.post("/api/start")
async def start(form_url: str = Form(...), form_password: str = Form(...), specialization: str = Form(""),
                recruiter_name: str = Form(""), submit_records: str = Form("false"), csv_file: UploadFile = File(...),
                qualification: List[str] = Form(default=[]), job_titles: List[str] = Form(default=[]), cv_files: List[UploadFile] = File(default=[])):
    if not form_url.startswith("https://airtable.com/"):
        return JSONResponse({"error": "Enter a valid Airtable form URL"}, 400)
    if not csv_file.filename.lower().endswith(".csv"):
        return JSONResponse({"error": "Upload a CSV file"}, 400)
    jid = str(uuid.uuid4())[:8]
    path = BASE / "uploads" / (jid + "_" + Path(csv_file.filename).name)
    with path.open("wb") as f:
        shutil.copyfileobj(csv_file.file, f)
    cv_dir = None
    real = [c for c in cv_files if c.filename]
    if real:                                           # CVs uploaded from the browser (needed on a server)
        cv_dir = BASE / "uploads" / (jid + "_cv")
        cv_dir.mkdir(exist_ok=True)
        for c in real:
            with (cv_dir / Path(c.filename.replace("\\", "/")).name).open("wb") as out:
                shutil.copyfileobj(c.file, out)
    try:
        df = read_csv(path)
    except Exception as e:
        return JSONResponse({"error": f"CSV read error: {e}"}, 400)
    if df.empty:
        return JSONResponse({"error": "CSV has no data rows"}, 400)
    JOBS[jid] = {"status": "queued", "total": len(df), "done": 0, "success": 0, "failed": 0, "logs": []}
    asyncio.create_task(run_job(jid, path, form_url, form_password,
                                "; ".join(j.strip() for j in job_titles if j.strip()) or specialization.strip(), recruiter_name.strip(),
                                True, cv_dir, "; ".join(q.strip() for q in qualification if q.strip())))   # always auto-submit (UI checkbox ignored)
    return {"job_id": jid, "total": len(df)}


@app.get("/api/status/{jid}")
async def status(jid):
    if jid not in JOBS:
        return JSONResponse({"error": "Job not found"}, 404)
    return JOBS[jid]
