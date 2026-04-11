"""
Scraper for Flat N' White — https://flatnwhite.com
Argentine specialty coffee roaster. WooCommerce + XStore theme.

The site uses ShortPixel for image lazy-loading (data-u attribute on <img> tags).
Coffee attributes are rendered as <h2> label + <p> value pairs.
"""

import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

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
SITEMAP_URL = f"{BASE_URL}/wp-sitemap-posts-product-1.xml"

# Products that appear in the sitemap but aren't coffee — used by the
# sitemap fallback to filter out machines, teas, accessories, etc.
_NON_COFFEE_SLUGS = {
    "maquina", "cafetera", "tetera", "mate", "yerba", "accesorio",
    "taza", "molinillo", "kit", "filtro", "cuchara", "infusor",
    "te-", "tea", "tisana", "herbal",
}


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

    # ------------------------------------------------------------------
    # Product link discovery
    # ------------------------------------------------------------------

    async def _get_product_links(self, browser) -> list[str]:
        """Get coffee product URLs from the category page, falling back to sitemap."""
        # Primary: load the coffee category page with Playwright
        urls = await self._fetch_category_page(browser)
        if urls:
            return urls

        # Fallback: sitemap XML filtered by blocklist
        logger.warning("[flat-n-white] Category page returned 0 URLs — falling back to sitemap")
        return self._fetch_sitemap_urls()

    async def _fetch_category_page(self, browser) -> list[str]:
        """Load the coffee category page and extract product links."""
        page = await browser.new_page()
        try:
            await page.goto(LISTING_URL, wait_until="networkidle", timeout=60000)

            # Wait for product elements to appear
            try:
                await page.wait_for_selector(
                    "li.product a, a.woocommerce-LoopProduct-link",
                    timeout=15000,
                )
            except Exception:
                logger.warning("[flat-n-white] Product links did not appear on category page")

            html = await page.content()
        finally:
            await page.close()

        tree = HTMLParser(html)
        urls = []
        seen = set()

        for sel in ["a.woocommerce-LoopProduct-link", "li.product a"]:
            for link in tree.css(sel):
                href = link.attributes.get("href", "")
                if href and href not in seen and href.startswith(BASE_URL):
                    seen.add(href)
                    urls.append(href)
            if urls:
                break

        return urls

    def _fetch_sitemap_urls(self) -> list[str]:
        """Fetch product URLs from WordPress sitemap XML, filtering non-coffee items."""
        try:
            req = urllib.request.Request(
                SITEMAP_URL,
                headers={"User-Agent": "Mozilla/5.0 (compatible; KoffeScraper/1.0)"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                xml_bytes = resp.read()
            root = ET.fromstring(xml_bytes)
            ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
            all_urls = [loc.text.strip() for loc in root.findall(".//sm:loc", ns) if loc.text]

            # Filter out non-coffee products using the blocklist
            urls = []
            for u in all_urls:
                slug = u.rstrip("/").split("/")[-1].lower()
                if not any(kw in slug for kw in _NON_COFFEE_SLUGS):
                    urls.append(u)

            logger.info(f"[flat-n-white] Sitemap returned {len(urls)} coffee URL(s) (filtered from {len(all_urls)} total)")
            return urls
        except Exception as e:
            logger.warning(f"[flat-n-white] Sitemap fetch failed: {e}")
            return []

    # ------------------------------------------------------------------
    # Product page scraping
    # ------------------------------------------------------------------

    async def _scrape_product(self, browser, url: str) -> list[CoffeeData]:
        page = await browser.new_page()
        try:
            await page.goto(url, wait_until="networkidle", timeout=60000)

            # Wait for title — proves the page content loaded
            try:
                await page.wait_for_selector("h1.product_title, h1", timeout=15000)
            except Exception:
                logger.warning(f"[flat-n-white] Title not found for {url} — skipping")
                return []

            # Wait for price to render (WooCommerce JS injects it)
            try:
                await page.wait_for_selector(".woocommerce-Price-amount", timeout=10000)
            except Exception:
                pass  # Price might come from variation JSON instead

            # Extract image URL from live DOM (after ShortPixel resolves lazy images)
            image_url = await page.evaluate("""() => {
                const img = document.querySelector(
                    '.woocommerce-product-gallery__image img, .wp-post-image'
                );
                if (!img) return null;
                const candidates = [
                    img.currentSrc,
                    img.src,
                    img.dataset.u,
                    img.dataset.src,
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

        # Name — try .product_title first, then plain h1
        name_node = tree.css_first("h1.product_title") or tree.css_first("h1")
        if not name_node:
            return []
        name = clean_text(name_node.text())
        if not name:
            return []

        # Image fallback — parse offline HTML if live DOM didn't find one
        if not image_url:
            image_url = self._extract_image_from_html(tree)

        # Short description
        desc_node = tree.css_first(
            ".woocommerce-product-details__short-description, .short-description"
        )
        description = clean_text(desc_node.text()) if desc_node else None

        # Availability from JSON-LD
        is_available_default = self._parse_availability(tree)

        # --- Extract attributes from h2/p pairs (new site structure) ---
        h2p_attrs = self._extract_attributes_from_h2p(tree)

        # Metadata from page body text (used as fallback)
        page_text = tree.body.text() if tree.body else ""

        # Origin
        origin_country = h2p_attrs.get("origin") or normalize_origin(name, page_text)

        # Roast level
        roast_level = h2p_attrs.get("roast") or normalize_roast(
            self._extract_field(page_text, ["tueste", "tostado", "roast"])
        )

        # Process: h2p → extract_field → tags → keyword scan
        process = h2p_attrs.get("process")
        if not process:
            _process_raw = self._extract_field(
                page_text,
                ["beneficio", "beneficiado", "proceso", "process", "fermentacion", "fermentación"],
            )
            if _process_raw:
                process = normalize_process(_process_raw)
        if not process:
            process = self._extract_process_from_tags(tree)
        if not process:
            process = normalize_process(self._scan_process_keywords(page_text))

        # Variety
        variety = h2p_attrs.get("variety") or self._extract_field(
            page_text, ["varietal", "varietales", "variedad"]
        )

        # Altitude
        altitude_masl = self._extract_altitude(page_text)

        # Tasting notes: h2p first, then DOM/regex fallback
        tasting_notes = h2p_attrs.get("tasting_notes")
        if not tasting_notes:
            tasting_notes = normalize_tasting_notes(self._extract_tasting_notes(tree, page_text))

        # Brew methods
        desc_tab = tree.css_first("#tab-description, .woocommerce-Tabs-panel--description")
        brew_methods = normalize_brew_methods(desc_tab.text() if desc_tab else None)

        # Build attributes dict
        attributes: dict = {}
        if tasting_notes:
            attributes["tasting_notes"] = tasting_notes
        if h2p_attrs.get("score"):
            attributes["score"] = h2p_attrs["score"]
        if h2p_attrs.get("cup_profile"):
            attributes["cup_profile"] = h2p_attrs["cup_profile"]

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

    # ------------------------------------------------------------------
    # Attribute extraction from <h2> label + <p> value pairs
    # ------------------------------------------------------------------

    def _extract_attributes_from_h2p(self, tree: HTMLParser) -> dict:
        """Parse <h2>Label</h2><p>Value</p> pairs used by the current site layout.

        Returns a dict with keys: origin, roast, process, variety, score,
        cup_profile, tasting_notes. Missing keys are omitted.
        """
        result = {}

        # Map h2 label text (lowercased) to handler
        label_map = {
            "puntaje": "score",
            "tueste": "roast",
            "origen": "origin",
            "perfil en taza": "cup_profile",
            "notas de cata": "tasting_notes",
            "proceso": "process",
            "beneficio": "process",
            "varietal": "variety",
            "varietales": "variety",
            "variedad": "variety",
        }

        for h2 in tree.css("h2"):
            label_text = clean_text(h2.text())
            if not label_text:
                continue
            label_lower = label_text.lower().strip()

            field_key = label_map.get(label_lower)
            if not field_key:
                continue

            # The value is in the next <p> sibling
            sibling = h2.next
            # Skip whitespace text nodes
            while sibling and sibling.tag in (None, "-text"):
                sibling = sibling.next
            if not sibling or sibling.tag != "p":
                continue

            value = clean_text(sibling.text())
            if not value:
                continue

            if field_key == "score":
                result["score"] = value
            elif field_key == "roast":
                result["roast"] = normalize_roast(value)
            elif field_key == "origin":
                result["origin"] = normalize_origin(None, value) or value
            elif field_key == "cup_profile":
                result["cup_profile"] = value
            elif field_key == "tasting_notes":
                raw_notes = [n.strip() for n in re.split(r"[,/&+]|\s+y\s+", value) if n.strip()]
                result["tasting_notes"] = normalize_tasting_notes(raw_notes)
            elif field_key == "process":
                result["process"] = normalize_process(value)
            elif field_key == "variety":
                result["variety"] = value

        return result

    # ------------------------------------------------------------------
    # Image extraction from offline HTML
    # ------------------------------------------------------------------

    def _extract_image_from_html(self, tree: HTMLParser) -> str | None:
        """Extract image URL from parsed HTML (fallback when live DOM fails)."""
        img_node = tree.css_first(
            ".woocommerce-product-gallery__image img, .wp-post-image"
        )
        if not img_node:
            return None

        # ShortPixel stores real URL in data-u
        data_u = img_node.attributes.get("data-u")
        if data_u and data_u.startswith("http"):
            return data_u

        # Regular src (if not a placeholder)
        src = img_node.attributes.get("src", "")
        if src and not src.startswith("data:") and src.startswith("http"):
            return src

        # Other lazy-load attributes
        for attr in ("data-src", "data-lazy-src", "data-large_image"):
            val = img_node.attributes.get(attr)
            if val and val.startswith("http"):
                return val

        # Last resort: WooCommerce stores full-size URL in parent <a>
        parent = img_node.parent
        if parent and parent.tag == "a":
            href = parent.attributes.get("href")
            if href and href.startswith("http"):
                return href

        return None

    # ------------------------------------------------------------------
    # Helpers (kept from previous version — still work the same)
    # ------------------------------------------------------------------

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
        """Check WooCommerce product tags (<a rel="tag">) for process keywords."""
        for tag_node in tree.css("a[rel='tag']"):
            result = normalize_process(tag_node.text())
            if result:
                return result
        return None

    def _scan_process_keywords(self, text: str) -> str | None:
        """Fallback: scan page text for standalone process keywords."""
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
        """DOM + regex fallback for tasting notes (used when h2/p parsing finds nothing)."""
        # DOM-first: find an elementor-shortcode div near "notas de cata"
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

        # Regex fallback
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
