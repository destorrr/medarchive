"""SQLite-реализации репозиториев."""
from datetime import datetime
from medarchive.adapters.sqlite.database import Database
from medarchive.domain.models import Card, Box, Branch, User, Courier
from medarchive.domain.statuses import CardStatus, BoxStatus, UserRole


class CardRepositoryImpl:
    def __init__(self, db: Database):
        self.db = db

    async def get_by_id(self, card_id: int) -> Card | None:
        row = await self.db.fetch_one("SELECT * FROM cards WHERE id = ?", (card_id,))
        if row is None:
            return None
        return Card(
            id=row["id"],
            full_name=row["full_name"],
            status=CardStatus(row["status"]),
        )

    async def find_by_name(self, full_name: str) -> list[Card]:
        rows = await self.db.fetch_all(
            "SELECT * FROM cards WHERE full_name = ?", (full_name,)
        )
        return [
            Card(id=r["id"], full_name=r["full_name"], status=CardStatus(r["status"]))
            for r in rows
        ]

    async def create(self, full_name: str) -> Card:
        cursor = await self.db.execute(
            "INSERT INTO cards (full_name, status, created_at) VALUES (?, ?, ?)",
            (full_name, CardStatus.in_branch.value, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        await self.db.conn.commit()
        return Card(id=cursor.lastrowid, full_name=full_name)

    async def update_status(self, card_id: int, status: CardStatus) -> None:
        await self.db.execute(
            "UPDATE cards SET status = ? WHERE id = ?",
            (status.value, card_id),
        )
        await self.db.conn.commit()


class BoxRepositoryImpl:
    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def _row_to_box(row) -> Box:
        return Box(
            id=row["id"],
            box_number=row["box_number"],
            origin_branch_id=row["origin_branch_id"],
            created_by=row["created_by"],
            status=BoxStatus(row["status"]),
        )

    async def get_by_id(self, box_id: int) -> Box | None:
        row = await self.db.fetch_one("SELECT * FROM boxes WHERE id = ?", (box_id,))
        if row is None:
            return None
        return self._row_to_box(row)

    async def get_by_number(self, box_number: str) -> Box | None:
        row = await self.db.fetch_one(
            "SELECT * FROM boxes WHERE box_number = ?", (box_number,)
        )
        if row is None:
            return None
        return self._row_to_box(row)

    async def get_active_for_user(self, user_id: int) -> Box | None:
        """Активная коробка пользователя.

        Приоритет: явно выбранная (users.active_box_id), если она ещё открыта.
        Fallback: последняя незакрытая (packing) коробка пользователя.
        """
        row = await self.db.fetch_one(
            """SELECT b.* FROM boxes b
               JOIN users u ON u.active_box_id = b.id
               WHERE u.id = ? AND b.status = ? AND b.created_by = u.id""",
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

    async def create(self, box_number: str, origin_branch_id: int, created_by: int) -> Box:
        cursor = await self.db.execute(
            "INSERT INTO boxes (box_number, origin_branch_id, created_by, status, created_at) VALUES (?, ?, ?, ?, ?)",
            (box_number, origin_branch_id, created_by, BoxStatus.packing.value,
             datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        await self.db.conn.commit()
        return Box(
            id=cursor.lastrowid,
            box_number=box_number,
            origin_branch_id=origin_branch_id,
            created_by=created_by,
        )

    async def update_status(self, box_id: int, status: BoxStatus) -> None:
        await self.db.execute(
            "UPDATE boxes SET status = ? WHERE id = ?",
            (status.value, box_id),
        )
        await self.db.conn.commit()

    async def add_card(self, box_id: int, card_id: int) -> bool:
        """Добавить карту в коробку. Возвращает False, если карта уже там."""
        # Check for duplicate
        existing = await self.db.fetch_one(
            "SELECT id FROM box_cards WHERE box_id = ? AND card_id = ? AND removed_at IS NULL",
            (box_id, card_id),
        )
        if existing is not None:
            return False  # Already in box, ignore
        await self.db.execute(
            "INSERT INTO box_cards (box_id, card_id, added_at) VALUES (?, ?, ?)",
            (box_id, card_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        await self.db.conn.commit()
        return True

    async def remove_card(self, box_id: int, card_id: int) -> bool:
        """Мягкое удаление карты из коробки. Возвращает True, если карта была в коробке."""
        cursor = await self.db.execute(
            "UPDATE box_cards SET removed_at = datetime('now') "
            "WHERE box_id = ? AND card_id = ? AND removed_at IS NULL",
            (box_id, card_id),
        )
        await self.db.conn.commit()
        return cursor.rowcount > 0

    async def card_in_box(self, box_id: int, card_id: int) -> bool:
        """Есть ли карта в этой коробке (активная, не удалённая запись)."""
        row = await self.db.fetch_one(
            "SELECT id FROM box_cards WHERE box_id = ? AND card_id = ? AND removed_at IS NULL",
            (box_id, card_id),
        )
        return row is not None

    async def card_other_boxes(self, card_id: int, exclude_box_id: int) -> list[dict]:
        """Активные коробки карты кроме указанной: [{box_id, box_number, status, branch_name}]."""
        rows = await self.db.fetch_all(
            """SELECT b.id AS box_id, b.box_number, b.status, br.name AS branch_name
               FROM box_cards bc
               JOIN boxes b ON b.id = bc.box_id
               JOIN branches br ON br.id = b.origin_branch_id
               WHERE bc.card_id = ? AND bc.removed_at IS NULL AND bc.box_id != ?
               ORDER BY b.id""",
            (card_id, exclude_box_id),
        )
        return [
            {
                "box_id": r["box_id"],
                "box_number": r["box_number"],
                "status": BoxStatus(r["status"]),
                "branch_name": r["branch_name"],
            }
            for r in rows
        ]

    async def card_in_other_boxes(self, card_id: int, exclude_box_id: int) -> bool:
        """Есть ли карта в других (не удалённых) коробках, кроме указанной."""
        return bool(await self.card_other_boxes(card_id, exclude_box_id))

    async def get_cards(self, box_id: int) -> list[Card]:
        rows = await self.db.fetch_all(
            """SELECT c.* FROM cards c
               JOIN box_cards bc ON c.id = bc.card_id
               WHERE bc.box_id = ? AND bc.removed_at IS NULL""",
            (box_id,),
        )
        return [
            Card(id=r["id"], full_name=r["full_name"], status=CardStatus(r["status"]))
            for r in rows
        ]


class BranchRepositoryImpl:
    def __init__(self, db: Database):
        self.db = db

    async def list_all(self) -> list[Branch]:
        rows = await self.db.fetch_all("SELECT * FROM branches ORDER BY name")
        return [Branch(id=r["id"], name=r["name"], code=r["code"]) for r in rows]

    async def get_by_id(self, branch_id: int) -> Branch | None:
        row = await self.db.fetch_one("SELECT * FROM branches WHERE id = ?", (branch_id,))
        if row is None:
            return None
        return Branch(id=row["id"], name=row["name"], code=row["code"])

    async def create(self, name: str, code: str) -> Branch:
        cursor = await self.db.execute(
            "INSERT INTO branches (name, code) VALUES (?, ?)",
            (name, code),
        )
        await self.db.conn.commit()
        return Branch(id=cursor.lastrowid, name=name, code=code)

    async def update(self, branch_id: int, name: str | None = None, code: str | None = None) -> None:
        fields = []
        params: list = []
        if name is not None:
            fields.append("name = ?")
            params.append(name)
        if code is not None:
            fields.append("code = ?")
            params.append(code)
        if not fields:
            return
        params.append(branch_id)
        await self.db.execute(
            f"UPDATE branches SET {', '.join(fields)} WHERE id = ?",
            tuple(params),
        )
        await self.db.conn.commit()

    async def delete(self, branch_id: int) -> None:
        await self.db.execute("DELETE FROM branches WHERE id = ?", (branch_id,))
        await self.db.conn.commit()


class UserRepositoryImpl:
    def __init__(self, db: Database):
        self.db = db

    async def get_by_id(self, user_id: int) -> User | None:
        row = await self.db.fetch_one("SELECT * FROM users WHERE id = ?", (user_id,))
        if row is None:
            return None
        return User.from_row(row)

    async def get_by_username(self, username: str) -> User | None:
        row = await self.db.fetch_one(
            "SELECT * FROM users WHERE username = ?", (username,)
        )
        if row is None:
            return None
        return User.from_row(row)

    async def create(self, username: str, password_hash: str, role: str, branch_id: int | None) -> User:
        cursor = await self.db.execute(
            "INSERT INTO users (username, password_hash, role, branch_id) VALUES (?, ?, ?, ?)",
            (username, password_hash, role, branch_id),
        )
        await self.db.conn.commit()
        return User.from_row({
            "id": cursor.lastrowid,
            "username": username,
            "password_hash": password_hash,
            "role": role,
            "branch_id": branch_id,
            "active_box_id": None,
        })

    async def list_all(self) -> list[User]:
        rows = await self.db.fetch_all("SELECT * FROM users ORDER BY username")
        return [User.from_row(r) for r in rows]

    async def update(
        self,
        user_id: int,
        username: str | None = None,
        password_hash: str | None = None,
        role: str | None = None,
        branch_id: int | None = None,
    ) -> None:
        fields = []
        params: list = []
        if username is not None:
            fields.append("username = ?")
            params.append(username)
        if password_hash is not None:
            fields.append("password_hash = ?")
            params.append(password_hash)
        if role is not None:
            fields.append("role = ?")
            params.append(role.value if isinstance(role, UserRole) else role)
        if branch_id is not None:
            fields.append("branch_id = ?")
            params.append(branch_id)
        if not fields:
            return
        params.append(user_id)
        await self.db.execute(
            f"UPDATE users SET {', '.join(fields)} WHERE id = ?",
            tuple(params),
        )
        await self.db.conn.commit()

    async def delete(self, user_id: int) -> None:
        await self.db.execute("DELETE FROM users WHERE id = ?", (user_id,))
        await self.db.conn.commit()


class CourierRepositoryImpl:
    def __init__(self, db: Database):
        self.db = db

    async def create(self, name: str, phone: str | None = None) -> Courier:
        cursor = await self.db.execute(
            "INSERT INTO couriers (name, phone) VALUES (?, ?)",
            (name, phone),
        )
        await self.db.conn.commit()
        return Courier(id=cursor.lastrowid, name=name, phone=phone)

    async def list_all(self) -> list[Courier]:
        rows = await self.db.fetch_all("SELECT * FROM couriers ORDER BY name")
        return [Courier(id=r["id"], name=r["name"], phone=r["phone"]) for r in rows]

    async def get_by_id(self, courier_id: int) -> Courier | None:
        row = await self.db.fetch_one("SELECT * FROM couriers WHERE id = ?", (courier_id,))
        if row is None:
            return None
        return Courier(id=row["id"], name=row["name"], phone=row["phone"])

    async def update(self, courier_id: int, name: str | None = None, phone: str | None = None) -> None:
        fields = []
        params: list = []
        if name is not None:
            fields.append("name = ?")
            params.append(name)
        if phone is not None:
            fields.append("phone = ?")
            params.append(phone)
        if not fields:
            return
        params.append(courier_id)
        await self.db.execute(
            f"UPDATE couriers SET {', '.join(fields)} WHERE id = ?",
            tuple(params),
        )
        await self.db.conn.commit()

    async def delete(self, courier_id: int) -> None:
        await self.db.execute("DELETE FROM couriers WHERE id = ?", (courier_id,))
        await self.db.conn.commit()


class SectionRepositoryImpl:
    """Справочник секций архива."""

    def __init__(self, db: Database):
        self.db = db

    async def list_all(self) -> list[dict]:
        return await self.db.fetch_all(
            "SELECT * FROM archive_sections ORDER BY name"
        )

    async def get_by_id(self, section_id: int) -> dict | None:
        return await self.db.fetch_one(
            "SELECT * FROM archive_sections WHERE id = ?", (section_id,)
        )

    async def create(self, name: str) -> int:
        cursor = await self.db.execute(
            "INSERT INTO archive_sections (name) VALUES (?)", (name,)
        )
        await self.db.conn.commit()
        return cursor.lastrowid

    async def update(self, section_id: int, name: str) -> None:
        await self.db.execute(
            "UPDATE archive_sections SET name = ? WHERE id = ?",
            (name, section_id),
        )
        await self.db.conn.commit()

    async def delete(self, section_id: int) -> None:
        await self.db.execute(
            "DELETE FROM archive_sections WHERE id = ?", (section_id,)
        )
        await self.db.conn.commit()


class ShelfRepositoryImpl:
    """Справочник полок архива (вложены в секции)."""

    def __init__(self, db: Database):
        self.db = db

    async def list_all(self) -> list[dict]:
        return await self.db.fetch_all(
            """SELECT s.*, sec.name as section_name
               FROM archive_shelves s
               JOIN archive_sections sec ON s.section_id = sec.id
               ORDER BY sec.name, s.name"""
        )

    async def get_by_section(self, section_id: int) -> list[dict]:
        return await self.db.fetch_all(
            "SELECT * FROM archive_shelves WHERE section_id = ? ORDER BY name",
            (section_id,),
        )

    async def get_by_id(self, shelf_id: int) -> dict | None:
        return await self.db.fetch_one(
            "SELECT * FROM archive_shelves WHERE id = ?", (shelf_id,)
        )

    async def create(
        self, section_id: int, name: str, qr_code: str | None = None
    ) -> int:
        cursor = await self.db.execute(
            "INSERT INTO archive_shelves (section_id, name, qr_code) VALUES (?, ?, ?)",
            (section_id, name, qr_code),
        )
        await self.db.conn.commit()
        return cursor.lastrowid

    async def update(
        self,
        shelf_id: int,
        name: str | None = None,
        qr_code: str | None = None,
    ) -> None:
        fields = []
        params: list = []
        if name is not None:
            fields.append("name = ?")
            params.append(name)
        if qr_code is not None:
            fields.append("qr_code = ?")
            params.append(qr_code)
        if not fields:
            return
        params.append(shelf_id)
        await self.db.execute(
            f"UPDATE archive_shelves SET {', '.join(fields)} WHERE id = ?",
            tuple(params),
        )
        await self.db.conn.commit()

    async def delete(self, shelf_id: int) -> None:
        await self.db.execute(
            "DELETE FROM archive_shelves WHERE id = ?", (shelf_id,)
        )
        await self.db.conn.commit()

    async def generate_qr(self, shelf_id: int) -> str:
        """Генерирует QR-код полки (текст S{section_id}-P{shelf_id})
        и сохраняет base64 PNG в поле qr_code. Возвращает base64."""
        import base64
        import io
        import qrcode

        shelf = await self.get_by_id(shelf_id)
        if shelf is None:
            raise ValueError(f"Полка {shelf_id} не найдена")
        text = f"S{shelf['section_id']}-P{shelf['id']}"
        qr = qrcode.make(text)
        buf = io.BytesIO()
        qr.save(buf, "PNG")
        buf.seek(0)
        qr_code = base64.b64encode(buf.getvalue()).decode()
        await self.update(shelf_id, qr_code=qr_code)
        return qr_code
