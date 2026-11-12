"""Команды администрирования целевой PostgreSQL-БД."""
import argparse
import asyncio
import getpass

from sqlalchemy import func, select, text

from medarchive.adapters.postgresql.database import Database
from medarchive.adapters.postgresql.models import User
from medarchive.adapters.postgresql.repositories import UserRepository
from medarchive.config import Config
from medarchive.services.auth_service import AuthService


async def create_first_admin(username: str, full_name: str, password: str) -> None:
    config = Config()
    database = Database(config.effective_database_url)
    credentials = AuthService("bootstrap-password-hashing-only")
    try:
        async with database.session_factory() as session:
            async with session.begin():
                # Serialize concurrent bootstrap attempts and prevent two first admins.
                await session.execute(text("SELECT pg_advisory_xact_lock(741928361)"))
                user_count = await session.scalar(select(func.count()).select_from(User))
                if user_count:
                    raise RuntimeError(
                        "Bootstrap запрещён: в таблице users уже есть пользователи."
                    )
                await UserRepository(session).create(
                    username=username,
                    password_hash=credentials.hash_password(password),
                    full_name=full_name,
                    role="admin",
                    branch_id=None,
                )
    finally:
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="medarchive-admin")
    subparsers = parser.add_subparsers(dest="command", required=True)
    admin_parser = subparsers.add_parser(
        "create-admin", help="создать первого администратора в пустой PostgreSQL-БД"
    )
    admin_parser.add_argument("--username", required=True)
    admin_parser.add_argument("--full-name", required=True)
    args = parser.parse_args()

    username = args.username.strip().lower()
    full_name = args.full_name.strip()
    if not username or not full_name:
        parser.error("Логин и ФИО не должны быть пустыми.")

    password = getpass.getpass("Пароль (не менее 12 символов): ")
    confirmation = getpass.getpass("Повторите пароль: ")
    if len(password) < 12:
        parser.error("Пароль должен содержать не менее 12 символов.")
    if password != confirmation:
        parser.error("Пароли не совпадают.")

    asyncio.run(create_first_admin(username, full_name, password))
    print(f"Администратор {username} создан.")


if __name__ == "__main__":
    main()
