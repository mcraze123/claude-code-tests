#!/usr/bin/env python3
"""Download every file linked from a web page.

Standard library only -- no pip install required.

    python3 scrape_files.py https://example.com/post.html -o downloads

By default it grabs the page, collects every URL that looks like a file
(images, documents, archives, audio, video), and downloads them into an
output directory. Blogger/Blogspot thumbnails are automatically upgraded to
their full-resolution originals, which is usually what you actually want from
a Blogspot post.
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import json
import mimetypes
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from html.parser import HTMLParser

DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

EXTENSION_GROUPS = {
    "image": [
        "jpg", "jpeg", "png", "gif", "bmp", "webp", "svg", "tif", "tiff", "ico",
    ],
    "document": [
        "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "odt", "ods", "odp",
        "rtf", "txt", "csv", "epub", "djvu",
    ],
    "archive": ["zip", "rar", "7z", "tar", "gz", "bz2", "xz", "tgz"],
    "audio": ["mp3", "wav", "flac", "ogg", "m4a", "aac", "wma"],
    "video": ["mp4", "avi", "mkv", "mov", "wmv", "flv", "webm", "m4v", "3gp"],
    "data": ["json", "xml", "sql", "bin", "hex", "iso", "img", "sch", "brd", "dsn"],
}

# HTML attributes that can carry a URL, per tag.
URL_ATTRS = {
    "a": ("href",),
    "area": ("href",),
    "link": ("href",),
    "img": ("src", "data-src", "data-original", "data-lazy-src", "srcset"),
    "source": ("src", "srcset"),
    "video": ("src", "poster"),
    "audio": ("src",),
    "embed": ("src",),
    "iframe": ("src",),
    "object": ("data",),
    "script": ("src",),
}

SKIP_SCHEMES = ("javascript:", "mailto:", "tel:", "data:", "about:", "#")


class LinkParser(HTMLParser):
    """Collect (tag, url) pairs from every URL-bearing attribute."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        wanted = URL_ATTRS.get(tag)
        if not wanted:
            return
        for name, value in attrs:
            if name not in wanted or not value:
                continue
            if name == "srcset":
                # "url 1x, url 2x" / "url 320w, url 640w"
                for candidate in value.split(","):
                    part = candidate.strip().split(" ")[0]
                    if part:
                        self.found.append((tag, part))
            else:
                self.found.append((tag, value.strip()))


# --------------------------------------------------------------------------
# URL helpers
# --------------------------------------------------------------------------

# Blogger serves images as .../s320/name.jpg or .../w400-h300/name.jpg where the
# segment is a resize directive. /s0/ asks for the untouched original.
_BLOGGER_HOSTS = ("bp.blogspot.com", "blogger.googleusercontent.com",
                  "lh3.googleusercontent.com", "googleusercontent.com")
_BLOGGER_SIZE_SEGMENT = re.compile(
    r"/(?:s\d+|w\d+-h\d+|s\d+-c|w\d+|h\d+)(?:-[a-zA-Z0-9]+)*/"
)
_BLOGGER_SIZE_SUFFIX = re.compile(r"=(?:s\d+|w\d+-h\d+|w\d+|h\d+)(?:-[a-zA-Z0-9]+)*$")


def is_blogger_image(url: str) -> bool:
    host = urllib.parse.urlparse(url).netloc.lower()
    return any(host == h or host.endswith("." + h) or h in host for h in _BLOGGER_HOSTS)


def upgrade_blogger_image(url: str) -> str:
    """Rewrite a Blogger thumbnail URL to request the full-size original."""
    if not is_blogger_image(url):
        return url
    parts = urllib.parse.urlsplit(url)
    path = _BLOGGER_SIZE_SEGMENT.sub("/s0/", parts.path, count=1)
    path = _BLOGGER_SIZE_SUFFIX.sub("=s0", path)
    return urllib.parse.urlunsplit(parts._replace(path=path))


def normalize(base: str, url: str) -> str | None:
    url = url.strip()
    if not url or url.lower().startswith(SKIP_SCHEMES):
        return None
    absolute = urllib.parse.urljoin(base, url)
    parts = urllib.parse.urlsplit(absolute)
    if parts.scheme not in ("http", "https"):
        return None
    return urllib.parse.urlunsplit(parts._replace(fragment=""))


def url_extension(url: str) -> str:
    path = urllib.parse.urlsplit(url).path
    ext = os.path.splitext(path)[1].lstrip(".").lower()
    return ext if 1 <= len(ext) <= 5 and ext.isalnum() else ""


_UNSAFE = re.compile(r'[^A-Za-z0-9._-]+')


def filename_for(url: str, content_type: str | None) -> str:
    path = urllib.parse.urlsplit(url).path
    name = urllib.parse.unquote(os.path.basename(path))
    name = _UNSAFE.sub("_", name).strip("._") or "file"
    if not os.path.splitext(name)[1] and content_type:
        guessed = mimetypes.guess_extension(content_type.split(";")[0].strip())
        if guessed:
            name += guessed
    return name[:180]


# --------------------------------------------------------------------------
# Scraper
# --------------------------------------------------------------------------

class Scraper:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.extensions = self._resolve_extensions()
        self.out_dir = os.path.abspath(args.output)
        self.lock = threading.Lock()
        self.used_names: set[str] = set()
        self.seen_urls: set[str] = set()
        self.results: list[dict] = []
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._last_request = 0.0

    # -- setup ------------------------------------------------------------
    def _resolve_extensions(self) -> set[str] | None:
        if self.args.all:
            return None  # no filtering
        if self.args.types:
            wanted: set[str] = set()
            for item in self.args.types.split(","):
                item = item.strip().lower().lstrip(".")
                if not item:
                    continue
                if item in EXTENSION_GROUPS:
                    wanted.update(EXTENSION_GROUPS[item])
                else:
                    wanted.add(item)
            return wanted
        return {ext for group in EXTENSION_GROUPS.values() for ext in group}

    # -- network ----------------------------------------------------------
    def _throttle(self) -> None:
        if self.args.delay <= 0:
            return
        with self.lock:
            wait = self._last_request + self.args.delay - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()

    def _open(self, url: str, method: str = "GET"):
        request = urllib.request.Request(
            url,
            method=method,
            headers={
                "User-Agent": self.args.user_agent,
                "Accept": "*/*",
                "Referer": self.args.url,
            },
        )
        return urllib.request.urlopen(request, timeout=self.args.timeout)

    def _with_retries(self, url: str, action):
        delay = 1.0
        last_error: Exception | None = None
        for attempt in range(self.args.retries + 1):
            try:
                return action()
            except urllib.error.HTTPError as exc:
                last_error = exc
                # Only 429 and 5xx are worth retrying.
                if exc.code not in (408, 429) and exc.code < 500:
                    raise
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc
            if attempt < self.args.retries:
                time.sleep(delay)
                delay *= 2
        assert last_error is not None
        raise last_error

    def allowed_by_robots(self, url: str) -> bool:
        if self.args.ignore_robots:
            return True
        parts = urllib.parse.urlsplit(url)
        root = f"{parts.scheme}://{parts.netloc}"
        with self.lock:
            cached = self._robots.get(root, "miss")
        if cached == "miss":
            parser = urllib.robotparser.RobotFileParser()
            parser.set_url(root + "/robots.txt")
            try:
                with self._open(root + "/robots.txt") as response:
                    parser.parse(response.read().decode("utf-8", "replace").splitlines())
            except Exception:
                parser = None  # unreachable robots.txt -> assume allowed
            with self.lock:
                self._robots[root] = parser
            cached = parser
        if cached is None:
            return True
        return cached.can_fetch(self.args.user_agent, url)

    # -- page discovery ---------------------------------------------------
    def fetch_page(self, url: str) -> tuple[str, str] | None:
        def action():
            self._throttle()
            with self._open(url) as response:
                content_type = response.headers.get("Content-Type", "")
                if "html" not in content_type.lower():
                    return None
                charset = response.headers.get_content_charset() or "utf-8"
                body = response.read(self.args.max_page_bytes)
                return body.decode(charset, "replace"), response.geturl()

        try:
            return self._with_retries(url, action)
        except Exception as exc:
            print(f"[page] failed {url}: {exc}", file=sys.stderr)
            return None

    def collect(self, html_text: str, base_url: str) -> tuple[list[str], list[str]]:
        """Return (file urls, page urls) discovered on the page."""
        parser = LinkParser()
        parser.feed(html_text)

        files: list[str] = []
        pages: list[str] = []
        base_host = urllib.parse.urlsplit(base_url).netloc.lower()

        for tag, raw in parser.found:
            url = normalize(base_url, raw)
            if not url:
                continue
            if self.args.full_size:
                url = upgrade_blogger_image(url)
            ext = url_extension(url)
            embedded = tag in ("img", "source", "video", "audio", "embed", "object")

            if self.extensions is None:
                # --all: everything that isn't an obvious page link
                if ext not in ("html", "htm", "php", "asp", "aspx", "") or embedded:
                    files.append(url)
            elif ext in self.extensions:
                files.append(url)
            elif embedded and not ext:
                # Extension-less embedded resource (Blogger images often are).
                files.append(url)

            if tag == "a" and ext in ("", "html", "htm", "php"):
                if urllib.parse.urlsplit(url).netloc.lower() == base_host:
                    pages.append(url)

        return files, pages

    # -- downloading ------------------------------------------------------
    def _reserve_name(self, name: str) -> str:
        with self.lock:
            if name not in self.used_names:
                self.used_names.add(name)
                return name
            stem, ext = os.path.splitext(name)
            counter = 1
            while f"{stem}-{counter}{ext}" in self.used_names:
                counter += 1
            unique = f"{stem}-{counter}{ext}"
            self.used_names.add(unique)
            return unique

    def download(self, url: str) -> dict:
        record = {"url": url, "status": "ok", "path": None, "bytes": 0, "content_type": None}

        if not self.allowed_by_robots(url):
            record.update(status="blocked-by-robots")
            return record

        try:
            def action():
                self._throttle()
                return self._open(url)

            response = self._with_retries(url, action)
        except Exception as exc:
            record.update(status="error", error=str(exc))
            return record

        with response:
            content_type = response.headers.get("Content-Type")
            record["content_type"] = content_type
            length = response.headers.get("Content-Length")
            if length and self.args.max_bytes and int(length) > self.args.max_bytes:
                record.update(status="too-large", bytes=int(length))
                return record

            name = self._reserve_name(filename_for(response.geturl(), content_type))
            target = os.path.join(self.out_dir, name)

            if self.args.skip_existing and os.path.exists(target) and os.path.getsize(target) > 0:
                record.update(status="skipped-existing", path=target,
                              bytes=os.path.getsize(target))
                return record

            total = 0
            partial = target + ".part"
            try:
                with open(partial, "wb") as handle:
                    while True:
                        chunk = response.read(65536)
                        if not chunk:
                            break
                        total += len(chunk)
                        if self.args.max_bytes and total > self.args.max_bytes:
                            raise ValueError(f"exceeds --max-bytes ({self.args.max_bytes})")
                        handle.write(chunk)
                os.replace(partial, target)
            except Exception as exc:
                if os.path.exists(partial):
                    os.remove(partial)
                record.update(status="error", error=str(exc))
                return record

            record.update(path=target, bytes=total)
            return record

    # -- driver -----------------------------------------------------------
    def run(self) -> int:
        pending = [(self.args.url, 0)]
        visited_pages: set[str] = set()
        file_urls: list[str] = []

        while pending:
            page_url, depth = pending.pop(0)
            if page_url in visited_pages:
                continue
            visited_pages.add(page_url)

            if not self.allowed_by_robots(page_url):
                print(f"[page] robots.txt disallows {page_url}", file=sys.stderr)
                continue

            fetched = self.fetch_page(page_url)
            if not fetched:
                continue
            html_text, final_url = fetched
            found_files, found_pages = self.collect(html_text, final_url)

            new = 0
            for url in found_files:
                if url not in self.seen_urls:
                    self.seen_urls.add(url)
                    file_urls.append(url)
                    new += 1
            print(f"[page] {page_url} -> {new} new file(s)")

            if depth < self.args.max_depth:
                for url in found_pages:
                    if url not in visited_pages:
                        pending.append((url, depth + 1))

        if not file_urls:
            print("No matching files found.")
            return 1

        print(f"\nFound {len(file_urls)} file(s).")
        if self.args.dry_run:
            for url in file_urls:
                print("  " + url)
            return 0

        os.makedirs(self.out_dir, exist_ok=True)
        with futures.ThreadPoolExecutor(max_workers=self.args.workers) as pool:
            for record in pool.map(self.download, file_urls):
                self.results.append(record)
                mark = {"ok": "OK  ", "skipped-existing": "SKIP"}.get(record["status"], "FAIL")
                detail = record.get("path") or record.get("error") or record["status"]
                label = os.path.basename(str(detail))
                size = f"  ({record['bytes']:,} bytes)" if record["status"] == "ok" else ""
                print(f"  [{mark}] {label}{size}")

        ok = sum(1 for r in self.results if r["status"] == "ok")
        skipped = sum(1 for r in self.results if r["status"] == "skipped-existing")
        failed = len(self.results) - ok - skipped
        total_bytes = sum(r["bytes"] for r in self.results if r["status"] == "ok")
        print(f"\nDownloaded {ok}, skipped {skipped}, failed {failed} "
              f"({total_bytes:,} bytes) -> {self.out_dir}")

        if self.args.manifest:
            with open(self.args.manifest, "w", encoding="utf-8") as handle:
                json.dump({"source": self.args.url, "files": self.results}, handle, indent=2)
            print(f"Manifest written to {self.args.manifest}")

        return 0 if failed == 0 else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download all files linked from a web page.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  python3 scrape_files.py https://example.com/post.html
  python3 scrape_files.py URL -o out --types image,pdf
  python3 scrape_files.py URL --dry-run
  python3 scrape_files.py URL --max-depth 1 --delay 1
""",
    )
    parser.add_argument("url", help="page to scrape")
    parser.add_argument("-o", "--output", default="downloads", help="output directory")
    parser.add_argument("--types", help="comma-separated extensions or groups "
                                        f"({', '.join(EXTENSION_GROUPS)})")
    parser.add_argument("--all", action="store_true",
                        help="download every linked resource, not just known file types")
    parser.add_argument("--max-depth", type=int, default=0,
                        help="follow same-host page links this many levels deep (default 0)")
    parser.add_argument("--workers", type=int, default=4, help="parallel downloads")
    parser.add_argument("--delay", type=float, default=0.5,
                        help="minimum seconds between requests (politeness)")
    parser.add_argument("--timeout", type=float, default=30.0, help="per-request timeout")
    parser.add_argument("--retries", type=int, default=3, help="retries on network/5xx errors")
    parser.add_argument("--user-agent", default=DEFAULT_UA)
    parser.add_argument("--manifest", help="write a JSON report of what was downloaded")
    parser.add_argument("--dry-run", action="store_true", help="list URLs, download nothing")
    parser.add_argument("--ignore-robots", action="store_true",
                        help="do not consult robots.txt")
    parser.add_argument("--no-full-size", dest="full_size", action="store_false",
                        help="keep Blogger thumbnail URLs instead of upgrading to originals")
    parser.add_argument("--overwrite", dest="skip_existing", action="store_false",
                        help="re-download files that already exist")
    parser.add_argument("--max-bytes", type=int, default=0,
                        help="skip files larger than this (0 = no limit)")
    parser.add_argument("--max-page-bytes", type=int, default=10 * 1024 * 1024,
                        help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not urllib.parse.urlsplit(args.url).scheme:
        args.url = "https://" + args.url
    try:
        return Scraper(args).run()
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
