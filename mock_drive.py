"""A stand-in for Google Drive's public endpoints, used by the test suite.

Reproduces the behavior that makes real Drive downloads awkward: the
virus-scan interstitial on large files, filenames that live only in the
Content-Disposition header, folder pages with an embedded JSON listing, and
the error pages Drive serves with an HTTP 200 status.
"""
import http.server, os, re, threading, urllib.parse

FILES = {
    "SMALLFILEID00000000001": ("service_manual.pdf", b"%PDF-1.4 fake manual" + os.urandom(400)),
    "BIGFILEID000000000000002": ("boardview_pack.rar", b"Rar!\x1a\x07\x00" + os.urandom(900)),
    "BRDFILEID000000000000003": ("mainboard.brd", b"BRD" + os.urandom(300)),
    "FZFILEID0000000000000004": ("circuit.fz", b"<?xml fritzing?>" + os.urandom(200)),
    "QUOTAFILEID00000000000005": ("blocked.pdf", b""),
    "PRIVATEFILEID000000000006": ("private.pdf", b""),
}
NEEDS_CONFIRM = {"BIGFILEID000000000000002"}
QUOTA = {"QUOTAFILEID00000000000005"}
PRIVATE = {"PRIVATEFILEID000000000006"}
FOLDER = ["SMALLFILEID00000000001", "BIGFILEID000000000000002", "BRDFILEID000000000000003"]
SUBFOLDER_ID = "SUBFOLDERID00000000000099"
SUBFOLDER = ["FZFILEID0000000000000004"]

INTERSTITIAL = """<html><head><title>Google Drive - Virus scan warning</title></head>
<body><form id="download-form" action="{base}/download" method="get">
<input type="hidden" name="id" value="{fid}">
<input type="hidden" name="export" value="download">
<input type="hidden" name="confirm" value="t-abc123">
<input type="hidden" name="uuid" value="deadbeef-1234">
</form><p>This file is too large for Google to scan for viruses.</p></body></html>"""

QUOTA_PAGE = """<html><head><title>Google Drive - Quota exceeded</title></head><body>
<p>Sorry, you can't view or download this file at this time.</p>
<p>Too many users have viewed or downloaded this file recently.</p></body></html>"""

PRIVATE_PAGE = """<html><head><title>Sign in - Google Accounts</title></head>
<body><p>You need access. Request access, or switch to an account with access.</p></body></html>"""


def folder_page(children):
    """Mimic the JSON blob Drive embeds in a public folder page."""
    entries = []
    for fid in children:
        name, _ = FILES[fid]
        entries.append(f'["{fid}",["FOLDERID0000000000000001"],"{name}","application/octet-stream"]')
    if children is FOLDER:
        entries.append(f'["{SUBFOLDER_ID}",["FOLDERID0000000000000001"],"subfolder",'
                       f'"application/vnd.google-apps.folder"]')
    blob = ",".join(entries)
    return f"<html><body><script>AF_initDataCallback({{data:[{blob}]}});</script></body></html>"


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body, ctype, disposition=None, code=200):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if disposition:
            self.send_header("Content-Disposition", disposition)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parts = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(parts.query)
        base = f"http://127.0.0.1:{self.server.server_address[1]}"

        folder = re.match(r"/drive/folders/([\w-]+)", parts.path)
        if folder:
            children = SUBFOLDER if folder.group(1) == SUBFOLDER_ID else FOLDER
            return self._send(folder_page(children), "text/html; charset=utf-8")

        doc = re.match(r"/(document|spreadsheets|presentation)/d/([\w-]+)/export", parts.path)
        if doc:
            return self._send(b"%PDF-1.4 exported doc", "application/pdf",
                              'attachment; filename="exported_notes.pdf"')

        if parts.path == "/download":
            fid = query.get("id", [""])[0]
            if fid in QUOTA:
                return self._send(QUOTA_PAGE, "text/html; charset=utf-8")
            if fid in PRIVATE:
                return self._send(PRIVATE_PAGE, "text/html; charset=utf-8")
            if fid not in FILES:
                return self._send("<html><title>Error 404</title></html>",
                                  "text/html", code=404)
            if fid in NEEDS_CONFIRM and query.get("confirm", [""])[0] != "t-abc123":
                return self._send(INTERSTITIAL.format(base=base, fid=fid),
                                  "text/html; charset=utf-8")
            name, blob = FILES[fid]
            return self._send(blob, "application/octet-stream",
                              f'attachment; filename="{name}"')

        self._send("not found", "text/plain", code=404)


def start():
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"
