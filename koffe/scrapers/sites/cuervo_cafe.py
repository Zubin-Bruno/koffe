"""
Scraper for Cuervo Café — https://cuervocafe.com
Argentine specialty coffee roaster. WordPress + WooCommerce site.
"""

import re

from loguru import logger
from selectolax.parser import HTMLParser

from koffe.scrapers.base import BaseScraper, CoffeeData
from koffe.scrapers.utils import (
    clean_text,
    normalize_process,
    parse_weight_grams,
)


def _parse_ars_price(raw: str | None) -> int | None:
    """
    Parse Argentine peso prices like '$ 27.000' or '$ 27.000,00'.
    Dots are thousands separators in ARS format.
    Returns integer cents (e.g. 27000 ARS → 2700000).
    """
    if not raw:
        return None
    # Strip currency symbol and whitespace
    cleaned = re.sub(r"[^\d.,]", "", raw.strip())
    if not cleaned:
        return None
    # ARS format: dot = thousands separator, comma = decimal separator
    # e.g. "27.000" or "27.000,00"
    if "," in cleaned:
        # Remove thousands dots, replace decimal comma
        cleaned = cleaned.replace(".", "").replace(",", ".")
    else:
        # Only dots present — treat as thousands separator if 3 trailing digits
        if re.match(r"^\d{1,3}(\.\d{3})+$", cleaned):
            cleaned = cleaned.replace(".", "")
    try:
        return round(float(cleaned) * 100)
    except ValueError:
        return None


class CuervoCafeScraper(BaseScraper):
    roaster_slug = "cuervo-cafe"
    start_url = "https://cuervocafe.com"

    async def scrape(self, browser) -> list[CoffeeData]:
        coffees: list[CoffeeData] = []

        page = await browser.new_page()
        try:
            await page.goto(f"{self.start_url}/tienda/", wait_until="networkidle")
            html = await page.content()
        finally:
            await page.close()

        tree = HTMLParser(html)

        # Collect product links + listing-page prices (variable products render
        # price in the card before JS kicks in, so this is more reliable).
        product_entries: list[dict] = []
        seen: set[str] = set()

        # Each product card is wrapped in an <a> pointing to /producto/<slug>/
        for a in tree.css("a[href*='/producto/']"):
            href = a.attributes.get("href", "")
            if not href or href in seen:
                continue
            seen.add(href)

            price_node = a.css_first(".woocommerce-Price-amount")
            price_text = price_node.text() if price_node else None

            product_entries.append({"url": href, "price_text": price_text})

        logger.debug(f"[cuervo-cafe] Found {len(product_entries)} product links")

        for entry in product_entries:
            url = entry["url"]
            slug = url.rstrip("/").split("/")[-1]
            try:
                coffee = await self._scrape_product(browser, url, slug, entry["price_text"])
                if coffee:
                    coffees.append(coffee)
            except Exception as e:
                logger.warning(f"[cuervo-cafe] Failed to scrape {url}: {e}")

        logger.info(f"[cuervo-cafe] Total coffees found: {len(coffees)}")
        return coffees

    async def _scrape_product(self, browser, url: str, slug: str, listing_price_text: str | None = None) -> CoffeeData | None:
        page = await browser.new_page()
        try:
            await page.goto(url, wait_until="networkidle")
            html = await page.content()
        finally:
            await page.close()

        tree = HTMLParser(html)

        # Name
        name_node = tree.css_first("h1.product_title") or tree.css_first(".product-name") or tree.css_first("h1")
        if not name_node:
            return None
        name = clean_text(name_node.text())
        if not name:
            return None

        # Price — prefer the listing-page value (variable products may not render
        # price correctly before JS on the detail page).
        price_cents = _parse_ars_price(listing_price_text)
        if not price_cents:
            price_node = tree.css_first(".woocommerce-Price-amount bdi") or tree.css_first(".woocommerce-Price-amount")
            price_cents = _parse_ars_price(price_node.text() if price_node else None)

        # Weight — look for the default/first variant option or product size text
        weight_grams = None
        for selector in [".product-size", "[data-attribute_name='attribute_pa_gramaje']", ".variations select option"]:
            node = tree.css_first(selector)
            if node:
                weight_grams = parse_weight_grams(node.text())
                if weight_grams:
                    break
        if not weight_grams:
            weight_grams = 250  # all products default to 250g

        # Image
        image_node = (
            tree.css_first(".woocommerce-product-gallery__image img")
            or tree.css_first(".wp-post-image")
        )
        image_url = None
        if image_node:
            image_url = image_node.attributes.get("src") or image_node.attributes.get("data-src")

        # Description — short description block
        desc_node = tree.css_first(".woocommerce-product-details__short-description") or tree.css_first(".product-description")
        description = clean_text(desc_node.text()) if desc_node else None

        # Extract structured fields from the full page text (meta tables, custom fields)
        page_text = tree.body.text() if tree.body else ""

        origin_country = self._extract_origin(page_text)
        process = normalize_process(self._extract_field(page_text, ["proceso", "process", "beneficio"]))
        variety = clean_text(self._extract_field(page_text, ["variedad", "variety", "variedades"]))
        altitude_masl = self._extract_altitude(page_text)

        # Acidity / sweetness / body — Elementor rating widgets with numeric content attr.
        # Each rating block is preceded by an h2 like "Acidez:", "Cuerpo:", "Dulzor:".
        acidity, sweetness, body = self._extract_ratings(tree)

        # Availability — WooCommerce marks out-of-stock
        stock_node = tree.css_first(".out-of-stock") or tree.css_first(".stock.out-of-stock")
        is_available = stock_node is None

        # Category (section on the shop page: año-redondo, microlote, edición especial)
        category_node = tree.css_first(".posted_in a") or tree.css_first(".product_cat a")
        category = clean_text(category_node.text()) if category_node else None

        logger.debug(f"[cuervo-cafe] Scraped: {name} | {price_cents} ARS-cents | {origin_country}")

        return CoffeeData(
            external_id=slug,
            name=name,
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
            acidity=acidity,
            sweetness=sweetness,
            body=body,
            attributes={"category": category},
        )

    def _extract_ratings(self, tree: HTMLParser) -> tuple[int | None, int | None, int | None]:
        """
        Parse Elementor rating widgets. Each is preceded by an h2 heading like
        'Acidez:', 'Cuerpo:', 'Dulzor:'. The rating value is the `content` attr
        on the `div.e-rating-wrapper[itemprop='ratingValue']` element.
        """
        acidity = sweetness = body = None

        # Walk headings and look for the next rating widget sibling in the DOM text
        full_html = tree.html or ""
        # Use regex to find heading + rating pairs
        pattern = re.compile(
            r'<h[2-4][^>]*>([^<]*(?:acidez|dulzor|cuerpo|body|sweetness|acidity)[^<]*)</h[2-4]>'
            r'.*?itemprop="ratingValue"\s+content="(\d)',
            re.IGNORECASE | re.DOTALL,
        )
        for match in pattern.finditer(full_html):
            label = match.group(1).lower()
            value = int(match.group(2))
            if "acidez" in label or "acidity" in label:
                acidity = value if 1 <= value <= 5 else None
            elif "dulzor" in label or "sweetness" in label or "dulzura" in label:
                sweetness = value if 1 <= value <= 5 else None
            elif "cuerpo" in label or "body" in label:
                body = value if 1 <= value <= 5 else None

        return acidity, sweetness, body

    def _extract_field(self, text: str, labels: list[str]) -> str | None:
        """Find a labeled field in page text like 'Proceso: Lavado'."""
        for label in labels:
            match = re.search(
                rf"{label}[:\s]+([^\n\r|/]{{2,60}})",
                text,
                re.IGNORECASE,
            )
            if match:
                value = match.group(1).strip().rstrip(".,;")
                if value:
                    return value
        return None

    def _extract_origin(self, text: str) -> str | None:
        countries = [
            ("colombia", "Colombia"),
            ("ethiopia", "Ethiopia"), ("etiopía", "Ethiopia"), ("etiopia", "Ethiopia"),
            ("kenya", "Kenya"), ("kenia", "Kenya"),
            ("guatemala", "Guatemala"),
            ("peru", "Perú"), ("perú", "Perú"),
            ("brazil", "Brazil"), ("brasil", "Brazil"),
            ("costa rica", "Costa Rica"),
            ("panama", "Panamá"), ("panamá", "Panamá"),
            ("el salvador", "El Salvador"),
            ("honduras", "Honduras"),
            ("nicaragua", "Nicaragua"),
            ("rwanda", "Rwanda"),
        ]
        lower = text.lower()
        for keyword, canonical in countries:
            if keyword in lower:
                return canonical
        return None

    def _extract_altitude(self, text: str) -> int | None:
        match = re.search(r"(\d{3,4})\s*(?:–|-|a|to)\s*(\d{3,4})\s*m", text, re.IGNORECASE)
        if match:
            # Return the midpoint
            lo, hi = int(match.group(1)), int(match.group(2))
            return (lo + hi) // 2
        match = re.search(r"(\d{3,4})\s*m(?:snm|asl|\.s\.n\.m)?", text, re.IGNORECASE)
        if match:
            return int(match.group(1))
        return None
