"""Формирование отчётов Excel (.xlsx) из табличных данных 1С."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import urllib.parse
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

# Корень проекта: tools/excel_export.py → parents[1] = D:\rag_agent
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXPORT_DIR = PROJECT_ROOT / "exports"

# Локальный HTTP для скачивания из чата LM Studio (не example.com!)
# 8765 занят панелью настроек (panel/server.py)
_EXPORT_HTTP_PORT = int(os.environ.get("RAG_EXPORT_HTTP_PORT", "8766"))
_export_http_base: str | None = None
_export_http_lock = threading.Lock()
_export_http_started = False


def clean_summary_period_rows(sbp: Any) -> list[dict[str, Any]]:
    """summary_by_period → строки для Excel без служебных полей (groups_shown…)."""
    if not isinstance(sbp, list):
        return []
    skip = {
        "groups_shown",
        "groups_total",
        "error",
        "raw",
        "query",
    }
    out: list[dict[str, Any]] = []
    for p in sbp:
        if not isinstance(p, dict):
            continue
        item = {
            k: v
            for k, v in p.items()
            if k not in skip and not str(k).startswith("_")
        }
        if item:
            out.append(item)
    return out


def summary_sheet_worth_it(sbp: Any) -> bool:
    """
    Лист «Итоги» + pie/bar по периодам имеет смысл только при 2+ срезах.
    Один период «за все время» → одна строка, pie 100% — мусор.
    """
    rows = clean_summary_period_rows(sbp)
    if len(rows) < 2:
        return False
    periods = {
        str(r.get("Период") or r.get("period") or "").strip()
        for r in rows
    }
    periods.discard("")
    return len(periods) >= 2


def downloads_dir() -> Path:
    """Папка «Загрузки» пользователя (Windows / fallback home)."""
    home = Path.home()
    for candidate in (
        home / "Downloads",
        home / "Загрузки",
        Path(os.environ.get("USERPROFILE", "")) / "Downloads",
    ):
        if candidate and str(candidate) != "." and candidate.exists():
            return candidate
    d = home / "Downloads"
    d.mkdir(parents=True, exist_ok=True)
    return d


def file_uri(path: Path | str) -> str:
    """file:///D:/rag_agent/exports/x.xlsx — открывается в проводнике/браузере."""
    p = Path(path).resolve()
    return p.as_uri()


def export_base_url() -> str:
    """Базовый URL раздачи exports (запущенного сервера или порта по умолчанию)."""
    return _export_http_base or f"http://127.0.0.1:{_EXPORT_HTTP_PORT}"


def _is_our_export_server(base: str) -> bool:
    try:
        import urllib.request

        with urllib.request.urlopen(base + "/", timeout=1) as r:
            return r.headers.get("X-RAG-Exports") == "1"
    except Exception:
        return False


def start_export_http_server(
    export_dir: Path | None = None,
    port: int | None = None,
) -> str | None:
    """
    Фоновый HTTP на 127.0.0.1:port, раздаёт папку exports.
    Возвращает base URL или None.
    """
    global _export_http_base, _export_http_started
    with _export_http_lock:
        if _export_http_started and _export_http_base:
            return _export_http_base

        out_dir = Path(export_dir) if export_dir else DEFAULT_EXPORT_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        port = int(port or _EXPORT_HTTP_PORT)

        class _Handler(SimpleHTTPRequestHandler):
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                super().__init__(*args, directory=str(out_dir), **kwargs)

            def log_message(self, fmt: str, *args: Any) -> None:  # quieter
                return

            def end_headers(self) -> None:
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("X-RAG-Exports", "1")
                path = urllib.parse.unquote(self.path.split("?", 1)[0])
                if path.lower().endswith(".xlsx"):
                    name = Path(path).name
                    # Content-Disposition: только ASCII (latin-1) + RFC5987 filename*
                    ascii_name = re.sub(r"[^A-Za-z0-9._-]+", "_", name) or "report.xlsx"
                    if not ascii_name.lower().endswith(".xlsx"):
                        ascii_name += ".xlsx"
                    quoted = urllib.parse.quote(name)
                    self.send_header(
                        "Content-Disposition",
                        f"attachment; filename=\"{ascii_name}\"; "
                        f"filename*=UTF-8''{quoted}",
                    )
                    self.send_header(
                        "Content-Type",
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
                super().end_headers()

        server = None
        for p in range(port, port + 10):
            try:
                server = ThreadingHTTPServer(("127.0.0.1", p), _Handler)
                port = p
                break
            except OSError:
                # порт занят: свой сервер (другой процесс MCP) — переиспользуем, чужой — следующий порт
                base = f"http://127.0.0.1:{p}"
                if _is_our_export_server(base):
                    _export_http_base = base
                    _export_http_started = True
                    return base
        if server is None:
            return None

        def _run() -> None:
            try:
                server.serve_forever()
            except Exception:
                pass

        th = threading.Thread(target=_run, name="export-http", daemon=True)
        th.start()
        _export_http_base = f"http://127.0.0.1:{port}"
        _export_http_started = True
        return _export_http_base


def open_in_explorer(path: Path | str) -> dict[str, Any]:
    """Открыть файл или папку в проводнике Windows / xdg-open."""
    p = Path(path).resolve()
    try:
        if os.name == "nt":
            if p.is_file():
                # выделить файл в проводнике
                subprocess.Popen(["explorer", f"/select,{p}"])
            else:
                os.startfile(str(p))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(p if p.is_dir() else p.parent)])
        return {"ok": True, "opened": str(p)}
    except Exception as e:
        return {"ok": False, "error": str(e), "path": str(p)}


def enrich_export_meta(
    path: Path | str,
    *,
    copy_to_downloads: bool = True,
    open_folder: bool = False,
    ensure_http: bool = True,
) -> dict[str, Any]:
    """
    Добавить путь, file://, копию в Загрузки, http://127.0.0.1:….
    Проводник по умолчанию НЕ открывается — только ссылки/пути в ответе.
    """
    p = Path(path).resolve()
    meta: dict[str, Any] = {
        "path": str(p),
        "filename": p.name,
        "folder": str(p.parent),
        "file_uri": file_uri(p),
        # модели часто выдумывают example.com — явный запрет
        "prefer": (
            "Покажи пользователю: 1) path (локальный путь), "
            "2) download_url если есть (ТОЛЬКО из этого ответа, не example.com). "
            "Проводник сам НЕ открывать. НЕ выдумывай ссылки https://example.com."
        ),
        "how_to_open": (
            f"Файл на диске (не в интернете):\n"
            f"• Путь: {p}\n"
            f"• Папка: {p.parent}\n"
            f"• Скопируйте path или откройте download_url в браузере"
        ),
    }

    if copy_to_downloads:
        try:
            dest_dir = downloads_dir()
            dest = dest_dir / p.name
            if dest.exists():
                stamp = datetime.now().strftime("%H%M%S")
                dest = dest_dir / f"{p.stem}_{stamp}{p.suffix}"
            shutil.copy2(p, dest)
            meta["downloads_path"] = str(dest.resolve())
            meta["downloads_folder"] = str(dest_dir.resolve())
            meta["how_to_open"] += f"\n• Копия в Загрузки: {dest}"
        except Exception as e:
            meta["downloads_copy_error"] = str(e)

    if ensure_http:
        base = start_export_http_server(p.parent)
        if base:
            # URL-encode кириллицу в имени файла
            quoted = urllib.parse.quote(p.name)
            meta["download_url"] = f"{base}/{quoted}"
            meta["download_base"] = base
            meta["how_to_open"] += (
                f"\n• Скачать в браузере (локально): {meta['download_url']}"
            )

    if open_folder:
        opened = open_in_explorer(p)
        meta["explorer_opened"] = bool(opened.get("ok"))
        if not opened.get("ok"):
            meta["explorer_error"] = opened.get("error")

    return meta


def _safe_filename(name: str) -> str:
    name = (name or "report").strip()
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = re.sub(r"\s+", "_", name)
    name = name.strip("._") or "report"
    if not name.lower().endswith(".xlsx"):
        name += ".xlsx"
    return name[:120]


def _safe_sheet_name(name: str) -> str:
    name = (name or "Отчёт").strip()
    name = re.sub(r'[:\\/?*\[\]]', "_", name)
    return (name[:31] or "Отчёт")


def _polish_sheet(
    ws: Any,
    *,
    tab_color: str | None = "1F4E79",
    landscape: bool = True,
    hide_grid: bool = False,
) -> None:
    """Печать A4, цвет вкладки, без лишней сетки на дашборде."""
    try:
        if tab_color:
            ws.sheet_properties.tabColor = tab_color
        ws.page_setup.orientation = "landscape" if landscape else "portrait"
        ws.page_setup.paperSize = 9  # A4
        ws.page_setup.fitToPage = True
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.page_setup.horizontalCentered = True
        ws.page_margins.left = 0.4
        ws.page_margins.right = 0.4
        ws.page_margins.top = 0.5
        ws.page_margins.bottom = 0.5
        ws.sheet_view.showGridLines = not hide_grid
        if hide_grid:
            ws.sheet_view.showGridLines = False
    except Exception:
        pass


def rows_to_workbook(
    rows: list[dict[str, Any]],
    *,
    title: str = "",
    sheet_name: str = "Отчёт",
) -> Any:
    """Создаёт openpyxl Workbook из списка словарей."""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError as e:
        raise RuntimeError(
            "Нужен пакет openpyxl: pip install openpyxl"
        ) from e

    wb = Workbook()
    ws = wb.active
    ws.title = _safe_sheet_name(sheet_name)

    if not rows:
        ws["A1"] = title or "Нет данных"
        return wb

    # Единый порядок колонок: ключи первой строки + остальные
    columns: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        for k in row.keys():
            key = str(k)
            if key not in seen:
                seen.add(key)
                columns.append(key)

    if not columns:
        ws["A1"] = title or "Нет колонок"
        return wb

    start_row = 1
    if title:
        ws.merge_cells(
            start_row=1,
            start_column=1,
            end_row=1,
            end_column=max(1, len(columns)),
        )
        cell = ws.cell(1, 1, title)
        cell.font = Font(bold=True, size=14)
        cell.alignment = Alignment(horizontal="left", vertical="center")
        start_row = 3

    header_fill = PatternFill("solid", fgColor="1F4E79")
    header_font = Font(bold=True, color="FFFFFF")
    for col_idx, col_name in enumerate(columns, start=1):
        cell = ws.cell(start_row, col_idx, col_name)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", wrap_text=True)

    for r_idx, row in enumerate(rows, start=start_row + 1):
        if not isinstance(row, dict):
            continue
        for c_idx, col_name in enumerate(columns, start=1):
            val = row.get(col_name)
            if val is None:
                cell_val: Any = ""
            elif isinstance(val, (int, float, bool)):
                cell_val = val
            else:
                cell_val = str(val)
            ws.cell(r_idx, c_idx, cell_val)

    # ширина колонок
    for col_idx, col_name in enumerate(columns, start=1):
        max_len = len(str(col_name))
        for row in rows[:200]:
            if isinstance(row, dict):
                max_len = max(max_len, len(str(row.get(col_name, ""))[:80]))
        ws.column_dimensions[get_column_letter(col_idx)].width = min(max(10, max_len + 2), 40)

    ws.auto_filter.ref = (
        f"A{start_row}:{get_column_letter(len(columns))}{start_row + len(rows)}"
    )
    ws.freeze_panes = ws.cell(start_row + 1, 1).coordinate

    _polish_sheet(ws, tab_color="1F4E79", landscape=True)
    return wb


def save_rows_excel(
    rows: list[dict[str, Any]],
    *,
    filename: str = "",
    title: str = "",
    sheet_name: str = "Отчёт",
    export_dir: Path | None = None,
    charts: bool = False,
) -> dict[str, Any]:
    """
    Сохраняет rows в .xlsx.
    charts=True — простая столбчатая диаграмма по числовым колонкам.
    """
    if not isinstance(rows, list):
        return {"error": "rows должен быть списком объектов"}

    # нормализовать строки
    clean: list[dict[str, Any]] = []
    for r in rows:
        if isinstance(r, dict):
            clean.append(r)
        else:
            clean.append({"value": r})

    out_dir = Path(export_dir) if export_dir else DEFAULT_EXPORT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    if filename:
        fname = _safe_filename(filename)
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = _safe_filename(title or "report").replace(".xlsx", "")
        fname = f"{base}_{stamp}.xlsx"

    path = out_dir / fname
    # не перезаписывать молча
    if path.exists():
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = out_dir / f"{path.stem}_{stamp}.xlsx"

    try:
        cat0, val0 = infer_axes_from_rows(clean) if clean else (None, None)
        use_chart = "bar" if (charts or val0) else None
        if charts or _wants_styled(clean) or use_chart:
            wb = build_analyst_workbook(
                title=title or "Отчёт",
                sheets=[
                    {
                        "name": sheet_name or "Данные",
                        "title": title or sheet_name or "Данные",
                        "rows": clean,
                        "chart": use_chart,
                        "chart_value": val0,
                        "chart_category": cat0,
                    }
                ],
                charts=[
                    {"type": "bar", "value": val0, "chart_category": cat0},
                    {"type": "pie", "value": val0, "chart_category": cat0},
                ]
                if val0
                else None,
            )
        else:
            wb = rows_to_workbook(clean, title=title, sheet_name=sheet_name)
        wb.save(path)
    except Exception as e:
        return {"error": f"Ошибка записи Excel: {e}"}

    columns: list[str] = []
    if clean:
        for k in clean[0].keys():
            columns.append(str(k))

    # path + Загрузки + file:// + http://127.0.0.1 (без автооткрытия проводника)
    meta = enrich_export_meta(
        path,
        copy_to_downloads=True,
        open_folder=False,
        ensure_http=True,
    )
    return {
        "ok": True,
        "rows_count": len(clean),
        "columns": columns,
        "charts": bool(charts),
        **meta,
    }


def _wants_styled(rows: list[dict[str, Any]]) -> bool:
    """Всегда оформляем таблицу (шапка, зебра, фильтр) при наличии строк."""
    return bool(rows)


def _is_numeric_col_name(name: str) -> bool:
    kl = (name or "").lower().replace("ё", "е")
    return any(
        x in kl
        for x in (
            "сумм",
            "кол",
            "amount",
            "qty",
            "%",
            "доля",
            "дельта",
            "рост",
            "дней",
            "остат",
            "цена",
            "стоим",
            "выруч",
            "марж",
            "итого",
            "оборот",
            "вес",
            "объём",
            "объем",
        )
    )


def _write_sheet_table(
    ws: Any,
    rows: list[dict[str, Any]],
    *,
    title: str = "",
    header_color: str = "1F4E79",
    add_totals: bool = True,
) -> tuple[list[str], int, int]:
    """Пишет заголовок+таблицу (+итог). Возвращает (columns, header_row, last_data_row)."""
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    columns: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        for k in row.keys():
            key = str(k)
            if key.startswith("_"):
                continue
            if key not in seen:
                seen.add(key)
                columns.append(key)
    if not columns:
        ws["A1"] = title or "Нет данных"
        return [], 1, 1

    thin = Border(
        left=Side(style="thin", color="D0D0D0"),
        right=Side(style="thin", color="D0D0D0"),
        top=Side(style="thin", color="D0D0D0"),
        bottom=Side(style="thin", color="D0D0D0"),
    )
    start_row = 1
    if title:
        ws.merge_cells(
            start_row=1,
            start_column=1,
            end_row=1,
            end_column=max(1, len(columns)),
        )
        cell = ws.cell(1, 1, title)
        cell.font = Font(bold=True, size=15, color=header_color)
        cell.alignment = Alignment(horizontal="left", vertical="center")
        ws.row_dimensions[1].height = 24
        # подзаголовок: строк / дата
        ws.merge_cells(
            start_row=2,
            start_column=1,
            end_row=2,
            end_column=max(1, len(columns)),
        )
        sub = ws.cell(
            2,
            1,
            f"Строк: {len(rows)}  ·  {datetime.now().strftime('%d.%m.%Y %H:%M')}",
        )
        sub.font = Font(size=9, color="888888", italic=True)
        start_row = 4

    header_fill = PatternFill("solid", fgColor=header_color)
    header_font = Font(bold=True, color="FFFFFF", size=11)
    ws.row_dimensions[start_row].height = 20
    for col_idx, col_name in enumerate(columns, start=1):
        cell = ws.cell(start_row, col_idx, col_name)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", wrap_text=True, vertical="center")
        cell.border = thin

    alt_fill = PatternFill("solid", fgColor="F2F7FB")
    num_cols = {c for c in columns if _is_numeric_col_name(c)}
    # эвристика: если в данных много чисел — тоже num
    for c in columns:
        if c in num_cols:
            continue
        nums = 0
        for row in rows[:30]:
            if isinstance(row, dict) and _cell_float(row.get(c)) is not None:
                nums += 1
        if nums >= min(5, max(2, len(rows) // 3)):
            num_cols.add(c)

    totals: dict[str, float] = {c: 0.0 for c in num_cols}
    total_hits: dict[str, int] = {c: 0 for c in num_cols}

    for r_idx, row in enumerate(rows, start=start_row + 1):
        if not isinstance(row, dict):
            continue
        for c_idx, col_name in enumerate(columns, start=1):
            val = row.get(col_name)
            if val is None:
                cell_val: Any = ""
            elif isinstance(val, bool):
                cell_val = "Да" if val else "Нет"
            elif isinstance(val, (int, float)):
                cell_val = val
            else:
                try:
                    if col_name in num_cols or (
                        isinstance(val, str)
                        and re.match(r"^-?\d+[.,]?\d*$", val.strip())
                    ):
                        cell_val = float(
                            str(val).replace("\xa0", "").replace(" ", "").replace(",", ".")
                        )
                    else:
                        cell_val = str(val)
                except (TypeError, ValueError):
                    cell_val = str(val)
            cell = ws.cell(r_idx, c_idx, cell_val)
            cell.border = thin
            cell.alignment = Alignment(vertical="center")
            if (r_idx - start_row) % 2 == 0:
                if not isinstance(cell_val, (int, float)) or col_name not in num_cols:
                    cell.fill = alt_fill
            if isinstance(cell_val, float):
                if any(x in col_name.lower() for x in ("%", "доля", "процент")):
                    cell.number_format = "0.0%"
                else:
                    cell.number_format = (
                        "#,##0"
                        if abs(cell_val - round(cell_val)) < 1e-9
                        else "#,##0.00"
                    )
                if col_name in totals:
                    totals[col_name] += float(cell_val)
                    total_hits[col_name] += 1
            elif isinstance(cell_val, int) and col_name in num_cols:
                cell.number_format = "#,##0"
                if col_name in totals:
                    totals[col_name] += float(cell_val)
                    total_hits[col_name] += 1

    last_row = start_row + len(rows)
    # строка «Итого»
    if add_totals and rows and any(total_hits.get(c, 0) > 0 for c in num_cols):
        last_row += 1
        total_fill = PatternFill("solid", fgColor="E8EEF5")
        total_font = Font(bold=True, color="1F4E79", size=11)
        for c_idx, col_name in enumerate(columns, start=1):
            if c_idx == 1:
                cell = ws.cell(last_row, c_idx, "Итого")
            elif col_name in num_cols and total_hits.get(col_name, 0) > 0:
                cell = ws.cell(last_row, c_idx, totals[col_name])
                if any(x in col_name.lower() for x in ("%", "доля", "процент")):
                    cell.number_format = "0.0%"
                else:
                    cell.number_format = "#,##0.00"
            else:
                cell = ws.cell(last_row, c_idx, "")
            cell.fill = total_fill
            cell.font = total_font
            cell.border = thin

    for col_idx, col_name in enumerate(columns, start=1):
        max_len = len(str(col_name))
        for row in rows[:200]:
            if isinstance(row, dict):
                max_len = max(max_len, len(str(row.get(col_name, ""))[:80]))
        ws.column_dimensions[get_column_letter(col_idx)].width = min(
            max(12, max_len + 2), 42
        )

    if rows:
        data_end = start_row + len(rows)  # без строки Итого в фильтре
        ws.auto_filter.ref = (
            f"A{start_row}:{get_column_letter(len(columns))}{data_end}"
        )
        ws.freeze_panes = ws.cell(start_row + 1, 1).coordinate
        ws.print_title_rows = f"{start_row}:{start_row}"
    # last_data_row для графика — до totals
    data_last = start_row + len(rows)
    return columns, start_row, data_last


# Универсальные роли колонок (любые данные: продажи, склад, заказы, производство…)
_SKIP_AS_CATEGORY = {
    "период",
    "period",
    "датаначала",
    "датаконца",
    "дата",
    "время",
    "год",
    "месяц",
    "квартал",
    "день",
    "year",
    "month",
    "номер",
    "number",
    "error",
    "статус",
    "класс",
    "ссылка",
    "uid",
    "uuid",
    "id",
    "код",  # код часто числовой — как подпись оси ок, но ниже score
}
# подписи оси X: кто/что (измерения, не метрики)
_CATEGORY_HINTS = (
    # товары / производство
    "номенклатур",
    "товар",
    "издели",
    "продукц",
    "материал",
    "полуфабрик",
    "серия",
    "характеристик",
    "sku",
    "артикул",
    # место / орг
    "склад",
    "ячейк",
    "помещен",
    "цех",
    "участк",
    "производств",
    "организац",
    "подраздел",
    "проект",
    # контрагенты / люди
    "контрагент",
    "клиент",
    "покупат",
    "поставщик",
    "партнер",
    "партнёр",
    "менеджер",
    "сотрудник",
    "ответственн",
    "заказчик",
    # документы / аналитика
    "заказ",
    "договор",
    "сделк",
    "заявка",
    "группа",
    "категор",
    "вид",
    "тип",
    "название",
    "наименован",
    "представление",
    "подпись",
)
# метрики оси Y
_VALUE_HINTS = (
    "количество",
    "колво",
    "qty",
    "count",
    "остат",
    "свободн",
    "врезерв",
    "заказан",
    "к поступлен",
    "к отгруз",
    "в производств",
    "выпуск",
    "брак",
    "сумм",
    "amount",
    "выруч",
    "себестоим",
    "стоим",
    "цена",
    "price",
    "дельта",
    "доля",
    "вес",
    "объем",
    "объём",
    "площад",
    "длина",
    "час",
    "трудозатрат",
    "оборот",
    "приход",
    "расход",
)


def infer_axes_from_rows(
    rows: list[dict[str, Any]],
    *,
    preferred_category: str | None = None,
    preferred_value: str | None = None,
) -> tuple[str | None, str | None]:
    """
    По факту строк выбрать (category_key, value_key) — универсально.
    Любые данные: склад, заказы, производство, продажи…
    """
    if not rows:
        return None, None
    sample = [r for r in rows[:80] if isinstance(r, dict)]
    if not sample:
        return None, None
    keys: list[str] = []
    seen: set[str] = set()
    for r in sample:
        for k in r.keys():
            sk = str(k)
            if sk.startswith("_") or sk in seen:
                continue
            seen.add(sk)
            keys.append(sk)

    def cat_score(k: str) -> float:
        kl = k.lower().replace("ё", "е")
        if kl in _SKIP_AS_CATEGORY or any(
            x in kl for x in ("дата", "время", "period", "сумм", "колич", "остат", "%")
        ):
            # количество/остаток — метрика, не категория
            if any(x in kl for x in ("сумм", "колич", "остат", "цена", "дельта", "qty")):
                return -100
            if any(x in kl for x in ("дата", "period", "год", "месяц")):
                return -50
        sc = 0.0
        if preferred_category and preferred_category.lower() in kl:
            sc += 100
        for h in _CATEGORY_HINTS:
            if h in kl:
                sc += 30
                break
        # доля непустых строк-строк
        n_str = 0
        n = 0
        for r in sample:
            v = r.get(k)
            if v is None or v == "":
                continue
            n += 1
            if isinstance(v, str) and not _cell_float(v):
                n_str += 1
            elif not isinstance(v, (int, float)):
                n_str += 1
        if n:
            sc += 20 * (n_str / n)
        if kl in ("код", "code") or kl.endswith("код"):
            sc += 5
        return sc

    def val_score(k: str) -> float:
        kl = k.lower().replace("ё", "е")
        sc = 0.0
        if preferred_value and preferred_value.lower().replace("ё", "е") in kl:
            sc += 100
        for h in _VALUE_HINTS:
            if h in kl:
                sc += 25
                break
        # numeric density
        n_num = 0
        n = 0
        for r in sample:
            v = r.get(k)
            if v is None or v == "":
                continue
            n += 1
            if _cell_float(v) is not None:
                n_num += 1
        if n:
            sc += 30 * (n_num / n)
        if any(x in kl for x in ("дата", "номер", "код", "period", "год", "месяц")):
            sc -= 40
        if "%" in kl or "доля" in kl:
            sc += 5
        return sc

    cat_key = max(keys, key=cat_score) if keys else None
    if cat_key and cat_score(cat_key) < 0:
        # fallback: first mostly-string col
        cat_key = None
        for k in keys:
            if cat_score(k) > 0:
                cat_key = k
                break
        if cat_key is None and keys:
            cat_key = keys[0]

    val_keys = [k for k in keys if k != cat_key]
    val_key = max(val_keys, key=val_score) if val_keys else None
    if val_key and val_score(val_key) < 5:
        # any numeric
        for k in val_keys:
            if val_score(k) >= 5:
                val_key = k
                break
    return cat_key, val_key


def _row_column_names(rows: list[dict[str, Any]] | None) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        for k in r.keys():
            sk = str(k)
            if sk.startswith("_") or sk in seen:
                continue
            seen.add(sk)
            names.append(sk)
        if names:
            break
    return names


def match_column_name(
    rows: list[dict[str, Any]] | None,
    preferred: str | None,
) -> str | None:
    """Сопоставить подсказку (Сумма/Остаток) с реальным именем колонки rows."""
    want = (preferred or "").strip()
    if not want:
        return None
    wl = want.lower().replace("ё", "е")
    cols = _row_column_names(rows)
    for k in cols:
        if str(k).lower().replace("ё", "е") == wl:
            return str(k)
    for k in cols:
        kl = str(k).lower().replace("ё", "е")
        if wl in kl or kl in wl:
            return str(k)
    return None


def bind_charts_spec_to_rows(
    charts_spec: list[Any] | None,
    rows: list[dict[str, Any]] | None,
    *,
    phrase: str = "",
) -> list[dict[str, Any]]:
    """
    Подставить оси графиков из фактических колонок таблицы.
    Не оставлять «Сумма», если в rows её нет (остаток / qty / цена…).
    """
    sample = [r for r in (rows or []) if isinstance(r, dict)]
    cat, val = infer_axes_from_rows(sample) if sample else (None, None)
    spec = [dict(c) for c in (charts_spec or []) if isinstance(c, dict)]
    pl = (phrase or "").lower().replace("ё", "е")
    keys_blob = " ".join(k.lower() for k in _row_column_names(sample))
    want_line = any(
        x in pl for x in ("динамик", "тренд", "по месяц", "по период")
    ) or any(x in keys_blob for x in ("период", "месяц"))
    if not spec:
        if not val:
            return []
        spec = [
            {"type": "bar", "value": val, "title": f"bar: {val}"},
            {"type": "pie", "value": val, "title": f"pie: {val}"},
        ]
        if want_line:
            spec.append({"type": "line", "value": val, "title": f"line: {val}"})
    types_have = {str(c.get("type") or "") for c in spec}
    if want_line and "line" not in types_have and val:
        spec.append({"type": "line", "value": val, "title": f"line: {val}"})
    out: list[dict[str, Any]] = []
    for c in spec[:6]:
        want = str(c.get("value") or "").strip()
        real = match_column_name(sample, want) if want else None
        if not real:
            real = val or want or None
        if real:
            c["value"] = real
            title = str(c.get("title") or "")
            if (
                not title
                or title.lower() in ("bar", "pie", "line", "сумма", want.lower())
                or title.endswith(": Сумма")
            ):
                c["title"] = f"{c.get('type') or 'bar'}: {real}"
        ck = str(c.get("chart_category") or "").strip()
        real_c = match_column_name(sample, ck) if ck else None
        if not real_c:
            real_c = cat
        if real_c:
            c["chart_category"] = real_c
        out.append(c)
    return out


def _cell_float(val: Any) -> float | None:
    if val is None or val == "":
        return None
    if isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip().replace("\xa0", " ").replace(" ", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _human_num(v: float) -> str:
    if abs(v - round(v)) < 1e-9:
        return f"{int(round(v)):,}".replace(",", " ")
    return f"{v:,.2f}".replace(",", " ")


def _chart_data_sheet(wb: Any) -> Any:
    name = "_chart_data"
    if name in wb.sheetnames:
        ws = wb[name]
    else:
        ws = wb.create_sheet(name)
        ws["A1"] = "Ряды диаграмм (служебный, скрытый)"
    try:
        ws.sheet_state = "hidden"
    except Exception:
        pass
    return ws


def _next_block_row(ws: Any) -> int:
    mr = int(ws.max_row or 1)
    if mr <= 1 and ws.cell(1, 1).value is None:
        return 1
    return mr + 3


def _rank_category_keys(rows: list[dict[str, Any]]) -> list[str]:
    """Категории для дашборда: сначала «широкая» (номенклатура), потом компактная (склад)."""
    sample = [r for r in rows[:400] if isinstance(r, dict)]
    keys = _row_column_names(sample)
    scored: list[tuple[float, str, int]] = []
    for k in keys:
        kl = k.lower().replace("ё", "е")
        if any(
            x in kl
            for x in ("сумм", "колич", "остат", "цена", "qty", "amount", "%", "доля")
        ):
            continue
        filled: list[str] = []
        for r in sample:
            v = r.get(k)
            if v is None or v == "":
                continue
            if _cell_float(v) is not None and not isinstance(v, str):
                continue
            filled.append(str(v).strip())
        n = len(filled)
        if n < 2:
            continue
        n_dist = len(set(filled))
        if n_dist < 2:
            continue
        empty_ratio = 1.0 - (n / max(1, len(sample)))
        if empty_ratio > 0.45:
            continue
        sc = 0.0
        for h in _CATEGORY_HINTS:
            if h in kl:
                sc += 30
                break
        sc += 12 * (n / max(1, len(sample)))
        sc -= 25 * empty_ratio
        # pie-friendly: 2…25 значений
        if 2 <= n_dist <= 25:
            sc += 18
        elif n_dist > 80:
            sc += 8  # топ-N bar
        scored.append((sc, k, n_dist))
    scored.sort(key=lambda t: t[0], reverse=True)
    # широкая ось (много уникальных) первой, компактная второй
    wide = [k for _, k, d in scored if d > 25]
    compact = [k for _, k, d in scored if d <= 25]
    ordered: list[str] = []
    for k in wide + compact:
        if k not in ordered:
            ordered.append(k)
    if not ordered:
        ordered = [k for _, k, _ in scored]
    return ordered[:4]


def infer_dashboard_dims(
    rows: list[dict[str, Any]],
) -> tuple[str | None, str | None, str | None, str | None]:
    """(cat_bar, val, cat_pie, val2) по факту колонок — любая CF."""
    _cat, val = infer_axes_from_rows(rows)
    cats = _rank_category_keys(rows)
    sample = [r for r in rows[:400] if isinstance(r, dict)]

    def n_dist(k: str) -> int:
        return len(
            {
                str(r.get(k)).strip()
                for r in sample
                if r.get(k) not in (None, "")
            }
        )

    if cats:
        cat_bar = max(cats, key=n_dist)
        compact = [k for k in cats if k != cat_bar and 2 <= n_dist(k) <= 25]
        compact.sort(key=n_dist)
        cat_pie = compact[0] if compact else None
    else:
        cat_bar, cat_pie = _cat, None
    val2 = None
    keys = _row_column_names(rows)
    if val:
        for k in keys:
            if k == val:
                continue
            kl = k.lower().replace("ё", "е")
            if any(h in kl for h in _VALUE_HINTS):
                n_num = 0
                for r in rows[:40]:
                    if isinstance(r, dict) and _cell_float(r.get(k)) is not None:
                        n_num += 1
                if n_num >= 2:
                    val2 = k
                    break
    return cat_bar, val, cat_pie, val2


def _aggregate_pairs(
    rows: list[dict[str, Any]],
    cat_key: str,
    val_key: str,
) -> list[tuple[str, float]]:
    agg: dict[str, float] = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        fv = _cell_float(r.get(val_key))
        if fv is None:
            continue
        name = _cat_label(r.get(cat_key))
        agg[name] = agg.get(name, 0.0) + fv
    return sorted(agg.items(), key=lambda x: abs(x[1]), reverse=True)


def _cat_label(val: Any) -> str:
    s = "" if val is None else str(val).strip()
    if s.lower() in ("", "—", "-", "none", "null", "nan"):
        return "Не указано"
    return s


def _caption(name: str, max_len: int = 22) -> str:
    """Короткая подпись колонки: ВНаличииОстаток → «В наличии»."""
    s = str(name or "").strip()
    s = re.sub(r"(Остаток|Обороты|Оборот)$", "", s)
    parts = re.findall(r"[A-ZА-ЯЁ][a-zа-яё]+|[A-ZА-ЯЁ]+(?![a-zа-яё])|[a-zа-яё]+|\d+", s)
    if len(parts) >= 2:
        s = " ".join(p.lower() if i else p.capitalize() for i, p in enumerate(parts))
        s = s[0].upper() + s[1:] if s else s
    s = re.sub(r"\s+", " ", s).strip() or str(name or "")
    return s[:max_len]


def _apply_chart_no_overlap(chart: Any, ctype: str) -> None:
    """Заголовок отдельно; подписи категорий и значений всегда видны."""
    from openpyxl.chart.label import DataLabelList
    from openpyxl.chart.layout import Layout, ManualLayout
    from openpyxl.chart.legend import Legend

    try:
        if getattr(chart, "title", None) is not None:
            chart.title.overlay = False
    except Exception:
        pass
    if ctype == "pie":
        chart.width = 12
        chart.height = 10
        try:
            chart.layout = Layout(
                manualLayout=ManualLayout(
                    xMode="edge",
                    yMode="edge",
                    x=0.04,
                    y=0.14,
                    w=0.92,
                    h=0.62,
                )
            )
        except Exception:
            pass
        try:
            chart.legend = Legend()
            chart.legend.position = "b"
            chart.legend.overlay = False
        except Exception:
            pass
        try:
            chart.dataLabels = DataLabelList()
            chart.dataLabels.showPercent = True
            chart.dataLabels.showCatName = True
            chart.dataLabels.showVal = False
            chart.dataLabels.showSerName = False
            chart.dataLabels.showLegendKey = False
            chart.dataLabels.dLblPos = "outEnd"
            chart.dataLabels.showLeaderLines = True
        except Exception:
            pass
        return

    # type=bar: X = значения, Y = категории. Не ставить числовой формат на Y.
    chart.width = 15
    chart.height = 10
    try:
        chart.layout = Layout(
            manualLayout=ManualLayout(
                xMode="edge",
                yMode="edge",
                x=0.36,
                y=0.14,
                w=0.50,
                h=0.80,
            )
        )
    except Exception:
        pass
    try:
        chart.legend = None
    except Exception:
        pass
    try:
        chart.y_axis.title = None
        chart.x_axis.title = None
        chart.x_axis.numFmt = "#,##0"
        chart.x_axis.tickLblPos = "nextTo"
        chart.y_axis.tickLblPos = "nextTo"
        chart.y_axis.delete = False
        chart.x_axis.delete = False
    except Exception:
        pass
    try:
        labs = DataLabelList()
        labs.showVal = True
        labs.showCatName = False
        labs.showSerName = False
        labs.showPercent = False
        labs.dLblPos = "outEnd"
        chart.dataLabels = labs
    except Exception:
        pass


def _build_chart_object(
    ws_src: Any,
    *,
    hdr: int,
    data_end: int,
    chart_type: str,
    title: str,
    val_header: str,
    cat_col: int = 1,
    val_col: int = 2,
) -> Any | None:
    try:
        from openpyxl.chart import BarChart, LineChart, PieChart, Reference
        from openpyxl.chart.data_source import AxDataSource, StrRef
        from openpyxl.utils import absolute_coordinate, get_column_letter, quote_sheetname
    except Exception:
        return None

    ctype = (chart_type or "bar").lower()
    if ctype in ("col", "column", "столб", ""):
        ctype = "bar"
    cats = Reference(ws_src, min_col=cat_col, min_row=hdr + 1, max_row=data_end)
    data = Reference(ws_src, min_col=val_col, min_row=hdr + 1, max_row=data_end)
    n_pts = max(0, data_end - hdr)
    short_title = (title or _caption(val_header) or "Диаграмма")[:48]
    if ctype == "pie":
        chart = PieChart()
        chart.style = 10
        chart.title = short_title
        chart.add_data(data, titles_from_data=False)
        chart.set_categories(cats)
    elif ctype == "line":
        chart = LineChart()
        chart.style = 12
        chart.title = short_title
        chart.add_data(data, titles_from_data=False)
        chart.set_categories(cats)
    else:
        chart = BarChart()
        # горизонтальные полосы: длинные имена не крутятся и не наезжают
        chart.type = "bar"
        chart.style = 10
        chart.grouping = "clustered"
        chart.title = short_title
        chart.add_data(data, titles_from_data=False)
        chart.set_categories(cats)
        try:
            chart.shape = 4
            for ser in chart.series or []:
                ser.graphicalProperties.solidFill = "1F4E79"
        except Exception:
            pass
    try:
        letter = get_column_letter(cat_col)
        cat_range = (
            f"{quote_sheetname(ws_src.title)}!"
            f"{absolute_coordinate(f'{letter}{hdr + 1}')}:"
            f"{absolute_coordinate(f'{letter}{data_end}')}"
        )
        for ser in chart.series or []:
            ser.cat = AxDataSource(strRef=StrRef(f=cat_range))
    except Exception:
        pass
    _apply_chart_no_overlap(chart, ctype)
    if ctype == "bar" and n_pts > 8:
        chart.height = 10
    return chart


def _add_chart_from_pairs(
    ws: Any,
    pairs: list[tuple[str, float]],
    *,
    chart_type: str = "bar",
    title: str = "",
    val_header: str = "Значение",
    anchor: str = "A18",
    pie_top: int = 10,
    max_cats: int = 12,
) -> bool:
    if len(pairs) < 2:
        return False
    ctype = (chart_type or "bar").lower()
    n_show = pie_top if ctype == "pie" else max_cats
    top = list(pairs[: max(2, n_show)])
    rest = sum(v for _, v in pairs[n_show:])
    if ctype == "pie" and rest > 0 and len(pairs) > n_show:
        top.append(("Прочие", rest))
    src = _chart_data_sheet(ws.parent)
    block = _next_block_row(src)
    src.cell(block, 1, title or val_header)
    hdr = block + 1
    src.cell(hdr, 1, "Категория")
    src.cell(hdr, 2, str(val_header))
    for i, (name, fv) in enumerate(top):
        label = name if len(name) <= 28 else name[:26] + "…"
        src.cell(hdr + 1 + i, 1, label)
        cell = src.cell(hdr + 1 + i, 2, float(fv))
        cell.number_format = "#,##0.00"
    data_end = hdr + len(top)
    chart = _build_chart_object(
        src,
        hdr=hdr,
        data_end=data_end,
        chart_type=ctype,
        title=title,
        val_header=val_header,
    )
    if chart is None:
        return False
    if ctype == "pie":
        chart.width = 11.0
        chart.height = 9.0
    else:
        chart.width = 13.0
        chart.height = 9.5
    ws.add_chart(chart, anchor)
    return True


def _write_compact_table(
    ws: Any,
    *,
    start_row: int,
    start_col: int,
    title: str,
    pairs: list[tuple[str, float]],
    val_header: str,
    color: str = "1F4E79",
    top_n: int = 12,
) -> tuple[int, dict[str, int]]:
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    thin = Border(
        left=Side(style="thin", color="D0D0D0"),
        right=Side(style="thin", color="D0D0D0"),
        top=Side(style="thin", color="D0D0D0"),
        bottom=Side(style="thin", color="D0D0D0"),
    )
    total = sum(v for _, v in pairs) or 1.0
    show = pairs[:top_n]
    cell = ws.cell(start_row, start_col, title)
    cell.font = Font(bold=True, size=12, color=color)
    hdr = start_row + 1
    for i, h in enumerate(("Категория", _caption(val_header, 16), "Доля"), start=0):
        c = ws.cell(hdr, start_col + i, h)
        c.fill = PatternFill("solid", fgColor=color)
        c.font = Font(bold=True, color="FFFFFF", size=10)
        c.alignment = Alignment(horizontal="center")
        c.border = thin
    alt = PatternFill("solid", fgColor="F2F7FB")
    for i, (name, fv) in enumerate(show):
        r = hdr + 1 + i
        label = name if len(name) <= 34 else name[:32] + "…"
        c0 = ws.cell(r, start_col, label)
        c1 = ws.cell(r, start_col + 1, fv)
        c2 = ws.cell(r, start_col + 2, fv / total)
        c1.number_format = "#,##0.00" if abs(fv - round(fv)) > 1e-9 else "#,##0"
        c2.number_format = "0.0%"
        for c in (c0, c1, c2):
            c.border = thin
            if i % 2 == 1:
                c.fill = alt
    last = hdr + len(show)
    for i, w in enumerate((32, 12, 8)):
        ws.column_dimensions[get_column_letter(start_col + i)].width = w
    return last, {
        "header_row": hdr,
        "last_row": last,
        "cat_col": start_col,
        "val_col": start_col + 1,
    }


def _write_kpi_strip(
    ws: Any,
    *,
    row: int,
    items: list[tuple[str, str]],
) -> None:
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    colors = ("1F4E79", "2E7D32", "E65100", "00838F", "6A1B9A", "C62828")
    for i, (label, value) in enumerate(items[:4]):
        col = 1 + i * 3
        fill = PatternFill("solid", fgColor=colors[i % len(colors)])
        a = ws.cell(row, col, label)
        a.fill = fill
        a.font = Font(color="FFFFFF", size=9, bold=True)
        a.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
        b = ws.cell(row + 1, col, value)
        b.fill = fill
        b.font = Font(color="FFFFFF", size=14, bold=True)
        b.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
        try:
            ws.merge_cells(
                start_row=row,
                start_column=col,
                end_row=row,
                end_column=col + 2,
            )
            ws.merge_cells(
                start_row=row + 1,
                start_column=col,
                end_row=row + 1,
                end_column=col + 2,
            )
            for extra in (1, 2):
                ws.cell(row, col + extra).fill = fill
                ws.cell(row + 1, col + extra).fill = fill
        except Exception:
            pass
        ws.column_dimensions[get_column_letter(col)].width = 14
        ws.column_dimensions[get_column_letter(col + 1)].width = 8
        ws.column_dimensions[get_column_letter(col + 2)].width = 6
        ws.row_dimensions[row].height = 18
        ws.row_dimensions[row + 1].height = 24


def _write_dashboard(
    ws: Any,
    *,
    title: str,
    rows: list[dict[str, Any]],
    bullets: list[str] | None = None,
    charts: list[dict[str, Any]] | None = None,
) -> int:
    """Один понятный лист: KPI + топы + графики. Без пустой «Сводки»."""
    from openpyxl.styles import Alignment, Font
    from openpyxl.utils import get_column_letter

    cat_bar, val, cat_pie, val2 = infer_dashboard_dims(rows)
    n = len(rows)
    total = 0.0
    if val:
        for r in rows:
            fv = _cell_float(r.get(val)) if isinstance(r, dict) else None
            if fv is not None:
                total += fv
    n_cat = 0
    if cat_bar:
        n_cat = len(
            {
                str(r.get(cat_bar)).strip()
                for r in rows
                if isinstance(r, dict) and r.get(cat_bar) not in (None, "")
            }
        )
    n_pie = 0
    if cat_pie:
        n_pie = len(
            {
                str(r.get(cat_pie)).strip()
                for r in rows
                if isinstance(r, dict) and r.get(cat_pie) not in (None, "")
            }
        )

    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=12)
    c = ws.cell(1, 1, title or "Отчёт")
    c.font = Font(bold=True, size=20, color="1F4E79")
    c.alignment = Alignment(vertical="center")
    ws.row_dimensions[1].height = 30
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=12)
    sub = ws.cell(
        2,
        1,
        f"Сформировано {datetime.now().strftime('%d.%m.%Y %H:%M')}  ·  "
        f"полная таблица на соседнем листе",
    )
    sub.font = Font(size=10, color="888888", italic=True)

    kpis: list[tuple[str, str]] = [
        ("Строк", _human_num(float(n))),
    ]
    if val:
        kpis.append((_caption(val, 18) or "Сумма", _human_num(total)))
    if cat_bar:
        kpis.append((_caption(cat_bar, 16) or "Групп", _human_num(float(n_cat))))
    if cat_pie and n_pie and cat_pie != cat_bar:
        kpis.append((_caption(cat_pie, 16), _human_num(float(n_pie))))
    elif val2:
        s2 = 0.0
        for r in rows:
            fv = _cell_float(r.get(val2)) if isinstance(r, dict) else None
            if fv is not None:
                s2 += fv
        kpis.append((_caption(val2, 18), _human_num(s2)))
    kpis = kpis[:4]
    _write_kpi_strip(ws, row=4, items=kpis)

    pairs_bar = (
        _aggregate_pairs(rows, cat_bar, val) if cat_bar and val else []
    )
    pairs_pie = (
        _aggregate_pairs(rows, cat_pie, val) if cat_pie and val else []
    )
    # pie по той же оси, если второй категории нет, но топ читаемый
    if len(pairs_pie) < 2 and len(pairs_bar) >= 2:
        pairs_pie = pairs_bar
        cat_pie = cat_bar

    table_row = 7
    last_tbl = table_row
    bar_src: dict[str, int] | None = None
    pie_src: dict[str, int] | None = None
    if pairs_bar:
        last_bar, bar_src = _write_compact_table(
            ws,
            start_row=table_row,
            start_col=1,
            title=f"Топ · {_caption(cat_bar, 20)}",
            pairs=pairs_bar,
            val_header=str(val or "Значение"),
            color="1F4E79",
            top_n=8,
        )
        last_tbl = max(last_tbl, last_bar)
        pie_src = bar_src
    if pairs_pie and cat_pie != cat_bar:
        last_pie, pie_src = _write_compact_table(
            ws,
            start_row=table_row,
            start_col=5,
            title=f"Срез · {_caption(cat_pie, 20)}",
            pairs=pairs_pie,
            val_header=str(val or "Значение"),
            color="2E7D32",
            top_n=8,
        )
        last_tbl = max(last_tbl, last_pie)

    note_row = last_tbl + 2
    if bullets:
        ws.cell(note_row, 1, "Кратко").font = Font(bold=True, size=11, color="1F4E79")
        for i, b in enumerate(bullets[:8]):
            cell = ws.cell(note_row + 1 + i, 1, f"• {b}")
            cell.alignment = Alignment(wrap_text=True)
            ws.merge_cells(
                start_row=note_row + 1 + i,
                start_column=1,
                end_row=note_row + 1 + i,
                end_column=6,
            )
        note_row += min(8, len(bullets)) + 2

    spec = [dict(c) for c in (charts or []) if isinstance(c, dict)]
    if not spec and val:
        spec = [{"type": "bar", "title": f"Топ: {cat_bar or ''}"}]
        pie_key = (cat_pie or cat_bar or "").lower().replace("ё", "е")
        if any(x in pie_key for x in ("период", "месяц", "квартал", "год", "дата")):
            spec.append({"type": "line", "title": f"Динамика: {val}"})
        elif pairs_pie:
            spec.append({"type": "pie", "title": f"Структура: {cat_pie or cat_bar}"})
    # не плодить одинаковые pie/bar без смысла
    seen_t: set[str] = set()
    clean_spec: list[dict[str, Any]] = []
    for c in spec[:3]:
        t = str(c.get("type") or "bar").lower()
        if t in seen_t:
            continue
        seen_t.add(t)
        clean_spec.append(c)

    chart_row = max(note_row, last_tbl + 3)
    bar_anchor = f"A{chart_row}"
    pie_anchor = f"J{chart_row}"

    added = 0
    for c in clean_spec:
        t = str(c.get("type") or "bar").lower()
        if t == "pie":
            want_cat = cat_pie or cat_bar
            src = pie_src or bar_src
        elif t == "line":
            want_cat = cat_pie or cat_bar
            src = pie_src or bar_src
        else:
            want_cat = str(c.get("chart_category") or "") or cat_bar
            src = bar_src
        want_val = str(c.get("value") or "") or val
        title_c = str(c.get("title") or "").strip()
        if not title_c or title_c.lower().startswith(("bar:", "pie:", "line:")):
            if t == "pie":
                title_c = f"Структура · {_caption(want_cat, 18)}"
            elif t == "line":
                title_c = f"Динамика · {_caption(want_val, 18)}"
            else:
                title_c = f"Топ · {_caption(want_cat, 18)}"
        ok = False
        if src:
            chart = _build_chart_object(
                ws,
                hdr=int(src["header_row"]),
                data_end=int(src["last_row"]),
                chart_type=t,
                title=title_c,
                val_header=str(want_val or "Значение"),
                cat_col=int(src["cat_col"]),
                val_col=int(src["val_col"]),
            )
            if chart is not None:
                if t == "pie":
                    chart.width = 12.0
                    chart.height = 10.0
                    ws.add_chart(chart, pie_anchor)
                else:
                    chart.width = 15.0
                    chart.height = 10.0
                    ws.add_chart(chart, bar_anchor)
                ok = True
        else:
            pairs = (
                _aggregate_pairs(rows, want_cat, want_val)
                if want_cat and want_val
                else (pairs_pie if t == "pie" else pairs_bar)
            )
            ok = _add_chart_from_pairs(
                ws,
                pairs,
                chart_type=t,
                title=title_c,
                val_header=str(want_val or "Значение"),
                anchor=pie_anchor if t == "pie" else bar_anchor,
                pie_top=6,
                max_cats=8,
            )
        if ok:
            added += 1
            if t != "pie":
                bar_anchor = f"A{chart_row + 18}"

    for col in range(1, 13):
        letter = get_column_letter(col)
        if ws.column_dimensions[letter].width is None or ws.column_dimensions[letter].width < 11:
            ws.column_dimensions[letter].width = 12
    _polish_sheet(ws, tab_color="1F4E79", landscape=True, hide_grid=True)
    return added


def _primary_sheet_rows(sheets: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    candidates = [
        s
        for s in (sheets or [])
        if isinstance(s, dict) and isinstance(s.get("rows"), list) and s.get("rows")
    ]
    if not candidates:
        return []
    best = max(
        candidates,
        key=lambda s: (
            0
            if str(s.get("name") or "").lower()
            in ("итоги", "summary", "сводка", "рост", "падение")
            else 1,
            len(s.get("rows") or []),
        ),
    )
    return [r for r in (best.get("rows") or []) if isinstance(r, dict)]


def build_analyst_workbook(
    *,
    title: str = "Аналитический отчёт",
    sheets: list[dict[str, Any]] | None = None,
    bullets: list[str] | None = None,
    meta_extra: dict[str, Any] | None = None,
    charts: list[dict[str, Any]] | None = None,
) -> Any:
    """
    Книга: Дашборд (KPI + графики) + листы данных.
    Без пустой «Сводки», без «Служебно», без копий таблицы под каждый график.
    """
    try:
        from openpyxl import Workbook
    except ImportError as e:
        raise RuntimeError("Нужен openpyxl: pip install openpyxl") from e

    wb = Workbook()
    colors = ("1F4E79", "2E7D32", "E65100", "6A1B9A", "00838F", "C62828")
    dash_rows = _primary_sheet_rows(sheets)
    note_bits = [str(b) for b in (bullets or []) if str(b).strip()][:6]

    ws0 = wb.active
    ws0.title = "Дашборд"
    n_charts = 0
    if dash_rows:
        n_charts = _write_dashboard(
            ws0,
            title=title,
            rows=dash_rows,
            bullets=note_bits[:8] if note_bits else None,
            charts=charts,
        )
    else:
        ws0["A1"] = title or "Нет данных"
        _polish_sheet(ws0, tab_color="1F4E79", hide_grid=True)

    for i, spec in enumerate(sheets or []):
        if not isinstance(spec, dict):
            continue
        name = _safe_sheet_name(str(spec.get("name") or f"Лист{i+1}"))
        if name.lower() in ("сводка", "служебно", "дашборд"):
            name = "Данные"
        base_name = name
        n = 1
        while name in wb.sheetnames:
            name = _safe_sheet_name(f"{base_name[:28]}_{n}")
            n += 1
        ws = wb.create_sheet(name)
        rows = spec.get("rows") or []
        if not isinstance(rows, list):
            rows = []
        clean_rows = [r for r in rows if isinstance(r, dict)]
        sheet_title = str(spec.get("title") or "")
        _write_sheet_table(
            ws,
            clean_rows,
            title=sheet_title,
            header_color=colors[i % len(colors)],
        )
        _polish_sheet(ws, tab_color=colors[i % len(colors)], landscape=True)
        # график на листе данных только если дашборд не собрался и строк мало
        if n_charts == 0 and spec.get("chart") and 2 <= len(clean_rows) <= 40:
            cat, val = infer_axes_from_rows(clean_rows)
            if cat and val:
                _add_chart_from_pairs(
                    ws,
                    _aggregate_pairs(clean_rows, cat, val),
                    chart_type=str(spec.get("chart") or "bar"),
                    title=sheet_title or name,
                    val_header=str(val),
                    anchor="H3",
                )
    if "_chart_data" in wb.sheetnames:
        try:
            hidden = wb["_chart_data"]
            wb._sheets.remove(hidden)
            wb._sheets.append(hidden)
            hidden.sheet_state = "hidden"
        except Exception:
            pass
    return wb


def save_analyst_excel(
    *,
    title: str = "Аналитический отчёт",
    sheets: list[dict[str, Any]] | None = None,
    bullets: list[str] | None = None,
    meta_extra: dict[str, Any] | None = None,
    filename: str = "",
    export_dir: Path | None = None,
    charts_spec: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Сохранить красивый аналитический xlsx с графиками.
    charts_spec: [{type: bar|line|pie, value: Сумма|Дельта|…, title?}] —
    несколько диаграмм на лист «Данные» (или отдельные листы).
    """
    out_dir = Path(export_dir) if export_dir else DEFAULT_EXPORT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    if filename:
        fname = _safe_filename(filename)
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        fname = f"{_safe_filename(title).replace('.xlsx', '')}_{stamp}.xlsx"
    path = out_dir / fname
    if path.exists():
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = out_dir / f"{path.stem}_{stamp}.xlsx"

    sheets_list = list(sheets or [])
    cs = list(charts_spec or []) if isinstance(charts_spec, list) else []
    primary = _primary_sheet_rows(sheets_list)
    if cs and primary:
        cs = bind_charts_spec_to_rows(cs, primary)

    try:
        wb = build_analyst_workbook(
            title=title,
            sheets=sheets_list,
            bullets=bullets,
            meta_extra=meta_extra,
            charts=cs or None,
        )
        wb.save(path)
    except Exception as e:
        return {"error": f"Ошибка записи Excel: {e}"}

    total_rows = 0
    for s in sheets_list:
        if isinstance(s, dict) and isinstance(s.get("rows"), list):
            total_rows += len(s["rows"])
    meta = enrich_export_meta(
        path, copy_to_downloads=True, open_folder=False, ensure_http=True
    )
    return {
        "ok": True,
        "rows_count": total_rows,
        "sheets": [
            str(s.get("name"))
            for s in sheets_list
            if isinstance(s, dict)
        ],
        "charts": True,
        "charts_count": len(cs) if cs else 2,
        "styled": True,
        **meta,
    }
