"""Сервис бэкапов: создание, информация и восстановление базы данных."""
import os
from datetime import datetime


class BackupService:
    """Работа с файлом SQLite-БД: скачивание и восстановление."""

    @staticmethod
    def create_backup(db_path: str) -> tuple[bytes, str]:
        """Читает .db файл, возвращает (binary_data, filename)."""
        with open(db_path, "rb") as f:
            data = f.read()
        filename = f"medarchive_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        return data, filename

    @staticmethod
    def get_db_info(db_path: str) -> dict:
        """Размер файла в МБ и путь к БД."""
        if os.path.exists(db_path):
            size_mb = os.path.getsize(db_path) / (1024 * 1024)
        else:
            size_mb = 0.0
        return {"path": db_path, "size_mb": round(size_mb, 2)}

    @staticmethod
    def restore_backup(db_path: str, file_bytes: bytes) -> None:
        """Перезаписывает .db файл содержимым бэкапа."""
        with open(db_path, "wb") as f:
            f.write(file_bytes)
