"""
Scraper for Flat and White — https://flatnwhite.com
Argentine specialty roaster on WooCommerce/WordPress.

Primary method: WooCommerce Store API (JSON, no browser needed).
Fallback: Playwright + selectolax (kept for resilience if the API is ever blocked).

Data is extracted from the product description HTML which contains a
"Trazabilidad" or "Ficha Técnica" section with origin, process, variety, etc.
"""

import json
import re

import httpx
from loguru import logger
from selectolax.parser import HTMLParser

from koffe.scrapers.base import BaseScraper, CoffeeData
from koffe.scrapers.utils import (
    clean_text,
    normalize_brew_methods,
    normalize_origin,
    normalize_process,
    normalize_tasting_notes,
    parse_price_cents,
)

LISTING_URL = "https://flatnwhite.com/cafe-de-especialidad-flatwhite-argentina/"

# WooCommerce Store API — public, no auth needed.
# category=17 is "Cafe de especialidad" on flatnwhite.com.
WC_API_URL = "https://flatnwhite.com/wp-json/wc/store/v1/products"
COFFEE_CATEGORY_ID = 17


class FlatAndWhiteScraper(BaseScraper):
    roaster_slug = "flat-and-white"
    start_url = "https://flatnwhite.com"

    # ── Main entry point ─────────────────────────────────────────────────

    async def scrape(self, browser) -> list[CoffeeData]:
        # Try the WooCommerce JSON API first — it bypasses CAPTCHA/anti-bot
        # because it's a data endpoint, not a rendered page.
        self._ip_blocked = False
        coffees = await self._scrape_via_api()
        if coffees:
            return coffees

        # If the API returned an HTML CAPTCHA page, Playwright will also be
        # blocked by the same IP check — skip it to save 15+ seconds.
        if self._ip_blocked:
            logger.warning(
                "[flat-and-white] IP is blocked by anti-bot — skipping Playwright fallback. "
                "Use scripts/push_coffees.py from a non-datacenter IP to update production."
            )
            return []

        # Fallback: use Playwright to load the site in a real browser.
        # This may fail on datacenter IPs due to CAPTCHA, but works locally.
        logger.warning("[flat-and-white] API returned 0 products, falling back to Playwright")
        return await self._scrape_via_playwright(browser)

    # ── WooCommerce API approach (primary) ────────────────────────────────

    async def _scrape_via_api(self) -> list[CoffeeData]:
        """Fetch all coffee products from the WooCommerce Store API."""
        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=30.0,
                headers={"User-Agent": "Mozilla/5.0"},
            ) as client:
                resp = await client.get(
                    WC_API_URL,
                    params={"per_page": 100, "category": COFFEE_CATEGORY_ID},
                )
                resp.raise_for_status()

                content_type = resp.headers.get("content-type", "")
                if "text/html" in content_type:
                    logger.error(
                        f"[flat-and-white] API returned HTML instead of JSON — likely CAPTCHA/IP block. "
                        f"Preview: {resp.text[:200]}"
                    )
                    self._ip_blocked = True
                    return []

                products = resp.json()
        except Exception as e:
            logger.warning(f"[flat-and-white] WooCommerce API request failed: {e}")
            return []

        logger.debug(f"[flat-and-white] API returned {len(products)} products")

        coffees: list[CoffeeData] = []
        for product in products:
            try:
                coffee = self._parse_api_product(product)
                if coffee:
                    coffees.append(coffee)
            except Exception as e:
                slug = product.get("slug", "?")
                logger.warning(f"[flat-and-white] Failed to parse API product '{slug}': {e}")

        logger.info(f"[flat-and-white] Total coffees found (API): {len(coffees)}")
        return coffees

    def _parse_api_product(self, product: dict) -> CoffeeData | None:
        """Convert a single WooCommerce API product JSON into CoffeeData."""
        name = clean_text(product.get("name", ""))
        if not name:
            return None

        slug = product.get("slug", "")
        url = product.get("permalink", "")
        external_id = slug or url.rstrip("/").split("/")[-1]

        # --- Price ---
        # The API returns prices in centavos (smallest currency unit).
        # Our DB also stores prices in cents, so we use the value directly.
        prices = product.get("prices", {})
        price_str = prices.get("price", "")
        price_cents = int(price_str) if price_str and price_str.isdigit() else None

        # --- Image ---
        images = product.get("images", [])
        image_url = images[0]["src"] if images else None

        # --- Tags (needed for process fallback and brew methods) ---
        tags = product.get("tags", [])

        # --- Description → Ficha Técnica / Trazabilidad ---
        description_html = product.get("description", "")
        ficha = self._extract_ficha_from_html(description_html)

        # Origin
        origin_raw = ficha.get("origen", "")
        origin_country = normalize_origin(None, origin_raw) if origin_raw else None
        if not origin_country:
            origin_country = normalize_origin(name, None)

        # Variety
        variety = (
            clean_text(ficha.get("varietal"))
            or clean_text(ficha.get("varietales"))
        )

        # Process
        process = normalize_process(ficha.get("beneficio") or ficha.get("proceso"))
        if not process:
            process = normalize_process(name)
        if not process:
            # Fallback: check API tags (e.g. Tanzania has "Lavado" as a tag)
            for tag in tags:
                tag_name = tag.get("name", "")
                process = normalize_process(tag_name)
                if process:
                    break

        # Altitude
        altitude_masl = self._parse_altitude(ficha.get("altura"))

        # Tasting notes
        tasting_notes_raw = (
            ficha.get("notas de cata")
            or ficha.get("notas")
            or ficha.get("perfil")
            or ficha.get("perfil sensorial")
            or ficha.get("perfil de sabor")
        )
        tasting_notes = None
        if tasting_notes_raw:
            parts = re.split(r",|\by\b", tasting_notes_raw, flags=re.IGNORECASE)
            parts = [p.strip() for p in parts if p.strip()]
            tasting_notes = normalize_tasting_notes(parts)

        # Brew methods — from API tags
        tags = product.get("tags", [])
        brew_methods = self._brew_methods_from_tags(tags)

        attributes = {}
        if tasting_notes:
            attributes["tasting_notes"] = tasting_notes

        logger.debug(
            f"[flat-and-white] {name} | {price_cents} ARS-cents | "
            f"origin={origin_country} | process={process}"
        )

        return CoffeeData(
            external_id=external_id,
            name=name,
            url=url,
            price_cents=price_cents,
            currency="ARS",
            weight_grams=250,
            is_available=True,
            image_url=image_url,
            origin_country=origin_country,
            variety=variety,
            process=process,
            altitude_masl=altitude_masl,
            acidity=None,
            sweetness=None,
            body=None,
            brew_methods=brew_methods,
            attributes=attributes,
        )

    # ── HTML description parsing ──────────────────────────────────────────

    def _extract_ficha_from_html(self, html: str) -> dict[str, str]:
        """
        Parse the product description HTML for structured data.

        Handles two formats that Flat N' White uses:

        1. "Trazabilidad" section with <li> tags (newer products like Papua):
           <li><strong>Origen:</strong> Papúa Nueva Guinea</li>

        2. "Ficha Técnica" plain-text section (older products like Colombia):
           Origen: Blend regional de Huila y Tolima, Colombia
           Varietales: Caturra, Colombia, Typica

        3. Plain-text key:value lines without any heading (Ethiopia):
           Origen: Etiopía<br/>
           Región: Chelbesa<br/>
        """
        if not html:
            return {}

        tree = HTMLParser(html)
        fields: dict[str, str] = {}

        # Strategy 1: Look for <li> elements with <strong>Key:</strong> Value
        for li in tree.css("li"):
            strong = li.css_first("strong")
            if not strong:
                continue
            key_text = clean_text(strong.text()).rstrip(":")
            if not key_text:
                continue
            # The value is the full <li> text minus the <strong> part
            full_text = clean_text(li.text())
            value = full_text.replace(strong.text().strip(), "", 1).strip().lstrip(":")
            value = value.strip()
            if key_text and value:
                fields[key_text.lower()] = value

        # Strategy 2: Plain-text "Key: Value" lines (from the raw text)
        if not fields:
            page_text = tree.body.text() if tree.body else html
            for line_match in re.finditer(
                r"([A-Za-záéíóúñÁÉÍÓÚÑ\s]+):\s*(.+?)(?:\n|$)",
                page_text,
            ):
                key = line_match.group(1).strip().lower()
                value = line_match.group(2).strip()
                if key and value and len(key) < 30:
                    fields[key] = value

        return fields

    def _brew_methods_from_tags(self, tags: list[dict]) -> list[str] | None:
        """Extract brew methods from WooCommerce API tag objects."""
        methods = []
        seen: set[str] = set()
        for tag in tags:
            tag_name = tag.get("name", "")
            if not tag_name:
                continue
            result = normalize_brew_methods(tag_name)
            if result:
                for method in result:
                    if method not in seen:
                        seen.add(method)
                        methods.append(method)
        return methods if methods else None

    # ── Playwright fallback (original approach) ───────────────────────────

    async def _scrape_via_playwright(self, browser) -> list[CoffeeData]:
        """Original Playwright-based scraping — kept as fallback."""
        coffees: list[CoffeeData] = []

        product_urls = await self._collect_links(browser)
        logger.debug(f"[flat-and-white] Found {len(product_urls)} product links")

        for url in product_urls:
            try:
                coffee = await self._scrape_product(browser, url)
                if coffee:
                    coffees.append(coffee)
            except Exception as e:
                logger.warning(f"[flat-and-white] Failed to scrape {url}: {e}")

        logger.info(f"[flat-and-white] Total coffees found (Playwright): {len(coffees)}")
        return coffees

    async def _collect_links(self, browser) -> list[str]:
        """Load the listing page and return all unique product page URLs."""
        page = await browser.new_page()
        try:
            await page.goto(LISTING_URL, wait_until="networkidle", timeout=60000)
            await page.wait_for_selector(".woocommerce-LoopProduct-link", timeout=15000)
            html = await page.content()
        except Exception as e:
            logger.warning(f"[flat-and-white] Could not fetch listing page: {e}")
            return []
        finally:
            await page.close()

        tree = HTMLParser(html)
        seen: set[str] = set()
        urls: list[str] = []

        for link in tree.css("a.woocommerce-LoopProduct-link"):
            href = link.attributes.get("href", "")
            if href and href not in seen:
                seen.add(href)
                urls.append(href)

        return urls

    async def _scrape_product(self, browser, url: str) -> CoffeeData | None:
        """Scrape a single product page and return a CoffeeData object."""
        external_id = url.rstrip("/").split("/")[-1]

        page = await browser.new_page()
        try:
            await page.goto(url, wait_until="networkidle", timeout=60000)
            await page.wait_for_selector("h1.product_title", timeout=15000)

            image_url = await page.evaluate("""
                () => {
                    const img = document.querySelector(
                        '.woocommerce-product-gallery__image img'
                    );
                    if (img) {
                        const candidates = [
                            img.dataset.u,
                            img.dataset.src,
                            img.dataset.lazySrc,
                            img.getAttribute('data-large_image'),
                        ].filter(u => u && !u.startsWith('data:'));
                        if (candidates.length) return candidates[0];
                        if (img.currentSrc && !img.currentSrc.startsWith('data:')) return img.currentSrc;
                        if (img.src && !img.src.startsWith('data:')) return img.src;
                    }
                    const og = document.querySelector('meta[property="og:image"]');
                    return og ? og.getAttribute('content') : null;
                }
            """)

            html = await page.content()
        finally:
            await page.close()

        tree = HTMLParser(html)

        if not image_url:
            og_node = tree.css_first('meta[property="og:image"]')
            if og_node:
                image_url = og_node.attributes.get("content")

        # --- Name ---
        name_node = tree.css_first("h1.product_title")
        if not name_node:
            logger.warning(f"[flat-and-white] No product title found at {url}")
            return None
        name = clean_text(name_node.text())
        if not name:
            return None

        # --- Price (from JSON-LD) ---
        price_cents = None
        for script_node in tree.css('script[type="application/ld+json"]'):
            try:
                ld = json.loads(script_node.text())
                candidates = [ld]
                if "@graph" in ld:
                    candidates = ld["@graph"]
                for item in candidates:
                    offers = item.get("offers") if isinstance(item, dict) else None
                    if not offers:
                        continue
                    if isinstance(offers, list):
                        raw_price = offers[0].get("price")
                    else:
                        raw_price = offers.get("price") or offers.get("lowPrice")
                    if raw_price:
                        price_cents = parse_price_cents(str(raw_price))
                        break
                if price_cents is not None:
                    break
            except (json.JSONDecodeError, KeyError, IndexError, TypeError):
                pass

        if price_cents is None:
            price_node = (
                tree.css_first(".price ins .amount") or
                tree.css_first(".price .amount")
            )
            price_cents = parse_price_cents(price_node.text() if price_node else None)

        # --- Ficha Técnica ---
        page_text = tree.body.text() if tree.body else ""
        ficha = self._extract_ficha_tecnica(page_text)

        origin_raw = ficha.get("origen", "")
        origin_country = normalize_origin(None, origin_raw) if origin_raw else None
        if not origin_country:
            origin_country = normalize_origin(name, None)

        variety = clean_text(ficha.get("varietales")) or clean_text(ficha.get("varietal"))

        process = normalize_process(ficha.get("beneficio") or ficha.get("proceso"))
        if not process:
            process = normalize_process(name)

        altitude_masl = self._parse_altitude(ficha.get("altura"))

        tasting_notes_raw = (
            ficha.get("notas")
            or ficha.get("notas de cata")
            or ficha.get("perfil")
            or ficha.get("perfil sensorial")
            or ficha.get("perfil de sabor")
        )
        tasting_notes = None
        if tasting_notes_raw:
            parts = re.split(r",|\by\b", tasting_notes_raw, flags=re.IGNORECASE)
            parts = [p.strip() for p in parts if p.strip()]
            tasting_notes = normalize_tasting_notes(parts)

        brew_methods = self._extract_brew_methods(tree)

        attributes = {}
        if tasting_notes:
            attributes["tasting_notes"] = tasting_notes

        logger.debug(
            f"[flat-and-white] {name} | {price_cents} ARS-cents | "
            f"origin={origin_country} | process={process}"
        )

        return CoffeeData(
            external_id=external_id,
            name=name,
            url=url,
            price_cents=price_cents,
            currency="ARS",
            weight_grams=250,
            is_available=True,
            image_url=image_url,
            origin_country=origin_country,
            variety=variety,
            process=process,
            altitude_masl=altitude_masl,
            acidity=None,
            sweetness=None,
            body=None,
            brew_methods=brew_methods,
            attributes=attributes,
        )

    # ── Shared helpers ────────────────────────────────────────────────────

    def _extract_ficha_tecnica(self, page_text: str) -> dict[str, str]:
        """
        Find the "Ficha Técnica" or "Trazabilidad" heading in the page text
        and parse the key-value pairs that follow it.
        """
        match = re.search(r"ficha\s+t[eé]cnica|trazabilidad", page_text, re.IGNORECASE)
        if not match:
            # No heading found — try to find key:value pairs anywhere
            # (some products like Ethiopia have them without a heading)
            fields: dict[str, str] = {}
            for line_match in re.finditer(
                r"^([A-Za-záéíóúñÁÉÍÓÚÑ\s]+):\s*(.+)$",
                page_text,
                re.MULTILINE,
            ):
                key = line_match.group(1).strip().lower()
                value = line_match.group(2).strip()
                if key and value and len(key) < 30:
                    fields[key] = value
            return fields

        after = page_text[match.end():]

        fields = {}
        for line_match in re.finditer(r"^([A-Za-záéíóúñÁÉÍÓÚÑ\s]+):\s*(.+)$", after, re.MULTILINE):
            key = line_match.group(1).strip().lower()
            value = line_match.group(2).strip()
            if key and value:
                fields[key] = value

        return fields

    def _extract_brew_methods(self, tree) -> list[str] | None:
        """Extract brew methods from WooCommerce product tag HTML elements."""
        tags = []
        tagged_node = tree.css_first(".tagged_as")
        if tagged_node:
            tags = [a.text().strip() for a in tagged_node.css("a") if a.text().strip()]
        if not tags:
            page_text = tree.body.text() if tree.body else ""
            m = re.search(r"etiquetas?\s*:\s*(.+?)(?:\n|$)", page_text, re.IGNORECASE)
            if m:
                tags = [t.strip() for t in m.group(1).split(",") if t.strip()]

        methods = []
        seen: set[str] = set()
        for tag in tags:
            result = normalize_brew_methods(tag)
            if result:
                for method in result:
                    if method not in seen:
                        seen.add(method)
                        methods.append(method)
        return methods if methods else None

    def _parse_altitude(self, raw: str | None) -> int | None:
        """Extract altitude in meters from strings like '1600msnm' or '1600 m'."""
        if not raw:
            return None
        range_match = re.search(r"(\d{3,4})\s*[-–]\s*(\d{3,4})", raw)
        if range_match:
            lo, hi = int(range_match.group(1)), int(range_match.group(2))
            return (lo + hi) // 2
        single_match = re.search(r"(\d{3,4})", raw)
        if single_match:
            return int(single_match.group(1))
        return None
