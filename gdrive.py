#!/usr/bin/env python3
"""Google Drive link resolution and downloading, standard library only.

Blog posts usually link a *page* on Drive, not a file. This module turns those
links into real downloads:

    https://drive.google.com/file/d/<id>/view      -> the actual PDF/RAR/BRD/...
    https://drive.google.com/open?id=<id>          -> same
    https://drive.google.com/drive/folders/<id>    -> every file in the folder
    https://docs.google.com/document/d/<id>/edit   -> exported as PDF/XLSX/PPTX

It handles the parts that make naive Drive downloads fail: the virus-scan
confirmation interstitial on large files, cookies, filenames that only exist in
the Content-Disposition header, and the HTML error pages Drive returns with a
200 status when a file is rate-limited or private.
"""

from __future__ import annotations

import http.cookiejar
import re
import urllib.parse
import urllib.request
from html.parser import HTMLParser

# Endpoints are module constants so tests can point them at a local server.
DOWNLOAD_ENDPOINT = "https://drive.usercontent.google.com/download"
DOCS_EXPORT = "https://docs.google.com/{kind}/d/{file_id}/export"
FOLDER_PAGE = "https://drive.google.com/drive/folders/{file_id}"

ID = r"[A-Za-z0-9_-]{10,}"

_FILE_PATTERNS = (
    re.compile(rf"drive\.google\.com/file/d/({ID})"),
    re.compile(rf"drive\.google\.com/(?:uc|open)\?(?:[^#]*&)?id=({ID})"),
    re.compile(rf"drive\.usercontent\.google\.com/download\?(?:[^#]*&)?id=({ID})"),
    re.compile(rf"drive\.google\.com/thumbnail\?(?:[^#]*&)?id=({ID})"),
)
_FOLDER_PATTERN = re.compile(rf"drive\.google\.com/drive/(?:u/\d+/)?folders/({ID})")
_DOC_PATTERN = re.compile(
    rf"docs\.google\.com/(document|spreadsheets|presentation)/d/({ID})"
)

# Native Google formats have no "original file" -- pick a sensible export.
DOC_EXPORT_FORMATS = {
    "document": ("pdf", "pdf"),
    "spreadsheets": ("xlsx", "xlsx"),
    "presentation": ("pptx", "pptx"),
}

# Drive answers these with HTTP 200 and an HTML body, so the status code lies.
_ERROR_SIGNATURES = (
    ("too many users have viewed or downloaded", "rate-limited by Drive (quota exceeded)"),
    ("quota for this file has been exceeded", "rate-limited by Drive (quota exceeded)"),
    ("you can't view or download this file at this time", "temporarily blocked by Drive"),
    ("request access", "private file (needs permission)"),
    ("you need access", "private file (needs permission)"),
    ("sign in to continue", "private file (login required)"),
    ("file has been deleted", "file no longer exists"),
    ("no longer exists", "file no longer exists"),
)


class DriveError(Exception):
    """A Drive link that cannot be downloaded, with a human-readable reason."""


class DriveTarget:
    """One downloadable thing on Drive."""

    __slots__ = ("file_id", "kind", "name", "source")

    def __init__(self, file_id: str, kind: str = "file", name: str | None = None,
                 source: str | None = None) -> None:
        self.file_id = file_id
        self.kind = kind          # "file" | "document" | "spreadsheets" | "presentation"
        self.name = name          # known only when listed from a folder
        self.source = source or file_id

    @property
    def url(self) -> str:
        return f"https://drive.google.com/file/d/{self.file_id}/view"

    def __repr__(self) -> str:
        return f"DriveTarget({self.file_id!r}, kind={self.kind!r}, name={self.name!r})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, DriveTarget) and other.file_id == self.file_id

    def __hash__(self) -> int:
        return hash(self.file_id)


def is_drive_url(url: str) -> bool:
    host = urllib.parse.urlsplit(url).netloc.lower()
    if not (host.endswith("google.com") or host.endswith("googleusercontent.com")):
        return False
    return bool(parse_target(url) or _FOLDER_PATTERN.search(url))


def parse_target(url: str) -> DriveTarget | None:
    """Return the single file/doc a Drive URL points at, if it is one."""
    match = _DOC_PATTERN.search(url)
    if match:
        return DriveTarget(match.group(2), kind=match.group(1), source=url)
    for pattern in _FILE_PATTERNS:
        match = pattern.search(url)
        if match:
            return DriveTarget(match.group(1), kind="file", source=url)
    return None


def parse_folder_id(url: str) -> str | None:
    match = _FOLDER_PATTERN.search(url)
    return match.group(1) if match else None


class _FormParser(HTMLParser):
    """Pull the action and hidden fields out of Drive's confirmation form."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.action: str | None = None
        self.fields: dict[str, str] = {}
        self._in_form = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {k: (v or "") for k, v in attrs}
        if tag == "form" and self.action is None:
            self.action = attributes.get("action")
            self._in_form = True
        elif tag == "input" and self._in_form:
            name = attributes.get("name")
            if name:
                self.fields[name] = attributes.get("value", "")

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self._in_form = False


def _filename_from_disposition(header: str | None) -> str | None:
    if not header:
        return None
    # RFC 5987: filename*=UTF-8''name.pdf takes precedence over filename="name.pdf"
    extended = re.search(r"filename\*\s*=\s*[^']*''([^;]+)", header, re.I)
    if extended:
        return urllib.parse.unquote(extended.group(1).strip().strip('"'))
    plain = re.search(r'filename\s*=\s*"([^"]+)"', header, re.I)
    if plain:
        return plain.group(1)
    bare = re.search(r"filename\s*=\s*([^;]+)", header, re.I)
    return bare.group(1).strip() if bare else None


def _describe_html_error(body: str) -> str:
    lowered = body.lower()
    for needle, reason in _ERROR_SIGNATURES:
        if needle in lowered:
            return reason
    title = re.search(r"<title>(.*?)</title>", body, re.I | re.S)
    if title:
        return f"Drive returned a page, not a file ({title.group(1).strip()[:80]})"
    return "Drive returned a page, not a file"


class DriveClient:
    """Resolves and downloads Drive links. Safe to share across threads."""

    def __init__(self, user_agent: str, timeout: float = 30.0,
                 folder_depth: int = 2) -> None:
        self.user_agent = user_agent
        self.timeout = timeout
        self.folder_depth = folder_depth
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )

    # -- HTTP -------------------------------------------------------------
    def _get(self, url: str):
        request = urllib.request.Request(
            url, headers={"User-Agent": self.user_agent, "Accept": "*/*"}
        )
        return self.opener.open(request, timeout=self.timeout)

    # -- resolution -------------------------------------------------------
    def expand(self, url: str) -> list[DriveTarget]:
        """Turn any Drive URL into the list of files it represents."""
        folder_id = parse_folder_id(url)
        if folder_id:
            return self.list_folder(folder_id)
        target = parse_target(url)
        return [target] if target else []

    def list_folder(self, folder_id: str, _depth: int = 0) -> list[DriveTarget]:
        """Best-effort folder listing by scraping the folder page.

        Drive has no unauthenticated listing API, so this reads the JSON blob
        embedded in the public folder page. It works today but is inherently
        brittle -- if Drive changes that page, this returns nothing rather than
        wrong results.
        """
        url = FOLDER_PAGE.format(file_id=folder_id) + "?hl=en"
        try:
            with self._get(url) as response:
                body = response.read().decode("utf-8", "replace")
        except Exception as exc:
            raise DriveError(f"could not open folder {folder_id}: {exc}") from exc

        targets: list[DriveTarget] = []
        seen: set[str] = set()
        # Entries look like: ["<id>",["<parent>"],"<name>","<mimetype>",...
        entry = re.compile(
            rf'\["({ID})",\["{ID}"\],"((?:[^"\\]|\\.)*)","([a-zA-Z0-9.+/-]+)"'
        )
        for file_id, raw_name, mime in entry.findall(body):
            if file_id in seen or file_id == folder_id:
                continue
            seen.add(file_id)
            name = raw_name.encode().decode("unicode_escape", "replace")
            if mime == "application/vnd.google-apps.folder":
                if _depth < self.folder_depth:
                    targets.extend(self.list_folder(file_id, _depth + 1))
                continue
            kind = "file"
            for doc_kind in DOC_EXPORT_FORMATS:
                if mime == f"application/vnd.google-apps.{doc_kind.rstrip('s')}":
                    kind = doc_kind
            targets.append(DriveTarget(file_id, kind=kind, name=name, source=url))
        return targets

    # -- downloading ------------------------------------------------------
    def open_download(self, target: DriveTarget):
        """Return (response, suggested_filename) ready to be streamed to disk.

        Raises DriveError when Drive serves an error page instead of the file.
        """
        if target.kind in DOC_EXPORT_FORMATS:
            fmt, extension = DOC_EXPORT_FORMATS[target.kind]
            url = (DOCS_EXPORT.format(kind=target.kind, file_id=target.file_id)
                   + "?" + urllib.parse.urlencode({"format": fmt}))
            response = self._get(url)
            name = (_filename_from_disposition(response.headers.get("Content-Disposition"))
                    or f"{target.name or target.file_id}.{extension}")
            return response, name

        url = DOWNLOAD_ENDPOINT + "?" + urllib.parse.urlencode(
            {"id": target.file_id, "export": "download", "confirm": "t"}
        )
        response = self._get(url)

        if self._looks_like_html(response):
            body = response.read(300_000).decode("utf-8", "replace")
            response.close()
            follow_up = self._confirmation_url(body, target.file_id)
            if not follow_up:
                raise DriveError(_describe_html_error(body))
            response = self._get(follow_up)
            if self._looks_like_html(response):
                body = response.read(300_000).decode("utf-8", "replace")
                response.close()
                raise DriveError(_describe_html_error(body))

        name = (_filename_from_disposition(response.headers.get("Content-Disposition"))
                or target.name or target.file_id)
        return response, name

    @staticmethod
    def _looks_like_html(response) -> bool:
        return "text/html" in (response.headers.get("Content-Type") or "").lower()

    @staticmethod
    def _confirmation_url(body: str, file_id: str) -> str | None:
        """Build the follow-up URL from Drive's virus-scan warning page."""
        parser = _FormParser()
        try:
            parser.feed(body)
        except Exception:
            pass
        if parser.action and parser.fields:
            query = {k: v for k, v in parser.fields.items() if v != ""}
            query.setdefault("id", file_id)
            query.setdefault("export", "download")
            return parser.action + "?" + urllib.parse.urlencode(query)

        # Older interstitial: a plain confirm link in the body.
        token = re.search(r"[?&]confirm=([0-9A-Za-z_-]+)", body)
        if token:
            return DOWNLOAD_ENDPOINT + "?" + urllib.parse.urlencode(
                {"id": file_id, "export": "download", "confirm": token.group(1)}
            )
        return None
