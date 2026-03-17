"""
Scraper for Flat N' White — https://flatnwhite.com
Argentine specialty coffee roaster. WooCommerce + XStore theme.
"""

import json
import re

from loguru import logger
from selectolax.parser import HTMLParser

from koffe.scrapers.base import BaseScraper, CoffeeData
from koffe.scrapers.utils import (
    clean_text,
    normalize_process,
    normalize_roast,
    parse_price_cents,
    parse_weight_grams,
)

BASE_URL = "https://flatnwhite.com"
LISTING_URL = f"{BASE_URL}/cafe-de-especialidad-flatwhite-argentina/"


class FlatNWhiteScraper(BaseScraper):
    roaster_slug = "flat-n-white"
    start_url = BASE_URL

    async def scrape(self, browser) -> list[CoffeeData]:
        coffees: list[CoffeeData] = []

        product_urls = await self._get_product_links(browser)
        logger.debug(f"[flat-n-white] Found {len(product_urls)} product links")

        for url in product_urls:
            try:
                products = await self._scrape_product(browser, url)
                coffees.extend(products)
            except Exception as e:
                logger.warning(f"[flat-n-white] Failed to scrape {url}: {e}")

        logger.info(f"[flat-n-white] Total coffees found: {len(coffees)}")
        return coffees

    async def _get_product_links(self, browser) -> list[str]:
        page = await browser.new_page()
        try:
            await page.goto(LISTING_URL, wait_until="networkidle", timeout=60000)
            html = await page.content()
        finally:
            await page.close()

        tree = HTMLParser(html)
        urls = []
        seen = set()

        # WooCommerce standard: product links in listing use woocommerce-LoopProduct-link
        # XStore theme may use different classes; try several selectors
        selectors = [
            "a.woocommerce-LoopProduct-link",
            "li.product a[rel='bookmark']",
            ".products .product a",
        ]
        for sel in selectors:
            for link in tree.css(sel):
                href = link.attributes.get("href", "")
                if href and href not in seen and href.startswith(BASE_URL):
                    seen.add(href)
                    urls.append(href)
            if urls:
                break

        return urls

    async def _scrape_product(self, browser, url: str) -> list[CoffeeData]:
        page = await browser.new_page()
        try:
            await page.goto(url, wait_until="networkidle", timeout=60000)
            html = await page.content()
        finally:
            await page.close()

        tree = HTMLParser(html)

        # Name
        name_node = tree.css_first("h1.product_title") or tree.css_first("h1")
        if not name_node:
            return []
        name = clean_text(name_node.text())
        if not name:
            return []

        # Image
        image_url = None
        img_node = tree.css_first(
            ".woocommerce-product-gallery__image img, .wp-post-image"
        )
        if img_node:
            image_url = (
                img_node.attributes.get("src")
                or img_node.attributes.get("data-src")
                or img_node.attributes.get("data-lazy-src")
            )

        # Short description
        desc_node = tree.css_first(
            ".woocommerce-product-details__short-description, .short-description"
        )
        description = clean_text(desc_node.text()) if desc_node else None

        # Availability fallback from JSON-LD
        is_available_default = self._parse_availability(tree)

        # Metadata from page body text
        page_text = tree.body.text() if tree.body else ""
        origin_country = self._extract_origin(name, page_text)
        process = normalize_process(
            self._extract_field(page_text, ["beneficio", "proceso", "process"])
        )
        variety = self._extract_field(page_text, ["varietal", "varietales", "variedad"])
        roast_level = normalize_roast(
            self._extract_field(page_text, ["tueste", "tostado", "roast"])
        )
        altitude_masl = self._extract_altitude(page_text)
        tasting_notes = self._extract_tasting_notes(page_text)
        attributes: dict = {}
        if tasting_notes:
            attributes["tasting_notes"] = tasting_notes

        # WooCommerce variations
        variations = self._parse_variations(tree)

        coffees = []
        if variations:
            for var in variations:
                var_name = name
                if var.get("label"):
                    var_name = f"{name} — {var['label']}"
                coffees.append(
                    CoffeeData(
                        external_id=str(var["id"]),
                        name=var_name,
                        url=url,
                        price_cents=var.get("price_cents"),
                        currency="ARS",
                        weight_grams=var.get("weight_grams"),
                        is_available=var.get("is_available", is_available_default),
                        image_url=image_url,
                        description=description,
                        origin_country=origin_country,
                        process=process,
                        roast_level=roast_level,
                        variety=variety,
                        altitude_masl=altitude_masl,
                        attributes=attributes,
                    )
                )
        else:
            # No variations found — create single entry
            slug = url.rstrip("/").split("/")[-1]
            price_node = tree.css_first(".woocommerce-Price-amount")
            price_cents = parse_price_cents(price_node.text() if price_node else None)
            coffees.append(
                CoffeeData(
                    external_id=slug,
                    name=name,
                    url=url,
                    price_cents=price_cents,
                    currency="ARS",
                    is_available=is_available_default,
                    image_url=image_url,
                    description=description,
                    origin_country=origin_country,
                    process=process,
                    roast_level=roast_level,
                    variety=variety,
                    altitude_masl=altitude_masl,
                    attributes=attributes,
                )
            )

        return coffees

    def _parse_availability(self, tree: HTMLParser) -> bool:
        """Check JSON-LD for InStock status; default True."""
        for script in tree.css('script[type="application/ld+json"]'):
            try:
                data = json.loads(script.text())
                items = data.get("@graph", [data]) if isinstance(data, dict) else data
                for item in (items if isinstance(items, list) else [items]):
                    if not isinstance(item, dict):
                        continue
                    if "Product" in str(item.get("@type", "")):
                        offers = item.get("offers", {})
                        if isinstance(offers, list):
                            offers = offers[0] if offers else {}
                        availability = offers.get("availability", "")
                        return "InStock" in availability
            except (json.JSONDecodeError, Exception):
                continue
        return True

    def _parse_variations(self, tree: HTMLParser) -> list[dict]:
        """Parse WooCommerce variation data from the form's data-product_variations."""
        form = tree.css_first(".variations_form")
        if not form:
            return []
        data_str = form.attributes.get("data-product_variations", "")
        if not data_str:
            return []
        try:
            var_data = json.loads(data_str)
        except json.JSONDecodeError:
            return []

        variations = []
        for var in var_data:
            var_id = var.get("variation_id")
            if not var_id:
                continue

            display_price = var.get("display_price")
            price_cents = (
                parse_price_cents(str(display_price)) if display_price else None
            )

            is_available = var.get("is_in_stock", True) and var.get(
                "is_purchasable", True
            )

            attrs = var.get("attributes", {})
            label_parts = []
            weight_grams = None
            for attr_val in attrs.values():
                if attr_val:
                    label_parts.append(attr_val)
                    w = parse_weight_grams(attr_val)
                    if w:
                        weight_grams = w

            # Try weight from WooCommerce variation weight field (in kg)
            if not weight_grams:
                raw_weight = var.get("weight", "")
                if raw_weight:
                    try:
                        weight_grams = int(float(raw_weight) * 1000)
                    except (ValueError, TypeError):
                        pass

            # Filter out grind-type-only labels so name stays clean
            size_parts = [p for p in label_parts if parse_weight_grams(p)]
            label = " / ".join(size_parts) if size_parts else " / ".join(label_parts) if label_parts else None

            variations.append(
                {
                    "id": var_id,
                    "label": label,
                    "price_cents": price_cents,
                    "weight_grams": weight_grams,
                    "is_available": is_available,
                }
            )

        return variations

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
            ("tanzania", "Tanzania"),
            ("burundi", "Burundi"),
            ("yemen", "Yemen"),
        ]
        for src in (name.lower(), text.lower()):
            for keyword, canonical in countries:
                if keyword in src:
                    return canonical
        return None

    def _extract_field(self, text: str, labels: list[str]) -> str | None:
        for label in labels:
            match = re.search(
                rf"{label}[:\s]+(.+?)(?:\s+(?:beneficio|proceso|varietal|varietales|variedad|altura|finca|origen|region|notas?|tueste|tostado|acidez|dulzura|cuerpo|puntaje)[:\s]|\n|\r|$)",
                text,
                re.IGNORECASE,
            )
            if match:
                value = match.group(1).strip().rstrip(".,;")
                if value and len(value) < 60:
                    return value
        return None

    def _extract_altitude(self, text: str) -> int | None:
        match = re.search(
            r"(\d{3,4})\s*(?:–|-|a|to)\s*(\d{3,4})\s*m", text, re.IGNORECASE
        )
        if match:
            return (int(match.group(1)) + int(match.group(2))) // 2
        match = re.search(
            r"(\d{3,4})\s*m(?:snm|asl|etros|\.s\.n\.m)", text, re.IGNORECASE
        )
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
