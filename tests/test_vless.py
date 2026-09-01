"""تست‌های src.vless — تابع‌های خالص پارس و برچسب‌گذاری."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import vless

REALITY = (
    "vless://11111111-2222-3333-4444-555555555555@1.2.3.4:443"
    "?security=reality&type=tcp&sni=example.com&pbk=ABC&fp=chrome#My_Node"
)
WS_TLS = (
    "vless://11111111-2222-3333-4444-555555555555@example.org:8443"
    "?security=tls&type=ws&path=%2Fws%2Fpath&host=example.org#WS Node"
)


def test_parse_basic():
    info = vless.parse(REALITY)
    assert info is not None
    assert info.uuid == "11111111-2222-3333-4444-555555555555"
    assert info.host == "1.2.3.4"
    assert info.port == 443
    assert info.security == "reality"
    assert info.network == "tcp"
    assert info.sni == "example.com"
    assert info.is_reality is True
    assert info.name == "My_Node"


def test_parse_percent_decodes_path():
    """باگ اصلی: path به‌صورت %2Fws به xray می‌رسید."""
    info = vless.parse(WS_TLS)
    assert info is not None
    assert info.params["path"] == "/ws/path"
    assert info.network == "ws"


def test_parse_keeps_plus_in_values():
    """parse_qs از عمد استفاده نشده چون + را به space تبدیل می‌کند."""
    info = vless.parse(
        "vless://11111111-2222-3333-4444-555555555555@h.io:443"
        "?security=tls&path=/a+b"
    )
    assert info is not None
    assert info.params["path"] == "/a+b"


def test_parse_rejects_non_vless():
    assert vless.parse("vmess://abc") is None
    assert vless.parse("") is None


def test_parse_bad_port_returns_none():
    assert vless.parse(
        "vless://11111111-2222-3333-4444-555555555555@h.io:notaport?security=tls"
    ) is None


def test_ipv6_host_has_no_brackets():
    info = vless.parse(
        "vless://11111111-2222-3333-4444-555555555555@[2001:db8::1]:443"
        "?security=tls"
    )
    assert info is not None
    assert info.host == "2001:db8::1"


# ─── برچسب‌گذاری ──────────────────────────────────────────

def test_add_tag_keeps_name_and_replaces_old_tag():
    once = vless.add_tag(REALITY, 84.4, "NL")
    assert once.endswith("#My_Node|NL|84ms")
    twice = vless.add_tag(once, 120, "DE")
    assert twice.endswith("#My_Node|DE|120ms")


def test_add_tag_skips_unknown_country():
    tagged = vless.add_tag(REALITY, 50, "??")
    assert tagged.endswith("#My_Node|50ms")


def test_add_tag_noop_without_data():
    assert vless.add_tag(REALITY, 0, "") == REALITY


def test_getters_roundtrip():
    tagged = vless.add_tag(WS_TLS, 210, "DE")
    assert vless.get_name(tagged) == "WS Node"
    assert vless.get_country(tagged) == "DE"
    assert vless.get_latency(tagged) == "210ms"
    assert vless.get_latency_ms(tagged) == 210.0


def test_untagged_sorts_last():
    assert vless.get_latency_ms(REALITY) == float("inf")


def test_security_label_ignores_name_text():
    """کانفیگ TLS با کلمه‌ی reality در اسمش نباید Reality شمرده شود."""
    cfg = WS_TLS.replace("#WS Node", "#free-reality-node")
    assert vless.get_security_label(cfg) == "TLS"
    assert vless.is_reality(cfg) is False
    assert vless.get_security_label(REALITY) == "Reality"


def test_security_label_other_for_garbage():
    assert vless.get_security_label("not a config") == "Other"


# ─── استخراج ─────────────────────────────────────────────

def test_extract_from_plain_text():
    text = f"blah {REALITY} blah\n{WS_TLS}"
    found = vless.extract_configs(text)
    assert len(found) == 2
    assert found[0].startswith("vless://")


def test_extract_respects_limit():
    assert len(vless.extract_configs(f"{REALITY}\n{WS_TLS}", limit=1)) == 1


def test_extract_from_multiline_base64():
    """padding باید بعد از حذف newline محاسبه شود."""
    import base64
    blob = base64.b64encode(f"{REALITY}\n{WS_TLS}".encode()).decode()
    chunked = "\n".join(blob[i:i + 40] for i in range(0, len(blob), 40))
    assert len(vless.extract_configs(chunked)) == 2


def test_extract_empty_and_garbage():
    assert vless.extract_configs("") == []
    assert vless.extract_configs("hello world") == []
