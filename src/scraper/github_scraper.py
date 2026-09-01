"""
Scraper GitHub Repos.

سه اصلاح:
  ۱. الگوی انتخاب فایل: نسخه‌ی قبلی `.*(...|all|sub|config|...).*` بود که
     تقریباً هر مسیری رو می‌گرفت (install.md، wallpaper.png، ...) و سهمیه‌ی
     API و پهنای باند رو روی فایل‌های بی‌ربط خرج می‌کرد.
  ۲. انتخاب «۱۵ فایل بزرگ‌تر» جای خودش رو به امتیازدهی بر اساس اسم داد؛
     بزرگ‌ترین blob مخزن معمولاً کانفیگ نیست.
  ۳. ssl=False حذف شد — دلیلی برای پذیرش گواهی جعلی روی api.github.com و
     raw.githubusercontent.com وجود نداره.
"""
import asyncio
import re
from typing import List

import aiohttp

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from src import vless
from src.config import GITHUB_REPOS, GITHUB_TOKEN, MAX_PER_SOURCE
from src.logger import get_logger

logger = get_logger("github_scraper")

API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"

# پسوندهای متنی که ممکنه لیست کانفیگ باشن
TEXT_EXT = re.compile(r"\.(txt|text|sub|conf|list|md|yaml|yml|json|base64)$", re.I)

# کلمه‌هایی که در اسم فایل نشانه‌ی لیست کانفیگ هستن
CONFIG_HINT = re.compile(
    r"(vless|reality|v2ray|xray|sub(scription)?|config|servers?|nodes?|proxies|proxy|mix|all)",
    re.I,
)

# مسیرهایی که هیچ‌وقت کانفیگ نیستن
SKIP_PATH = re.compile(r"(^|/)(\.github|\.git|node_modules|docs?|images?|assets)/", re.I)
SKIP_EXT = re.compile(
    r"\.(png|jpe?g|gif|svg|ico|zip|gz|tar|7z|exe|dll|so|pdf|mp4|ttf|woff2?|lock)$", re.I
)

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_FILES_PER_REPO = 25


def extract_vless(text: str) -> List[str]:
    return vless.extract_configs(text, limit=MAX_PER_SOURCE)


def is_candidate(path: str) -> bool:
    """آیا این مسیر ارزش دانلود داره؟"""
    if SKIP_PATH.search(path) or SKIP_EXT.search(path):
        return False
    name = path.rsplit("/", 1)[-1]
    if TEXT_EXT.search(name):
        return True
    # فایل بدون پسوند فقط اگه اسمش صریحاً کانفیگ رو نشون بده
    return "." not in name and bool(CONFIG_HINT.search(name))


def rank(entry: dict) -> tuple:
    """اولویت: اسم مرتبط، بعد حجم بیشتر."""
    path = entry.get("path", "")
    return (1 if CONFIG_HINT.search(path) else 0, entry.get("size", 0))


async def get_repo_files(
    session: aiohttp.ClientSession, repo: str, headers: dict
) -> List[dict]:
    try:
        async with session.get(
            f"{API}/repos/{repo}/git/trees/HEAD?recursive=1",
            headers=headers,
        ) as resp:
            if resp.status == 403:
                logger.warning(f"⚠️ Rate limit: {repo}")
                return []
            if resp.status == 404:
                logger.warning(f"⚠️ پیدا نشد: {repo}")
                return []
            if resp.status != 200:
                logger.warning(f"⚠️ HTTP {resp.status}: {repo}")
                return []
            data = await resp.json()
    except Exception as exc:
        logger.debug(f"خطا {repo}: {exc}")
        return []

    return [
        entry
        for entry in data.get("tree", [])
        if entry.get("type") == "blob"
        and entry.get("size", 0) <= MAX_FILE_BYTES
        and is_candidate(entry.get("path", ""))
    ]


async def fetch_file(session: aiohttp.ClientSession, repo: str, path: str) -> str:
    try:
        async with session.get(f"{RAW}/{repo}/HEAD/{path}") as resp:
            if resp.status == 200:
                return await resp.text(encoding="utf-8", errors="ignore")
    except Exception:
        pass
    return ""


async def scrape_repo(
    session: aiohttp.ClientSession, repo: str, headers: dict
) -> List[str]:
    configs: List[str] = []
    files = await get_repo_files(session, repo, headers)
    files.sort(key=rank, reverse=True)
    files = files[:MAX_FILES_PER_REPO]

    contents = await asyncio.gather(
        *[fetch_file(session, repo, f["path"]) for f in files],
        return_exceptions=True,
    )
    for content in contents:
        if isinstance(content, str) and content:
            configs.extend(extract_vless(content))
            if len(configs) >= MAX_PER_SOURCE:
                break

    logger.info(f"  📂 {repo}: {len(configs)} کانفیگ ({len(files)} فایل)")
    return configs[:MAX_PER_SOURCE]


async def scrape_github() -> List[str]:
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "VPN-Collector/2.0",
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"
        logger.info("✅ GitHub Token فعاله")
    else:
        logger.warning("⚠️ GitHub Token نیست — سهمیه‌ی API خیلی کمه")

    all_configs: List[str] = []
    connector = aiohttp.TCPConnector(limit=10)
    timeout = aiohttp.ClientTimeout(total=30)

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        results = await asyncio.gather(
            *[scrape_repo(session, repo, headers) for repo in GITHUB_REPOS],
            return_exceptions=True,
        )

    for item in results:
        if isinstance(item, list):
            all_configs.extend(item)

    logger.info(f"✅ GitHub: {len(all_configs)} کانفیگ از {len(GITHUB_REPOS)} repo")
    return all_configs
