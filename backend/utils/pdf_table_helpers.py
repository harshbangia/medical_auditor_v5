"""ReportLab table helpers for tabular audit PDF sections."""

from typing import List, Optional, Sequence, Tuple

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import Paragraph, Spacer, Table, TableStyle


def _cell(text: str, style: ParagraphStyle) -> Paragraph:
    safe = (
        str(text or "-")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    return Paragraph(safe or "-", style)


def kv_table(
    rows: Sequence[Tuple[str, str]],
    col_widths: Optional[Sequence[float]] = None,
    header: bool = True,
) -> Table:
    """Two-column key | value table."""
    styles = getSampleStyleSheet()
    key_style = ParagraphStyle(
        "TableKey",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=11,
    )
    val_style = ParagraphStyle(
        "TableVal",
        parent=styles["Normal"],
        fontSize=9,
        leading=11,
    )

    data: List[list] = []
    if header:
        data.append([_cell("Field", key_style), _cell("Value", key_style)])
    for key, val in rows:
        if not str(val or "").strip() and not str(key or "").strip():
            continue
        data.append([_cell(key, key_style), _cell(val, val_style)])

    if len(data) <= (1 if header else 0):
        data = [[_cell("—", val_style), _cell("Not documented", val_style)]]

    table = Table(data, colWidths=col_widths or [160, 340], repeatRows=1 if header else 0)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EEF4")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#1A1A1A")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#B0BEC5")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return table


def data_table(
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    col_widths: Optional[Sequence[float]] = None,
) -> Table:
    """Multi-column data table with header row."""
    styles = getSampleStyleSheet()
    hdr_style = ParagraphStyle(
        "HdrCell",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
    )
    cell_style = ParagraphStyle(
        "DataCell",
        parent=styles["Normal"],
        fontSize=8,
        leading=10,
    )

    data = [[_cell(h, hdr_style) for h in headers]]
    for row in rows:
        data.append([_cell(c, cell_style) for c in row])

    if len(data) == 1:
        data.append([_cell("—", cell_style) for _ in headers])

    n = len(headers)
    if col_widths is None:
        total = 500
        col_widths = [total / n] * n

    table = Table(data, colWidths=list(col_widths), repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EEF4")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#B0BEC5")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return table


def section(title: str, styles) -> list:
    """Heading + spacer for a report section."""
    return [Paragraph(title, styles["Heading2"]), Spacer(1, 6)]
