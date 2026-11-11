"""What the Cash view keeps, as data. These labels are the site's own, not private.

Labels are compared ignoring case and outer spaces, because the export really does hold some
with a trailing space, such as the journal code for a check.
"""

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

Row = dict[str, Any]


@dataclass(frozen=True)
class Removal:
    """Removes a row where every `where` column matches and no `unless` column does."""

    name: str
    where: Mapping[str, str]
    unless: Mapping[str, str] = field(default_factory=dict)


REMOVALS = (
    Removal("Asset", {"GLAccountTypeName": "Asset"}),
    Removal("Convenience fee", {"GLAccountNameRaw": "Convenience Fee"}),
    Removal("Applied deposit", {"JournalCodeDescription": "Applied Deposit"}),
    Removal("Deposit refund", {"JournalCodeDescription": "Refund"},
            unless={"GLAccountTypeName": "Income"}),
    Removal("Owner draw by check", {"GLAccountName": "Owner Draw",
                                    "JournalCodeDescription": "Check"}),
    Removal("Opening balance equity", {"GLAccountName": "Opening Balance Equity"}),
    Removal("Opening security credits", {"GLAccountTypeName": "Liability",
                                         "JournalCodeDescription": "Credit"}),
)
DEPOSIT_TRANSFER = "Deposit Transfer"
DEPOSIT_TRANSFER_TYPE = "Equity"


@dataclass(frozen=True)
class CashView:
    rows: list[Row]
    removed: Counter[str]
    deposit_transfers: int


def same_label(a: str, b: str) -> bool:
    return a.strip().casefold() == b.strip().casefold()


def _matches(row: Row, removal: Removal) -> bool:
    return (all(same_label(row[column], label) for column, label in removal.where.items())
            and not any(same_label(row[column], label)
                        for column, label in removal.unless.items()))


def _is_deposit_transfer(row: Row) -> bool:
    return same_label(row["GLAccountNameRaw"], DEPOSIT_TRANSFER) and bool(row["PayeeName"].strip())


def cash_view(rows: Sequence[Row]) -> CashView:
    """The entity tab's rows: type fix, then every amount's sign flipped, then the removals.

    The order matters: a Deposit Transfer on an Asset account becomes Equity first, so the Asset
    removal keeps it. A removed row counts under the first rule that matched. The input rows
    don't change, and they stay in their order.
    """
    kept: list[Row] = []
    removed: Counter[str] = Counter()
    transfers = 0
    for original in rows:
        row = dict(original)
        if _is_deposit_transfer(row):
            row["GLAccountTypeName"] = DEPOSIT_TRANSFER_TYPE
            transfers += 1
        row["Amount"] = -row["Amount"]
        rule = next((removal for removal in REMOVALS if _matches(row, removal)), None)
        if rule is None:
            kept.append(row)
        else:
            removed[rule.name] += 1
    return CashView(kept, removed, transfers)
