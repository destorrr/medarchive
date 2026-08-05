# Этап 0 — Каркас и инфраструктура: Implementation Plan

> **Для исполнителя:** TDD строго. Сначала падающий тест, запустить, убедиться что упал. Потом реализация.

**Goal:** Работающее веб-приложение FastAPI с аутентификацией (4 роли), луковой структурой, SQLite-БД со всеми 12 таблицами, базовым лейаутом и дашбордом-заглушкой.

**Architecture:** FastAPI + Jinja2/HTMX + SQLite, луковые слои (domain → services → repositories → adapters/sqlite → routers). Монолит с server-side rendering.

**Tech Stack:** Python 3.11+, FastAPI, Jinja2, aiosqlite, passlib[bcrypt], itsdangerous (сессии), pytest, httpx (TestClient)

**Design doc:** `docs/plans/2026-08-05-medarchive-design.md`

---

## Task 0: Инициализация проекта

**Files:**
- Create: `pyproject.toml`
- Create: `src/medarchive/__init__.py`

**Step 1: Write `pyproject.toml`**

```toml
[project]
name = "medarchive"
version = "0.1.0"
description = "Система управления архивом медицинских карт"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.30.0",
    "jinja2>=3.1.0",
    "aiosqlite>=0.20.0",
    "passlib[bcrypt]>=1.7.4",
    "itsdangerous>=2.1.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.24.0",
    "httpx>=0.27.0",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

**Step 2: Install dependencies**

```bash
cd projects/medarchive
pip install -e ".[dev]"
```

**Step 3: Create `src/medarchive/__init__.py`**

```python
"""МедАрхив — система управления архивом медицинских карт."""
__version__ = "0.1.0"
```

**Step 4: Commit**

```bash
git add pyproject.toml src/
git commit -m "feat: инициализация проекта МедАрхив (pyproject.toml + зависимости)"
```

---

## Task 1: Конфигурация

**Files:**
- Create: `src/medarchive/config.py`
- Create: `tests/test_config.py`

**Step 1: Write failing test**

```python
# tests/test_config.py
import os
import tempfile
from medarchive.config import Config, load_config


class TestConfig:
    def test_defaults(self):
        """Конфиг без переменных окружения имеет все поля с разумными дефолтами."""
        cfg = Config()
        assert cfg.db_path == "medarchive.db"
        assert cfg.secret_key is None  # должен быть установлен явно или из env

    def test_from_env(self):
        """Переменные окружения переопределяют дефолты."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            os.environ["MEDARCHIVE_DB_PATH"] = db_path
            os.environ["MEDARCHIVE_SECRET_KEY"] = "test-secret-42"
            cfg = Config()
            assert cfg.db_path == db_path
            assert cfg.secret_key == "test-secret-42"
        finally:
            os.unlink(db_path)
            os.environ.pop("MEDARCHIVE_DB_PATH", None)
            os.environ.pop("MEDARCHIVE_SECRET_KEY", None)
```

**Step 2: Run test — confirm FAIL**

```bash
python -m pytest tests/test_config.py -v
# Expected: FAIL — ModuleNotFoundError: No module named 'medarchive.config'
```

**Step 3: Write `src/medarchive/config.py`**

```python
"""Конфигурация приложения."""
import os
from dataclasses import dataclass, field


@dataclass
class Config:
    db_path: str = field(
        default_factory=lambda: os.environ.get("MEDARCHIVE_DB_PATH", "medarchive.db")
    )
    secret_key: str | None = field(
        default_factory=lambda: os.environ.get("MEDARCHIVE_SECRET_KEY")
    )

    @property
    def effective_secret_key(self) -> str:
        if self.secret_key is None:
            raise RuntimeError(
                "MEDARCHIVE_SECRET_KEY not set — "
                "generate with: python -c 'import secrets; print(secrets.token_urlsafe(32))'"
            )
        return self.secret_key


def load_config() -> Config:
    return Config()
```

**Step 4: Run test — confirm PASS**

```bash
python -m pytest tests/test_config.py -v
# Expected: PASS (2 tests)
```

**Step 5: Commit**

```bash
git add src/medarchive/config.py tests/test_config.py
git commit -m "feat: конфигурация приложения (Config dataclass + env vars)"
```

---

## Task 2: Доменные модели (статусы + сущности)

**Files:**
- Create: `src/medarchive/domain/__init__.py`
- Create: `src/medarchive/domain/statuses.py`
- Create: `src/medarchive/domain/models.py`
- Create: `tests/domain/__init__.py`
- Create: `tests/domain/test_statuses.py`
- Create: `tests/domain/test_models.py`

**Step 1: Write failing test for status enums**

```python
# tests/domain/test_statuses.py
import pytest
from medarchive.domain.statuses import (
    CardStatus,
    BoxStatus,
    ShipmentStatus,
    RequestStatus,
    UserRole,
    CARD_STATUS_TRANSITIONS,
    BOX_STATUS_TRANSITIONS,
)


class TestCardStatus:
    def test_all_statuses_defined(self):
        expected = {"in_branch", "in_box", "in_transit", "in_archive", "placed", "pending_return"}
        assert {s.value for s in CardStatus} == expected

    @pytest.mark.parametrize("from_s,to_s", [
        (CardStatus.in_branch, CardStatus.in_box),
        (CardStatus.in_box, CardStatus.in_transit),
        (CardStatus.in_transit, CardStatus.in_archive),
        (CardStatus.in_archive, CardStatus.placed),
        (CardStatus.placed, CardStatus.pending_return),
        (CardStatus.pending_return, CardStatus.in_transit),
        (CardStatus.in_transit, CardStatus.in_branch),
    ])
    def test_valid_transitions(self, from_s, to_s):
        assert to_s in CARD_STATUS_TRANSITIONS[from_s]

    @pytest.mark.parametrize("from_s,to_s", [
        (CardStatus.in_branch, CardStatus.placed),
        (CardStatus.placed, CardStatus.in_branch),
        (CardStatus.in_box, CardStatus.placed),
    ])
    def test_invalid_transitions(self, from_s, to_s):
        assert to_s not in CARD_STATUS_TRANSITIONS[from_s]


class TestBoxStatus:
    def test_all_statuses_defined(self):
        expected = {"packing", "ready", "in_transit", "received", "placed", "lost"}
        assert {s.value for s in BoxStatus} == expected

    @pytest.mark.parametrize("from_s,to_s", [
        (BoxStatus.packing, BoxStatus.ready),
        (BoxStatus.ready, BoxStatus.in_transit),
        (BoxStatus.in_transit, BoxStatus.received),
        (BoxStatus.received, BoxStatus.placed),
    ])
    def test_valid_transitions(self, from_s, to_s):
        assert to_s in BOX_STATUS_TRANSITIONS[from_s]


class TestUserRole:
    def test_roles(self):
        expected = {"branch", "archive", "viewer", "admin"}
        assert {r.value for r in UserRole} == expected
```

**Step 1b: Write failing test for domain models**

```python
# tests/domain/test_models.py
from medarchive.domain.models import Card, Box
from medarchive.domain.statuses import CardStatus, BoxStatus


class TestCard:
    def test_create_card_defaults(self):
        card = Card(id=1, full_name="Иванов Иван Иванович")
        assert card.id == 1
        assert card.full_name == "Иванов Иван Иванович"
        assert card.status == CardStatus.in_branch
        assert card.created_at is not None


class TestBox:
    def test_create_box_defaults(self):
        box = Box(id=1, box_number="B-0001", origin_branch_id=1, created_by=1)
        assert box.box_number == "B-0001"
        assert box.status == BoxStatus.packing
```

**Step 2: Run tests — confirm FAIL**

```bash
python -m pytest tests/domain/ -v
# Expected: FAIL — ModuleNotFoundError
```

**Step 3: Write `src/medarchive/domain/statuses.py`**

```python
"""Статусы сущностей и допустимые переходы."""
from enum import Enum


class CardStatus(str, Enum):
    in_branch = "in_branch"
    in_box = "in_box"
    in_transit = "in_transit"
    in_archive = "in_archive"
    placed = "placed"
    pending_return = "pending_return"


CARD_STATUS_TRANSITIONS: dict[CardStatus, set[CardStatus]] = {
    CardStatus.in_branch: {CardStatus.in_box},
    CardStatus.in_box: {CardStatus.in_transit},
    CardStatus.in_transit: {CardStatus.in_archive, CardStatus.in_branch},
    CardStatus.in_archive: {CardStatus.placed, CardStatus.pending_return},
    CardStatus.placed: {CardStatus.pending_return},
    CardStatus.pending_return: {CardStatus.in_transit},
}


class BoxStatus(str, Enum):
    packing = "packing"
    ready = "ready"
    in_transit = "in_transit"
    received = "received"
    placed = "placed"
    lost = "lost"


BOX_STATUS_TRANSITIONS: dict[BoxStatus, set[BoxStatus]] = {
    BoxStatus.packing: {BoxStatus.ready, BoxStatus.lost},
    BoxStatus.ready: {BoxStatus.in_transit, BoxStatus.packing},
    BoxStatus.in_transit: {BoxStatus.received, BoxStatus.lost},
    BoxStatus.received: {BoxStatus.placed},
    BoxStatus.placed: set(),
    BoxStatus.lost: set(),
}


class ShipmentStatus(str, Enum):
    issued = "issued"
    received = "received"
    lost = "lost"
    damaged = "damaged"


class RequestStatus(str, Enum):
    new = "new"
    in_progress = "in_progress"
    completed = "completed"
    cancelled = "cancelled"
    not_found = "not_found"


class UserRole(str, Enum):
    branch = "branch"
    archive = "archive"
    viewer = "viewer"
    admin = "admin"
```

**Step 3b: Write `src/medarchive/domain/models.py`**

```python
"""Доменные сущности (чистые dataclass'ы, без ORM)."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from medarchive.domain.statuses import (
    CardStatus, BoxStatus, ShipmentStatus, RequestStatus, UserRole
)


@dataclass
class Card:
    id: int
    full_name: str
    status: CardStatus = CardStatus.in_branch
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class Box:
    id: int
    box_number: str
    origin_branch_id: int
    created_by: int
    status: BoxStatus = BoxStatus.packing
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class Branch:
    id: int
    name: str
    code: str


@dataclass
class Courier:
    id: int
    name: str
    phone: str | None = None


@dataclass
class User:
    id: int
    username: str
    password_hash: str
    role: UserRole
    branch_id: int | None = None
```

**Step 3c: Create `src/medarchive/domain/__init__.py`** (empty)

**Step 4: Run tests — confirm PASS**

```bash
python -m pytest tests/domain/ -v
# Expected: PASS (all tests)
```

**Step 5: Commit**

```bash
git add src/medarchive/domain/ tests/domain/
git commit -m "feat: доменные модели (статусы, переходы, сущности Card/Box/User)"
```

---

## Task 3: База данных и миграции

**Files:**
- Create: `src/medarchive/adapters/__init__.py`
- Create: `src/medarchive/adapters/sqlite/__init__.py`
- Create: `src/medarchive/adapters/sqlite/database.py`
- Create: `src/medarchive/adapters/sqlite/migrations.py`
- Create: `tests/adapters/__init__.py`
- Create: `tests/adapters/sqlite/__init__.py`
- Create: `tests/adapters/sqlite/test_database.py`
- Create: `tests/conftest.py`

**Step 1: Write failing test**

```python
# tests/conftest.py
import pytest
import tempfile
import os
from medarchive.config import Config


@pytest.fixture
def test_config():
    """Временная БД для тестов."""
    fd, path = tempfile.mkstemp(suffix=".db", prefix="medarchive_test_")
    os.close(fd)
    os.environ["MEDARCHIVE_DB_PATH"] = path
    os.environ["MEDARCHIVE_SECRET_KEY"] = "test-secret-key-for-testing"
    cfg = Config()
    yield cfg
    os.environ.pop("MEDARCHIVE_DB_PATH", None)
    os.environ.pop("MEDARCHIVE_SECRET_KEY", None)
    if os.path.exists(path):
        os.unlink(path)


@pytest.fixture
async def db(test_config):
    from medarchive.adapters.sqlite.database import Database
    db = Database(test_config)
    await db.init()
    yield db
    await db.close()
```

```python
# tests/adapters/sqlite/test_database.py
import pytest


@pytest.mark.asyncio
async def test_database_init_creates_tables(db):
    """После init() все 12 таблиц существуют."""
    tables = await db.fetch_all(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )
    table_names = {row["name"] for row in tables}

    expected = {
        "branches", "users", "couriers",
        "archive_sections", "archive_shelves",
        "cards", "boxes", "box_cards",
        "shipments", "box_placements",
        "requests", "card_movements",
    }
    assert expected.issubset(table_names), f"Missing tables: {expected - table_names}"


@pytest.mark.asyncio
async def test_create_and_find_branch(db):
    """Можно создать и найти филиал."""
    await db.execute(
        "INSERT INTO branches (name, code) VALUES (?, ?)",
        ("Филиал 1", "F01"),
    )
    row = await db.fetch_one("SELECT * FROM branches WHERE code = ?", ("F01",))
    assert row is not None
    assert row["name"] == "Филиал 1"
```

**Step 2: Run test — confirm FAIL**

```bash
python -m pytest tests/adapters/sqlite/test_database.py -v
# Expected: FAIL — ModuleNotFoundError
```

**Step 3: Write `src/medarchive/adapters/sqlite/database.py`**

```python
"""Управление подключением к SQLite."""
import aiosqlite
from medarchive.config import Config
from medarchive.adapters.sqlite.migrations import run_migrations


class Database:
    def __init__(self, config: Config):
        self.config = config
        self._conn: aiosqlite.Connection | None = None

    async def init(self) -> None:
        self._conn = await aiosqlite.connect(self.config.db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await run_migrations(self._conn)

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database not initialized. Call await db.init() first.")
        return self._conn

    async def execute(self, sql: str, params: tuple = ()) -> aiosqlite.Cursor:
        return await self.conn.execute(sql, params)

    async def fetch_one(self, sql: str, params: tuple = ()):
        async with self.conn.execute(sql, params) as cursor:
            return await cursor.fetchone()

    async def fetch_all(self, sql: str, params: tuple = ()):
        async with self.conn.execute(sql, params) as cursor:
            return await cursor.fetchall()
```

**Step 3b: Write `src/medarchive/adapters/sqlite/migrations.py`**

```python
"""Миграции — создание всех таблиц."""
import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS branches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    code TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('branch','archive','viewer','admin')),
    branch_id INTEGER REFERENCES branches(id)
);

CREATE TABLE IF NOT EXISTS couriers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    phone TEXT
);

CREATE TABLE IF NOT EXISTS archive_sections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS archive_shelves (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    section_id INTEGER NOT NULL REFERENCES archive_sections(id),
    name TEXT NOT NULL,
    qr_code TEXT
);

CREATE TABLE IF NOT EXISTS cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'in_branch',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS boxes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    box_number TEXT NOT NULL UNIQUE,
    origin_branch_id INTEGER NOT NULL REFERENCES branches(id),
    status TEXT NOT NULL DEFAULT 'packing',
    created_by INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS box_cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    box_id INTEGER NOT NULL REFERENCES boxes(id),
    card_id INTEGER NOT NULL REFERENCES cards(id),
    added_at TEXT NOT NULL DEFAULT (datetime('now')),
    removed_at TEXT
);

CREATE TABLE IF NOT EXISTS shipments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK(kind IN ('box','card')),
    box_id INTEGER REFERENCES boxes(id),
    card_id INTEGER REFERENCES cards(id),
    from_branch_id INTEGER REFERENCES branches(id),
    to_branch_id INTEGER REFERENCES branches(id),
    to_department INTEGER NOT NULL DEFAULT 0,
    courier_id INTEGER NOT NULL REFERENCES couriers(id),
    issued_by INTEGER NOT NULL REFERENCES users(id),
    issued_at TEXT NOT NULL DEFAULT (datetime('now')),
    received_by INTEGER REFERENCES users(id),
    received_at TEXT,
    status TEXT NOT NULL DEFAULT 'issued'
);

CREATE TABLE IF NOT EXISTS box_placements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    box_id INTEGER NOT NULL REFERENCES boxes(id),
    shelf_id INTEGER NOT NULL REFERENCES archive_shelves(id),
    placed_at TEXT NOT NULL DEFAULT (datetime('now')),
    removed_at TEXT
);

CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id INTEGER NOT NULL REFERENCES cards(id),
    requested_by INTEGER NOT NULL REFERENCES users(id),
    target_branch_id INTEGER REFERENCES branches(id),
    target_department INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'new',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT,
    fulfilled_by INTEGER REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS card_movements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id INTEGER NOT NULL REFERENCES cards(id),
    action TEXT NOT NULL,
    from_location TEXT,
    to_location TEXT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    timestamp TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


async def run_migrations(conn: aiosqlite.Connection) -> None:
    await conn.executescript(SCHEMA)
    await conn.commit()
```

**Step 3c: Create empty `__init__.py` files**

```python
# src/medarchive/adapters/__init__.py (empty)
# src/medarchive/adapters/sqlite/__init__.py (empty)
# tests/adapters/__init__.py (empty)
# tests/adapters/sqlite/__init__.py (empty)
```

**Step 4: Run tests — confirm PASS**

```bash
python -m pytest tests/adapters/sqlite/test_database.py -v
# Expected: PASS (2 tests)
```

**Step 5: Commit**

```bash
git add src/medarchive/adapters/ tests/adapters/ tests/conftest.py
git commit -m "feat: SQLite-адаптер (Database + миграции 12 таблиц)"
```

---

## Task 4: Репозитории (интерфейсы)

**Files:**
- Create: `src/medarchive/repositories/__init__.py`
- Create: `src/medarchive/repositories/base.py`

**Step 1: No separate test — structural file, correctness verified by type checker. Skip TDD for protocol definitions.**

**Step 2: Write `src/medarchive/repositories/base.py`**

```python
"""Интерфейсы репозиториев (Protocols)."""
from typing import Protocol
from medarchive.domain.models import Card, Box, User
from medarchive.domain.statuses import CardStatus, BoxStatus


class CardRepository(Protocol):
    async def get_by_id(self, card_id: int) -> Card | None: ...
    async def find_by_name(self, full_name: str) -> list[Card]: ...
    async def create(self, full_name: str) -> Card: ...
    async def update_status(self, card_id: int, status: CardStatus) -> None: ...


class BoxRepository(Protocol):
    async def get_by_id(self, box_id: int) -> Box | None: ...
    async def get_by_number(self, box_number: str) -> Box | None: ...
    async def create(self, box_number: str, origin_branch_id: int, created_by: int) -> Box: ...
    async def update_status(self, box_id: int, status: BoxStatus) -> None: ...
    async def get_cards(self, box_id: int) -> list[Card]: ...


class UserRepository(Protocol):
    async def get_by_id(self, user_id: int) -> User | None: ...
    async def get_by_username(self, username: str) -> User | None: ...
    async def create(self, username: str, password_hash: str, role: str, branch_id: int | None) -> User: ...
```

**Step 3: Create `src/medarchive/repositories/__init__.py`** (empty)

**Step 4: Commit**

```bash
git add src/medarchive/repositories/
git commit -m "feat: интерфейсы репозиториев (Protocols для Card/Box/User)"
```

---

## Task 5: Сервис аутентификации

**Files:**
- Create: `src/medarchive/services/__init__.py`
- Create: `src/medarchive/services/auth_service.py`
- Create: `tests/services/__init__.py`
- Create: `tests/services/test_auth_service.py`

**Step 1: Write failing test**

```python
# tests/services/test_auth_service.py
import pytest
from medarchive.services.auth_service import AuthService
from medarchive.domain.models import User
from medarchive.domain.statuses import UserRole


@pytest.fixture
def auth_service():
    return AuthService(secret_key="test-secret")


class TestAuthService:
    def test_hash_and_verify_password(self, auth_service):
        hashed = auth_service.hash_password("mypassword123")
        assert hashed != "mypassword123"
        assert auth_service.verify_password("mypassword123", hashed)
        assert not auth_service.verify_password("wrongpassword", hashed)

    def test_create_session_token(self, auth_service):
        user = User(id=1, username="operator", password_hash="...", role=UserRole.branch)
        token = auth_service.create_session_token(user)
        assert isinstance(token, str)
        assert len(token) > 0

    def test_verify_session_token(self, auth_service):
        user = User(id=1, username="operator", password_hash="...", role=UserRole.branch)
        token = auth_service.create_session_token(user)
        payload = auth_service.verify_session_token(token)
        assert payload is not None
        assert payload["user_id"] == 1
        assert payload["username"] == "operator"
        assert payload["role"] == "branch"

    def test_verify_invalid_token(self, auth_service):
        assert auth_service.verify_session_token("garbage-token") is None

    def test_verify_tampered_token(self, auth_service):
        user = User(id=1, username="operator", password_hash="...", role=UserRole.branch)
        token = auth_service.create_session_token(user)
        # Tamper: add a character
        assert auth_service.verify_session_token(token + "x") is None
```

**Step 2: Run test — confirm FAIL**

```bash
python -m pytest tests/services/test_auth_service.py -v
# Expected: FAIL — ModuleNotFoundError
```

**Step 3: Write `src/medarchive/services/auth_service.py`**

```python
"""Сервис аутентификации: хеширование паролей и сессионные токены."""
from passlib.context import CryptContext
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from medarchive.domain.models import User


pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class AuthService:
    def __init__(self, secret_key: str):
        self._serializer = URLSafeTimedSerializer(secret_key)

    def hash_password(self, password: str) -> str:
        return pwd_context.hash(password)

    def verify_password(self, password: str, password_hash: str) -> bool:
        return pwd_context.verify(password, password_hash)

    def create_session_token(self, user: User) -> str:
        payload = {
            "user_id": user.id,
            "username": user.username,
            "role": user.role.value,
        }
        return self._serializer.dumps(payload)

    def verify_session_token(self, token: str) -> dict | None:
        try:
            return self._serializer.loads(token, max_age=86400 * 7)  # 7 days
        except (BadSignature, SignatureExpired):
            return None
```

**Step 4: Run tests — confirm PASS**

```bash
python -m pytest tests/services/test_auth_service.py -v
# Expected: PASS (5 tests)
```

**Step 5: Commit**

```bash
git add src/medarchive/services/ tests/services/
git commit -m "feat: сервис аутентификации (bcrypt + signed sessions)"
```

---

## Task 6: FastAPI приложение + роутер аутентификации

**Files:**
- Create: `src/medarchive/main.py`
- Create: `src/medarchive/dependencies.py`
- Create: `src/medarchive/routers/__init__.py`
- Create: `src/medarchive/routers/auth.py`
- Create: `tests/routers/__init__.py`
- Create: `tests/routers/test_auth.py`

**Step 1: Write failing test**

```python
# tests/routers/test_auth.py
import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client(test_config, db):
    """FastAPI TestClient с временной БД."""
    from medarchive.main import create_app
    app = create_app(test_config)

    # Seed a test user
    from medarchive.services.auth_service import AuthService
    auth = AuthService(secret_key=test_config.effective_secret_key)
    password_hash = auth.hash_password("test123")
    await db.execute(
        "INSERT INTO branches (id, name, code) VALUES (1, 'Test Branch', 'TB')"
    )
    await db.execute(
        "INSERT INTO users (id, username, password_hash, role, branch_id) "
        "VALUES (1, 'test_branch', ?, 'branch', 1)",
        (password_hash,),
    )
    await db.execute(
        "INSERT INTO users (id, username, password_hash, role) "
        "VALUES (2, 'test_admin', ?, 'admin')",
        (password_hash,),
    )
    await db.conn.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestAuthRouter:
    async def test_login_page_renders(self, client):
        r = await client.get("/login")
        assert r.status_code == 200
        assert "вход" in r.text.lower()

    async def test_login_success_redirects(self, client):
        r = await client.post("/login", data={"username": "test_branch", "password": "test123"}, follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/dashboard"

    async def test_login_wrong_password(self, client):
        r = await client.post("/login", data={"username": "test_branch", "password": "wrong"})
        assert r.status_code == 200  # re-renders login page
        assert "неверный" in r.text.lower()

    async def test_logout_clears_session(self, client):
        # Login first
        await client.post("/login", data={"username": "test_branch", "password": "test123"})
        r = await client.get("/logout", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/login"

    async def test_protected_page_redirects_to_login(self, client):
        r = await client.get("/dashboard", follow_redirects=False)
        assert r.status_code == 303
        assert "/login" in r.headers["location"]
```

**Step 2: Run test — confirm FAIL**

```bash
python -m pytest tests/routers/test_auth.py -v
# Expected: FAIL — ModuleNotFoundError
```

**Step 3a: Write `src/medarchive/dependencies.py`**

```python
"""Зависимости FastAPI: БД, текущий пользователь, проверка ролей."""
from fastapi import Request, HTTPException, Depends
from medarchive.config import Config
from medarchive.adapters.sqlite.database import Database
from medarchive.services.auth_service import AuthService
from medarchive.domain.models import User
from medarchive.domain.statuses import UserRole

_db: Database | None = None
_auth: AuthService | None = None


def init_dependencies(config: Config) -> None:
    global _db, _auth
    _db = Database(config)
    _auth = AuthService(secret_key=config.effective_secret_key)


async def get_db() -> Database:
    if _db is None:
        raise RuntimeError("Dependencies not initialized. Call init_dependencies first.")
    return _db


def get_auth() -> AuthService:
    if _auth is None:
        raise RuntimeError("Dependencies not initialized. Call init_dependencies first.")
    return _auth


async def get_current_user(request: Request) -> User:
    auth = get_auth()
    token = request.cookies.get("session")
    if token is None:
        raise HTTPException(status_code=401, detail="Not authenticated")

    payload = auth.verify_session_token(token)
    if payload is None:
        raise HTTPException(status_code=401, detail="Invalid session")

    db = await get_db()
    row = await db.fetch_one("SELECT * FROM users WHERE id = ?", (payload["user_id"],))
    if row is None:
        raise HTTPException(status_code=401, detail="User not found")

    return User(
        id=row["id"],
        username=row["username"],
        password_hash=row["password_hash"],
        role=UserRole(row["role"]),
        branch_id=row["branch_id"],
    )


def require_role(*roles: UserRole):
    """Декоратор для проверки роли пользователя."""
    async def dependency(user: User = Depends(get_current_user)):
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="Forbidden")
        return user
    return dependency
```

**Step 3b: Write `src/medarchive/routers/auth.py`**

```python
"""Роутер аутентификации: логин/логаут."""
from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import RedirectResponse
from medarchive.dependencies import get_db, get_auth, get_current_user
from medarchive.adapters.sqlite.database import Database
from medarchive.services.auth_service import AuthService

router = APIRouter()


@router.get("/login")
async def login_page(request: Request):
    return request.app.state.templates.TemplateResponse("login.html", {"request": request})


@router.post("/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Database = Depends(get_db),
    auth: AuthService = Depends(get_auth),
):
    row = await db.fetch_one("SELECT * FROM users WHERE username = ?", (username,))
    if row is None or not auth.verify_password(password, row["password_hash"]):
        return request.app.state.templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Неверный логин или пароль"},
            status_code=200,
        )

    from medarchive.domain.models import User as DomainUser
    from medarchive.domain.statuses import UserRole

    user = DomainUser(
        id=row["id"],
        username=row["username"],
        password_hash=row["password_hash"],
        role=UserRole(row["role"]),
        branch_id=row["branch_id"],
    )
    token = auth.create_session_token(user)

    response = RedirectResponse(url="/dashboard", status_code=303)
    response.set_cookie("session", token, httponly=True, max_age=86400 * 7)
    return response


@router.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("session")
    return response
```

**Step 3c: Write `src/medarchive/main.py`**

```python
"""Точка входа FastAPI-приложения."""
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape

from medarchive.config import Config
from medarchive.dependencies import init_dependencies


def create_app(config: Config | None = None) -> FastAPI:
    if config is None:
        config = Config()

    app = FastAPI(title="МедАрхив", version="0.1.0")

    # Jinja2 templates
    app.state.templates = Environment(
        loader=FileSystemLoader("src/medarchive/templates"),
        autoescape=select_autoescape(["html"]),
    )

    # Init dependencies
    init_dependencies(config)

    # Startup: init DB
    @app.on_event("startup")
    async def startup():
        from medarchive.dependencies import get_db
        db = await get_db()
        await db.init()

    # Routers
    from medarchive.routers.auth import router as auth_router
    from medarchive.routers.dashboard import router as dashboard_router

    app.include_router(auth_router)
    app.include_router(dashboard_router)

    return app
```

**Step 4: Tests will FAIL — dashboard router not created yet. Skipped until Task 7.**

**Commit partial:**

```bash
git add src/medarchive/main.py src/medarchive/dependencies.py src/medarchive/routers/
git commit -m "feat: FastAPI приложение + зависимости + роутер аутентификации"
```

---

## Task 7: Шаблоны и дашборд

**Files:**
- Create: `src/medarchive/templates/base.html`
- Create: `src/medarchive/templates/login.html`
- Create: `src/medarchive/templates/dashboard.html`
- Create: `src/medarchive/routers/dashboard.py`
- Create: `tests/routers/test_dashboard.py`

**Step 1: Write failing test**

```python
# tests/routers/test_dashboard.py
import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def client(test_config, db):
    from medarchive.main import create_app
    app = create_app(test_config)

    from medarchive.services.auth_service import AuthService
    auth = AuthService(secret_key=test_config.effective_secret_key)

    # Seed users for all roles
    users = [
        ("branch_user", "branch", 1, 1),
        ("archive_user", "archive", None, 2),
        ("viewer_user", "viewer", None, 3),
        ("admin_user", "admin", None, 4),
    ]
    for username, role, branch_id, user_id in users:
        password_hash = auth.hash_password("test123")
        await db.execute(
            "INSERT OR REPLACE INTO users (id, username, password_hash, role, branch_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, username, password_hash, role, branch_id),
        )
    await db.execute(
        "INSERT OR REPLACE INTO branches (id, name, code) VALUES (1, 'Test Branch', 'TB')"
    )
    await db.conn.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestDashboard:
    async def test_redirects_to_login_when_unauthenticated(self, client):
        r = await client.get("/dashboard", follow_redirects=False)
        assert r.status_code == 303

    @pytest.mark.parametrize("username", ["branch_user", "archive_user", "viewer_user", "admin_user"])
    async def test_dashboard_accessible_for_all_roles(self, client, username):
        await client.post("/login", data={"username": username, "password": "test123"})
        r = await client.get("/dashboard")
        assert r.status_code == 200
        assert "медархив" in r.text.lower()

    async def test_sidebar_shows_role_specific_items(self, client):
        # Branch user sees packing menu
        await client.post("/login", data={"username": "branch_user", "password": "test123"})
        r = await client.get("/dashboard")
        assert r.status_code == 200
        assert "упаковка" in r.text.lower()
        # Archive-specific item — not visible to branch
        assert "приёмка" not in r.text.lower()
```

**Step 2: Run test — confirm FAIL**

```bash
python -m pytest tests/routers/test_dashboard.py -v
# Expected: FAIL — ModuleNotFoundError or 404
```

**Step 3a: Write `src/medarchive/templates/base.html`**

```html
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>МедАрхив</title>
    <script src="https://unpkg.com/htmx.org@2.0.4"></script>
    <style>
        *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
        body { font: 14px/1.5 -apple-system, BlinkMacSystemFont, system-ui, sans-serif; background: #f5f5f5; display: flex; min-height: 100vh; }
        .sidebar { width: 240px; background: #1a1a2e; color: #eee; padding: 20px 0; flex-shrink: 0; }
        .sidebar h2 { padding: 0 20px 16px; font-size: 18px; color: #e94560; border-bottom: 1px solid #333; margin-bottom: 8px; }
        .sidebar nav a { display: block; padding: 10px 20px; color: #ccc; text-decoration: none; font-size: 14px; }
        .sidebar nav a:hover { background: #16213e; color: #fff; }
        .sidebar .role-badge { padding: 4px 20px 12px; font-size: 12px; color: #888; }
        .sidebar .logout { margin-top: 20px; padding: 0 20px; }
        .sidebar .logout a { color: #e94560; text-decoration: none; font-size: 13px; }
        .main { flex: 1; padding: 24px; }
        .main h1 { font-size: 22px; margin-bottom: 16px; color: #1a1a2e; }
        .card { background: #fff; border-radius: 8px; padding: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); margin-bottom: 16px; }
        .flash { background: #fff3cd; border: 1px solid #ffc107; border-radius: 6px; padding: 10px 16px; margin-bottom: 12px; }
        input, select, button { font: inherit; }
        .btn { display: inline-block; padding: 8px 16px; border: none; border-radius: 6px; cursor: pointer; font-weight: 600; text-decoration: none; }
        .btn-primary { background: #1a1a2e; color: #fff; }
        .btn-primary:hover { background: #16213e; }
        .btn-danger { background: #e94560; color: #fff; }
        .form-group { margin-bottom: 12px; }
        .form-group label { display: block; margin-bottom: 4px; font-weight: 600; }
        .form-group input { width: 100%; padding: 8px 12px; border: 1px solid #ddd; border-radius: 6px; }
    </style>
</head>
<body>
    <aside class="sidebar">
        <h2>📦 МедАрхив</h2>
        <div class="role-badge">{{ role_label }}</div>
        <nav>
            {% for item in menu_items %}
            <a href="{{ item.url }}">{{ item.label }}</a>
            {% endfor %}
        </nav>
        <div class="logout"><a href="/logout">Выход</a></div>
    </aside>
    <div class="main">
        {% if error %}<div class="flash">{{ error }}</div>{% endif %}
        {% block content %}{% endblock %}
    </div>
    {% block scripts %}{% endblock %}
</body>
</html>
```

**Step 3b: Write `src/medarchive/templates/login.html`**

```html
{% extends "base.html" %}
{% block content %}
<div style="max-width: 400px; margin: 80px auto;">
    <div class="card">
        <h1 style="margin-bottom: 20px;">Вход в МедАрхив</h1>
        <form method="post" action="/login">
            <div class="form-group">
                <label for="username">Логин</label>
                <input type="text" id="username" name="username" required autofocus>
            </div>
            <div class="form-group">
                <label for="password">Пароль</label>
                <input type="password" id="password" name="password" required>
            </div>
            <button type="submit" class="btn btn-primary" style="width:100%">Войти</button>
        </form>
    </div>
</div>
{% endblock %}
```

**Step 3c: Write `src/medarchive/templates/dashboard.html`**

```html
{% extends "base.html" %}
{% block content %}
<h1>Добро пожаловать, {{ username }}</h1>
<div class="card">
    <p>Вы вошли как <strong>{{ role_label }}</strong>.</p>
    <p style="color: #888; margin-top: 8px;">Система управления архивом медицинских карт v0.1.0</p>
</div>
{% endblock %}
```

**Step 3d: Write `src/medarchive/routers/dashboard.py`**

```python
"""Роутер дашборда."""
from fastapi import APIRouter, Request, Depends
from fastapi.responses import RedirectResponse
from medarchive.dependencies import get_current_user
from medarchive.domain.models import User
from medarchive.domain.statuses import UserRole

router = APIRouter()

ROLE_LABELS = {
    UserRole.branch: "Филиал",
    UserRole.archive: "Архив",
    UserRole.viewer: "Просмотр",
    UserRole.admin: "Администратор",
}

MENU_BY_ROLE = {
    UserRole.branch: [
        {"url": "/packing", "label": "📦 Упаковка коробки"},
        {"url": "/transfer", "label": "🚚 Передача курьеру"},
        {"url": "/my-shipments", "label": "📋 Мои отправки"},
        {"url": "/requests/new", "label": "📝 Заявки на возврат"},
        {"url": "/receive-return", "label": "📥 Приём возврата"},
    ],
    UserRole.archive: [
        {"url": "/receive", "label": "📥 Приёмка"},
        {"url": "/placement", "label": "📍 Размещение"},
        {"url": "/requests", "label": "📋 Очередь заявок"},
        {"url": "/search", "label": "🔍 Поиск"},
        {"url": "/relocate", "label": "🔄 Перестановка"},
    ],
    UserRole.viewer: [
        {"url": "/dashboard", "label": "📊 Дашборд"},
        {"url": "/search", "label": "🔍 Поиск"},
        {"url": "/history", "label": "📜 Журнал движений"},
    ],
    UserRole.admin: [
        {"url": "/dashboard", "label": "📊 Дашборд"},
        {"url": "/packing", "label": "📦 Упаковка"},
        {"url": "/receive", "label": "📥 Приёмка"},
        {"url": "/requests", "label": "📋 Заявки"},
        {"url": "/search", "label": "🔍 Поиск"},
        {"url": "/admin", "label": "⚙️ Справочники"},
    ],
}


def _menu_context(user: User) -> dict:
    return {
        "role_label": ROLE_LABELS.get(user.role, "Неизвестно"),
        "menu_items": MENU_BY_ROLE.get(user.role, []),
    }


@router.get("/dashboard")
async def dashboard(request: Request, user: User = Depends(get_current_user)):
    ctx = _menu_context(user)
    ctx["username"] = user.username
    ctx["request"] = request
    return request.app.state.templates.get_template("dashboard.html").render(ctx)
```

**Step 3e: Update `src/medarchive/templates/login.html`** — remove `extends "base.html"` (login page doesn't use sidebar):

```html
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>МедАрхив — Вход</title>
    <style>
        *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
        body { font: 14px/1.5 -apple-system, BlinkMacSystemFont, system-ui, sans-serif; background: #f5f5f5; }
        .card { background: #fff; border-radius: 8px; padding: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
        .btn { display: inline-block; padding: 8px 16px; border: none; border-radius: 6px; cursor: pointer; font-weight: 600; }
        .btn-primary { background: #1a1a2e; color: #fff; width: 100%; }
        .form-group { margin-bottom: 12px; }
        .form-group label { display: block; margin-bottom: 4px; font-weight: 600; }
        .form-group input { width: 100%; padding: 8px 12px; border: 1px solid #ddd; border-radius: 6px; font: inherit; }
        .flash { background: #f8d7da; border: 1px solid #f5c6cb; border-radius: 6px; padding: 10px 16px; margin-bottom: 12px; color: #721c24; }
    </style>
</head>
<body>
    <div style="max-width: 400px; margin: 80px auto;">
        <div class="card">
            <h1 style="margin-bottom: 20px; color: #1a1a2e;">📦 Вход в МедАрхив</h1>
            {% if error %}<div class="flash">{{ error }}</div>{% endif %}
            <form method="post" action="/login">
                <div class="form-group">
                    <label for="username">Логин</label>
                    <input type="text" id="username" name="username" required autofocus>
                </div>
                <div class="form-group">
                    <label for="password">Пароль</label>
                    <input type="password" id="password" name="password" required>
                </div>
                <button type="submit" class="btn btn-primary">Войти</button>
            </form>
        </div>
    </div>
</body>
</html>
```

**Step 3f: Update `src/medarchive/routers/auth.py`** — login_page renders standalone, not via template env:

```python
# In login_page handler, use a custom response
from fastapi.responses import HTMLResponse

@router.get("/login", response_class=HTMLResponse)
async def login_page():
    # Standalone login page — doesn't use base.html layout
    return """..."""  # We'll use Jinja2 TemplateResponse instead
```

Actually, better to use `Jinja2Templates` from FastAPI. Let me restructure. Replace the manual `Environment` in main.py with `Jinja2Templates`:

**In `src/medarchive/main.py`**, replace the templates setup:

```python
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="src/medarchive/templates")

def create_app(config: Config | None = None) -> FastAPI:
    # ... same ...
    app.state.templates = templates
```

**In `src/medarchive/routers/auth.py`**, login_page becomes:

```python
from fastapi.templating import Jinja2Templates

@router.get("/login")
async def login_page(request: Request):
    return request.app.state.templates.TemplateResponse(
        "login.html", {"request": request}
    )
```

Dashboard also uses `request.app.state.templates.TemplateResponse`.

**Step 4: Run all router tests — confirm PASS**

```bash
python -m pytest tests/routers/ -v
# Expected: PASS (9 tests)
```

**Step 5: Commit**

```bash
git add src/medarchive/templates/ src/medarchive/routers/dashboard.py src/medarchive/main.py
git commit -m "feat: шаблоны (base/layout + login + dashboard) и роутер дашборда"
```

---

## Task 8: Интеграционный smoke-тест (E2E skeleton)

**Files:**
- Create: `tests/test_smoke.py`

**Step 1: Write smoke test**

```python
# tests/test_smoke.py
import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def app_client(test_config, db):
    from medarchive.main import create_app
    from medarchive.services.auth_service import AuthService
    auth = AuthService(secret_key=test_config.effective_secret_key)
    await db.execute("INSERT OR REPLACE INTO branches (id, name, code) VALUES (1, 'B1', 'B1')")
    await db.execute(
        "INSERT OR REPLACE INTO users (id, username, password_hash, role, branch_id) "
        "VALUES (1, 'admin', ?, 'admin', NULL)",
        (auth.hash_password("admin123"),),
    )
    await db.conn.commit()

    app = create_app(test_config)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestSmoke:
    async def test_login_to_dashboard_flow(self, app_client):
        """Полный цикл: GET /login → POST login → GET /dashboard → logout."""
        # 1. Login page
        r = await app_client.get("/login")
        assert r.status_code == 200

        # 2. Submit login
        r = await app_client.post(
            "/login",
            data={"username": "admin", "password": "admin123"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/dashboard"
        assert "session" in r.cookies

        # 3. Follow redirect to dashboard
        r = await app_client.get("/dashboard")
        assert r.status_code == 200
        assert "admin" in r.text.lower()
        assert "администратор" in r.text.lower()

        # 4. Logout
        r = await app_client.get("/logout", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/login"

    async def test_protected_routes_block_unauthenticated(self, app_client):
        for path in ["/dashboard"]:
            r = await app_client.get(path, follow_redirects=False)
            assert r.status_code == 303, f"{path} should redirect to login"
```

**Step 2: Run smoke test — confirm PASS**

```bash
python -m pytest tests/test_smoke.py -v
# Expected: PASS (2 tests)
```

**Step 3: Commit**

```bash
git add tests/test_smoke.py
git commit -m "test: интеграционный smoke-тест (логин → дашборд → логаут)"
```

---

## Проверка всего этапа 0

```bash
python -m pytest tests/ -v
# Expected: ALL tests PASS (domain + database + services + routers + smoke)
```

Итого: **8 задач, ~18 тестов**, каркас с аутентификацией, БД, шаблонами и дашбордом.
