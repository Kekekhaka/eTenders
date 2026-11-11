"""Tests for documents.py: finding the scope of work in a tender's files.
The PDFs and Word files are built in memory; nothing leaves this machine."""

import io
import time
import zipfile

import pytest

import documents

BASE = "https://www.etenders.gov.za/home/Download/?blobName="
FILLER = " ".join(["general"] * 60)  # enough words that a page is not taken for a scan


def make_pdf(pages: list[str]) -> bytes:
    """A real, minimal PDF with one line of Helvetica text per page."""
    n = len(pages)
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            ("<< /Type /Pages /Kids [%s] /Count %d >>"
             % (" ".join(f"{4 + 2 * i} 0 R" for i in range(n)), n)).encode(),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    for i, text in enumerate(pages):
        content = f"BT /F1 9 Tf 20 700 Td ({text}) Tj ET".encode()
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 2000 792] "
                    f"/Resources << /Font << /F1 3 0 R >> >> /Contents {5 + 2 * i} 0 R >>".encode())
        objs.append(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
    out, offsets = b"%PDF-1.4\n", []
    for k, obj in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % k + obj + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % off for off in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return out


def make_docx(text: str, size: int | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        body = f"<w:document><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>"
        if size:
            body += " " * size  # unpacks far bigger than it downloads
        z.writestr("word/document.xml", body)
    return buf.getvalue()


class Http:
    """Fake requests: serves bytes by URL and records every download."""

    def __init__(self, files: dict, status=200):
        self.files, self.status, self.got = files, status, []

    def get(self, url, stream=True, timeout=None, allow_redirects=True):
        self.got.append(url)
        assert allow_redirects is False  # a redirect could lead off the eTenders host
        body, status = self.files.get(url, b""), self.status

        class Response:
            status_code = status

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def iter_content(self, size):
                for i in range(0, len(body), size):
                    yield body[i:i + size]
        return Response()


def doc(name, title=None):
    return {"title": title if title is not None else name, "url": BASE + name}


def later(seconds=60):
    return time.monotonic() + seconds


def test_the_text_starts_at_the_scope_of_work():
    pdf = make_pdf([f"Invitation to bid {FILLER}", f"Conditions {FILLER}",
                    f"Terms of reference: build a data warehouse {FILLER}"])
    text, status = documents.scope_text([doc("rfq.pdf")], later(), Http({BASE + "rfq.pdf": pdf}))
    assert status == "ok"
    assert text.startswith("Terms of reference: build a data warehouse")
    assert "Invitation" not in text


def test_a_file_titled_scope_is_tried_first_and_the_rest_are_not_downloaded():
    files = {BASE + "forms.pdf": make_pdf([f"SBD 4 declaration {FILLER}"]),
             BASE + "scope.pdf": make_pdf([f"Scope of work: Power BI dashboards {FILLER}"])}
    http = Http(files)
    text, status = documents.scope_text([doc("forms.pdf", "SBD forms"), doc("scope.pdf", "Part B Scope of work")],
                                        later(), http)
    assert status == "ok" and "Power BI" in text
    assert http.got == [BASE + "scope.pdf"]


def test_help_guides_are_never_downloaded():
    http = Http({BASE + "guide.pdf": make_pdf([f"Scope of work {FILLER}"])})
    _, status = documents.scope_text([doc("guide.pdf", "SAP Ariba Sourcing User Guide")], later(), http)
    assert http.got == [] and status == "unreadable"


@pytest.mark.parametrize("url", [
    "http://www.etenders.gov.za/home/Download/?blobName=a.pdf",   # not https
    "https://evil.example/a.pdf",                                   # another host
    "https://www.etenders.gov.za.evil.example/a.pdf",               # a look-alike host
    "https://10.0.0.1/a.pdf",                                       # an internal address
    "javascript:alert(1)",
])
def test_only_https_links_on_the_etenders_portal_are_fetched(url):
    http = Http({url: make_pdf([f"Scope of work {FILLER}"])})
    documents.scope_text([{"title": "Scope", "url": url}], later(), http)
    assert http.got == []


def test_a_file_over_the_size_cap_is_not_read(monkeypatch):
    monkeypatch.setattr(documents, "MAX_BYTES", 1000)
    http = Http({BASE + "big.pdf": make_pdf([f"Scope of work {FILLER}" * 20])})
    _, status = documents.scope_text([doc("big.pdf")], later(), http)
    assert status == "unreadable"


def test_a_scanned_pdf_is_reported_as_scanned():
    http = Http({BASE + "scan.pdf": make_pdf(["", ""])})
    assert documents.scope_text([doc("scan.pdf")], later(), http) == (None, "scanned")


def test_a_word_file_is_read():
    http = Http({BASE + "tor.docx": make_docx(f"Terms of reference: data governance {FILLER}")})
    text, status = documents.scope_text([doc("tor.docx")], later(), http)
    assert status == "ok" and text.startswith("Terms of reference: data governance")


def test_a_word_file_that_unpacks_too_big_is_not_read(monkeypatch):
    monkeypatch.setattr(documents, "MAX_BYTES", 100_000)
    http = Http({BASE + "bomb.docx": make_docx(f"Scope of work {FILLER}", size=500_000)})
    assert len(http.files[BASE + "bomb.docx"]) < 100_000  # small to download
    assert documents.scope_text([doc("bomb.docx")], later(), http) == (None, "unreadable")


@pytest.mark.parametrize("body", [b"<html>Access denied</html>", b"%PDF-1.4 broken", b"PK\x03\x04 broken"])
def test_a_broken_or_wrong_file_is_skipped_never_raised(body):
    assert documents.scope_text([doc("x")], later(), Http({BASE + "x": body})) == (None, "unreadable")


def test_without_a_scope_heading_the_start_of_the_file_is_used():
    http = Http({BASE + "rfq.pdf": make_pdf([f"Supply of software licences {FILLER}"])})
    text, status = documents.scope_text([doc("rfq.pdf")], later(), http)
    assert status == "no_scope" and text.startswith("Supply of software licences")


def test_the_text_is_cut_to_max_words(monkeypatch):
    monkeypatch.setattr(documents, "MAX_WORDS", 70)
    http = Http({BASE + "rfq.pdf": make_pdf([f"Scope of work {FILLER}", f"More {FILLER}"])})
    text, _ = documents.scope_text([doc("rfq.pdf")], later(), http)
    assert len(text.split()) == 70


def test_nothing_is_downloaded_after_the_deadline():
    http = Http({BASE + "rfq.pdf": make_pdf([f"Scope of work {FILLER}"])})
    assert documents.scope_text([doc("rfq.pdf")], time.monotonic() - 1, http) == (None, "out_of_time")
    assert http.got == []


def test_a_failed_download_is_skipped():
    http = Http({BASE + "rfq.pdf": make_pdf([f"Scope of work {FILLER}"])}, status=404)
    assert documents.scope_text([doc("rfq.pdf")], later(), http) == (None, "unreadable")


def test_a_tender_without_documents():
    assert documents.scope_text([], later(), Http({})) == (None, "no_documents")
