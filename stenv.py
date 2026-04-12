"""Загрузка переменных из файла .env в корне проекта (если установлен python-dotenv)."""

from __future__ import annotations

from pathlib import Path


def load_project_env() -> None:
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.is_file():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(env_path)
