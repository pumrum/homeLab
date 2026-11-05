"""Turn a downloaded file into the lines to paste. Pure: no network, no I/O.

Each file is a flat list, one value per line, with no header. Lines are kept
exactly as they are, including blank lines inside the list. The only checks are
the cheap ones that stop a bad file from replacing a good block.
"""


class ParseError(Exception):
    """The file can't be used. The message says why, without repeating its contents."""


def parse_lines(raw: bytes) -> list[str]:
    """The file's lines. Accepts Windows or Linux line endings and a byte-order mark."""
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ParseError("the file is not valid UTF-8") from None
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        raise ParseError("the file is empty")
    return lines
