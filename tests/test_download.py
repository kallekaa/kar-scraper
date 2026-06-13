import hashlib
from pathlib import Path

import pytest

from kar_scraper.download import DownloadError, filename_for_candidate, sanitize_filename, validate_kar_bytes
from kar_scraper.models import RankedCandidate, SearchIntent


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

