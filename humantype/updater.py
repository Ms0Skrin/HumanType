"""Checking GitHub for a newer release and fetching it; the same for Windows and Linux.

A release is a tag v<version> with HumanType-Setup-<version>.exe attached (see build.yml).
Windows runs that installer silently; Linux checks the tag out and runs linux/install.sh."""

import hashlib
import json
import logging
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import __version__

log = logging.getLogger(__name__)

REPO = "Ms0Skrin/HumanType"
LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
PAGE = f"https://github.com/{REPO}/releases/latest"
TIMEOUT = 15


@dataclass
class Release:
    version: str
    tag: str
    notes: str
    installer_url: str = ""  # the Windows setup, when the release has one
    installer_sha256: str = ""


def parse_version(text: str) -> tuple[int, ...]:
    """`v0.6`, `0.6.1` → (0, 6), (0, 6, 1); anything else sorts as oldest."""
    try:
        return tuple(int(p) for p in text.strip().lstrip("v").split("."))
    except ValueError:
        return ()


def _get(url: str) -> urllib.request.addinfourl:
    request = urllib.request.Request(url, headers={"User-Agent": f"HumanType/{__version__}", "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(request, timeout=TIMEOUT)


def latest() -> Release:
    """The newest published release. Raises OSError when GitHub cannot be reached."""
    with _get(LATEST) as response:
        data = json.load(response)
    tag = data["tag_name"]
    release = Release(version=tag.lstrip("v"), tag=tag, notes=(data.get("body") or "").strip())
    for asset in data.get("assets", []):
        if asset["name"].lower().endswith(".exe"):
            release.installer_url = asset["browser_download_url"]
            release.installer_sha256 = (asset.get("digest") or "").removeprefix("sha256:")
    return release


def newer() -> Release | None:
    """The latest release if it is newer than this copy, else None."""
    release = latest()
    if parse_version(release.version) > parse_version(__version__):
        return release
    return None


def download_installer(release: Release, progress=None) -> Path:
    """Saves the Windows setup to the temp folder and checks its digest when GitHub gives one."""
    if not release.installer_url:
        raise OSError(f"в релизе {release.tag} нет установщика")
    target = Path(tempfile.gettempdir()) / f"HumanType-Setup-{release.version}.exe"
    digest = hashlib.sha256()
    with _get(release.installer_url) as response, open(target, "wb") as out:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        while chunk := response.read(1 << 16):
            out.write(chunk)
            digest.update(chunk)
            done += len(chunk)
            if progress and total:
                progress(done / total)
    if release.installer_sha256 and digest.hexdigest() != release.installer_sha256.lower():
        target.unlink(missing_ok=True)
        raise OSError("установщик скачался с ошибкой (не совпала контрольная сумма)")
    log.info("downloaded %s", target)
    return target
