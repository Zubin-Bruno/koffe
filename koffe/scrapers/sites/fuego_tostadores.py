"""
Scraper for Fuego Tostadores — https://fuegotostadores.com
Argentine specialty roaster. Tiendanube platform, Playwright + selectolax.
"""

import re

from loguru import logger
from selectolax.parser import HTMLParser

from koffe.scrapers.base import BaseScraper, CoffeeData
from koffe.scrapers.utils import (
    clean_text,
    normalize_intensity,
    normalize_process,
    normalize_roast,
    parse_price_cents,
)

BASE_URL = "https://fuegotostadores.com"

# Each category page implies a fixed weight for all products in it.
CATEGORY_PAGES = [
    {"url": f"{BASE_URL}/cafe-de-especialidad/cuartos-de-cafe/", "weight": 250},
]


class FuegoTostadoresScraper(BaseScraper):
    roaster_slug = "fuego-tostadores"

    async def scrape(self, browser) -> list[CoffeeData]:
        coffees: list[CoffeeData] = []
        # Collect product links from all category pages, dedup by URL
        product_entries: dict[str, dict] = {}  # url -> {url, price_text, weight}

        for cat in CATEGORY_PAGES:
            entries = await self._collect_listing(browser, cat["url"], cat["weight"])
            for entry in entries:
                # First-seen weight wins (no overwrite on dedup)
                if entry["url"] not in product_entries:
                    product_entries[entry["url"]] = entry

        logger.debug(f"[fuego-tostadores] Found {len(product_entries)} unique product links")

        for entry in product_entries.values():
            url = entry["url"]
            slug = url.rstrip("/").split("/")[-1]
            try:
                coffee = await self._scrape_product(
                    browser, url, slug, entry["price_text"], entry["weight"]
                )
                if coffee:
                    coffees.append(coffee)
            except Exception as e:
                logger.warning(f"[fuego-tostadores] Failed to scrape {url}: {e}")

        logger.info(f"[fuego-tostadores] Total coffees found: {len(coffees)}")
        return coffees

    async def _collect_listing(
        self, browser, listing_url: str, weight: int
    ) -> list[dict]:
        """Load a category page, handle infinite scroll, return product entries."""
        page = await browser.new_page()
        try:
            await page.goto(listing_url, wait_until="networkidle", timeout=60000)

            # Handle pagination: try "load more" button, then scroll fallback
            for _ in range(20):  # safety cap
                btn = page.locator("a.js-load-more-btn")
                if await btn.count() > 0 and await btn.is_visible():
                    await btn.click()
                    await page.wait_for_load_state("networkidle")
                else:
                    break

            html = await page.content()
        finally:
            await page.close()

        tree = HTMLParser(html)
        entries: list[dict] = []

        for card in tree.css(".js-item-product"):
            link = card.css_first("a[href*='/productos/']")
            if not link:
                continue
            href = link.attributes.get("href", "")
            if not href:
                continue
            if href.startswith("/"):
                href = BASE_URL + href

            price_node = card.css_first(".js-price-display")
            price_text = price_node.text() if price_node else None

            entries.append({"url": href, "price_text": price_text, "weight": weight})

        return entries

    async def _scrape_product(
        self,
        browser,
        url: str,
        slug: str,
        listing_price_text: str | None,
        weight: int,
    ) -> CoffeeData | None:
        page = await browser.new_page()
        try:
            await page.goto(url, wait_until="networkidle", timeout=60000)

            # Availability: check add-to-cart button (Tiendanube pattern)
            add_btn = page.locator("input.js-addtocart, button.js-addtocart")
            is_available = (
                await add_btn.count() > 0 and await add_btn.first.is_enabled()
            )

            html = await page.content()
        finally:
            await page.close()

        tree = HTMLParser(html)

        # Name
        name_node = tree.css_first("h1")
        if not name_node:
            return None
        name = clean_text(name_node.text())
        if not name:
            return None

        # Price — prefer listing page price, fall back to detail page
        price_cents = parse_price_cents(listing_price_text)
        if not price_cents:
            price_node = tree.css_first("#price_display")
            price_cents = parse_price_cents(price_node.text() if price_node else None)

        # Image
        image_node = tree.css_first(".js-product-slide-img, .product-image img")
        image_url = None
        if image_node:
            image_url = (
                image_node.attributes.get("src")
                or image_node.attributes.get("data-src")
                or image_node.attributes.get("data-lazy")
            )
            if image_url and image_url.startswith("//"):
                image_url = "https:" + image_url

        # Description
        desc_node = tree.css_first(".product-description, .js-product-description")
        description = clean_text(desc_node.text()) if desc_node else None

        # Extract structured fields from page text
        page_text = tree.body.text() if tree.body else ""

        origin_country = self._extract_origin(name, page_text)

        # Process: prefer name, fall back to page metadata
        process = normalize_process(self._extract_process_from_name(name))
        if not process:
            process = normalize_process(
                self._extract_field(page_text, ["beneficio", "proceso"])
            )

        # Variety: prefer name, fall back to page metadata
        variety = self._extract_variety_from_name(name)
        if not variety:
            raw_variety = self._extract_field(
                page_text, ["varietal", "variedad", "variety"]
            )
            variety = clean_text(raw_variety) if raw_variety else None

        altitude_masl = self._extract_altitude(page_text)

        # Roast level
        raw_roast = self._extract_field(page_text, ["tueste", "tostado", "roast"])
        roast_level = normalize_roast(raw_roast)

        # Intensity fields (1–5)
        acidity = normalize_intensity(
            self._extract_field(page_text, ["acidez", "acidity"])
        )
        sweetness = normalize_intensity(
            self._extract_field(page_text, ["dulzura", "dulzor", "sweetness"])
        )
        body = normalize_intensity(
            self._extract_field(page_text, ["cuerpo", "body"])
        )

        # Tasting notes
        tasting_notes = self._extract_tasting_notes(page_text)
        attributes = {}
        if tasting_notes:
            attributes["tasting_notes"] = tasting_notes

        logger.debug(
            f"[fuego-tostadores] Scraped: {name} | {price_cents} ARS-cents | {origin_country}"
        )

        return CoffeeData(
            external_id=slug,
            name=name,
            url=url,
            price_cents=price_cents,
            currency="ARS",
            weight_grams=weight,
            is_available=is_available,
            image_url=image_url,
            description=description,
            origin_country=origin_country,
            process=process,
            roast_level=roast_level,
            acidity=acidity,
            sweetness=sweetness,
            body=body,
            variety=variety,
            altitude_masl=altitude_masl,
            attributes=attributes,
        )

    # --- Helper methods (same as Puerto Blest, generic for Tiendanube) ---

    def _extract_origin(self, name: str, text: str) -> str | None:
        countries = [
            ("guatemala", "Guatemala"),
            ("peru", "Perú"), ("perú", "Perú"),
            ("colombia", "Colombia"),
            ("ethiopia", "Ethiopia"), ("etiopía", "Ethiopia"), ("etiopia", "Ethiopia"),
            ("nicaragua", "Nicaragua"),
            ("costa rica", "Costa Rica"),
            ("brazil", "Brazil"), ("brasil", "Brazil"),
            ("kenya", "Kenya"), ("kenia", "Kenya"),
            ("el salvador", "El Salvador"),
            ("honduras", "Honduras"),
            ("panama", "Panamá"), ("panamá", "Panamá"),
            ("rwanda", "Rwanda"),
        ]
        lower_name = name.lower()
        for keyword, canonical in countries:
            if keyword in lower_name:
                return canonical
        lower = text.lower()
        for keyword, canonical in countries:
            if keyword in lower:
                return canonical
        return None

    def _extract_process_from_name(self, name: str) -> str | None:
        lower = name.lower()
        if "lavado" in lower or "washed" in lower:
            return "lavado"
        if "natural" in lower:
            return "natural"
        if "honey" in lower or "miel" in lower:
            return "honey"
        if "anaeróbico" in lower or "anaerobico" in lower or "anaerobic" in lower:
            return "anaeróbico"
        return None

    def _extract_variety_from_name(self, name: str) -> str | None:
        match = re.search(r"-\s+([A-Za-záéíóúÁÉÍÓÚ\s]+?)(?:\s*/\s*|\s*$)", name)
        if match:
            candidate = match.group(1).strip()
            processes = {"lavado", "natural", "honey", "washed", "anaerobico", "anaeróbico"}
            if candidate.lower() not in processes and len(candidate) > 2:
                return candidate
        return None

    def _extract_field(self, text: str, labels: list[str]) -> str | None:
        for label in labels:
            match = re.search(
                rf"{label}[:\s]+(.+?)(?:\s+(?:beneficio|proceso|varietal|variedad|altura|finca|origen|region|notas?|tueste|tostado|acidez|dulzura|cuerpo)[:\s]|\n|\r|$)",
                text,
                re.IGNORECASE,
            )
            if match:
                value = match.group(1).strip().rstrip(".,;")
                # Split at any field label that bled in due to missing spaces in HTML text
                value = re.split(
                    r'(?:beneficio|proceso|varietal|variedad|altura|finca|origen|region|notas?|tueste|tostado|acidez|dulzura|cuerpo)\s*:',
                    value, flags=re.IGNORECASE, maxsplit=1
                )[0].strip().rstrip(".,;")
                if value and len(value) < 60:
                    return value
        return None

    def _extract_altitude(self, text: str) -> int | None:
        match = re.search(r"(\d{3,4})\s*(?:–|-|a|to)\s*(\d{3,4})\s*m", text, re.IGNORECASE)
        if match:
            lo, hi = int(match.group(1)), int(match.group(2))
            return (lo + hi) // 2
        match = re.search(r"(\d{3,4})\s*m(?:snm|asl|etros|\.s\.n\.m)", text, re.IGNORECASE)
        if match:
            return int(match.group(1))
        return None

    def _extract_tasting_notes(self, text: str) -> list[str] | None:
        match = re.search(
            r"(?:notas?|notes?|perfil)[:\s]+([^\n\r]{5,120})",
            text,
            re.IGNORECASE,
        )
        if match:
            raw = match.group(1).strip()
            notes = [n.strip() for n in re.split(r"[,/y&+]", raw) if n.strip()]
            return notes[:6] if notes else None
        return None
