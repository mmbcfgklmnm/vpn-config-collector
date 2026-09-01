"""کمکی‌های قالب‌بندی پیام تلگرام.

مشکلی که این ماژول حل می‌کنه: پیام‌ها با ParseMode.MARKDOWN (نسخه‌ی legacy)
فرستاده می‌شدند و *اسم* کانفیگ — که از منابع عمومی scrape شده — مستقیم
داخل متن درج می‌شد. اسم‌های scrape‌شده مرتب `_`، `*`، `[` و بک‌تیک دارند و
تلگرام با خطای «Can't parse entities» جواب ۴۰۰ می‌ده، یعنی پیام هیچ‌وقت
فرستاده نمی‌شه. Markdown نسخه‌ی legacy مکانیزم escape رسمی نداره، پس
امن‌ترین کار حذف این کاراکترها از مقدارهای پویاست.
"""
from __future__ import annotations

# کاراکترهایی که در Markdown legacy معنی دارند و escape مطمئنی ندارند.
_MD_SPECIALS = "_*`["


def strip_md(text: object, limit: int = 0) -> str:
    """حذف کاراکترهای معنادار Markdown از یک مقدار پویا.

    برای متنی که *خارج* از code span درج می‌شه (اسم سرور، اسم کاربر).
    """
    cleaned = "".join(ch for ch in str(text) if ch not in _MD_SPECIALS)
    cleaned = cleaned.replace("\\", "")
    if limit and len(cleaned) > limit:
        cleaned = cleaned[: limit - 1].rstrip() + "…"
    return cleaned


def code(text: object) -> str:
    """درج یک مقدار داخل code span تک‌خطی.

    بک‌تیک حذف می‌شه چون code span رو نصفه می‌بنده و بقیه‌ی پیام رو خراب می‌کنه.
    """
    return f"`{str(text).replace('`', '')}`"


def truncate(text: str, limit: int) -> str:
    """کوتاه کردن پیام به سقف تلگرام بدون شکستن code span.

    اگه تعداد بک‌تیک‌ها فرد بشه یکی اضافه می‌کنیم تا span بسته بمونه.
    """
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rstrip()
    if cut.count("`") % 2:
        cut += "`"
    return cut + "…"
