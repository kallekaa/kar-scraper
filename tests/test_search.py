from kar_scraper.models import PageContent
from kar_scraper.search import build_queries, extract_kar_candidates, is_direct_kar_url, normalize_candidate_url


def test_build_queries_prioritizes_artist_and_song() -> None:
    queries = build_queries("queen bohemian rhapsody", "Queen", "Bohemian Rhapsody", None, None)
    assert queries[0] == '"Queen" "Bohemian Rhapsody" filetype:kar'
    assert any("karaoke .kar" in query for query in queries)


def test_build_queries_prioritizes_planned_queries() -> None:
    queries = build_queries(
        "queen bohemian rhapsody",
        "Queen",
        "Bohemian Rhapsody",
        None,
        None,
        ["custom planned query", '"Queen" "Bohemian Rhapsody" filetype:kar'],
    )
    assert queries[:2] == ["custom planned query", '"Queen" "Bohemian Rhapsody" filetype:kar']
    assert queries.count('"Queen" "Bohemian Rhapsody" filetype:kar') == 1


def test_extract_kar_candidates_from_html_and_markdown() -> None:
    page = PageContent(
        source_url="https://example.com/karaoke/index.html",
        html='<a href="/files/song.kar">Song</a><a href="/files/readme.txt">Readme</a>',
        markdown="[Other](https://cdn.example.com/other.kar)",
    )
    candidates = extract_kar_candidates(page)
    assert [candidate.url for candidate in candidates] == [
        "https://example.com/files/song.kar",
        "https://cdn.example.com/other.kar",
    ]


def test_extract_kar_candidates_respects_allowed_domains() -> None:
    page = PageContent(
        source_url="https://example.com/index.html",
        html='<a href="https://example.com/a.kar">A</a><a href="https://other.test/b.kar">B</a>',
    )
    candidates = extract_kar_candidates(page, ["example.com"])
    assert [candidate.url for candidate in candidates] == ["https://example.com/a.kar"]


def test_direct_kar_url_requires_path_suffix() -> None:
    assert is_direct_kar_url("https://example.com/file.kar?download=1")
    assert not is_direct_kar_url("https://example.com/download?id=file.kar")
    assert not is_direct_kar_url("https:///file.kar")


def test_extract_kar_candidates_dedupes_fragment_variants() -> None:
    page = PageContent(
        source_url="https://example.com/index.html",
        html='<a href="/song.kar#first">A</a><a href="/song.kar#second">B</a>',
    )
    candidates = extract_kar_candidates(page)
    assert [candidate.url for candidate in candidates] == ["https://example.com/song.kar"]


def test_normalize_candidate_url_drops_fragments() -> None:
    assert normalize_candidate_url("https://example.com/song.kar#download") == "https://example.com/song.kar"
