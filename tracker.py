"""
Асинхронный скрипт мониторинга свободных мест на поезда БелЖД (pass.rw.by).
Поддерживает несколько поездов через запятую (например: "635Б, 657Б"),
параллельную проверку направлений и отправку одного сообщения с прикрепленными скриншотами.
"""

import sys

# Обеспечиваем корректный вывод UTF-8 в консоли Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import asyncio
import time
import urllib.parse
from datetime import datetime
from typing import Dict, List, Optional, Any

from playwright.async_api import async_playwright, BrowserContext, Page, TimeoutError as PlaywrightTimeoutError

import config
from telegram_notifier import send_combined_alert


def normalize_train_num(num: str) -> str:
    """
    Нормализует номер поезда:
    - убирает пробелы, приводит к верхнему регистру
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
    Парсит фильтр поездов: поддерживает как строку через запятую '635Б, 657Б',
    так и список ['635Б', '657Б'] или одиночное значение.
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
    Проверяет, подходит ли найденный номер поезда под хотя бы один из запрошенных номеров.
    Гарантирует, что пустые строки не сопоставляются.
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
    Формирует прямую ссылку для поиска на pass.rw.by.
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
    Воспроизводит звуковой сигнал на компьютере (Windows).
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
    Удаляет баннеры cookie из DOM без их принятия, чтобы они не загораживали скриншот.
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


async def extract_trains_data(page: Page) -> List[Dict[str, Any]]:
    """
    Извлекает информацию обо всех поездах и свободных местах со страницы pass.rw.by.
    Фильтрует строки без номеров (заголовки) и исключает дубли.
    """
    js_code = """
    () => {
        const results = [];
        const seenKeys = new Set();
        
        let rows = Array.from(document.querySelectorAll('.sch-table__row, .sch-table__item, .b-train-item, tr.sch-item'));
        if (rows.length === 0) {
            rows = Array.from(document.querySelectorAll('.sch-table tr, .schedule-table tr, div[data-train]'));
        }

        for (const row of rows) {
            const text = (row.innerText || '').trim();
            if (!text || text.length < 8) continue;

            // 1. Извлекаем номер поезда
            let trainNumber = '';
            const numEl = row.querySelector('.sch-table__train-num, .train-num, .train-item__number, .train-item__num, .sch-title__train');
            if (numEl) {
                trainNumber = (numEl.innerText || '').trim();
            }
            if (!trainNumber) {
                const m = text.match(/(?:^|[^\\d])(\\d{3}\\s*[А-ЯA-Za-z]?)(?:[^\\wА-Яа-яЁё]|$)/u);
                if (m) {
                    trainNumber = m[1].trim();
                }
            }
            trainNumber = trainNumber.replace(/\\s+/g, '').toUpperCase();

            // Если номера поезда нет (например строка заголовка таблицы) — пропускаем!
            if (!trainNumber) {
                continue;
            }

            // 2. Время отправления и прибытия
            const times = text.match(/(\\b\\d{1,2}:\\d{2}\\b)/g) || [];
            const depTime = times.length > 0 ? times[0] : '';
            const arrTime = times.length > 1 ? times[1] : '';

            // Дедупликация: если этот же поезд с этим же временем уже обработан, пропускаем
            const dedupeKey = `${trainNumber}_${depTime}_${arrTime}`;
            if (seenKeys.has(dedupeKey)) {
                continue;
            }
            seenKeys.add(dedupeKey);

            // 3. Проверка наличия мест
            const hasNoSeats = /нет мест|мест нет/i.test(text);
            
            const hasOrderButton = !!row.querySelector(
                'a[href*="/order/"], a[href*="/buy/"], .btn-buy, .sch-table__btn, button, a.button'
            ) || /выбрать мест|купить/i.test(text);

            const carMatches = [];
            const carPatterns = [
                { type: 'Плацкарт', regex: /плацкарт[а-я]*\\s*[:\\-]?\\s*(\\d+)?/i },
                { type: 'Купе', regex: /купе\\s*[:\\-]?\\s*(\\d+)?/i },
                { type: 'Сидячий', regex: /сидяч[а-я]*\\s*[:\\-]?\\s*(\\d+)?/i },
                { type: 'Общий', regex: /общ[а-я]*\\s*[:\\-]?\\s*(\\d+)?/i },
                { type: 'СВ / Люкс', regex: /(?:св|люкс)\\s*[:\\-]?\\s*(\\d+)?/i }
            ];

            for (const item of carPatterns) {
                const match = text.match(item.regex);
                if (match) {
                    const count = match[1] ? `${match[1]} мест` : 'есть места';
                    carMatches.push(`${item.type}: ${count}`);
                }
            }

            const hasTickets = (!hasNoSeats && hasOrderButton) || (carMatches.length > 0 && !hasNoSeats);

            results.push({
                trainNumber: trainNumber,
                rawText: text,
                depTime: depTime,
                arrTime: arrTime,
                hasTickets: hasTickets,
                carInfoList: carMatches,
                carInfo: carMatches.length > 0 ? carMatches.join(', ') : (hasTickets ? 'Места доступны' : 'Мест нет')
            });
        }

        return results;
    }
    """
    try:
        return await page.evaluate(js_code)
    except Exception as e:
        print(f"[Парсер] Ошибка при чтении DOM: {e}")
        return []


async def check_single_route_async(
    context: BrowserContext,
    route: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Асинхронно открывает страницу маршрута, собирает данные обо всех поездах,
    фильтрует их, удаляет баннеры cookie и делает скриншот при наличии мест.
    """
    from_st = route.get("from", "")
    to_st = route.get("to", "")
    date_val = route.get("date", "")
    req_trains = parse_train_filter(route.get("train"))
    req_cars = route.get("car_types", [])

    target_url = get_search_url(from_st, to_st, date_val)
    page = await context.new_page()

    result = {
        "route": route,
        "target_url": target_url,
        "req_trains": req_trains,
        "all_trains": [],
        "matched_trains": [],
        "available_trains": [],
        "screenshot_bytes": None,
        "error": None
    }

    try:
        await page.goto(target_url, wait_until="domcontentloaded", timeout=25000)
    except PlaywrightTimeoutError:
        pass
    except Exception as e:
        err_msg = str(e)
        if "ERR_CONNECTION_TIMED_OUT" in err_msg or "Timeout" in err_msg:
            result["error"] = "Таймаут соединения (проверьте, отключен ли McAfee VPN)"
        else:
            result["error"] = str(e)
        await page.close()
        return result

    try:
        await page.wait_for_selector(".sch-table, .schedule-table, body", timeout=7000)
        await asyncio.sleep(1.5)
    except Exception:
        pass

    # Удаляем баннер cookie перед парсингом
    await remove_cookie_banners(page)

    all_trains = await extract_trains_data(page)
    result["all_trains"] = all_trains

    matched_trains = []
    available_trains = []

    for train in all_trains:
        # Проверка соответствия номерам поездов
        if req_trains:
            if not matches_any_train(req_trains, train.get("trainNumber", "")):
                continue

        matched_trains.append(train)

        # Проверка наличия свободных мест
        if not train.get("hasTickets"):
            continue

        # Фильтр по типам вагонов
        if req_cars:
            car_info = train.get("carInfo", "").lower()
            matched_car = any(t.lower() in car_info for t in req_cars)
            if not matched_car and "места доступны" not in car_info:
                continue

        available_trains.append(train)

    result["matched_trains"] = matched_trains
    result["available_trains"] = available_trains

    # Делаем скриншот, если билеты найдены
    if available_trains and config.SEND_SCREENSHOT:
        try:
            await remove_cookie_banners(page)
            result["screenshot_bytes"] = await page.screenshot(full_page=False)
        except Exception as e:
            print(f"[Скриншот] Ошибка захвата страницы: {e}")

    await page.close()
    return result


def format_combined_alert_message(found_routes: List[Dict[str, Any]]) -> str:
    """
    Формирует красивое и компактное сообщение, которое гарантированно
    помещается целиком под скриншотами (до 1024 символов) без обрезки.
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
            # Краткий список мест
            raw_cars = t.get("carInfoList", [])
            cars_str = ", ".join(raw_cars) if raw_cars else t.get("carInfo", "Есть места")

            lines.append(f"• <b>№{num}</b> ({dep} ➔ {arr}): {cars_str}")

        dir_label = "ТУДА" if "туда" in name.lower() or idx == 0 else "ОБРАТНО"
        lines.append(f'🔗 <a href="{url}"><b>Купить билет {dir_label}</b></a>\n')

    now_time = datetime.now().strftime("%H:%M:%S")
    lines.append(f"🕒 <i>Обновлено в {now_time}</i>")

    return "\n".join(lines)


def get_signature_for_route(item: Dict[str, Any]) -> str:
    """
    Генерирует уникальную сигнатуру свободных мест для маршрута.
    """
    trains = item.get("available_trains", [])
    if not trains:
        return ""
    route = item.get("route", {})
    return f"{route.get('from')}_{route.get('to')}_{route.get('date')}:" + "-".join(
        [f"{t.get('trainNumber')}_{t.get('carInfo')}" for t in trains]
    )


async def async_main():
    routes = config.ROUTES

    print("=" * 65)
    print("  АСИНХРОННЫЙ МОНИТОРИНГ БИЛЕТОВ БЕЛЖД (pass.rw.by)")
    print("=" * 65)
    print(f"Количество маршрутов: {len(routes)} (проверяются параллельно)")
    for i, r in enumerate(routes, 1):
        r_name = r.get("name") or f"{r.get('from')} -> {r.get('to')}"
        parsed_t = parse_train_filter(r.get('train'))
        train_filter = ", ".join([f"№{x}" for x in parsed_t]) if parsed_t else 'Любой'
        cars_filter = ', '.join(r.get('car_types')) if r.get('car_types') else 'Любые'
        print(f"  {i}. {r_name}")
        print(f"     Дата: {r.get('date')} | Поезда: {train_filter} | Вагоны: {cars_filter}")
    print(f"Интервал между проверками: {config.CHECK_INTERVAL} сек")
    print(f"Фоновый режим:            {'Да (Headless)' if config.HEADLESS else 'Нет (видимое окно)'}")
    print("=" * 65)

    if not config.BOT_TOKEN or "ВАШ_ТОКЕН" in config.BOT_TOKEN or config.BOT_TOKEN == "your_bot_token_here":
        print("[!] ОШИБКА: Заполните TELEGRAM_BOT_TOKEN в файле .env (или config.py)!")
        return

    if not config.CHAT_ID or "ВАШ_CHAT" in config.CHAT_ID or config.CHAT_ID == "your_chat_id_here":
        print("[!] ОШИБКА: Заполните TELEGRAM_CHAT_ID в файле .env (или config.py)!")
        return

    if not routes:
        print("[!] ОШИБКА: Список маршрутов ROUTES пуст (проверьте routes.json или config.py)!")
        return

    print("Запуск браузера Chromium...")
    print("Для остановки нажмите Ctrl + C\n")

    last_signatures: Dict[str, str] = {}
    check_cycle = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=config.HEADLESS,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox"
            ]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 900},
            locale="ru-RU"
        )

        try:
            while True:
                check_cycle += 1
                now_str = datetime.now().strftime("%H:%M:%S")
                print(f"[{now_str}] Цикл #{check_cycle} (параллельная проверка {len(routes)} направлений):", flush=True)

                tasks = [check_single_route_async(context, r) for r in routes]
                results = await asyncio.gather(*tasks, return_exceptions=False)

                routes_with_tickets = []
                has_new_tickets = False

                for res in results:
                    r = res["route"]
                    r_name = r.get("name") or f"{r.get('from')} ➔ {r.get('to')}"
                    r_key = f"{r.get('from')}_{r.get('to')}_{r.get('date')}_{str(r.get('train', ''))}"
                    req_trains = res.get("req_trains", [])

                    if res.get("error"):
                        print(f"   ❌ {r_name}: Ошибка - {res['error']}")
                        continue

                    all_trains = res.get("all_trains", [])
                    available = res.get("available_trains", [])

                    # Список номеров всех реальных поездов, найденных в расписании
                    actual_train_nums = [f"№{t.get('trainNumber')}" for t in all_trains if t.get('trainNumber')]

                    # Четкий и понятный отчет в терминале по каждому запрошенному поезду
                    if req_trains:
                        for req_num in req_trains:
                            # Ищем конкретный поезд
                            matched_specific = [
                                t for t in all_trains
                                if matches_any_train([req_num], t.get("trainNumber", ""))
                            ]
                            if not matched_specific:
                                sch_hint = f" (в расписании есть: {', '.join(actual_train_nums)})" if actual_train_nums else ""
                                print(f"   ⚪ {r_name}: Поезд №{req_num} НЕ НАЙДЕН на эту дату{sch_hint}")
                            else:
                                for t in matched_specific:
                                    t_num = t.get("trainNumber") or req_num
                                    times_str = f"{t.get('depTime')} ➔ {t.get('arrTime')}"
                                    if t in available:
                                        print(f"   🎉 {r_name}: Поезд №{t_num} ({times_str}) — НАЙДЕНЫ БИЛЕТЫ ({t.get('carInfo')})!")
                                    else:
                                        print(f"   ⚪ {r_name}: Поезд №{t_num} ({times_str}) — найден, но МЕСТ НЕТ")
                    else:
                        # Если номер поезда не задан (ищем любой)
                        if available:
                            t_nums = ", ".join([f"№{t.get('trainNumber')}" for t in available])
                            print(f"   🎉 {r_name}: НАЙДЕНЫ БИЛЕТЫ на поезда: {t_nums}")
                        else:
                            print(f"   ⚪ {r_name}: В расписании {len(all_trains)} поезд(а/ов), но свободных мест сейчас нет")

                    # Проверка для отправки уведомления
                    if available:
                        routes_with_tickets.append(res)
                        current_sig = get_signature_for_route(res)
                        if current_sig != last_signatures.get(r_key):
                            last_signatures[r_key] = current_sig
                            has_new_tickets = True
                    else:
                        last_signatures[r_key] = ""

                # Если появились новые билеты — отправляем ЕДИНОЕ сообщение С ПРИКРЕПЛЕННЫМИ СКРИНШОТАМИ
                if routes_with_tickets and has_new_tickets:
                    print(f"[{now_str}] 📲 Отправка сообщения со скриншотами в Telegram...")

                    # Звуковой сигнал
                    play_sound_alert()

                    # Единый текст сообщения
                    combined_message = format_combined_alert_message(routes_with_tickets)

                    # Сбор скриншотов
                    screenshots = [
                        item["screenshot_bytes"]
                        for item in routes_with_tickets
                        if item.get("screenshot_bytes")
                    ]

                    # Отправка: фото со встроенной подписью (в одном посте)
                    ok = send_combined_alert(config.BOT_TOKEN, config.CHAT_ID, combined_message, screenshots)
                    if ok:
                        print(f"[{now_str}] ✅ Сообщение с {len(screenshots)} прикрепленными скриншотами успешно отправлено в Telegram!")
                    else:
                        print(f"[{now_str}] ⚠️ Возникла проблема при отправке в Telegram.")
                elif routes_with_tickets and not has_new_tickets:
                    print(f"[{now_str}] [i] Билеты всё ещё доступны (уведомление уже было отправлено ранее).")

                print(f"--- Пауза {config.CHECK_INTERVAL} сек до следующей проверки ---\n")
                await asyncio.sleep(config.CHECK_INTERVAL)

        except (KeyboardInterrupt, asyncio.CancelledError):
            print("\n[Остановка] Мониторинг остановлен пользователем.")
        finally:
            try:
                await context.close()
            except Exception:
                pass
            try:
                await browser.close()
            except Exception:
                pass


def main():
    try:
        asyncio.run(async_main())
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    main()
