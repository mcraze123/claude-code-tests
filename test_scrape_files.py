#!/usr/bin/env python3
"""Tests for scrape_files.py -- self-contained, serves a fixture on localhost.

    python3 test_scrape_files.py
"""

import http.server
import os
import shutil
import tempfile
import threading
import unittest
import urllib.request

import gdrive
import mock_drive
import scrape_files
from scrape_files import (
    LinkParser,
    filename_for,
    main,
    normalize,
    upgrade_blogger_image,
    url_extension,
)

FIXTURE_PAGE = """<html><head><link rel="stylesheet" href="/style.css"></head><body>
<img src="files/a.jpg">
<img data-src="/files/b.png" src="">
<img srcset="files/a.jpg 320w, /files/c%20d.jpg 640w">
<a href="files/doc.pdf">manual</a>
<a href="/files/pack.rar">archive</a>
<a href="files/doc.pdf">same manual again</a>
<a href="subpage.html">related</a>
<a href="https://drive.google.com/file/d/X/view">external</a>
<a href="javascript:void(0)">js</a><a href="mailto:a@b.c">mail</a><a href="#top">anchor</a>
</body></html>"""

SUBPAGE = '<html><body><a href="files/extra.pdf">extra</a></body></html>'

# A post shaped like the real thing: local images, schematics parked on Drive.
DRIVE_PAGE = """<html><body>
<img src="files/a.jpg">
<a href="https://drive.google.com/file/d/SMALLFILEID00000000001/view?usp=sharing">Manual</a>
<a href="https://drive.google.com/open?id=BIGFILEID000000000000002">Boardview pack</a>
<a href="https://drive.google.com/file/d/BRDFILEID000000000000003/view">PCB file</a>
<a href="https://drive.google.com/file/d/QUOTAFILEID00000000000005/view">Rate-limited</a>
<a href="https://drive.google.com/file/d/PRIVATEFILEID000000000006/view">Private</a>
<a href="files/doc.pdf">local pdf</a>
</body></html>"""

DRIVE_FOLDER_PAGE = """<html><body>
<a href="https://drive.google.com/drive/folders/FOLDERID0000000000000001">Whole folder</a>
</body></html>"""


class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):  # keep test output clean
        pass


class FixtureServer:
    def __init__(self):
        self.root = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, "files"))
        for name, size in [("a.jpg", 500), ("b.png", 400), ("c d.jpg", 300),
                           ("doc.pdf", 900), ("pack.rar", 700), ("extra.pdf", 250)]:
            with open(os.path.join(self.root, "files", name), "wb") as fh:
                fh.write(os.urandom(size))
        for name, body in [("index.html", FIXTURE_PAGE), ("subpage.html", SUBPAGE),
                           ("drive.html", DRIVE_PAGE), ("folder.html", DRIVE_FOLDER_PAGE),
                           ("style.css", "body{}"), ("robots.txt", "User-agent: *\nDisallow: /private/\n")]:
            with open(os.path.join(self.root, name), "w") as fh:
                fh.write(body)

        directory = self.root
        handler = lambda *a, **kw: Handler(*a, directory=directory, **kw)
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}/index.html"

    def page(self, name):
        return f"http://127.0.0.1:{self.port}/{name}"

    def stop(self):
        self.httpd.shutdown()
        shutil.rmtree(self.root, ignore_errors=True)


class TestUrlHelpers(unittest.TestCase):
    def test_blogger_thumbnails_upgrade_to_full_size(self):
        for src, want in [
            ("https://1.bp.blogspot.com/-A/X/AA/d/s320/diagram.jpg",
             "https://1.bp.blogspot.com/-A/X/AA/d/s0/diagram.jpg"),
            ("https://4.bp.blogspot.com/-a/b/c/d/s1600/board.png",
             "https://4.bp.blogspot.com/-a/b/c/d/s0/board.png"),
            ("https://blogger.googleusercontent.com/img/b/R29vZ2xl/AVvXsEg/s640/pinout.jpg",
             "https://blogger.googleusercontent.com/img/b/R29vZ2xl/AVvXsEg/s0/pinout.jpg"),
            ("https://1.bp.blogspot.com/-a/b/c/w400-h300/thumb.jpg",
             "https://1.bp.blogspot.com/-a/b/c/s0/thumb.jpg"),
            ("https://1.bp.blogspot.com/-a/b/c/s320-c/crop.jpg",
             "https://1.bp.blogspot.com/-a/b/c/s0/crop.jpg"),
            ("https://lh3.googleusercontent.com/abc=s220",
             "https://lh3.googleusercontent.com/abc=s0"),
        ]:
            self.assertEqual(upgrade_blogger_image(src), want)

    def test_non_blogger_urls_are_untouched(self):
        url = "https://example.com/images/s320/photo.jpg"
        self.assertEqual(upgrade_blogger_image(url), url)

    def test_normalize(self):
        self.assertEqual(normalize("http://a.com/p/x.html", "../f/y.pdf"), "http://a.com/f/y.pdf")
        self.assertEqual(normalize("http://a.com/p", "//cdn.com/a.jpg"), "http://cdn.com/a.jpg")
        self.assertEqual(normalize("http://a.com/p", "http://a.com/x.jpg#f"), "http://a.com/x.jpg")
        for bad in ("javascript:x", "mailto:a@b.c", "#top", "data:image/png;base64,AA", ""):
            self.assertIsNone(normalize("http://a.com/", bad))

    def test_url_extension(self):
        self.assertEqual(url_extension("http://a.com/f.PDF?x=1"), "pdf")
        self.assertEqual(url_extension("http://a.com/page"), "")

    def test_filename_sanitizing(self):
        self.assertEqual(filename_for("http://a.com/a%20b.jpg", None), "a_b.jpg")
        self.assertEqual(filename_for("http://a.com/../../etc/passwd", None), "passwd")
        self.assertTrue(filename_for("http://a.com/get?id=9", "application/pdf").endswith(".pdf"))

    def test_srcset_parsing(self):
        parser = LinkParser()
        parser.feed('<img srcset="one.jpg 320w, two.jpg 640w">')
        self.assertEqual([u for _, u in parser.found], ["one.jpg", "two.jpg"])


class TestEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = FixtureServer()

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def setUp(self):
        self.out = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.out, ignore_errors=True)

    def scrape(self, *extra):
        return main([self.server.url, "-o", self.out, "--delay", "0", *extra])

    def downloaded(self):
        return sorted(os.listdir(self.out))

    def test_downloads_every_linked_file(self):
        self.assertEqual(self.scrape(), 0)
        self.assertEqual(self.downloaded(),
                         ["a.jpg", "b.png", "c_d.jpg", "doc.pdf", "pack.rar"])

    def test_duplicate_links_download_once(self):
        self.scrape()
        self.assertEqual([n for n in self.downloaded() if n.startswith("doc")], ["doc.pdf"])

    def test_second_run_skips_existing(self):
        self.scrape()
        before = {n: os.path.getmtime(os.path.join(self.out, n)) for n in self.downloaded()}
        self.scrape()
        after = {n: os.path.getmtime(os.path.join(self.out, n)) for n in self.downloaded()}
        self.assertEqual(before, after)

    def test_type_filter(self):
        self.scrape("--types", "pdf,rar")
        self.assertEqual(self.downloaded(), ["doc.pdf", "pack.rar"])

    def test_type_group(self):
        self.scrape("--types", "image")
        self.assertEqual(self.downloaded(), ["a.jpg", "b.png", "c_d.jpg"])

    def test_crawl_depth_follows_same_host_pages(self):
        self.scrape("--max-depth", "1")
        self.assertIn("extra.pdf", self.downloaded())

    def test_depth_zero_stays_on_page(self):
        self.scrape()
        self.assertNotIn("extra.pdf", self.downloaded())

    def test_dry_run_writes_nothing(self):
        self.scrape("--dry-run")
        self.assertFalse(os.path.exists(self.out) and os.listdir(self.out))

    def test_manifest(self):
        import json
        path = os.path.join(self.out, "..", "manifest.json")
        self.scrape("--manifest", path)
        with open(path) as fh:
            data = json.load(fh)
        self.assertEqual(len(data["files"]), 5)
        self.assertTrue(all(f["status"] == "ok" for f in data["files"]))
        os.remove(path)

    def test_partial_file_removed_on_failure(self):
        self.scrape("--max-bytes", "600")
        self.assertFalse([n for n in self.downloaded() if n.endswith(".part")])


class TestDriveIntegration(unittest.TestCase):
    """A page whose files live on Google Drive -- the schematic-blog case."""

    @classmethod
    def setUpClass(cls):
        cls.server = FixtureServer()
        cls.drive, base = mock_drive.start()
        cls._saved = (gdrive.DOWNLOAD_ENDPOINT, gdrive.DOCS_EXPORT, gdrive.FOLDER_PAGE)
        gdrive.DOWNLOAD_ENDPOINT = base + "/download"
        gdrive.DOCS_EXPORT = base + "/{kind}/d/{file_id}/export"
        gdrive.FOLDER_PAGE = base + "/drive/folders/{file_id}"

    @classmethod
    def tearDownClass(cls):
        gdrive.DOWNLOAD_ENDPOINT, gdrive.DOCS_EXPORT, gdrive.FOLDER_PAGE = cls._saved
        cls.drive.shutdown()
        cls.server.stop()

    def setUp(self):
        self.out = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.out, ignore_errors=True)

    def scrape(self, page="drive.html", *extra):
        return main([self.server.page(page), "-o", self.out, "--delay", "0", *extra])

    def downloaded(self):
        return sorted(os.listdir(self.out))

    def test_drive_links_are_followed_and_downloaded(self):
        self.scrape()
        got = self.downloaded()
        self.assertIn("service_manual.pdf", got)   # plain Drive file
        self.assertIn("boardview_pack.rar", got)   # needed virus-scan confirmation
        self.assertIn("mainboard.brd", got)        # schematic format

    def test_drive_files_get_their_real_names_and_contents(self):
        self.scrape()
        with open(os.path.join(self.out, "boardview_pack.rar"), "rb") as fh:
            self.assertTrue(fh.read(4).startswith(b"Rar!"))

    def test_local_files_still_downloaded_alongside_drive(self):
        self.scrape()
        self.assertIn("doc.pdf", self.downloaded())
        self.assertIn("a.jpg", self.downloaded())

    def test_unavailable_drive_files_are_not_saved_as_html(self):
        self.scrape()
        for name in self.downloaded():
            path = os.path.join(self.out, name)
            with open(path, "rb") as fh:
                head = fh.read(200).lower()
            self.assertNotIn(b"<html", head, f"{name} is an HTML error page")

    def test_unavailable_drive_files_are_reported(self):
        import json
        manifest = os.path.join(self.out, "..", "m.json")
        self.scrape("drive.html", "--manifest", manifest)
        with open(manifest) as fh:
            records = json.load(fh)["files"]
        os.remove(manifest)
        unavailable = {r["error"] for r in records if r["status"] == "unavailable"}
        self.assertEqual(len(unavailable), 2)
        self.assertTrue(any("quota" in e.lower() for e in unavailable))
        self.assertTrue(any("permission" in e.lower() for e in unavailable))

    def test_drive_folder_expands_to_all_files(self):
        self.scrape("folder.html")
        got = self.downloaded()
        for name in ("service_manual.pdf", "boardview_pack.rar", "mainboard.brd",
                     "circuit.fz"):
            self.assertIn(name, got)

    def test_no_drive_flag_skips_them(self):
        self.scrape("drive.html", "--no-drive")
        self.assertNotIn("service_manual.pdf", self.downloaded())

    def test_type_filter_applies_to_resolved_drive_filenames(self):
        self.scrape("drive.html", "--types", "eda")
        got = self.downloaded()
        self.assertIn("mainboard.brd", got)
        self.assertNotIn("service_manual.pdf", got)

    def test_eda_group_covers_schematic_formats(self):
        for ext in ("brd", "sch", "fz", "cad", "dsn", "kicad_pcb"):
            self.assertIn(ext, scrape_files.EXTENSION_GROUPS["eda"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
