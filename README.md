# kar-scraper

`kar-scraper` is a conservative CLI agent for finding public direct `.kar`
karaoke files from a natural-language request and saving verified downloads to a
folder.

It uses LangGraph for the workflow, Firecrawl for search/scrape, and OpenAI's
Responses API for request interpretation and candidate ranking. It does not
bypass logins, paywalls, anti-bot controls, or download anything except direct
public `.kar` URLs.

## Setup

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

## Usage

```powershell
kar-scraper "Bohemian Rhapsody Queen karaoke .kar" --dry-run
kar-scraper "80s rock karaoke by Queen" --limit 5 --out downloads
kar-scraper "ABBA Dancing Queen" --allowed-domain example.com
```

Default output is `./downloads`; successful downloads are recorded in
`./downloads/manifest.json`.

## Safety Model

- Only direct URLs whose path ends in `.kar` are considered.
- Downloaded content must begin with the MIDI/KAR `MThd` header.
- Existing files are not overwritten.
- Files are deduplicated by URL and SHA256.
- Optional domain allowlists can be set with `ALLOWED_DOMAINS` or
  `--allowed-domain`.

