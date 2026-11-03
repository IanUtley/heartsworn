#!/usr/bin/env python3
"""Assemble the browser demo into ``browser_demo/site``.

    python3 browser_demo/build.py

The site is static files: the web client page, Pyodide, and the engine's Python
sources as one zip. It ships no game data and no database. The player's own Hex
folder supplies ``Data/gamedata``, and the browser builds the database from it
with ``static.ensure_schema`` and the FRA populator, as the desktop server does.
"""
import argparse
import shutil
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SITE = HERE / "site"
PYODIDE = HERE / "probe" / "node_modules" / "pyodide"

# Directories whose Python is never imported by the browser run.
SKIP_DIRS = {"hex-private", "browser_demo", "tests", "campaign_editor", "docs",
             "node_modules", "__pycache__", "unity"}

# Repository folders holding authored JSON the engine loads by path (generated
# data is derived from the player's gamedata, so it is built, not shipped).
DATA_DIRS = ("campaign",)

INDEX_HEAD = '  <script src="demo/fetch-shim.js"></script>\n'


def _skipped(rel: Path) -> bool:
    return any(part in SKIP_DIRS or part.startswith(".") for part in rel.parts)


def write_python_zip(path: Path) -> int:
    count = 0
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for source in sorted(ROOT.rglob("*.py")):
            rel = source.relative_to(ROOT)
            if _skipped(rel):
                continue
            archive.write(source, rel.as_posix())
            count += 1
        # Authored data the engine reads at run time (campaign rules).
        for base in DATA_DIRS:
            for source in sorted((ROOT / base).rglob("*.json")):
                rel = source.relative_to(ROOT)
                if not _skipped(rel):
                    archive.write(source, rel.as_posix())
                    count += 1
        for name in ("shim.py", "bridge.py", "fra_from_gamedata.py"):
            archive.write(HERE / "demo" / name, f"demo/{name}")
            count += 1
        # The bridge imports ``demo`` as a package.
        archive.writestr("demo/__init__.py", "")
    return count


def copy_static() -> None:
    static = SITE / "static"
    shutil.copytree(ROOT / "web_client" / "static", static, dirs_exist_ok=True)
    html = (ROOT / "web_client" / "static" / "index.html").read_text(encoding="utf-8")
    head = "<head>\n"
    if head not in html:
        raise SystemExit("index.html has no <head> to hold the fetch shim")
    # Relative links, so the page also works under a sub-path such as GitHub Pages.
    html = html.replace('="/static/', '="static/').replace(
        '/demo/fetch-shim.js', 'demo/fetch-shim.js')
    (SITE / "index.html").write_text(html.replace(head, head + INDEX_HEAD, 1),
                                    encoding="utf-8")


def copy_demo_files() -> None:
    target = SITE / "demo"
    target.mkdir(parents=True, exist_ok=True)
    for name in ("worker.js", "fetch-shim.js"):
        shutil.copy2(HERE / "demo" / name, target / name)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.parse_args(argv)
    if SITE.exists():
        shutil.rmtree(SITE)
    SITE.mkdir(parents=True)
    shutil.copytree(PYODIDE, SITE / "pyodide")
    count = write_python_zip(SITE / "hex-py.zip")
    copy_static()
    copy_demo_files()
    size = sum(p.stat().st_size for p in SITE.rglob("*") if p.is_file())
    print(f"site: {SITE} ({count} Python modules, {size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
