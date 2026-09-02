"""تنظیمات مرکزی پروژه"""
import os


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


def _bool_env(name: str, default: bool = False) -> bool:
    raw = (os.getenv(name, "") or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


# ─── تلگرام ───────────────────────────────────────────────
TELEGRAM_BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "")
TELEGRAM_API_ID     = os.getenv("TELEGRAM_API_ID", "")
TELEGRAM_API_HASH   = os.getenv("TELEGRAM_API_HASH", "")
TELEGRAM_SESSION    = os.getenv("TELEGRAM_SESSION", "")   # StringSession
ADMIN_IDS           = [
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().lstrip("-").isdigit()
]
# برای برچسب‌گذاری کانفیگ‌ها و امضای پیام‌ها.
CHANNEL_USERNAME    = os.getenv("CHANNEL_USERNAME", "").lstrip("@")


def _list_env(name: str, default: str) -> list:
    return [x.strip() for x in os.getenv(name, "").split(",") if x.strip()] or [
        x.strip() for x in default.split(",") if x.strip()
    ]


# ─── فیلتر پروتکل ─────────────────────────────────────────
# فقط VLESS
PROTOCOL_PREFIX = "vless://"

# فقط این security ها قبول میشن
ALLOWED_SECURITY = ["reality", "tls", "xtls"]

# اجازه‌ی security=none وقتی کانفیگ پشت CDN و روی transport مبتنی بر HTTP است.
# چرا: در پول واقعی ۸۰۰ کانفیگ فقط به دلیل «security نامعتبر: none» رد می‌شدند،
# در حالی که اندازه‌گیری با نودهای ایرانی check-host نشان داد همین دسته
# (CF-IP + ws + بدون TLS) بالاترین نرخ دسترسی از ایران را دارد: ۲۳ از ۲۴.
# اینها همان الگوی رایج «VLESS over WS از طریق Cloudflare» هستند.
ALLOW_CDN_PLAIN     = _bool_env("ALLOW_CDN_PLAIN", True)
CDN_PLAIN_NETWORKS  = ("ws", "httpupgrade", "xhttp", "grpc")

# ─── تست ──────────────────────────────────────────────────
TCP_TIMEOUT_SEC     = _int_env("TCP_TIMEOUT_SEC", 4)
XRAY_TIMEOUT_SEC    = _int_env("XRAY_TIMEOUT_SEC", 12)
MAX_CONCURRENT_TCP  = _int_env("MAX_CONCURRENT_TCP", 100)
MAX_CONCURRENT_XRAY = _int_env("MAX_CONCURRENT_XRAY", 8)    # xray سنگینه

# چند بار اتصال TCP باید *پشت سر هم* موفق شود تا endpoint قبول شود.
# چرا ۲: کاربر ۲۴۸ کانفیگ را دستی تست کرد و فقط ۸۸ تا TCP فعال داشتند، در
# حالی که pipeline تقریباً همه را قبول کرده بود. یک اتصال موفق می‌تواند
# شانسی باشد (SYN که به میدل‌باکس خورده، یا سروری که فقط لحظه‌ای بالا بوده).
# دو اتصال با فاصله، endpoint های لرزان را حذف می‌کند.
TCP_ATTEMPTS        = _int_env("TCP_ATTEMPTS", 2)
TCP_ATTEMPT_GAP_SEC = _float_env("TCP_ATTEMPT_GAP_SEC", 0.4)

TLS_TIMEOUT_SEC     = _int_env("TLS_TIMEOUT_SEC", 5)
MAX_CONCURRENT_TLS  = _int_env("MAX_CONCURRENT_TLS", 50)
MAX_CONCURRENT_GEO  = _int_env("MAX_CONCURRENT_GEO", 20)

# ─── تست دسترسی از ایران (check-host.net) ─────────────────
# مهم‌ترین لایه‌ی این نسخه. اندازه‌گیری روی ۳۰ endpoint از خروجی تأییدشده:
# از نودهای اروپا/آمریکا ۳۰ از ۳۰ زنده، از نودهای ایران ۱ از ۳۰. یعنی تست
# محلی روی رانر آمریکایی گیت‌هاب «سالم» را می‌سنجید نه «قابل استفاده».
# API عمومی check-host کلید نمی‌خواهد؛ اگر کلید داری ست کن، هدر Authorization
# فرستاده می‌شود.
CHECKHOST_API_KEY   = os.getenv("CHECKHOST_API_KEY", "")
# نودهای ایرانی check-host (تهران/اصفهان/شیراز). ir6 وجود ندارد.
CHECKHOST_NODES     = _list_env(
    "CHECKHOST_NODES",
    "ir1.node.check-host.net,ir3.node.check-host.net,ir5.node.check-host.net",
)
# چند نود ایرانی باید موفق شوند تا کانفیگ قبول شود. ۱ یعنی سخت‌گیری کمتر.
CHECKHOST_MIN_NODES     = _int_env("CHECKHOST_MIN_NODES", 1)
CHECKHOST_CONCURRENCY   = _int_env("CHECKHOST_CONCURRENCY", 12)
# سقف endpoint هایی که به API فرستاده می‌شوند (سهمیه‌ی سایت مستند نیست، پس
# محافظه‌کارانه). نرخ اندازه‌گیری‌شده ~۲ endpoint در ثانیه با همزمانی ۱۲.
CHECKHOST_MAX_ENDPOINTS = _int_env("CHECKHOST_MAX_ENDPOINTS", 600)
# به محض رسیدن به این تعداد endpoint زنده، تست متوقف می‌شود.
CHECKHOST_TARGET_ALIVE  = _int_env("CHECKHOST_TARGET_ALIVE", 150)
CHECKHOST_TIMEOUT_SEC   = _int_env("CHECKHOST_TIMEOUT_SEC", 20)

# سقف ورودی لایه ۶ (فقط برای بستن زمان job؛ کانفیگ‌های تست‌نشده از خروجی
# حذف نمی‌شوند و بعد از تأییدشده‌ها می‌آیند). اجرای ۱۸:۱۴ UTC با سقف ۴۰۰
# کل pipeline را ۱۹۹s برد و سقف job ۵۵ دقیقه است، پس جا برای بیشتر هست.
MAX_HTTP_TEST       = _int_env("MAX_HTTP_TEST", 1500)

# چند بار تست HTTP واقعی تکرار شود. الگو از 0xRadikal/Free-v2ray-Configs:
# در اندازه‌گیری آن‌ها ۳۰٪ کانفیگ‌هایی که «یک بار» جواب دادند در دورهای بعد
# fail شدند. کانفیگ باید *همه‌ی* دورها را پاس کند تا publish شود.
HTTP_TEST_ROUNDS    = _int_env("HTTP_TEST_ROUNDS", 3)
# فاصله‌ی بین دورها؛ بدون فاصله هر سه دور یک لحظه‌ی شبکه را می‌سنجند.
HTTP_ROUND_GAP_SEC  = _int_env("HTTP_ROUND_GAP_SEC", 3)

# سرور نباید در این کشورها باشه. RU و CN از پیش‌فرض حذف شدن چون
# سرورهای سالم زیادی اونجا هست و over-filter می‌کرد.
BLOCKED_COUNTRIES = {
    c.strip().upper()
    for c in os.getenv("BLOCKED_COUNTRIES", "IR,KP").split(",")
    if c.strip()
}

SKIP_XRAY     = _bool_env("SKIP_XRAY")
SKIP_TELEGRAM = _bool_env("SKIP_TELEGRAM")
SKIP_CHECKHOST = _bool_env("SKIP_CHECKHOST")

# ─── منابع ────────────────────────────────────────────────
GITHUB_REPOS = [
    "0xRadikal/Free-v2ray-Configs",
    "barry-far/V2ray-Configs",
    "mahdibland/V2RayAggregator",
    "4n0nymou3/V2Ray-Configs-Premium",
    "roosterkid/openproxylist",
    "yebekhe/V2Hub",
    "hossein-mohseni/v2ray",
    "Pawdroid/Free-servers",
    "aiboboxx/v2rayfree",
    "mfuu/v2ray",
]

DIRECT_URLS = [
    # 0xRadikal - بهترین منبع، هر ۳۱ دقیقه
    "https://cdn.jsdelivr.net/gh/0xRadikal/Free-v2ray-Configs@main/protocols/vless.txt",
    "https://cdn.jsdelivr.net/gh/0xRadikal/Free-v2ray-Configs@main/light/configs.txt",
    # بقیه منابع
    "https://raw.githubusercontent.com/barry-far/V2ray-Configs/main/All_Configs_Sub.txt",
    "https://raw.githubusercontent.com/mahdibland/ShadowsocksAggregator/master/Eternity.txt",
    "https://raw.githubusercontent.com/4n0nymou3/V2Ray-Configs-Premium/main/configs.txt",
    "https://raw.githubusercontent.com/roosterkid/openproxylist/main/V2RAY_RAW.txt",
    "https://raw.githubusercontent.com/yebekhe/V2Hub/main/sub/mix",
    "https://raw.githubusercontent.com/Pawdroid/Free-servers/main/sub",
    "https://raw.githubusercontent.com/aiboboxx/v2rayfree/main/v2",
    "https://raw.githubusercontent.com/vpei/Free-Node-Merge/main/o/config.txt",
    "https://raw.githubusercontent.com/ermaozi/get_subscribe/main/subscribe/v2ray.txt",
]

TELEGRAM_CHANNELS = [
    "v2ray_configs",
    "V2RAYCONFIGSPOOL",
    "MahsaNetConfigTopic",
    "ShadowException",
    "freev2ray",
    "v2ray_free_conf",
    "proxystore11",
    "proxy_mtm",
    "ConfigsHUB",
    "v2rayNG_Backup",
]

# ─── فایل‌ها ───────────────────────────────────────────────
CONFIGS_DIR        = "configs"
VALID_FILE         = "configs/valid.txt"
ALL_FILE           = "configs/all.txt"
STATS_FILE         = "configs/stats.json"
# کانفیگ‌هایی که ادمین با /add دستی اضافه می‌کنه. جدا نگه داشته میشه تا
# روی فایل آمده از GitHub سایه نندازه.
MANUAL_FILE        = os.getenv("MANUAL_FILE", "configs/manual.txt")

# ─── خروجی‌های تازه (الگو از 0xRadikal/Free-v2ray-Configs) ──
# subscription نسخه‌ی Base64: کلاینت‌های قدیمی‌تر (v2rayNG قدیم، برخی نسخه‌های
# NekoRay) فقط این را درست می‌خوانند.
SUB_B64_FILE       = "configs/sub_base64.txt"
# کانفیگ‌هایی که از ایران هم زنده‌اند *و* همه‌ی دورهای xray را پاس کرده‌اند.
IRAN_FILE          = "configs/iran.txt"
IRAN_B64_FILE      = "configs/iran_base64.txt"
# ۱۰ کانفیگ برتر — همان چیزی که در کانال پست می‌شود.
TOP_FILE           = "configs/top10.txt"
# قرارداد ماشین‌خوان: مصرف‌کننده باید این را بخواند نه مسیرها را hardcode کند.
INDEX_FILE         = "configs/index.json"
# سلامت منابع: کدام منبع چند کانفیگ داد و چه خطایی خورد.
HEALTH_FILE        = "configs/health.json"
# وضعیت چرخش انتشار (کدام کانفیگ‌ها قبلاً پست شده‌اند).
PUBLISH_STATE_FILE = os.getenv("PUBLISH_STATE_FILE", "configs/publish_state.json")
# تفکیک بر اساس کشور — پوشه، هر کشور یک فایل.
BY_COUNTRY_DIR     = "configs/countries"

# ─── انتشار در کانال ───────────────────────────────────────
# ۱۰ کانفیگ در ۱۰ پیام جدا + یک پیام یازدهم با لینک subscription.
PUBLISH_COUNT       = _int_env("PUBLISH_COUNT", 10)
# فاصله‌ی انتشار به دقیقه. ربات (پروسه‌ی همیشه-روشن) این را اجرا می‌کند، نه
# cron گیت‌هاب: زمان‌بندی cron گیت‌هاب best-effort است و ۵ دقیقه‌ای نمی‌شود.
PUBLISH_INTERVAL_MIN = _int_env("PUBLISH_INTERVAL_MIN", 5)
# انتشار خودکار داخل پروسه‌ی ربات.
AUTO_PUBLISH        = _bool_env("AUTO_PUBLISH", True)
# آیا خودِ collector هم بعد از pipeline یک دسته پست کند؟ پیش‌فرض نه: اگر هم
# ربات (هر ۵ دقیقه) و هم collector (هر اجرا) پست کنند، دو حافظه‌ی چرخش
# مستقل دارند و کانفیگ تکراری در فاصله‌ی cooldown پست می‌شود. اگر ربات
# همیشه-روشن نداری این را روشن کن تا هر اجرا یک دسته منتشر شود.
PUBLISH_AFTER_COLLECT = _bool_env("PUBLISH_AFTER_COLLECT", False)
# فاصله‌ی بین پیام‌ها؛ تلگرام روی کانال ~۲۰ پیام در دقیقه اجازه می‌دهد.
PUBLISH_MSG_GAP_SEC = _int_env("PUBLISH_MSG_GAP_SEC", 3)
# یک کانفیگ تا وقتی این تعداد چرخه نگذرد دوباره پست نمی‌شود.
PUBLISH_COOLDOWN    = _int_env("PUBLISH_COOLDOWN", 6)
# پیام «سرِ دسته». پیش‌فرض خاموش است تا هر دوره دقیقاً ۱۰ کانفیگ + ۱ پیام
# لینک اشتراک باشد (خواسته‌ی صریح کاربر: پیام یازدهم لینک اشتراک است).
PUBLISH_INTRO       = _bool_env("PUBLISH_INTRO", False)

# ─── محدودیت‌ها ────────────────────────────────────────────
MAX_PER_SOURCE          = _int_env("MAX_PER_SOURCE", 2000)
CONFIGS_PER_TG_MESSAGE  = _int_env("CONFIGS_PER_TG_MESSAGE", 10)
MAX_TG_MSG_LEN          = 4096
# سقف واقعی برای ساخت پیام؛ کمی زیر ۴۰۹۶ تا entity ها جا بشن.
TG_SAFE_MSG_LEN         = 3800

# ─── xray ──────────────────────────────────────────────────
# روی ویندوز باینری xray.exe است؛ بدون این، اجرای محلی لایه ۶ همیشه
# «xray پیدا نشد» می‌داد.
_XRAY_DEFAULT  = "./xray.exe" if os.name == "nt" else "./xray"
XRAY_PATH      = os.getenv("XRAY_BINARY_PATH", _XRAY_DEFAULT)

# ─── GitHub ────────────────────────────────────────────────
# پیش‌فرض‌ها روی همین repo تنظیم شده‌اند. قبلاً خالی بودند و اگر روی
# Railway ست نمی‌شدند، ربات نه می‌توانست valid.txt تازه را از گیت‌هاب
# بخواند (پس snapshot زمان deploy را تحویل می‌داد) و نه لینک subscription
# را نشان دهد.
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO  = os.getenv("GITHUB_REPO", "mmbcfgklmnm/vpn-config-collector")
REPO_URL     = os.getenv("REPO_URL", f"https://github.com/{GITHUB_REPO}")
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main")

RAW_BASE = (
    f"https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}"
    if GITHUB_REPO else ""
)
# آینه‌ی jsDelivr. raw ترجیح داده می‌شود چون cache آن ۵ دقیقه است در برابر
# ۱۲ ساعت jsDelivr — برای پولی که هر نیم‌ساعت عوض می‌شود ۱۲ ساعت یعنی کهنه.
MIRROR_BASE = (
    f"https://cdn.jsdelivr.net/gh/{GITHUB_REPO}@{GITHUB_BRANCH}"
    if GITHUB_REPO else ""
)

SUB_URL       = f"{RAW_BASE}/{VALID_FILE}" if RAW_BASE else ""
SUB_B64_URL   = f"{RAW_BASE}/{SUB_B64_FILE}" if RAW_BASE else ""
SUB_IRAN_URL  = f"{RAW_BASE}/{IRAN_FILE}" if RAW_BASE else ""
INDEX_URL     = f"{RAW_BASE}/{INDEX_FILE}" if RAW_BASE else ""
SUB_MIRROR_URL = f"{MIRROR_BASE}/{VALID_FILE}" if MIRROR_BASE else ""
