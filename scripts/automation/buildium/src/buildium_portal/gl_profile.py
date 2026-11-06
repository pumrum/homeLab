"""--dump: describe a general ledger export without repeating anything private from it.

Every value is reduced to its shape, with digits as 9 and letters as a, so the result can be
pasted into a conversation. Only the column names the code already knows and the generic
labels below appear as words. This is what the parser is checked against.
"""

from collections import Counter

from buildium_portal.gl_parse import EXPECTED_COLUMNS, decode, read_records

_MAX_SHAPE = 40
_TOP_SHAPES = 3
_SAMPLE_ROWS = 2
_SAMPLE_BLANK_ID_ROWS = 3
# The generic words the row rules look for: how often each appears, and in which case
_LABELS = {
    "GLAccountTypeName": ("Asset", "Equity", "Income", "Liability"),
    "GLAccountName": ("Opening Balance Equity", "Owner Draw"),
    "GLAccountNameRaw": ("Convenience Fee", "Deposit Transfer"),
    "JournalCodeDescription": ("Applied Deposit", "Check", "Credit", "Refund"),
}


def shape(value: str) -> str:
    """A value's form with its content removed: "-1,234.50" is "-9,999.99"."""
    masked = "".join("9" if c.isdigit() else "a" if c.isalpha() else c for c in value)
    masked = masked.replace("\r", "\\r").replace("\n", "\\n")
    return masked if len(masked) <= _MAX_SHAPE else f"{masked[:_MAX_SHAPE]}..."


def _bytes_section(raw: bytes, encoding: str, record_count: int) -> list[str]:
    crlf = raw.count(b"\r\n")
    return [
        f"bytes: {len(raw)}",
        f"byte-order mark: {'yes' if raw.startswith(b'\xef\xbb\xbf') else 'no'}",
        f"decoded as: {encoding}",
        f"line endings: CRLF {crlf}, LF {raw.count(b'\n') - crlf}, "
        f"CR {raw.count(b'\r') - crlf}",
        f"records, with the header: {record_count}",
    ]


def _header_section(header: list[str]) -> list[str]:
    known = {name.casefold(): name for name in EXPECTED_COLUMNS}
    found = {name.casefold() for name in header}
    missing = [name for name in EXPECTED_COLUMNS if name.casefold() not in found]
    unknown = [shape(name) for name in header if name.strip().casefold() not in known]
    case_only = [name for name in header
                 if name.casefold() in known and name != known[name.casefold()]]
    same_order = [name.casefold() for name in header] == [
        name.casefold() for name in EXPECTED_COLUMNS]
    return [
        "",
        f"columns: {len(header)} (expected {len(EXPECTED_COLUMNS)})",
        f"same names in the same order, ignoring case: {'yes' if same_order else 'no'}",
        f"missing: {', '.join(missing) or 'none'}",
        f"not expected, as shapes: {', '.join(unknown) or 'none'}",
        f"different case from expected: {', '.join(case_only) or 'none'}",
    ]


def _name(header: list[str], index: int) -> str:
    known = {name.casefold() for name in EXPECTED_COLUMNS}
    if index < len(header) and header[index].casefold() in known:
        return header[index]
    return f"column {index + 1}"


def _rows_section(header: list[str], rows: list[list[str]]) -> list[str]:
    lowered = [name.casefold() for name in header]
    id_at = lowered.index("id") if "id" in lowered else None
    posting_at = lowered.index("postingid") if "postingid" in lowered else None
    lines = [
        "",
        f"rows: {len(rows)}",
        f"rows with a different number of fields: "
        f"{sum(1 for row in rows if len(row) != len(header))}",
    ]
    if id_at is None:
        return [*lines, "no Id column, so blank Id rows can't be counted"]
    lines.append(f"rows with a blank Id: {sum(1 for row in rows if not _cell(row, id_at).strip())}")
    if posting_at is not None:
        keys = Counter((_cell(row, id_at), _cell(row, posting_at)) for row in rows
                       if _cell(row, id_at).strip())
        lines.append(f"Id and PostingId pairs seen more than once: "
                     f"{sum(1 for count in keys.values() if count > 1)}")
    return lines


def _cell(row: list[str], index: int) -> str:
    return row[index] if index < len(row) else ""


def _columns_section(header: list[str], rows: list[list[str]]) -> list[str]:
    lines = ["", "column | blank | distinct shapes | most common shapes"]
    for index in range(len(header)):
        values = [_cell(row, index) for row in rows]
        shapes = Counter(shape(value) for value in values if value.strip())
        top = "  ".join(f"[{found}] x{count}" for found, count in shapes.most_common(_TOP_SHAPES))
        blank = sum(1 for value in values if not value.strip())
        lines.append(f"{_name(header, index)} | {blank} | {len(shapes)} | {top}")
    return lines


def _labels_section(header: list[str], rows: list[list[str]]) -> list[str]:
    lowered = [name.casefold() for name in header]
    lines = ["", "column | label | exact matches | matches ignoring case and outer spaces"]
    for column, labels in _LABELS.items():
        if column.casefold() not in lowered:
            continue
        index = lowered.index(column.casefold())
        values = [_cell(row, index) for row in rows]
        for label in labels:
            exact = sum(1 for value in values if value == label)
            loose = sum(1 for value in values if value.strip().casefold() == label.casefold())
            lines.append(f"{column} | {label} | {exact} | {loose}")
    return lines


def _masked_row(header: list[str], row: list[str]) -> str:
    return "; ".join(f"{_name(header, index)}=[{shape(value)}]"
                     for index, value in enumerate(row) if value.strip())


def _samples_section(header: list[str], rows: list[list[str]]) -> list[str]:
    lowered = [name.casefold() for name in header]
    id_at = lowered.index("id") if "id" in lowered else 0
    blank = [row for row in rows if not _cell(row, id_at).strip()]
    filled = [row for row in rows if _cell(row, id_at).strip()]
    lines = ["", "first rows with an Id, as shapes (blank fields left out):"]
    lines += [_masked_row(header, row) for row in filled[:_SAMPLE_ROWS]]
    lines += ["", "first rows with a blank Id, as shapes (blank fields left out):"]
    lines += [_masked_row(header, row) for row in blank[:_SAMPLE_BLANK_ID_ROWS]]
    return lines


def profile(raw: bytes) -> str:
    """The text report for one export, safe to share."""
    text, encoding = decode(raw)
    records = read_records(text)
    lines = _bytes_section(raw, encoding, len(records))
    if not records:
        return "\n".join([*lines, "", "the export is empty"]) + "\n"
    header, rows = records[0], records[1:]
    lines += _header_section(header)
    lines += _rows_section(header, rows)
    lines += _columns_section(header, rows)
    lines += _labels_section(header, rows)
    lines += _samples_section(header, rows)
    return "\n".join(lines) + "\n"
