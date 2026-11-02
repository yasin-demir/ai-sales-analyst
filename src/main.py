"""Proje kökünde çalıştırın: python -m src.main"""

import argparse
from pathlib import Path

from .analysis import summarize_csv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA = PROJECT_ROOT / "data" / "Sample - Superstore.csv"


def main() -> int:
    parser = argparse.ArgumentParser(description="Superstore satış özeti oluşturur.")
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA, help="CSV dosyasının yolu")
    args = parser.parse_args()
    if not args.data.is_file():
        print(f"Veri dosyası bulunamadı: {args.data}")
        print("Gerçek Sample - Superstore.csv dosyanızı data klasörüne koyun veya --data kullanın.")
        return 1
    try:
        summary = summarize_csv(args.data)
    except (OSError, ValueError, UnicodeError) as exc:
        print(f"Analiz yapılamadı: {exc}")
        return 1
    print(f"Satır sayısı: {summary['rows']:,}")
    print(f"Toplam satış: {summary['sales']:,.2f}")
    if summary["profit"] is not None:
        print(f"Toplam kâr: {summary['profit']:,.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
