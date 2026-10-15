"""
Вспомогательные утилиты для нормализации поездов, звукового сигнала и работы со страницей.
"""

import sys
import time
import urllib.parse
from typing import Any, List
from playwright.async_api import Page
from src import config


def normalize_train_num(num: str) -> str:
    """
    Нормализует номер поезда:
    - убирает пробелы, переводит в верхний регистр
    - заменяет похожие латинские буквы на кириллицу (например 'B' -> 'Б', 'A' -> 'А')
    """
    if not num:
        return ""
    trans = str.maketrans({
        'B': 'Б', 'b': 'Б',
        'A': 'А', 'a': 'А',
        'C': 'С', 'c': 'С',
        'T': 'Т', 't': 'Т',
        'H': 'Н', 'h': 'Н',
        'P': 'Р', 'p': 'Р',
        'O': 'О', 'o': 'О',
        'X': 'Х', 'x': 'Х',
        'E': 'Е', 'e': 'Е',
        'M': 'М', 'm': 'М',
        'K': 'К', 'k': 'К',
    })
    return str(num).strip().replace(" ", "").upper().translate(trans)


def parse_train_filter(filter_val: Any) -> List[str]:
    """
    Парсит фильтр поездов: поддерживает список ['635Б', '657Б'],
    строку через запятую '635Б, 657Б' или одиночное значение.
    """
    if not filter_val:
        return []
    if isinstance(filter_val, (list, tuple)):
        raw_list = filter_val
    elif isinstance(filter_val, str):
        raw_list = [x.strip() for x in filter_val.replace(";", ",").split(",")]
    else:
        raw_list = [str(filter_val)]

    return [normalize_train_num(x) for x in raw_list if x.strip()]


def matches_any_train(req_trains: List[str], found_num: str) -> bool:
    """
    Проверяет, соответствует ли найденный номер поезда фильтру.
    """
    if not req_trains:
        return True
    norm_found = normalize_train_num(found_num)
    if not norm_found:
        return False
    for req in req_trains:
        if not req:
            continue
        if req == norm_found or req in norm_found or (len(req) >= 3 and len(norm_found) >= 3 and norm_found in req):
            return True
    return False


def get_search_url(from_station: str, to_station: str, date: str) -> str:
    """
    Формирует ссылку для поиска расписания на pass.rw.by.
    """
    params = {
        "from": from_station,
        "to": to_station,
        "date": date
    }
    encoded_params = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    return f"https://pass.rw.by/ru/route/?{encoded_params}"


def play_sound_alert():
    """
    Воспроизводит звуковой сигнал на компьютере при появлении билетов.
    """
    if not config.SOUND_ALERT:
        return
    if sys.platform == "win32":
        try:
            import winsound
            for freq in (800, 1000, 1200, 1500):
                winsound.Beep(freq, 180)
                time.sleep(0.04)
        except Exception:
            pass


async def remove_cookie_banners(page: Page):
    """
    Удаляет баннеры cookie из DOM страницы без принятия,
    чтобы они не закрывали расписание на скриншотах.
    """
    remove_js = """
    () => {
        const selectors = [
            '[class*="cookie" i]',
            '[id*="cookie" i]',
            '.b-cookies',
            '.cookie-banner',
            '.cookies-block',
            '.cookies-notice',
            '.popup-cookie',
            '.modal-cookie',
            '.alert-cookie'
        ];
        selectors.forEach(sel => {
            try {
                document.querySelectorAll(sel).forEach(el => el.remove());
            } catch(e) {}
        });
    }
    """
    try:
        await page.evaluate(remove_js)
    except Exception:
        pass
