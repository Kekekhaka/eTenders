"""Renders a digest.Digest into the email subject, the HTML body and the xlsx.

Pure: no I/O, no Azure, nothing from pipeline/memory/email_sender. The rules
it follows are in docs/cosmos/API_CONTRACT.md section 2.6:
- the backend decides what is true (flags, counts, order); this module only
  decides how it looks, and never re-sorts or changes d.tenders;
- every value is HTML-escaped with _e() where it is interpolated;
- a link is only made from a URL that passes digest.is_safe_url;
- the xlsx never holds a formula.

The HTML is an email: table layout, inline styles, no scripts, no images, no
external CSS or fonts, so it renders in Outlook, Gmail and on phones.
"""

import html
import io
from urllib.parse import urlsplit

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from digest import DISPLAY_TZ, NOT_AI_CHECKED, Digest, is_safe_url, strip_unwritable

OTHER_ROWS_CAP = 25

# ---------------------------------------------------------------------------
# Design tokens. Contrast ratios are against the background they sit on and
# are checked by tests/test_report.py (WCAG 2.2 AA needs 4.5:1 for text).
# ---------------------------------------------------------------------------
_INK = "#1F2328"         # body text on white, 15.8:1
_MUTED = "#57606A"       # secondary text on white, 6.4:1
_LINK = "#0B57A4"        # links on white, 7.0:1
_RULE = "#D0D7DE"        # hairlines between rows
_PAPER = "#FFFFFF"       # the message card
_PAGE = "#F3F4F6"        # behind the card
_NEW_BG = "#0B5563"      # NEW label: white text, 7.9:1
_SOON = "#9A3412"        # CLOSING SOON label (white text, 7.3:1) and soon dates on white
_AI_BG = "#5B21B6"       # AI-flagged label: white text
_MISSED_BG, _MISSED_FG, _MISSED_EDGE = "#FDECEC", "#7A1414", "#B42318"
_PARTIAL_BG, _PARTIAL_FG, _PARTIAL_EDGE = "#FFF4D6", "#5C3B00", "#B76E00"
_INFO_BG, _INFO_EDGE = "#EEF2F6", "#57606A"  # text is _INK

# Type: system fonts only (no web fonts in email). Segoe UI is Outlook's
# own face; tender numbers are monospaced so "RFQ2596" and "RFQ2569" differ
# at a glance and O/0, l/1 can't be confused.
_SANS = "Segoe UI,Helvetica,Arial,sans-serif"
_MONO = "Consolas,Menlo,monospace"
_SIZE_TITLE, _SIZE_BODY, _SIZE_TABLE, _SIZE_SMALL = "20px", "14px", "13px", "12px"

# Spacing scale: 4 / 8 / 12 / 16 / 24 px. Cell padding comes from the
# table's cellpadding attribute so it isn't repeated on every cell.

_TD = f' style="border-bottom:1px solid {_RULE};"'
_SMALL = f' style="color:{_MUTED};font-size:{_SIZE_SMALL};"'

_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

_KIND_TEXT = {"never_fetched": "never fetched", "only_partly_fetched": "only partly fetched"}
_ETENDERS = "https://www.etenders.gov.za/"


def _e(x) -> str:
    """Escape one scalar for HTML. Null and "" both become ""."""
    return html.escape("" if pd.isna(x) else str(x), quote=True)


# ---------------------------------------------------------------------------
# Subject
# ---------------------------------------------------------------------------

def render_subject(d: Digest) -> str:
    """Counts and constants only, never API text (AC-35)."""
    new = "new unknown" if d.new_count is None else f"{int(d.new_count)} new"
    subject = f"eTenders digest - {int(d.total)} tender(s), {new}"
    if d.partial_days:
        subject += " (partial)"
    return subject


# ---------------------------------------------------------------------------
# HTML body
# ---------------------------------------------------------------------------

def _sast(ts):
    """A tz-aware UTC timestamp in SAST, or None for NaT."""
    return None if pd.isna(ts) else ts.tz_convert(DISPLAY_TZ)


def _day_text(t) -> str:
    """'Fri 3 Oct' for a time already in SAST."""
    return f"{_DAYS[t.weekday()]} {t.day} {_MONTHS[t.month - 1]}"


def _when(ts, run_year: int) -> str:
    """'Fri 3 Oct<br>11:00' in SAST; the year only when it isn't this year."""
    t = _sast(ts)
    day = _day_text(t)
    if t.year != run_year:
        day += f" {t.year}"
    return f"{_e(day)}<br>{_e(t.strftime('%H:%M'))}"


def _link(url, text: str) -> str:
    """An <a> only for a safe URL, with its host shown under it; else ""."""
    if not is_safe_url(url):
        return ""
    host = urlsplit(url).hostname
    return (f'<a href="{_e(url)}" style="color:{_LINK};">{_e(text)}</a>'
            f"<br><span{_SMALL}>{_e(host)}</span>")


def _label(text: str, background: str) -> str:
    return (f'<span style="background-color:{background};color:{_PAPER};'
            f'font-weight:bold;font-size:{_SIZE_SMALL};padding:1px 4px;white-space:nowrap;">{text}</span>')


def _shown_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Every NEW or closing-soon row, plus the first OTHER_ROWS_CAP others,
    all in digest order (OQ-BA1, AC-17)."""
    priority = df["is_new"] | df["closing_soon"]
    keep = priority.copy()
    keep[df.index[~priority][:OTHER_ROWS_CAP]] = True
    return df[keep]


def _banner(background: str, colour: str, edge: str, heading: str, body: str) -> str:
    return (f'<tr><td style="padding:0 16px 12px;">'
            f'<table role="presentation" width="100%" cellpadding="12" cellspacing="0" border="0">'
            f'<tr><td style="background-color:{background};color:{colour};'
            f'border-left:4px solid {edge};font-size:{_SIZE_BODY};line-height:1.45;">'
            f"<strong>{heading}</strong><br>{body}</td></tr></table></td></tr>")


def _banners(d: Digest) -> list[str]:
    """Coverage banners, in the fixed order of API_CONTRACT 2.6 (AC-18)."""
    out = []
    if d.missed_days:
        days = "<br>".join(f"{_e(m.day.isoformat())} ({_e(_KIND_TEXT.get(m.kind, m.kind))})"
                           for m in d.missed_days)
        out.append(_banner(
            _MISSED_BG, _MISSED_FG, _MISSED_EDGE,
            "Permanently missed days: check etenders.gov.za by hand",
            "These publication days left the digest window before they were fetched "
            "completely. No digest will list their tenders, so search "
            f'<a href="{_e(_ETENDERS)}" style="color:{_MISSED_FG};">etenders.gov.za</a> '
            f"by hand for tenders published on:<br>{days}"))
    if d.partial_days:
        days = ", ".join(f"{_e(p.day.isoformat())} ({_e(p.reason)})" for p in d.partial_days)
        out.append(_banner(
            _PARTIAL_BG, _PARTIAL_FG, _PARTIAL_EDGE,
            "Partial digest.",
            f"These days could not be fetched completely, so tenders published on them "
            f"may be missing: {days}. Later runs try them again. A day that leaves the "
            "window first will be listed as permanently missed."))
    if d.memory_status == "unavailable":
        out.append(_banner(
            _INFO_BG, _INK, _INFO_EDGE,
            "New-tender marking and missed-day tracking were unavailable for this run.",
            "No tender is marked NEW, and permanently missed days could not be checked. "
            "Every open tender is still listed."))
    elif d.memory_status == "first_run":
        out.append(_banner(
            _INFO_BG, _INK, _INFO_EDGE,
            "This is the first run with new-tender marking.",
            "Every tender is marked NEW. From the next digest on, NEW means the tender "
            "was not in any earlier digest."))
    return out


def _row(r, run_year: int) -> str:
    soon = bool(r.closing_soon)
    flags = []
    if bool(r.is_new):
        flags.append(_label("NEW", _NEW_BG))
    if soon:
        flags.append(_label("CLOSING SOON", _SOON))
    # Llama is the normal decider, so only the exceptions get a label.
    if r.ai_flag == "nano":
        flags.append(_label("AI: nano", _AI_BG))
    elif r.ai_flag == NOT_AI_CHECKED:
        flags.append(_label("NOT AI-CHECKED", _AI_BG))

    if pd.isna(r.closing):
        closes = f"<span{_SMALL}>{_e(r.closing_raw) or 'no closing date'}</span>"
        days_left = ""
    else:
        closes = _when(r.closing, run_year)
        days_left = _e(int(r.days_left))
    if soon:
        closes = f'<strong style="color:{_SOON};">{closes}</strong>'
        days_left = f'<strong style="color:{_SOON};">{days_left}</strong>'

    notes = []
    matched = _e(r.matched_keywords)
    if matched:
        notes.append(f"Matched: {matched}")
    blockers = _e(r.blockers)
    if blockers:
        notes.append(f"Check: {blockers}")
    if r.briefing_compulsory is True and "briefing" not in blockers.lower():
        notes.append("Compulsory briefing")
    title = _e(r.title)
    if notes:
        title += f"<br><span{_SMALL}>{' · '.join(notes)}</span>"

    document = _link(r.document_urls[0], "View document") if r.document_urls else f"<span{_SMALL}>none</span>"

    number = _e(r.tender_number) or _e(r.ocid)
    return (f'<tr valign="top">'
            f"<td{_TD}>{'<br>'.join(flags)}</td>"
            f"<td{_TD}>{closes}</td>"
            f"<td{_TD}>{days_left}</td>"
            f'<th scope="row" style="border-bottom:1px solid {_RULE};text-align:left;'
            f'font-weight:normal;font-family:{_MONO};">{number}</th>'
            f"<td{_TD}>{_e(r.buyer)}</td>"
            f"<td{_TD}>{title}</td>"
            f"<td{_TD}>{_e(r.category)}</td>"
            f"<td{_TD}>{document}</td></tr>")


_HEADERS = ("Flags", "Closes (SAST)", "Days left", "Tender #", "Buyer", "Title",
            "Category", "Document")


def _group(label: str, rows: pd.DataFrame, run_year: int) -> str:
    if rows.empty:
        return ""
    head = (f'<tr><th scope="rowgroup" colspan="{len(_HEADERS)}" style="text-align:left;'
            f'background-color:{_INFO_BG};border-bottom:1px solid {_RULE};">'
            f"{_e(label)} ({_e(len(rows))})</th></tr>")
    return "<tbody>" + head + "".join(_row(r, run_year) for r in rows.itertuples(index=False)) + "</tbody>"


def _table(d: Digest, shown: pd.DataFrame) -> str:
    run_year = d.run_at.astimezone(DISPLAY_TZ).year
    new_rows = shown[shown["is_new"]]
    other_rows = shown[~shown["is_new"]]
    if d.memory_status == "unavailable":
        other_label = "New or seen before: unknown this run"
    else:
        other_label = "In an earlier digest"
    header = "".join(f'<th scope="col" style="text-align:left;color:{_MUTED};'
                     f'font-size:{_SIZE_SMALL};border-bottom:2px solid {_INK};">{h}</th>'
                     for h in _HEADERS)
    return (f'<table width="100%" cellpadding="6" cellspacing="0" border="0" '
            f'style="border-collapse:collapse;font-family:{_SANS};font-size:{_SIZE_TABLE};'
            f'color:{_INK};line-height:1.35;">'
            f'<caption style="text-align:left;color:{_MUTED};font-size:{_SIZE_SMALL};'
            f'padding:0 0 8px;">Open tenders in digest order: new first, then by keyword '
            f"score, then by closing date. Times are SAST (UTC+2).</caption>"
            f"<thead><tr>{header}</tr></thead>"
            + _group("New", new_rows, run_year)
            + _group(other_label, other_rows, run_year)
            + "</table>")


def _footer(d: Digest) -> list[str]:
    lines = []
    if d.heartbeat != "on":
        line = "Heartbeat monitoring is off."
        if d.heartbeat == "misconfigured":
            line += " HEARTBEAT_URL is set but is not an https://hc-ping.com/ address, so it is ignored."
        else:
            line += " If a run fails without sending any email, nothing will alert you."
        lines.append(_e(line))
    if d.dropped_releases > 0:
        lines.append(_e(f"{int(d.dropped_releases)} malformed release(s) from the eTenders API "
                        "were left out."))
    if d.skipped_releases > 0:
        lines.append(_e(f"{int(d.skipped_releases)} tender record(s) could not be read because the "
                        "eTenders API returns an error for them. They are left out, so a tender may "
                        f"be missing: search {_ETENDERS} by hand if you suspect a gap."))
    if d.ai_rejected is not None:
        flags = set(d.tenders["ai_flag"].dropna())
        lines.append("Tenders are chosen by an AI model (Llama 3.3 70B) against your data profile.")
        if "nano" in flags:
            lines.append("AI: nano marks tenders judged by the fallback model, which was less "
                         "accurate in testing. Check them more carefully.")
        if NOT_AI_CHECKED in flags:
            lines.append("NOT AI-CHECKED marks tenders kept on a keyword match only, because the "
                         "AI was unavailable for them this run.")
        if len(d.ai_rejected):
            lines.append(_e(f"{len(d.ai_rejected)} keyword match(es) the AI rejected are on the "
                            f'xlsx sheet "{_REJECTED_SHEET}".'))
    if d.total:
        lines.append("The attached xlsx has every tender with all columns, including province, "
                     "contact and published date.")
    return lines


def render_html(d: Digest) -> str:
    """The email body: summary, banners, table, footer (API_CONTRACT 2.6)."""
    df = d.tenders.copy()  # never mutate the backend's frame
    new = "new unknown" if d.new_count is None else f"{int(d.new_count)} new"
    summary = (f"{int(d.total)} tender(s) · {new} · {int(d.closing_soon_count)} closing "
               f"within {int(d.closing_soon_days)} days")
    run = d.run_at.astimezone(DISPLAY_TZ)
    context = (f"Publication days {d.window[0].isoformat()} to {d.window[1].isoformat()} · "
               f"run {_day_text(run)} {run.year} {run.strftime('%H:%M')} SAST")

    parts = [
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="color-scheme" content="light"><title>eTenders digest</title></head>',
        f'<body style="margin:0;padding:0;background-color:{_PAGE};">',
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="background-color:{_PAGE};"><tr><td align="center" style="padding:16px 8px;">',
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="max-width:1080px;background-color:{_PAPER};border:1px solid {_RULE};'
        f'font-family:{_SANS};color:{_INK};text-align:left;">',
        f'<tr><td style="padding:24px 16px 16px;">'
        f'<h1 style="margin:0 0 4px;font-size:{_SIZE_TITLE};line-height:1.3;">{_e(summary)}</h1>'
        f'<p style="margin:0;color:{_MUTED};font-size:{_SIZE_SMALL};">{_e(context)}</p></td></tr>',
    ]
    parts += _banners(d)

    if d.total == 0:
        body = (f'<p style="margin:0;font-size:{_SIZE_BODY};">'
                "No matching tenders were found in this run.</p>")
    else:
        shown = _shown_rows(df)
        body = _table(d, shown)
        hidden = len(df) - len(shown)
        if hidden:
            body += (f'<p style="margin:12px 0 0;font-size:{_SIZE_BODY};">'
                     f"{_e(hidden)} more tender(s) in the attached xlsx only.</p>")
    parts.append(f'<tr><td style="padding:0 16px 16px;">{body}</td></tr>')

    footer = "<br>".join(_footer(d))
    parts.append(f'<tr><td style="padding:12px 16px 16px;border-top:1px solid {_RULE};'
                 f'color:{_MUTED};font-size:{_SIZE_SMALL};line-height:1.5;">{footer}</td></tr>')
    parts.append("</table></td></tr></table></body></html>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# xlsx
# ---------------------------------------------------------------------------

# (header, width in characters). Triage columns first; reference columns last.
_XLSX_COLUMNS = (
    ("new", 7), ("closing_soon", 13), ("days_left", 10), ("closing", 17),
    ("tender_number", 18), ("title", 60), ("buyer", 36), ("category", 26),
    ("province", 16), ("matched_keywords", 28), ("ai_flag", 15), ("blockers", 34),
    ("briefing_compulsory", 20), ("method", 24), ("contact", 30), ("published", 17),
    ("score", 7), ("document", 50), ("closing_raw", 26), ("ocid", 30),
)
_DATE_FORMAT = "yyyy-mm-dd hh:mm"
# A cell starting with one of these could be read as a formula by Excel
# (AC-21a); a tab or CR is how payloads hide the "=" from a naive check.
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _text(x):
    """API text for a cell: None for null or "", with xlsx-illegal characters removed."""
    if pd.isna(x) or x == "":
        return None
    return strip_unwritable(str(x)) or None


def _naive_sast(ts):
    """openpyxl can't store tz-aware datetimes, so write SAST wall-clock time."""
    t = _sast(ts)
    return None if t is None else t.tz_localize(None).to_pydatetime()


def _xlsx_values(r, new_known: bool) -> list:
    briefing = r.briefing_compulsory if isinstance(r.briefing_compulsory, bool) else None
    return [
        bool(r.is_new) if new_known else None,  # blank, not FALSE, when marking was unavailable
        bool(r.closing_soon),
        None if pd.isna(r.days_left) else int(r.days_left),
        _naive_sast(r.closing),
        _text(r.tender_number), _text(r.title), _text(r.buyer), _text(r.category),
        _text(r.province), _text(r.matched_keywords), _text(r.ai_flag), _text(r.blockers),
        briefing,
        _text(r.method), _text(r.contact),
        _naive_sast(r.published),
        int(r.score),
        r.document_urls[0] if r.document_urls else None,
        _text(r.closing_raw), _text(r.ocid),
    ]


def _neutralise_formulas(wb) -> None:
    """Store every formula-looking value as plain text, on every sheet (AC-21a).
    quotePrefix also stops Excel turning it into a formula if the cell is edited;
    the value itself is unchanged (no ' or space is added)."""
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if cell.data_type == "f" or (
                        isinstance(cell.value, str) and cell.value.startswith(_FORMULA_START)):
                    cell.data_type = "s"
                    cell.quotePrefix = True


_REJECTED_SHEET = "Keyword match, AI said no"


def build_xlsx(d: Digest) -> bytes | None:
    """Sheet "Tenders": every row in digest order. When the AI step ran, a
    second sheet lists the keyword hits it rejected. None iff both are empty."""
    rejected = d.ai_rejected if d.ai_rejected is not None else d.tenders.iloc[0:0]
    if d.total == 0 and rejected.empty:
        return None
    new_known = d.memory_status != "unavailable"

    wb = Workbook()
    ws = wb.active
    ws.title = "Tenders"
    _write_sheet(ws, d.tenders, new_known)
    if d.ai_rejected is not None:
        _write_sheet(wb.create_sheet(_REJECTED_SHEET), rejected, new_known)
    _neutralise_formulas(wb)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _write_sheet(ws, df: pd.DataFrame, new_known: bool) -> None:
    ws.append([name for name, _ in _XLSX_COLUMNS])
    for r in df.itertuples(index=False):
        ws.append(_xlsx_values(r, new_known))

    header_fill = PatternFill("solid", fgColor=_INFO_BG[1:])
    new_fill = PatternFill("solid", fgColor="D9ECEE")
    soon_fill = PatternFill("solid", fgColor="FBE4D8")
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = header_fill
    col = {}
    for i, (name, width) in enumerate(_XLSX_COLUMNS, start=1):
        col[name] = i
        ws.column_dimensions[get_column_letter(i)].width = width
    for row in ws.iter_rows(min_row=2):
        if row[col["new"] - 1].value is True:
            row[col["new"] - 1].fill = new_fill
            row[col["new"] - 1].font = Font(bold=True, color=_NEW_BG[1:])
        if row[col["closing_soon"] - 1].value is True:
            for name in ("closing_soon", "days_left", "closing"):
                row[col[name] - 1].fill = soon_fill
                row[col[name] - 1].font = Font(bold=True, color=_SOON[1:])
        for name in ("closing", "published"):
            row[col[name] - 1].number_format = _DATE_FORMAT
        doc = row[col["document"] - 1]
        if is_safe_url(doc.value):  # checked again at the sink (AC-21b)
            doc.hyperlink = doc.value
            doc.style = "Hyperlink"

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
