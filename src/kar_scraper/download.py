from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx

from kar_scraper.config import Settings
from kar_scraper.models import DownloadedFile, Manifest, RankedCandidate, SearchIntent

SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9 ._()&'+,-]+")


class DownloadError(RuntimeError):
    pass


def sanitize_filename(value: str) -> str:
    cleaned = SAFE_NAME_RE.sub("_", value).strip(" ._")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:150] or "karaoke"


def filename_for_candidate(candidate: RankedCandidate, intent: SearchIntent, content_hash: str) -> str:
    if intent.artist and intent.song:
        base = f"{intent.artist} - {intent.song}"
    else:
        path_name = Path(unquote(urlparse(candidate.url).path)).name
        base = path_name[:-4] if path_name.lower().endswith(".kar") else path_name
    return f"{sanitize_filename(base)}-{content_hash[:8]}.kar"


def validate_kar_bytes(data: bytes, max_size: int) -> None:
    if not data:
        raise DownloadError("Downloaded file is empty")
    if len(data) > max_size:
        raise DownloadError(f"Downloaded file is larger than {max_size} bytes")
    if not data.startswith(b"MThd"):
        raise DownloadError("Downloaded file does not start with the MIDI/KAR MThd header")


def existing_manifest_entries(manifest_path: Path) -> list[DownloadedFile]:
    if not manifest_path.exists():
        return []
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        return [DownloadedFile.model_validate(item) for item in payload.get("files", [])]
    except (OSError, json.JSONDecodeError, ValueError):
        return []


def download_candidate(
    candidate: RankedCandidate,
    intent: SearchIntent,
    out_dir: Path,
    settings: Settings,
    known_files: list[DownloadedFile],
) -> DownloadedFile:
    known_urls = {item.url for item in known_files}
    known_hashes = {item.sha256 for item in known_files}
    if candidate.url in known_urls:
        raise DownloadError("URL already exists in manifest")

    with httpx.Client(follow_redirects=True, timeout=settings.request_timeout_seconds) as client:
        response = client.get(candidate.url)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").lower()
        if "text/html" in content_type:
            raise DownloadError("URL returned HTML instead of a .kar file")
        data = response.content

    validate_kar_bytes(data, settings.max_file_size_bytes)
    digest = hashlib.sha256(data).hexdigest()
    if digest in known_hashes:
        raise DownloadError("File content already exists in manifest")

    out_dir.mkdir(parents=True, exist_ok=True)
    filename = filename_for_candidate(candidate, intent, digest)
    path = out_dir / filename
    path.write_bytes(data)
    return DownloadedFile(
        url=candidate.url,
        source_page=candidate.source_page,
        filename=filename,
        path=str(path),
        sha256=digest,
        size_bytes=len(data),
        reason=candidate.reason,
    )


def write_manifest(manifest_path: Path, request: str, files: list[DownloadedFile], errors: list[str]) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    existing = existing_manifest_entries(manifest_path)
    by_key = {(item.url, item.sha256): item for item in existing}
    for item in files:
        by_key[(item.url, item.sha256)] = item
    manifest = Manifest(request=request, files=list(by_key.values()), errors=errors)
    manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")

