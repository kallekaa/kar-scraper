# kar-scraper

`kar-scraper` is a conservative CLI agent for finding public direct `.kar`
karaoke files from a natural-language request and saving verified downloads to a
local folder.

The app combines:

- LangGraph for the search/download workflow.
- Firecrawl for web search and page scraping.
- OpenAI Responses API for request interpretation and candidate ranking.
- `httpx` for streamed downloads with size, type, and content validation.

It is intentionally narrow: it only considers public HTTP(S) URLs whose final
path ends in `.kar`. It does not bypass logins, paywalls, anti-bot controls, or
download from non-direct file endpoints.

## How It Works

For each request, the agent:

1. Uses OpenAI to extract artist, song, genre, era, search intent, and planned
   search queries.
2. Adds fallback `.kar` search queries such as `filetype:kar` and `karaoke .kar`.
3. Searches the web with Firecrawl and scrapes result pages.
4. Extracts direct `.kar` links from page HTML and markdown.
5. Optionally filters candidates by allowed domain.
6. Uses OpenAI to rank candidates against the original request.
7. Downloads accepted candidates unless `--dry-run` is set.
8. Verifies each download and writes `manifest.json`.

## Setup

Requires Python 3.11 or newer.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env
```

Fill in `.env`:

```text
OPENAI_API_KEY=...
FIRECRAWL_API_KEY=...
```

## Configuration

Settings are loaded from `.env`.

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `OPENAI_API_KEY` | Yes | | API key used by the OpenAI planner/ranker. |
| `FIRECRAWL_API_KEY` | Yes | | API key used for search and scraping. |
| `OPENAI_MODEL` | No | `gpt-4.1-mini` | Model used with the Responses API. |
| `MAX_DOWNLOADS` | No | `10` | Upper bound for accepted downloads, from 1 to 50. |
| `REQUEST_TIMEOUT_SECONDS` | No | `30` | HTTP download timeout. |
| `ALLOWED_DOMAINS` | No | | Comma-separated domain allowlist for direct `.kar` URLs. |
| `MAX_FILE_SIZE_BYTES` | No | `2000000` | Maximum accepted download size in bytes. |

`ALLOWED_DOMAINS` accepts hostnames or URLs and normalizes them to hostnames.
For example, `https://Example.com/path, cdn.example.com.` becomes
`example.com, cdn.example.com`.

## Usage

Run one request:

```powershell
kar-scraper "Bohemian Rhapsody Queen karaoke .kar"
```

Preview ranked candidates without downloading:

```powershell
kar-scraper "Bohemian Rhapsody Queen karaoke .kar" --dry-run
```

Limit accepted candidates and choose the output folder:

```powershell
kar-scraper "80s rock karaoke by Queen" --limit 5 --out downloads
```

Restrict direct file links to one or more domains:

```powershell
kar-scraper "ABBA Dancing Queen" --allowed-domain example.com --allowed-domain cdn.example.com
```

Suppress verbose workflow progress:

```powershell
kar-scraper "ABBA Dancing Queen" --quiet
```

When no request is provided, the CLI prompts interactively and asks whether to
handle another request after each run:

```powershell
kar-scraper
```

Default output is `./downloads`. Successful downloads are recorded in
`./downloads/manifest.json`.

## Output

Downloaded filenames are based on the interpreted `artist - song` when both are
known; otherwise they fall back to the source URL filename. A short SHA256 prefix
is appended to avoid ambiguous names:

```text
Queen - Bohemian Rhapsody-a1b2c3d4.kar
```

The manifest stores:

- Original request.
- Generation timestamp.
- Downloaded file URL and source page.
- Local filename and path.
- SHA256 hash and byte size.
- Candidate ranking reason.
- Warnings collected during the run.

If an existing manifest is corrupt, it is preserved once as
`manifest.json.corrupt` before a replacement manifest is written.

## Safety Model

- Only HTTP(S) URLs with a path ending in `.kar` are candidates.
- Redirects must also end at a direct `.kar` URL.
- Downloads are rejected when the response looks like text or HTML.
- Downloaded content must begin with the MIDI/KAR `MThd` header.
- Files larger than the configured maximum are rejected while streaming.
- Existing output files are not overwritten.
- Previously downloaded URLs and SHA256 hashes are skipped.
- Optional domain allowlists can be set with `ALLOWED_DOMAINS` or
  `--allowed-domain`.

## Exit Codes

- `0`: request completed successfully.
- `1`: no direct candidates were found, or no files were downloaded.
- `2`: configuration, validation, or runtime setup failed.

## Development

Install development dependencies with:

```powershell
pip install -e ".[dev]"
```

Run tests:

```powershell
pytest
```

The test suite uses fake planners/searchers for the agent and `respx` for HTTP
download behavior, so most checks do not require live OpenAI or Firecrawl calls.
