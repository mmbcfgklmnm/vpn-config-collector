"""احیای کانفیگ‌های کلودفلری با IP تمیز — «لایه ۴b»

مسئله
─────
در کانفیگ «VLESS over WS از طریق کلودفلر»، آدرسِ داخل لینک فقط یک *درِ ورود*
به شبکه‌ی انی‌کست CF است. مسیریابی واقعی تا سرور اصلی با هدر Host (و در حالت
TLS با SNI) انجام می‌شود. نتیجه‌اش این است: وقتی آن IP از ایران فیلتر می‌شود،
سرور پشتش سالم است و فقط در ورودی بسته شده. کافی است آدرس را با یک IP تمیز CF
عوض کنیم و Host/SNI را *دست نزنیم* — همان کانفیگ دوباره کار می‌کند.

چرا این‌جا و نه در لایه ۷
────────────────────────
این کار فقط روی endpoint هایی انجام می‌شود که لایه ۴ (check-host از نودهای
ایرانی) حکم «از ایران بسته است» داده باشد. روی کانفیگ سالم دست نمی‌زنیم:
عوض کردن آدرسِ چیزی که کار می‌کند فقط ریسک است.

چرا اسکن از رانر «تمیزی» را ثابت نمی‌کند
────────────────────────────────────────
رانر گیت‌هاب در آمریکاست و از آن‌جا تقریباً همه‌ی IP های CF زنده‌اند. پس اسکن
TCP این‌جا فقط IP های *مرده* را دور می‌ریزد. داوریِ واقعیِ «از ایران باز است»
با check-host انجام می‌شود؛ به همین دلیل find_clean_ips یک تابع verify
اختیاری می‌گیرد و خودش به checkhost وابسته نیست (هم قابل تست می‌ماند، هم
سهمیه‌ی API در یک جا مدیریت می‌شود).

IP های تأییدشده در CF_CLEAN_IP_FILE کش می‌شوند تا اجرای بعدی از صفر شروع
نکند: هر IP تازه یک درخواست check-host است و سهمیه محدود است.
"""
from __future__ import annotations

import asyncio
import ipaddress
import os
import random
import sys
import time
from typing import Awaitable, Callable, Dict, List, Optional, Tuple
from urllib.parse import quote, urlparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src import cdn, vless
from src.config import (
    CF_CLEAN_IP_FILE, CF_CLEAN_IP_WANT, CF_REVIVE_MAX, CF_SCAN_CANDIDATES,
    CF_SCAN_CONCURRENCY, CF_SCAN_TIMEOUT_SEC,
)
from src.logger import get_logger

logger = get_logger("clean_ip")

# فقط IPv4: پشتیبانی IPv6 در شبکه‌های خانگی ایران پراکنده است و کانفیگی که
# روی v6 احیا شود برای بیشتر کاربران بی‌فایده است.
_CF_NETS = [ipaddress.ip_network(net) for net in cdn.CLOUDFLARE_V4]

# پورت مرجع اسکن. IP های انی‌کست CF روی همه‌ی پورت‌های مجموعه‌ی CF گوش
# می‌دهند، پس «تمیزیِ» یک IP با ۴۴۳ سنجیده می‌شود و پورت خودِ کانفیگ
# دست‌نخورده می‌ماند.
SCAN_PORT = 443

# ترنسپورت‌هایی که از CDN رد می‌شوند. tcp خالی از CF رد نمی‌شود.
CDN_TRANSPORTS = frozenset({"ws", "httpupgrade", "xhttp", "grpc"})

# نشانه‌ی احیا در اسم کانفیگ. سه کاراکتر است، پس get_country (که دقیقاً دو
# حرف بزرگ می‌خواهد) آن را کد کشور نمی‌خواند.
REVIVE_MARK = "♻CF"


# ─── فهرست محلی ───────────────────────────────────────────

def load_local(path: str = CF_CLEAN_IP_FILE) -> List[str]:
    """IP های ذخیره‌شده. خطِ خالی و # نادیده گرفته می‌شود.

    هر خط می‌تواند «IP» یا «IP # توضیح» باشد. هر چیزی که IP کلودفلر نباشد
    دور ریخته می‌شود: فایل دستی ویرایش می‌شود و یک اشتباه تایپی نباید باعث
    شود کانفیگ سالم به آدرسی بی‌ربط منتقل شود.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return []
    ips: List[str] = []
    for line in lines:
        candidate = line.split("#")[0].strip()
        if candidate and cdn.is_cloudflare_ip(candidate) and candidate not in ips:
            ips.append(candidate)
    return ips


def save_local(ips: List[str], path: str = CF_CLEAN_IP_FILE) -> bool:
    """ذخیره‌ی IP های تأییدشده برای اجرای بعدی.

    فایل خالی نوشته نمی‌شود: بهتر است فهرست قبلی (حتی کهنه) بماند تا اجرای
    بعدی دستِ خالی شروع نکند — همان قاعده‌ی «هیچ‌وقت خروجی خالی ننویس».
    """
    if not ips:
        return False
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("# IP های تمیز کلودفلر — خودکار ساخته می‌شود\n")
            fh.write(f"# آخرین به‌روزرسانی: {stamp}\n")
            for ip in ips:
                fh.write(f"{ip}\n")
        return True
    except OSError as exc:
        logger.warning(f"ذخیره‌ی فهرست IP تمیز نشد: {exc}")
        return False


# ─── نمونه‌گیری و اسکن ─────────────────────────────────────

def sample_candidates(count: int, exclude: Optional[List[str]] = None) -> List[str]:
    """count تا IP تصادفی از محدوده‌های اعلام‌شده‌ی کلودفلر.

    وزنِ هر محدوده اندازه‌ی خودش است: بیشتر IP های لبه‌ی CF در /12 و /13 های
    بزرگ‌اند و انتخابِ یکنواخت از *فهرست محدوده‌ها* عملاً وزن را به رنج‌های
    کوچک می‌داد.
    """
    seen = set(exclude or [])
    weights = [net.num_addresses for net in _CF_NETS]
    out: List[str] = []
    # سقف تلاش، تا با برخورد تصادفی حلقه بی‌پایان نشود.
    for _ in range(max(1, count) * 8):
        if len(out) >= count:
            break
        net = random.choices(_CF_NETS, weights=weights, k=1)[0]
        # آدرس شبکه و broadcast میزبان نیستند.
        offset = random.randint(1, max(1, net.num_addresses - 2))
        ip = str(net.network_address + offset)
        if ip not in seen:
            seen.add(ip)
            out.append(ip)
    return out


async def tcp_alive(
    ip: str, port: int = SCAN_PORT, timeout: float = CF_SCAN_TIMEOUT_SEC
) -> Tuple[bool, float]:
    """یک اتصال TCP ساده → (موفق بود؟, تأخیر ms)."""
    start = time.monotonic()
    writer = None
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(ip, port), timeout=timeout
        )
        return True, round((time.monotonic() - start) * 1000, 1)
    except Exception:
        return False, 0.0
    finally:
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass


async def scan(
    candidates: List[str], want: int = 0, port: int = SCAN_PORT
) -> List[str]:
    """اسکن موازی؛ خروجی: زنده‌ها از سریع‌ترین به کندترین."""
    if not candidates:
        return []
    semaphore = asyncio.Semaphore(max(1, CF_SCAN_CONCURRENCY))

    async def bounded(ip: str) -> Tuple[str, bool, float]:
        async with semaphore:
            ok, ms = await tcp_alive(ip, port)
            return ip, ok, ms

    results = await asyncio.gather(
        *[bounded(ip) for ip in candidates], return_exceptions=True
    )
    alive: List[Tuple[float, str]] = []
    for item in results:
        if isinstance(item, BaseException):
            continue
        ip, ok, ms = item
        if ok:
            alive.append((ms, ip))
    alive.sort()
    ips = [ip for _, ip in alive]
    return ips[:want] if want else ips


# نوع تابع داوری: فهرست IP → {IP: تأخیر از ایران}. ۰ = بسته یا نامعلوم.
# دسته‌ای است نه یکی‌یکی، تا داور بتواند یک نشست و یک gate برای همه به کار
# ببرد؛ هر درخواست check-host چند ثانیه طول می‌کشد و سهمیه‌بر است.
Verifier = Callable[[List[str]], Awaitable[Dict[str, float]]]


async def find_clean_ips(
    want: int = CF_CLEAN_IP_WANT,
    verify: Optional[Verifier] = None,
    path: str = CF_CLEAN_IP_FILE,
) -> List[str]:
    """فهرست IP تمیز: اول کشِ محلی، بعد اسکن، در پایان داوری از ایران.

    ترتیب مهم است. کشِ محلی قبلاً از ایران تأیید شده، پس اگر همان‌ها هنوز
    زنده باشند نه اسکن لازم است نه سهمیه‌ی check-host.
    """
    cached = load_local(path)
    verified = await scan(cached) if cached else []
    if len(verified) >= want:
        logger.info(f"♻️ {len(verified)} IP تمیز از کش محلی")
        return verified[:want]

    fresh = await scan(sample_candidates(CF_SCAN_CANDIDATES, exclude=verified))
    logger.info(
        f"🔎 اسکن CF: {len(fresh)} زنده از {CF_SCAN_CANDIDATES} نامزد "
        f"(+{len(verified)} از کش)"
    )
    if verify is None:
        # بدون داوری از ایران فقط «زنده» را می‌دانیم، نه «تمیز». کش هم
        # نمی‌کنیم تا فایل ذخیره‌شده فقط IP های واقعاً تأییدشده را نگه دارد.
        return (verified + fresh)[:want]

    # داوری از ایران گران است (هر IP یک درخواست check-host)، ولی همین چند
    # درخواست جلوی هدر رفتن ده‌ها درخواست روی کانفیگ‌های احیاشده‌ی بی‌فایده
    # را می‌گیرد: اگر IP بسته باشد، هر کانفیگی که رویش سوار شود هم بسته است.
    budget = fresh[: max(1, want * 2)]
    try:
        judged = await verify(budget)
    except Exception as exc:
        logger.warning(f"داوری IP تمیز از ایران نشد: {type(exc).__name__}")
        judged = {}
    picked = list(verified) + [ip for ip in budget if judged.get(ip, 0.0) > 0]
    if picked:
        save_local(picked, path)
    logger.info(
        f"✅ {len(picked)} IP تمیز ({len(budget)} نامزد از ایران داوری شد)"
    )
    return picked[:want]


def remember(ips: List[str], path: str = CF_CLEAN_IP_FILE) -> None:
    """IP هایی که واقعاً کانفیگی را احیا کردند، بالای فهرست کش می‌نشینند."""
    proven = [ip for ip in ips if cdn.is_cloudflare_ip(ip)]
    if not proven:
        return
    merged = proven + [ip for ip in load_local(path) if ip not in proven]
    save_local(merged[: max(CF_CLEAN_IP_WANT * 4, 12)], path)


# ─── احیای کانفیگ ─────────────────────────────────────────

def routing_domain(config: str) -> str:
    """دامنه‌ای که CF با آن مسیر را پیدا می‌کند: host → sni → خودِ آدرس.

    اگر هیچ دامنه‌ای نبود (آدرس IP و بدون host/sni)، احیا ممکن نیست: با عوض
    کردن آدرس هیچ نشانی نمی‌ماند که CF بسته را به کدام origin بفرستد.
    """
    info = vless.parse(config)
    if info is None:
        return ""
    for candidate in (info.params.get("host"), info.params.get("sni"), info.host):
        candidate = (candidate or "").strip()
        if candidate and "." in candidate and not cdn.is_ip(candidate):
            return candidate
    return ""


def can_revive(config: str) -> bool:
    """آیا این کانفیگ نامزد جایگزینی IP است؟

    سه شرط: ترنسپورت از CDN رد شود، پورت از مجموعه‌ی پورت‌های CF باشد
    (پورت غیرعادی یعنی احتمالاً سرور مستقیم است نه لبه‌ی CF)، و یک دامنه‌ی
    مسیریابی وجود داشته باشد.
    """
    info = vless.parse(config)
    if info is None or not info.uuid or not info.host:
        return False
    net = {"raw": "tcp", "http": "h2"}.get(info.network, info.network)
    if net not in CDN_TRANSPORTS:
        return False
    if (info.port or 443) not in cdn.CF_PORTS:
        return False
    return bool(routing_domain(config))


def _set_query_param(query: str, key: str, value: str) -> str:
    """جای‌گذاری یک کلید در کوئریِ خام، با حفظ عینِ بقیه‌ی کلیدها.

    کل کوئری بازنویسی نمی‌شود: بعضی کلاینت‌ها کلیدها را با همان حروف
    بزرگ/کوچک می‌خوانند (مثل serviceName) و رمزگذاری دوباره‌ی همه‌چیز
    می‌تواند کانفیگی را که در کلاینت کاربر کار می‌کرد خراب کند.
    """
    encoded = quote(value, safe="")
    parts: List[str] = []
    replaced = False
    for part in query.split("&"):
        if not part:
            continue
        name = part.partition("=")[0]
        if name.strip().lower() == key:
            if replaced:
                continue          # کلید تکراری در لینک اصلی
            parts.append(f"{name}={encoded}")
            replaced = True
        else:
            parts.append(part)
    if not replaced:
        parts.append(f"{key}={encoded}")
    return "&".join(parts)


def revive(config: str, ip: str) -> str:
    """آدرس را با IP تمیز عوض می‌کند و مسیر CDN را دست‌نخورده نگه می‌دارد.

    Host و SNI *صریح* نوشته می‌شوند، حتی اگر در لینک اصلی نبودند: کلاینت در
    نبودشان هدر Host را از آدرس برمی‌دارد و آدرس بعد از جایگزینی یک IP است.
    برچسب قبلی (کشور/تأخیر) هم دور ریخته می‌شود چون برای ورودی تازه بی‌اعتبار
    است — کانفیگ احیاشده باید از اول سنجیده شود.
    """
    info = vless.parse(config)
    domain = routing_domain(config)
    if info is None or not domain or not cdn.is_cloudflare_ip(ip):
        return ""
    base, fragment = vless.split_fragment(config)
    query = _set_query_param(urlparse(base).query, "host", domain)
    if info.security in ("tls", "xtls", "reality"):
        query = _set_query_param(query, "sni", domain)
    name = fragment.split("|")[0].strip() or vless.short_id(config)
    if REVIVE_MARK not in name:
        name = f"{name}{REVIVE_MARK}"
    return f"vless://{info.uuid}@{ip}:{info.port or 443}?{query}#{name}"


def revive_batch(
    configs: List[str], ips: List[str], limit: int = CF_REVIVE_MAX
) -> List[str]:
    """احیای گروهی؛ IP ها به‌نوبت (round-robin) پخش می‌شوند.

    چرا نوبتی و نه همه روی بهترین IP: بار روی یک IP آن را زودتر می‌سوزاند و
    با فیلتر شدنش کل خروجی این مرحله یک‌جا از بین می‌رود.
    """
    if not ips:
        return []
    out: List[str] = []
    seen = set()
    for index, config in enumerate(c for c in configs if can_revive(c)):
        if len(out) >= limit:
            break
        revived = revive(config, ips[index % len(ips)])
        if revived and revived not in seen:
            seen.add(revived)
            out.append(revived)
    return out


def ip_of(config: str) -> str:
    """آدرسِ کانفیگ اگر IP باشد — برای به‌خاطر سپردن IP هایی که جواب دادند."""
    info = vless.parse(config)
    host = (info.host if info else "") or ""
    return host if cdn.is_ip(host) else ""
