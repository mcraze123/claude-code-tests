# claude-code-tests

## scrape_files.py

Downloads every file linked from a web page, **including files parked on Google
Drive**. Python 3.8+, standard library only — no `pip install` needed.

Files: `scrape_files.py` (the scraper) and `gdrive.py` (Drive support). Keep them
in the same directory; without `gdrive.py` the scraper still runs and just skips
Drive links.

```bash
python3 scrape_files.py https://schematic-x.blogspot.com/2018/04/blog-post.html -o downloads
```

### What it collects

Every URL-bearing attribute on the page — `href`, `src`, `data-src`, `srcset`,
`poster`, `data` — filtered down to things that look like files. By default that's
images, documents, archives, audio, video, and the `eda` group — schematic, PCB and
CAD formats (`brd`, `sch`, `fz`, `cad`, `dsn`, `lay`, `kicad_pcb`, boardview types
and friends). Page links,
`javascript:`, `mailto:` and anchors are ignored.

### Blogspot / Blogger notes

Blogger serves images through resize URLs like `.../s320/diagram.jpg`. The scraper
rewrites those to `/s0/`, which returns the **full-resolution original** rather than
the thumbnail the page displays. Use `--no-full-size` to keep the displayed version.

### Google Drive links

Schematic blogs usually don't host the schematic — they link a Drive *page* that
stands in for the file. The scraper follows those and downloads the real bytes:

| Link on the page | What gets downloaded |
| --- | --- |
| `drive.google.com/file/d/<id>/view` | the actual PDF / RAR / ZIP / BRD / FZ / CAD file |
| `drive.google.com/open?id=<id>` | same |
| `drive.google.com/drive/folders/<id>` | every file in the folder, and its subfolders |
| `docs.google.com/document/d/<id>/edit` | exported as PDF (Sheets → XLSX, Slides → PPTX) |

It handles the parts that break naive Drive downloads:

- **The virus-scan interstitial.** Files over ~100 MB return a warning page instead
  of the file; the scraper submits the confirmation form and gets the real download.
- **Real filenames.** A Drive file's name exists only in the `Content-Disposition`
  header, not the URL, so files land as `service_manual.rar`, not `1a2b3c4d`.
- **Error pages that return HTTP 200.** When a file is rate-limited ("too many users
  have viewed or downloaded this file") or private, Drive serves *HTML with a success
  status*. Downloading blindly gives you an HTML page named `.pdf`. The scraper
  detects these, refuses to save them, and reports the reason.
- **Type filtering after resolution.** A Drive URL doesn't reveal its file type, so
  `--types` is applied to the resolved filename rather than the link.

Rate-limited files are worth retrying later — that quota is per-file and resets.

`--no-drive` turns all of this off. `--drive-folder-depth N` bounds subfolder
recursion (default 2).

**Folder listing is best-effort.** Drive has no unauthenticated listing API, so
folder contents are read from the JSON embedded in the public folder page. That
works today but will break if Google changes the page; it returns nothing rather
than wrong results. Individual file links don't depend on this.

Other hosts (MediaFire, Mega, etc.) are still not followed — each needs its own
handling.

### Options

| Flag | Purpose |
| --- | --- |
| `-o, --output DIR` | Output directory (default `downloads`) |
| `--types LIST` | Extensions or groups: `image,document,archive,audio,video,data,eda`, or `pdf,rar` |
| `--all` | Take every linked resource, not just known file types |
| `--dry-run` | List what would be downloaded, fetch nothing |
| `--max-depth N` | Also follow same-host page links N levels deep (default 0) |
| `--workers N` | Parallel downloads (default 4) |
| `--delay S` | Minimum seconds between requests (default 0.5) |
| `--retries N` | Retries on network/5xx/429 errors, exponential backoff (default 3) |
| `--manifest FILE` | Write a JSON report of every URL and its outcome |
| `--overwrite` | Re-download files that already exist (default: skip) |
| `--max-bytes N` | Skip files larger than N bytes |
| `--no-drive` | Don't follow Google Drive links |
| `--drive-folder-depth N` | How deep to recurse into Drive subfolders (default 2) |
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
python3 test_scrape_files.py   # 25 tests: scraping, plus the Drive integration
python3 test_gdrive.py         # 18 tests: Drive URL parsing and downloading
```

43 tests, no network access required. `mock_drive.py` stands in for Drive's public
endpoints and reproduces its awkward behavior — the virus-scan interstitial,
`Content-Disposition` filenames, folder pages, and error pages served with HTTP 200
— so the Drive path is exercised end to end against a fixture blog post.
