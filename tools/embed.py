#!/usr/bin/env python3
"""Copy the website into another site under a sub-path.

    python3 tools/embed.py OUT_DIR BASE_PATH API_ORIGIN

    python3 tools/embed.py ../mertcangenc.com/pacemaker /pacemaker/ https://pacemaker.mertcangenc.workers.dev

The page itself is static. Only the BPM and sharing API runs on the Worker, so
the copy is told where the API lives and allowed to connect to it.
"""
import re
import shutil
import sys
from pathlib import Path

SITE = Path(__file__).resolve().parents[1] / "site"


def main(out_dir: str, base: str, api: str) -> None:
    if not base.startswith("/") or not base.endswith("/"):
        sys.exit("BASE_PATH must start and end with /, e.g. /pacemaker/")
    if not re.fullmatch(r"https?://[A-Za-z0-9.-]+(:\d+)?", api):
        sys.exit("API_ORIGIN must be an origin like https://pacemaker.example.workers.dev")
    out = Path(out_dir)
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(SITE, out)

    index = out / "index.html"
    html = index.read_text(encoding="utf-8")
    replacements = [
        ('<base href="/">', f'<base href="{base}">'),
        ('<meta name="pacemaker-api" content="">', f'<meta name="pacemaker-api" content="{api}">'),
        ("connect-src 'self'", f"connect-src 'self' {api}"),
    ]
    for old, new in replacements:
        if html.count(old) != 1:
            sys.exit(f"index.html changed shape; expected exactly one {old!r}")
        html = html.replace(old, new)
    index.write_text(html, encoding="utf-8")
    print(f"pacemaker -> {out} (base {base}, api {api})")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    main(*sys.argv[1:])
