from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CoffeeData:
    """Normalized coffee data returned by every scraper."""

    external_id: str          # Unique ID on the roaster's site (e.g. Shopify product ID)
    name: str
    url: str
    price_cents: int | None = None
    currency: str = "ARS"
    weight_grams: int | None = None
    is_available: bool = True
    image_url: str | None = None
    description: str | None = None
    origin_country: str | None = None
    process: str | None = None
    roast_level: str | None = None
    acidity: int | None = None    # 1–5
    sweetness: int | None = None  # 1–5
    body: int | None = None       # 1–5
    variety: str | None = None
    altitude_masl: int | None = None
    attributes: dict[str, Any] = field(default_factory=dict)


class BaseScraper(ABC):
    """
    Abstract base class for all roaster scrapers.

    Subclasses only need to implement `scrape()`. The runner handles
    DB writes, error logging, and marking unavailable coffees.
    """

    roaster_slug: str  # Must match the slug in the DB
    start_url: str

    @abstractmethod
    async def scrape(self, browser) -> list[CoffeeData]:
        """
        Fetch and parse coffee products from the roaster's site.

        Args:
            browser: A Playwright Browser instance (already launched).

        Returns:
            List of CoffeeData for every coffee found in this run.
        """
        ...
