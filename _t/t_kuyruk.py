"""Kuyrukta duran gonderi ikinci kez planlanmasin.

17.09.2026: onay modu acikken "Bosphorus Boat Trip" gonderisi uretildi ve
"ready" olarak bekledi. plan_week yalnizca "published" kayitlara baktigi
icin bir sonraki tur ayni konuyu bastan planladi: ikinci model cagrisi,
ikinci AI fotograf (~25 sent), ve kuyrukta ikiz kayit. Ikizin biri onay
acilirsa Instagram'a ayni gonderiyi tekrar basacakti.

"skipped" bilerek disarida kaliyor: 14.09.2026'da atlanan bir konunun
kalici olarak yanmasi ayri bir arizaydi, o duzeltme bozulmamali.
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import social  # noqa: E402

BUGUN = date(2026, 9, 17)          # persembe; haftanin basi 2026-09-14
BASLIK = "Bosphorus Boat Trip: Which Cruise Suits You?"


class SahteSite:
    domain = "ornek.com"
    raw = {"social": {"mix": {"article_derived": 2}}, "link_map": {}}


def yazi(baslik=BASLIK, tarih="2026-09-17"):
    return {"title": baslik, "url": "https://ornek.com/x/", "slug": "x",
            "date": tarih}


def kayit(durum, baslik=BASLIK, tarih="2026-09-17"):
    return {"subject": baslik, "status": durum, "scheduled_for": tarih}


def planla(gecmis):
    return social.plan_week(SahteSite(), [yazi()], [], gecmis, media=[],
                            today=BUGUN, adet=1, yalniz_makale=True)


def konular(plan):
    return [p["subject"] for p in plan]


def test_gecmis_bossa_planlanir():
    assert konular(planla([])) == [BASLIK]


def test_yayinlanan_konu_tekrar_planlanmaz():
    assert planla([kayit("published")]) == []


def test_kuyrukta_bekleyen_ready_tekrar_planlanmaz():
    """Asil duzeltme: 17.09.2026'daki ikiz kayit bundan cikti."""
    assert planla([kayit("ready")]) == []


def test_onaylanmis_bekleyen_tekrar_planlanmaz():
    assert planla([kayit("approved")]) == []


def test_atlanan_konu_geri_gelir():
    """14.09.2026 duzeltmesi korunuyor: skipped kalici yanma degil."""
    assert konular(planla([kayit("skipped")])) == [BASLIK]


def test_sekiz_haftadan_eski_kayit_engellemez():
    assert konular(planla([kayit("ready", tarih="2026-07-01")])) == [BASLIK]


def test_baska_konunun_kaydi_engellemez():
    assert konular(planla([kayit("ready", baslik="Camlica Hill")])) == [BASLIK]


def test_buyuk_kucuk_harf_farki_onemsiz():
    assert planla([kayit("ready", baslik=BASLIK.upper())]) == []


if __name__ == "__main__":
    import traceback
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL  {fn.__name__}: {exc}")
        except Exception:
            failed += 1
            print(f"  ERROR {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} test geçti")
    sys.exit(1 if failed else 0)
