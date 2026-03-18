from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


class Base(DeclarativeBase):
    pass


class Roaster(Base):
    __tablename__ = "roasters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    slug: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    website_url: Mapped[str] = mapped_column(String, nullable=False)
    country: Mapped[str | None] = mapped_column(String)
    scraper_module: Mapped[str] = mapped_column(String, nullable=False)
    scrape_interval_hours: Mapped[int] = mapped_column(Integer, default=24)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    coffees: Mapped[list["Coffee"]] = relationship(back_populates="roaster")
    scrape_runs: Mapped[list["ScrapeRun"]] = relationship(back_populates="roaster")

    def __repr__(self) -> str:
        return f"<Roaster {self.slug}>"


class Coffee(Base):
    __tablename__ = "coffees"
    __table_args__ = (UniqueConstraint("roaster_id", "external_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    roaster_id: Mapped[int] = mapped_column(ForeignKey("roasters.id"), nullable=False)
    external_id: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    url: Mapped[str] = mapped_column(String, nullable=False)
    price_cents: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String, default="ARS")
    weight_grams: Mapped[int | None] = mapped_column(Integer)
    is_available: Mapped[bool] = mapped_column(Boolean, default=True)
    image_url: Mapped[str | None] = mapped_column(String)
    description: Mapped[str | None] = mapped_column(Text)
    origin_country: Mapped[str | None] = mapped_column(String, index=True)
    process: Mapped[str | None] = mapped_column(String, index=True)
    roast_level: Mapped[str | None] = mapped_column(String, index=True)
    acidity: Mapped[float | None] = mapped_column(Float, index=True)   # 1–5
    sweetness: Mapped[float | None] = mapped_column(Float, index=True) # 1–5
    body: Mapped[float | None] = mapped_column(Float, index=True)      # 1–5
    variety: Mapped[str | None] = mapped_column(String)
    altitude_masl: Mapped[int | None] = mapped_column(Integer)
    attributes: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    brew_methods: Mapped[list[str] | None] = mapped_column(JSON)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    roaster: Mapped["Roaster"] = relationship(back_populates="coffees")

    def __repr__(self) -> str:
        return f"<Coffee {self.name}>"


class ScrapeRun(Base):
    __tablename__ = "scrape_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    roaster_id: Mapped[int] = mapped_column(ForeignKey("roasters.id"), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String, default="running")  # success, failed, partial
    coffees_found: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)

    roaster: Mapped["Roaster"] = relationship(back_populates="scrape_runs")

    def __repr__(self) -> str:
        return f"<ScrapeRun roaster_id={self.roaster_id} status={self.status}>"
