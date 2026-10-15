"""
Точка входа для запуска мониторинга билетов БелЖД (pass.rw.by).
Основная модульная логика находится в директории src/.
"""

import sys
from pathlib import Path

# Обеспечиваем корректный вывод UTF-8 в консоли Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from src.tracker import main

if __name__ == "__main__":
    main()
