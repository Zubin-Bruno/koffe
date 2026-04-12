"""
Scraper for Flat and White — https://flatnwhite.com
Argentine specialty roaster. WooCommerce/WordPress platform, Playwright + selectolax.

Data is extracted from the "Ficha Técnica" section on each product page.
"""

import re

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


class FlatAndWhiteScraper(BaseScraper):
    roaster_slug = "flat-and-white"
    start_url = "https://flatnwhite.com"

    async def scrape(self, browser) -> list[CoffeeData]:
        coffees: list[CoffeeData] = []

        # Step 1: Collect all product links from the listing page
        product_urls = await self._collect_links(browser)
        logger.debug(f"[flat-and-white] Found {len(product_urls)} product links")

        # Step 2: Scrape each product page
        for url in product_urls:
            try:
                coffee = await self._scrape_product(browser, url)
                if coffee:
                    coffees.append(coffee)
            except Exception as e:
                logger.warning(f"[flat-and-white] Failed to scrape {url}: {e}")

        logger.info(f"[flat-and-white] Total coffees found: {len(coffees)}")
        return coffees

    async def _collect_links(self, browser) -> list[str]:
        """Load the listing page and return all unique product page URLs."""
        page = await browser.new_page()
        try:
            await page.goto(LISTING_URL, wait_until="networkidle", timeout=60000)
            # Wait for WooCommerce product links to appear
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
        # external_id is the last URL segment (slug), e.g. "cafe-papua-nueva-guinea"
        external_id = url.rstrip("/").split("/")[-1]

        page = await browser.new_page()
        try:
            await page.goto(url, wait_until="networkidle", timeout=60000)
            await page.wait_for_selector("h1.product_title", timeout=15000)

            # Extract image using JavaScript — the site uses ShortPixel lazy-loading.
            # We try several data-* attributes in order, skipping any that are base64
            # data: blobs (those are just 1-pixel placeholders, not the real image).
            # If all fail, we fall back to currentSrc/src, then to the og:image meta tag.
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

        # Selectolax OG-image fallback — if JS couldn't extract a URL, check the
        # <meta property="og:image"> tag that WordPress always injects in <head>.
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

        # --- Price ---
        # WooCommerce sale prices wrap the real amount in an <ins> tag.
        # We check that first; if absent, fall back to the regular .price .amount.
        price_node = (
            tree.css_first(".price ins .amount") or
            tree.css_first(".price .amount")
        )
        price_cents = parse_price_cents(price_node.text() if price_node else None)

        # --- Ficha Técnica ---
        # Get the full page text and find the section below "Ficha Técnica"
        page_text = tree.body.text() if tree.body else ""
        ficha = self._extract_ficha_tecnica(page_text)

        # Map each field from the Ficha Técnica to our data model.
        # If a field is missing (no Ficha Técnica on this page), fall back to
        # extracting the info from the product name itself.
        origin_raw = ficha.get("origen", "")
        origin_country = normalize_origin(None, origin_raw) if origin_raw else None
        if not origin_country:
            # Fallback: try to find the origin in the product name
            # e.g. "Café de especialidad Tanzania" → "Tanzania"
            origin_country = normalize_origin(name, None)

        variety = clean_text(ficha.get("varietales")) or clean_text(ficha.get("varietal"))

        process = normalize_process(ficha.get("beneficio") or ficha.get("proceso"))
        if not process:
            # Fallback: try to extract process from product name
            # e.g. "Café de especialidad Etiopía Lavado" → "Washed"
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
            # Split by comma and the word "y" (Spanish "and")
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
            weight_grams=250,  # Flat and White only sells 250g bags
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

    def _extract_ficha_tecnica(self, page_text: str) -> dict[str, str]:
        """
        Find the "Ficha Técnica" heading in the page text and parse the key-value
        pairs that follow it.

        Example input (after the heading):
            Origen: Blend regional de Huila y Tolima, Colombia
            Varietales: Caturra, Colombia, Typica
            Beneficio: Lavado
            Altura: 1600msnm
            Notas: Chocolate con leche, caramelo y naranja

        Returns a dict like:
            {"origen": "Blend regional de Huila...", "varietales": "Caturra, ...", ...}
        """
        # Find where "Ficha Técnica" appears (case-insensitive, with or without accent)
        match = re.search(r"ficha\s+t[eé]cnica", page_text, re.IGNORECASE)
        if not match:
            logger.debug("[flat-and-white] No 'Ficha Técnica' section found on page")
            return {}

        # Take only the text that comes AFTER the heading
        after = page_text[match.end():]

        # Each line looks like "Label: value" — parse them into a dict
        fields: dict[str, str] = {}
        # Match lines of the form "Word(s): rest of line"
        for line_match in re.finditer(r"^([A-Za-záéíóúñÁÉÍÓÚÑ\s]+):\s*(.+)$", after, re.MULTILINE):
            key = line_match.group(1).strip().lower()
            value = line_match.group(2).strip()
            if key and value:
                fields[key] = value

        return fields

    def _extract_brew_methods(self, tree) -> list[str] | None:
        """
        Find brew method tags from the WooCommerce "Etiquetas:" (product tags) element.

        WordPress/WooCommerce renders product tags like:
            <span class="tagged_as">Etiquetas: <a>espresso</a>, <a>filtro</a></span>

        Each tag text is run through normalize_brew_methods() to map it to a
        canonical method name (e.g. "espresso" → "Espresso", "v60" → "V60").
        If the CSS node isn't found, we fall back to a regex scan of the page text.
        """
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
        # Range: "1400-1800msnm" → average
        range_match = re.search(r"(\d{3,4})\s*[-–]\s*(\d{3,4})", raw)
        if range_match:
            lo, hi = int(range_match.group(1)), int(range_match.group(2))
            return (lo + hi) // 2
        # Single value: "1600msnm" or "1600 m"
        single_match = re.search(r"(\d{3,4})", raw)
        if single_match:
            return int(single_match.group(1))
        return None
