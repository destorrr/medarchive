from medarchive.services.backup_service import BackupService


class TestBackupService:
    def test_create_backup_returns_bytes(self, tmp_path):
        db_path = tmp_path / "test.db"
        db_path.write_bytes(b"\x00\x01fake-db-content")
        data, filename = BackupService.create_backup(str(db_path))
        assert isinstance(data, bytes)
        assert data == b"\x00\x01fake-db-content"
        assert filename.endswith(".db")
        assert "medarchive" in filename

    def test_get_db_info(self, tmp_path):
        db_path = tmp_path / "test.db"
        # 1 МБ = 1048576 байт
        db_path.write_bytes(b"x" * (1024 * 1024))
        info = BackupService.get_db_info(str(db_path))
        assert info["path"] == str(db_path)
        assert info["size_mb"] == 1.0

    def test_get_db_info_missing_file(self, tmp_path):
        db_path = tmp_path / "no_such.db"
        info = BackupService.get_db_info(str(db_path))
        assert info["path"] == str(db_path)
        assert info["size_mb"] == 0.0

    def test_restore_backup_overwrites_file(self, tmp_path):
        db_path = tmp_path / "test.db"
        db_path.write_bytes(b"old-content")
        BackupService.restore_backup(str(db_path), b"new-backup-content")
        assert db_path.read_bytes() == b"new-backup-content"
