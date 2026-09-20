"""Sosyal metin düzelticileri — hepsi gerçek bir yayın kazasından doğdu.

Bu üç işlev, Instagram'a çıkmış üç hatanın ardından yazıldı. Kod açıklamaları
olayı anlatıyor ama testi yoktu; yani düzeltmeler sessizce bozulabilirdi ve
aynı hata ikinci kez yayına çıkardı.

Denetim dönemi için önemli: Evren gönderileri okuyacak. Okuduğu metni tam da
bu işlevler şekillendiriyor.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import social  # noqa: E402

METIN = ("The underground cistern in Sultanahmet is a Roman structure "
         "with columns.")


# --- 15.09.2026: "HOOK" ve "BODY" satırları yayına çıktı -----------------

def test_bolum_basliklari_atiliyor():
    c = "HOOK: Bir bak\nBODY\nAsıl metin.\nHASHTAGS: #a"
    assert social.strip_labels(c) == "Bir bak\nAsıl metin.\n#a"


def test_yalniz_etiketten_ibaret_satir_dusuyor():
    assert social.strip_labels("BODY\nMetin") == "Metin"


def test_normal_metne_dokunmuyor():
    c = "Bir hamam ziyareti 90 dakika sürer.\n#Istanbul"
    assert social.strip_labels(c) == c


def test_bos_girdi_patlatmiyor():
    assert social.strip_labels("") == ""
    assert social.strip_labels(None) == ""


# --- 15.09.2026: "a Istanbul street" yayına çıktı ------------------------

def test_sesliden_once_an_oluyor():
    assert social.fix_articles("beneath a Istanbul street") == \
        "beneath an Istanbul street"


def test_sessizden_once_a_kaliyor():
    assert social.fix_articles("a hamam visit") == "a hamam visit"


def test_eu_ve_one_istisnasi():
    """"a European" ve "a one-day" DOĞRU. Kural harfe değil sese bakmalı;
    bu ikisi yazımı sesli, okunuşu sessiz."""
    assert social.fix_articles("a European city") == "a European city"
    assert social.fix_articles("a one-day guide") == "a one-day guide"


def test_bastaki_buyuk_harf_korunuyor():
    assert social.fix_articles("A Istanbul morning") == "An Istanbul morning"


# --- 15.09.2026: "#UndergoundArchitecture" yayına çıktı ------------------

def test_etiketteki_yazim_hatasi_metinden_duzeltiliyor():
    assert social.fix_hashtags("#UndergoundArchitecture", METIN) == \
        "#UndergroundArchitecture"


def test_bolunmus_etiket_butun_olarak_duzeltiliyor():
    """16.09.2026: "#SultansHmet" çıktı. Kelime ayırıcı etiketi "Sultans" +
    "Hmet" diye bölüyor, iki parça da "sultanahmet"e uzak kalıyor ve hiçbiri
    düzelmiyordu. Önce bütün etikete bakılıyor."""
    assert social.fix_hashtags("#SultansHmet", METIN) == "#Sultanahmet"


def test_metinde_gecmeyen_etikete_DOKUNULMUYOR():
    """Semt ve yer adları gövdede geçmeyebilir ve yanlış da değildir.
    Zorla düzeltmek, doğru etiketi bozmak olur."""
    assert social.fix_hashtags("#Fatih #Uskudar", METIN) == "#Fatih #Uskudar"


def test_dogru_etiket_bozulmuyor():
    assert social.fix_hashtags("#Sultanahmet", METIN) == "#Sultanahmet"


def test_metin_yoksa_etikete_dokunulmuyor():
    """Doğrulama kaynağı yokken tahminle düzeltmek zarar verir."""
    assert social.fix_hashtags("#Undergound", "") == "#Undergound"


def test_kisa_etiket_duzeltilmiyor():
    """Altı harften kısa kelimede Levenshtein gürültüye dönüyor."""
    assert social.fix_hashtags("#Spa #Bath", METIN) == "#Spa #Bath"


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
