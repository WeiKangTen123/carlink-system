"""Load the plug-ins named in the settings."""

from __future__ import annotations

import importlib
import logging

from ..config import Settings
from ..core.fetch import PageCache
from .base import Source, SourceContext

log = logging.getLogger("parts_mcp.sources")


def load_sources(settings: Settings, page_cache: PageCache) -> tuple[list[Source], dict[str, str]]:
    """Returns (loaded sources in settings order, {name: why it did not load})."""
    ctx = SourceContext(settings=settings, page_cache=page_cache)
    loaded: list[Source] = []
    problems: dict[str, str] = {}
    for name in settings.sources:
        target = settings.factories.get(name)
        if target is None:
            problems[name] = "no factory configured"
            continue
        module_name, _, func_name = target.partition(":")
        try:
            factory = getattr(importlib.import_module(module_name), func_name or "create_source")
            source = factory(ctx)
        except ModuleNotFoundError as exc:
            if exc.name and module_name.startswith(exc.name):
                problems[name] = f"plug-in not installed ({module_name})"
            else:
                problems[name] = f"missing dependency: {exc}"
            log.warning("source %s not loaded: %s", name, problems[name])
            continue
        except Exception as exc:  # a broken plug-in must not stop the server
            problems[name] = f"{type(exc).__name__}: {exc}"
            log.exception("source %s failed to load", name)
            continue
        if source.name != name:
            problems[name] = f"factory returned a source named {source.name!r}"
            log.warning("source %s not loaded: %s", name, problems[name])
            continue
        loaded.append(source)
    return loaded, problems
