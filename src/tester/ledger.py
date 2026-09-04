"""دفترِ نوبتِ لایه ۷ — «کدام کانفیگ‌ها قبلاً از تونل آزموده شده‌اند؟»

شکایت کاربر: «چرا حس می‌کنم ربات دیگر دنبال کانفیگ نیست و همان‌های قبلی را
می‌فرستد؟ این بخش را خراب کردی.» مقصر جمع‌آوری نبود — صف‌بندی لایه ۷ بود.
ورودی آن لایه فقط با «تأییدشده‌ی ایران، بعد سریع‌ترین TLS» مرتب می‌شد و
بودجه‌ی زمانی دُمِ صف را می‌بُرید. یعنی هر نیم‌ساعت همان سریع‌ترین‌ها آزموده
می‌شدند و بقیه هیچ‌وقت به تونل نمی‌رسیدند: در اجرای ۱۷:۴۶، ۵۱۰۳ کانفیگِ
پاس‌کرده‌ی لایه ۶ آزموده نشدند. کشف کار می‌کرد، ولی به کانال نمی‌رسید.

این ماژول حافظه‌ی بین‌اجرایی است: برای هر کانفیگ یک هش، شماره‌ی اجرایی که در
آن آزموده شد، و نتیجه. فایلش کنار خروجی‌ها در configs/ می‌نشیند و
collect.yml همان پوشه را commit می‌کند، پس بین اجراهای گیت‌هاب می‌ماند بدون
هیچ سرویس یا volume ای.

سه قاعده:
  ۱. هر اجرا سهمِ *تضمینی* به «هرگز آزموده‌نشده‌ها» می‌دهد (HTTP_FRESH_SHARE).
  ۲. صف درهم‌بافته است، نه پشت‌سرهم: بودجه هرجا قطع شود همان نسبت رعایت شده.
     (پشت‌سرهم گذاشتن یعنی اگر بودجه در ۶۰٪ تمام شود، سهم تازه‌ها صفر است.)
  ۳. کانفیگی که آخرین قضاوتش «رد» بوده به پول ذخیره نمی‌رود. «تست نشد» ≠ «رد
     شد» دو طرف تیغ دارد: ردشده هم نباید «تست‌نشده» جا بزند.
"""
import hashlib
import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from src import vless
from src.config import (
    HTTP_FRESH_SHARE, HTTP_LOG_KEEP_RUNS, HTTP_LOG_MAX, HTTP_PROVEN_CORE,
    HTTP_RETRY_AFTER_RUNS, LAYER7_LOG_FILE,
)
from src.logger import get_logger

logger = get_logger("ledger")

# طول هش. vless.short_id چهار نویسه است چون برای *چشمِ آدم* ساخته شده؛ این‌جا
# برخورد یعنی کانفیگی که هرگز آزموده نشده «آزموده» حساب شود و بی‌صدا از صف
# بیفتد. با ۱۲ نویسه (۴۸ بیت) احتمال برخورد در ۲۰هزار رکورد ~۱۰⁻⁶ است.
KEY_LEN = 12


def key(config: str) -> str:
    """شناسه‌ی پایدارِ یک کانفیگ برای دفتر.

    از *بدنه‌ی* لینک گرفته می‌شود (بدون fragment)، پس برچسبِ تأخیر/کشور/سنجه
    عوضش نمی‌کند: همان کانفیگ در اجرای بعدی همان کلید را دارد.
    """
    base, _ = vless.split_fragment(config.strip())
    return hashlib.sha1(base.encode("utf-8", "ignore")).hexdigest()[:KEY_LEN]


@dataclass
class Ledger:
    """وضعیت دفتر در حافظه.

    run شماره‌ی *همین* اجراست (بزرگ‌ترین شماره‌ی ذخیره‌شده + ۱)، پس شمارنده
    جای دیگری نگه‌داری نمی‌شود و فایل خودش را ترمیم می‌کند. یک واحدِ run =
    یک اجرایی که واقعاً لایه ۷ را اجرا کرد؛ اجرای SKIP_XRAY دفتر را دست
    نمی‌زند، پس «۶ اجرا» همیشه یعنی ۶ آزمونِ واقعی.
    """

    run: int = 1
    entries: Dict[str, Tuple[int, bool]] = field(default_factory=dict)

    def verdict(self, config: str) -> Optional[bool]:
        """آخرین حکمِ لایه ۷: True قبول، False رد، None هرگز آزموده نشده."""
        entry = self.entries.get(key(config))
        return None if entry is None else entry[1]

    def cooling(self, config: str) -> bool:
        """رد شده و هنوز نوبتِ فرصتِ دوباره‌اش نرسیده."""
        entry = self.entries.get(key(config))
        if entry is None or entry[1]:
            return False
        return self.run - entry[0] < max(1, HTTP_RETRY_AFTER_RUNS)

    def record(self, tested: List[str], passed) -> Dict[str, int]:
        """نتیجه‌ی این اجرا را ثبت می‌کند → آمار کوتاهِ «چه چیز تازه‌ای شد».

        فقط کانفیگی ثبت می‌شود که واقعاً آزموده شد. بی‌نوبت‌مانده‌های بودجه
        نباید ثبت شوند، وگرنه اجرای بعدی آن‌ها را «آزموده» می‌بیند و همان
        حلقه‌ی تکرار از نو ساخته می‌شود.
        """
        passed_set = set(passed)
        counts = {"tested": 0, "first_time": 0, "new_passed": 0, "recovered": 0}
        for config in tested:
            identifier = key(config)
            before = self.entries.get(identifier)
            ok = config in passed_set
            counts["tested"] += 1
            if before is None:
                counts["first_time"] += 1
                if ok:
                    counts["new_passed"] += 1
            elif ok and not before[1]:
                counts["recovered"] += 1
            self.entries[identifier] = (self.run, ok)
        return counts


def load(path: str = LAYER7_LOG_FILE) -> Ledger:
    """دفتر را از دیسک می‌خواند. فایلِ نبود/خراب = دفترِ خالی، نه خطا.

    خالی بودن دفتر بی‌خطر است: همه‌ی کانفیگ‌ها «تازه» حساب می‌شوند و اجرا
    مثل نسخه‌ی قبلی رفتار می‌کند. پس یک فایلِ گم‌شده کیفیت را پایین نمی‌آورد،
    فقط یک اجرا حافظه ندارد.
    """
    entries: Dict[str, Tuple[int, bool]] = {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError:
        return Ledger()

    for line in lines:
        parts = line.split("#")[0].split()
        if len(parts) != 3:
            continue
        identifier, raw_run, raw_ok = parts
        if len(identifier) != KEY_LEN or raw_ok not in ("0", "1"):
            continue
        try:
            run = int(raw_run)
        except ValueError:
            continue
        if run > 0:
            entries[identifier] = (run, raw_ok == "1")

    latest = max((run for run, _ in entries.values()), default=0)
    return Ledger(run=latest + 1, entries=entries)


def _prune(entries: Dict[str, Tuple[int, bool]], run: int) -> Dict[str, Tuple[int, bool]]:
    """رکوردهای کهنه را می‌اندازد و سقف تعداد را نگه می‌دارد."""
    horizon = run - max(1, HTTP_LOG_KEEP_RUNS)
    kept = {k: v for k, v in entries.items() if v[0] > horizon}
    if len(kept) > max(1, HTTP_LOG_MAX):
        # قدیمی‌ترها اول می‌روند؛ ترتیبِ دومِ کلید فقط برای قطعی بودن است.
        ranked = sorted(kept.items(), key=lambda item: (item[1][0], item[0]))
        kept = dict(ranked[len(kept) - max(1, HTTP_LOG_MAX):])
    return kept


def save(log: Ledger, path: str = LAYER7_LOG_FILE) -> bool:
    """دفتر را برای اجرای بعدی می‌نویسد (مرتب‌شده تا diff هر اجرا کوچک بماند).

    دفترِ خالی نوشته نمی‌شود: بهتر است نسخه‌ی قبلی بماند تا اجرای بعدی حافظه
    داشته باشد — همان قاعده‌ی «هیچ‌وقت خروجی خالی ننویس» بقیه‌ی پروژه.
    """
    entries = _prune(log.entries, log.run)
    if not entries:
        return False
    passed = sum(1 for _, ok in entries.values() if ok)
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
        temporary = f"{path}.tmp"
        with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("# دفترِ نوبتِ لایه ۷ — خودکار (شرح: src/tester/ledger.py)\n")
            handle.write(f"# اجرا {log.run} | {stamp} | {passed}/{len(entries)} قبول\n")
            handle.write("# هش‌کانفیگ  اجرا  نتیجه(1=قبول)\n")
            for identifier, (run, ok) in sorted(entries.items()):
                handle.write(f"{identifier} {run} {1 if ok else 0}\n")
        os.replace(temporary, path)
        return True
    except OSError as exc:
        logger.warning(f"ذخیره‌ی دفتر لایه ۷ نشد: {exc}")
        return False


def _interleave(seen: List[str], fresh: List[str]) -> List[str]:
    """دو صف را با نسبتِ ثابت درهم می‌بافد.

    نسبت از خودِ طولِ دو صف درمی‌آید، پس هر پیشوندِ خروجی همان ترکیب را دارد:
    بودجه‌ی لایه ۷ هرجا قطع شود، سهم تازه‌ها از دست نرفته.
    """
    if not seen or not fresh:
        return list(seen) + list(fresh)
    share = len(fresh) / (len(seen) + len(fresh))
    out: List[str] = []
    taken = 0
    seen_index = fresh_index = 0
    while seen_index < len(seen) or fresh_index < len(fresh):
        want_fresh = fresh_index < len(fresh) and taken < share * (len(out) + 1)
        if want_fresh or seen_index >= len(seen):
            out.append(fresh[fresh_index])
            fresh_index += 1
            taken += 1
        else:
            out.append(seen[seen_index])
            seen_index += 1
    return out


@dataclass
class Plan:
    """تصمیمِ این اجرا برای لایه ۷."""

    queue: List[str] = field(default_factory=list)   # به ترتیبِ آزمودن
    spare: List[str] = field(default_factory=list)   # آزموده نشد → پول ذخیره
    fresh_queued: int = 0
    proven_queued: int = 0
    held: int = 0                                    # اجرای قبل ردشان کرد


def plan(
    ordered: List[str],
    log: Ledger,
    limit: int,
    share: float = HTTP_FRESH_SHARE,
    core: int = HTTP_PROVEN_CORE,
) -> Plan:
    """صفِ لایه ۷ را از فهرستِ مرتبِ ورودی می‌سازد.

    ordered باید همان ترتیبِ ارزشِ pipeline باشد (تأییدشده‌ی ایران، بعد
    سریع‌ترین TLS)؛ این تابع فقط *نوبت* را عوض می‌کند، نه معیارِ ارزش را: در
    هر رده همان ترتیب حفظ می‌شود.

    سه رده:
      • proven  — اجرای قبل قبولش کرد. دوباره آزموده می‌شود چون انتشار به
        تأییدِ همین اجرا نیاز دارد، و اولِ صف است تا کانال بی‌کانفیگ نماند.
      • fresh   — هرگز آزموده نشده. سهمِ تضمینی برای همین‌هاست.
      • retry   — قبلاً رد شد و دوره‌ی انتظارش تمام شده؛ آخرِ صف.
    """
    proven: List[str] = []
    fresh: List[str] = []
    retry: List[str] = []
    held: List[str] = []
    for config in ordered:
        state = log.verdict(config)
        if state is None:
            fresh.append(config)
        elif state:
            proven.append(config)
        elif log.cooling(config):
            held.append(config)
        else:
            retry.append(config)

    limit = max(0, limit)
    head = proven[:max(0, min(core, limit))]
    rest = proven[len(head):] + retry
    budget = max(0, limit - len(head))
    # سهم تضمینی اول کنار گذاشته می‌شود، بعد هر ردهْ خالیِ دیگری را پر می‌کند:
    # نه سهم تازه‌ها خورده می‌شود، نه بودجه بی‌مصرف می‌ماند.
    reserved = min(len(fresh), int(budget * max(0.0, min(1.0, share))))
    seen_take = rest[:budget - reserved]
    fresh_take = fresh[:budget - len(seen_take)]

    queued = set(head) | set(seen_take) | set(fresh_take)
    return Plan(
        queue=head + _interleave(seen_take, fresh_take),
        # ذخیره فقط از «هرگز آزموده‌نشده» و «قبولِ اجرای قبل» پر می‌شود؛
        # ردشده‌ها (held و بازمانده‌ی retry) هیچ‌کدام. ترتیبِ ورودی حفظ
        # می‌شود چون پول ذخیره هم سقف دارد و سریع‌ترین‌ها باید بالا باشند.
        spare=[
            c for c in ordered
            if c not in queued and log.verdict(c) is not False
        ],
        fresh_queued=len(fresh_take),
        proven_queued=len(head) + sum(1 for c in seen_take if log.verdict(c)),
        held=len(held) + sum(1 for c in retry if c not in queued),
    )
