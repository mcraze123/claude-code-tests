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


if __name__ == "__main__":
    unittest.main(verbosity=2)
