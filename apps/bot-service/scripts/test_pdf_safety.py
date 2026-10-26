"""The PDF maker must treat typed text as text and never touch the network.

Why this exists: the template rendered fields unescaped and Chromium loaded
the page with the network on, so a reporter name like
<iframe src=http://internal-host/> (typed into the dashboard, or sent to the
Telegram bot by anyone) pulled that internal page into the PDF.

    python scripts/test_pdf_safety.py
"""
import base64
import re
import copy
import io
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("STORAGE_DIR", tempfile.mkdtemp(prefix="pdf-safety-"))

from PIL import Image  # noqa: E402

import app.rendering.renderer as renderer  # noqa: E402
from render_sample import SAMPLE_CAR_REPORT  # noqa: E402


def has_image(pdf: Path) -> bool:
    return re.search(rb"/Subtype\s*/Image", pdf.read_bytes()) is not None


def ok(msg: str) -> None:
    print(f"    ok   {msg}")


hits: list[str] = []


class Recorder(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        hits.append(self.path)
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<h1>INTERNAL PAGE</h1>")

    def log_message(self, *args):
        pass


server = ThreadingHTTPServer(("127.0.0.1", 0), Recorder)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{server.server_address[1]}"

ATTACKS = [
    f'<iframe src="{BASE}/iframe"></iframe>',
    f'<img src="{BASE}/img">',
    f'<meta http-equiv="refresh" content="0;url={BASE}/refresh">',
    f'<link rel="stylesheet" href="{BASE}/css">',
    f'<script>fetch("{BASE}/script")</script>',
]
PAYLOAD = "".join(ATTACKS)

out_dir = Path(tempfile.mkdtemp(prefix="pdf-out-"))


def hostile_report() -> dict:
    report = copy.deepcopy(SAMPLE_CAR_REPORT)
    report["reporter_name"] = PAYLOAD
    report["report_id"] = PAYLOAD
    report["annex_a"] = {"items": [{
        "part_name": PAYLOAD, "condition": PAYLOAD, "action": "Replace", "qty": 1,
        "workshop_est": 100.0, "adjusted_cost": 90.0, "discount_pct": None, "is_net_item": False,
    }]}
    report["sign_off"] = {"surveyor_name": "x", "qualifications": PAYLOAD, "license_number": PAYLOAD}
    return report


# 1. Typed fields come out escaped in the HTML handed to Chromium.
captured: list[str] = []
real_html_to_pdf = renderer.html_to_pdf
renderer.html_to_pdf = lambda html, path, slot_wait=None: captured.append(html)
try:
    renderer.render_pdf(hostile_report(), [], str(out_dir / "captured.pdf"))
finally:
    renderer.html_to_pdf = real_html_to_pdf
html = captured[0]
for tag in ("<iframe", "<img src=\"http", "<meta http-equiv", "<link rel", "<script>"):
    assert tag not in html, f"{tag!r} reached the page unescaped"
assert "&lt;iframe" in html
assert "Requested by:" in html and "WX 8888 A" in html  # the rest still renders
ok("typed fields are escaped (iframe, img, meta refresh, link, script)")

# 2. A whole hostile report renders, and nothing is fetched.
pdf = out_dir / "hostile.pdf"
renderer.render_pdf(hostile_report(), [], str(pdf))
assert pdf.exists() and pdf.stat().st_size > 1000
assert hits == [], f"PDF maker fetched {hits}"
ok("a hostile report renders to a PDF and makes no requests")

# 3. Even raw markup that skipped escaping can't reach the network.
raw = out_dir / "raw.pdf"
renderer.html_to_pdf(f"<html><head>{ATTACKS[2]}{ATTACKS[3]}</head><body>{PAYLOAD}</body></html>", str(raw))
assert raw.exists()
assert hits == [], f"raw markup fetched {hits}"
ok("raw iframe/img/refresh/stylesheet/script markup is blocked offline")

# 4. Inline (data:) images still print -- photos depend on them.
buf = io.BytesIO()
Image.new("RGB", (400, 300), (200, 30, 30)).save(buf, "PNG")
data_uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
with_img = out_dir / "with_img.pdf"
without_img = out_dir / "without_img.pdf"
renderer.html_to_pdf(f'<html><body><img src="{data_uri}"></body></html>', str(with_img))
renderer.html_to_pdf("<html><body>no image</body></html>", str(without_img))
assert has_image(with_img), "data: image missing from the PDF"
assert not has_image(without_img)
ok("data: URI images are still embedded")

# 5. A report's real photos still make it into the PDF.
photo = out_dir / "photo.jpg"
Image.new("RGB", (800, 600), (30, 120, 200)).save(photo, "JPEG")
with_photo = out_dir / "with_photo.pdf"
renderer.render_pdf(copy.deepcopy(SAMPLE_CAR_REPORT), [str(photo)], str(with_photo))
assert has_image(with_photo), "case photo missing from the PDF"
assert hits == []
ok("case photos are still embedded in a normal report")

server.shutdown()
print("\n  all PDF safety assertions passed\n")
