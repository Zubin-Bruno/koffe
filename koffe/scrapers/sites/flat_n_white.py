"""
Scraper for Flat N' White — https://flatnwhite.com
Argentine specialty coffee roaster. WooCommerce + XStore theme.
"""

import base64
import json
import re
import urllib.parse

from loguru import logger
from selectolax.parser import HTMLParser

from koffe.scrapers.base import BaseScraper, CoffeeData
from koffe.scrapers.utils import (
    clean_text,
    normalize_brew_methods,
    normalize_name,
    normalize_origin,
    normalize_process,
    normalize_roast,
    normalize_tasting_notes,
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
        urls = await self._fetch_listing_page(browser)

        # Retry up to 2 more times if 0 links found — WooCommerce/XStore
        # sometimes returns a skeleton page on the first load (transient CDN
        # or JS issue).  The third attempt waits longer to give the CDN time.
        if not urls:
            import asyncio
            logger.warning("[flat-n-white] 0 product links on first attempt, retrying in 5s…")
            await asyncio.sleep(5)
            urls = await self._fetch_listing_page(browser)

        if not urls:
            import asyncio
            logger.warning("[flat-n-white] 0 product links on second attempt, retrying in 10s…")
            await asyncio.sleep(10)
            urls = await self._fetch_listing_page(browser)

        return urls

    async def _fetch_listing_page(self, browser) -> list[str]:
        """Load the listing page once and extract product links.

        Flat & White uses "Rocket LazyLoadScripts" (WP Rocket v2.0.3) that
        defers all JavaScript until a user interaction event.  Rocket listens
        on **window** (not document) and **ignores the first mousemove**.  It
        responds to: mousedown, touchstart, keydown, etc.

        We use four complementary strategies, each more aggressive:
        """
        page = await browser.new_page()
        try:
            await page.goto(LISTING_URL, wait_until="networkidle", timeout=60000)

            # Strategy 1: JavaScript dispatchEvent — fire events that Rocket
            # actually responds to, on `window` (where Rocket listens).
            # Rocket ignores the first mousemove, so we use mousedown,
            # touchstart, and keydown instead.
            await page.evaluate("""() => {
                ['mousedown', 'touchstart', 'keydown'].forEach(type => {
                    window.dispatchEvent(new Event(type, { bubbles: true }));
                });
            }""")

            # Strategy 2: Playwright mouse.click — a click fires mousedown +
            # mouseup + click, all of which Rocket listens for.  (mouse.move
            # only fires mousemove, which Rocket ignores.)
            await page.mouse.click(100, 200)

            # Strategy 3: scroll — triggers Intersection Observers and any
            # scroll-based lazy loaders.
            await page.evaluate("window.scrollBy(0, 300)")

            # Wait for product elements to appear after lazy scripts execute
            products_appeared = True
            try:
                await page.wait_for_selector(
                    "li.product a, a.woocommerce-LoopProduct-link, .products .product a",
                    timeout=20000,
                )
            except Exception:
                products_appeared = False
                logger.warning("[flat-n-white] Products did not appear after triggering lazy load")

            # Strategy 4 (nuclear): If products still haven't appeared,
            # directly execute Rocket-deferred scripts by changing their type
            # from "rocketlazyloadscript" back to real script elements.
            if not products_appeared:
                logger.info("[flat-n-white] Attempting direct Rocket script bypass…")
                await page.evaluate("""() => {
                    document.querySelectorAll('script[type="rocketlazyloadscript"]').forEach(old => {
                        const s = document.createElement('script');
                        [...old.attributes].forEach(a => {
                            if (a.name === 'type') return;
                            s.setAttribute(a.name === 'data-rocket-src' ? 'src' : a.name, a.value);
                        });
                        if (old.textContent) s.textContent = old.textContent;
                        old.parentNode.replaceChild(s, old);
                    });
                }""")
                # Wait again for products to render after forced script execution
                try:
                    await page.wait_for_selector(
                        "li.product a, a.woocommerce-LoopProduct-link, .products .product a",
                        timeout=10000,
                    )
                except Exception:
                    logger.warning("[flat-n-white] Products still missing after Rocket bypass")

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

            # --- Trigger lazy-loading (same strategies as _fetch_listing_page) ---
            # Rocket LazyLoadScripts defers all JS until a user interaction event.
            # Without this, <img> tags still have data:image/svg+xml placeholders.
            await page.evaluate("""() => {
                ['mousedown', 'touchstart', 'keydown'].forEach(type => {
                    window.dispatchEvent(new Event(type, { bubbles: true }));
                });
            }""")
            await page.mouse.click(100, 200)
            await page.evaluate("window.scrollBy(0, 300)")

            # Wait for the product title to appear (proves content has loaded).
            # On Render's slower Docker, a blind 2s wait is not enough.
            title_appeared = True
            try:
                await page.wait_for_selector("h1.product_title", timeout=15000)
            except Exception:
                title_appeared = False
                logger.warning(f"[flat-n-white] product_title not found on first attempt for {url}")

            # Nuclear fallback: force-execute Rocket-deferred scripts (same as listing page)
            if not title_appeared:
                logger.info(f"[flat-n-white] Attempting Rocket bypass on product page {url}")
                await page.evaluate("""() => {
                    document.querySelectorAll('script[type="rocketlazyloadscript"]').forEach(old => {
                        const s = document.createElement('script');
                        [...old.attributes].forEach(a => {
                            if (a.name === 'type') return;
                            s.setAttribute(a.name === 'data-rocket-src' ? 'src' : a.name, a.value);
                        });
                        if (old.textContent) s.textContent = old.textContent;
                        old.parentNode.replaceChild(s, old);
                    });
                }""")
                try:
                    await page.wait_for_selector("h1.product_title", timeout=10000)
                except Exception:
                    logger.warning(f"[flat-n-white] product_title STILL missing after Rocket bypass — skipping {url}")
                    await page.close()
                    return []

            # --- Extract image URL from the LIVE DOM before closing the page ---
            # This is critical: after lazy-loading triggers, the browser replaces
            # SVG placeholders with real image URLs. We must read them while the
            # page is still open. Using page.evaluate() reads the actual current
            # state of the DOM in the browser.
            image_url = await page.evaluate("""() => {
                const img = document.querySelector(
                    '.woocommerce-product-gallery__image img, .wp-post-image'
                );
                if (!img) return null;
                const candidates = [
                    img.currentSrc,
                    img.src,
                    img.dataset.src,
                    img.dataset.lazySrc,
                    img.dataset.largeImage,
                    img.closest('a') ? img.closest('a').href : null,
                ];
                for (const url of candidates) {
                    if (url && !url.startsWith('data:') && url.startsWith('http')) {
                        return url;
                    }
                }
                return null;
            }""")

            html = await page.content()
        finally:
            await page.close()

        tree = HTMLParser(html)

        # Name
        name_node = tree.css_first("h1.product_title")
        if not name_node:
            return []
        name = clean_text(name_node.text())
        if not name:
            return []

        # Image — fallback: if live DOM extraction didn't find a URL, try
        # offline HTML parsing (SVG base64 decode, data attributes, etc.)
        if not image_url:
            img_node = tree.css_first(
                ".woocommerce-product-gallery__image img, .wp-post-image"
            )
            if img_node:
                raw_src = img_node.attributes.get("src", "")
                if raw_src and not raw_src.startswith("data:"):
                    image_url = raw_src
                if not image_url and raw_src.startswith("data:image/svg+xml;base64,"):
                    # Decode the SVG and extract the real URL from its data-u attribute
                    try:
                        svg_text = base64.b64decode(
                            raw_src.split(",", 1)[1]
                        ).decode("utf-8", errors="ignore")
                        match = re.search(r'data-u="([^"]+)"', svg_text)
                        if match:
                            image_url = urllib.parse.unquote(match.group(1))
                    except Exception:
                        pass
                if not image_url:
                    image_url = (
                        img_node.attributes.get("data-src")
                        or img_node.attributes.get("data-lazy-src")
                        or img_node.attributes.get("data-large_image")
                    )
                # Last resort: WooCommerce stores full-size URL in the parent <a>
                if not image_url:
                    parent = img_node.parent
                    if parent and parent.tag == "a":
                        image_url = parent.attributes.get("href") or None

        # Short description
        desc_node = tree.css_first(
            ".woocommerce-product-details__short-description, .short-description"
        )
        description = clean_text(desc_node.text()) if desc_node else None

        # Availability fallback from JSON-LD
        is_available_default = self._parse_availability(tree)

        # Metadata from page body text
        page_text = tree.body.text() if tree.body else ""
        origin_country = normalize_origin(name, page_text)
        process = None
        _process_raw = self._extract_field(
            page_text,
            ["beneficio", "beneficiado", "proceso", "process", "fermentacion", "fermentación"]
            # "método" removed — too broad, matches "método de pago" / "método de envío" on WooCommerce pages
        )
        if _process_raw:
            process = normalize_process(_process_raw)
        if process is None:
            process = self._extract_process_from_tags(tree)
        if process is None:
            process = normalize_process(self._scan_process_keywords(page_text))
        variety = self._extract_field(page_text, ["varietal", "varietales", "variedad"])
        roast_level = normalize_roast(
            self._extract_field(page_text, ["tueste", "tostado", "roast"])
        )
        altitude_masl = self._extract_altitude(page_text)
        tasting_notes = normalize_tasting_notes(self._extract_tasting_notes(tree, page_text))
        # Brew methods are mentioned in prose inside the full description tab,
        # not in a labeled field. Pass the full tab text to normalize_brew_methods().
        desc_tab = tree.css_first("#tab-description, .woocommerce-Tabs-panel--description")
        brew_methods = normalize_brew_methods(desc_tab.text() if desc_tab else None)
        attributes: dict = {}
        if tasting_notes:
            attributes["tasting_notes"] = tasting_notes

        # One entry per product — collapse all grind/size variants
        slug = url.rstrip("/").split("/")[-1]
        variations = self._parse_variations(tree)

        if variations:
            prices = [v["price_cents"] for v in variations if v.get("price_cents")]
            price_cents = min(prices) if prices else None
            is_available = any(v.get("is_available", False) for v in variations)
        else:
            price_node = tree.css_first(".woocommerce-Price-amount")
            price_cents = parse_price_cents(price_node.text() if price_node else None)
            is_available = is_available_default

        return [
            CoffeeData(
                external_id=slug,
                name=normalize_name(name),
                url=url,
                price_cents=price_cents,
                currency="ARS",
                is_available=is_available,
                image_url=image_url,
                description=description,
                origin_country=origin_country,
                process=process,
                roast_level=roast_level,
                variety=variety,
                altitude_masl=altitude_masl,
                brew_methods=brew_methods,
                attributes=attributes,
            )
        ]

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

    def _extract_process_from_tags(self, tree: HTMLParser) -> str | None:
        """
        WooCommerce renders product tags as <a rel="tag"> links (the "Etiquetas" section).
        Run normalize_process() on each tag to find a process keyword.
        """
        for tag_node in tree.css("a[rel='tag']"):
            result = normalize_process(tag_node.text())
            if result:
                return result
        return None

    def _scan_process_keywords(self, text: str) -> str | None:
        """
        Fallback: scan page text for standalone process keywords when no labeled
        field was found. Returns the raw keyword so normalize_process() can map it.
        Order matters — most specific patterns first.
        """
        candidates = [
            (r"\banaer[oó]bic[oa]?\b", "anaerobico"),
            (r"\bdoble\s+fermentaci[oó]n\b", "anaerobico"),
            (r"\blavado\b", "lavado"),
            (r"\bwashed\b", "washed"),
            (r"\bhoney\b", "honey"),
            (r"\bmiel\b", "miel"),
            (r"\bnatural\b", "natural"),
            (r"\bseco\b", "seco"),
        ]
        lower = text.lower()
        for pattern, keyword in candidates:
            if re.search(pattern, lower):
                return keyword
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

    def _extract_tasting_notes(self, tree: HTMLParser, text: str) -> list[str] | None:
        # DOM-first: find an elementor-shortcode div whose parent/grandparent
        # contains "notas de cata" — this is the dedicated cup-notes element on
        # Flat N' White product pages.
        for node in tree.css("div.elementor-shortcode"):
            for ancestor in (node.parent, node.parent.parent if node.parent else None):
                if ancestor is None:
                    continue
                ancestor_text = ancestor.text(deep=True)
                if re.search(r"notas?\s+de\s+cata", ancestor_text, re.IGNORECASE):
                    raw = node.text().strip()
                    if raw:
                        notes = [n.strip() for n in re.split(r"[,/&+]|\s+y\s+", raw) if n.strip()]
                        return notes[:6] if notes else None

        # Regex fallback — "perfil" removed to avoid matching "perfil en taza"
        match = re.search(
            r"(?:notas?\s+de\s+cata|notas?|notes?)[:\s]+(.+?)(?:tostado|cosecha|recolecci[oó]n|secado|presentaci[oó]n|beneficio|proceso|varietal|varietales|variedad|altura|finca|origen|regi[oó]n|tueste|acidez|dulzura|cuerpo|puntaje|\n|\r|$)",
            text,
            re.IGNORECASE,
        )
        if match:
            raw = match.group(1).strip()
            notes = [n.strip() for n in re.split(r"[,/&+]|\s+y\s+", raw) if n.strip()]
            return notes[:6] if notes else None
        return None
