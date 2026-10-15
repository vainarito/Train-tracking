"""
Модуль отправки уведомлений в Telegram (текст, фото, медиагруппы со скриншотами).
"""

import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional
import requests


def strip_html_tags(text: str) -> str:
    """Удаляет любые HTML теги из строки."""
    return re.sub(r'<[^>]*>', '', text)


def _safe_fit_caption(text: Optional[str], limit: int = 1024) -> Optional[str]:
    """
    Безопасно укладывает текст подписи в лимит Telegram (1024 символа),
    не разрывая HTML-теги и автоматически закрывая открытые теги.
    """
    if not text:
        return None
    if len(text) <= limit:
        return text

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

    for tag in ("b", "i", "code", "a"):
        open_count = len(re.findall(rf'<{tag}\b[^>]*>', trimmed, re.IGNORECASE))
        close_count = len(re.findall(rf'</{tag}>', trimmed, re.IGNORECASE))
        if open_count > close_count:
            trimmed += f"</{tag}>" * (open_count - close_count)

    return trimmed


def format_combined_alert_message(found_routes: List[Dict[str, Any]]) -> str:
    """
    Формирует структурированное сообщение для Telegram,
    гарантированно укладывающееся под скриншотами (до 1024 символов).
    """
    lines = [
        "🎫 <b>НАЙДЕНЫ БИЛЕТЫ НА ПОЕЗД!</b>\n"
    ]

    for idx, item in enumerate(found_routes):
        route = item["route"]
        trains = item["available_trains"]
        url = item["target_url"]

        name = route.get("name") or f"{route.get('from')} ➔ {route.get('to')}"
        date_str = route.get("date", "")
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            weekdays = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
            formatted_date = f"{dt.strftime('%d.%m.%Y')} ({weekdays[dt.weekday()]})"
        except Exception:
            formatted_date = date_str

        direction_icon = "🔹" if "туда" in name.lower() or idx == 0 else "🔸"
        lines.append(f"{direction_icon} <b>{name}</b> ({formatted_date})")

        for t in trains:
            num = t.get("trainNumber") or "Поезд"
            dep = t.get("depTime") or "—"
            arr = t.get("arrTime") or "—"
            raw_cars = t.get("carInfoList", [])
            cars_str = ", ".join(raw_cars) if raw_cars else t.get("carInfo", "Есть места")
            lines.append(f"• <b>№{num}</b> ({dep} ➔ {arr}): {cars_str}")

        dir_label = "ТУДА" if "туда" in name.lower() or idx == 0 else "ОБРАТНО"
        lines.append(f'🔗 <a href="{url}"><b>Купить билет {dir_label}</b></a>\n')

    now_time = datetime.now().strftime("%H:%M:%S")
    lines.append(f"🕒 <i>Обновлено в {now_time}</i>")

    return "\n".join(lines)


def send_message(token: str, chat_id: str, text: str, parse_mode: Optional[str] = "HTML") -> bool:
    """Отправляет текстовое сообщение в Telegram."""
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
        if "can't parse entities" in desc.lower() and parse_mode:
            clean_text = strip_html_tags(text)
            return send_message(token, chat_id, clean_text, parse_mode=None)

        print(f"[Telegram] Ошибка API sendMessage: {desc}")
        return False
    except Exception as e:
        print(f"[Telegram] Исключение при отправке сообщения: {e}")
        return False


def send_photo(token: str, chat_id: str, photo_bytes: bytes, caption: Optional[str] = None, parse_mode: Optional[str] = "HTML") -> bool:
    """Отправляет одно фото с подписью в Telegram."""
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

    files = {"photo": ("ticket.png", photo_bytes, "image/png")}

    try:
        response = requests.post(url, data=data, files=files, timeout=25)
        res_json = response.json()
        if response.status_code == 200 and res_json.get("ok"):
            return True

        desc = res_json.get("description", "")
        if "can't parse entities" in desc.lower() and parse_mode and clean_caption:
            return send_photo(token, chat_id, photo_bytes, caption=strip_html_tags(clean_caption), parse_mode=None)

        print(f"[Telegram] Ошибка API sendPhoto: {desc}")
        return False
    except Exception as e:
        print(f"[Telegram] Исключение при отправке фото: {e}")
        return False


def send_media_group(token: str, chat_id: str, photos: List[bytes], caption: Optional[str] = None, parse_mode: Optional[str] = "HTML") -> bool:
    """Отправляет альбом фото со встроенной подписью на первом фото."""
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
        if "can't parse entities" in desc.lower() and parse_mode and clean_caption:
            return send_media_group(token, chat_id, photos, caption=strip_html_tags(clean_caption), parse_mode=None)

        print(f"[Telegram] Ошибка API sendMediaGroup: {desc}")
        if len(photos) > 0:
            return send_photo(token, chat_id, photos[0], caption=caption, parse_mode=parse_mode)
        return False
    except Exception as e:
        print(f"[Telegram] Исключение при отправке медиагруппы: {e}")
        return False


def send_combined_alert(token: str, chat_id: str, text: str, photos: Optional[List[bytes]] = None) -> bool:
    """
    Универсальная отправка уведомления:
    - если есть фото -> отправляет медиагруппу со скриншотами и подписью
    - если фото нет -> текстовое сообщение
    """
    if photos and len(photos) > 0:
        success = send_media_group(token, chat_id, photos, caption=text)
        if not success:
            return send_message(token, chat_id, text)
        return success
    else:
        return send_message(token, chat_id, text)
