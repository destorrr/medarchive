# «Мои открытые коробки» + сообщение о дубликате карты — Implementation Plan

> **For implementer:** Use TDD throughout. Write failing test first. Watch it fail. Then implement.

**Goal:** На `/packing` показывать все открытые коробки пользователя с возможностью «Перейти» в любую, а при повторном сканировании карты показывать «Карта уже в этой коробке».

**Architecture:** Выбор активной коробки хранится в БД (`users.active_box_id`). `BoxRepositoryImpl.add_card()` возвращает `bool` (дубликат → False), `BoxService.scan_card()` бросает `DuplicateCardError`, роутер показывает flash. Все htmx-ответы включают список открытых коробок.

**Tech Stack:** FastAPI + aiosqlite, Jinja2, htmx, pytest (asyncio).

**Дизайн-док:** `docs/plans/2026-08-28-packing-open-boxes-design.md` (утверждён).

---

### Task 1: Миграция — колонка `users.active_box_id` (свежая схема + ALTER для старых БД)

**Files:**
- Modify: `src/medarchive/adapters/sqlite/migrations.py`
- Test: `tests/adapters/sqlite/test_database.py`

**Step 1: Write the failing test** — добавить в `tests/adapters/sqlite/test_database.py`:

```python
@pytest.mark.asyncio
async def test_users_table_has_active_box_id(db):
    """Колонка users.active_box_id существует после init()."""
    cols = await db.fetch_all("PRAGMA table_info(users)")
    names = {row["name"] for row in cols}
    assert "active_box_id" in names


@pytest.mark.asyncio
async def test_migration_adds_active_box_id_to_legacy_db(test_config):
    """Существующая БД (старая схема) получает колонку через ALTER без потери данных."""
    import aiosqlite
    from medarchive.adapters.sqlite.migrations import run_migrations
    conn = await aiosqlite.connect(test_config.db_path)
    await conn.execute(
        "CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE, "
        "password_hash TEXT NOT NULL, role TEXT NOT NULL, branch_id INTEGER)"
    )
    await conn.execute(
        "INSERT INTO users (id, username, password_hash, role) VALUES (1, 'u1', 'h', 'branch')"
    )
    await conn.commit()
    await run_migrations(conn)
    cols = await conn.execute_fetchall("PRAGMA table_info(users)")
    names = {row[1] for row in cols}
    assert "active_box_id" in names
    rows = await conn.execute_fetchall("SELECT id, username FROM users")
    assert len(rows) == 1
    assert rows[0][1] == "u1"
    await conn.close()
```

**Step 2: Run test — confirm it fails**
Command: `pytest tests/adapters/sqlite/test_database.py -v`
Expected: FAIL — `active_box_id` отсутствует в `PRAGMA table_info`.

**Step 3: Implement** — в `migrations.py`:
1. В `SCHEMA`, таблица `users`: добавить строку `active_box_id INTEGER REFERENCES boxes(id)`.
2. В `run_migrations` после `executescript(SCHEMA)`:

```python
    # Инкрементальная миграция: users.active_box_id (выбранная открытая коробка)
    # для уже существующих БД, созданных до добавления колонки.
    cols = await conn.execute_fetchall("PRAGMA table_info(users)")
    if not any(row[1] == "active_box_id" for row in cols):
        await conn.execute("ALTER TABLE users ADD COLUMN active_box_id INTEGER REFERENCES boxes(id)")
    await conn.commit()
```

**Step 4: Run test — confirm it passes**
Command: `pytest tests/adapters/sqlite/test_database.py -v`
Expected: PASS (оба теста).

**Step 5: Commit**
`git add src/medarchive/adapters/sqlite/migrations.py tests/adapters/sqlite/test_database.py && git commit -m "feat: миграция users.active_box_id (схема + ALTER для старых БД)"`

---

### Task 2: Поле `active_box_id` в модели User и всех построениях User

**Files:**
- Modify: `src/medarchive/domain/models.py`, `src/medarchive/adapters/sqlite/repositories.py`, `src/medarchive/dependencies.py`
- Test: `tests/adapters/sqlite/test_repositories.py`

**Step 1: Write the failing test** — в `TestUserRepository` добавить:

```python
    async def test_user_has_active_box_id_field(self, user_repo, db):
        await db.execute(
            "INSERT INTO users (id, username, password_hash, role) VALUES (10, 'admin1', 'hash123', 'admin')"
        )
        await db.conn.commit()
        user = await user_repo.get_by_username("admin1")
        assert user.active_box_id is None
```

**Step 2: Run test — confirm it fails**
Command: `pytest tests/adapters/sqlite/test_repositories.py::TestUserRepository::test_user_has_active_box_id_field -v`
Expected: FAIL — `AttributeError: 'User' object has no attribute 'active_box_id'`.

**Step 3: Implement**
1. `domain/models.py`, класс `User`: добавить `active_box_id: int | None = None`.
2. `repositories.py`, `UserRepositoryImpl.get_by_id` / `get_by_username`: добавить `active_box_id=row["active_box_id"]` в конструктор User.
3. `dependencies.py`, `get_current_user`: добавить `active_box_id=row["active_box_id"]` в конструктор User.

**Step 4: Run test — confirm it passes**
Command: `pytest tests/adapters/sqlite/test_repositories.py::TestUserRepository -v`
Expected: PASS.

**Step 5: Commit**
`git add src/medarchive/domain/models.py src/medarchive/adapters/sqlite/repositories.py src/medarchive/dependencies.py tests/adapters/sqlite/test_repositories.py && git commit -m "feat: User.active_box_id в модели и построениях"`

---

### Task 3: BoxRepositoryImpl — `add_card→bool`, `set_active_box`, `list_open_for_user`, приоритет активной

**Files:**
- Modify: `src/medarchive/adapters/sqlite/repositories.py`
- Test: `tests/adapters/sqlite/test_repositories.py`

**Step 1: Write the failing tests** — в `TestBoxRepository` обновить `test_add_duplicate_card_to_box_ignored` (строки с `add_card`):

```python
        added1 = await box_repo.add_card(box.id, card.id)
        added2 = await box_repo.add_card(box.id, card.id)
        assert added1 is True
        assert added2 is False

        cards = await box_repo.get_cards(box.id)
        assert len(cards) == 1
```

и добавить новые (везде в начале — seed branch + user, как в существующих тестах):

```python
    async def test_set_active_box_and_priority(self, box_repo, db):
        await db.execute("INSERT INTO branches (id, name, code) VALUES (1, 'B1', 'B1')")
        await db.execute(
            "INSERT INTO users (id, username, password_hash, role) VALUES (1, 'u1', 'h', 'branch')"
        )
        await db.conn.commit()
        box1 = await box_repo.create("B-0101", origin_branch_id=1, created_by=1)
        box2 = await box_repo.create("B-0102", origin_branch_id=1, created_by=1)

        # Без выбора — fallback на последнюю открытую
        active = await box_repo.get_active_for_user(1)
        assert active.id == box2.id

        # Явный выбор имеет приоритет
        await box_repo.set_active_box(1, box1.id)
        active = await box_repo.get_active_for_user(1)
        assert active.id == box1.id

        # Выбранная закрыта → fallback на последнюю открытую
        await box_repo.update_status(box1.id, BoxStatus.ready)
        active = await box_repo.get_active_for_user(1)
        assert active.id == box2.id

        # Сброс выбора
        await box_repo.set_active_box(1, None)
        active = await box_repo.get_active_for_user(1)
        assert active.id == box2.id

    async def test_list_open_for_user(self, box_repo, card_repo, db):
        await db.execute("INSERT INTO branches (id, name, code) VALUES (1, 'B1', 'B1')")
        await db.execute(
            "INSERT INTO users (id, username, password_hash, role) VALUES (1, 'u1', 'h', 'branch')"
        )
        await db.conn.commit()
        box1 = await box_repo.create("B-0201", origin_branch_id=1, created_by=1)
        box2 = await box_repo.create("B-0202", origin_branch_id=1, created_by=1)
        await box_repo.update_status(box2.id, BoxStatus.ready)  # закрыта — не в списке
        card = await card_repo.create("Карта")
        await box_repo.add_card(box1.id, card.id)

        boxes = await box_repo.list_open_for_user(1)
        assert len(boxes) == 1
        assert boxes[0]["box_number"] == "B-0201"
        assert boxes[0]["cards_count"] == 1
        assert "created_at" in boxes[0]
```

**Step 2: Run test — confirm it fails**
Command: `pytest tests/adapters/sqlite/test_repositories.py::TestBoxRepository -v`
Expected: FAIL — `add_card` возвращает None, `get_active_for_user` не учитывает выбор, `list_open_for_user` не существует.

**Step 3: Implement** — в `repositories.py`, `BoxRepositoryImpl`:

```python
    async def get_active_for_user(self, user_id: int) -> Box | None:
        """Активная коробка пользователя.

        Приоритет: явно выбранная (users.active_box_id), если она ещё открыта.
        Fallback: последняя незакрытая (packing) коробка пользователя.
        """
        row = await self.db.fetch_one(
            """SELECT b.* FROM boxes b
               JOIN users u ON u.active_box_id = b.id
               WHERE u.id = ? AND b.status = ?""",
            (user_id, BoxStatus.packing.value),
        )
        if row is not None:
            return self._row_to_box(row)
        row = await self.db.fetch_one(
            "SELECT * FROM boxes WHERE created_by = ? AND status = ? ORDER BY id DESC LIMIT 1",
            (user_id, BoxStatus.packing.value),
        )
        if row is None:
            return None
        return self._row_to_box(row)

    async def set_active_box(self, user_id: int, box_id: int | None) -> None:
        """Запомнить выбранную пользователем коробку (или сбросить выбор)."""
        await self.db.execute(
            "UPDATE users SET active_box_id = ? WHERE id = ?",
            (box_id, user_id),
        )
        await self.db.conn.commit()

    async def list_open_for_user(self, user_id: int) -> list[dict]:
        """Все открытые (packing) коробки пользователя с числом карт и датой."""
        rows = await self.db.fetch_all(
            """SELECT b.id, b.box_number, b.created_at,
                      (SELECT COUNT(*) FROM box_cards bc
                       WHERE bc.box_id = b.id AND bc.removed_at IS NULL) AS cards_count
               FROM boxes b
               WHERE b.created_by = ? AND b.status = ?
               ORDER BY b.id DESC""",
            (user_id, BoxStatus.packing.value),
        )
        return [dict(r) for r in rows]
```

Плюс: вынести повторяющееся конструирование Box в `_row_to_box` и переиспользовать в `get_by_id`/`get_by_number`/`get_active_for_user`; в `add_card` заменить `return` на `return False`, а в конце добавить `return True`.

**Step 4: Run test — confirm it passes**
Command: `pytest tests/adapters/sqlite/test_repositories.py::TestBoxRepository -v`
Expected: PASS.

**Step 5: Commit**
`git add src/medarchive/adapters/sqlite/repositories.py tests/adapters/sqlite/test_repositories.py && git commit -m "feat: BoxRepository — add_card→bool, set_active_box, list_open_for_user, приоритет активной"`

---

### Task 4: BoxService — `DuplicateCardError`, активная коробка при создании/закрытии, `select_box`, `list_open_boxes`

**Files:**
- Modify: `src/medarchive/services/box_service.py`
- Test: `tests/services/test_box_service.py`

**Step 1: Write the failing tests** — обновить `test_scan_duplicate_in_same_box_ignored`:

```python
    async def test_scan_duplicate_raises(self, box_service, db):
        await _seed_branch_and_user(db)
        box = await box_service.create_box(branch_id=1, user_id=1)
        await box_service.scan_card(box.id, "Дубль")
        with pytest.raises(DuplicateCardError):
            await box_service.scan_card(box.id, "Дубль")

        contents = await box_service.get_box_contents(box.id)
        assert len(contents) == 1
```

и добавить (импорт: `from medarchive.services.box_service import BoxService, DuplicateCardError`):

```python
    async def test_create_box_sets_active(self, box_service, db):
        await _seed_branch_and_user(db)
        box = await box_service.create_box(branch_id=1, user_id=1)
        row = await db.fetch_one("SELECT active_box_id FROM users WHERE id = 1")
        assert row["active_box_id"] == box.id

    async def test_select_box_switches_active(self, box_service, db):
        await _seed_branch_and_user(db)
        box1 = await box_service.create_box(branch_id=1, user_id=1)
        box2 = await box_service.create_box(branch_id=1, user_id=1)
        await box_service.select_box(box1.id, user_id=1)
        row = await db.fetch_one("SELECT active_box_id FROM users WHERE id = 1")
        assert row["active_box_id"] == box1.id

    async def test_select_foreign_box_raises(self, box_service, db):
        await _seed_branch_and_user(db)
        await db.execute(
            "INSERT INTO users (id, username, password_hash, role) VALUES (2, 'u2', 'h', 'branch')"
        )
        await db.conn.commit()
        box = await box_service.create_box(branch_id=1, user_id=1)
        with pytest.raises(ValueError):
            await box_service.select_box(box.id, user_id=2)

    async def test_select_closed_box_raises(self, box_service, db):
        await _seed_branch_and_user(db)
        box = await box_service.create_box(branch_id=1, user_id=1)
        await box_service.scan_card(box.id, "Пациент 1")
        await box_service.close_box(box.id, user_id=1)
        with pytest.raises(ValueError):
            await box_service.select_box(box.id, user_id=1)

    async def test_close_active_box_resets_active(self, box_service, db):
        await _seed_branch_and_user(db)
        box = await box_service.create_box(branch_id=1, user_id=1)
        await box_service.scan_card(box.id, "Пациент 1")
        await box_service.close_box(box.id, user_id=1)
        row = await db.fetch_one("SELECT active_box_id FROM users WHERE id = 1")
        assert row["active_box_id"] is None

    async def test_list_open_boxes(self, box_service, db):
        await _seed_branch_and_user(db)
        box1 = await box_service.create_box(branch_id=1, user_id=1)
        box2 = await box_service.create_box(branch_id=1, user_id=1)
        await box_service.scan_card(box1.id, "Пациент 1")
        await box_service.close_box(box2.id, user_id=1)  # закрыть НЕЛЬЗЯ — пустая, поэтому:
        # закрываем box2 с картой, box1 остаётся открытой
        await box_service.scan_card(box2.id, "Пациент 2")
        await box_service.close_box(box2.id, user_id=1)
        boxes = await box_service.list_open_boxes(1)
        assert len(boxes) == 1
        assert boxes[0]["id"] == box1.id
```

**Step 2: Run test — confirm it fails**
Command: `pytest tests/services/test_box_service.py -v`
Expected: FAIL — `DuplicateCardError` не существует, `scan_card` не бросает, `select_box`/`list_open_boxes` нет, `active_box_id` не выставляется.

**Step 3: Implement** — `box_service.py`:

```python
class DuplicateCardError(Exception):
    """Карта уже находится в этой коробке."""
```

- `create_box`: после `box.box_number = box_number` добавить `await self.box_repo.set_active_box(user_id, box.id)`.
- `scan_card`: заменить `await self.box_repo.add_card(box.id, card.id)` на:

```python
        added = await self.box_repo.add_card(box_id, card.id)
        if not added:
            raise DuplicateCardError(f"Карта «{full_name}» уже в этой коробке")
```

- добавить методы:

```python
    async def select_box(self, box_id: int, user_id: int) -> Box:
        """Сделать коробку активной (проверка: своя, ещё открытая)."""
        box = await self.box_repo.get_by_id(box_id)
        if box is None or box.created_by != user_id or box.status != BoxStatus.packing:
            raise ValueError("Коробка не найдена или уже закрыта")
        await self.box_repo.set_active_box(user_id, box.id)
        return box

    async def list_open_boxes(self, user_id: int) -> list[dict]:
        """Все открытые коробки пользователя: id, номер, число карт, дата создания."""
        return await self.box_repo.list_open_for_user(user_id)
```

- `close_box` — сигнатура `close_box(self, box_id: int, user_id: int | None = None)`, после `update_status`:

```python
        # Если закрыли активную коробку — выбор сбрасывается (остаются другие открытые)
        if user_id is not None:
            row = await self.db.fetch_one(
                "SELECT active_box_id FROM users WHERE id = ?", (user_id,)
            )
            if row is not None and row["active_box_id"] == box_id:
                await self.box_repo.set_active_box(user_id, None)
```

**Step 4: Run test — confirm it passes**
Command: `pytest tests/services/test_box_service.py -v`
Expected: PASS.

**Step 5: Commit**
`git add src/medarchive/services/box_service.py tests/services/test_box_service.py && git commit -m "feat: BoxService — DuplicateCardError, select_box, активная коробка при create/close"`

---

### Task 5: Роутер — ловим дубликат, `POST /packing/open-box`, `close-box` с опциональным `box_id`, контекст `open_boxes`

**Files:**
- Modify: `src/medarchive/routers/packing.py`
- Test: `tests/routers/test_packing.py`

**Step 1: Write the failing tests** — добавить в `TestPackingRouter`:

```python
    async def test_duplicate_scan_shows_message(self, packing_client):
        await packing_client.post("/login", data={"username": "branch1", "password": "***"})
        await packing_client.post("/packing/new-box")
        await packing_client.post("/packing/scan-card", data={"full_name": "Иванов И.И."})
        r = await packing_client.post("/packing/scan-card", data={"full_name": "Иванов И.И."})
        assert r.status_code == 200
        assert "уже в этой коробке" in r.text
        assert r.text.count("Иванов") == 1  # карта не задвоилась

    async def test_open_box_switches_active(self, packing_client):
        await packing_client.post("/login", data={"username": "branch1", "password": "***"})
        await packing_client.post("/packing/new-box")
        await packing_client.post("/packing/new-box")
        from medarchive.dependencies import get_db
        db = await get_db()
        rows = await db.fetch_all("SELECT id FROM boxes WHERE created_by = 1 ORDER BY id")
        first_id = rows[0]["id"]
        r = await packing_client.post("/packing/open-box", data={"box_id": first_id})
        assert r.status_code == 200
        row = await db.fetch_one("SELECT active_box_id FROM users WHERE id = 1")
        assert row["active_box_id"] == first_id

    async def test_close_box_by_id_keeps_active(self, packing_client):
        await packing_client.post("/login", data={"username": "branch1", "password": "***"})
        await packing_client.post("/packing/new-box")
        await packing_client.post("/packing/new-box")
        from medarchive.dependencies import get_db
        db = await get_db()
        rows = await db.fetch_all("SELECT id FROM boxes WHERE created_by = 1 ORDER BY id")
        first_id, second_id = rows[0]["id"], rows[1]["id"]
        # во вторую коробку — карту, чтобы её можно было закрыть
        await packing_client.post("/packing/scan-card", data={"full_name": "Карта Второй"})
        # закрываем ПЕРВУЮ (неактивную) коробку из списка
        r = await packing_client.post("/packing/close-box", data={"box_id": first_id})
        assert r.status_code == 303
        row = await db.fetch_one("SELECT status FROM boxes WHERE id = ?", (first_id,))
        assert row["status"] == "ready"
        row = await db.fetch_one("SELECT active_box_id FROM users WHERE id = 1")
        assert row["active_box_id"] == second_id  # активная не тронута
```

**Step 2: Run test — confirm it fails**
Command: `pytest tests/routers/test_packing.py -v`
Expected: FAIL — дубликат без сообщения; `POST /packing/open-box` → 404; `close-box` без `box_id` не принимает.

**Step 3: Implement** — переписать `routers/packing.py`:

```python
"""Роутер упаковки коробок (роль: Филиал).

Активная коробка пользователя — выбранная им открытая коробка
(users.active_box_id, хранится в БД, переживает логин/логаут/рестарт).
На странице видны все открытые коробки пользователя: можно переключиться
на любую (продолжить сканирование) или закрыть её.
"""
from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import RedirectResponse
from medarchive.dependencies import get_db, require_role
from medarchive.domain.models import User
from medarchive.domain.statuses import UserRole
from medarchive.adapters.sqlite.repositories import CardRepositoryImpl, BoxRepositoryImpl
from medarchive.services.box_service import BoxService, DuplicateCardError

router = APIRouter()


async def _active_box(db, user: User):
    """Возвращает (box, cards) для активной коробки пользователя или (None, [])."""
    box_repo = BoxRepositoryImpl(db)
    box = await box_repo.get_active_for_user(user.id)
    if box is None:
        return None, []
    cards = await box_repo.get_cards(box.id)
    return box, cards


async def _fragment_ctx(db, user, request, box, cards, error=None) -> dict:
    """Общий контекст фрагмента: содержимое активной коробки + список открытых коробок."""
    box_repo = BoxRepositoryImpl(db)
    open_boxes = await box_repo.list_open_for_user(user.id)
    return {
        "request": request,
        "error": error,
        "box": box,
        "cards": cards,
        "cards_count": len(cards),
        "active_box_id": box.id if box else None,
        "open_boxes": open_boxes,
    }


async def _page_ctx(db, user, request, box, cards) -> dict:
    """Полный контекст страницы /packing (шапка + фрагмент)."""
    ctx = await _fragment_ctx(db, user, request, box, cards)
    ctx.update({
        "username": user.username,
        "role_label": "Филиал",
        "menu_items": [],
    })
    return ctx


@router.get("/packing")
async def packing_page(request: Request, user: User = Depends(require_role(UserRole.branch, UserRole.admin))):
    db = await get_db()
    box, cards = await _active_box(db, user)
    ctx = await _page_ctx(db, user, request, box, cards)
    return request.app.state.templates.TemplateResponse(request=request, name="packing.html", context=ctx)


@router.post("/packing/new-box")
async def new_box(request: Request, user: User = Depends(require_role(UserRole.branch, UserRole.admin))):
    db = await get_db()
    card_repo = CardRepositoryImpl(db)
    box_repo = BoxRepositoryImpl(db)
    service = BoxService(db, card_repo, box_repo)
    box = await service.create_box(branch_id=user.branch_id or 1, user_id=user.id)
    ctx = await _page_ctx(db, user, request, box, [])
    return request.app.state.templates.TemplateResponse(request=request, name="packing.html", context=ctx)


@router.post("/packing/scan-card")
async def scan_card(request: Request, full_name: str = Form(...), user: User = Depends(require_role(UserRole.branch, UserRole.admin))):
    db = await get_db()
    box, _ = await _active_box(db, user)
    if box is None:
        ctx = await _fragment_ctx(db, user, request, None, [], error="Сначала создайте коробку")
        return request.app.state.templates.TemplateResponse(request=request, name="packing_fragment.html", context=ctx)
    card_repo = CardRepositoryImpl(db)
    box_repo = BoxRepositoryImpl(db)
    service = BoxService(db, card_repo, box_repo)
    try:
        await service.scan_card(box.id, full_name)
    except DuplicateCardError as exc:
        cards = await service.get_box_contents(box.id)
        ctx = await _fragment_ctx(db, user, request, box, cards, error=str(exc))
        return request.app.state.templates.TemplateResponse(request=request, name="packing_fragment.html", context=ctx)
    cards = await service.get_box_contents(box.id)
    ctx = await _fragment_ctx(db, user, request, box, cards)
    return request.app.state.templates.TemplateResponse(request=request, name="packing_fragment.html", context=ctx)


@router.post("/packing/remove-card")
async def remove_card(request: Request, card_id: int = Form(...), user: User = Depends(require_role(UserRole.branch, UserRole.admin))):
    db = await get_db()
    box, _ = await _active_box(db, user)
    if box is None:
        ctx = await _fragment_ctx(db, user, request, None, [], error="Сначала создайте коробку")
        return request.app.state.templates.TemplateResponse(request=request, name="packing_fragment.html", context=ctx)
    card_repo = CardRepositoryImpl(db)
    box_repo = BoxRepositoryImpl(db)
    service = BoxService(db, card_repo, box_repo)
    await service.remove_card(box.id, card_id)
    cards = await service.get_box_contents(box.id)
    ctx = await _fragment_ctx(db, user, request, box, cards)
    return request.app.state.templates.TemplateResponse(request=request, name="packing_fragment.html", context=ctx)


@router.post("/packing/open-box")
async def open_box(request: Request, box_id: int = Form(...), user: User = Depends(require_role(UserRole.branch, UserRole.admin))):
    """Переключиться на другую открытую коробку (продолжить её упаковку)."""
    db = await get_db()
    card_repo = CardRepositoryImpl(db)
    box_repo = BoxRepositoryImpl(db)
    service = BoxService(db, card_repo, box_repo)
    try:
        box = await service.select_box(box_id, user.id)
    except ValueError as exc:
        active, _ = await _active_box(db, user)
        cards = await service.get_box_contents(active.id) if active else []
        ctx = await _fragment_ctx(db, user, request, active, cards, error=str(exc))
        return request.app.state.templates.TemplateResponse(request=request, name="packing_fragment.html", context=ctx)
    cards = await service.get_box_contents(box.id)
    ctx = await _fragment_ctx(db, user, request, box, cards)
    return request.app.state.templates.TemplateResponse(request=request, name="packing_fragment.html", context=ctx)


@router.post("/packing/close-box")
async def close_box(request: Request, box_id: int | None = Form(default=None), user: User = Depends(require_role(UserRole.branch, UserRole.admin))):
    db = await get_db()
    card_repo = CardRepositoryImpl(db)
    box_repo = BoxRepositoryImpl(db)
    service = BoxService(db, card_repo, box_repo)
    if box_id is None:
        box, _ = await _active_box(db, user)
        if box is None:
            return RedirectResponse(url="/packing", status_code=303)
        box_id = box.id
    await service.close_box(box_id, user_id=user.id)
    return RedirectResponse(url=f"/transfer?box_id={box_id}", status_code=303)
```

**Step 4: Run test — confirm it passes**
Command: `pytest tests/routers/test_packing.py -v`
Expected: PASS (все, включая существующие).

**Step 5: Commit**
`git add src/medarchive/routers/packing.py tests/routers/test_packing.py && git commit -m "feat: /packing — open-box, close-box по id, flash о дубликате карты"`

---

### Task 6: Шаблон — блок «📂 Мои открытые коробки» + «➕ Новая коробка» всегда

**Files:**
- Modify: `src/medarchive/templates/packing_fragment.html`
- Test: `tests/routers/test_packing.py`

**Step 1: Write the failing tests** — добавить в `TestPackingRouter`:

```python
    async def test_packing_page_lists_open_boxes(self, packing_client):
        await packing_client.post("/login", data={"username": "branch1", "password": "***"})
        await packing_client.post("/packing/new-box")          # B-0001
        await packing_client.post("/packing/scan-card", data={"full_name": "Иванов И.И."})
        await packing_client.post("/packing/new-box")          # B-0002
        await packing_client.post("/packing/scan-card", data={"full_name": "Петров П.П."})
        await packing_client.post("/packing/scan-card", data={"full_name": "Сидоров С.С."})

        r = await packing_client.get("/packing")
        assert r.status_code == 200
        assert "Мои открытые коробки" in r.text
        assert "B-0001" in r.text and "B-0002" in r.text       # обе открытые видны
        assert "Перейти" in r.text                              # кнопка переключения
        assert "Новая коробка" in r.text                        # видна всегда
        assert r.text.count("Перейти") == 1                     # только у неактивной
```

**Step 2: Run test — confirm it fails**
Command: `pytest tests/routers/test_packing.py::TestPackingRouter::test_packing_page_lists_open_boxes -v`
Expected: FAIL — «Мои открытые коробки» / «Перейти» в HTML отсутствуют.

**Step 3: Implement** — `packing_fragment.html`: после блока активной коробки (перед `{% if not box %}`) вставить блок списка и сделать «➕ Новая коробка» видимой всегда:

```html
{% if open_boxes %}
<div class="card" style="margin-top:16px">
    <h3 style="margin-bottom:12px">📂 Мои открытые коробки</h3>
    <table style="width:100%;border-collapse:collapse">
        <thead>
            <tr style="border-bottom:2px solid #eee;text-align:left">
                <th style="padding:8px">Коробка</th>
                <th style="padding:8px">Карт</th>
                <th style="padding:8px">Создана</th>
                <th style="padding:8px"></th>
            </tr>
        </thead>
        <tbody>
            {% for ob in open_boxes %}
            <tr style="border-bottom:1px solid #f0f0f0">
                <td style="padding:8px;font-weight:600">{{ ob.box_number }}</td>
                <td style="padding:8px">{{ ob.cards_count }}</td>
                <td style="padding:8px;color:#888">{{ ob.created_at }}</td>
                <td style="padding:8px;text-align:right">
                    {% if active_box_id == ob.id %}
                    <span style="background:#e8f5e9;color:#2e7d32;border-radius:12px;padding:2px 10px;font-size:12px">● активная</span>
                    {% else %}
                    <form hx-post="/packing/open-box" hx-target="#main-content" hx-swap="innerHTML" style="display:inline">
                        <input type="hidden" name="box_id" value="{{ ob.id }}">
                        <button type="submit" class="btn"
                                style="background:#eef1f6;color:#1a1a2e;padding:4px 10px;font-size:13px">Перейти</button>
                    </form>
                    {% endif %}
                </td>
            </tr>
            {% endfor %}
        </tbody>
    </table>
</div>
{% endif %}

<div style="margin-top:16px">
    <form method="post" action="/packing/new-box">
        <button type="submit" class="btn btn-primary">➕ Новая коробка</button>
    </form>
</div>
```

Удалить старый блок `{% if not box %} … Новая коробка … {% endif %}` (он заменяется безусловным).

**Step 4: Run test — confirm it passes**
Command: `pytest tests/routers/test_packing.py -v`
Expected: PASS.

**Step 5: Commit**
`git add src/medarchive/templates/packing_fragment.html tests/routers/test_packing.py && git commit -m "feat: /packing — блок «Мои открытые коробки» с кнопкой «Перейти»"`

---

## Финальная проверка

```bash
pytest -q
```

Ожидание: все тесты зелёные (было 191, добавится ~12). Затем:
- `git status` чистый, `git log --oneline -8` показывает 6 коммитов задач.

## Scope boundaries
- Не трогаем /transfer, receive, остальные роутеры.
- «Карта уже в другой коробке» — вне scope.
- Поиск/пагинация по коробкам — вне scope.
