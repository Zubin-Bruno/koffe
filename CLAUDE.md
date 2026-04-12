# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the project

```bash
# Start the web server (http://localhost:8000)
python -m uvicorn koffe.api.main:app --reload

# Run all scrapers immediately
python scripts/scrape_now.py

# Seed DB with sample data (no scraping needed, good for UI work)
python scripts/seed_db.py
```

All commands must be run from the **project root** (the `cafecito/` folder on the Desktop, where `pyproject.toml` lives). The `koffe/` directory is the Python package inside it — not the root.

## Installing / setup

```bash
# Install dependencies
python -m uv pip install -e .

# Install Playwright's Chromium browser (only needed once)
python -m playwright install chromium
```

## Architecture

**Data flow:**
```
APScheduler (3am daily) → runner.py → Playwright scrapes roaster sites → upsert → koffe.db
FastAPI → reads koffe.db → serves Jinja2+HTMX HTML + JSON API
```

**Scraper pattern:** Every roaster is one file in `koffe/scrapers/sites/`. Each file defines a class that inherits `BaseScraper` and implements `async def scrape(self, browser) -> list[CoffeeData]`. The runner auto-discovers the class and handles all DB writes — scrapers only return data. Use `koffe/scrapers/sites/sample_roaster.py` as a template.

**Upsert logic:** `runner.py` matches on `(roaster_id, external_id)`. Coffees not seen in a run are marked `is_available=False` (never deleted). A `Roaster` row must exist in the DB before its scraper will run — the runner loads active roasters from the DB and instantiates their scraper via the `scraper_module` dotted path stored in the row.

**HTMX partial:** `GET /coffees` returns only the card grid HTML (no full page). The filter form in `index.html` targets `#results` via `hx-get="/coffees"`, so filters update without a full reload.

**Price storage:** Prices are stored as integer cents (e.g. `499000` = $4,990 ARS) to avoid float bugs. Use `parse_price_cents()` from `utils.py` when scraping.

**Intensity fields:** `acidity`, `sweetness`, and `body` are stored as integers 1–5. Use `normalize_intensity()` from `utils.py` to convert raw text ("vibrant acidity", "baja", "3", etc.) to a number. Returns `None` if unparseable — all these fields are nullable.

## Key normalization helpers (`koffe/scrapers/utils.py`)

| Function | Purpose |
|---|---|
| `parse_price_cents(raw)` | Handles ARS/USD formats, thousands separators |
| `parse_weight_grams(raw)` | Parses "250g", "1kg", "500 gr" |
| `normalize_intensity(raw)` | Text or number → 1–5 int for acidity/sweetness/body |
| `normalize_process(raw)` | → "Natural", "Washed", "Honey", "Anaerobic" |
| `normalize_roast(raw)` | → "Light", "Medium", "Medium-Dark", "Dark" |

## Vision AI (`koffe/scrapers/vision.py`)

Some roasters embed intensity data (acidity, body, sweetness) in product **images** as bar charts instead of HTML text. The `vision.py` module handles this by sending images to an AI vision model (Gemini 2.0 Flash via OpenRouter) and parsing the response.

Two functions are available:
- `extract_intensities_from_image(image_url)` — for Puerto Blest; reads a 1–10 scale chart and converts to 1–5.
- `extract_fuego_intensities(image_url)` — for Fuego Tostadores; reads a 1–5 scale chart directly.

Both return `None` values for all fields if `OPENROUTER_API_KEY` is not set or the call fails (graceful fallback).

## Chat AI (`koffe/api/routes/chat.py`)

`POST /api/chat` — an AI barista assistant. The user describes what they want (in Argentine Spanish or English) and the LLM searches the catalog using tool calling.

**How it works:**
1. Receives the full conversation history from the frontend.
2. Sends it to Gemini 2.0 Flash (via OpenRouter) with a `search_coffees` tool definition.
3. The LLM calls `search_coffees` with filter parameters → we execute the DB query.
4. A second LLM call turns the results into a friendly text response.
5. Everything streams back to the browser via SSE (Server-Sent Events).

Uses `OPENROUTER_API_KEY` and `CHAT_MODEL` (env var, defaults to `google/gemini-2.0-flash-001`).

## Feedback (`koffe/api/routes/feedback.py`)

`POST /feedback` — saves user suggestions (roaster requests, general feedback) to a `Feedback` table in the DB. Returns an HTML snippet for HTMX to swap in as a thank-you message.

## Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `OPENROUTER_API_KEY` | For AI features | Vision extraction + chat assistant |
| `CHAT_MODEL` | Optional | Override the default chat LLM (default: `google/gemini-2.0-flash-001`) |

## DB

SQLite file at `data/koffe.db` (gitignored). Tables are created automatically on startup via `create_tables()` — no migration needed for a fresh install. For schema changes on an existing DB, use Alembic (`alembic/` is set up but migrations must be authored manually).

Inspect the DB visually with [DB Browser for SQLite](https://sqlitebrowser.org/).

## Deployment

The site is hosted on **Render.com** and auto-deploys on every push to `master`.

**Production URL:** https://www.xn--busca-kaf-j4a.com.ar/ (buscakafé.com.ar)

## GitHub

The `gh` CLI is not in the default shell PATH. Always invoke it with the full path:

```bash
/c/Program\ Files/GitHub\ CLI/gh
```

**Do NOT auto-commit or auto-push.** Always wait for the user to test changes and give explicit approval before committing. When asked to commit, push to `origin master` (never `cafeito`).

## Adding a new roaster

1. Insert a row into `roasters` (via `seed_db.py` or directly in DB Browser)
2. Create `koffe/scrapers/sites/<slug>.py` — copy from `sample_roaster.py`
3. Set `roaster_slug` to match the DB slug and implement `scrape()`
4. Run `python scripts/scrape_now.py` to verify

## Explaining the user
The user is a begginer in the world of coding, he only knows the basics of python, BS4, and SQL.
You should explain carefully, with lots of detail and in simple and understandable language all the things that you do and why are you doing them
Always do explain after and before performing any kind of action, from planning, to writting code, to reviewing code, executing commands, etc.