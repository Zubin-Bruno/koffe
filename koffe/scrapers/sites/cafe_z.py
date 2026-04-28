"""
Scraper for Café Z — https://cafez.empretienda.com.ar
Argentine specialty roaster on the Empretienda platform.
Uses Playwright to render the listing page, then parses each product detail
page with selectolax.

Field map (from the per-product description block):
    Origen   → attributes["farm"]   (e.g. "Cooperativa Capucas")
    Región   → attributes["region"] (e.g. "Copan")
    Proceso  → process              (normalized, e.g. "Washed")
    Altura   → altitude_masl        (range "1400 a 1650 msnm" → avg int 1525)
    Varietal → variety              (e.g. "Parainema, Obata")
    Notas    → attributes["tasting_notes"]
    <number> → attributes["cupping_score"] (e.g. "83.5")

Acidity / sweetness / body are NOT published numerically by Café Z, so they are
left NULL.  Country comes from the title's first word (e.g. "Honduras Lavado 250g"
→ Honduras), via normalize_origin.
"""

import re

from loguru import logger
from selectolax.parser import HTMLParser

from koffe.scrapers.base import BaseScraper, CoffeeData
from koffe.scrapers.utils import (
    clean_text,
    normalize_name,
    normalize_origin,
    normalize_process,
    normalize_tasting_notes,
    parse_price_cents,
    parse_weight_grams,
)

BASE_URL = "https://cafez.empretienda.com.ar"
LISTING_URL = f"{BASE_URL}/250-g"


class CafeZScraper(BaseScraper):
    roaster_slug = "cafe-z"
    start_url = BASE_URL

    async def scrape(self, browser) -> list[CoffeeData]:
        product_urls = await self._collect_listing(browser)
        logger.info(f"[cafe-z] Found {len(product_urls)} product link(s)")

        coffees: list[CoffeeData] = []
        for url in product_urls:
            try:
                coffee = await self._scrape_product(browser, url)
                if coffee:
                    coffees.append(coffee)
            except Exception as e:
                logger.warning(f"[cafe-z] Failed to scrape {url}: {e}")

        logger.info(f"[cafe-z] Total coffees scraped: {len(coffees)}")
        return coffees

    async def _collect_listing(self, browser) -> list[str]:
        """Open the /250-g listing and return absolute URLs of every product."""
        page = await browser.new_page()
        try:
            await page.goto(LISTING_URL, wait_until="networkidle", timeout=60000)
            html = await page.content()
        finally:
            await page.close()

        tree = HTMLParser(html)
        urls: list[str] = []
        seen: set[str] = set()
        for link in tree.css("a.products-feed__product-link"):
            href = link.attributes.get("href")
            if not href:
                continue
            if href.startswith("/"):
                href = BASE_URL + href
            if href in seen:
                continue
            seen.add(href)
            urls.append(href)
        return urls

    async def _scrape_product(self, browser, url: str) -> CoffeeData | None:
        page = await browser.new_page()
        try:
            await page.goto(url, wait_until="networkidle", timeout=60000)
            html = await page.content()
        finally:
            await page.close()

        tree = HTMLParser(html)

        # Title
        title_node = tree.css_first(".product-vip__title")
        if not title_node:
            logger.warning(f"[cafe-z] No title found at {url}")
            return None
        raw_title = clean_text(title_node.text()) or ""
        if not raw_title:
            return None

        # Price
        price_node = tree.css_first(".product-vip__price-value")
        price_cents = parse_price_cents(price_node.text() if price_node else None)

        # Weight (from title; fallback to 250 since the catalog is /250-g)
        weight_grams = parse_weight_grams(raw_title) or 250

        # Description block — multiple <p> tags joined with newlines.
        desc_text = self._extract_description_text(tree)

        # Structured fields from description
        farm = self._field(desc_text, "Origen")
        region = self._field(desc_text, "Región|Region")
        proceso_raw = self._field(desc_text, "Proceso")
        altura_raw = self._field(desc_text, "Altura")
        varietal_raw = self._field(desc_text, "Varietal|Variedad")
        notas_raw = self._field(desc_text, "Notas")
        cupping_score = self._extract_cupping_score(desc_text)

        # Strip parenthetical clarifications first — Café Z writes things like
        # "Honey (semilavado)", and the shared normalizer would otherwise match
        # "lavado" before "honey" and mislabel it as Washed.
        proceso_clean = re.sub(r"\(.*?\)", "", proceso_raw).strip() if proceso_raw else None
        process = normalize_process(proceso_clean)
        variety = clean_text(varietal_raw)
        altitude_masl = self._extract_altitude(altura_raw) if altura_raw else None
        origin_country = normalize_origin(raw_title, desc_text)

        # Tasting notes — "Acidez suave y jugosa, dulce, cuerpo redondo"
        # Split on commas, "y", and "/"; normalize_tasting_notes does the rest.
        tasting_notes = None
        if notas_raw:
            raw_list = [n.strip() for n in re.split(r"[,/&+]|\s+y\s+", notas_raw) if n.strip()]
            tasting_notes = normalize_tasting_notes(raw_list)

        # Image — first product carousel image
        img_node = tree.css_first(".product-vip__carrousel-image")
        image_url = img_node.attributes.get("src") if img_node else None
        if image_url and image_url.startswith("//"):
            image_url = "https:" + image_url

        # Availability — Empretienda renders #add_to_cart-btn for in-stock items
        is_available = tree.css_first("#add_to_cart-btn") is not None

        # external_id — use the unique URL slug (e.g. "honduras-lavado-250g")
        external_id = url.rstrip("/").split("/")[-1]

        attributes: dict = {}
        if tasting_notes:
            attributes["tasting_notes"] = tasting_notes
        if farm:
            attributes["farm"] = clean_text(farm)
        if region:
            attributes["region"] = clean_text(region)
        if cupping_score is not None:
            attributes["cupping_score"] = cupping_score

        logger.debug(
            f"[cafe-z] {raw_title} | {price_cents} | {origin_country} | "
            f"{process} | alt={altitude_masl} | notes={tasting_notes}"
        )

        return CoffeeData(
            external_id=external_id,
            name=normalize_name(raw_title) or raw_title,
            url=url,
            price_cents=price_cents,
            currency="ARS",
            weight_grams=weight_grams,
            is_available=is_available,
            image_url=image_url,
            description=desc_text or None,
            origin_country=origin_country,
            process=process,
            variety=variety,
            altitude_masl=altitude_masl,
            attributes=attributes,
        )

    def _extract_description_text(self, tree) -> str:
        """Concatenate text from the description block(s).

        The page renders the same description twice (one mobile, one desktop)
        — both share the class. Either one works; we just take the first.
        """
        node = tree.css_first(".product-vip__description")
        if not node:
            return ""
        # Use newline as separator so each <p> becomes its own line.
        # selectolax's .text() collapses whitespace; we manually walk children.
        lines: list[str] = []
        for p in node.css("p"):
            txt = (p.text() or "").strip()
            if txt:
                lines.append(txt)
        if lines:
            return "\n".join(lines)
        return clean_text(node.text()) or ""

    def _field(self, text: str, label_pattern: str) -> str | None:
        """Pull a labeled value (e.g. 'Proceso: Lavado') from the description text."""
        if not text:
            return None
        pattern = rf"(?:{label_pattern})\s*:\s*(.+?)\s*(?:\n|$)"
        m = re.search(pattern, text, re.IGNORECASE)
        return m.group(1).strip() if m else None

    def _extract_altitude(self, raw: str) -> int | None:
        """Parse '1400 a 1650 msnm' or '1500 msnm' → int."""
        m = re.search(r"(\d{3,4})\s*(?:–|-|a|to)\s*(\d{3,4})", raw, re.IGNORECASE)
        if m:
            lo, hi = int(m.group(1)), int(m.group(2))
            return (lo + hi) // 2
        m = re.search(r"(\d{3,4})", raw)
        if m:
            return int(m.group(1))
        return None

    def _extract_cupping_score(self, text: str) -> float | None:
        """Find a standalone score line (e.g. '83.5')."""
        if not text:
            return None
        for line in text.splitlines():
            line = line.strip()
            m = re.fullmatch(r"\d{2}(?:\.\d+)?", line)
            if m:
                try:
                    val = float(line)
                    if 60 <= val <= 100:
                        return val
                except ValueError:
                    continue
        return None
