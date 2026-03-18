"""
Scraper for Grains Argentina — https://www.grains.com.ar
Argentine specialty coffee roaster. Uses Shopify.
"""

import httpx
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


class GrainsArScraper(BaseScraper):
    roaster_slug = "grains-ar"
    start_url = "https://www.grains.com.ar"

    async def scrape(self, browser) -> list[CoffeeData]:
        coffees: list[CoffeeData] = []

        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
            page_num = 1
            while True:
                resp = await client.get(
                    f"{self.start_url}/products.json",
                    params={"limit": 250, "page": page_num},
                )
                resp.raise_for_status()
                products = resp.json().get("products", [])

                if not products:
                    break

                for product in products:
                    # Only include coffee products (skip merch, equipment, etc.)
                    tags = [t.lower() for t in product.get("tags", [])]
                    product_type = product.get("product_type", "").lower()
                    if not self._is_coffee(product["title"], tags, product_type):
                        continue

                    image_url = None
                    if product.get("images"):
                        image_url = product["images"][0]["src"]

                    # Strip HTML from body
                    body_html = product.get("body_html") or ""
                    description = clean_text(HTMLParser(body_html).text()) if body_html else None

                    for variant in product["variants"]:
                        coffees.append(
                            CoffeeData(
                                external_id=str(variant["id"]),
                                name=self._build_name(product["title"], variant["title"]),
                                url=f"{self.start_url}/products/{product['handle']}",
                                price_cents=parse_price_cents(variant.get("price")),
                                currency="ARS",
                                weight_grams=parse_weight_grams(variant.get("title")),
                                is_available=variant.get("available", True),
                                image_url=image_url,
                                description=description,
                                origin_country=self._extract_origin(tags, product["title"]),
                                process=normalize_process(self._find_tag(tags, ["natural", "washed", "honey", "lavado", "seco"])),
                                roast_level=normalize_roast(self._find_tag(tags, ["light", "medium", "dark", "claro", "medio", "oscuro"])),
                                attributes={"tags": product.get("tags", [])},
                            )
                        )

                logger.debug(f"[grains-ar] Page {page_num}: {len(products)} products")
                page_num += 1

        logger.info(f"[grains-ar] Total coffees found: {len(coffees)}")
        return coffees

    def _is_coffee(self, title: str, tags: list[str], product_type: str) -> bool:
        coffee_signals = ["café", "cafe", "coffee", "espresso", "filtrado", "blend", "single origin"]
        non_coffee = ["taza", "mug", "equipo", "equipment", "merch", "ropa", "grinder", "molino"]
        title_lower = title.lower()
        if any(w in title_lower for w in non_coffee):
            return False
        return any(w in title_lower or w in tags or w in product_type for w in coffee_signals)

    def _build_name(self, product_title: str, variant_title: str) -> str:
        if variant_title and variant_title.lower() not in ("default title", "default"):
            return f"{product_title} — {variant_title}"
        return product_title

    def _find_tag(self, tags: list[str], keywords: list[str]) -> str | None:
        for tag in tags:
            if any(kw in tag for kw in keywords):
                return tag
        return None

    def _extract_origin(self, tags: list[str], title: str) -> str | None:
        countries = [
            "ethiopia", "etiopía", "etiopia",
            "colombia", "brazil", "brasil",
            "peru", "perú", "kenya", "kenia",
            "guatemala", "rwanda", "ruanda",
            "honduras", "costa rica", "panama", "panamá",
            "el salvador", "nicaragua", "burundi",
            "indonesia", "yemen", "bolivia",
        ]
        text = " ".join(tags) + " " + title.lower()
        for country in countries:
            if country in text:
                return country.title()
        return None
