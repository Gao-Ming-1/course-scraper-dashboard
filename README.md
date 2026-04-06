# SkillsFuture Intelligence Dashboard

A full-stack web application for scraping, analysing, and visualising course data from [MySkillsFuture.gov.sg](https://www.myskillsfuture.gov.sg) — Singapore's official government portal for SkillsFuture courses.

**Live demo:** [https://course-scraper-dashboard-1.onrender.com](https://course-scraper-dashboard-1.onrender.com)

![Dark mode dashboard](https://img.shields.io/badge/theme-dark%20%2F%20light-00e5ff?style=flat-square)
![Python](https://img.shields.io/badge/python-3.10%2B-3776ab?style=flat-square&logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/flask-3.0-black?style=flat-square&logo=flask)
![Playwright](https://img.shields.io/badge/playwright-1.43-45ba4b?style=flat-square&logo=playwright&logoColor=white)
![Chart.js](https://img.shields.io/badge/chart.js-4.4-ff6384?style=flat-square)

---

## Features

### Scraper
- Enter any **course keyword** (e.g. `data analytics`, `python`, `project management`)
- Set a **max page limit** (1–200) or enter `0` / leave blank to **scrape all pages**
- Real-time **progress bar** powered by Server-Sent Events (SSE) with auto-reconnect
- Handles duplicate detection — scrape the same keyword multiple times using **Append** or **Overwrite** modes
- Scrapes: course title, provider, URL, full fee, post-funding fee, star rating, review count, next available date

### Dashboard — 5 Interactive Charts
| Chart | Description |
|---|---|
| **Courses per Training Provider** | Horizontal bar chart; click any bar to filter the data table |
| **Course Fee Distribution** | Histogram with auto or custom bin sizes; filterable by provider |
| **Star Rating vs Review Volume** | Scatter plot coloured by provider |
| **Top Rated Courses** | Ranked bar chart with min-review threshold |
| **Course Fee vs Star Rating** | Scatter plot to explore price–quality correlation |

All charts support:
- **Provider multi-select filter** with search and Select All / Clear All
- **PNG / JPG export** per chart
- **Click-through** on bar charts → jumps to filtered Data Table

### Data Table
- Paginated (20 rows per page) with smart page number truncation
- **Free-text search** across title and provider
- **Provider dropdown filter**
- **Sortable** by rating, fee, or review count
- Filter banner shows active chart filter with one-click clear

### Other
- **Dark / Light theme** toggle, persisted in `localStorage`
- **Excel export** of the full dataset for the current keyword
- **Data Credibility panel** — shows total records, duplicates removed, and last scraped timestamp

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.10+, Flask 3 |
| Scraper | Playwright (Chromium headless) |
| Frontend | Vanilla HTML + CSS + JavaScript |
| Charts | Chart.js 4.4 |
| Storage | Excel files (openpyxl) |
| Deployment | Docker on Render |

---

## Local Setup

### Prerequisites
- Python 3.10+
- Git

### Installation

```bash
git clone https://github.com/your-username/skillsfuture-dashboard.git
cd skillsfuture-dashboard

python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

pip install -r requirements.txt
playwright install chromium
```

### Run

```bash
python app.py
```

Open [http://127.0.0.1:5000](http://127.0.0.1:5000) in your browser.

---

## Project Structure

```
skillsfuture-dashboard/
├── app.py                  # Flask backend — routes, scraper, SSE, Excel
├── Dockerfile              # Playwright base image for Render deployment
├── render.yaml             # Render service config
├── requirements.txt
├── templates/
│   └── index.html          # Complete frontend (HTML + CSS + JS, single file)
└── skillsfuture_data/      # Auto-created; stores scraped Excel files
```

---

## Deployment (Render)

This app is deployed using Docker on [Render](https://render.com) to ensure Chromium's system dependencies are available.

### `Dockerfile`
```dockerfile
FROM mcr.microsoft.com/playwright/python:v1.43.0-jammy

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install chromium

COPY . .
EXPOSE 10000
CMD ["gunicorn", "app:app", "--workers", "1", "--threads", "4", "--timeout", "120", "--bind", "0.0.0.0:10000"]
```

### `render.yaml`
```yaml
services:
  - type: web
    name: skillsfuture-dashboard
    env: docker
    plan: free
    dockerfilePath: ./Dockerfile
    envVars:
      - key: PORT
        value: 10000
```

> **Note:** `--workers 1` is required. The scraper stores job state in an in-memory dict (`JOBS`), so multiple Gunicorn workers would cause "Job not found" errors as requests land on different processes.

---

## How It Works

```
Browser → POST /api/scrape/start → Flask spawns daemon thread
                                        │
                                   asyncio loop
                                   Playwright scrapes page by page
                                        │ updates JOBS dict
                                        ▼
Browser ← GET /api/scrape/progress/id ← SSE stream (1s interval + 15s keepalive ping)

On completion → GET /api/data/<keyword> → renders charts + table
```

SSE keepalive pings (`: ping\n\n`) are sent every 15 seconds to prevent Render's reverse proxy from closing idle connections.

---

## Known Limitations

- **Ephemeral storage on Render** — scraped Excel files are lost when the service restarts (free tier containers are stateless). For persistent storage, connect an S3 bucket or database.
- **Single worker** — only one scrape job can run at a time per instance.
- **Rate limiting** — MySkillsFuture may throttle aggressive scraping. Use reasonable page limits and avoid scraping the same keyword repeatedly in quick succession.

---

## License

MIT
