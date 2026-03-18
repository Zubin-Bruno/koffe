"""
Scraper for Puerto Blest Tostadores — https://www.cafepuertoblest.com
Argentine specialty roaster. Tiendanube platform, Playwright + selectolax.
"""

import re

from loguru import logger
from selectolax.parser import HTMLParser

from koffe.scrapers.base import BaseScraper, CoffeeData
from koffe.scrapers.utils import (
    clean_text,
    normalize_name,
    normalize_process,
    parse_price_cents,
    parse_weight_grams,
)

BASE_URL = "https://www.cafepuertoblest.com"
LISTING_URL = f"{BASE_URL}/cafe-especial/"
FILTER_URL = f"{BASE_URL}/filtrados/"


class PuertoBlestScraper(BaseScraper):
    roaster_slug = "puerto-blest"

    async def scrape(self, browser) -> list[CoffeeData]:
        coffees: list[CoffeeData] = []

        # Fetch the filter-only listing page to know which slugs are filter coffees
        filter_slugs = await self._get_slugs_from_listing(browser, FILTER_URL)
        logger.debug(f"[puerto-blest] Filter slugs: {filter_slugs}")

        page = await browser.new_page()
        try:
            await page.goto(LISTING_URL, wait_until="networkidle", timeout=60000)

            # Click "Mostrar más productos" until all products are loaded
            for _ in range(20):  # safety cap
                btn = page.locator("a.js-load-more-btn")
                if await btn.count() == 0 or not await btn.is_visible():
                    break
                await btn.click()
                await page.wait_for_load_state("networkidle")

            html = await page.content()
        finally:
            await page.close()

        tree = HTMLParser(html)

        product_links: list[dict] = []
        seen: set[str] = set()

        for card in tree.css(".js-item-product"):
            link = card.css_first("a[href*='/productos/']")
            if not link:
                continue
            href = link.attributes.get("href", "")
            if not href or href in seen:
                continue
            seen.add(href)

            # Ensure absolute URL
            if href.startswith("/"):
                href = BASE_URL + href

            price_node = card.css_first(".js-price-display")
            price_text = price_node.text() if price_node else None

            product_links.append({"url": href, "price_text": price_text})

        logger.debug(f"[puerto-blest] Found {len(product_links)} product links")

        for entry in product_links:
            url = entry["url"]
            slug = url.rstrip("/").split("/")[-1]
            brew_methods = ["Filtro"] if slug in filter_slugs else ["Espresso"]
            try:
                coffee = await self._scrape_product(browser, url, slug, entry["price_text"], brew_methods)
                if coffee:
                    coffees.append(coffee)
            except Exception as e:
                logger.warning(f"[puerto-blest] Failed to scrape {url}: {e}")

        logger.info(f"[puerto-blest] Total coffees found: {len(coffees)}")
        return coffees

    async def _get_slugs_from_listing(self, browser, url: str) -> set[str]:
        """Fetch a listing page and return the set of product slugs found on it."""
        page = await browser.new_page()
        try:
            await page.goto(url, wait_until="networkidle", timeout=60000)
            html = await page.content()
        except Exception as e:
            logger.warning(f"[puerto-blest] Could not fetch {url}: {e}")
            return set()
        finally:
            await page.close()

        tree = HTMLParser(html)
        slugs: set[str] = set()
        for card in tree.css(".js-item-product"):
            link = card.css_first("a[href*='/productos/']")
            if not link:
                continue
            href = link.attributes.get("href", "")
            if href:
                slug = href.rstrip("/").split("/")[-1]
                slugs.add(slug)
        return slugs

    async def _scrape_product(
        self, browser, url: str, slug: str, listing_price_text: str | None = None,
        brew_methods: list[str] | None = None,
    ) -> CoffeeData | None:
        page = await browser.new_page()
        try:
            await page.goto(url, wait_until="networkidle", timeout=60000)

            # Check availability via the add-to-cart button (Playwright, before closing)
            # Tiendanube hides a "Sin stock" label in the template even for available products,
            # so we check whether the cart button is present and not disabled.
            add_btn = page.locator("input.js-addtocart, button.js-addtocart")
            is_available = (
                await add_btn.count() > 0
                and await add_btn.first.is_enabled()
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

        # Weight — look in variant names or description
        weight_grams = None
        for node in tree.css(".js-variation-option, .variant-label"):
            weight_grams = parse_weight_grams(node.text())
            if weight_grams:
                break
        if not weight_grams:
            weight_grams = 250  # all products default to 250g

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
        # Prefer process from name (most reliable for this site)
        process = normalize_process(self._extract_process_from_name(name))
        if not process:
            process = normalize_process(self._extract_field(page_text, ["beneficio", "proceso"]))
        # Prefer variety from name, fall back to page metadata
        variety = self._extract_variety_from_name(name)
        if not variety:
            raw_variety = self._extract_field(page_text, ["varietal", "variedad", "variety"])
            variety = clean_text(raw_variety) if raw_variety else None
        altitude_masl = self._extract_altitude(page_text)

        # Tasting notes from description
        tasting_notes = self._extract_tasting_notes(page_text)
        attributes = {}
        if tasting_notes:
            attributes["tasting_notes"] = tasting_notes

        logger.debug(f"[puerto-blest] Scraped: {name} | {price_cents} ARS-cents | {origin_country}")

        return CoffeeData(
            external_id=slug,
            name=normalize_name(name),
            url=url,
            price_cents=price_cents,
            currency="ARS",
            weight_grams=weight_grams,
            is_available=is_available,
            image_url=image_url,
            description=description,
            origin_country=origin_country,
            process=process,
            variety=variety,
            altitude_masl=altitude_masl,
            brew_methods=brew_methods,
            attributes=attributes,
        )

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
            ("bolivia", "Bolivia"),
        ]
        # Check name first (most reliable)
        lower_name = name.lower()
        for keyword, canonical in countries:
            if keyword in lower_name:
                return canonical
        # Fall back to full page text
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
        """Try to extract variety from product name like 'Guatemala - Bourbon / Lavado'."""
        # Pattern: "Origin - Variety / Process" or "Origin - Variety Rojo / Process"
        match = re.search(r"-\s+([A-Za-záéíóúÁÉÍÓÚ\s]+?)(?:\s*/\s*|\s*$)", name)
        if match:
            candidate = match.group(1).strip()
            # Filter out process words
            processes = {"lavado", "natural", "honey", "washed", "anaerobico", "anaeróbico"}
            if candidate.lower() not in processes and len(candidate) > 2:
                return candidate
        return None

    def _extract_field(self, text: str, labels: list[str]) -> str | None:
        # Stop capture at the next label-like word (e.g. "Beneficio:", "Altura:", "Finca:")
        for label in labels:
            match = re.search(
                rf"{label}[:\s]+(.+?)(?:\s+(?:beneficio|proceso|varietal|variedad|altura|finca|origen|region|notas?)[:\s]|\n|\r|$)",
                text,
                re.IGNORECASE,
            )
            if match:
                value = match.group(1).strip().rstrip(".,;")
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
            r"(?:notas?|notes?|perfil)[:\s]+(.+?)(?:tostado|cosecha|recolecci[oó]n|secado|presentaci[oó]n|beneficio|proceso|varietal|variedad|altura|finca|origen|regi[oó]n|tueste|acidez|dulzura|cuerpo|\n|\r|$)",
            text,
            re.IGNORECASE,
        )
        if match:
            raw = match.group(1).strip()
            notes = [n.strip() for n in re.split(r"[,/y&+]", raw) if n.strip()]
            return notes[:6] if notes else None
        return None
