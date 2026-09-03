"""
لایه ۷: تأخیر واقعی + پایداری + سرعت، از داخل تونل xray

چه چیزی این‌جا اندازه‌گیری می‌شود
────────────────────────────────
پینگ TCP (لایه ۳) فقط می‌گوید «چیزی روی این پورت جواب می‌دهد». این‌جا یک
درخواست واقعی از *داخل* تونل VLESS رد می‌شود و سه چیز به دست می‌آید:

  • تأخیر واقعی  — زمان رفت‌وبرگشتِ یک پاسخ ۲۰۴/۲۰۰. سقفش REAL_DELAY_MAX_MS
    است (پیش‌فرض ۳۰۰۰ms). دیرتر از آن = رد.
  • افت بسته و لرزش — PROBE_COUNT درخواست با فاصله‌ی PROBE_GAP_SEC ثانیه.
    یک probe نه افت می‌دهد نه لرزش. نودی با تأخیر ۱۰۰ms و ۰٪ افت از نودی
    با ۵۰ms و ۲۰٪ افت بهتر است، پس افت جدا از تأخیر سنجیده و برچسب می‌شود.
  • سرعت دانلود — یک فایل کوچک (SPEED_TEST_BYTES) از همان تونل. کانفیگی که
    وصل می‌شود ولی ۱۰ KB/s می‌دهد عملاً بی‌فایده است.

همه‌ی این‌ها روی *یک* پروسه‌ی xray به‌ازای هر کانفیگ انجام می‌شود: بالا آوردن
xray گران‌ترین بخش کار است، پس probe ها و بنچمارک سرعت همان inbound را
به‌اشتراک می‌گذارند.

سه اشکال نسخه‌ی قبلی
────────────────────
  ۱. پورت SOCKS با random.randint انتخاب می‌شد؛ با ۸ تست همزمان و ۱۰هزار
     پورت، برخورد اتفاق می‌افتاد و کانفیگ سالم به‌خاطر پورت اشغال fail می‌شد
     (یا بدتر، ترافیک از تونل کانفیگ دیگری رد می‌شد).
  ۲. بعد از اجرای xray فقط ۱.۵ ثانیه sleep می‌شد و stderr به DEVNULL می‌رفت،
     پس «xray بالا نیامد» از «سرور جواب نداد» قابل تشخیص نبود.
  ۳. پارامترها percent-decode نمی‌شدند، پس path=%2Fws به‌صورت literal به xray
     می‌رسید و همه‌ی کانفیگ‌های WebSocket این‌جا رد می‌شدند.

چرا چند دور (الگو از 0xRadikal/Free-v2ray-Configs)
──────────────────────────────────────────────────
یک تست موفق تضمین نمی‌کند کانفیگ ۵ دقیقه بعد هم کار کند. در اندازه‌گیری آن
پروژه ~۳۰٪ کانفیگ‌هایی که یک بار جواب دادند در دور بعد fail شدند. این‌جا
کانفیگ باید *همه‌ی* HTTP_TEST_ROUNDS دور را پاس کند و بین دورها
HTTP_ROUND_GAP_SEC ثانیه فاصله است تا سه دور یک لحظه‌ی شبکه را نسنجند.
تأخیر گزارش‌شده میانه‌ی دورهاست، نه بهترین دور — بهترین دور خوش‌بینانه است.
"""
import asyncio
import json
import os
import socket
import statistics
import tempfile
import time
from dataclasses import dataclass
from typing import List, Optional, Set, Tuple

import aiohttp
from aiohttp_socks import ProxyConnector

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from src import vless
from src.config import (
    HTTP_ROUND_GAP_SEC, HTTP_TEST_ROUNDS, MAX_CONCURRENT_XRAY, MAX_JITTER_MS,
    MAX_PACKET_LOSS_PCT, PROBE_COUNT, PROBE_GAP_SEC, REAL_DELAY_MAX_MS,
    SPEED_MIN_KBPS, SPEED_RESCUE_MIN, SPEED_TEST_BYTES, SPEED_TEST_ENABLED,
    SPEED_TEST_TIMEOUT_SEC, SPEED_TEST_URL, XRAY_PATH, XRAY_TIMEOUT_SEC,
)
from src.logger import get_logger

logger = get_logger("http_tester")

# چند URL تست — اگه یکی جواب داد کافیه. هم HTTP و هم HTTPS داریم: بعضی
# میدل‌باکس‌ها پاسخ ۲۰۴ ساده را جعل می‌کنند، پس یک مسیر TLS-دار هم لازم است
# تا «۲۰۴ گرفتم» با «تونل واقعاً کار می‌کند» یکی باشد.
TEST_URLS = (
    "http://cp.cloudflare.com/generate_204",
    "https://www.google.com/generate_204",
    "http://www.gstatic.com/generate_204",
    "http://connectivitycheck.gstatic.com/generate_204",
)

# خواسته‌ی صریح: ۲۰۰ یا ۲۰۴ هر دو قبول. نسخه‌ی قبلی برای هر URL یک status
# دقیق می‌خواست، پس ۲۰۰ (که بعضی endpoint ها می‌دهند) fail شمرده می‌شد.
OK_STATUS = frozenset({200, 204})

# سقف انتظار برای بالا آمدن inbound محلی xray
XRAY_START_TIMEOUT = 6.0

# ─── پاک‌سازی پارامترهای اسکرپ‌شده ──────────────────────────
# پارامترهای لینک از کانال تلگرام و مخزن گیت‌هاب می‌آیند و *داده‌ی نامعتمد*
# هستند: هر رشته‌ای ممکن است داخل fp/alpn/flow باشد. اگر همان را دست‌نخورده
# به xray بدهیم، پروسه با «config load error» بالا نمی‌آید و کانفیگی که
# سرورش سالم است به‌خاطر یک پارامتر بی‌ربط رد می‌شود.
KNOWN_FINGERPRINTS = frozenset({
    "chrome", "firefox", "safari", "ios", "android", "edge", "360", "qq",
    "random", "randomized", "randomizednoalpn",
})
KNOWN_ALPN = frozenset({"h2", "http/1.1", "h3"})
# فقط flow های نسل vision در xray امروز پذیرفته می‌شوند. مقدارهای قدیمی
# (xtls-rprx-origin / -direct / -splice) *حذف* شده‌اند و xray با دیدنشان
# کانفیگ را بار نمی‌کند — یکی از دلیل‌های «xray بالا نیامد».
KNOWN_FLOWS = frozenset({"xtls-rprx-vision", "xtls-rprx-vision-udp443"})


def clean_error(log_text: str) -> str:
    """پیام ریشه‌ای خطای xray را از لاگ بیرون می‌کشد.

    xray خطاها را تودرتو و با `>` جدا می‌کند:
    `main: failed to load config > infra/conf: ... > ریشه‌ی واقعی`.
    نسخه‌ی قبلی ۸۰ کاراکتر *آخر* لاگ را برمی‌داشت، پس همیشه دنباله‌ی جمله‌ی
    راهنما («Please update your config(s)...») گزارش می‌شد و کلیدِ مقصر
    هیچ‌وقت دیده نمی‌شد. این‌جا آخرین قطعه‌ی معنادار (= ریشه) برداشته می‌شود.
    """
    lines = [ln.strip() for ln in log_text.splitlines() if ln.strip()]
    if not lines:
        return ""
    # خط اول لاگ همان خط خطاست؛ خطوط بعدی معمولاً usage/راهنما هستند.
    line = next((ln for ln in lines if ">" in ln or "ailed" in ln), lines[0])
    # مهر زمان و سطح لاگ به تشخیص کمکی نمی‌کند.
    for marker in ("] ", "Error: ", "error: "):
        if marker in line:
            line = line.split(marker, 1)[1]
    parts = [p.strip() for p in line.split(">") if p.strip()]
    root = parts[-1] if parts else line
    # جمله‌ی عمومیِ «کانفیگ را به‌روز کن» اطلاعاتی ندارد؛ خودِ مقصر مهم است.
    root = root.split(". Please update")[0].strip()
    return root[:120]


_used_ports: Set[int] = set()
_port_lock = asyncio.Lock()


async def reserve_port() -> int:
    """گرفتن یک پورت آزاد و رزرو کردنش تا پایان تست.

    بایند به پورت ۰ اجازه می‌ده کرنل پورت آزاد رو انتخاب کنه؛ ست هم جلوی
    استفاده‌ی دوباره در همین اجرا رو می‌گیره.
    """
    async with _port_lock:
        for _ in range(50):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                try:
                    sock.bind(("127.0.0.1", 0))
                except OSError:
                    continue
                port = sock.getsockname()[1]
            if port not in _used_ports:
                _used_ports.add(port)
                return port
    raise RuntimeError("پورت آزاد پیدا نشد")


async def release_port(port: int) -> None:
    async with _port_lock:
        _used_ports.discard(port)


@dataclass
class Probe:
    """نتیجه‌ی کامل سنجش یک کانفیگ در یک دور.

    چرا یک dataclass و نه tuple بلند: پنج عدد و دو پرچم داریم و ترتیبشان
    در tuple به‌سادگی جابه‌جا می‌شود؛ این‌جا هر مقدار اسم دارد.
    """

    ok: bool = False
    delay_ms: float = 0.0          # میانه‌ی probe های موفق
    jitter_ms: float = -1.0        # ‏-1 = قابل محاسبه نبود (کمتر از ۲ probe)
    loss_pct: float = -1.0         # ‏-1 = اندازه‌گیری نشد
    speed_kbps: float = 0.0        # ‏۰ = اندازه‌گیری نشد
    reason: str = ""
    # کانفیگ همه‌ی probe ها را پاس کرد و *فقط* روی گیت سرعت افتاد. این‌ها
    # نامزدهای «نجات» هستند: اگر گیت سرعت همه را رد کند، تقصیر شبکه‌ی
    # رانر است نه کانفیگ‌ها. → قاعده‌ی «تست نشد ≠ رد شد».
    speed_only_fail: bool = False


def _jitter(samples: List[float]) -> float:
    """لرزش = میانگین قدرمطلقِ اختلاف probe های پیاپی.

    انحراف معیار هم عدد می‌دهد ولی ترتیب را نادیده می‌گیرد: [50,150,50,150]
    و [50,50,150,150] انحراف معیار یکسان دارند، در حالی که تجربه‌ی کاربر در
    اولی بدتر است. معیار IPD (اختلاف پیاپی) همان چیزی است که RFC 3550 برای
    jitter به کار می‌برد.
    """
    if len(samples) < 2:
        return -1.0
    diffs = [abs(b - a) for a, b in zip(samples, samples[1:])]
    return round(sum(diffs) / len(diffs), 1)


def vless_to_xray(config: str, socks_port: int) -> Optional[dict]:
    """لینک VLESS → کانفیگ xray با یک inbound محلی SOCKS5."""
    info = vless.parse(config)
    if info is None or not info.host or not info.uuid:
        return None

    p = info.params
    net = info.network
    # fp نامعتمد است: هر رشته‌ای در لینک‌های اسکرپ‌شده دیده می‌شود. مقدار
    # ناشناخته باعث می‌شود xray کانفیگ را بار نکند، پس به chrome برمی‌گردیم.
    fp = p.get("fp", "chrome").strip().lower()
    if fp not in KNOWN_FINGERPRINTS:
        fp = "chrome"
    path = p.get("path", "/") or "/"
    if not path.startswith("/"):
        path = "/" + path
    host_header = p.get("host") or info.host
    # flow فقط با لایه‌ی رمز معنا دارد؛ روی security=none خطای بارگذاری می‌دهد.
    flow = p.get("flow", "").strip().lower()
    if flow not in KNOWN_FLOWS or info.security not in ("tls", "xtls", "reality"):
        flow = ""

    # xray نام "http" را نمی‌شناسد؛ معادلش "h2" است. "raw" نام جدید "tcp".
    net = {"http": "h2", "raw": "tcp"}.get(net, net)

    stream: dict = {"network": net}

    if info.security == "reality":
        stream["security"] = "reality"
        stream["realitySettings"] = {
            "serverName": info.sni,
            "fingerprint": fp,
            "publicKey": p.get("pbk", ""),
            "shortId": p.get("sid", ""),
            "spiderX": p.get("spx", ""),
        }
    elif info.security in ("tls", "xtls"):
        stream["security"] = "tls"
        stream["tlsSettings"] = {
            "serverName": info.sni,
            "fingerprint": fp,
            # همان رفتار کلاینت واقعی: فقط اگه لینک صریحاً خواسته باشه.
            "allowInsecure": p.get("allowinsecure", "").lower() in ("1", "true"),
        }
        if p.get("alpn"):
            # فقط ALPN های شناخته‌شده؛ مقدار دلخواه در لینک باعث رد شدن
            # کانفیگ توسط xray می‌شود نه مذاکره‌ی متفاوت TLS.
            alpn = [
                a.strip().lower() for a in p["alpn"].split(",")
                if a.strip().lower() in KNOWN_ALPN
            ]
            if alpn:
                stream["tlsSettings"]["alpn"] = alpn
    else:
        # CDN-plain: بدون لایه‌ی رمز. صریح می‌نویسیم تا از پیش‌فرض‌های
        # نسخه‌های مختلف xray مستقل باشد.
        stream["security"] = "none"

    if net == "ws":
        stream["wsSettings"] = {"path": path, "headers": {"Host": host_header}}
    elif net == "httpupgrade":
        # transport رایج در کانفیگ‌های CDN جدید؛ قبلاً پشتیبانی نمی‌شد و
        # همه‌ی این کانفیگ‌ها با «تبدیل ناموفق» یا خطای xray رد می‌شدند.
        stream["httpupgradeSettings"] = {"path": path, "host": host_header}
    elif net == "xhttp":
        stream["xhttpSettings"] = {"path": path, "host": host_header}
        if p.get("mode"):
            stream["xhttpSettings"]["mode"] = p["mode"]
    elif net == "grpc":
        stream["grpcSettings"] = {"serviceName": p.get("servicename", "")}
    elif net == "h2":
        stream["httpSettings"] = {"host": [host_header], "path": path}

    # VLESS کلاسیک «encryption=none» است. نسل تازه (mlkem768...) رمز واقعی
    # دارد و اگر لینک آن را خواسته باشد باید عیناً منتقل شود، وگرنه سرور
    # دست‌دادن را رد می‌کند. مقدارهای کپی‌شده از VMess (مثل auto) نامعتبرند.
    enc = p.get("encryption", "none").strip() or "none"
    if enc != "none" and not enc.startswith("mlkem768"):
        enc = "none"

    return {
        "log": {"loglevel": "warning"},
        "inbounds": [{
            "listen": "127.0.0.1",
            "port": socks_port,
            "protocol": "socks",
            "settings": {"auth": "noauth", "udp": False},
        }],
        "outbounds": [{
            "protocol": "vless",
            "settings": {"vnext": [{
                "address": info.host,
                "port": info.port or 443,
                "users": [{
                    "id": info.uuid,
                    "encryption": enc,
                    "flow": flow,
                }],
            }]},
            "streamSettings": stream,
        }],
    }


async def wait_socks_ready(process, port: int) -> bool:
    """صبر تا وقتی inbound محلی اتصال بپذیره؛ False یعنی xray بالا نیامد."""
    deadline = time.monotonic() + XRAY_START_TIMEOUT
    while time.monotonic() < deadline:
        if process.returncode is not None:
            return False
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", port), timeout=1.0
            )
        except Exception:
            await asyncio.sleep(0.15)
            continue
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True
    return False


async def _probe_once(session, url: str, deadline_sec: float) -> float:
    """یک probe → تأخیر به ms، یا ‏-1 اگر نرسید یا کد غیرقابل‌قبول داد."""
    start = time.monotonic()
    try:
        async with session.get(
            url,
            allow_redirects=False,
            timeout=aiohttp.ClientTimeout(total=deadline_sec),
        ) as resp:
            ms = (time.monotonic() - start) * 1000
            # خواسته‌ی صریح: ۲۰۰ یا ۲۰۴ هر دو «سالم» است.
            return round(ms, 1) if resp.status in OK_STATUS else -1.0
    except Exception:
        return -1.0


async def _pick_url(session, deadline_sec: float) -> Tuple[Optional[str], float]:
    """اولین URL ای که از این تونل جواب می‌دهد، همراه تأخیرش.

    probe های بعدی همان URL را می‌زنند: اگر هر probe به مقصد دیگری برود،
    عددها با هم مقایسه‌شدنی نیستند و jitter عددِ بی‌معنایی می‌شود.
    """
    for url in TEST_URLS:
        ms = await _probe_once(session, url, deadline_sec)
        if ms >= 0:
            return url, ms
    return None, -1.0


async def _speed_test(session) -> Tuple[float, str]:
    """دانلود SPEED_TEST_BYTES بایت از داخل تونل → سرعت به KB/s.

    زمان‌سنجی *بعد* از رسیدن هدرها شروع می‌شود: تا آن لحظه فقط دست‌دادن و
    تأخیر مسیر را سنجیده‌ایم و آن را جدا و دقیق‌تر در probe ها داریم. این‌جا
    چیزی که مهم است نرخ عبور داده است، نه هزینه‌ی برقراری اتصال.
    """
    url = SPEED_TEST_URL
    if url.endswith("="):
        url = f"{url}{SPEED_TEST_BYTES}"
    try:
        async with session.get(
            url, timeout=aiohttp.ClientTimeout(total=SPEED_TEST_TIMEOUT_SEC)
        ) as resp:
            if resp.status not in (200, 206):
                return 0.0, f"سرعت: کد {resp.status}"
            started = time.monotonic()
            total = 0
            async for chunk in resp.content.iter_chunked(32 * 1024):
                total += len(chunk)
                if total >= SPEED_TEST_BYTES:
                    break
            elapsed = time.monotonic() - started
    except asyncio.TimeoutError:
        return 0.0, "سرعت: timeout"
    except Exception as exc:
        return 0.0, f"سرعت: {type(exc).__name__}"
    if total < 16 * 1024 or elapsed <= 0:
        # حجم کمتر از این، نرخ قابل‌اتکایی نمی‌دهد؛ «اندازه‌گیری نشد».
        return 0.0, "سرعت: داده‌ی ناکافی"
    return round(total / 1024 / elapsed, 1), ""


def _startup_reason(log_path: str) -> str:
    """دلیل بالا نیامدن xray، با پیام ریشه‌ای نه دنباله‌ی جمله‌ی راهنما."""
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as rfh:
            detail = clean_error(rfh.read())
    except OSError:
        detail = ""
    return f"xray بالا نیامد: {detail}" if detail else "xray بالا نیامد"


async def probe_config(config: str) -> Probe:
    """سنجش کامل یک کانفیگ روی *یک* پروسه‌ی xray.

    ترتیب دروازه‌ها: تأخیر واقعی → افت بسته → لرزش → سرعت. اولین دروازه‌ای
    که رد شود همان دلیل ثبت می‌شود، پس گزارش «چرا رد شد» یک عامل مشخص دارد.
    """
    xray = os.path.abspath(XRAY_PATH)
    if not os.path.exists(xray):
        return Probe(reason="xray پیدا نشد")

    port = await reserve_port()
    cfg = vless_to_xray(config, port)
    if not cfg:
        await release_port(port)
        return Probe(reason="تبدیل ناموفق")

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as fh:
        json.dump(cfg, fh)
        cfg_path = fh.name

    # لاگ xray در فایل نگه داشته میشه نه PIPE، تا پر شدن بافر باعث
    # بلوکه شدن پروسه نشه.
    log_path = cfg_path + ".log"
    process = None
    log_fh = None
    try:
        log_fh = open(log_path, "wb")
        process = await asyncio.create_subprocess_exec(
            xray, "run", "-c", cfg_path,
            stdout=log_fh,
            stderr=asyncio.subprocess.STDOUT,
        )

        if not await wait_socks_ready(process, port):
            return Probe(reason=_startup_reason(log_path))

        connector = ProxyConnector.from_url(f"socks5://127.0.0.1:{port}")
        # سقف هر probe = همان سقف تأخیر واقعی؛ پس probe کندتر از حد، خودش
        # timeout می‌شود و «افت بسته» شمرده می‌شود — همان‌طور که کاربر
        # واقعی هم آن اتصال را قطع‌شده می‌بیند.
        deadline = max(1.0, REAL_DELAY_MAX_MS / 1000.0)
        async with aiohttp.ClientSession(
            connector=connector,
            timeout=aiohttp.ClientTimeout(total=max(deadline, XRAY_TIMEOUT_SEC)),
        ) as session:
            url, first_ms = await _pick_url(session, deadline)
            if url is None:
                return Probe(reason="همه URL ها fail شدن")

            samples = [first_ms]
            lost = 0
            for _ in range(max(1, PROBE_COUNT) - 1):
                if PROBE_GAP_SEC > 0:
                    # فاصله‌ی بین probe ها؛ بدون آن هر چهار probe یک لحظه‌ی
                    # شبکه را می‌سنجند و jitter عملاً صفر در می‌آید.
                    await asyncio.sleep(PROBE_GAP_SEC)
                ms = await _probe_once(session, url, deadline)
                if ms < 0:
                    lost += 1
                else:
                    samples.append(ms)

            probe = Probe(
                ok=True,
                delay_ms=round(statistics.median(samples), 1),
                jitter_ms=_jitter(samples),
                loss_pct=round(lost * 100.0 / (len(samples) + lost), 1),
            )

            if probe.delay_ms > REAL_DELAY_MAX_MS:
                probe.ok = False
                probe.reason = f"تأخیر واقعی {round(probe.delay_ms)}ms"
                return probe
            if probe.loss_pct > MAX_PACKET_LOSS_PCT:
                probe.ok = False
                probe.reason = f"افت بسته {round(probe.loss_pct)}%"
                return probe
            if MAX_JITTER_MS > 0 and probe.jitter_ms > MAX_JITTER_MS:
                probe.ok = False
                probe.reason = f"لرزش {round(probe.jitter_ms)}ms"
                return probe

            if SPEED_TEST_ENABLED:
                kbps, speed_err = await _speed_test(session)
                probe.speed_kbps = kbps
                if kbps < SPEED_MIN_KBPS:
                    # همه‌ی probe ها پاس شده‌اند و فقط سرعت کم است. این را
                    # جدا علامت می‌زنیم تا اگر گیت سرعت *همه* را رد کرد
                    # (URL بسته، رانر محدود) بتوان نجاتش داد.
                    probe.ok = False
                    probe.speed_only_fail = True
                    probe.reason = speed_err or f"سرعت {kbps} KB/s"
            return probe

    except Exception as exc:
        return Probe(reason=f"{type(exc).__name__}: {str(exc)[:40]}")
    finally:
        if process is not None:
            try:
                process.terminate()
                await asyncio.wait_for(process.wait(), timeout=3)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass
        if log_fh is not None:
            try:
                log_fh.close()
            except OSError:
                pass
        for path in (cfg_path, log_path):
            try:
                os.unlink(path)
            except OSError:
                pass
        await release_port(port)


async def http_test_single(config: str) -> Tuple[bool, float, str]:
    """پوسته‌ی سازگاری: فقط «سالم بود؟ چند ms؟ چرا نه؟».

    ابزارهای دیگر (و تست‌ها) همین سه‌گانه را می‌خواهند؛ سنجه‌های تازه از
    probe_config گرفته می‌شوند.
    """
    probe = await probe_config(config)
    return probe.ok, probe.delay_ms if probe.ok else 0.0, probe.reason


async def _one_round(
    configs: List[str],
) -> Tuple[List[Tuple[str, float]], int, dict, dict]:
    """یک دور سنجش → (قبولی‌ها با تأخیر، تعداد رد، دلایل، سنجه‌ی هر کانفیگ)."""
    passed: List[Tuple[str, float]] = []
    speed_rejects: List[Tuple[str, Probe]] = []
    quality: dict = {}
    failed = 0
    reasons: dict = {}
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_XRAY)

    async def bounded(cfg: str):
        async with semaphore:
            return cfg, await probe_config(cfg)

    results = await asyncio.gather(
        *[bounded(c) for c in configs],
        return_exceptions=True,
    )

    for item in results:
        if isinstance(item, BaseException):
            failed += 1
            name = type(item).__name__
            reasons[name] = reasons.get(name, 0) + 1
            continue
        cfg, probe = item
        if probe.ok:
            passed.append((cfg, probe.delay_ms))
            quality[cfg] = probe
        elif probe.speed_only_fail:
            # قضاوت درباره‌اش را تا بعد از دیدن کل دور عقب می‌اندازیم.
            speed_rejects.append((cfg, probe))
        else:
            failed += 1
            reasons[probe.reason] = reasons.get(probe.reason, 0) + 1

    # نجات از گیت سرعت: اگر چند کانفیگ *فقط* روی سرعت افتادند و هیچ کانفیگی
    # از این دور قبول نشد، مقصر خودِ بنچمارک است (URL بسته، پهنای‌باند رانر
    # اشغال)، نه کانفیگ‌ها. قاعده‌ی پروژه: «تست نشد ≠ رد شد» — پس قبول
    # می‌شوند و سرعتشان «اندازه‌گیری‌نشده» (۰) ثبت می‌شود تا برچسب دروغ نگوید.
    if not passed and len(speed_rejects) >= SPEED_RESCUE_MIN:
        logger.warning(
            f"⚠️ گیت سرعت همه‌ی {len(speed_rejects)} کانفیگ سالم را رد کرد؛ "
            "بنچمارک نامعتبر فرض شد و بدون داده‌ی سرعت عبور می‌کنند."
        )
        for cfg, probe in speed_rejects:
            probe.ok = True
            probe.speed_kbps = 0.0
            probe.reason = ""
            passed.append((cfg, probe.delay_ms))
            quality[cfg] = probe
    else:
        for cfg, probe in speed_rejects:
            failed += 1
            reasons[probe.reason] = reasons.get(probe.reason, 0) + 1

    return passed, failed, reasons, quality


def _aggregate(probes: List[Probe]) -> dict:
    """سنجه‌های چند دورِ یک کانفیگ → یک عدد برای هر سنجه.

    افت بسته میانگین می‌شود: تعداد probe در همه‌ی دورها یکسان است، پس
    میانگینِ درصدها همان نرخ کل افت روی همه‌ی probe هاست. لرزش هم میانگین.
    سرعت اما میانه است تا یک دورِ شلوغ روی رانر، عدد را خراب نکند.
    """
    losses = [p.loss_pct for p in probes if p.loss_pct >= 0]
    jitters = [p.jitter_ms for p in probes if p.jitter_ms >= 0]
    speeds = [p.speed_kbps for p in probes if p.speed_kbps > 0]
    return {
        "loss_pct": round(sum(losses) / len(losses), 1) if losses else -1.0,
        "jitter_ms": round(sum(jitters) / len(jitters), 1) if jitters else -1.0,
        "speed_kbps": round(statistics.median(speeds), 1) if speeds else 0.0,
    }


async def http_test_batch(
    configs: List[str],
) -> Tuple[List[Tuple[str, float]], dict]:
    """سنجش چنددوره‌ای؛ فقط کانفیگی که *همه‌ی* دورها را پاس کند برمی‌گردد.

    خروجی همان دوگانه‌ی همیشگی است (لیست (کانفیگ، تأخیر) و آمار)، تا هیچ
    فراخوانی‌ای نشکند؛ سنجه‌های تازه در stats["_quality"] سوار می‌شوند —
    همان الگوی stats["_reserve"] که لینک‌ها را از stats.json دور نگه می‌دارد.
    """
    rounds = max(1, HTTP_TEST_ROUNDS)
    logger.info(
        f"🌐 تأخیر واقعی + پایداری + سرعت برای {len(configs)} کانفیگ در "
        f"{rounds} دور (همزمان: {MAX_CONCURRENT_XRAY})..."
    )

    survivors = list(configs)
    per_config_ms: dict = {}
    per_config_probes: dict = {}
    round_stats: List[dict] = []
    reasons: dict = {}

    for index in range(1, rounds + 1):
        if not survivors:
            break
        if index > 1 and HTTP_ROUND_GAP_SEC > 0:
            # بدون فاصله هر سه دور یک لحظه‌ی شبکه را می‌سنجند و شرط
            # «همه‌ی دورها» بی‌معنا می‌شود.
            await asyncio.sleep(HTTP_ROUND_GAP_SEC)

        started = time.monotonic()
        passed, failed, round_reasons, quality = await _one_round(survivors)
        for reason, count in round_reasons.items():
            reasons[reason] = reasons.get(reason, 0) + count
        for cfg, ms in passed:
            per_config_ms.setdefault(cfg, []).append(ms)
        for cfg, probe in quality.items():
            per_config_probes.setdefault(cfg, []).append(probe)

        round_stats.append({
            "round": index,
            "input": len(survivors),
            "passed": len(passed),
            "failed": failed,
            "duration_seconds": round(time.monotonic() - started, 1),
        })
        logger.info(
            f"   دور {index}/{rounds}: {len(passed)}/{len(survivors)} پاس | "
            f"{round_stats[-1]['duration_seconds']}s"
        )
        survivors = [cfg for cfg, _ in passed]

    # میانه‌ی دورها؛ نماینده‌ی واقع‌بینانه‌تری از تأخیر است تا بهترین دور.
    valid: List[Tuple[str, float]] = [
        (cfg, round(statistics.median(per_config_ms[cfg]), 1))
        for cfg in survivors
        if per_config_ms.get(cfg)
    ]
    valid.sort(key=lambda x: x[1])

    latencies = [ms for _, ms in valid]
    quality_map = {
        cfg: _aggregate(per_config_probes[cfg])
        for cfg, _ in valid
        if per_config_probes.get(cfg)
    }
    speeds = [q["speed_kbps"] for q in quality_map.values() if q["speed_kbps"] > 0]
    losses = [q["loss_pct"] for q in quality_map.values() if q["loss_pct"] >= 0]
    stats = {
        "total": len(configs),
        "passed": len(valid),
        "failed": len(configs) - len(valid),
        "rounds": rounds,
        "round_stats": round_stats,
        "avg_ms": round(sum(latencies) / len(latencies), 1) if latencies else 0,
        "best_ms": round(min(latencies), 1) if latencies else 0,
        "avg_speed_kbps": round(sum(speeds) / len(speeds), 1) if speeds else 0,
        "speed_measured": len(speeds),
        "avg_loss_pct": round(sum(losses) / len(losses), 1) if losses else -1,
        "zero_loss": sum(1 for loss in losses if loss == 0),
        "top_reasons": dict(sorted(reasons.items(), key=lambda x: -x[1])[:3]),
        # کانال کناری: دیکشنری کانفیگ→سنجه. فراخوان آن را pop می‌کند تا
        # لینک‌ها هیچ‌وقت داخل stats.json ننشینند.
        "_quality": quality_map,
    }
    logger.info(
        f"لایه ۷ (تأخیر واقعی): {stats['passed']}/{stats['total']} بعد از "
        f"{rounds} دور | avg: {stats['avg_ms']}ms | best: {stats['best_ms']}ms | "
        f"افت: {stats['avg_loss_pct']}% | سرعت: {stats['avg_speed_kbps']} KB/s"
    )
    return valid, stats
