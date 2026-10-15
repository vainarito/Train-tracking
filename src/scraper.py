"""
Модуль скрапера pass.rw.by на Playwright: извлечение расписания, проверка свободных мест и скриншоты.
"""

import asyncio
from typing import Any, Dict, List
from playwright.async_api import BrowserContext, Page, TimeoutError as PlaywrightTimeoutError

from src import config
from src.utils import (
    get_search_url,
    matches_any_train,
    parse_train_filter,
    remove_cookie_banners
)


async def extract_trains_data(page: Page) -> List[Dict[str, Any]]:
    """
    Извлекает информацию обо всех поездах и свободных местах со страницы pass.rw.by.
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

            // Пропускаем строки заголовков
            if (!trainNumber) {
                continue;
            }

            // 2. Время отправления и прибытия
            const times = text.match(/(\\b\\d{1,2}:\\d{2}\\b)/g) || [];
            const depTime = times.length > 0 ? times[0] : '';
            const arrTime = times.length > 1 ? times[1] : '';

            // Дедупликация
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
    Асинхронно открывает страницу направления, парсит поезда,
    фильтрует по номеру и типу вагона и сохраняет скриншот при наличии билетов.
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
            result["error"] = "Таймаут соединения (проверьте, отключен ли VPN)"
        else:
            result["error"] = str(e)
        await page.close()
        return result

    try:
        await page.wait_for_selector(".sch-table, .schedule-table, body", timeout=7000)
        await asyncio.sleep(1.5)
    except Exception:
        pass

    # Удаляем баннеры cookie
    await remove_cookie_banners(page)

    all_trains = await extract_trains_data(page)
    result["all_trains"] = all_trains

    matched_trains = []
    available_trains = []

    for train in all_trains:
        # Проверка соответствия поезду
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

    # Захват скриншота при наличии билетов
    if available_trains and config.SEND_SCREENSHOT:
        try:
            await remove_cookie_banners(page)
            result["screenshot_bytes"] = await page.screenshot(full_page=False)
        except Exception as e:
            print(f"[Скриншот] Ошибка захвата страницы: {e}")

    await page.close()
    return result
