"""
TEMPLATE — Copy this file to create a new roaster scraper.

Steps:
1. Copy this file to scrapers/sites/<roaster_slug>.py
2. Set `roaster_slug` to match the slug in the `roasters` DB table
3. Set `start_url` to the roaster's shop/products page
4. Implement `scrape()` to return a list of CoffeeData

Shopify stores: use the /products.json approach (see the httpx example below).
HTML stores: use Playwright + selectolax (see the HTML example below).

Remember: `external_id` must be stable across runs (use the product ID from the site,
not the product position or name).
"""

import httpx

from koffe.scrapers.base import BaseScraper, CoffeeData
from koffe.scrapers.utils import clean_text, normalize_process, normalize_roast, parse_price_cents, parse_weight_grams


class SampleRoasterScraper(BaseScraper):
    roaster_slug = "sample-roaster"
    start_url = "https://sample-roaster.com"

    async def scrape(self, browser) -> list[CoffeeData]:
        coffees = []

        # ── Option A: Shopify /products.json ─────────────────────────────────
        # Shopify stores expose a clean JSON API — no HTML parsing needed.
        #
        # async with httpx.AsyncClient(follow_redirects=True) as client:
        #     page = 1
        #     while True:
        #         resp = await client.get(
        #             f"{self.start_url}/products.json",
        #             params={"limit": 250, "page": page},
        #         )
        #         products = resp.json().get("products", [])
        #         if not products:
        #             break
        #         for product in products:
        #             for variant in product["variants"]:
        #                 coffees.append(CoffeeData(
        #                     external_id=str(variant["id"]),
        #                     name=f"{product['title']} — {variant['title']}",
        #                     url=f"{self.start_url}/products/{product['handle']}",
        #                     price_cents=parse_price_cents(variant.get("price")),
        #                     currency="ARS",
        #                     weight_grams=parse_weight_grams(variant.get("title")),
        #                     is_available=variant.get("available", True),
        #                     image_url=(product["images"][0]["src"] if product["images"] else None),
        #                     description=clean_text(product.get("body_html")),
        #                     attributes={"tags": product.get("tags", [])},
        #                 ))
        #         page += 1

        # ── Option B: HTML with Playwright + selectolax ───────────────────────
        #
        # page = await browser.new_page()
        # await page.goto(self.start_url + "/cafe")
        # await page.wait_for_selector(".product-card")   # wait for JS to render
        # html = await page.content()
        # await page.close()
        #
        # from selectolax.parser import HTMLParser
        # tree = HTMLParser(html)
        # for card in tree.css(".product-card"):
        #     name_node = card.css_first(".product-title")
        #     price_node = card.css_first(".price")
        #     link_node = card.css_first("a")
        #     if not name_node or not link_node:
        #         continue
        #     coffees.append(CoffeeData(
        #         external_id=link_node.attributes.get("href", ""),
        #         name=clean_text(name_node.text()),
        #         url=self.start_url + link_node.attributes.get("href", ""),
        #         price_cents=parse_price_cents(price_node.text() if price_node else None),
        #     ))

        return coffees
