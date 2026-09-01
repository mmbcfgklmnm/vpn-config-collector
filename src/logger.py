"""
لاگر پروژه.

چهار تغییر: colorlog اختیاری شد، فایل لاگ چرخشی شد، اگه فایل‌سیستم
read-only باشه (مثل بعضی محیط‌های deploy) به‌جای crash فقط کنسول می‌مونه،
و خروجی کنسول روی UTF-8 تنظیم میشه.
"""
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

LOG_DIR = os.getenv("LOG_DIR", "logs")
LOG_FILE = os.path.join(LOG_DIR, "collector.log")
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 2

_CONSOLE_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_FILE_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

# کنسول ویندوز پیش‌فرض cp1252 است و اولین پیام فارسی با
# UnicodeEncodeError کل اجرا را می‌کشد. رانر گیت‌هاب UTF-8 است ولی محلی نه.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError, ValueError):
        pass


def _console_handler() -> logging.Handler:
    handler = logging.StreamHandler()
    handler.setLevel(logging.INFO)
    try:
        import colorlog

        handler.setFormatter(
            colorlog.ColoredFormatter(
                f"%(log_color)s{_CONSOLE_FORMAT}%(reset)s",
                datefmt="%H:%M:%S",
                log_colors={
                    "DEBUG": "cyan",
                    "INFO": "green",
                    "WARNING": "yellow",
                    "ERROR": "red",
                    "CRITICAL": "bold_red",
                },
            )
        )
    except ImportError:
        handler.setFormatter(logging.Formatter(_CONSOLE_FORMAT, datefmt="%H:%M:%S"))
    return handler


def _file_handler() -> logging.Handler | None:
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        handler = RotatingFileHandler(
            LOG_FILE,
            maxBytes=MAX_BYTES,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
        )
    except OSError:
        return None  # فایل‌سیستم read-only — فقط کنسول
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(
        logging.Formatter(_FILE_FORMAT, datefmt="%Y-%m-%d %H:%M:%S")
    )
    return handler


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    logger.addHandler(_console_handler())
    file_handler = _file_handler()
    if file_handler:
        logger.addHandler(file_handler)
    return logger
