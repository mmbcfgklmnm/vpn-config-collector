"""تست لایه ۴b: احیای کانفیگ کلودفلری با IP تمیز.

خواسته‌ی صریح کاربر: «Host و SNI اصلی حفظ شود تا مسیر CDN نشکند و فقط IP
فیلترشده در بخش آدرس با یک IP تمیزِ تست‌شده‌ی کلودفلر عوض شود.»

پس مهم‌ترین چیزی که این‌جا قفل می‌شود همان است: بعد از احیا، *فقط* آدرس
عوض شده باشد. هر تغییر دیگری در کوئری یعنی کانفیگ در کلاینت کاربر می‌شکند.
هیچ تستی به شبکه دست نمی‌زند؛ اسکن و داوری همه جایگزین می‌شوند.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import cdn, clean_ip, vless
from src.clean_ip import REVIVE_MARK

UUID = "11111111-2222-3333-4444-555555555555"

CLEAN = "172.67.1.2"          # داخل 172.64.0.0/13
CLEAN2 = "104.18.7.7"         # داخل 104.16.0.0/13
NOT_CF = "8.8.8.8"

WS_TLS = (
    f"vless://{UUID}@104.16.5.9:443?type=ws&security=tls"
    "&host=cdn.example.com&path=%2Fws&serviceName=Grpc#MyNode|NL|84ms|IR212"
)
WS_PLAIN = (
    f"vless://{UUID}@104.16.5.9:80?type=ws&host=cdn.example.com&path=%2Fx#Plain"
)
DIRECT_TCP = f"vless://{UUID}@1.2.3.4:443?type=tcp&security=tls&sni=a.example.com#T"
ODD_PORT = (
    f"vless://{UUID}@104.16.5.9:9999?type=ws&security=tls&host=cdn.example.com#O"
)
NO_DOMAIN = f"vless://{UUID}@104.16.5.9:443?type=ws&security=tls&path=%2Fws#N"
DOMAIN_ADDR = f"vless://{UUID}@cdn.example.com:443?type=ws&security=tls#D"


# ─── حفظ مسیر CDN ─────────────────────────────────────────

def test_revive_replaces_only_the_address():
    revived = clean_ip.revive(WS_TLS, CLEAN)
    info = vless.parse(revived)
    assert info.host == CLEAN
    assert info.port == 443
    assert info.uuid == UUID
    assert info.params["host"] == "cdn.example.com"
    assert info.params["path"] == "/ws"
    assert info.params["type"] == "ws"
    assert info.params["security"] == "tls"


def test_revive_writes_sni_explicitly_for_tls():
    """کلاینت در نبودِ sni آن را از آدرس برمی‌دارد و آدرس حالا یک IP است."""
    info = vless.parse(clean_ip.revive(WS_TLS, CLEAN))
    assert info.params["sni"] == "cdn.example.com"


def test_revive_leaves_sni_out_of_plain_http():
    """بدون TLS نوشتن sni فقط لینک را شلوغ می‌کند و بعضی کلاینت‌ها را گیج."""
    info = vless.parse(clean_ip.revive(WS_PLAIN, CLEAN))
    assert "sni" not in info.params
    assert info.params["host"] == "cdn.example.com"
    assert info.port == 80


def test_revive_keeps_unknown_params_and_their_casing():
    """serviceName با همان حروف بزرگ می‌ماند: کلاینت‌ها case-sensitive می‌خوانند."""
    revived = clean_ip.revive(WS_TLS, CLEAN)
    assert "serviceName=Grpc" in revived
    # path دوباره رمزگذاری نشده؛ عینِ لینک اصلی رفته است
    assert "path=%2Fws" in revived


def test_revive_recovers_domain_from_address_when_host_missing():
    """آدرسِ دامنه‌ای: قبل از عوض کردن، همان دامنه در host/sni ثبت می‌شود."""
    info = vless.parse(clean_ip.revive(DOMAIN_ADDR, CLEAN))
    assert info.host == CLEAN
    assert info.params["host"] == "cdn.example.com"
    assert info.params["sni"] == "cdn.example.com"


def test_revive_marks_the_config_and_drops_the_stale_tag():
    """برچسب قبلی برای ورودی تازه بی‌اعتبار است و باید از اول سنجیده شود."""
    revived = clean_ip.revive(WS_TLS, CLEAN)
    assert revived.endswith(f"#MyNode{REVIVE_MARK}")
    assert vless.get_country(revived) == ""      # NL رفته
    assert vless.get_latency_ms(revived) == float("inf")
    assert vless.get_iran_ms(revived) == 0.0


def test_revive_mark_is_not_read_as_a_country_code():
    """‏♻CF سه کاراکتر است، پس get_country آن را کد کشور نمی‌خواند."""
    tagged = vless.add_tag(clean_ip.revive(WS_TLS, CLEAN), 90.0, "DE", 210.0)
    assert vless.get_country(tagged) == "DE"
    assert vless.get_name(tagged) == f"MyNode{REVIVE_MARK}"


def test_revive_is_idempotent_about_its_mark():
    once = clean_ip.revive(WS_TLS, CLEAN)
    twice = clean_ip.revive(once, CLEAN2)
    assert twice.count(REVIVE_MARK) == 1
    assert vless.parse(twice).host == CLEAN2


def test_revive_refuses_a_non_cloudflare_ip():
    """آدرس تصادفی روی کانفیگ CDN فقط کانفیگ را خراب می‌کند."""
    assert clean_ip.revive(WS_TLS, NOT_CF) == ""
    assert clean_ip.revive(WS_TLS, "not-an-ip") == ""


def test_revive_refuses_when_there_is_no_routing_domain():
    """بدون دامنه، CF نمی‌داند بسته را به کدام origin بفرستد."""
    assert clean_ip.revive(NO_DOMAIN, CLEAN) == ""
    assert clean_ip.revive("garbage", CLEAN) == ""


# ─── دروازه‌ی نامزدی ──────────────────────────────────────

def test_can_revive_accepts_cdn_transport_on_a_cf_port():
    assert clean_ip.can_revive(WS_TLS) is True
    assert clean_ip.can_revive(WS_PLAIN) is True
    assert clean_ip.can_revive(DOMAIN_ADDR) is True


def test_can_revive_rejects_direct_and_odd_endpoints():
    """tcp خالی از CF رد نمی‌شود و پورت غیرعادی یعنی سرور مستقیم."""
    assert clean_ip.can_revive(DIRECT_TCP) is False
    assert clean_ip.can_revive(ODD_PORT) is False
    assert clean_ip.can_revive(NO_DOMAIN) is False
    assert clean_ip.can_revive("vless://broken") is False


def test_grpc_and_httpupgrade_are_candidates():
    for network in ("grpc", "httpupgrade", "xhttp"):
        cfg = (
            f"vless://{UUID}@104.16.5.9:443?type={network}&security=tls"
            "&host=cdn.example.com#g"
        )
        assert clean_ip.can_revive(cfg) is True, network


def test_ip_of_reports_only_ip_addresses():
    assert clean_ip.ip_of(clean_ip.revive(WS_TLS, CLEAN)) == CLEAN
    assert clean_ip.ip_of(DOMAIN_ADDR) == ""
    assert clean_ip.ip_of("garbage") == ""


# ─── احیای گروهی ──────────────────────────────────────────

def _named(count: int):
    return [WS_TLS.replace("MyNode", f"n{i}") for i in range(count)]


def test_revive_batch_spreads_configs_over_ips():
    """نوبتی، تا بار روی یک IP جمع نشود و با سوختنش همه‌چیز نرود."""
    out = clean_ip.revive_batch(_named(4), [CLEAN, CLEAN2])
    assert [vless.parse(c).host for c in out] == [CLEAN, CLEAN2, CLEAN, CLEAN2]


def test_revive_batch_skips_non_candidates():
    out = clean_ip.revive_batch([DIRECT_TCP, WS_TLS, ODD_PORT], [CLEAN])
    assert len(out) == 1
    assert vless.parse(out[0]).params["host"] == "cdn.example.com"


def test_revive_batch_respects_the_limit():
    assert len(clean_ip.revive_batch(_named(10), [CLEAN, CLEAN2], limit=3)) == 3


def test_revive_batch_deduplicates_identical_results():
    out = clean_ip.revive_batch([WS_TLS, WS_TLS], [CLEAN])
    assert len(out) == 1


def test_revive_batch_without_ips_does_nothing():
    assert clean_ip.revive_batch(_named(3), []) == []
    assert clean_ip.revive_batch([], [CLEAN]) == []


# ─── فهرست محلی ───────────────────────────────────────────

def test_local_list_round_trip(tmp_path):
    path = str(tmp_path / "clean.txt")
    assert clean_ip.save_local([CLEAN, CLEAN2], path) is True
    assert clean_ip.load_local(path) == [CLEAN, CLEAN2]


def test_load_local_ignores_comments_junk_and_duplicates(tmp_path):
    """فایل دستی ویرایش می‌شود؛ یک اشتباه تایپی نباید به کانفیگ برسد."""
    path = tmp_path / "clean.txt"
    path.write_text(
        f"# سرآمد\n\n{CLEAN}  # این کار کرد\n{NOT_CF}\nhello\n{CLEAN}\n{CLEAN2}\n",
        encoding="utf-8",
    )
    assert clean_ip.load_local(str(path)) == [CLEAN, CLEAN2]


def test_load_local_of_a_missing_file_is_empty(tmp_path):
    assert clean_ip.load_local(str(tmp_path / "nope.txt")) == []


def test_save_local_never_truncates_the_list_to_empty(tmp_path):
    """فهرست کهنه از فهرست خالی بهتر است — همان قاعده‌ی خروجی خالی."""
    path = str(tmp_path / "clean.txt")
    clean_ip.save_local([CLEAN], path)
    assert clean_ip.save_local([], path) is False
    assert clean_ip.load_local(path) == [CLEAN]


def test_remember_promotes_the_ips_that_actually_worked(tmp_path):
    path = str(tmp_path / "clean.txt")
    clean_ip.save_local([CLEAN, CLEAN2], path)
    clean_ip.remember([CLEAN2, NOT_CF, ""], path)
    assert clean_ip.load_local(path) == [CLEAN2, CLEAN]


def test_remember_with_nothing_proven_keeps_the_file(tmp_path):
    path = str(tmp_path / "clean.txt")
    clean_ip.save_local([CLEAN], path)
    clean_ip.remember([NOT_CF], path)
    assert clean_ip.load_local(path) == [CLEAN]


# ─── نمونه‌گیری ───────────────────────────────────────────

def test_sampled_candidates_are_cloudflare_and_unique():
    ips = clean_ip.sample_candidates(60)
    assert len(ips) == 60 and len(set(ips)) == 60
    assert all(cdn.is_cloudflare_ip(ip) for ip in ips)


def test_sampling_never_returns_an_excluded_ip(monkeypatch):
    """با تصادفِ مهارشده: IP ای که قبلاً داریم دوباره نامزد نمی‌شود."""
    net = clean_ip._CF_NETS[0]
    monkeypatch.setattr(
        clean_ip.random, "choices", lambda seq, weights, k: [net]
    )
    monkeypatch.setattr(clean_ip.random, "randint", lambda a, b: 1)
    fixed = str(net.network_address + 1)
    assert clean_ip.sample_candidates(3) == [fixed]
    assert clean_ip.sample_candidates(3, exclude=[fixed]) == []


# ─── انتخاب IP تمیز ───────────────────────────────────────

def _patch_scan(monkeypatch, alive):
    async def scan(candidates, want=0, port=clean_ip.SCAN_PORT):
        keep = [ip for ip in candidates if ip in alive]
        return keep[:want] if want else keep

    monkeypatch.setattr(clean_ip, "scan", scan)


def _patch_pool(monkeypatch, pool):
    monkeypatch.setattr(
        clean_ip, "sample_candidates", lambda count, exclude=None: list(pool)
    )


def test_a_live_cache_skips_both_scanning_and_the_iran_quota(tmp_path, monkeypatch):
    """کشِ محلی قبلاً از ایران تأیید شده؛ دوباره سهمیه خرج نمی‌کنیم."""
    path = str(tmp_path / "clean.txt")
    clean_ip.save_local([CLEAN, CLEAN2], path)
    _patch_scan(monkeypatch, {CLEAN, CLEAN2})
    calls = []

    async def verify(ips):
        calls.append(list(ips))
        return {}

    got = asyncio.run(clean_ip.find_clean_ips(want=2, verify=verify, path=path))
    assert got == [CLEAN, CLEAN2]
    assert calls == []


def test_only_iran_verified_ips_survive_and_get_cached(tmp_path, monkeypatch):
    """زنده بودن از رانر آمریکایی «تمیز» نیست؛ داور نودهای ایرانی‌اند."""
    path = str(tmp_path / "clean.txt")
    _patch_pool(monkeypatch, [CLEAN, CLEAN2])
    _patch_scan(monkeypatch, {CLEAN, CLEAN2})

    async def verify(ips):
        return {CLEAN: 0.0, CLEAN2: 210.0}      # ۰ = بسته یا نامعلوم

    got = asyncio.run(clean_ip.find_clean_ips(want=3, verify=verify, path=path))
    assert got == [CLEAN2]
    assert clean_ip.load_local(path) == [CLEAN2]


def test_without_a_verifier_liveness_is_used_but_not_cached(tmp_path, monkeypatch):
    """بدون داوری فقط «زنده» را می‌دانیم؛ فایل کش باید فقط تأییدشده بماند."""
    path = str(tmp_path / "clean.txt")
    _patch_pool(monkeypatch, [CLEAN])
    _patch_scan(monkeypatch, {CLEAN})

    got = asyncio.run(clean_ip.find_clean_ips(want=2, verify=None, path=path))
    assert got == [CLEAN]
    assert not os.path.exists(path)


def test_a_broken_verifier_yields_nothing_instead_of_guessing(tmp_path, monkeypatch):
    path = str(tmp_path / "clean.txt")
    _patch_pool(monkeypatch, [CLEAN])
    _patch_scan(monkeypatch, {CLEAN})

    async def verify(ips):
        raise RuntimeError("check-host down")

    got = asyncio.run(clean_ip.find_clean_ips(want=2, verify=verify, path=path))
    assert got == []
    assert not os.path.exists(path)


def test_the_iran_quota_is_bounded_by_want(tmp_path, monkeypatch):
    """هر IP یک درخواست check-host است؛ ۲۰ نامزد نباید ۲۰ درخواست بشود."""
    path = str(tmp_path / "clean.txt")
    pool = [f"104.18.0.{i}" for i in range(1, 21)]
    _patch_pool(monkeypatch, pool)
    _patch_scan(monkeypatch, set(pool))
    judged = []

    async def verify(ips):
        judged.append(len(ips))
        return {ip: 200.0 for ip in ips}

    got = asyncio.run(clean_ip.find_clean_ips(want=3, verify=verify, path=path))
    assert judged == [6]                 # want * 2
    assert len(got) == 3
