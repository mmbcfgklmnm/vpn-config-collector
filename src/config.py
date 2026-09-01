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

# ─── فیلتر پروتکل ─────────────────────────────────────────
# فقط VLESS
PROTOCOL_PREFIX = "vless://"

# فقط این security ها قبول میشن
ALLOWED_SECURITY = ["reality", "tls", "xtls"]

# ─── تست ──────────────────────────────────────────────────
TCP_TIMEOUT_SEC     = _int_env("TCP_TIMEOUT_SEC", 4)
XRAY_TIMEOUT_SEC    = _int_env("XRAY_TIMEOUT_SEC", 12)
MAX_CONCURRENT_TCP  = _int_env("MAX_CONCURRENT_TCP", 100)
MAX_CONCURRENT_XRAY = _int_env("MAX_CONCURRENT_XRAY", 8)    # xray سنگینه

TLS_TIMEOUT_SEC     = _int_env("TLS_TIMEOUT_SEC", 5)
MAX_CONCURRENT_TLS  = _int_env("MAX_CONCURRENT_TLS", 50)
MAX_CONCURRENT_GEO  = _int_env("MAX_CONCURRENT_GEO", 20)

# سقف ورودی لایه ۶. تست xray برای هر کانفیگ چند ثانیه طول می‌کشه و
# job گیت‌هاب ۵۵ دقیقه سقف داره؛ فقط سریع‌ترین‌های لایه ۴ تست میشن.
MAX_HTTP_TEST       = _int_env("MAX_HTTP_TEST", 400)

# سرور نباید در این کشورها باشه. RU و CN از پیش‌فرض حذف شدن چون
# سرورهای سالم زیادی اونجا هست و over-filter می‌کرد.
BLOCKED_COUNTRIES = {
    c.strip().upper()
    for c in os.getenv("BLOCKED_COUNTRIES", "IR,KP").split(",")
    if c.strip()
}

SKIP_XRAY     = _bool_env("SKIP_XRAY")
SKIP_TELEGRAM = _bool_env("SKIP_TELEGRAM")

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

# ─── محدودیت‌ها ────────────────────────────────────────────
MAX_PER_SOURCE          = _int_env("MAX_PER_SOURCE", 2000)
CONFIGS_PER_TG_MESSAGE  = _int_env("CONFIGS_PER_TG_MESSAGE", 10)
MAX_TG_MSG_LEN          = 4096
# سقف واقعی برای ساخت پیام؛ کمی زیر ۴۰۹۶ تا entity ها جا بشن.
TG_SAFE_MSG_LEN         = 3800

# ─── xray ──────────────────────────────────────────────────
XRAY_PATH      = os.getenv("XRAY_BINARY_PATH", "./xray")

# ─── GitHub ────────────────────────────────────────────────
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO  = os.getenv("GITHUB_REPO", "")
REPO_URL     = os.getenv("REPO_URL", "")
SUB_URL      = f"{REPO_URL.replace('github.com','raw.githubusercontent.com')}/main/configs/valid.txt" if REPO_URL else ""
