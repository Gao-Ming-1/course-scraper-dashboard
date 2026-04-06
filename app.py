#!/usr/bin/env python3
"""
SkillsFuture Dashboard  ─  Flask Backend  (stateless / Render-compatible)
==========================================================================
All scraped data is held in-memory for the lifetime of the server process.
No files are written to disk — no history persistence between restarts.

Routes:
  GET  /                        → Main UI
  POST /api/scrape/start        → Launch scraping job
  GET  /api/scrape/progress/<id>→ SSE stream for real-time progress
  GET  /api/data/<keyword>      → Return JSON data for a keyword
  GET  /api/check/<keyword>     → Check if keyword has in-memory data
  GET  /api/export/<keyword>    → Download Excel for keyword
  DELETE /api/delete/<keyword>  → Remove keyword from memory
"""

import sys, asyncio, re, json, uuid, threading, time, io, os
from datetime import datetime, timezone

# ── Windows asyncio fix ───────────────────────────────────────
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

try:
    import nest_asyncio
    nest_asyncio.apply()
except ImportError:
    pass

from flask import (Flask, render_template, request, jsonify,
                   Response, send_file, abort)
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

# ── App ───────────────────────────────────────────────────────
app = Flask(__name__)

# ── In-memory stores ─────────────────────────────────────────
# { keyword: [course_dict, ...] }
STORE: dict[str, list[dict]] = {}
STORE_LOCK = threading.Lock()

# { job_id: { status, pages_done, ... } }
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


# ─────────────────────────────────────────────────────────────
# EXCEL EXPORT
# ─────────────────────────────────────────────────────────────

COLUMNS = [
    ("Course Title",               52),
    ("Training Provider",          32),
    ("Course URL",                 40),
    ("Full Course Fee",            18),
    ("After SkillsFuture Funding", 22),
    ("Star Rating",                14),
    ("No. of Ratings",             14),
    ("Upcoming Course Date",       22),
    ("Scraped At",                 26),
]

_thin       = Side(style="thin", color="C0C0C0")
HEADER_FILL = PatternFill("solid", fgColor="1A4E8C")
ALT_FILL    = PatternFill("solid", fgColor="EBF0FA")
HEADER_FONT = Font(name="Calibri", bold=True, color="FFFFFF", size=11)
BODY_FONT   = Font(name="Calibri", size=10)
TITLE_FONT  = Font(name="Calibri", bold=True, size=13, color="1A4E8C")
BORDER      = Border(left=_thin, right=_thin, top=_thin, bottom=_thin)
WRAP        = Alignment(wrap_text=True, vertical="top")
CENTER      = Alignment(horizontal="center", vertical="top")


def build_excel(courses: list[dict], keyword: str) -> io.BytesIO:
    wb = Workbook()
    ws = wb.active
    ws.title = "SkillsFuture Courses"
    ws.merge_cells(f"A1:{get_column_letter(len(COLUMNS))}1")
    ws["A1"] = (
        f'SkillsFuture: "{keyword}"  |  '
        f"Exported {datetime.now().strftime('%d %b %Y %H:%M')}"
    )
    ws["A1"].font = TITLE_FONT
    ws["A1"].alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[1].height = 28

    for col_idx, (header, width) in enumerate(COLUMNS, 1):
        cell = ws.cell(row=2, column=col_idx, value=header)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = CENTER
        cell.border = BORDER
        ws.column_dimensions[get_column_letter(col_idx)].width = width
    ws.row_dimensions[2].height = 22

    for row_idx, course in enumerate(courses, 3):
        fill = ALT_FILL if row_idx % 2 == 0 else None
        for col_idx, (key, _) in enumerate(COLUMNS, 1):
            val = course.get(key, "")
            cell = ws.cell(row=row_idx, column=col_idx, value=val)
            cell.font = BODY_FONT
            cell.alignment = WRAP
            cell.border = BORDER
            if fill:
                cell.fill = fill
        ws.row_dimensions[row_idx].height = 40

    ws.freeze_panes = "A3"
    ws.auto_filter.ref = f"A2:{get_column_letter(len(COLUMNS))}2"

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


# ─────────────────────────────────────────────────────────────
# SCRAPER
# ─────────────────────────────────────────────────────────────

SEARCH_BASE = (
    "https://www.myskillsfuture.gov.sg/content/portal/en/"
    "portal-search/portal-search.html"
    "?fq=Course_Supp_Period_To_1%3A%5B2026-03-18T00%3A00%3A00Z%20TO%20*%5D"
    "&fq=IsValid%3Atrue&q="
)


async def _scrape_page(page) -> list[dict]:
    await page.wait_for_selector("div.card", timeout=30_000, state="attached")
    await asyncio.sleep(1.5)
    raw = await page.evaluate("""
    () => {
        const results = [];
        for (const card of document.querySelectorAll("div.card")) {
            const providerEl = card.querySelector("div.course-provider a");
            const provider   = providerEl ? providerEl.innerText.trim() : "N/A";
            const titleEl    = card.querySelector(
                "h5.card-title a[data-bind*='courseTitleShorten']");
            const title      = titleEl ? titleEl.innerText.trim() : "";
            if (!title) continue;
            const courseUrl  = titleEl ? (titleEl.href || "") : "";
            const dateEl     = card.querySelector(
                "li.list-group-item strong[data-bind*='formatDate']");
            const upcomingDate = dateEl ? dateEl.innerText.trim() : "None listed";
            const starsHolder  = card.querySelector("div.stars-holder");
            let starRating = null;
            if (starsHolder) {
                const filled = starsHolder.querySelectorAll("i.fa-solid.fa-star").length;
                const half   = starsHolder.querySelectorAll(
                    "i.fa-solid.fa-star-half-stroke, i.fa-solid.fa-star-half").length;
                if (filled > 0 || half > 0)
                    starRating = parseFloat((filled + half * 0.5).toFixed(1));
            }
            const ratingsEl = card.querySelector("span[data-bind*='NumberOfRespondents']");
            let numRatings = "0";
            if (ratingsEl) {
                const m = ratingsEl.innerText.match(/(\\d+)/);
                if (m) numRatings = m[1];
            }
            const feeEl   = card.querySelector(
                "strong[data-bind*='Tol_Cost_of_Trn_Per_Trainee']");
            const fullFee = feeEl ? feeEl.innerText.trim() : "N/A";
            const nettDiv = card.querySelector("div[name='nettfee']");
            let nettFee   = "N/A";
            if (nettDiv) {
                const nettEl = nettDiv.querySelector("strong");
                if (nettEl) nettFee = nettEl.innerText.trim();
            }
            results.push({ title, provider, courseUrl, fullFee, nettFee,
                           starRating, numRatings, upcomingDate });
        }
        return results;
    }
    """)
    return [
        {
            "Course Title":                c["title"],
            "Training Provider":           c["provider"],
            "Course URL":                  c["courseUrl"],
            "Full Course Fee":             c["fullFee"],
            "After SkillsFuture Funding":  c["nettFee"],
            "Star Rating":                 c["starRating"],
            "No. of Ratings":              int(c["numRatings"]),
            "Upcoming Course Date":        c["upcomingDate"],
            "Scraped At":                  datetime.now(timezone.utc).isoformat(),
        }
        for c in raw if c.get("title")
    ]


async def _run_scraper(job_id: str, keyword: str, max_pages: int, mode: str):
    import urllib.parse
    from playwright.async_api import async_playwright

    url        = SEARCH_BASE + urllib.parse.quote(keyword)
    new_courses: list[dict] = []
    seen_titles: set[str]   = set()
    warnings:    list[str]  = []
    page_num = 0

    def _set(key, val):
        with JOBS_LOCK:
            JOBS[job_id][key] = val

    _set("status",      "running")
    _set("pages_done",  0)
    _set("total_pages", max_pages)
    _set("warnings",    [])
    _set("error",       None)

    scrape_all = max_pages >= 999_999

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 900},
            )
            page = await context.new_page()

            _set("message", f"Opening MySkillsFuture for '{keyword}'…")
            await page.goto(url, wait_until="networkidle", timeout=90_000)
            await asyncio.sleep(3)

            for page_num in range(1, max_pages + 1):
                _set("pages_done", page_num - 1)
                if scrape_all:
                    _set("message",      f"Scraping page {page_num} (all-pages mode)…")
                    _set("total_pages",  page_num)
                else:
                    _set("message", f"Scraping page {page_num} of {max_pages}…")

                try:
                    page_courses = await _scrape_page(page)
                except Exception as e:
                    warnings.append(f"Page {page_num}: {str(e)[:120]}")
                    _set("warnings", warnings)
                    break

                fresh = [c for c in page_courses if c["Course Title"] not in seen_titles]
                seen_titles.update(c["Course Title"] for c in fresh)
                new_courses.extend(fresh)

                # Check for next page
                next_btn     = await page.query_selector('a.page-link[aria-label="View next page"]')
                if not next_btn:
                    break
                parent_li    = await next_btn.evaluate_handle("el => el.closest('li')")
                parent_class = await (await parent_li.get_property("className")).json_value()
                if "disabled" in parent_class:
                    break

                await next_btn.click()
                await page.wait_for_load_state("networkidle")
                await asyncio.sleep(2)

            _set("pages_done", page_num)
            await browser.close()

        if not new_courses:
            _set("status",  "no_results")
            _set("message", f"No courses found for '{keyword}'.")
            return

        # ── Merge into in-memory store ────────────────────────
        with STORE_LOCK:
            if mode == "overwrite" or keyword not in STORE:
                STORE[keyword] = new_courses
                added = len(new_courses)
            else:
                existing = STORE[keyword]
                seen = {(c.get("Course Title",""), c.get("Training Provider",""))
                        for c in existing}
                unique_new = [
                    c for c in new_courses
                    if (c.get("Course Title",""), c.get("Training Provider","")) not in seen
                ]
                STORE[keyword] = existing + unique_new
                added = len(unique_new)
            total = len(STORE[keyword])

        _set("status",  "completed")
        _set("message", f"Done! {added} new courses added ({total} total).")
        _set("warnings", warnings)

    except Exception as e:
        _set("status",  "error")
        _set("error",   str(e))
        _set("message", f"Scraping failed: {str(e)[:200]}")


def _thread_scrape(job_id, keyword, max_pages, mode):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_run_scraper(job_id, keyword, max_pages, mode))
    finally:
        loop.close()


# ─────────────────────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/check/<keyword>")
def api_check(keyword):
    with STORE_LOCK:
        exists = keyword in STORE
        count  = len(STORE.get(keyword, []))
    return jsonify({"exists": exists, "count": count})


@app.route("/api/data/<keyword>")
def api_data(keyword):
    with STORE_LOCK:
        courses = list(STORE.get(keyword, []))
    if not courses:
        return jsonify({"error": "No data found"}), 404
    return jsonify(courses)


@app.route("/api/scrape/start", methods=["POST"])
def api_scrape_start():
    body      = request.get_json(force=True)
    keyword   = (body.get("keyword") or "").strip()
    raw_pages = body.get("max_pages", 5)
    mode      = body.get("mode", "overwrite")

    try:
        max_pages = int(raw_pages)
    except (TypeError, ValueError):
        max_pages = 0

    scrape_all = max_pages <= 0
    if scrape_all:
        max_pages = 999_999

    if not keyword:
        return jsonify({"error": "Keyword is required."}), 400
    if not scrape_all and max_pages > 200:
        return jsonify({"error": "MAX_PAGES must be 1–200 (or 0 for all)."}), 400
    if mode not in ("overwrite", "append"):
        return jsonify({"error": "Invalid mode."}), 400

    job_id = str(uuid.uuid4())
    with JOBS_LOCK:
        JOBS[job_id] = {
            "status":      "queued",
            "keyword":     keyword,
            "pages_done":  0,
            "total_pages": max_pages,
            "scrape_all":  scrape_all,
            "message":     "Starting…",
            "warnings":    [],
            "error":       None,
        }

    t = threading.Thread(
        target=_thread_scrape, args=(job_id, keyword, max_pages, mode), daemon=True
    )
    t.start()
    return jsonify({"job_id": job_id})


@app.route("/api/scrape/progress/<job_id>")
def api_scrape_progress(job_id):
    def generate():
        while True:
            with JOBS_LOCK:
                job = JOBS.get(job_id)
            if not job:
                yield f"data: {json.dumps({'error': 'Job not found'})}\n\n"
                break
            with STORE_LOCK:
                count = len(STORE.get(job.get("keyword",""), []))
            payload = {
                "status":     job["status"],
                "pages_done": job["pages_done"],
                "total":      job["total_pages"],
                "scrape_all": job.get("scrape_all", False),
                "message":    job["message"],
                "count":      count,
                "warnings":   job.get("warnings", []),
                "error":      job.get("error"),
            }
            yield f"data: {json.dumps(payload)}\n\n"
            if job["status"] in ("completed", "error", "no_results"):
                break
            time.sleep(1)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/export/<keyword>")
def api_export(keyword):
    with STORE_LOCK:
        courses = list(STORE.get(keyword, []))
    if not courses:
        abort(404)
    buf = build_excel(courses, keyword)
    safe = re.sub(r'[\\/*?:"<>|]', "_", keyword).replace(" ", "_").lower()
    return send_file(
        buf,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=f"skillsfuture_{safe}.xlsx",
    )


@app.route("/api/delete/<keyword>", methods=["DELETE"])
def api_delete(keyword):
    with STORE_LOCK:
        STORE.pop(keyword, None)
    return jsonify({"ok": True})


# ─────────────────────────────────────────────────────────────
# ENTRYPOINT
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"SkillsFuture Dashboard running at http://127.0.0.1:{port}")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
