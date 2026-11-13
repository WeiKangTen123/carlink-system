"""Paths, per-source fetch policies and brand routing.

Nothing is created at import. `Settings.ensure_dirs()` creates the data
folder when the server starts.

Environment overrides:
  PARTS_MCP_DATA_DIR   data folder (default: parts_mcp/data)
  PARTS_MCP_SOURCES    comma list of sources to load (default: microfiches)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from .core.fetch import TTL_CATALOGUE, TTL_PRICE, FetchPolicy

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = PACKAGE_DIR / "data"

# Honest UA for sites whose robots.txt welcomes bots. Says who we are and why.
HONEST_USER_AGENT = (
    "CarlinkPartsBot/0.1 (+https://carlink.34-16-92-98.sslip.io; "
    "motor-claims surveyor parts lookup; low volume, caches results)"
)

POLICIES: dict[str, FetchPolicy] = {
    # bmw-spare-parts.com, parts-honda.uk, mini-spare-parts.com. robots.txt
    # asks Crawl-delay 5; the fetcher also reads it live and uses the larger.
    "microfiches": FetchPolicy(
        name="microfiches",
        ua_mode="honest",
        user_agent=HONEST_USER_AGENT,
        min_delay=5.0,
        max_delay=8.0,
        daily_cap=500,  # per host
        timeout=30.0,
    ),
}

# Plug-in factories: "module:function". Each function is `create_source(ctx) -> Source`.
SOURCE_FACTORIES: dict[str, str] = {
    "microfiches": "parts_mcp.sources.microfiches:create_source",
    "fake": "parts_mcp.sources.fake:create_source",
}

DEFAULT_SOURCES = ("microfiches",)

# Brand -> sources to try in order. Brands not listed use DEFAULT_ROUTE (none:
# a make no loaded source serves is answered "not supported").
ROUTES: dict[str, tuple[str, ...]] = {
    "BMW": ("microfiches",),
    "MINI": ("microfiches",),
    "HONDA": ("microfiches",),
}
DEFAULT_ROUTE: tuple[str, ...] = ()


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    sources: tuple[str, ...] = DEFAULT_SOURCES
    policies: Mapping[str, FetchPolicy] = field(default_factory=lambda: dict(POLICIES))
    factories: Mapping[str, str] = field(default_factory=lambda: dict(SOURCE_FACTORIES))
    routes: Mapping[str, tuple[str, ...]] = field(default_factory=lambda: dict(ROUTES))
    default_route: tuple[str, ...] = DEFAULT_ROUTE
    catalogue_ttl: float = TTL_CATALOGUE  # groups, diagrams, parts, vehicles
    price_ttl: float = TTL_PRICE

    @property
    def catalogue_db(self) -> Path:
        return self.data_dir / "catalogue.db"

    @property
    def page_cache_db(self) -> Path:
        return self.data_dir / "pages.db"

    @property
    def cookie_dir(self) -> Path:
        return self.data_dir / "cookies"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.cookie_dir, self.log_dir):
            path.mkdir(parents=True, exist_ok=True)


def load_settings(
    *,
    data_dir: str | Path | None = None,
    sources: tuple[str, ...] | None = None,
    env: Mapping[str, str] | None = None,
) -> Settings:
    """Settings from arguments, then environment, then defaults."""
    env = os.environ if env is None else env
    folder = Path(data_dir or env.get("PARTS_MCP_DATA_DIR") or DEFAULT_DATA_DIR)
    if sources is None:
        listed = env.get("PARTS_MCP_SOURCES", "")
        sources = tuple(s.strip() for s in listed.split(",") if s.strip()) or DEFAULT_SOURCES
    return Settings(data_dir=folder.resolve(), sources=sources)
