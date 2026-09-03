"""دکمه‌های رنگی تلگرام — shim سازگاری.

مسئله
─────
Bot API 9.4 (۹ فوریه ۲۰۲۶) فیلد `style` را به `KeyboardButton` و
`InlineKeyboardButton` اضافه کرد و python-telegram-bot از نسخه‌ی 22.7 آن را
پاس می‌دهد. اگر همین رشته را به PTB قدیمی‌تر بدهیم، `TypeError` می‌گیریم و
*کل* کیبورد از کار می‌افتد — یعنی یک وابستگی عقب‌مانده روی سرور، ربات را
می‌خواباند، نه اینکه فقط رنگ را از دست بدهد.

راه‌حل
──────
یک بار امضای سازنده را بازرسی می‌کنیم و `style` را *فقط* وقتی پاس می‌دهیم که
پشتیبانی شود. نتیجه: روی PTB ≥ 22.7 دکمه رنگی است، روی 21.x همان دکمه‌ی
معمولی — بدون شرط پراکنده در سراسر bot.py.

نکته‌ی سمت کلاینت: رنگ فقط در نسخه‌های تلگرام بعد از ۹ فوریه ۲۰۲۶ دیده
می‌شود؛ کلاینت قدیمی‌تر دکمه را بی‌رنگ نشان می‌دهد و خطا نمی‌دهد. پس رنگ
هیچ‌وقت تنها حامل معنا نیست: متن هر دکمه خودش گویا است (الزام دسترس‌پذیری).
"""
from __future__ import annotations

import inspect
from typing import Optional

from telegram import InlineKeyboardButton, KeyboardButton

# مقدارهای مجاز API: primary (آبی)، success (سبز)، danger (سرخ).
# از enum خود PTB می‌خوانیم اگر موجود باشد تا اگر روزی مقادیر عوض شد،
# اینجا هم عوض شود؛ وگرنه همان رشته‌ی خام API.
try:  # pragma: no cover - بسته به نسخه‌ی نصب‌شده
    from telegram.constants import KeyboardButtonStyle as _Style

    PRIMARY = str(_Style.PRIMARY)
    SUCCESS = str(_Style.SUCCESS)
    DANGER = str(_Style.DANGER)
except Exception:  # pragma: no cover
    PRIMARY, SUCCESS, DANGER = "primary", "success", "danger"


def _accepts_style(cls: type) -> bool:
    try:
        return "style" in inspect.signature(cls.__init__).parameters
    except (TypeError, ValueError):  # pragma: no cover
        return False


KB_STYLED = _accepts_style(KeyboardButton)
IKB_STYLED = _accepts_style(InlineKeyboardButton)
STYLED = KB_STYLED and IKB_STYLED


def kb(text: str, style: Optional[str] = None, **kwargs) -> KeyboardButton:
    """دکمه‌ی کیبورد پایین صفحه، رنگی اگر نسخه اجازه دهد."""
    if style and KB_STYLED:
        kwargs["style"] = style
    return KeyboardButton(text, **kwargs)


def ikb(text: str, style: Optional[str] = None, **kwargs) -> InlineKeyboardButton:
    """دکمه‌ی inline، رنگی اگر نسخه اجازه دهد."""
    if style and IKB_STYLED:
        kwargs["style"] = style
    return InlineKeyboardButton(text, **kwargs)


def support_note() -> str:
    """یک خط برای لاگ راه‌اندازی — تا معلوم باشد رنگ فعال است یا نه."""
    if STYLED:
        return "🎨 دکمه‌های رنگی فعال (PTB style پشتیبانی می‌شود)"
    return (
        "🎨 دکمه‌های رنگی غیرفعال — python-telegram-bot ≥ 22.7 لازم است "
        "(دکمه‌ها بی‌رنگ کار می‌کنند)"
    )
