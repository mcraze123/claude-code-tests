#!/usr/bin/env python3
"""Tests for gdrive.py, run against a local mock of Drive's endpoints."""

import unittest

import gdrive
import mock_drive

SMALL = "SMALLFILEID00000000001"
BIG = "BIGFILEID000000000000002"
FOLDER = "FOLDERID0000000000000001"


class DriveTestCase(unittest.TestCase):
    """Points gdrive's endpoints at the mock server for the duration."""

    @classmethod
    def setUpClass(cls):
        cls.httpd, base = mock_drive.start()
        cls._saved = (gdrive.DOWNLOAD_ENDPOINT, gdrive.DOCS_EXPORT, gdrive.FOLDER_PAGE)
        gdrive.DOWNLOAD_ENDPOINT = base + "/download"
        gdrive.DOCS_EXPORT = base + "/{kind}/d/{file_id}/export"
        gdrive.FOLDER_PAGE = base + "/drive/folders/{file_id}"
        cls.base = base

    @classmethod
    def tearDownClass(cls):
        gdrive.DOWNLOAD_ENDPOINT, gdrive.DOCS_EXPORT, gdrive.FOLDER_PAGE = cls._saved
        cls.httpd.shutdown()

    def setUp(self):
        self.client = gdrive.DriveClient("test-agent", timeout=10)


class TestUrlRecognition(unittest.TestCase):
    def test_recognizes_drive_link_shapes(self):
        for url in [
            "https://drive.google.com/file/d/SMALLFILEID00000000001/view?usp=sharing",
            "https://drive.google.com/file/d/SMALLFILEID00000000001/view?usp=drive_link",
            "https://drive.google.com/open?id=BIGFILEID000000000000002",
            "https://drive.google.com/uc?export=download&id=BIGFILEID000000000000002",
            "https://drive.google.com/drive/folders/FOLDERID0000000000000001",
            "https://drive.google.com/drive/u/0/folders/FOLDERID0000000000000001",
            "https://docs.google.com/document/d/DOCID000000000000001/edit",
            "https://docs.google.com/spreadsheets/d/DOCID000000000000001/edit#gid=0",
        ]:
            self.assertTrue(gdrive.is_drive_url(url), url)

    def test_ignores_non_drive_links(self):
        for url in [
            "https://example.com/x.pdf",
            "https://www.google.com/search?q=drive",
            "https://mediafire.com/file/abc/manual.rar",
            "https://sites.google.com/view/something",
        ]:
            self.assertFalse(gdrive.is_drive_url(url), url)

    def test_extracts_file_id(self):
        target = gdrive.parse_target(
            "https://drive.google.com/file/d/SMALLFILEID00000000001/view?usp=sharing")
        self.assertEqual(target.file_id, SMALL)
        self.assertEqual(target.kind, "file")

    def test_extracts_doc_kind(self):
        target = gdrive.parse_target(
            "https://docs.google.com/spreadsheets/d/ABCDEFGHIJKLMNOP/edit")
        self.assertEqual(target.kind, "spreadsheets")

    def test_folder_id(self):
        self.assertEqual(
            gdrive.parse_folder_id("https://drive.google.com/drive/folders/" + FOLDER),
            FOLDER)
        self.assertIsNone(gdrive.parse_folder_id("https://drive.google.com/file/d/X/view"))


class TestContentDisposition(unittest.TestCase):
    def test_quoted_filename(self):
        self.assertEqual(
            gdrive._filename_from_disposition('attachment; filename="a b.pdf"'), "a b.pdf")

    def test_rfc5987_takes_precedence(self):
        header = "attachment; filename=\"fallback.pdf\"; filename*=UTF-8''sch%C3%A9ma.pdf"
        self.assertEqual(gdrive._filename_from_disposition(header), "schéma.pdf")

    def test_unquoted(self):
        self.assertEqual(
            gdrive._filename_from_disposition("attachment; filename=plain.rar"), "plain.rar")

    def test_missing(self):
        self.assertIsNone(gdrive._filename_from_disposition(None))


class TestDownloading(DriveTestCase):
    def test_downloads_a_plain_file(self):
        target = gdrive.parse_target(f"https://drive.google.com/file/d/{SMALL}/view")
        response, name = self.client.open_download(target)
        with response:
            body = response.read()
        self.assertEqual(name, "service_manual.pdf")
        self.assertTrue(body.startswith(b"%PDF"))

    def test_clears_virus_scan_interstitial_on_large_file(self):
        target = gdrive.parse_target(f"https://drive.google.com/open?id={BIG}")
        response, name = self.client.open_download(target)
        with response:
            body = response.read()
        self.assertEqual(name, "boardview_pack.rar")
        self.assertTrue(body.startswith(b"Rar!"))

    def test_quota_error_page_raises_not_saves(self):
        with self.assertRaises(gdrive.DriveError) as caught:
            self.client.open_download(gdrive.DriveTarget("QUOTAFILEID00000000000005"))
        self.assertIn("quota", str(caught.exception).lower())

    def test_private_file_raises(self):
        with self.assertRaises(gdrive.DriveError) as caught:
            self.client.open_download(gdrive.DriveTarget("PRIVATEFILEID000000000006"))
        self.assertIn("permission", str(caught.exception).lower())

    def test_google_doc_is_exported(self):
        response, name = self.client.open_download(
            gdrive.DriveTarget("DOCID0001", kind="document"))
        with response:
            body = response.read()
        self.assertTrue(name.endswith(".pdf"))
        self.assertTrue(body.startswith(b"%PDF"))


class TestFolders(DriveTestCase):
    def test_lists_folder_contents(self):
        targets = self.client.expand(f"https://drive.google.com/drive/folders/{FOLDER}")
        names = sorted(t.name for t in targets)
        self.assertIn("service_manual.pdf", names)
        self.assertIn("mainboard.brd", names)

    def test_recurses_into_subfolders(self):
        targets = self.client.expand(f"https://drive.google.com/drive/folders/{FOLDER}")
        self.assertIn("circuit.fz", [t.name for t in targets])

    def test_respects_folder_depth_limit(self):
        shallow = gdrive.DriveClient("test-agent", timeout=10, folder_depth=0)
        targets = shallow.expand(f"https://drive.google.com/drive/folders/{FOLDER}")
        self.assertNotIn("circuit.fz", [t.name for t in targets])

    def test_file_link_expands_to_itself(self):
        targets = self.client.expand(f"https://drive.google.com/file/d/{SMALL}/view")
        self.assertEqual([t.file_id for t in targets], [SMALL])


if __name__ == "__main__":
    unittest.main(verbosity=2)
