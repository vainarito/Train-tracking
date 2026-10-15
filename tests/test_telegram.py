"""
Скрипт проверки связи с Telegram ботом.
Запуск: python tests/test_telegram.py
"""

import sys
from pathlib import Path

# Обеспечиваем доступ к пакету src
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# Обеспечиваем корректный вывод UTF-8 в консоли Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from src import config
from src.notifier import send_message


def main():
    print("=" * 50)
    print("ПРОВЕРКА НАСТРОЕК TELEGRAM")
    print("=" * 50)

    if not config.BOT_TOKEN or config.BOT_TOKEN in ("ВАШ_ТОКЕН_БОТА", "your_bot_token_here"):
        print("[!] ОШИБКА: Не указан TELEGRAM_BOT_TOKEN!")
        print("    Создайте файл .env (на основе .env.example) и укажите токен от @BotFather.")
        sys.exit(1)

    if not config.CHAT_ID or config.CHAT_ID in ("ВАШ_CHAT_ID", "your_chat_id_here"):
        print("[!] ОШИБКА: Не указан TELEGRAM_CHAT_ID!")
        print("    Создайте файл .env (на основе .env.example) и укажите ваш ID от @userinfobot.")
        sys.exit(1)

    print(f"Отправка тестового сообщения на Chat ID: {config.CHAT_ID}...")
    
    test_text = (
        "👋 <b>Привет! Связь с ботом успешно настроена!</b>\n\n"
        "Скрипт отслеживания билетов на <code>pass.rw.by</code> сможет присылать вам "
        "уведомления сразу, как только кто-то сдаст билет."
    )

    success = send_message(config.BOT_TOKEN, config.CHAT_ID, test_text)

    if success:
        print("[+] УСПЕХ! Проверьте ваш Telegram — бот отправил тестовое сообщение.")
        print("Теперь можно запускать основной трекер: python main.py")
    else:
        print("[-] ОШИБКА отправки сообщения. Проверьте следующее:")
        print("1. Вы нажали кнопку 'START' / 'НАЧАТЬ' в диалоге с созданным ботом в Telegram?")
        print("2. Токен бота скопирован полностью и без лишних пробелов?")
        print("3. Chat ID указан корректно (только цифры)?")


if __name__ == "__main__":
    main()
