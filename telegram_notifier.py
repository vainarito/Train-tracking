import json
import re
import sys
from typing import Optional, List
import requests

# Обеспечиваем корректный вывод UTF-8 в консоли Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def strip_html_tags(text: str) -> str:
    """Удаляет любые HTML теги из строки."""
    return re.sub(r'<[^>]*>', '', text)


def _safe_fit_caption(text: Optional[str], limit: int = 1024) -> Optional[str]:
    """
    Безопасно укладывает текст в лимит Telegram (1024 символа),
    не разрывая HTML-теги и закрывая все открытые теги.
    """
    if not text:
        return None
    if len(text) <= limit:
        return text

    # Обрезаем по целым строкам
    lines = text.split("\n")
    result = []
    current_len = 0
    for line in lines:
        if current_len + len(line) + 1 > (limit - 50):
            break
        result.append(line)
        current_len += len(line) + 1

    result.append("\n...")
    trimmed = "\n".join(result)

    # Автоматически закрываем незакрытые теги b, i, code, a
    for tag in ("b", "i", "code", "a"):
        open_count = len(re.findall(rf'<{tag}\b[^>]*>', trimmed, re.IGNORECASE))
        close_count = len(re.findall(rf'</{tag}>', trimmed, re.IGNORECASE))
        if open_count > close_count:
            trimmed += f"</{tag}>" * (open_count - close_count)

    return trimmed


def send_message(token: str, chat_id: str, text: str, parse_mode: Optional[str] = "HTML") -> bool:
    """
    Отправляет текстовое сообщение в Telegram через HTTP API.
    При ошибке парсинга HTML автоматически переотправляет как чистый текст.
    """
    if not token or not chat_id:
        print("[Telegram] Ошибка: Не указан BOT_TOKEN или CHAT_ID.")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": False
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode

    try:
        response = requests.post(url, json=payload, timeout=15)
        data = response.json()
        if response.status_code == 200 and data.get("ok"):
            return True

        desc = data.get("description", "")
        # Если ошибка связана с HTML-тегами, переотправляем чистым текстом
        if "can't parse entities" in desc.lower() and parse_mode:
            clean_text = strip_html_tags(text)
            return send_message(token, chat_id, clean_text, parse_mode=None)

        print(f"[Telegram] Ошибка API sendMessage: {desc}")
        return False
    except Exception as e:
        print(f"[Telegram] Исключение при отправке сообщения: {e}")
        return False


def send_photo(token: str, chat_id: str, photo_bytes: bytes, caption: Optional[str] = None, parse_mode: Optional[str] = "HTML") -> bool:
    """
    Отправляет одно фото (скриншот) в Telegram с прикрепленной подписью.
    """
    if not token or not chat_id:
        print("[Telegram] Ошибка: Не указан BOT_TOKEN или CHAT_ID.")
        return False

    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    data = {"chat_id": chat_id}

    clean_caption = _safe_fit_caption(caption)
    if clean_caption:
        data["caption"] = clean_caption
        if parse_mode:
            data["parse_mode"] = parse_mode

    files = {
        "photo": ("ticket.png", photo_bytes, "image/png")
    }

    try:
        response = requests.post(url, data=data, files=files, timeout=25)
        res_json = response.json()
        if response.status_code == 200 and res_json.get("ok"):
            return True

        desc = res_json.get("description", "")
        # Если ошибка в HTML-тегах, пробуем отправить без HTML
        if "can't parse entities" in desc.lower() and parse_mode and clean_caption:
            return send_photo(token, chat_id, photo_bytes, caption=strip_html_tags(clean_caption), parse_mode=None)

        print(f"[Telegram] Ошибка API sendPhoto: {desc}")
        return False
    except Exception as e:
        print(f"[Telegram] Исключение при отправке фото: {e}")
        return False


def send_media_group(token: str, chat_id: str, photos: List[bytes], caption: Optional[str] = None, parse_mode: Optional[str] = "HTML") -> bool:
    """
    Отправляет альбом фото со встроенной подписью прямо на фото.
    """
    if not token or not chat_id:
        print("[Telegram] Ошибка: Не указан BOT_TOKEN или CHAT_ID.")
        return False

    if not photos:
        if caption:
            return send_message(token, chat_id, caption, parse_mode=parse_mode)
        return False

    if len(photos) == 1:
        return send_photo(token, chat_id, photos[0], caption=caption, parse_mode=parse_mode)

    url = f"https://api.telegram.org/bot{token}/sendMediaGroup"
    clean_caption = _safe_fit_caption(caption)

    media = []
    files = {}

    for idx, photo_bytes in enumerate(photos):
        attach_name = f"photo_{idx}"
        files[attach_name] = (f"{attach_name}.png", photo_bytes, "image/png")

        item = {
            "type": "photo",
            "media": f"attach://{attach_name}"
        }
        if idx == 0 and clean_caption:
            item["caption"] = clean_caption
            if parse_mode:
                item["parse_mode"] = parse_mode

        media.append(item)

    data = {
        "chat_id": chat_id,
        "media": json.dumps(media)
    }

    try:
        response = requests.post(url, data=data, files=files, timeout=35)
        res_json = response.json()
        if response.status_code == 200 and res_json.get("ok"):
            return True

        desc = res_json.get("description", "")
        # Если ошибка в HTML-тегах, отправляем альбом с чистым текстом
        if "can't parse entities" in desc.lower() and parse_mode and clean_caption:
            return send_media_group(token, chat_id, photos, caption=strip_html_tags(clean_caption), parse_mode=None)

        print(f"[Telegram] Ошибка API sendMediaGroup: {desc}")
        # Резерв: пробуем отправить первое фото
        if len(photos) > 0:
            return send_photo(token, chat_id, photos[0], caption=caption, parse_mode=parse_mode)
        return False
    except Exception as e:
        print(f"[Telegram] Исключение при отправке медиагруппы: {e}")
        return False


def send_combined_alert(token: str, chat_id: str, text: str, photos: Optional[List[bytes]] = None) -> bool:
    """
    Универсальная отправка уведомления:
    - если фото есть -> отправляет фото/альбом с прикрепленным текстом
    - если фото нет -> текстовое сообщение
    """
    if photos and len(photos) > 0:
        success = send_media_group(token, chat_id, photos, caption=text)
        if not success:
            # Если отправка фото совсем не удалась, гарантированно отправляем текст
            return send_message(token, chat_id, text)
        return success
    else:
        return send_message(token, chat_id, text)
