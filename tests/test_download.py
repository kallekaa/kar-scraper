import hashlib
import json
from pathlib import Path

import httpx
import pytest
import respx

from kar_scraper.config import Settings
from kar_scraper.download import (
    DownloadError,
    download_candidate,
    existing_manifest_entries,
    filename_for_candidate,
    sanitize_filename,
    validate_kar_bytes,
    write_manifest,
)
from kar_scraper.models import DownloadedFile, RankedCandidate, SearchIntent


def make_settings() -> Settings:
    settings = Settings(OPENAI_API_KEY="test", FIRECRAWL_API_KEY="test")
    settings.max_file_size_bytes = 12
    return settings


def ranked_candidate(url: str = "https://example.com/files/song.kar") -> RankedCandidate:
    return RankedCandidate(url=url, accepted=True, score=1.0, reason="matches")


def search_intent() -> SearchIntent:
    return SearchIntent(artist="Queen", song="Test Song", max_results=1, search_intent="queen test song")


def test_sanitize_filename_removes_unsafe_characters() -> None:
    assert sanitize_filename('Queen: Bohemian/Rhapsody*?') == "Queen_ Bohemian_Rhapsody"


def test_validate_kar_bytes_accepts_midi_header() -> None:
    validate_kar_bytes(b"MThd\x00\x00\x00\x06", 100)


def test_validate_kar_bytes_rejects_html() -> None:
    with pytest.raises(DownloadError):
        validate_kar_bytes(b"<html></html>", 100)


def test_filename_for_candidate_uses_artist_song_and_hash() -> None:
    candidate = RankedCandidate(url="https://example.com/files/original.kar", accepted=True)
    intent = SearchIntent(artist="Queen", song="Bohemian Rhapsody", max_results=1, search_intent="queen")
    digest = hashlib.sha256(b"MThd").hexdigest()
    assert filename_for_candidate(candidate, intent, digest) == f"Queen - Bohemian Rhapsody-{digest[:8]}.kar"


def test_filename_for_candidate_falls_back_to_url_name() -> None:
    candidate = RankedCandidate(url="https://example.com/files/my-song.kar", accepted=True)
    intent = SearchIntent(max_results=1, search_intent="my song")
    digest = hashlib.sha256(b"MThd").hexdigest()
    assert filename_for_candidate(candidate, intent, digest) == f"my-song-{digest[:8]}.kar"


def test_download_candidate_rejects_non_direct_original_url(tmp_path: Path) -> None:
    with pytest.raises(DownloadError, match="direct .kar URL"):
        download_candidate(
            ranked_candidate("https://example.com/download?id=song.kar"),
            search_intent(),
            tmp_path,
            make_settings(),
            [],
        )


@respx.mock
def test_download_candidate_rejects_redirect_to_non_direct_url(tmp_path: Path) -> None:
    respx.get("https://example.com/files/song.kar").mock(
        return_value=httpx.Response(302, headers={"location": "https://example.com/download?id=1"})
    )
    respx.get("https://example.com/download?id=1").mock(return_value=httpx.Response(200, content=b"MThdabc"))

    with pytest.raises(DownloadError, match="Final redirected URL"):
        download_candidate(ranked_candidate(), search_intent(), tmp_path, make_settings(), [])


@respx.mock
def test_download_candidate_rejects_oversized_content_length(tmp_path: Path) -> None:
    respx.get("https://example.com/files/song.kar").mock(
        return_value=httpx.Response(200, headers={"content-length": "13"}, content=b"MThd")
    )

    with pytest.raises(DownloadError, match="larger than 12 bytes"):
        download_candidate(ranked_candidate(), search_intent(), tmp_path, make_settings(), [])


@respx.mock
def test_download_candidate_rejects_oversized_streamed_body(tmp_path: Path) -> None:
    respx.get("https://example.com/files/song.kar").mock(return_value=httpx.Response(200, content=b"MThd" + b"x" * 9))

    with pytest.raises(DownloadError, match="larger than 12 bytes"):
        download_candidate(ranked_candidate(), search_intent(), tmp_path, make_settings(), [])


@respx.mock
def test_download_candidate_rejects_text_response(tmp_path: Path) -> None:
    respx.get("https://example.com/files/song.kar").mock(
        return_value=httpx.Response(200, headers={"content-type": "text/plain"}, content=b"MThd")
    )

    with pytest.raises(DownloadError, match="text/HTML"):
        download_candidate(ranked_candidate(), search_intent(), tmp_path, make_settings(), [])


@respx.mock
def test_download_candidate_rejects_existing_target_file(tmp_path: Path) -> None:
    data = b"MThdabc"
    digest = hashlib.sha256(data).hexdigest()
    (tmp_path / f"Queen - Test Song-{digest[:8]}.kar").write_bytes(b"existing")
    respx.get("https://example.com/files/song.kar").mock(return_value=httpx.Response(200, content=data))

    with pytest.raises(DownloadError, match="Target file already exists"):
        download_candidate(ranked_candidate(), search_intent(), tmp_path, make_settings(), [])


@respx.mock
def test_download_candidate_rejects_duplicate_hash(tmp_path: Path) -> None:
    data = b"MThdabc"
    digest = hashlib.sha256(data).hexdigest()
    known = [
        DownloadedFile(
            url="https://example.com/other.kar",
            filename="other.kar",
            path=str(tmp_path / "other.kar"),
            sha256=digest,
            size_bytes=len(data),
            reason="existing",
        )
    ]
    respx.get("https://example.com/files/song.kar").mock(return_value=httpx.Response(200, content=data))

    with pytest.raises(DownloadError, match="File content already exists"):
        download_candidate(ranked_candidate(), search_intent(), tmp_path, make_settings(), known)


def test_existing_manifest_entries_reports_corrupt_manifest(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{bad json", encoding="utf-8")
    errors: list[str] = []

    assert existing_manifest_entries(manifest_path, errors) == []
    assert "Could not read existing manifest" in errors[0]


def test_write_manifest_preserves_corrupt_manifest_and_writes_atomically(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{bad json", encoding="utf-8")
    item = DownloadedFile(
        url="https://example.com/song.kar",
        filename="song.kar",
        path=str(tmp_path / "song.kar"),
        sha256="abc123",
        size_bytes=6,
        reason="matches",
    )
    errors: list[str] = []

    write_manifest(manifest_path, "request", [item], errors)

    assert (tmp_path / "manifest.json.corrupt").read_text(encoding="utf-8") == "{bad json"
    assert not (tmp_path / ".manifest.json.tmp").exists()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["files"][0]["url"] == "https://example.com/song.kar"
    assert "Could not read existing manifest" in payload["errors"][0]
