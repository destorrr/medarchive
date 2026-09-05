"""Скрипт чистки остатков scripts/cleanup_stale_box_cards.py (Задача 2, 2026-09-05).

Скрипт standalone (только stdlib sqlite3) — тесты поднимают свою БД схемой из
migrations.SCHEMA, сидят данные и дёргают функции/CLI скрипта через importlib.
Покрывает: dry-run ничего не меняет, apply снимает только остатки (актуальная
коробка сохраняется), карта с одной коробкой не трогается, выбор актуальной по
правилу search_service (placed_at → added_at → id DESC), бэкап до записи.
"""
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

from medarchive.adapters.sqlite.migrations import SCHEMA

_SCRIPT = (Path(__file__).resolve().parent.parent
           / "scripts" / "cleanup_stale_box_cards.py")


@pytest.fixture(scope="module")
def script():
    spec = importlib.util.spec_from_file_location("cleanup_stale_box_cards", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def db_path(tmp_path):
    """Свежая БД со схемой МедАрхива и справочниками (филиал, юзер, полки)."""
    path = str(tmp_path / "test.db")
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO branches (id, name, code) VALUES (1, 'Филиал 1', 'F1')")
    conn.execute(
        "INSERT INTO users (id, username, password_hash, role, branch_id) "
        "VALUES (1, 'u1', 'h', 'branch', 1)"
    )
    conn.execute("INSERT INTO archive_sections (id, name) VALUES (1, 'Секция А')")
    conn.execute(
        "INSERT INTO archive_shelves (id, section_id, name, qr_code) VALUES "
        "(1, 1, 'Полка 1', 'A-1'), (2, 1, 'Полка 2', 'A-2')"
    )
    conn.commit()
    conn.close()
    return path


# --- сиды (синхронный sqlite3) ------------------------------------------------

def _card(conn, cid, name="Иванов Иван Иванович", status="placed"):
    conn.execute(
        "INSERT INTO cards (id, full_name, status) VALUES (?, ?, ?)",
        (cid, name, status),
    )


def _box(conn, bid, number, status="placed"):
    conn.execute(
        "INSERT INTO boxes (id, box_number, origin_branch_id, created_by, status) "
        "VALUES (?, ?, 1, 1, ?)",
        (bid, number, status),
    )


def _add(conn, box_id, card_id, added_at=None):
    """Активная запись карты в коробке (removed_at IS NULL)."""
    if added_at is None:
        conn.execute(
            "INSERT INTO box_cards (box_id, card_id) VALUES (?, ?)",
            (box_id, card_id),
        )
    else:
        conn.execute(
            "INSERT INTO box_cards (box_id, card_id, added_at) VALUES (?, ?, ?)",
            (box_id, card_id, added_at),
        )


def _place(conn, box_id, shelf_id, placed_at):
    """Активное размещение коробки на полке."""
    conn.execute(
        "INSERT INTO box_placements (box_id, shelf_id, placed_at) VALUES (?, ?, ?)",
        (box_id, shelf_id, placed_at),
    )


def _active_boxes(db_path, card_id):
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT box_id FROM box_cards WHERE card_id = ? AND removed_at IS NULL",
        (card_id,),
    ).fetchall()
    conn.close()
    return sorted(r[0] for r in rows)


def _removed_boxes(db_path, card_id):
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT box_id FROM box_cards WHERE card_id = ? AND removed_at IS NOT NULL",
        (card_id,),
    ).fetchall()
    conn.close()
    return sorted(r[0] for r in rows)


def _seed_two_placed_boxes(db_path):
    """Карта 1 (placed) в B-0010 (январь) и B-0011 (февраль)."""
    conn = sqlite3.connect(db_path)
    _card(conn, 1)
    _box(conn, 10, "B-0010")
    _box(conn, 11, "B-0011")
    _add(conn, 10, 1)
    _add(conn, 11, 1)
    _place(conn, 10, 1, "2026-01-01 10:00:00")
    _place(conn, 11, 2, "2026-02-01 10:00:00")
    conn.commit()
    conn.close()


class TestDryRun:
    def test_dry_run_readonly_and_report_lists_keep_and_remove(self, script, db_path):
        """Dry-run: ничего не меняет; отчёт показывает обе коробки — «Оставляем»
        и «Снимаем»."""
        _seed_two_placed_boxes(db_path)

        report = script.run(db_path)  # apply=False

        assert report["mode"] == "dry-run"
        assert report["backup"] is None  # в dry-run бэкап не создаётся
        # БД не тронута: обе записи активны.
        assert _active_boxes(db_path, 1) == [10, 11]
        assert _removed_boxes(db_path, 1) == []

        assert report["summary"]["cards_with_stale"] == 1
        assert report["summary"]["rows_to_remove"] == 1
        assert report["summary"]["skipped_cards"] == 0

        cards = report["cards"]
        assert len(cards) == 1
        # Актуальная — свежайшая по placed_at (февраль), остаток — B-0010.
        assert cards[0]["actual_box"]["box_number"] == "B-0011"
        assert [b["box_number"] for b in cards[0]["remove_boxes"]] == ["B-0010"]

        text = script.render_text(report)
        assert "Снимаем" in text and "Оставляем" in text
        assert "B-0011" in text and "B-0010" in text

    def test_no_stale_cards_report(self, script, db_path):
        """В базе без остатков отчёт пустой, dry-run корректен."""
        conn = sqlite3.connect(db_path)
        _card(conn, 1)
        _box(conn, 10, "B-0010")
        _add(conn, 10, 1)
        conn.commit()
        conn.close()

        report = script.run(db_path)
        assert report["cards"] == []
        assert report["summary"]["cards_with_stale"] == 0
        assert "Остатков не найдено" in script.render_text(report)


class TestApply:
    def test_apply_removes_stale_keeps_actual(self, script, db_path):
        """Apply: снимаются только остатки; актуальная коробка остаётся."""
        _seed_two_placed_boxes(db_path)

        report = script.run(db_path, apply=True, backup=False)

        assert report["mode"] == "apply"
        assert report["summary"]["removed_rows"] == 1
        assert report["summary"]["status_reset_cards"] == []
        # Остаток снят, актуальная (B-0011) активна — всего одна активная коробка.
        assert _active_boxes(db_path, 1) == [11]
        assert _removed_boxes(db_path, 1) == [10]
        # Статус карты не тронут: коробка осталась.
        conn = sqlite3.connect(db_path)
        status = conn.execute("SELECT status FROM cards WHERE id = 1").fetchone()[0]
        conn.close()
        assert status == "placed"

    def test_single_box_card_untouched(self, script, db_path):
        """Карта с одной активной коробкой не является остатком — не трогается."""
        conn = sqlite3.connect(db_path)
        _card(conn, 1)
        _box(conn, 10, "B-0010")
        _add(conn, 10, 1)
        _place(conn, 10, 1, "2026-01-01 10:00:00")
        conn.commit()
        conn.close()

        report = script.run(db_path, apply=True, backup=False)

        assert report["cards"] == []
        assert _active_boxes(db_path, 1) == [10]
        assert _removed_boxes(db_path, 1) == []

    def test_backup_created_before_changes(self, script, db_path):
        """При --apply создаётся .bak-<ts>; в бэкапе остатки ещё на месте."""
        _seed_two_placed_boxes(db_path)

        report = script.run(db_path, apply=True)  # backup=True по умолчанию

        backup_path = report["backup"]
        assert backup_path is not None
        assert backup_path.startswith(db_path + ".bak-")
        assert Path(backup_path).exists()

        # В бэкапе — состояние ДО изменений: обе записи активны.
        conn = sqlite3.connect(backup_path)
        active = conn.execute(
            "SELECT COUNT(*) FROM box_cards WHERE removed_at IS NULL"
        ).fetchone()[0]
        removed = conn.execute(
            "SELECT COUNT(*) FROM box_cards WHERE removed_at IS NOT NULL"
        ).fetchone()[0]
        conn.close()
        assert active == 2 and removed == 0

        # В рабочей базе остаток уже снят.
        assert _active_boxes(db_path, 1) == [11]
        assert _removed_boxes(db_path, 1) == [10]


class TestActualBoxRule:
    def test_freshest_placed_box_by_placed_at(self, script, db_path):
        """Две placed-коробки с разными placed_at: сохраняется свежайшая —
        даже если у неё более ранний added_at (placed_at главенствует)."""
        conn = sqlite3.connect(db_path)
        _card(conn, 1)
        _box(conn, 10, "B-0010")
        _box(conn, 11, "B-0011")
        _add(conn, 10, 1, added_at="2026-06-01 00:00:00")  # позже добавлена
        _add(conn, 11, 1, added_at="2026-01-01 00:00:00")  # раньше добавлена
        _place(conn, 10, 1, "2026-01-01 10:00:00")
        _place(conn, 11, 2, "2026-02-01 10:00:00")  # свежайшее размещение
        conn.commit()
        conn.close()

        report = script.run(db_path, apply=True, backup=False)

        assert report["cards"][0]["actual_box"]["box_number"] == "B-0011"
        assert _active_boxes(db_path, 1) == [11]
        assert _removed_boxes(db_path, 1) == [10]

    def test_tie_break_by_box_id_desc(self, script, db_path):
        """Одинаковые placed_at и added_at — актуальная коробка с большим id."""
        conn = sqlite3.connect(db_path)
        _card(conn, 1)
        _box(conn, 10, "B-0010")
        _box(conn, 11, "B-0011")
        _add(conn, 10, 1, added_at="2026-01-01 10:00:00")
        _add(conn, 11, 1, added_at="2026-01-01 10:00:00")
        _place(conn, 10, 1, "2026-01-01 10:00:00")
        _place(conn, 11, 2, "2026-01-01 10:00:00")
        conn.commit()
        conn.close()

        report = script.run(db_path)

        assert report["cards"][0]["actual_box"]["box_number"] == "B-0011"
        assert [b["box_number"] for b in report["cards"][0]["remove_boxes"]] == \
            ["B-0010"]

    def test_status_chain_prefers_matching_status_over_freshness(self, script, db_path):
        """in_archive-карта: актуальная — received-коробка (пусть и старше),
        а не свежайшая placed."""
        conn = sqlite3.connect(db_path)
        _card(conn, 1, status="in_archive")
        _box(conn, 10, "B-0010", status="received")
        _box(conn, 11, "B-0011", status="placed")
        _add(conn, 10, 1)
        _add(conn, 11, 1)
        _place(conn, 10, 1, "2026-01-01 10:00:00")
        _place(conn, 11, 2, "2026-02-01 10:00:00")  # свежайшая, но не подходит
        conn.commit()
        conn.close()

        report = script.run(db_path, apply=True, backup=False)

        assert report["cards"][0]["actual_box"]["box_number"] == "B-0010"
        assert _active_boxes(db_path, 1) == [10]
        assert _removed_boxes(db_path, 1) == [11]

    def test_in_transit_without_placement_falls_back_to_freshest_active(self,
                                                                       script,
                                                                       db_path):
        """Нет подходящей по статусу — актуальная свежайшая активная
        (in_transit-коробки без размещений: placed_at пуст)."""
        conn = sqlite3.connect(db_path)
        _card(conn, 1, status="in_transit")
        _box(conn, 10, "B-0010", status="in_transit")
        _box(conn, 11, "B-0011", status="in_transit")
        _add(conn, 10, 1, added_at="2026-01-01 10:00:00")
        _add(conn, 11, 1, added_at="2026-02-01 10:00:00")
        conn.commit()
        conn.close()

        report = script.run(db_path, apply=True, backup=False)

        assert report["cards"][0]["actual_box"]["box_number"] == "B-0011"
        assert _active_boxes(db_path, 1) == [11]
        assert _removed_boxes(db_path, 1) == [10]


class TestCli:
    def test_json_output_dry_run(self, script, db_path, capsys):
        """CLI --json: валидный JSON-отчёт, режим dry-run, БД не тронута."""
        _seed_two_placed_boxes(db_path)

        rc = script.main(["--db", db_path, "--json"])

        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data["mode"] == "dry-run"
        assert data["summary"]["rows_to_remove"] == 1
        assert _active_boxes(db_path, 1) == [10, 11]

    def test_missing_db_exit_code(self, script, capsys):
        """Несуществующая БД — ошибка в stderr и код возврата 1."""
        rc = script.main(["--db", "/nonexistent/medarchive.db"])
        assert rc == 1
        assert "ОШИБКА" in capsys.readouterr().err
