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

# ─── احیای کانفیگ CDN با IP تمیز کلودفلر ────────────────────
# مسئله: در کانفیگ «VLESS over WS از طریق کلودفلر»، آدرسِ ورودی فقط یک نقطه‌ی
# ورود به شبکه‌ی CF است؛ مسیریابی واقعی با هدر Host و SNI انجام می‌شود. پس
# وقتی آن IP از ایران فیلتر می‌شود، سرور *سالم* است و فقط درِ ورودی بسته شده.
# با عوض کردن آدرس به یک IP تمیز CF (و دست نزدن به Host/SNI) همان کانفیگ
# دوباره کار می‌کند. این کار *فقط* روی endpoint هایی انجام می‌شود که لایه ۴
# حکم «از ایران بسته است» داده باشد — نه روی کانفیگ سالم.
CF_CLEAN_IP_ENABLED = _bool_env("CF_CLEAN_IP_ENABLED", True)
# فهرست محلی IP های تمیز (یکی در هر خط، # برای توضیح). اگر نبود یا کهنه بود،
# اسکنر خودش از محدوده‌های CF نمونه می‌گیرد.
CF_CLEAN_IP_FILE    = os.getenv("CF_CLEAN_IP_FILE", "configs/clean_ips.txt")
# چند IP تمیز لازم داریم. هر کانفیگِ احیاشده یکی از این‌ها را می‌گیرد
# (round-robin) تا همه‌ی بار روی یک IP نیفتد.
CF_CLEAN_IP_WANT    = _int_env("CF_CLEAN_IP_WANT", 6)
# چند IP نامزد اسکن شود تا CF_CLEAN_IP_WANT تای زنده پیدا شود.
CF_SCAN_CANDIDATES  = _int_env("CF_SCAN_CANDIDATES", 120)
CF_SCAN_CONCURRENCY = _int_env("CF_SCAN_CONCURRENCY", 40)
CF_SCAN_TIMEOUT_SEC = _float_env("CF_SCAN_TIMEOUT_SEC", 2.5)
# سقف کانفیگ‌های احیاشده در هر اجرا — هر کدام یک endpoint تازه است و
# سهمیه‌ی check-host را مصرف می‌کند.
CF_REVIVE_MAX       = _int_env("CF_REVIVE_MAX", 120)

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
# نودهای ایرانی check-host. این فهرست حدسی نیست: با tools/checkhost_nodes.py
# از /nodes/hosts گرفته شده — ۷ نود در ۴ شهر و ۷ ASN مختلف (تهران، اصفهان،
# شیراز). ir6 وجود ندارد. هر ۷ نود در *یک* درخواست تست می‌شوند، پس اضافه
# کردن‌شان سهمیه‌ی بیشتری مصرف نمی‌کند و شاهد بیشتری می‌دهد.
CHECKHOST_NODES     = _list_env(
    "CHECKHOST_NODES",
    "ir1.node.check-host.net,ir2.node.check-host.net,ir3.node.check-host.net,"
    "ir4.node.check-host.net,ir5.node.check-host.net,ir7.node.check-host.net,"
    "ir8.node.check-host.net",
)
# چند نود ایرانی باید موفق شوند تا کانفیگ قبول شود. ۲ از ۷ یعنی «دو دیتاسنتر
# مستقل وصل شدند»؛ با ۱ یک نود بی‌ثبات کافی بود. اگر پول خروجی خیلی کوچک شد،
# با repository variable برگردانش به ۱.
CHECKHOST_MIN_NODES     = _int_env("CHECKHOST_MIN_NODES", 2)
# اجرای واقعی ۲۰:۱۱ UTC با همزمانی ۱۲ و ۶۰۰ endpoint: ۵۸۱ پاسخ «HTTP 429» و
# فقط ۱۳ حکم واقعی. یعنی سهمیه‌ی API عمومی بسیار کمتر از حدس اولیه است.
# محدودکننده‌ی نرخ (پایین) اضافه شد و همزمانی پایین آمد.
CHECKHOST_CONCURRENCY   = _int_env("CHECKHOST_CONCURRENCY", 4)
# کمینه‌ی فاصله‌ی بین دو درخواست ثبت. با هر 429 خودکار بزرگ می‌شود و بعد از
# موفقیت آرام‌آرام برمی‌گردد؛ پس این عدد نقطه‌ی شروع است نه سقف.
CHECKHOST_MIN_GAP_SEC   = _float_env("CHECKHOST_MIN_GAP_SEC", 0.8)
CHECKHOST_MAX_GAP_SEC   = _float_env("CHECKHOST_MAX_GAP_SEC", 20.0)
# بعد از این تعداد 429، ادامه دادن بی‌فایده است: بقیه‌ی endpoint ها «نامعلوم»
# می‌مانند (حذف نمی‌شوند) و لایه زودتر تمام می‌شود.
CHECKHOST_MAX_429       = _int_env("CHECKHOST_MAX_429", 30)
# سقف endpoint هایی که به API فرستاده می‌شوند. با نرخ ~۱ در ثانیه، ۳۰۰ تا
# حدود ۵ دقیقه می‌برد (سقف job پنجاه‌وپنج دقیقه است).
CHECKHOST_MAX_ENDPOINTS = _int_env("CHECKHOST_MAX_ENDPOINTS", 300)
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

# ─── تأخیر واقعی، پایداری و سرعت (داخل تونل) ────────────────
# «تأخیر واقعی» = زمان رفت‌وبرگشت یک درخواست 204 که *از داخل تونل* رد شده.
# با پینگ TCP فرق دارد: پینگ TCP فقط می‌گوید چیزی روی آن پورت جواب می‌دهد،
# این می‌گوید تونل VLESS واقعاً داده جابه‌جا می‌کند و چقدر طول می‌کشد.
REAL_DELAY_MAX_MS   = _int_env("REAL_DELAY_MAX_MS", 3000)
# چند probe پشت سر هم با این فاصله. یک probe نه jitter می‌دهد نه packet loss؛
# با ۴ تا هر دو قابل محاسبه‌اند و هزینه‌اش ۳ ثانیه بیشتر روی همان پروسه‌ی xray
# است (پروسه یک بار بالا می‌آید و همه‌ی probe ها از آن رد می‌شوند).
PROBE_COUNT         = _int_env("PROBE_COUNT", 4)
PROBE_GAP_SEC       = _float_env("PROBE_GAP_SEC", 1.0)
# سقف افت بسته. ۲۵٪ یعنی از ۴ probe یکی می‌تواند بیفتد. خواسته‌ی صریح کاربر:
# «نودی با پینگ ۱۰۰ و ۰٪ افت از نودی با پینگ ۵۰ و ۲۰٪ افت ارزشمندتر است».
MAX_PACKET_LOSS_PCT = _int_env("MAX_PACKET_LOSS_PCT", 25)
# سقف لرزش (ms). ۰ = بی‌اهمیت. تونل با jitter بالا برای تماس صوتی/تصویری
# بی‌فایده است حتی اگر میانه‌ی تأخیرش خوب باشد.
MAX_JITTER_MS       = _int_env("MAX_JITTER_MS", 0)

# بنچمارک سرعت دانلود — یک فایل کوچک از داخل همان تونل.
SPEED_TEST_ENABLED  = _bool_env("SPEED_TEST_ENABLED", True)
SPEED_TEST_BYTES    = _int_env("SPEED_TEST_BYTES", 512 * 1024)
# __down endpoint کلودفلر دقیقاً برای همین کار است و هرجا CDN دارد نزدیک است.
SPEED_TEST_URL      = os.getenv(
    "SPEED_TEST_URL", "https://speed.cloudflare.com/__down?bytes="
)
SPEED_TEST_TIMEOUT_SEC = _float_env("SPEED_TEST_TIMEOUT_SEC", 15.0)
# کمتر از این، کانفیگ حذف می‌شود (KB/s). ۵۰ KB/s کف «قابل استفاده» است.
SPEED_MIN_KBPS      = _float_env("SPEED_MIN_KBPS", 50.0)
# اگر بنچمارک سرعت *همه* را رد کرد، تقصیر کانفیگ‌ها نیست (URL بسته، رانر
# محدود). بالای این تعداد ردشدنِ فقط-سرعتی و صفر قبولی، گیت سرعت کنار
# گذاشته می‌شود — همان قاعده‌ی «تست نشد ≠ رد شد».
SPEED_RESCUE_MIN    = _int_env("SPEED_RESCUE_MIN", 3)

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
# هر ورودی این دو فهرست با tools/probe_sources.py سنجیده شده است — همان
# هدرها و همان استخراج‌کننده‌ی web_scraper. دلیلش: از ۱۱ منبع قبلی ۶ تا مرده
# بودند (۴۰۴ یا صفر کانفیگ) و از ۵۰ کاندید بررسی‌شده ۲۸ تا بی‌فایده. URL مرده
# هم زمان اجرا می‌خورد، هم لاگ را پر می‌کند، هم این توهم را می‌سازد که منبع
# زیاد داریم. عددهای کنار هر خط شمارش اندازه‌گیری‌شده در ۲۰۲۶-۰۹-۰۳ است
# (کانفیگ/endpoint یکتا) — برای تشخیص «منبع خشکید» در اجرای بعدی.
GITHUB_REPOS = [
    "0xRadikal/Free-v2ray-Configs",
    "Epodonios/v2ray-configs",
    "coldwater-10/V2ray-Config",
    "Kolandone/v2raycollector",
    "Leon406/SubCrawler",
    "mheidari98/.proxy",
    "Surfboardv2ray/TGParse",
    "LalatinaHub/Mineral",
    "MhdiTaheri/V2rayCollector",
    "AzadNetCH/Clash",
    "ALIILAPRO/v2rayNG-Config",
    "roosterkid/openproxylist",
    "Pawdroid/Free-servers",
    "ermaozi/get_subscribe",
]

DIRECT_URLS = [
    # ── منابع بزرگ (هر کدام هزاران endpoint یکتا) ──
    "https://raw.githubusercontent.com/Kolandone/v2raycollector/main/vless.txt",              # 34875/7634
    "https://raw.githubusercontent.com/coldwater-10/V2ray-Config/main/Splitted-By-Protocol/vless.txt",  # 17262/5709
    "https://raw.githubusercontent.com/Leon406/SubCrawler/main/sub/share/vless",               # 7901/6097
    "https://raw.githubusercontent.com/mheidari98/.proxy/main/vless",                          # 7212/4361
    "https://raw.githubusercontent.com/Epodonios/v2ray-configs/main/All_Configs_Sub.txt",       # 6173/2458
    "https://raw.githubusercontent.com/Epodonios/v2ray-configs/main/Splitted-By-Protocol/vless.txt",    # 6151/2448
    "https://raw.githubusercontent.com/Surfboardv2ray/TGParse/main/python/vless",              # 6013/2418
    # 0xRadikal — الگوی pipeline از همین پروژه گرفته شده، هر ۳۱ دقیقه آپدیت
    "https://cdn.jsdelivr.net/gh/0xRadikal/Free-v2ray-Configs@main/protocols/vless.txt",       # 5260/3670
    "https://cdn.jsdelivr.net/gh/0xRadikal/Free-v2ray-Configs@main/light/configs.txt",         # 1130/845
    # ── منابع متوسط ──
    "https://raw.githubusercontent.com/LalatinaHub/Mineral/master/result/nodes",               # 1336/1264
    "https://raw.githubusercontent.com/MhdiTaheri/V2rayCollector/main/sub/vless",              # 747/403
    "https://raw.githubusercontent.com/AzadNetCH/Clash/main/AzadNet.txt",                      # 588/351
    "https://raw.githubusercontent.com/MhdiTaheri/V2rayCollector_Py/main/sub/Mix/mix.txt",     # 409/220
    "https://raw.githubusercontent.com/ndsphonemy/proxy-sub/main/speed.txt",                   # 374/214
    "https://raw.githubusercontent.com/ALIILAPRO/v2rayNG-Config/main/server.txt",              # 347/244
    "https://raw.githubusercontent.com/liketolivefree/kobabi/main/sub.txt",                    # 300/131
    "https://raw.githubusercontent.com/roosterkid/openproxylist/main/V2RAY_RAW.txt",           # 125/124
    # ── منابع کوچک ولی زنده (هزینه‌شان یک درخواست است) ──
    "https://raw.githubusercontent.com/Kwinshadow/TelegramV2rayCollector/main/sublinks/vless.txt",      # 65/60
    "https://raw.githubusercontent.com/Ashkan-m/v2ray/main/Sub.txt",                           # 56/56
    "https://raw.githubusercontent.com/Rayan-Config/C-Sub/refs/heads/main/configs/proxy.txt",  # 31/19
    "https://raw.githubusercontent.com/hans-thomas/v2ray-subscription/master/servers.txt",     # 26/23
    "https://raw.githubusercontent.com/w1770946466/Auto_proxy/main/Long_term_subscription1",   # 18/16
    "https://raw.githubusercontent.com/ermaozi/get_subscribe/main/subscribe/v2ray.txt",        # 8/8
    "https://raw.githubusercontent.com/Pawdroid/Free-servers/main/sub",                        # 7/7
    "https://raw.githubusercontent.com/mahsanet/MahsaFreeConfig/main/mtn/sub_1.txt",           # 6/5
    "https://raw.githubusercontent.com/Everyday-VPN/Everyday-VPN/main/subscription/main.txt",  # 2/2
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
# تفکیک «داخلی/خارجی» در انتهای مسیر تولید لینک اشتراک: هرچه تأیید ایران
# نگرفته این‌جا می‌آید. یک لینک برای کاربر داخل ایران، یک لینک برای کسی که
# از بیرون یا با ISP دیگری وصل می‌شود.
INTL_FILE          = "configs/international.txt"
INTL_B64_FILE      = "configs/international_base64.txt"
# پول ذخیره (لایه ۲): کانفیگ‌هایی که TCP/TLS/Geo را پاس کردند ولی به لایه ۷
# نرسیدند یا آن را رد نکردند. تنها مصرفش پر کردن سهمیه‌ی ۱۰تایی انتشار است
# وقتی خروجی تأییدشده کمتر از ۱۰ تاست — با برچسب صریح «تست‌نشده».
POOL_FILE          = "configs/pool.txt"
# ۱۰ کانفیگ برتر — همان چیزی که در کانال پست می‌شود.
TOP_FILE           = "configs/top10.txt"
# قرارداد ماشین‌خوان: مصرف‌کننده باید این را بخواند نه مسیرها را hardcode کند.
INDEX_FILE         = "configs/index.json"
# سلامت منابع: کدام منبع چند کانفیگ داد و چه خطایی خورد.
HEALTH_FILE        = "configs/health.json"
# وضعیت چرخش انتشار (کدام کانفیگ‌ها قبلاً پست شده‌اند).
PUBLISH_STATE_FILE = os.getenv("PUBLISH_STATE_FILE", "configs/publish_state.json")
# کانفیگ‌های اهدایی کاربران. commit نمی‌شود (.gitignore): محتوایش داده‌ی
# کاربر است و نباید در مخزن عمومی بایگانی شود.
DONATIONS_FILE     = os.getenv("DONATIONS_FILE", "configs/donations.json")
# وضعیت ماندگار ربات (روشن/خاموش، انتشار مکث). با restart پاک نمی‌شود.
BOT_STATE_FILE     = os.getenv("BOT_STATE_FILE", "configs/bot_state.json")
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
# سهمیه‌ی ۱۰تایی *باید* پر شود. تأییدشده‌های ایران اول می‌آیند، بعد
# کانفیگ‌های بین‌المللی، و اگر باز کم بود از POOL_FILE (تست‌نشده‌ها) پر
# می‌شود. چرا: در چرخه‌های واقعی فقط ۳ کانفیگ پست شد چون پول تأییدشده کوچک
# بود؛ کاربر صریح گفت سهمیه‌ی ۱۰ نباید بشکند.
PUBLISH_STRICT_COUNT = _bool_env("PUBLISH_STRICT_COUNT", True)
# آخرین چاره برای پر کردن سهمیه: کانفیگ‌های پول ذخیره (لایه ۶ را پاس کردند،
# لایه ۷ تأییدشان نکرده). با برچسب «تست‌نشده» پست می‌شوند تا کاربر گمراه نشود.
PUBLISH_FILL_FROM_POOL = _bool_env("PUBLISH_FILL_FROM_POOL", True)
# چند کانفیگ اهدایی *اضافه بر* سهمیه‌ی ۱۰تایی در هر دوره. اهدایی‌ها هرگز
# دوبار پست نمی‌شوند، پس ۵۰ کانفیگ اهدایی = ۲۵ دوره.
PUBLISH_DONATED_COUNT = _int_env("PUBLISH_DONATED_COUNT", 2)

# ─── بخش اهدای کانفیگ ──────────────────────────────────────
# کاربر کانفیگ می‌فرستد، ربات اعتبارسنجی می‌کند و به صف اهدایی اضافه می‌شود.
DONATE_ENABLED        = _bool_env("DONATE_ENABLED", True)
# سقف هر پیام و سقف روزانه‌ی هر کاربر — جلوگیری از پر کردن صف با یک paste.
DONATE_MAX_PER_MSG    = _int_env("DONATE_MAX_PER_MSG", 20)
DONATE_MAX_PER_DAY    = _int_env("DONATE_MAX_PER_DAY", 50)
# فاصله‌ی حداقلی بین دو اهدای یک کاربر (ثانیه) — ضد spam.
DONATE_MIN_GAP_SEC    = _int_env("DONATE_MIN_GAP_SEC", 5)
# سقف کل صف؛ از بی‌نهایت شدن فایل جلوگیری می‌کند.
DONATE_QUEUE_MAX      = _int_env("DONATE_QUEUE_MAX", 5000)
# قبل از انتشار، کانفیگ اهدایی یک تست TCP می‌گیرد. اهدایی مرده بدتر از
# نبودنش است.
DONATE_TCP_CHECK      = _bool_env("DONATE_TCP_CHECK", True)

# ─── محدودیت‌ها ────────────────────────────────────────────
MAX_PER_SOURCE          = _int_env("MAX_PER_SOURCE", 2000)
# سقف پول ذخیره‌ی نوشته‌شده در POOL_FILE. این فایل هر نیم‌ساعت commit می‌شود،
# پس بی‌سقف بودنش مخزن را باد می‌کند. ۵۰۰ یعنی ۵۰ دوره‌ی انتشار ذخیره.
POOL_MAX                = _int_env("POOL_MAX", 500)
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
SUB_IRAN_B64_URL = f"{RAW_BASE}/{IRAN_B64_FILE}" if RAW_BASE else ""
SUB_INTL_URL  = f"{RAW_BASE}/{INTL_FILE}" if RAW_BASE else ""
SUB_INTL_B64_URL = f"{RAW_BASE}/{INTL_B64_FILE}" if RAW_BASE else ""
INDEX_URL     = f"{RAW_BASE}/{INDEX_FILE}" if RAW_BASE else ""
SUB_MIRROR_URL = f"{MIRROR_BASE}/{VALID_FILE}" if MIRROR_BASE else ""


def country_sub_url(code: str) -> str:
    """لینک اشتراک یک کشور. کد باید دوحرفیِ الفبایی باشد.

    چرا اعتبارسنجی: این تابع با ورودی‌ای صدا زده می‌شود که از callback_data
    ربات می‌آید، یعنی داده‌ی سمت کاربر. بدون این بررسی می‌شد با `../` مسیر
    دیگری از مخزن را در لینک جا داد.
    """
    code = (code or "").strip().upper()
    if len(code) != 2 or not code.isalpha() or not RAW_BASE:
        return ""
    return f"{RAW_BASE}/{BY_COUNTRY_DIR}/{code}.txt"
