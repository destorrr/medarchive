"""SQLite-реализации репозиториев."""
from medarchive.adapters.sqlite.database import Database
from medarchive.domain.models import Card, Box, User, Courier
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
            "INSERT INTO cards (full_name, status) VALUES (?, ?)",
            (full_name, CardStatus.in_branch.value),
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

    async def get_by_id(self, box_id: int) -> Box | None:
        row = await self.db.fetch_one("SELECT * FROM boxes WHERE id = ?", (box_id,))
        if row is None:
            return None
        return Box(
            id=row["id"],
            box_number=row["box_number"],
            origin_branch_id=row["origin_branch_id"],
            created_by=row["created_by"],
            status=BoxStatus(row["status"]),
        )

    async def get_by_number(self, box_number: str) -> Box | None:
        row = await self.db.fetch_one(
            "SELECT * FROM boxes WHERE box_number = ?", (box_number,)
        )
        if row is None:
            return None
        return Box(
            id=row["id"],
            box_number=row["box_number"],
            origin_branch_id=row["origin_branch_id"],
            created_by=row["created_by"],
            status=BoxStatus(row["status"]),
        )

    async def create(self, box_number: str, origin_branch_id: int, created_by: int) -> Box:
        cursor = await self.db.execute(
            "INSERT INTO boxes (box_number, origin_branch_id, created_by, status) VALUES (?, ?, ?, ?)",
            (box_number, origin_branch_id, created_by, BoxStatus.packing.value),
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

    async def add_card(self, box_id: int, card_id: int) -> None:
        # Check for duplicate
        existing = await self.db.fetch_one(
            "SELECT id FROM box_cards WHERE box_id = ? AND card_id = ? AND removed_at IS NULL",
            (box_id, card_id),
        )
        if existing is not None:
            return  # Already in box, ignore
        await self.db.execute(
            "INSERT INTO box_cards (box_id, card_id) VALUES (?, ?)",
            (box_id, card_id),
        )
        await self.db.conn.commit()

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


class UserRepositoryImpl:
    def __init__(self, db: Database):
        self.db = db

    async def get_by_id(self, user_id: int) -> User | None:
        row = await self.db.fetch_one("SELECT * FROM users WHERE id = ?", (user_id,))
        if row is None:
            return None
        return User(
            id=row["id"],
            username=row["username"],
            password_hash=row["password_hash"],
            role=UserRole(row["role"]),
            branch_id=row["branch_id"],
        )

    async def get_by_username(self, username: str) -> User | None:
        row = await self.db.fetch_one(
            "SELECT * FROM users WHERE username = ?", (username,)
        )
        if row is None:
            return None
        return User(
            id=row["id"],
            username=row["username"],
            password_hash=row["password_hash"],
            role=UserRole(row["role"]),
            branch_id=row["branch_id"],
        )

    async def create(self, username: str, password_hash: str, role: str, branch_id: int | None) -> User:
        cursor = await self.db.execute(
            "INSERT INTO users (username, password_hash, role, branch_id) VALUES (?, ?, ?, ?)",
            (username, password_hash, role, branch_id),
        )
        await self.db.conn.commit()
        return User(
            id=cursor.lastrowid,
            username=username,
            password_hash=password_hash,
            role=UserRole(role),
            branch_id=branch_id,
        )


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
