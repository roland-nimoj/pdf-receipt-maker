#!/usr/bin/env python3
"""
Генератор PDF-чеков из CSV/JSON данных и HTML-шаблонов.

Зависимости:
    pip install pandas weasyprint
"""

import json
import os
import platform
import subprocess
import sys
from pathlib import Path

import pandas as pd
from weasyprint import HTML


# ---------------------------------------------------------------------------
# Пути и папки
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
TEMPLATES_DIR = BASE_DIR / "templates"
OUTPUT_DIR = BASE_DIR / "output"

for _d in (DATA_DIR, TEMPLATES_DIR, OUTPUT_DIR):
    _d.mkdir(parents=True, exist_ok=True)


# Стиль по умолчанию — обеспечивает поддержку кириллицы (DejaVu Sans идёт
# в комплекте с WeasyPrint на большинстве систем; Roboto — как фолбэк).
DEFAULT_STYLE = """<style>
    body, table, td, th, div, span, p {
        font-family: 'DejaVu Sans', 'Roboto', 'Arial', sans-serif;
    }
    table { border-collapse: collapse; width: 100%; }
    th, td { border: 1px solid #444; padding: 6px 10px; text-align: left; }
    th { background: #f0f0f0; }
</style>
"""


# ---------------------------------------------------------------------------
# Обнаружение файлов
# ---------------------------------------------------------------------------
def list_data_files():
    files = []
    for ext in ("*.csv", "*.json"):
        files.extend(DATA_DIR.glob(ext))
    return sorted(files)


def list_templates():
    return sorted(TEMPLATES_DIR.glob("*.html"))


# ---------------------------------------------------------------------------
# Загрузка и нормализация данных
# ---------------------------------------------------------------------------
def _flat_record(d):
    return {
        "invoice_id": d.get("invoice_id"),
        "customer_name": d.get("customer_name"),
        "date": d.get("date"),
        "product": d.get("product"),
        "price": d.get("price"),
        "qty": d.get("qty"),
    }


def _normalize_json(data):
    """Приводим любой из двух поддерживаемых видов JSON к плоскому списку."""
    if isinstance(data, dict):
        # На всякий случай: если корень — объект с одним ключом-списком
        for key in ("data", "invoices", "records", "items"):
            if key in data and isinstance(data[key], list):
                data = data[key]
                break
        else:
            data = [data]

    if not isinstance(data, list):
        raise ValueError("JSON должен содержать список записей или чеков.")

    flat = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        if isinstance(entry.get("items"), list):
            # Вид (б): чек с вложенными позициями
            invoice_id = entry.get("invoice_id")
            customer_name = entry.get("customer_name")
            date = entry.get("date")
            for it in entry["items"]:
                flat.append({
                    "invoice_id": invoice_id,
                    "customer_name": customer_name,
                    "date": date,
                    "product": it.get("product"),
                    "price": it.get("price"),
                    "qty": it.get("qty"),
                })
        else:
            # Вид (а): плоская запись
            flat.append(_flat_record(entry))
    return flat


def load_data(path: Path):
    suffix = path.suffix.lower()
    if suffix == ".csv":
        df = pd.read_csv(path)
        return [_flat_record(r) for r in df.to_dict(orient="records")]
    if suffix == ".json":
        with open(path, "r", encoding="utf-8") as f:
            return _normalize_json(json.load(f))
    raise ValueError(f"Неподдерживаемый формат: {suffix}")


# ---------------------------------------------------------------------------
# Консольное меню
# ---------------------------------------------------------------------------
def print_numbered(items, title, formatter=None):
    print(f"\n{title}")
    if not items:
        print("  (пусто)")
        return
    for i, item in enumerate(items, 1):
        label = formatter(item) if formatter else str(item)
        print(f"  {i}. {label}")


def choose(items, prompt):
    while True:
        raw = input(prompt).strip()
        if not raw:
            continue
        try:
            idx = int(raw)
        except ValueError:
            print("  ⚠ Введите число.")
            continue
        if 1 <= idx <= len(items):
            return items[idx - 1]
        print(f"  ⚠ Допустимо от 1 до {len(items)}.")


# ---------------------------------------------------------------------------
# Рендеринг
# ---------------------------------------------------------------------------
def _fmt_number(value, digits=2):
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return ""


def build_rows_html(records):
    parts = []
    for r in records:
        price = r.get("price") or 0
        qty = r.get("qty") or 0
        try:
            line_total = float(price) * float(qty)
        except (TypeError, ValueError):
            line_total = 0.0
        parts.append(
            "<tr>"
            f"<td>{r.get('product') or ''}</td>"
            f"<td>{_fmt_number(price)}</td>"
            f"<td>{qty}</td>"
            f"<td>{line_total:.2f}</td>"
            "</tr>"
        )
    return "\n".join(parts)


def build_total(records):
    total = 0.0
    for r in records:
        try:
            total += float(r.get("price") or 0) * float(r.get("qty") or 0)
        except (TypeError, ValueError):
            continue
    return f"{total:.2f}"


def inject_default_style(html: str) -> str:
    if "</head>" in html:
        return html.replace("</head>", DEFAULT_STYLE + "</head>", 1)
    return DEFAULT_STYLE + html


def render_template(template: str, records, invoice_id: str) -> str:
    """Подстановка плейсхолдеров через str.replace (без Jinja2)."""
    first = records[0] if records else {}
    values = {
        "invoice_id": str(invoice_id),
        "customer_name": str(first.get("customer_name") or ""),
        "date": str(first.get("date") or ""),
        "rows": build_rows_html(records),
        "total": build_total(records),
    }

    # Приводим плейсхолдеры к единому виду: {{key}} без пробелов
    html = template.replace("{{ ", "{{").replace(" }}", "}}")
    for key, value in values.items():
        html = html.replace("{{" + key + "}}", value)

    return inject_default_style(html)


# ---------------------------------------------------------------------------
# Открытие PDF в системной программе
# ---------------------------------------------------------------------------
def open_in_system(path: Path):
    system = platform.system()
    try:
        if system == "Windows":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif system == "Darwin":
            subprocess.run(["open", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
    except Exception as e:
        print(f"  ⚠ Не удалось открыть PDF автоматически: {e}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main():
    print("=" * 60)
    print("  Генератор PDF-чеков")
    print("=" * 60)
    print(f"  data      → {DATA_DIR}")
    print(f"  templates → {TEMPLATES_DIR}")
    print(f"  output    → {OUTPUT_DIR}")

    data_files = list_data_files()
    templates = list_templates()

    print_numbered(data_files, "Доступные файлы данных:", lambda p: p.name)
    print_numbered(templates, "Доступные HTML-шаблоны:", lambda p: p.name)

    if not data_files:
        print("\n❌ Нет файлов данных в data/. Добавьте CSV или JSON и перезапустите.")
        return
    if not templates:
        print("\n❌ Нет HTML-шаблонов в templates/. Добавьте хотя бы один .html.")
        return

    data_path = choose(data_files, "\nВыберите файл данных (номер): ")
    template_path = choose(templates, "Выберите HTML-шаблон (номер): ")

    try:
        records = load_data(data_path)
    except Exception as e:
        print(f"\n❌ Ошибка чтения {data_path.name}: {e}")
        return

    # Группируем по invoice_id
    invoices = {}
    for r in records:
        iid = r.get("invoice_id")
        if iid is None:
            continue
        invoices.setdefault(iid, []).append(r)

    if not invoices:
        print("\n❌ В файле нет ни одного invoice_id.")
        return

    invoice_ids = list(invoices.keys())
    print_numbered(
        invoice_ids,
        "Доступные чеки (invoice_id):",
        lambda iid: f"{iid} — {invoices[iid][0].get('customer_name') or ''}",
    )

    invoice_id = choose(invoice_ids, "\nВыберите invoice_id (номер): ")

    try:
        template_str = template_path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"\n❌ Не удалось прочитать шаблон: {e}")
        return

    html = render_template(template_str, invoices[invoice_id], invoice_id)

    out_path = OUTPUT_DIR / f"invoice_{invoice_id}.pdf"
    try:
        HTML(string=html, base_url=str(BASE_DIR)).write_pdf(str(out_path))
    except Exception as e:
        print(f"\n❌ Ошибка генерации PDF: {e}")
        return

    print(f"\n✅ PDF сохранён: {out_path}")
    open_in_system(out_path)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nПрервано пользователем.")
        sys.exit(1)