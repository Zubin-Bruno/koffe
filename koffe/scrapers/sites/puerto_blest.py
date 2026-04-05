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
    normalize_origin,
    normalize_process,
    normalize_tasting_notes,
    parse_price_cents,
    parse_weight_grams,
)
from koffe.scrapers.vision import extract_intensities_from_image

BASE_URL = "https://www.cafepuertoblest.com"

# Each listing page we scrape.  Order matters: /filtrados/ and /espressos/ first
# so their brew_method labels take priority when a product appears on multiple pages.
# /cafe-especial/ is the catch-all that picks up anything not on the other two.
LISTING_PAGES = [
    {"url": f"{BASE_URL}/filtrados/",     "brew_method": "Filtro"},
    {"url": f"{BASE_URL}/espressos/",     "brew_method": "Espresso"},
    {"url": f"{BASE_URL}/cafe-especial/", "brew_method": None},  # catch-all
]


class PuertoBlestScraper(BaseScraper):
    roaster_slug = "puerto-blest"

    async def scrape(self, browser) -> list[CoffeeData]:
        coffees: list[CoffeeData] = []

        # Collect product links from all listing pages, dedup by URL.
        # First-seen entry wins, so brew_method from /filtrados/ and /espressos/
        # takes priority over the catch-all /cafe-especial/ page.
        product_entries: dict[str, dict] = {}  # url -> {url, price_text, brew_method}
        filter_slugs: set[str] = set()
        espresso_slugs: set[str] = set()

        for lp in LISTING_PAGES:
            entries = await self._collect_listing(browser, lp["url"])
            for entry in entries:
                slug = entry["url"].rstrip("/").split("/")[-1]
                if lp["brew_method"] == "Filtro":
                    filter_slugs.add(slug)
                elif lp["brew_method"] == "Espresso":
                    espresso_slugs.add(slug)
                if entry["url"] not in product_entries:
                    entry["brew_method"] = lp["brew_method"]
                    product_entries[entry["url"]] = entry

        logger.debug(f"[puerto-blest] Found {len(product_entries)} unique product links")
        logger.debug(f"[puerto-blest] Filter slugs: {filter_slugs}")
        logger.debug(f"[puerto-blest] Espresso slugs: {espresso_slugs}")

        for entry in product_entries.values():
            url = entry["url"]
            slug = url.rstrip("/").split("/")[-1]

            if "box-de-regalo" in slug:
                logger.debug(f"[puerto-blest] Skipping gift box: {slug}")
                continue

            # Assign brew_methods based on which listing pages the product appeared on
            if slug in filter_slugs and slug in espresso_slugs:
                brew_methods = ["Filtro", "Espresso"]
            elif slug in filter_slugs:
                brew_methods = ["Filtro"]
            elif slug in espresso_slugs:
                brew_methods = ["Espresso"]
            else:
                brew_methods = []  # only on catch-all, no specific brew method

            try:
                coffee = await self._scrape_product(browser, url, slug, entry["price_text"], brew_methods or None)
                if coffee:
                    coffees.append(coffee)
            except Exception as e:
                logger.warning(f"[puerto-blest] Failed to scrape {url}: {e}")

        logger.info(f"[puerto-blest] Total coffees found: {len(coffees)}")
        return coffees

    async def _collect_listing(self, browser, listing_url: str) -> list[dict]:
        """Load a listing page, click 'Mostrar más' to load ALL products, return entries."""
        page = await browser.new_page()
        try:
            await page.goto(listing_url, wait_until="networkidle", timeout=60000)

            # Click "Mostrar más" until all products are loaded
            for _ in range(20):  # safety cap
                btn = page.locator("a.js-load-more-btn")
                if await btn.count() > 0 and await btn.is_visible():
                    await btn.click()
                    await page.wait_for_load_state("networkidle")
                else:
                    break

            html = await page.content()
        except Exception as e:
            logger.warning(f"[puerto-blest] Could not fetch {listing_url}: {e}")
            return []
        finally:
            await page.close()

        tree = HTMLParser(html)
        entries: list[dict] = []
        seen: set[str] = set()

        for card in tree.css(".js-item-product"):
            link = card.css_first("a[href*='/productos/']")
            if not link:
                continue
            href = link.attributes.get("href", "")
            if not href or href in seen:
                continue
            seen.add(href)

            if href.startswith("/"):
                href = BASE_URL + href

            price_node = card.css_first(".js-price-display")
            price_text = price_node.text() if price_node else None

            entries.append({"url": href, "price_text": price_text})

        return entries

    async def _scrape_product(
        self, browser, url: str, slug: str, listing_price_text: str | None = None,
        brew_methods: list[str] | None = None,
    ) -> CoffeeData | None:
        page = await browser.new_page()
        try:
            await page.goto(url, wait_until="networkidle", timeout=60000)

            # Scroll the page to trigger lazy-loading of images.
            # The site uses on-scroll observers that swap placeholder GIFs
            # for real image URLs only when the user scrolls down.
            await page.evaluate("""
                async () => {
                    const delay = ms => new Promise(r => setTimeout(r, ms));
                    for (let y = 0; y < document.body.scrollHeight; y += 300) {
                        window.scrollTo(0, y);
                        await delay(100);
                    }
                    window.scrollTo(0, 0);
                }
            """)
            await page.wait_for_timeout(1000)

            # Check availability via the add-to-cart button (Playwright, before closing)
            # Tiendanube hides a "Sin stock" label in the template even for available products,
            # so we check whether the cart button is present and not disabled.
            add_btn = page.locator("input.js-addtocart, button.js-addtocart")
            is_available = (
                await add_btn.count() > 0
                and await add_btn.first.is_enabled()
            )

            # Extract image URLs from the live DOM (before closing the page).
            # The carousel wraps each image in an <a class="js-product-slide-link">
            # whose href always points to the full-res image — even when the <img>
            # itself still shows a lazy-load placeholder GIF.
            all_image_urls = await page.evaluate("""
                () => {
                    const links = document.querySelectorAll('.js-product-slide-link');
                    const urls = [];
                    for (const link of links) {
                        let url = link.href;
                        if (!url || url.includes('data:image')) continue;
                        if (url.startsWith('//')) url = 'https:' + url;
                        urls.push(url);
                    }
                    return urls;
                }
            """)

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

        image_url = all_image_urls[0] if all_image_urls else None

        # Description
        desc_node = tree.css_first(".product-description, .js-product-description")
        description = clean_text(desc_node.text()) if desc_node else None

        # Extract structured fields from page text
        page_text = tree.body.text() if tree.body else ""

        origin_country = normalize_origin(name, page_text)
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
        tasting_notes = normalize_tasting_notes(self._extract_tasting_notes(tree))
        attributes = {}
        if tasting_notes:
            attributes["tasting_notes"] = tasting_notes

        # Vision — extract acidity/body from the coffee card image.
        # Sweetness is hardcoded to 5: Puerto Blest cards use SCA "Dulzor"
        # which is a pass/fail metric (almost always 10/10), not an intensity.
        # Only attempt vision if there are 2+ images (the last one is the card).
        acidity = None
        body = None
        sweetness = 5
        if len(all_image_urls) >= 2:
            card_url = all_image_urls[-1]
            logger.debug(f"[puerto-blest] Sending coffee card to vision: {card_url}")
            intensities = await extract_intensities_from_image(card_url)
            acidity = intensities["acidity"]
            body = intensities["body"]
            logger.info(f"[puerto-blest] Vision: acidity={acidity}, body={body}, sweetness={sweetness} (hardcoded)")

        # Hardcode overrides for Altura 1 - House Blend (blend with known Peru origin;
        # vision-extracted intensities are unreliable for this coffee)
        normalized = normalize_name(name)
        if normalized and "altura 1" in normalized.lower() and "house blend" in normalized.lower():
            origin_country = "Perú"
            acidity = None
            sweetness = None
            body = None

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
            acidity=acidity,
            body=body,
            sweetness=sweetness,
            brew_methods=brew_methods,
            attributes=attributes,
        )

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

    def _extract_tasting_notes(self, tree) -> list[str] | None:
        for strong in tree.css("strong"):
            if "organolépticas" in (strong.text() or "").lower():
                parent = strong.parent
                if parent:
                    full = parent.text() or ""
                    _, _, after = full.partition(":")
                    raw = after.strip()
                    if raw:
                        notes = [n.strip() for n in re.split(r"[,/&+]|\s+y\s+", raw) if n.strip()]
                        return notes[:6] if notes else None
        return None
