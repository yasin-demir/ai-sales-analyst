"""Yerel CSV dosyası üzerinde basit ve denetlenebilir hesaplamalar."""

import csv
from decimal import Decimal, InvalidOperation
from pathlib import Path


def summarize_csv(path: Path) -> dict:
    """Satır sayısını, satışları ve varsa kârı hesapla."""
    try:
        return _read_summary(path, "utf-8-sig")
    except UnicodeDecodeError:
        return _read_summary(path, "cp1252")


def _read_summary(path: Path, encoding: str) -> dict:
    with path.open(newline="", encoding=encoding) as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "Sales" not in reader.fieldnames:
            raise ValueError("CSV içinde 'Sales' sütunu bulunamadı. Dosya başlıklarını kontrol edin.")
        has_profit = "Profit" in reader.fieldnames
        sales = Decimal("0")
        profit = Decimal("0")
        count = 0
        for row in reader:
            count += 1
            sales += _number(row.get("Sales"), reader.line_num, "Sales")
            if has_profit:
                profit += _number(row.get("Profit"), reader.line_num, "Profit")
    return {"rows": count, "sales": sales, "profit": profit if has_profit else None}


def _number(value: str | None, line: int, column: str) -> Decimal:
    try:
        number = Decimal((value or "").strip())
        if not number.is_finite():
            raise InvalidOperation
        return number
    except InvalidOperation as exc:
        raise ValueError(f"CSV satırı {line}: {column} sayısal değil: {value!r}") from exc
