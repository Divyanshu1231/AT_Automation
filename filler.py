"""Form-filling logic for the Airtable *interface/shared form* (password protected).

Built from the real DOM seen in screenshots:
  * every question is a  <div data-testid="stackedLabel">  (label on first line)
  * text questions       -> <textarea>/<input> inside it
  * single/multi select  -> <div role="combobox" data-testid="autocomplete-button">
                            click opens a popup with an input placeholder "Find an option"
                            and coloured option chips; multi-select has
                            <ul aria-label="Selected options"> and a "+" combobox button
  * file questions       -> "Drop files here or click to browse"
  * required fields      -> bottom message "Some required fields are empty: <list>"
                            and the Submit button stays disabled
"""
import re
import difflib
from pathlib import Path

CONTAINER = '[data-testid="stackedLabel"]'
SEARCH = 'input[placeholder*="find an option" i]'


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def val(row, aliases, exclude=None):
    """First non-empty CSV value whose column matches one of the aliases."""
    cols = {norm(c): c for c in row.index}
    for a in aliases:
        k = norm(a)
        if k in cols:
            v = str(row[cols[k]] or "").strip()
            if v:
                return v
    for a in aliases:
        k = norm(a)
        for kk, c in cols.items():
            if k and k in kk and not (exclude and re.search(exclude, kk)):
                v = str(row[c] or "").strip()
                if v:
                    return v
    return ""


# key, regex on normalised form label, CSV column aliases, exclude regex for loose column match
RULES = [
    ("recruiter", r"^recruiter", ["Recruiter Name", "Recruiter"], None),
    ("edited_cv", r"edited (resume|cv)", ["Edited Resume/CV", "Edited Resume", "Edited CV"], None),
    ("original_cv", r"original (resume|cv)", ["Original CV", "Original Resume", "Resume Path", "CV Path"], r"edited"),
    ("name", r"name of candidate|candidate name|doctor name|^(full )?name$",
     ["Name of Candidates", "Candidate Name", "Doctor Name", "Full Name", "Name"], r"recruiter|vacancy|job"),
    ("email", r"\bemail|\bmail id", ["Email ID", "Email", "Mail ID"], None),
    ("mobile", r"mobile|phone|contact", ["Mobile No", "Mobile Number", "Mobile", "Phone Number", "Phone", "Contact Number", "Contact"], None),
    ("notice", r"notice", ["Notice Period", "Notice"], None),
    ("qualification", r"qualification|education", ["Qualification", "Education"], None),
    ("job", r"job title|specialis|specializ", ["Job title and Specialisation", "Job Title", "Specialisation", "Specialization"], None),
    ("state", r"\bstate\b", ["Preferred Location State", "Preferred State", "State"], None),
    ("city", r"location|city", ["Preferred Location City", "Preferred Location", "Current Location", "Location", "City"], r"state"),
    ("vacancy", r"vacancy|reference code", ["Vacancy Reference Code", "Vacancy Code", "Vacancy"], None),
    ("experience", r"experience", ["Total Experience", "Experience"], None),
    ("skills", r"skill", ["Key skills", "Skills"], None),
]
# value from the web-form UI that overrides (True) or is only a default for (False) the CSV
UI_DEFAULTS = {"recruiter": True, "qualification": True, "job": True}


def clean_mobile(v):
    v = str(v or "").strip()
    if re.fullmatch(r"[0-9]+(\.[0-9]+)?[eE]\+?[0-9]+", v):       # Excel scientific: 9.87654E+09
        try:
            from decimal import Decimal
            v = str(int(Decimal(v)))
        except Exception:
            pass
    v = re.sub(r"\.0+$", "", v)
    return v.strip()


def find_mobile(row):
    """Last resort: any column that looks like a phone column, or a column full of 10-digit numbers."""
    for c in row.index:
        if re.search(r"mob|phone|contact|whats ?app|cell|\bno\b|number|tel", norm(c)) and not re.search(r"notice|vacancy|code|ref", norm(c)):
            v = clean_mobile(row[c])
            if v:
                return v
    for c in row.index:
        v = clean_mobile(row[c])
        if re.fullmatch(r"(\+?91[\s-]?)?[6-9]\d{9}", v):
            return v
    return ""


def resolve(key, aliases, exclude, row, ui):
    csv_v = val(row, aliases, exclude)
    ui_v = (ui or {}).get(key, "")
    v = (ui_v or csv_v) if UI_DEFAULTS.get(key) else (csv_v or ui_v)
    if key == "mobile":
        v = clean_mobile(v) or find_mobile(row)
    return v


def match_rule(label):
    n = norm(label)
    for key, rx, aliases, exclude in RULES:
        if re.search(rx, n):
            return key, aliases, exclude
    return None, None, None


# ---------------------------------------------------------------- password
async def form_visible(page):
    try:
        return await page.locator(CONTAINER).first.is_visible()
    except Exception:
        return False


async def unlock(page, pw, timeout=45000):
    await dismiss_cookies(page)
    try:                                                   # visible password box OR visible form, whichever comes first
        await page.wait_for_selector(f'input[type="password"], {CONTAINER}', state="visible", timeout=timeout)
    except Exception:
        raise RuntimeError("Form questions did not load (slow network or wrong link)")
    p = page.locator('input[type="password"]')
    if await p.count() and await p.first.is_visible() and not await form_visible(page):
        await p.first.fill(pw)
        b = page.get_by_role("button", name=re.compile("enter|submit|continue", re.I))
        if await b.count():
            await b.first.click()
        else:
            await p.first.press("Enter")
        for _ in range(int(timeout / 400)):                # wait until the form appears (no fixed sleep)
            if await form_visible(page):
                break
            body = (await page.locator("body").inner_text())[:1000]
            if re.search("incorrect password|wrong password", body, re.I):
                raise RuntimeError("Airtable rejected the password")
            await page.wait_for_timeout(400)
        else:
            raise RuntimeError("Form questions did not load after entering the password")
    await dismiss_cookies(page)


async def dismiss_cookies(page):
    """The Transcend 'Cookie Preferences' banner sits on top of the Submit button - remove it."""
    for name in (r"reject all", r"^\s*agree\s*$"):
        for target in [page] + list(page.frames[1:]):
            try:
                b = target.get_by_role("button", name=re.compile(name, re.I))
                if await b.count() and await b.first.is_visible():
                    await b.first.click(timeout=3000)
                    await page.wait_for_timeout(500)
                    break
            except Exception:
                pass
    try:   # last resort: delete the banner so it can never intercept clicks
        await page.evaluate("""() => document.querySelectorAll(
            '#transcend-shadow-root, [id*=transcend i], iframe[src*=transcend i]').forEach(e => e.remove())""")
    except Exception:
        pass


async def form_submitted(page):
    """True once the form questions are gone or a thank-you message appears."""
    try:
        if await page.locator(CONTAINER).count() == 0:
            return True
        body = (await page.locator("body").inner_text())[:3000]
        return bool(re.search(r"thank you|has been submitted|response (has been )?(recorded|submitted)|submit another", body, re.I))
    except Exception:
        return True      # page navigated away / context changed -> treat as submitted


async def click_submit(page, debug_dir=None):
    """Click Submit, trying several methods, and verify that the form really got submitted."""
    await dismiss_cookies(page)
    btn = page.get_by_role("button", name=re.compile(r"^\s*submit\s*$", re.I)).last
    if not await btn.count():
        raise RuntimeError("Submit button not found")
    await btn.scroll_into_view_if_needed()
    for _ in range(12):                                   # wait until Airtable enables it
        if await btn.is_enabled():
            break
        await page.wait_for_timeout(300)
    if not await btn.is_enabled():
        raise RuntimeError("Submit stays disabled")

    async def m_click():
        await btn.click(timeout=4000)

    async def m_mouse():
        bb = await btn.bounding_box()
        await page.mouse.click(bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2)

    async def m_force():
        await btn.click(force=True, timeout=3000)

    async def m_js():
        await btn.evaluate("e => e.click()")

    async def m_keyboard():
        await btn.focus()
        await page.keyboard.press("Enter")

    async def m_requestsubmit():
        await btn.evaluate("e => { const f = e.closest('form'); if (f && f.requestSubmit) f.requestSubmit(e); else e.click(); }")

    tried = []
    for name, fn in (("click", m_click), ("mouse", m_mouse), ("force", m_force),
                     ("js", m_js), ("enter-key", m_keyboard), ("requestSubmit", m_requestsubmit)):
        try:
            await fn()
            tried.append(name)
            polls = 30 if len(tried) == 1 else 16        # 15 s after the 1st click (avoid double submit), 8 s later
        except Exception:
            tried.append(f"{name}(err)")
            polls = 8                                      # a click that raised may still have submitted (page changed)
        for _ in range(polls):
            if await form_submitted(page):
                return name
            await page.wait_for_timeout(500)
    msg = ""
    try:
        msg = (await page.locator("body").inner_text())[-400:].replace("\n", " | ")
    except Exception:
        pass
    if debug_dir:
        try:
            Path(debug_dir).mkdir(exist_ok=True)
            await page.screenshot(path=str(Path(debug_dir) / "submit_failed.png"), full_page=True)
        except Exception:
            pass
    raise RuntimeError(f"Submit clicked ({', '.join(tried)}) but form not submitted. Page bottom says: {msg}")


# ---------------------------------------------------------------- scanning
SCAN_JS = """els => els.map((el, i) => {
  const lines = (el.innerText || '').split('\\n').map(s => s.trim()).filter(Boolean);
  const label = (lines.find(l => l !== '*') || '').replace(/\\s*\\*+$/, '').trim();
  const txt = el.querySelector('textarea, input:not([type=file]):not([type=hidden])');
  return {
    i, label,
    file: !!el.querySelector('input[type=file]') || /click to browse|drop files/i.test(el.innerText || ''),
    combo: !!el.querySelector('[role=combobox]'),
    multi: !!el.querySelector('[aria-label="Selected options"]'),
    text: !!txt,
    req: /\\*/.test((lines[0]||'') + (lines[1]||'')) || !!el.querySelector('[aria-description*="Required" i]')
  };
})"""

# Tags every option leaf in the open dropdown with data-bf-i and returns their text.
OPTIONS_JS = """(sel) => {
  document.querySelectorAll('[data-bf-i]').forEach(e => e.removeAttribute('data-bf-i'));
  const inp = [...document.querySelectorAll('input')].find(
      i => i.offsetParent !== null && /find an option/i.test(i.placeholder || ''));
  if (!inp) return [];
  let root = inp.parentElement;
  while (root && root !== document.body && !(root.innerText || '').trim()) root = root.parentElement;
  if (!root) return [];
  const out = [];
  root.querySelectorAll('*').forEach(e => {
    if (e.children.length === 0 && e !== inp && e.offsetParent !== null) {
      const t = (e.textContent || '').trim();
      if (t) { e.setAttribute('data-bf-i', String(out.length)); out.push(t); }
    }
  });
  return out;
}"""


def choose(options, value):
    v = value.strip()
    lv = v.lower()
    for test in (lambda o: o == v,
                 lambda o: o.lower() == lv,
                 lambda o: o.lower().startswith(lv),
                 lambda o: lv in o.lower()):
        for i, o in enumerate(options):
            if test(o):
                return i
    low = [o.lower() for o in options]
    m = difflib.get_close_matches(lv, low, n=1, cutoff=0.8)   # handles typos like Anesthesialogist
    return low.index(m[0]) if m else None


SCROLL_JS = """(reset) => {
  const inp = [...document.querySelectorAll('input')].find(
      i => i.offsetParent !== null && /find an option/i.test(i.placeholder || ''));
  if (!inp) return false;
  let root = inp.parentElement;
  while (root && root !== document.body && !(root.innerText || '').trim()) root = root.parentElement;
  if (!root) return false;
  const sc = [root, ...root.querySelectorAll('*')].find(e => {
    const o = getComputedStyle(e).overflowY;
    return (o === 'auto' || o === 'scroll') && e.scrollHeight > e.clientHeight + 2;
  });
  if (!sc) return false;
  const before = sc.scrollTop;
  sc.scrollTop = reset ? 0 : before + sc.clientHeight * 0.8;
  return sc.scrollTop !== before;
}"""


def strict(options, value):
    v = value.strip()
    for test in (lambda o: o == v, lambda o: o.lower() == v.lower()):
        for i, o in enumerate(options):
            if test(o):
                return i
    return None


async def pick_option(page, container, value):
    combo = container.locator('[role="combobox"]').last      # "+" button for multi-select
    await combo.scroll_into_view_if_needed()
    await combo.click()
    search = page.locator(SEARCH)
    try:
        await search.first.wait_for(state="visible", timeout=5000)
    except Exception:
        raise RuntimeError("dropdown did not open")
    idx, options = None, []
    for q in dict.fromkeys([value, value[:4]]):
        await search.first.fill(q)
        await page.wait_for_timeout(200)
        for _ in range(12):                                     # wait until the filtered list is there
            if await page.evaluate(OPTIONS_JS, SEARCH):
                break
            await page.wait_for_timeout(100)
        await page.evaluate(SCROLL_JS, True)                    # back to top of the list
        for _ in range(40):                                     # long lists are scrolled (virtualised)
            options = await page.evaluate(OPTIONS_JS, SEARCH)
            idx = strict(options, value)                        # exact match anywhere in the list wins
            if idx is not None or not await page.evaluate(SCROLL_JS, False):
                break
            await page.wait_for_timeout(120)
        if idx is not None:
            break
        await page.evaluate(SCROLL_JS, True)
        await page.wait_for_timeout(150)
        options = await page.evaluate(OPTIONS_JS, SEARCH)
        idx = choose(options, value)                            # loose / typo match on the first page
        if idx is not None:
            break
    if idx is None:
        await page.keyboard.press("Escape")
        raise RuntimeError(f'option "{value}" not found in dropdown')
    await page.locator(f'[data-bf-i="{idx}"]').first.click()
    await page.wait_for_timeout(200)
    if await search.count() and await search.first.is_visible():
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(80)
    return options[idx]


async def upload(page, container, path):
    inputs = container.locator("input[type=file]")
    if await inputs.count():
        await inputs.first.set_input_files(str(path))
    else:
        async with page.expect_file_chooser(timeout=8000) as fc:
            await container.get_by_text(re.compile("click to browse|drop files", re.I)).first.click()
        await (await fc.value).set_files(str(path))
    try:
        await container.get_by_text(Path(path).name).first.wait_for(timeout=25000)
    except Exception:
        await page.wait_for_timeout(2500)


async def missing_required(page):
    body = await page.locator("body").inner_text()
    m = re.search(r"Some required fields are empty:?\s*(.*?)(?:Clear form|Submit|$)", body, re.S)
    if not m:
        return []
    return [l.strip(" •\t") for l in m.group(1).splitlines() if l.strip(" •\t")]


def _known_options():
    import json
    known = set()
    for name in ("job_options.json", "qualification_options.json"):
        try:
            known.update(o.lower() for o in json.load(open(Path(__file__).parent / "static" / name, encoding="utf-8")))
        except Exception:
            pass
    return known


KNOWN_OPTIONS = _known_options()


def split_multi(v):
    """Split a multi-select cell. ; and | always split. A part that is a known option (e.g. 'X ray, MRI technician')
    is kept whole; otherwise commas split too, except inside brackets like 'International (DHA, MOH)'."""
    out = []
    for part in re.split(r"\s*[;|]\s*", v):
        part = part.strip()
        if not part:
            continue
        if part.lower() in KNOWN_OPTIONS:
            out.append(part)
        else:
            out += [p.strip() for p in re.split(r",(?![^()]*\))", part) if p.strip()]
    return out


def _load_options(name):
    import json
    try:
        return json.load(open(Path(__file__).parent / "static" / name, encoding="utf-8"))
    except Exception:
        return []


QUAL_OPTIONS = _load_options("qualification_options.json")


def map_qualification(value):
    """Map a qualification cell (UI ticks or CSV text) to real dropdown options.
    Returns (parts, missed): parts = option names to select, missed = values that are not in the dropdown."""
    parts, missed = [], []
    squash = lambda s: re.sub(r"[^a-z0-9]+", "", s.lower())
    for p in split_multi(value):
        hit = None
        for test in (lambda o: o == p,                       # exact, case-sensitive (BAMS != bams)
                     lambda o: o.lower() == p.lower(),
                     lambda o: squash(o) == squash(p)):       # B.H.M.S == BHMS
            hit = next((o for o in QUAL_OPTIONS if test(o)), None)
            if hit:
                break
        if not hit and QUAL_OPTIONS:
            m = difflib.get_close_matches(p.lower(), [o.lower() for o in QUAL_OPTIONS], n=1, cutoff=0.88)
            hit = next((o for o in QUAL_OPTIONS if m and o.lower() == m[0]), None)
        if hit:
            if hit not in parts:
                parts.append(hit)
        elif not QUAL_OPTIONS:
            parts.append(p)                                  # no list available: try the text as typed
        else:
            missed.append(p)
    return parts, missed


# ---------------------------------------------------------------- one row
async def fill_form(page, row, ui):
    """Fill the open form. Returns (filled_labels, warnings)."""
    containers = page.locator(CONTAINER)
    info = await containers.evaluate_all(SCAN_JS)
    filled, warns = [], []
    for it in info:
        label = it["label"]
        key, aliases, exclude = match_rule(label)
        if key:
            value = resolve(key, aliases, exclude, row, ui)
        else:   # unknown question: use a CSV column with the same name, if any
            value = val(row, [label]) if label else ""
        if not value:
            if it.get("req"):
                warns.append(f'{label}: REQUIRED but no CSV value found (CSV columns: {", ".join(map(str, row.index))})')
            continue
        c = containers.nth(it["i"])
        try:
            if it["file"]:
                f = Path(value.strip().strip('"')).expanduser()
                if not f.exists() and (ui or {}).get("cv_dir"):      # server mode: match by file name in uploaded CVs
                    base = re.split(r"[\\/]", value.strip().strip('"'))[-1].lower()
                    hit = [p for p in Path(ui["cv_dir"]).glob("*") if p.name.lower() == base]
                    f = hit[0] if hit else f
                if not f.exists():
                    warns.append(f'{label}: file not found ({f})')
                    continue
                await upload(page, c, f)
            elif it["combo"]:
                if key == "qualification":
                    parts, missed = map_qualification(value)
                    if missed:
                        warns.append(f'{label}: not in dropdown -> {", ".join(missed)}')
                else:
                    parts = split_multi(value) if it["multi"] else [value]
                chips = await c.locator('[aria-label="Selected options"] li').all_inner_texts() if it["multi"] else []
                have = {t.strip() for t in chips}
                for part in parts:
                    if part.strip() in have:
                        continue                       # exactly this option is already selected
                    await pick_option(page, c, part)
            elif it["text"]:
                box = c.locator("textarea, input:not([type=file]):not([type=hidden])").first
                await box.scroll_into_view_if_needed()
                await box.fill(value)
                if (await box.input_value()) != value:      # React ignored fill -> type like a human
                    await box.click()
                    await page.keyboard.press("Control+A")
                    await page.keyboard.type(value, delay=20)
                await box.press("Tab")        # blur so Airtable re-validates required fields
                await page.wait_for_timeout(40)
            else:
                continue
            filled.append(label)
        except Exception as e:
            warns.append(f"{label}: {str(e)[:120]}")
    return filled, warns
