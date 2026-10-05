#!/usr/bin/env python3
"""Package a firmware build for a GitHub release and the browser installer.

Writes, into --out:

* ``release/``: files attached to the GitHub release
  * ``tempem-remote-gateway.factory.bin``: full image for a first install (offset 0)
  * ``tempem-remote-gateway.ota.bin``: app image for updates
  * ``tempem-remote-gateway.manifest.json``: read by the gateway's update
    entity. Absolute URLs pin it to this release's files.
* ``site/``: the browser installer (ESP Web Tools) for GitHub Pages, with
  the same binaries and a manifest that uses relative paths (the browser
  can't fetch GitHub release assets cross-origin).

Only the standard library is used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil

NAME = "tempem-remote-gateway"
TITLE = "Tempem remote gateway"
CHIP_FAMILY = "ESP32-C6"


def _md5(path: Path) -> str:
    # ESPHome OTA verifies images with MD5.
    return hashlib.md5(path.read_bytes()).hexdigest()


def _manifest(version: str, factory: str, ota: str, md5: str, release_url: str) -> dict:
    return {
        "name": TITLE,
        "version": version,
        "new_install_prompt_erase": True,
        "builds": [
            {
                "chipFamily": CHIP_FAMILY,
                "parts": [{"path": factory, "offset": 0}],
                "ota": {
                    "path": ota,
                    "md5": md5,
                    "summary": f"{TITLE} {version}",
                    "release_url": release_url,
                },
            }
        ],
    }


def main() -> None:
    """Write the release and site folders."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version", required=True, help="release version, e.g. 0.2.0")
    parser.add_argument("--repo", required=True, help="owner/name on GitHub")
    parser.add_argument(
        "--build-dir",
        required=True,
        type=Path,
        help="ESPHome build dir with firmware.*.bin",
    )
    parser.add_argument(
        "--site-template", required=True, type=Path, help="folder with index.html"
    )
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    factory_src = args.build_dir / "firmware.factory.bin"
    ota_src = args.build_dir / "firmware.ota.bin"
    for src in (factory_src, ota_src):
        if not src.is_file():
            raise SystemExit(f"missing {src}")

    tag = f"v{args.version}"
    download = f"https://github.com/{args.repo}/releases/download/{tag}"
    release_url = f"https://github.com/{args.repo}/releases/tag/{tag}"
    factory, ota = f"{NAME}.factory.bin", f"{NAME}.ota.bin"
    md5 = _md5(ota_src)

    release = args.out / "release"
    site = args.out / "site"
    for folder in (release, site / "firmware"):
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(factory_src, folder / factory)
        shutil.copyfile(ota_src, folder / ota)

    (release / f"{NAME}.manifest.json").write_text(
        json.dumps(
            _manifest(
                args.version,
                f"{download}/{factory}",
                f"{download}/{ota}",
                md5,
                release_url,
            ),
            indent=2,
        )
        + "\n"
    )
    (site / "firmware" / "manifest.json").write_text(
        json.dumps(_manifest(args.version, factory, ota, md5, release_url), indent=2)
        + "\n"
    )
    index = (args.site_template / "index.html").read_text()
    (site / "index.html").write_text(
        index.replace("{{VERSION}}", args.version).replace(
            "{{RELEASE_URL}}", release_url
        )
    )
    print(f"packaged {tag}: ota md5 {md5}")


if __name__ == "__main__":
    main()
