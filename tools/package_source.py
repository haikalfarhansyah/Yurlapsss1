"""Create a clean YurLaps source package from this checkout.

The release ZIP is source-first: it contains the application, decoder vendor
metadata, Electron wrapper, workflow, and tests. Build caches and macOS finder
files are deliberately omitted so an uploaded archive cannot accidentally ship
stale or machine-specific files.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


EXCLUDED_DIRS = {
    ".git",
    ".pytest_cache",
    "__pycache__",
    "node_modules",
    "build",
    "dist",
    "dist-electron",
    ".venv",
    "venv",
}
EXCLUDED_NAMES = {".DS_Store", ".env"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".sqlite3", ".sqlite3-wal", ".sqlite3-shm"}
EXCLUDED_FILES = {
    "docs/quickrace-app.html", "docs/ui-mockup-akun-club.html",
    "docs/tangkapan-layar.html", "docs/RANCANGAN-AKUN-CLUB-SINKRONISASI.md",
    "run_stintlab_win8.py", "stintlab-win8.spec", "jalankan-win8.bat",
}


def included(path: Path) -> bool:
    if any(part in EXCLUDED_DIRS for part in path.parts):
        return False
    if path.name in EXCLUDED_NAMES or path.suffix.lower() in EXCLUDED_SUFFIXES:
        return False
    return True


def package(root: Path, output: Path) -> int:
    root = root.resolve()
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in root.rglob("*") if p.is_file() and included(p.relative_to(root))
                   and p.relative_to(root).as_posix() not in EXCLUDED_FILES
                   and not p.relative_to(root).as_posix().startswith((
                       "docs/ui-", "docs/app-qr-")))
    # Never include the output when it is placed inside the source tree.
    files = [p for p in files if p.resolve() != output]
    with ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            archive.write(path, path.relative_to(root).as_posix())
    print(f"Created {output} ({len(files)} files)")
    return len(files)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    package(args.root, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
