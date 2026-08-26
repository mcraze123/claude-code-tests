# claude-code-tests

## scrape_files.py

Downloads every file linked from a web page. Python 3.8+, standard library only —
no `pip install` needed.

```bash
python3 scrape_files.py https://schematic-x.blogspot.com/2018/04/blog-post.html -o downloads
```

### What it collects

Every URL-bearing attribute on the page — `href`, `src`, `data-src`, `srcset`,
`poster`, `data` — filtered down to things that look like files. By default that's
images, documents, archives, audio, video and common data/EDA formats. Page links,
`javascript:`, `mailto:` and anchors are ignored.

### Blogspot / Blogger notes

Blogger serves images through resize URLs like `.../s320/diagram.jpg`. The scraper
rewrites those to `/s0/`, which returns the **full-resolution original** rather than
the thumbnail the page displays. Use `--no-full-size` to keep the displayed version.

Blogspot posts often link attachments to third-party hosts (Google Drive, MediaFire,
etc.). Those are interstitial pages, not direct file URLs, so they're not downloaded —
they need per-host handling this tool doesn't do.

### Options

| Flag | Purpose |
| --- | --- |
| `-o, --output DIR` | Output directory (default `downloads`) |
| `--types LIST` | Extensions or groups: `image,document,archive,audio,video,data`, or `pdf,rar` |
| `--all` | Take every linked resource, not just known file types |
| `--dry-run` | List what would be downloaded, fetch nothing |
| `--max-depth N` | Also follow same-host page links N levels deep (default 0) |
| `--workers N` | Parallel downloads (default 4) |
| `--delay S` | Minimum seconds between requests (default 0.5) |
| `--retries N` | Retries on network/5xx/429 errors, exponential backoff (default 3) |
| `--manifest FILE` | Write a JSON report of every URL and its outcome |
| `--overwrite` | Re-download files that already exist (default: skip) |
| `--max-bytes N` | Skip files larger than N bytes |
| `--ignore-robots` | Skip the robots.txt check |
| `--user-agent UA` | Override the User-Agent |

### Behavior worth knowing

- **Resumable** — re-running skips files already on disk, so an interrupted run
  picks up where it stopped.
- **Polite by default** — obeys `robots.txt`, rate-limits with `--delay`, retries
  with exponential backoff.
- **Safe filenames** — URL-decoded, sanitized, deduplicated (`name-1.jpg`); path
  traversal in URLs can't escape the output directory.
- **Atomic writes** — downloads land in `.part` and are renamed on completion, so a
  failed transfer never leaves a truncated file behind.
- Exit code `0` all good, `1` nothing found, `2` some downloads failed.

### Tests

```bash
python3 test_scrape_files.py
```

16 tests covering URL normalization, Blogger upgrading, filename sanitizing and a
full end-to-end scrape against a fixture site served on localhost. No network access
required.
