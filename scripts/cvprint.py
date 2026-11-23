"""Print CV pages to PDF with headless Chrome and count the pages.

Shared by scripts/fit_cv.py and scripts/tailor_cv.py. Serves the repo over
a local http server so fonts and CSS load as they do on the site.
"""

import functools
import http.server
import os
import re
import socketserver
import subprocess
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")  # cv-data.json changes between prints
        super().end_headers()


class Printer:
    def __enter__(self):
        handler = functools.partial(_Quiet, directory=str(ROOT))
        self.httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *a):
        self.httpd.shutdown()
        self.httpd.server_close()

    def url(self, path, out):
        """Print a repo-relative URL path to `out`; return (pages, is_a4)."""
        subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                        "--virtual-time-budget=8000", f"--print-to-pdf={out}",
                        f"http://127.0.0.1:{self.port}/{path}"], capture_output=True, check=True)
        return pages(out)

    def html(self, html, name, out):
        """Write a page into build/private/ (its {{ROOT}} is ../../) and print it."""
        page = ROOT / "build" / "private" / name
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(html, encoding="utf-8")
        return self.url(f"build/private/{name}", out)


def pages(pdf):
    info = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True).stdout
    n = int(re.search(r"^Pages:\s+(\d+)", info, re.M).group(1))
    return n, "A4" in re.search(r"^Page size:.*$", info, re.M).group(0)
