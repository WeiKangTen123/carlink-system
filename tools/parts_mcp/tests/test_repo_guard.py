"""The repository is public, and the worker runs on the server with the Microfiches
catalogues only. The other parts site's plug-in and its browser-based clearance are
kept outside the repository (see README). This fails if any of it comes back into
tools/parts_mcp: code, scripts, requirements, Docker files and test fixtures (gzipped
pages included). README.md may name the site, to say why it is not here.
"""

from __future__ import annotations

import gzip
from pathlib import Path

from parts_mcp import config

# Split so that this file does not trip over itself.
BANNED = ("parts" + "ouq", "selenium" + "base", "clearance_" + "chrome", "cf_" + "clearance")
PACKAGE = Path(__file__).resolve().parents[1]
SKIP_DIRS = {"data", "__pycache__", ".pytest_cache"}  # local caches, never committed
SKIP_NAMES = {"README.md", "worker.env"}  # worker.env: the git-ignored token file on a PC


def package_files() -> list[Path]:
    out = []
    for path in sorted(PACKAGE.rglob("*")):
        rel = path.relative_to(PACKAGE)
        if path.is_dir() or any(part in SKIP_DIRS for part in rel.parts) or rel.as_posix() in SKIP_NAMES:
            continue
        out.append(path)
    return out


def text_of(path: Path) -> str:
    data = path.read_bytes()
    if path.suffix == ".gz":
        data = gzip.decompress(data)
    return data.decode("utf-8", errors="replace").lower()


def test_no_trace_of_the_other_site_or_browser_clearance():
    files = package_files()
    assert len(files) > 20 and any(p.name == "worker.py" for p in files)  # the walk sees the package
    found = [(p.relative_to(PACKAGE).as_posix(), word) for p in files for word in BANNED if word in text_of(p)]
    assert found == []


def test_only_microfiches_is_loaded_by_default():
    assert config.DEFAULT_SOURCES == ("microfiches",)
    assert set(config.SOURCE_FACTORIES) == {"microfiches", "fake"}
    assert set(config.POLICIES) == {"microfiches"} and config.POLICIES["microfiches"].ua_mode == "honest"
    assert {name for route in config.ROUTES.values() for name in route} == {"microfiches"}
    assert config.DEFAULT_ROUTE == ()
    modules = {p.stem for p in (PACKAGE / "sources").glob("*.py")}
    assert modules == {"__init__", "base", "fake", "microfiches", "microfiches_glossary", "registry"}
