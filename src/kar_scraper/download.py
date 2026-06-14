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


def existing_manifest_entries(manifest_path: Path, errors: list[str] | None = None) -> list[DownloadedFile]:
    entries, _ = _read_existing_manifest(manifest_path, errors)
    return entries


def _read_existing_manifest(manifest_path: Path, errors: list[str] | None = None) -> tuple[list[DownloadedFile], bool]:
    if not manifest_path.exists():
        return [], False
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        return [DownloadedFile.model_validate(item) for item in payload.get("files", [])], False
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        if errors is not None:
            message = f"Could not read existing manifest {manifest_path}: {exc}"
            if message not in errors:
                errors.append(message)
        return [], True


def download_candidate(
    candidate: RankedCandidate,
    intent: SearchIntent,
    out_dir: Path,
    settings: Settings,
    known_files: list[DownloadedFile],
) -> DownloadedFile:
    if not _is_direct_kar_url(candidate.url):
        raise DownloadError("Candidate URL is not a direct .kar URL")

    known_urls = {item.url for item in known_files}
    known_hashes = {item.sha256 for item in known_files}
    if candidate.url in known_urls:
        raise DownloadError("URL already exists in manifest")

    data = _download_bytes(candidate.url, settings)

    validate_kar_bytes(data, settings.max_file_size_bytes)
    digest = hashlib.sha256(data).hexdigest()
    if digest in known_hashes:
        raise DownloadError("File content already exists in manifest")

    out_dir.mkdir(parents=True, exist_ok=True)
    filename = filename_for_candidate(candidate, intent, digest)
    path = out_dir / filename
    try:
        with path.open("xb") as file:
            file.write(data)
    except FileExistsError as exc:
        raise DownloadError(f"Target file already exists: {filename}") from exc
    return DownloadedFile(
        url=candidate.url,
        source_page=candidate.source_page,
        filename=filename,
        path=str(path),
        sha256=digest,
        size_bytes=len(data),
        reason=candidate.reason,
    )


def _download_bytes(url: str, settings: Settings) -> bytes:
    try:
        with httpx.Client(follow_redirects=True, timeout=settings.request_timeout_seconds) as client:
            with client.stream("GET", url) as response:
                response.raise_for_status()
                if not _is_direct_kar_url(str(response.url)):
                    raise DownloadError("Final redirected URL is not a direct .kar URL")
                _validate_response_headers(response, settings.max_file_size_bytes)

                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > settings.max_file_size_bytes:
                        raise DownloadError(f"Downloaded file is larger than {settings.max_file_size_bytes} bytes")
                    chunks.append(chunk)
                return b"".join(chunks)
    except DownloadError:
        raise
    except httpx.HTTPStatusError as exc:
        raise DownloadError(f"Download failed with HTTP {exc.response.status_code}") from exc
    except httpx.HTTPError as exc:
        raise DownloadError(f"Download failed: {exc}") from exc


def _validate_response_headers(response: httpx.Response, max_size: int) -> None:
    content_type = response.headers.get("content-type", "").lower()
    if "html" in content_type or content_type.startswith("text/"):
        raise DownloadError("URL returned text/HTML instead of a .kar file")

    content_length = response.headers.get("content-length")
    if content_length is None:
        return
    try:
        size = int(content_length)
    except ValueError:
        return
    if size > max_size:
        raise DownloadError(f"Downloaded file is larger than {max_size} bytes")


def _is_direct_kar_url(url: str) -> bool:
    parsed = urlparse(url)
    return bool(parsed.hostname) and parsed.scheme in {"http", "https"} and parsed.path.lower().endswith(".kar")


def write_manifest(manifest_path: Path, request: str, files: list[DownloadedFile], errors: list[str]) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    existing, manifest_was_corrupt = _read_existing_manifest(manifest_path, errors)
    by_key = {(item.url, item.sha256): item for item in existing}
    for item in files:
        by_key[(item.url, item.sha256)] = item
    manifest = Manifest(request=request, files=list(by_key.values()), errors=errors)
    temp_path = manifest_path.with_name(f".{manifest_path.name}.tmp")
    temp_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    if manifest_was_corrupt:
        _preserve_corrupt_manifest(manifest_path)
    temp_path.replace(manifest_path)


def _preserve_corrupt_manifest(manifest_path: Path) -> None:
    backup_path = manifest_path.with_name(f"{manifest_path.name}.corrupt")
    if backup_path.exists():
        return
    try:
        backup_path.write_bytes(manifest_path.read_bytes())
    except OSError:
        return
