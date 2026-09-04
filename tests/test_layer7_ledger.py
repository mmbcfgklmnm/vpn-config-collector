"""تست دفترِ نوبتِ لایه ۷ — «کشفِ تازه باید به تونل برسد».

شکایت کاربر: «چرا حس می‌کنم ربات دیگر دنبال کانفیگ نیست و همان‌های قبلی را
می‌فرستد؟ این بخش را خراب کردی.» مقصر جمع‌آوری نبود؛ صف‌بندی لایه ۷ بود:
ورودی‌اش فقط با «تأییدشده‌ی ایران، بعد سریع‌ترین TLS» مرتب می‌شد و بودجه‌ی
زمانی دُم صف را می‌بُرید، پس هر نیم‌ساعت همان سریع‌ترین‌ها آزموده می‌شدند و
بقیه هیچ‌وقت به تونل نمی‌رسیدند.

چهار چیز این‌جا قفل می‌شود:
  • کلید دفتر با عوض شدن برچسب عوض نمی‌شود، وگرنه هر اجرا همه‌چیز «تازه»
    به نظر می‌آید و حافظه بی‌اثر است.
  • سهم تازه‌ها در *هر پیشوندِ* صف رعایت می‌شود، نه فقط در کلِ آن — بودجه
    وسط صف تمام می‌شود.
  • ردشده‌ی لایه ۷ نه در صف می‌آید (تا دوره‌ی انتظار) نه در پول ذخیره (هرگز).
  • اجرای دوم کانفیگ‌هایی را می‌آزماید که اجرای اول به آن‌ها نرسید.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import main, vless
from src.tester import ledger

UUID = "11111111-2222-3333-4444-555555555555"


def cfg(n: int) -> str:
    return f"vless://{UUID}@10.0.0.{n}:443?security=tls&type=tcp#node{n}"


def mk(run: int, entries: dict) -> ledger.Ledger:
    """دفترِ دستی: mk(4, {1: (3, True), 2: (1, False)}).

    کلید = شماره‌ی cfg، مقدار = (اجرایی که آزموده شد، نتیجه).
    """
    return ledger.Ledger(
        run=run, entries={ledger.key(cfg(i)): v for i, v in entries.items()},
    )


def read(path) -> str:
    return path.read_text(encoding="utf-8")


# ─── کلید ─────────────────────────────────────────────────

def test_the_key_ignores_the_tag():
    """برچسبِ تأخیر/کشور/سنجه هر اجرا عوض می‌شود؛ کلید نباید عوض شود.

    اگر می‌شد، دفتر هیچ کانفیگی را «آزموده» نمی‌دید و همان حلقه‌ی تکرار از
    راه دیگری برمی‌گشت — این‌بار بی‌صدا.
    """
    plain = cfg(1)
    tagged = vless.add_tag(
        plain, 118.0, "DE", 205.0, loss_pct=0.0, jitter_ms=9.0, speed_kbps=430.0
    )
    assert ledger.key(tagged) == ledger.key(plain)
    assert ledger.key(f"  {plain}  \n") == ledger.key(plain)
    assert ledger.key(cfg(2)) != ledger.key(plain)


def test_the_key_is_long_enough_not_to_collide():
    """۴ نویسه‌ی short_id برای چشم آدم بس است، برای دفتر نه.

    برخورد این‌جا یعنی کانفیگی که هرگز آزموده نشده «آزموده» حساب شود و بی‌صدا
    از صف بیفتد — همان باگی که کل این ماژول برای بستنش نوشته شده.
    """
    keys = {ledger.key(cfg(i)) for i in range(3000)}
    assert len(keys) == 3000
    assert all(len(k) == ledger.KEY_LEN for k in keys)
    assert ledger.KEY_LEN >= 12


# ─── خواندن و نوشتن ───────────────────────────────────────

def test_a_missing_file_is_an_empty_ledger_not_an_error(tmp_path):
    """فایلِ نبود = یک اجرا بی‌حافظه، نه اجرای شکست‌خورده."""
    log = ledger.load(str(tmp_path / "nope.txt"))
    assert log.run == 1
    assert log.entries == {}
    assert log.verdict(cfg(1)) is None
    assert log.cooling(cfg(1)) is False


def test_a_broken_line_is_skipped_and_the_rest_is_still_read(tmp_path):
    """یک خطِ خراب نباید حافظه‌ی کل اجراهای قبل را دور بریزد."""
    path = tmp_path / "log.txt"
    path.write_text(
        "# سرصفحه\n"
        "\n"
        "خط آشغال\n"
        f"{ledger.key(cfg(1))} 3 1\n"
        f"{ledger.key(cfg(2))} خیلی 1\n"     # شماره‌ی اجرا عدد نیست
        f"{ledger.key(cfg(3))} 3 2\n"        # نتیجه نه ۰ نه ۱
        "کوتاه 3 1\n"                        # طول کلید غلط
        f"{ledger.key(cfg(4))} 2 0  # با توضیح\n",
        encoding="utf-8",
    )
    log = ledger.load(str(path))
    assert log.verdict(cfg(1)) is True
    assert log.verdict(cfg(4)) is False
    assert log.verdict(cfg(2)) is None
    assert log.verdict(cfg(3)) is None
    assert log.run == 4                      # بزرگ‌ترین اجرای ذخیره‌شده + ۱


def test_a_round_trip_remembers_every_verdict(tmp_path):
    path = tmp_path / "log.txt"
    log = ledger.load(str(path))
    counts = log.record([cfg(1), cfg(2), cfg(3)], {cfg(1)})
    assert counts == {
        "tested": 3, "first_time": 3, "new_passed": 1, "recovered": 0,
    }
    assert ledger.save(log, str(path)) is True
    # نوشتن اتمی است (.tmp بعد os.replace) و رد نمی‌گذارد؛ فایلِ نیمه‌کاره را
    # workflow با git add configs/ commit می‌کرد.
    assert not os.path.exists(f"{path}.tmp")

    again = ledger.load(str(path))
    assert again.run == 2                    # اجرای بعدی خودش را می‌شمارد
    assert again.verdict(cfg(1)) is True
    assert again.verdict(cfg(2)) is False
    assert again.verdict(cfg(3)) is False
    assert again.verdict(cfg(9)) is None


def test_a_tagged_config_finds_its_own_plain_record(tmp_path):
    """کانفیگ در فایل خروجی برچسب دارد و در دفتر نه؛ باید هم را پیدا کنند."""
    path = tmp_path / "log.txt"
    log = ledger.load(str(path))
    log.record([cfg(1)], {cfg(1)})
    ledger.save(log, str(path))
    tagged = vless.add_tag(cfg(1), 100.0, "US", 200.0)
    assert ledger.load(str(path)).verdict(tagged) is True


def test_an_empty_ledger_never_overwrites_a_good_one(tmp_path):
    """همان قاعده‌ی «خروجی خالی ننویس»: حافظه‌ی دیروز از هیچ بهتر است."""
    path = tmp_path / "log.txt"
    log = ledger.load(str(path))
    log.record([cfg(1)], {cfg(1)})
    ledger.save(log, str(path))
    before = read(path)

    assert ledger.save(ledger.Ledger(), str(path)) is False
    assert read(path) == before


def test_the_file_stays_diff_friendly(tmp_path):
    """هر اجرا commit می‌شود؛ خروجیِ نامرتب یعنی diff بی‌دلیل بزرگ."""
    path = tmp_path / "log.txt"
    log = ledger.load(str(path))
    log.record([cfg(i) for i in range(9, 0, -1)], {cfg(1)})
    ledger.save(log, str(path))

    lines = [ln for ln in read(path).splitlines() if not ln.startswith("#")]
    assert lines == sorted(lines)
    assert all(len(ln.split()) == 3 for ln in lines)
    # لینک کامل داخل دفتر نمی‌رود: فایلِ عمومی نباید کانفیگ کاربر را لو بدهد.
    assert UUID not in read(path)


# ─── فراموشی ──────────────────────────────────────────────

def test_stale_records_are_forgotten(tmp_path, monkeypatch):
    """حکمِ دیروز برای همیشه معتبر نیست؛ کانفیگ دوباره «تازه» می‌شود."""
    monkeypatch.setattr(ledger, "HTTP_LOG_KEEP_RUNS", 3)
    path = tmp_path / "log.txt"
    ledger.save(mk(10, {1: (6, True), 2: (8, True)}), str(path))

    back = ledger.load(str(path))
    assert back.verdict(cfg(1)) is None      # ۱۰-۳=۷ → کهنه
    assert back.verdict(cfg(2)) is True


def test_the_ledger_has_a_hard_cap_and_drops_the_oldest(tmp_path, monkeypatch):
    """فایلِ commit‌شونده نباید بی‌مرز رشد کند."""
    monkeypatch.setattr(ledger, "HTTP_LOG_MAX", 2)
    path = tmp_path / "log.txt"
    ledger.save(mk(5, {i: (i, True) for i in (1, 2, 3, 4)}), str(path))

    back = ledger.load(str(path))
    assert len(back.entries) == 2
    assert back.verdict(cfg(4)) is True
    assert back.verdict(cfg(3)) is True
    assert back.verdict(cfg(1)) is None


# ─── صف ───────────────────────────────────────────────────

def test_a_never_tested_config_gets_a_guaranteed_share():
    """نیمی از بودجه برای کشفِ تازه — بی این، رفتار همان نسخه‌ی معیوب است."""
    ordered = [cfg(i) for i in range(1, 21)]
    log = mk(2, {i: (1, True) for i in range(1, 11)})
    turn = ledger.plan(ordered, log, limit=10, share=0.5, core=0)
    assert len(turn.queue) == 10
    assert turn.fresh_queued == 5
    assert turn.proven_queued == 5


def test_the_fresh_share_holds_at_every_prefix_of_the_queue():
    """بودجه هرجا قطع شود، سهم تازه‌ها باید تا همان‌جا رعایت شده باشد.

    صفِ پشت‌سرهم (اول آزموده‌ها، بعد تازه‌ها) این تست را رد می‌کند: اگر بودجه
    در ۶۰٪ صف تمام شود سهم تازه‌ها صفر است — همان باگِ اصلی، فقط پنهان‌تر.
    """
    ordered = [cfg(i) for i in range(1, 41)]
    log = mk(2, {i: (1, True) for i in range(1, 21)})
    queue = ledger.plan(ordered, log, limit=40, share=0.5, core=0).queue
    fresh = {cfg(i) for i in range(21, 41)}
    for cut in range(1, len(queue) + 1):
        assert sum(1 for c in queue[:cut] if c in fresh) >= cut // 2


def test_the_value_order_inside_each_tier_is_kept():
    """دفتر *نوبت* را عوض می‌کند نه معیارِ ارزش را: در هر رده ترتیب می‌ماند."""
    ordered = [cfg(i) for i in range(1, 21)]
    log = mk(2, {i: (1, True) for i in range(1, 11)})
    queue = ledger.plan(ordered, log, limit=10, share=0.5, core=0).queue

    fresh = {cfg(i) for i in range(11, 21)}
    assert [c for c in queue if c in fresh] == [cfg(i) for i in range(11, 16)]
    assert [c for c in queue if c not in fresh] == [cfg(i) for i in range(1, 6)]


def test_the_proven_core_sits_at_the_head_of_the_queue():
    """اگر بودجه خیلی زود بمیرد، کانال باید کانفیگِ *واقعاً تأییدشده* داشته باشد."""
    ordered = [cfg(i) for i in range(1, 21)]
    log = mk(2, {i: (1, True) for i in range(1, 11)})
    turn = ledger.plan(ordered, log, limit=10, share=0.5, core=3)
    assert turn.queue[:3] == [cfg(1), cfg(2), cfg(3)]
    assert turn.fresh_queued == 3             # سهم تازه‌ها هنوز هست
    assert len(turn.queue) == 10


def test_the_core_never_eats_more_than_the_limit():
    ordered = [cfg(i) for i in range(1, 6)]
    log = mk(2, {i: (1, True) for i in range(1, 6)})
    turn = ledger.plan(ordered, log, limit=2, share=0.5, core=30)
    assert turn.queue == [cfg(1), cfg(2)]
    assert turn.spare == [cfg(3), cfg(4), cfg(5)]


def test_a_zero_limit_queues_nothing_and_loses_nothing():
    ordered = [cfg(1), cfg(2)]
    turn = ledger.plan(ordered, mk(1, {}), limit=0, share=0.5, core=30)
    assert turn.queue == []
    assert turn.spare == ordered              # همه به پول ذخیره


def test_the_budget_is_never_left_unused():
    """سهم تضمینی نباید به قیمتِ بودجه‌ی هدررفته تمام شود.

    ۱۰ جای صف و فقط ۲ کانفیگ تازه → باید ۸ آزموده‌ی دیگر جای خالی را پر کنند.
    """
    ordered = [cfg(i) for i in range(1, 21)]
    log = mk(2, {i: (1, True) for i in range(1, 19)})
    turn = ledger.plan(ordered, log, limit=10, share=0.5, core=0)
    assert len(turn.queue) == 10
    assert turn.fresh_queued == 2
    assert turn.proven_queued == 8


# ─── ردشده: نه در صف، نه در ذخیره ─────────────────────────

def test_a_rejected_config_is_kept_out_of_the_reserve_pool():
    """«تست نشد» ≠ «رد شد» دو طرف تیغ دارد: ردشده هم «تست‌نشده» جا نمی‌زند.

    بدون این، کانفیگی که xray همین اجرا ردش کرد از درِ پول ذخیره وارد کانال
    می‌شد — با برچسبِ «تست‌نشده» که فنی درست ولی عملاً دروغ است.
    """
    ordered = [cfg(1), cfg(2), cfg(3)]
    log = mk(3, {1: (2, False), 2: (2, True)})
    turn = ledger.plan(ordered, log, limit=1, share=1.0, core=0)
    assert turn.queue == [cfg(3)]            # تازه، با سهم ۱۰۰٪
    assert turn.spare == [cfg(2)]            # ردشده در ذخیره نیست
    assert turn.held == 1


def test_a_failed_config_gets_a_second_chance_once_the_cooldown_ends(monkeypatch):
    """خرابیِ گذرا فرصت دوباره می‌گیرد، ولی نه در همان اجرا."""
    monkeypatch.setattr(ledger, "HTTP_RETRY_AFTER_RUNS", 3)

    cooling = mk(4, {1: (2, False)})         # ۴-۲=۲ < ۳ → هنوز نه
    assert cooling.cooling(cfg(1)) is True
    early = ledger.plan([cfg(1)], cooling, limit=5, share=0.5, core=0)
    assert early.queue == []
    assert early.spare == []
    assert early.held == 1

    ready = mk(6, {1: (2, False)})           # ۶-۲=۴ ≥ ۳ → نوبتش شد
    assert ready.cooling(cfg(1)) is False
    turn = ledger.plan([cfg(1)], ready, limit=5, share=0.5, core=0)
    assert turn.queue == [cfg(1)]
    assert turn.held == 0


def test_a_retry_that_did_not_fit_the_budget_is_counted_as_held():
    """آمار نباید ردشده‌ی بی‌نوبت را «تست‌نشده» گزارش کند."""
    ordered = [cfg(1), cfg(2)]
    log = mk(9, {1: (1, False), 2: (1, False)})
    turn = ledger.plan(ordered, log, limit=1, share=0.0, core=0)
    assert turn.queue == [cfg(1)]
    assert turn.spare == []
    assert turn.held == 1


# ─── ثبت نتیجه ────────────────────────────────────────────

def test_record_only_counts_what_was_really_tested():
    """بی‌نوبت‌مانده‌ی بودجه ثبت نمی‌شود، وگرنه اجرای بعدی «آزموده» می‌بیندش.

    این دقیقاً همان حلقه‌ای است که کاربر دیده بود، فقط یک لایه عقب‌تر: صفِ
    آزموده‌نشده‌ها بی آزمون خالی می‌شد.
    """
    log = mk(5, {1: (4, False), 2: (4, True)})
    counts = log.record([cfg(1), cfg(2), cfg(3)], {cfg(1), cfg(3)})
    assert counts == {
        "tested": 3, "first_time": 1, "new_passed": 1, "recovered": 1,
    }
    assert log.verdict(cfg(1)) is True        # برگشت
    assert log.verdict(cfg(2)) is False       # افتاد
    assert log.entries[ledger.key(cfg(3))] == (5, True)
    assert log.verdict(cfg(4)) is None        # اصلاً در صف نبود


def test_recording_the_same_config_twice_keeps_the_last_verdict():
    log = mk(2, {})
    log.record([cfg(1)], set())
    assert log.verdict(cfg(1)) is False
    assert log.record([cfg(1)], {cfg(1)})["recovered"] == 1
    assert log.verdict(cfg(1)) is True


# ─── دو اجرای پشت‌سرهم در pipeline ────────────────────────
# پاسخ مستقیم به شکایت: «چرا همان‌های قبلی را می‌فرستد؟» این بخش با خودِ
# main.pipeline کار می‌کند، نه با plan() تنها — چون باگ در وصل‌کردنِ لایه‌ها
# بود نه در حساب‌کردن.

CFGS = [cfg(i) for i in range(1, 13)]


def _stub(monkeypatch, path, seen):
    """لایه‌های شبکه‌ای stub؛ لایه ۷ فقط اولین کاندیدِ صف را تأیید می‌کند.

    `seen` صفِ هر اجرا را نگه می‌دارد تا تست ببیند اجرای دوم *چه چیز تازه‌ای*
    را آزمود.
    """
    async def tcp(c):
        return [(x, 5.0) for x in c], {"total": len(c), "connected": len(c)}

    async def iran(c):
        return [(x, 200.0) for x in c], {"total": len(c), "passed": len(c)}

    async def tls(c):
        # تأخیر صعودی به ترتیب شماره → ترتیبِ ارزشِ ورودی لایه ۷ قطعی است
        return [(x, 10.0 + CFGS.index(x)) for x in c], {"passed": len(c)}

    async def geo(c):
        return [(x, "US") for x in c], {"passed": len(c)}

    async def http(c):
        seen.append(list(c))
        return [(c[0], 100.0)], {"total": len(c), "passed": 1}

    monkeypatch.setattr(main, "filter_by_format", lambda c: (list(c), {}))
    monkeypatch.setattr(main, "deduplicate", lambda c: (list(c), {}))
    monkeypatch.setattr(main, "test_tcp_batch", tcp)
    monkeypatch.setattr(main, "check_iran_batch", iran)
    monkeypatch.setattr(main, "test_tls_batch", tls)
    monkeypatch.setattr(main, "check_geo_batch", geo)
    monkeypatch.setattr(main, "http_test_batch", http)
    monkeypatch.setattr(main, "SKIP_XRAY", False)
    monkeypatch.setattr(main, "CF_CLEAN_IP_ENABLED", False)
    monkeypatch.setattr(main, "MAX_HTTP_TEST", 4)
    monkeypatch.setattr(main, "LAYER7_LOG_FILE", path)


def _cycle(path):
    """یک اجرای کامل: pipeline حساب می‌کند، «main» دفتر را می‌نویسد."""
    final, stats = asyncio.run(main.pipeline(list(CFGS)))
    ledger.save(stats.pop("_layer7_log"), path)
    return final, stats


def test_the_second_run_tests_configs_the_first_one_never_reached(
    tmp_path, monkeypatch
):
    """قلبِ شکایت کاربر: اجرای بعدی نباید همان چهار تای قبل را دوباره بیازماید."""
    path = str(tmp_path / "layer7_log.txt")
    seen: list = []
    _stub(monkeypatch, path, seen)

    _, first = _cycle(path)
    assert seen[0] == [cfg(1), cfg(2), cfg(3), cfg(4)]
    rotation = first["layer7_http"]["rotation"]
    assert (rotation["run"], rotation["first_time"]) == (1, 4)
    assert rotation["new_passed"] == 1
    assert first["summary"]["fresh_tested"] == 4
    assert first["summary"]["new_passed"] == 1

    _, second = _cycle(path)
    # ۱ تأییدشده‌ی اجرای قبل (سرِ صف) + ۳ کانفیگِ هرگز آزموده‌نشده.
    assert seen[1] == [cfg(1), cfg(5), cfg(6), cfg(7)]
    rotation = second["layer7_http"]["rotation"]
    assert (rotation["run"], rotation["first_time"]) == (2, 3)
    assert rotation["held_after_fail"] == 3    # ۲،۳،۴ رد شدند و در انتظارند
    assert second["summary"]["fresh_tested"] == 3


def test_the_rejected_configs_are_not_offered_to_the_channel_as_untested(
    tmp_path, monkeypatch
):
    """پول ذخیره‌ی اجرای دوم باید فقط «نوبتش نرسید» باشد، نه «رد شد»."""
    path = str(tmp_path / "layer7_log.txt")
    _stub(monkeypatch, path, [])
    _cycle(path)
    _, second = _cycle(path)

    reserve = {ledger.key(c) for c in second["_reserve"]}
    assert ledger.key(cfg(2)) not in reserve   # لایه ۷ ردش کرد
    assert ledger.key(cfg(8)) in reserve       # فقط نوبتش نرسید
    assert second["layer7_http"]["not_tested"] == 5


def test_the_pipeline_computes_but_never_writes_the_ledger(tmp_path, monkeypatch):
    """نوشتن کارِ main() است.

    اگر pipeline خودش می‌نوشت، هر تستِ دیگرِ pipeline فایل واقعیِ configs/ را
    عوض می‌کرد و اجرای بعدیِ گیت‌هاب حافظه‌ی یک تستِ محلی را باور می‌کرد.
    """
    path = tmp_path / "layer7_log.txt"
    _stub(monkeypatch, str(path), [])
    _, stats = asyncio.run(main.pipeline(list(CFGS)))
    assert not path.exists()
    assert isinstance(stats["_layer7_log"], ledger.Ledger)
