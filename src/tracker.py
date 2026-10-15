"""
Главный управляющий модуль трекера: асинхронный цикл мониторинга, сверка изменений и запуск уведомлений.
"""

import asyncio
from datetime import datetime
from typing import Any, Dict

from playwright.async_api import async_playwright

from src import config
from src.notifier import format_combined_alert_message, send_combined_alert
from src.scraper import check_single_route_async
from src.utils import matches_any_train, parse_train_filter, play_sound_alert


def get_signature_for_route(item: Dict[str, Any]) -> str:
    """
    Генерирует уникальную сигнатуру свободных мест для маршрута,
    чтобы не отправлять повторные одинаковые уведомления.
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
        print("[!] ОШИБКА: Заполните TELEGRAM_BOT_TOKEN в файле .env!")
        return

    if not config.CHAT_ID or "ВАШ_CHAT" in config.CHAT_ID or config.CHAT_ID == "your_chat_id_here":
        print("[!] ОШИБКА: Заполните TELEGRAM_CHAT_ID в файле .env!")
        return

    if not routes:
        print("[!] ОШИБКА: Список маршрутов пуст (проверьте config/routes.json)!")
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
                    actual_train_nums = [f"№{t.get('trainNumber')}" for t in all_trains if t.get('trainNumber')]

                    if req_trains:
                        for req_num in req_trains:
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
                        if available:
                            t_nums = ", ".join([f"№{t.get('trainNumber')}" for t in available])
                            print(f"   🎉 {r_name}: НАЙДЕНЫ БИЛЕТЫ на поезда: {t_nums}")
                        else:
                            print(f"   ⚪ {r_name}: В расписании {len(all_trains)} поезд(а/ов), но свободных мест сейчас нет")

                    if available:
                        routes_with_tickets.append(res)
                        current_sig = get_signature_for_route(res)
                        if current_sig != last_signatures.get(r_key):
                            last_signatures[r_key] = current_sig
                            has_new_tickets = True
                    else:
                        last_signatures[r_key] = ""

                # Отправка единого уведомления при появлении новых билетов
                if routes_with_tickets and has_new_tickets:
                    print(f"[{now_str}] 📲 Отправка сообщения со скриншотами в Telegram...")
                    play_sound_alert()

                    combined_message = format_combined_alert_message(routes_with_tickets)
                    screenshots = [
                        item["screenshot_bytes"]
                        for item in routes_with_tickets
                        if item.get("screenshot_bytes")
                    ]

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
