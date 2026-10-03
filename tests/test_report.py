"""Tests for report.py: subject, HTML body and xlsx, built from hand-made
Digests (no pipeline, no network). Each test names the AC it covers."""

import datetime as dt
import io
import re
import zipfile
from html.parser import HTMLParser

import pandas as pd
import pytest
from openpyxl import load_workbook

import digest
import report

RUN_AT = dt.datetime(2026, 10, 1, 6, 0, tzinfo=dt.timezone.utc)  # 08:00 SAST
WINDOW = (dt.date(2026, 9, 26), dt.date(2026, 10, 1))
TORTURE = "<script>\"'&"


# ---------------------------------------------------------------------------
# Helpers: build Digests the way the backend does (API_CONTRACT 2.3)
# ---------------------------------------------------------------------------

def tender(n=1, **fields):
    """One row with every TENDER_COLUMN. closing/published are ISO strings or None."""
    row = {
        "ocid": f"ocds-9t57fa-{n:06d}",
        "tender_number": f"RFQ{2500 + n}",
        "title": f"Tender {n}",
        "buyer": "Department of Example",
        "province": "Gauteng",
        "category": "Services: General",
        "method": "Request for Quotation",
        "contact": "buyer@example.gov.za",
        "matched_keywords": "data",
        "blockers": None,
        "closing": "2026-10-20T09:00:00Z",
        "published": "2026-09-30T08:00:00Z",
        "closing_raw": "2026-10-20T11:00:00+02:00",
        "closing_soon": False,
        "is_new": False,
        "score": 3,
        "briefing_compulsory": None,
        "document_urls": [f"https://www.etenders.gov.za/doc{n}.pdf"],
    }
    row.update(fields)
    return row


def frame(rows):
    df = pd.DataFrame(rows, columns=[c for c in digest.TENDER_COLUMNS if c != "days_left"])
    df["closing"] = pd.to_datetime(df["closing"], utc=True)
    df["published"] = pd.to_datetime(df["published"], utc=True)
    left = (df["closing"] - RUN_AT) / pd.Timedelta(days=1)
    df["days_left"] = left.apply(lambda x: pd.NA if pd.isna(x) else -(-x // 1)).astype("Int64")
    df["closing_soon"] = df["closing_soon"].astype(bool)
    df["is_new"] = df["is_new"].astype(bool)
    df["score"] = df["score"].astype(int)
    return df[list(digest.TENDER_COLUMNS)].reset_index(drop=True)


def make_digest(rows=(), **overrides):
    df = frame(list(rows))
    status = overrides.get("memory_status", "ok")
    fields = dict(
        run_at=RUN_AT, window=WINDOW, tenders=df, total=len(df),
        new_count=None if status == "unavailable" else int(df["is_new"].sum()),
        closing_soon_count=int(df["closing_soon"].sum()), closing_soon_days=7,
        partial_days=(), missed_days=(), memory_status=status, heartbeat="on",
        dropped_releases=0,
    )
    fields.update(overrides)
    return digest.Digest(**fields)


def sample_digest():
    """A realistic digest with every state the operator can see: NEW rows,
    closing-soon rows, a no-closing-date row, a partial day and two
    permanently missed days. Used for docs/cosmos/samples/email_sample.html."""
    buyers = ["City of Tshwane", "Transnet SOC Ltd", "SITA", "Eskom Holdings SOC Ltd",
              "Department of Health: Limpopo", "City of Cape Town", "SANRAL",
              "Gauteng Department of e-Government", "Statistics South Africa", "PRASA"]
    titles = ["RFQ2596", "Appointment of a service provider for data warehouse support for 36 months",
              "Supply and delivery of laptops", "BID 14/2026: Power BI dashboards for revenue management",
              "RFQ/ICT/0912", "Provision of ETL and data migration services",
              "Maintenance of the ERP reporting environment", "Business intelligence licences renewal"]
    keywords = ["data", "power bi, dashboard", "data warehouse", "etl, data migration",
                "business intelligence", "analytics"]
    rows = []
    for n in range(1, 47):
        new = n <= 9
        hours = 30 + n * 23
        soon = hours <= 7 * 24
        rows.append(tender(
            n,
            tender_number=f"{['RFQ', 'BID', 'TEN'][n % 3]}{2560 + n * 7}",
            title=titles[n % len(titles)],
            buyer=buyers[n % len(buyers)],
            category=["Services: General", "Services: Professional", "Supplies: General"][n % 3],
            province=["Gauteng", "Western Cape", "National", "Limpopo"][n % 4],
            matched_keywords=keywords[n % len(keywords)],
            blockers="CIDB grading required" if n % 11 == 0 else None,
            briefing_compulsory=True if n % 8 == 0 else None,
            closing=(RUN_AT + dt.timedelta(hours=hours)).isoformat(),
            closing_soon=soon, is_new=new, score=12 - n // 4,
            document_urls=[f"https://www.etenders.gov.za/home/Download/?blobName=bid{n}.pdf"],
        ))
    rows.insert(4, tender(99, tender_number="RFQ0042", title="Cleaning of data centre",
                          closing=None, closing_raw="To be advised", is_new=True,
                          document_urls=[]))
    df_rows = sorted(rows, key=lambda r: (not r["is_new"], -r["score"]))
    return make_digest(
        df_rows,
        partial_days=(digest.PartialDay(dt.date(2026, 9, 29), "ReadTimeout"),
                      digest.PartialDay(dt.date(2026, 9, 27), "out of time")),
        missed_days=(digest.MissedDay(dt.date(2026, 9, 20), "never_fetched", dt.date(2026, 9, 26)),
                     digest.MissedDay(dt.date(2026, 9, 21), "only_partly_fetched", dt.date(2026, 9, 27))),
        heartbeat="off", dropped_releases=2,
    )


def text_of(body):
    """The visible text of an HTML body, with tags removed and entities decoded."""
    import html
    return html.unescape(re.sub(r"<[^>]+>", " ", body))


def reopen(xlsx: bytes):
    wb = load_workbook(io.BytesIO(xlsx))
    ws = wb["Tenders"]
    header = [c.value for c in ws[1]]
    rows = [dict(zip(header, (c for c in r))) for r in ws.iter_rows(min_row=2)]
    return wb, ws, header, rows


# ---------------------------------------------------------------------------
# Subject (F-2)
# ---------------------------------------------------------------------------

def test_subject_of_a_complete_digest_gives_the_counts_with_no_partial_suffix():  # AC-01
    d = make_digest([tender(1, is_new=True), tender(2), tender(3)])
    assert report.render_subject(d) == "eTenders digest - 3 tender(s), 1 new"


def test_subject_of_a_partial_digest_ends_with_partial():  # AC-03
    d = make_digest([tender(1)], partial_days=(digest.PartialDay(dt.date(2026, 9, 23), "ReadTimeout"),))
    assert report.render_subject(d) == "eTenders digest - 1 tender(s), 0 new (partial)"


def test_subject_says_new_unknown_when_memory_is_unavailable():  # AC-08, FR-17
    d = make_digest([tender(1)], memory_status="unavailable")
    assert report.render_subject(d) == "eTenders digest - 1 tender(s), new unknown"


def test_subject_holds_no_api_text():  # AC-35
    d = make_digest([tender(1, title=TORTURE, buyer=TORTURE)],
                    partial_days=(digest.PartialDay(dt.date(2026, 9, 23), TORTURE),))
    subject = report.render_subject(d)
    assert re.fullmatch(r"eTenders digest - \d+ tender\(s\), (\d+ new|new unknown)( \(partial\))?",
                        subject)


# ---------------------------------------------------------------------------
# HTML body (F-3, F-6)
# ---------------------------------------------------------------------------

def test_body_opens_with_the_summary_line_and_has_every_required_column():  # AC-16
    d = make_digest([tender(1, is_new=True, closing_soon=True,
                            closing="2026-10-03T09:00:00Z"), tender(2)])
    body = report.render_html(d)
    text = text_of(body)
    # The page <title> is the only text before the summary line.
    assert text.split("eTenders digest", 1)[1].lstrip().startswith(
        "2 tender(s) · 1 new · 1 closing within 7 days")
    for header in ("Flags", "Closes (SAST)", "Days left", "Tender #", "Buyer", "Title",
                   "Category", "Document"):
        assert re.search(rf'<th scope="col" [^>]*>{re.escape(header)}</th>', body)
    assert "<caption" in body
    assert "NEW" in text and "CLOSING SOON" in text


def test_summary_says_new_unknown_when_memory_is_unavailable():  # AC-08, AC-16
    body = report.render_html(make_digest([tender(1)], memory_status="unavailable"))
    assert "1 tender(s) · new unknown · 0 closing within 7 days" in text_of(body)


def test_closing_is_shown_in_sast_with_days_left():  # AC-16, NFR-13
    d = make_digest([tender(1, closing="2026-10-03T09:00:00Z")])  # 11:00 SAST
    text = text_of(report.render_html(d))
    assert "Sat 3 Oct" in text and "11:00" in text and "09:00" not in text
    assert re.search(r"11:00\s+3\s", text)  # 2 days 3 hours -> 3 days left


def test_priority_rows_are_never_capped_and_the_rest_is_counted():  # AC-17
    rows = [tender(n, is_new=True) for n in range(1, 41)]
    rows += [tender(n, closing_soon=True, closing="2026-10-02T09:00:00Z") for n in range(41, 44)]
    rows += [tender(n) for n in range(44, 104)]
    body = report.render_html(make_digest(rows))
    shown = set(re.findall(r"RFQ(\d+)", body))
    assert all(str(2500 + n) in shown for n in range(1, 44))  # every new and closing-soon row
    others = [n for n in range(44, 104) if str(2500 + n) in shown]
    assert others == list(range(44, 44 + report.OTHER_ROWS_CAP))  # the first 25, in order
    assert "35 more tender(s) in the attached xlsx only." in body


def test_no_more_line_when_every_row_is_shown():  # AC-17
    body = report.render_html(make_digest([tender(1), tender(2)]))
    assert "more tender(s) in the attached xlsx only" not in body


def test_rows_are_shown_in_digest_order_without_resorting():  # AC-19, FR-13
    rows = [tender(5, is_new=True, score=1), tender(3, is_new=True, score=9), tender(9, score=20)]
    body = report.render_html(make_digest(rows))
    assert re.findall(r"RFQ(\d+)", body) == ["2505", "2503", "2509"]


def test_banners_come_in_the_fixed_order_above_the_table():  # AC-18
    d = make_digest(
        [tender(1)], memory_status="first_run",
        partial_days=(digest.PartialDay(dt.date(2026, 9, 23), "ReadTimeout"),),
        missed_days=(digest.MissedDay(dt.date(2026, 9, 18), "never_fetched", dt.date(2026, 9, 24)),))
    body = report.render_html(d)
    order = [body.index(s) for s in ("Permanently missed", "Partial digest.",
                                     "first run with new-tender marking", "<caption")]
    assert order == sorted(order)


def test_partial_digest_names_each_day_with_its_reason():  # AC-03 (was in test_email_sender)
    d = make_digest([tender(1)], partial_days=(
        digest.PartialDay(dt.date(2026, 9, 23), "ReadTimeout"),
        digest.PartialDay(dt.date(2026, 9, 22), "out of time")))
    body = report.render_html(d)
    assert "Partial digest." in body
    assert "2026-09-23 (ReadTimeout)" in body and "2026-09-22 (out of time)" in body


def test_complete_digest_has_no_partial_warning():  # (was in test_email_sender)
    body = report.render_html(make_digest([tender(1)]))
    assert "Partial digest" not in body and "Permanently missed" not in body


@pytest.mark.parametrize("kind, words", [("never_fetched", "never fetched"),
                                         ("only_partly_fetched", "only partly fetched")])
def test_permanently_missed_days_name_the_kind_and_say_check_by_hand(kind, words):  # AC-09, AC-11
    d = make_digest([tender(1)], missed_days=(
        digest.MissedDay(dt.date(2026, 9, 20), kind, dt.date(2026, 9, 26)),))
    text = text_of(report.render_html(d))
    assert f"2026-09-20 ({words})" in text
    assert "check etenders.gov.za by hand" in text


def test_first_run_notice():  # AC-07
    body = report.render_html(make_digest([tender(1, is_new=True)], memory_status="first_run"))
    assert "first run with new-tender marking" in body
    assert "unavailable for this run" not in body


def test_memory_unavailable_banner():  # AC-08, AC-27, AC-28
    body = report.render_html(make_digest([tender(1)], memory_status="unavailable"))
    assert "New-tender marking and missed-day tracking were unavailable for this run." in body
    assert "first run with new-tender marking" not in body


def test_memory_ok_shows_no_memory_banner():
    body = report.render_html(make_digest([tender(1)]))
    assert "unavailable for this run" not in body and "first run" not in body


@pytest.mark.parametrize("raw, shown", [("To be advised", "To be advised"),
                                        (None, "no closing date"), ("", "no closing date")])
def test_a_tender_without_a_closing_date_says_so(raw, shown):  # AC-14
    d = make_digest([tender(1, closing=None, closing_raw=raw)])
    text = text_of(report.render_html(d))
    assert shown in text and "CLOSING SOON" not in text


def test_missing_values_render_as_blank_cells():  # (was in test_email_sender)
    row = tender(1, title=None, buyer=None, category=None, matched_keywords=None,
                 tender_number=None, closing=None, closing_raw=None, published=None)
    body = report.render_html(make_digest([row]))
    for junk in ("None", "nan", "NaT", "<NA>"):
        assert junk not in text_of(body)


def test_empty_digest_says_nothing_matched_and_has_no_table():  # AC-02
    d = make_digest([])
    body = report.render_html(d)
    assert "No matching tenders were found in this run." in body
    assert "<caption" not in body and 'scope="col"' not in body
    assert "0 tender(s) · 0 new · 0 closing within 7 days" in body
    assert "attached xlsx" not in body  # SF-1: there is no attachment on an empty digest


@pytest.mark.parametrize("state, says_off", [("off", True), ("misconfigured", True), ("on", False)])
def test_footer_says_when_heartbeat_monitoring_is_off(state, says_off):  # AC-31, AC-32
    body = report.render_html(make_digest([tender(1)], heartbeat=state))
    assert ("Heartbeat monitoring is off." in body) == says_off


@pytest.mark.parametrize("dropped, line", [
    (3, "3 malformed release(s) from the eTenders API were left out."), (0, None)])
def test_footer_states_dropped_releases(dropped, line):  # AC-29
    body = report.render_html(make_digest([tender(1)], dropped_releases=dropped))
    if line:
        assert line in body
    else:
        assert "malformed release" not in body


class _Tags(HTMLParser):
    """Collects every tag and attribute so the torture test can inspect them."""

    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, attrs))


def test_escaping_torture_test():  # AC-35, NFR-08(b), NFR-08(f)
    text_cols = ("tender_number", "title", "buyer", "province", "category", "method",
                 "contact", "matched_keywords", "blockers", "closing_raw")
    rows = [
        tender(1, closing=None, document_urls=["javascript:alert(1)"],
               **{c: TORTURE for c in text_cols}),
        tender(2, is_new=True, closing_soon=True, closing="2026-10-02T09:00:00Z",
               document_urls=["https://example.com/a\"b'c"],
               **{c: TORTURE for c in text_cols}),
    ]
    d = make_digest(rows, partial_days=(digest.PartialDay(dt.date(2026, 9, 23), TORTURE),),
                    missed_days=(digest.MissedDay(dt.date(2026, 9, 18), "never_fetched",
                                                  dt.date(2026, 9, 24)),))
    body = report.render_html(d)

    assert "<script" not in body.lower()
    assert "javascript:" not in body.lower()
    assert 'href="https://example.com/a&quot;b&#x27;c"' in body
    assert "mailto:" not in body.lower()
    assert "&lt;script&gt;&quot;&#x27;&amp;" in body  # the payload is there, escaped

    parser = _Tags()
    parser.feed(body)
    allowed_tags = {"html", "head", "meta", "title", "body", "table", "tr", "td", "th", "thead",
                    "tbody", "caption", "h1", "p", "span", "strong", "br", "a"}
    allowed_attrs = {"lang", "charset", "name", "content", "style", "role", "width",
                     "cellpadding", "cellspacing", "border", "align", "valign", "scope",
                     "colspan", "href"}
    for tag, attrs in parser.tags:
        assert tag in allowed_tags
        for name, value in attrs:
            assert name in allowed_attrs
            if name in ("style", "class"):
                assert "script" not in value and "<" not in value and "&" not in value
            if name == "href":
                assert digest.is_safe_url(value)


# AC-21b: the first URL is safe; none of the others may ever become a link.
LINK_CASES = [
    ("https://www.etenders.gov.za/doc.pdf", True),
    ("javascript:alert(1)", False),
    ("file://host/share", False),
    ("\\\\host\\share", False),  # a UNC path
    ("https://", False),
    ("https://a b.com", False),
    pytest.param("https://example.com/" + "a" * 2000, False, id="over-2000-chars"),
]


@pytest.mark.parametrize("url, linked", LINK_CASES)
def test_only_safe_links_become_hrefs_in_the_html(url, linked):  # AC-21b (HTML)
    body = report.render_html(make_digest([tender(1, document_urls=[url])]))
    hrefs = re.findall(r'href="([^"]*)"', body)
    doc_hrefs = [h for h in hrefs if h != "https://www.etenders.gov.za/"]
    assert doc_hrefs == ([url] if linked else [])


def test_a_link_shows_its_host():  # AC-35 ("show a link's host")
    body = report.render_html(make_digest([tender(1, document_urls=["https://docs.example.org/x.pdf"])]))
    assert "View document</a><br>" in body and "docs.example.org</span>" in body


def test_html_has_no_scripts_or_external_assets():  # NFR-13
    body = report.render_html(sample_digest()).lower()
    for banned in ("<script", "<link", "<img", "<style", "src=", "@import", "url("):
        assert banned not in body


def test_render_does_not_change_the_backends_frame():  # API_CONTRACT 2.1
    d = sample_digest()
    before = d.tenders.copy()
    report.render_html(d)
    report.build_xlsx(d)
    pd.testing.assert_frame_equal(d.tenders, before)


def test_html_stays_under_100_kb_at_60_rows_with_long_titles():  # F-5, NFR-13
    rows = [tender(n, is_new=True, title="T" * 200, buyer="B" * 80, matched_keywords="data, etl",
                   document_urls=["https://www.etenders.gov.za/home/Download/?blobName="
                                  + "x" * 80 + ".pdf"])
            for n in range(60)]
    size = len(report.render_html(make_digest(rows)).encode("utf-8"))
    assert size <= 100_000, size


# Text tokens and the backgrounds they sit on (WCAG 2.2 AA: 4.5:1).
CONTRAST_PAIRS = [
    (report._INK, report._PAPER), (report._MUTED, report._PAPER), (report._LINK, report._PAPER),
    (report._PAPER, report._NEW_BG), (report._PAPER, report._SOON), (report._SOON, report._PAPER),
    (report._MISSED_FG, report._MISSED_BG), (report._PARTIAL_FG, report._PARTIAL_BG),
    (report._INK, report._INFO_BG), (report._MUTED, report._INFO_BG),
]


def _luminance(hex_colour):
    channels = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


@pytest.mark.parametrize("fg, bg", CONTRAST_PAIRS)
def test_text_colours_meet_wcag_aa_contrast(fg, bg):  # FR-17, DESIGN_SPEC 8.6
    hi, lo = sorted((_luminance(fg), _luminance(bg)), reverse=True)
    assert (hi + 0.05) / (lo + 0.05) >= 4.5


# ---------------------------------------------------------------------------
# xlsx (F-4)
# ---------------------------------------------------------------------------

def test_xlsx_is_none_when_there_are_no_tenders():  # AC-02
    assert report.build_xlsx(make_digest([])) is None


def test_xlsx_has_one_sheet_with_every_row_in_digest_order_and_the_flags():  # AC-19, FR-18
    d = sample_digest()
    wb, ws, header, rows = reopen(report.build_xlsx(d))
    assert wb.sheetnames == ["Tenders"]
    assert {"new", "closing_soon", "days_left", "closing", "published"} <= set(header)
    assert [r["ocid"].value for r in rows] == list(d.tenders["ocid"])
    assert [r["new"].value for r in rows] == list(d.tenders["is_new"])
    assert [r["closing_soon"].value for r in rows] == list(d.tenders["closing_soon"])
    assert [r["days_left"].value for r in rows] == [
        None if pd.isna(x) else int(x) for x in d.tenders["days_left"]]


def test_xlsx_rows_match_the_html_order():  # AC-19
    d = sample_digest()
    _, _, _, rows = reopen(report.build_xlsx(d))
    html_numbers = re.findall(r'<th scope="row"[^>]*>([^<]*)</th>', report.render_html(d))
    xlsx_numbers = [r["tender_number"].value for r in rows]
    assert len(html_numbers) > 1
    assert html_numbers == [n for n in xlsx_numbers if n in html_numbers]


def test_xlsx_new_is_blank_not_false_when_marking_was_unavailable():  # NFR-04
    _, _, _, rows = reopen(report.build_xlsx(make_digest([tender(1)], memory_status="unavailable")))
    assert rows[0]["new"].value is None


def test_xlsx_dates_are_sast_datetimes_with_frozen_filtered_header():  # AC-20
    d = make_digest([tender(1, closing="2026-10-03T09:00:00Z", published="2026-09-30T22:30:00Z"),
                     tender(2, closing=None)])
    _, ws, header, rows = reopen(report.build_xlsx(d))
    assert rows[0]["closing"].value == dt.datetime(2026, 10, 3, 11, 0)
    assert rows[0]["published"].value == dt.datetime(2026, 10, 1, 0, 30)
    assert rows[0]["closing"].data_type == "d" or rows[0]["closing"].is_date
    assert rows[1]["closing"].value is None
    assert ws.freeze_panes == "A2"
    assert ws.auto_filter.ref == ws.dimensions
    assert ws.column_dimensions["F"].width >= 40  # title is readable


@pytest.mark.parametrize("url, linked", LINK_CASES)
def test_only_safe_links_become_xlsx_hyperlinks(url, linked):  # AC-21b (xlsx), AC-20
    _, ws, _, rows = reopen(report.build_xlsx(make_digest([tender(1, document_urls=[url])])))
    links = [c.hyperlink.target for row in ws.iter_rows() for c in row if c.hyperlink]
    assert links == ([url] if linked else [])


FORMULA_PAYLOADS = ["=1+1", "+1+1", "-1+1", "@SUM(A1)", "\t=1+1", "\r=1+1",
                    "=cmd|' /C calc'!A0", '=HYPERLINK("http://evil","x")']


@pytest.mark.parametrize("payload", FORMULA_PAYLOADS)
def test_api_text_is_never_a_formula(payload):  # AC-21a, NFR-08(c)
    d = make_digest([tender(1, title=payload, buyer=payload, blockers=payload, closing_raw=payload)])
    xlsx = report.build_xlsx(d)
    wb, _, _, rows = reopen(xlsx)
    assert wb.sheetnames == ["Tenders"]
    for col in ("title", "buyer", "blockers", "closing_raw"):
        cell = rows[0][col]
        assert cell.data_type == "s"
        # Nothing is added in front. XML turns a stored CR into LF on reading,
        # which still isn't a formula.
        assert cell.value.replace("\n", "\r") == payload.replace("\n", "\r")
    with zipfile.ZipFile(io.BytesIO(xlsx)) as z:
        sheets = [n for n in z.namelist() if n.startswith("xl/worksheets/")]
        for name in sheets:
            assert not re.search(rb"<f[ >]", z.read(name))


def test_xlsx_strips_illegal_control_characters_instead_of_crashing():  # AC-29 (sink)
    _, _, _, rows = reopen(report.build_xlsx(make_digest([tender(1, title="bad\x0btitle")])))
    assert rows[0]["title"].value == "badtitle"
