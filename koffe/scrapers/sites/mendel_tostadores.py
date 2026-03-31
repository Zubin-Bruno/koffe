"""
Scraper for Mendel Tostadores.

This roaster doesn't have a website yet — coffees are sold via WhatsApp catalog.
This scraper returns static/hardcoded data so the runner keeps the coffees
marked as available on each daily run.
"""

from koffe.scrapers.base import BaseScraper, CoffeeData

CATALOG_URL = "https://www.whatsapp.com/catalog/5491137628574/?app_absent=0&utm_source=ig"


class MendelTostadoresScraper(BaseScraper):
    roaster_slug = "mendel-tostadores"
    start_url = CATALOG_URL

    async def scrape(self, browser) -> list[CoffeeData]:
        """Return hardcoded coffees — no real scraping needed."""
        return [
            CoffeeData(
                external_id="colombia-excelso",
                name="Colombia Excelso",
                url=CATALOG_URL,
                price_cents=1900000,
                currency="ARS",
                weight_grams=250,
                is_available=True,
                image_url="/images/mendel-tostadores_colombia-excelso.jpeg",
                origin_country="Colombia",
                process="Washed",
                roast_level="Medium",
                acidity=4,
                sweetness=4,
                body=4,
                variety=None,
                brew_methods=["Espresso", "Filtro"],
                attributes={"tasting_notes": ["frutos rojos", "floral"]},
            ),
            CoffeeData(
                external_id="bolivia-typica-regional",
                name="Bolivia Typica Regional",
                url=CATALOG_URL,
                price_cents=1800000,
                currency="ARS",
                weight_grams=250,
                is_available=True,
                image_url="/images/mendel-tostadores_bolivia-typica-regional.jpeg",
                origin_country="Bolivia",
                process="Washed",
                roast_level="Medium-Dark",
                acidity=3,
                sweetness=4,
                body=4,
                variety="Typica",
                brew_methods=["Espresso"],
                attributes={"tasting_notes": ["chocolatoso", "frutos secos", "desecados"]},
            ),
            CoffeeData(
                external_id="honduras-piedra-habladora",
                name="Honduras Piedra Habladora",
                url=CATALOG_URL,
                price_cents=2000000,
                currency="ARS",
                weight_grams=250,
                is_available=True,
                image_url="/images/mendel-tostadores_honduras-piedra-habladora.jpeg",
                origin_country="Honduras",
                process="Washed",
                roast_level="Medium",
                acidity=4,
                sweetness=4,
                body=4,
                variety=None,
                brew_methods=["Espresso"],
                attributes={"tasting_notes": ["naranja", "panela", "dátiles"]},
            ),
        ]
