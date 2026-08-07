# Этап 5 — Полировка и развёртывание: Implementation Plan

> 2026-08-07 | Текущее: 185 тестов, этапы 0–4 завершены

## Tasks

### Task 1: README.md
- Назначение системы, стек
- Установка (зависимости, инициализация, первый запуск)
- Роли и доступ
- Администрирование (бэкап, восстановление)
- QR-печать

### Task 2: systemd-юнит
- `scripts/medarchive.service` — запуск uvicorn, auto-restart
- `scripts/install.sh` — скопировать юнит, enable, start

### Task 3: Бэкап по расписанию
- `scripts/medarchive-backup.timer` — systemd timer (hourly)
- `scripts/medarchive-backup.service` — вызов скрипта бэкапа
- `scripts/backup.sh` — копирование .db в backup-директорию с ротацией

### Task 4: Приёмочный тест
- Проверка всех E2E smoke-тестов (stage1–stage4)
- Проверка запуска приложения

### Task 5: git tag v1.0.0

**Итого:** 5 задач, ~0 новых unit-тестов (инфраструктурные). Все существующие 185 тестов должны остаться зелёными.
