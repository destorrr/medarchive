#!/usr/bin/env python3
"""Разовая чистка «остатков»: карта числится в >1 активной коробке.

Standalone-скрипт (только stdlib: sqlite3/datetime/argparse/os/json/sys) — не
импортирует сервисы МедАрхива и повторяет правило выбора актуальной коробки из
medarchive.services.search_service (дизайн /search, 2026-09-05, §1):

  - статус карты -> допустимые статусы актуальной коробки:
        in_box -> packing/ready; in_transit -> in_transit;
        in_archive -> received; placed/pending_return -> placed;
  - среди подходящих — свежайшая по (box_placements.placed_at,
    box_cards.added_at, box.id) DESC; если подходящих нет — свежайшая активная.

Прочие активные коробки карты — «остатки»: их строки box_cards снимаются
(removed_at = now). Актуальная коробка сохраняется ВСЕГДА; если актуальную
выделить нельзя, карта пропускается и попадает в отчёт.

Без --apply скрипт ничего не меняет (dry-run preview). При --apply до записи
создаётся бэкап <db>.bak-<YYYYmmdd-HHMMSS> через sqlite3.Connection.backup()
(корректно и при живом WAL); отключить можно --no-backup (не рекомендуется).

Примеры:
  .venv/bin/python scripts/cleanup_stale_box_cards.py --db medarchive.db
  .venv/bin/python scripts/cleanup_stale_box_cards.py --db medarchive.db --apply
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sqlite3
import sys

# БД по умолчанию: env MEDARCHIVE_DB_PATH, иначе ./medarchive.db.
DEFAULT_DB = os.environ.get("MEDARCHIVE_DB_PATH") or "./medarchive.db"

# Цепочка «статус карты -> допустимые статусы актуальной коробки»
# (зеркало search_service._CARD_STATUS_TO_BOX_STATUSES).
CARD_STATUS_TO_BOX_STATUSES: dict[str, tuple[str, ...]] = {
    "in_box": ("packing", "ready"),
    "in_transit": ("in_transit",),
    "in_archive": ("received",),
    "placed": ("placed",),
    "pending_return": ("placed",),
}

# Активные коробки карт с местом текущего размещения (как в SearchService).
_SELECT_ACTIVE = """
    SELECT c.id AS card_id, c.full_name, c.status,
           b.id AS box_id, b.box_number, b.status AS box_status,
           bp.placed_at, bc.added_at,
           ash.name AS shelf_name, asec.name AS section_name
    FROM cards c
    JOIN box_cards bc ON bc.card_id = c.id AND bc.removed_at IS NULL
    JOIN boxes b ON b.id = bc.box_id
    LEFT JOIN box_placements bp ON bp.box_id = b.id AND bp.removed_at IS NULL
    LEFT JOIN archive_shelves ash ON ash.id = bp.shelf_id
    LEFT JOIN archive_sections asec ON asec.id = ash.section_id
    ORDER BY c.full_name, c.id
"""


def _freshest_key(box: dict) -> tuple[str, str, int]:
    """Ключ «свежести» активной коробки: размещение, затем добавление, затем id DESC.

    Отсутствие размещения (placed_at = None) сортируется как самое старое —
    коробка без полки не может быть «свежайшим размещением».
    """
    return (box["placed_at"] or "", box["added_at"] or "", box["box_id"])


def _place(section_name: str | None, shelf_name: str | None) -> str | None:
    if section_name and shelf_name:
        return f"{section_name} / {shelf_name}"
    return None


def _connect(db_path: str) -> sqlite3.Connection:
    """Autocommit-подключение; транзакции в apply — явные BEGIN/COMMIT."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.isolation_level = None
    return conn


def _box_view(row: dict) -> dict:
    """Коробка для отчёта/JSON: номер, статус, место, ключи свежести."""
    return {
        "box_id": row["box_id"],
        "box_number": row["box_number"],
        "box_status": row["box_status"],
        "placed_at": row["placed_at"],
        "added_at": row["added_at"],
        "place": _place(row["section_name"], row["shelf_name"]),
    }


def _decide(rows) -> tuple[list[dict], int]:
    """Разложить строки join'а по картам и вынести решение по каждой.

    Возвращает (cards, rows_to_remove):
      cards — карты с >1 активной коробкой:
        {card_id, full_name, status, actual_box, remove_boxes, skipped,
         skip_reason};
      rows_to_remove — число строк box_cards, которые будут сняты (с учётом
        дублей строк на коробку).
    """
    by_card: dict[int, dict] = {}
    order: list[int] = []
    for row in rows:
        card = by_card.get(row["card_id"])
        if card is None:
            card = {"full_name": row["full_name"], "status": row["status"],
                    "boxes": {}}
            by_card[row["card_id"]] = card
            order.append(row["card_id"])
        boxes = card["boxes"]
        prev = boxes.get(row["box_id"])
        # На коробку — свежайшее активное размещение (страховка от дублей
        # box_placements), как в SearchService.
        if prev is None or (row["placed_at"] or "") > (prev["placed_at"] or ""):
            boxes[row["box_id"]] = dict(row)

    cards: list[dict] = []
    rows_to_remove = 0
    for card_id in order:
        card = by_card[card_id]
        box_list = list(card["boxes"].values())
        if len(box_list) < 2:
            continue  # одна активная коробка — не остаток
        ordered = sorted(box_list, key=_freshest_key, reverse=True)
        allowed = CARD_STATUS_TO_BOX_STATUSES.get(card["status"], ())
        preferred = [b for b in ordered if b["box_status"] in allowed]
        actual = (preferred or ordered)[0]
        remove = [b for b in ordered if b["box_id"] != actual["box_id"]]
        decision = {
            "card_id": card_id,
            "full_name": card["full_name"],
            "status": card["status"],
            "actual_box": _box_view(actual),
            "remove_boxes": [_box_view(b) for b in remove],
            "skipped": False,
            "skip_reason": None,
        }
        if not remove:
            # Страховка: актуальную не выделили — карту не трогаем (в отчёт).
            decision.update(
                skipped=True,
                skip_reason="не удалось выделить актуальную коробку",
                actual_box=None,
            )
        else:
            remove_ids = {b["box_id"] for b in remove}
            rows_to_remove += sum(
                1 for r in rows
                if r["card_id"] == card_id and r["box_id"] in remove_ids
            )
        cards.append(decision)
    return cards, rows_to_remove


def _backup(conn: sqlite3.Connection, db_path: str) -> str:
    """Бэкап <db>.bak-<YYYYmmdd-HHMMSS> через Connection.backup() (живой WAL ок)."""
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = f"{db_path}.bak-{ts}"
    if os.path.exists(path):
        raise FileExistsError(f"Бэкап уже существует: {path} (повторите через секунду)")
    dest = sqlite3.connect(path)
    try:
        conn.backup(dest)
    finally:
        dest.close()
    return path


def _apply(conn: sqlite3.Connection, cards: list[dict]) -> tuple[int, list[int]]:
    """Снять остатки (removed_at=now). Вернёт (снято строк, карты без коробок)."""
    removed_rows = 0
    reset_cards: list[int] = []
    for decision in cards:
        if decision["skipped"]:
            continue
        ids = [b["box_id"] for b in decision["remove_boxes"]]
        placeholders = ",".join("?" for _ in ids)
        cur = conn.execute(
            f"UPDATE box_cards SET removed_at = datetime('now','localtime') "
            f"WHERE card_id = ? AND box_id IN ({placeholders}) AND removed_at IS NULL",
            [decision["card_id"], *ids],
        )
        removed_rows += cur.rowcount
        # Страховка: если активных коробок не осталось (не должно случиться —
        # актуальная сохраняется) — карта обратно в филиал.
        left = conn.execute(
            "SELECT COUNT(*) FROM box_cards WHERE card_id = ? AND removed_at IS NULL",
            (decision["card_id"],),
        ).fetchone()[0]
        if left == 0:
            conn.execute("UPDATE cards SET status = 'in_branch' WHERE id = ?",
                         (decision["card_id"],))
            reset_cards.append(decision["card_id"])
    return removed_rows, reset_cards


def run(db_path: str | None = None, apply: bool = False, backup: bool = True) -> dict:
    """Главная функция (импортируется тестами): читает БД, считает решение.

    apply=False — dry-run: только чтение, БД не меняется, бэкап не создаётся.
    apply=True — до записи бэкап (если backup), затем одна транзакция UPDATE.
    Возвращает отчёт-словарь {mode, db, backup, cards, summary}.
    """
    db_path = db_path or DEFAULT_DB
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"БД не найдена: {db_path}")
    conn = _connect(db_path)
    try:
        rows = conn.execute(_SELECT_ACTIVE).fetchall()
        cards, rows_to_remove = _decide(rows)
        backup_path: str | None = None
        removed_rows = 0
        reset_cards: list[int] = []
        if apply:
            if backup:
                backup_path = _backup(conn, db_path)
            conn.execute("BEGIN")
            try:
                removed_rows, reset_cards = _apply(conn, cards)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        summary = {
            "cards_with_stale": len(cards),
            "rows_to_remove": rows_to_remove,
            "removed_rows": removed_rows,
            "skipped_cards": sum(1 for c in cards if c["skipped"]),
            "status_reset_cards": reset_cards,
        }
        return {
            "mode": "apply" if apply else "dry-run",
            "db": db_path,
            "backup": backup_path,
            "cards": cards,
            "summary": summary,
        }
    finally:
        conn.close()


def render_text(report: dict) -> str:
    """Человекочитаемый отчёт: шапка, таблица карт, сводка."""
    lines = []
    mode = report["mode"]
    lines.append("Режим: " + ("apply — изменения записаны" if mode == "apply"
                              else "dry-run — ничего не записано"))
    lines.append(f"База: {report['db']}")
    if report["backup"]:
        lines.append(f"Бэкап: {report['backup']}")

    cards = report["cards"]
    if not cards:
        lines.append("")
        lines.append("Остатков не найдено: нет карт с более чем одной активной коробкой.")
    else:
        rows = []
        for c in cards:
            keep = c["actual_box"]
            if keep:
                bits = [f"{keep['box_number']} ({keep['box_status']}"]
                if keep["place"]:
                    bits[0] += f", {keep['place']}"
                bits[0] += ")"
                keep_txt = bits[0]
            else:
                keep_txt = "—"
            remove_txt = ", ".join(b["box_number"] for b in c["remove_boxes"]) or "—"
            rows.append((c["full_name"], c["status"], keep_txt, remove_txt))
        headers = ("ФИО", "Статус", "Оставляем", "Снимаем")
        widths = [len(headers[i]) for i in range(4)]
        for r in rows:
            for i in range(4):
                widths[i] = max(widths[i], len(r[i]))
        sep = "-+-".join("-" * w for w in widths)
        lines.append("")
        lines.append(" | ".join(h.ljust(widths[i]) for i, h in enumerate(headers)))
        lines.append(sep)
        for r in rows:
            lines.append(" | ".join(r[i].ljust(widths[i]) for i in range(4)))

    lines.append("")
    s = report["summary"]
    lines.append(f"Карт с остатками: {s['cards_with_stale']}")
    if mode == "apply":
        lines.append(f"Снято строк box_cards: {s['removed_rows']}")
    else:
        lines.append(f"Снимаемых строк box_cards: {s['rows_to_remove']}")
    lines.append(f"Пропущено карт (актуальная не определяется): {s['skipped_cards']}")
    if s["status_reset_cards"]:
        lines.append(f"Статус сброшен в in_branch (не осталось коробок): "
                     f"{', '.join(str(i) for i in s['status_reset_cards'])}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="cleanup_stale_box_cards.py",
        description="Чистка «остатков»: карта в >1 активной коробке. "
                    "Без --apply — dry-run (ничего не меняет).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--db", default=None,
        help=f"путь к БД SQLite (по умолчанию: env MEDARCHIVE_DB_PATH или {DEFAULT_DB})",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="записывать изменения (иначе dry-run preview); при apply до записи "
             "создаётся бэкап <db>.bak-<ts>",
    )
    parser.add_argument(
        "--no-backup", action="store_true",
        help="при --apply не создавать бэкап (не рекомендуется)",
    )
    parser.add_argument(
        "--json", action="store_true", dest="as_json",
        help="вывести отчёт одним JSON-объектом (для тестов/автоматизации)",
    )
    args = parser.parse_args(argv)

    try:
        report = run(db_path=args.db, apply=args.apply, backup=not args.no_backup)
    except FileNotFoundError as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return 1
    except (sqlite3.Error, FileExistsError) as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return 1

    if args.as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_text(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
