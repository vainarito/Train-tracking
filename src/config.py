"""
Модуль конфигурации: считывает переменные окружения из .env и маршруты из config/routes.json.
"""

import json
import os
from pathlib import Path
from dotenv import load_dotenv

# Базовая директория проекта (корень train-tracking)
BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE_DIR / "config"

# Загружаем переменные окружения из .env (в корне проекта)
env_path = BASE_DIR / ".env"
if env_path.exists():
    load_dotenv(dotenv_path=env_path)
else:
    load_dotenv()


# ==============================================================================
# 1. НАСТРОЙКИ TELEGRAM (из .env)
# ==============================================================================
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID", "")


# ==============================================================================
# 2. ПАРАМЕТРЫ РАБОТЫ СКРИПТА (из .env)
# ==============================================================================
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "15"))
HEADLESS = os.getenv("HEADLESS", "True").strip().lower() in ("true", "1", "yes")
SEND_SCREENSHOT = os.getenv("SEND_SCREENSHOT", "True").strip().lower() in ("true", "1", "yes")
SOUND_ALERT = os.getenv("SOUND_ALERT", "True").strip().lower() in ("true", "1", "yes")


# ==============================================================================
# 3. МАРШРУТЫ ПО УМОЛЧАНИЮ
# ==============================================================================
DEFAULT_ROUTES = [
    {
        "name": "ТУДА (Минск ➔ Брест)",
        "from": "Минск-Пассажирский",
        "to": "Брест-Центральный",
        "date": "2026-12-30",
        "train": "",
        "car_types": []
    },
    {
        "name": "ОБРАТНО (Брест ➔ Минск)",
        "from": "Брест-Центральный",
        "to": "Минск-Пассажирский",
        "date": "2027-01-03",
        "train": "727Б, 657Б",
        "car_types": ["Плацкарт", "Купе"]
    }
]


def load_routes() -> list:
    """
    Загружает список маршрутов из config/routes.json (или кастомного ROUTES_FILE).
    Если файл не найден, возвращает DEFAULT_ROUTES.
    """
    custom_file = os.getenv("ROUTES_FILE", "routes.json")
    
    candidates = [
        CONFIG_DIR / custom_file,
        BASE_DIR / custom_file,
        Path(custom_file)
    ]

    for path in candidates:
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    if isinstance(loaded, list) and len(loaded) > 0:
                        return loaded
            except Exception as e:
                print(f"[config] Ошибка загрузки маршрутов из {path}: {e}")

    return DEFAULT_ROUTES


ROUTES = load_routes()
