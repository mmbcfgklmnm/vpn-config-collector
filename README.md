# 🛡️ VPN Config Collector

کانفیگ‌های VLESS جمع‌آوری‌شده از منابع عمومی که از یک pipeline شش‌لایه
عبور کرده‌اند: فرمت → حذف تکراری → TCP → TLS → Geo → HTTP واقعی.

## آمار آخرین اجرا

<!-- STATS_START -->
| فیلد | مقدار |
|------|-------|
| آخرین آپدیت | 2026-09-01 20:12:51 UTC |
| مدت اجرا | 177.6s |
| جمع‌آوری | 3706 |
| ✅ معتبر | **279** |
| لایه ۱ فرمت | 2789 |
| لایه ۲ dedup | 2548 |
| لایه ۳ TCP | 1993 |
| لایه ۴ TLS | 1841 |
| لایه ۵ Geo | 1832 |
| لایه ۶ HTTP | 279 |
<!-- STATS_END -->

## استفاده

لینک subscription:

```
https://raw.githubusercontent.com/mmbcfgklmnm/vpn-config-collector/main/configs/valid.txt
```

- `configs/valid.txt` — کانفیگ‌هایی که همه لایه‌ها را پاس کرده‌اند
- `configs/all.txt` — همه‌ی کانفیگ‌های خام جمع‌آوری‌شده
- `configs/stats.json` — آمار کامل اجرا

## اجرای محلی

```bash
pip install -r requirements.txt
SKIP_TELEGRAM=true SKIP_XRAY=true python -m src.main
```

برای اجرای لایه ۶ باینری xray لازم است و مسیرش با `XRAY_BINARY_PATH` تنظیم می‌شود.
