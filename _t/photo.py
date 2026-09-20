"""AI fotoğraf üretimi — blog öne çıkan görseli ve Instagram gönderisi.

FOTOĞRAFI BİZ SEÇMİYORUZ. 15.09.2026'da Evren'in kuralı: "fotografi sen secme,
chatgpt'nin apisini verdim sana, blog'u komple ver gerekiyorsa prompt gibi
chatgpt'ye o da sana bu blog ve postta kullanabilecek bir foto olustursun,
basit ve duz. fikir uretme bana."

Bu dosyada ÖNCEDEN elle yazılmış bir sahne tablosu vardı (SAHNELER): konu
"bosphorus" içeriyorsa çay bardaklı güverte, "bazaar" içeriyorsa lambalı
çarşı sokağı... Bunlar benim uydurduğum sahnelerdi ve yazının kendisiyle
ilgileri yoktu — Haliç yazısına Boğaz fotoğrafı çıkmasının sebeplerinden
biri buydu. Tablo kaldırıldı. Artık yazının METNİ modele veriliyor, sahneyi
model kuruyor.

Modele giden istem üç parçadan oluşuyor:
  1. görev cümlesi — "bu yazıyı oku, onu anlatan tek bir sade fotoğraf üret"
  2. yazının kendisi (etiketlerden arındırılmış, ilk ~2.500 karakter)
  3. aşağıdaki teknik kısıtlar

Kısıtlar uydurma değil, üretim hatalarından çıktı:

YAZI YOK. Görsel modelleri harfleri hâlâ bozuk üretiyor ("ISTAMBUL" yerine
"ISTMABUL"). Marka tipografisini visuals.social_post kendi çiziyor; modelden
yalnızca temiz fotoğraf isteniyor.

TANINMIŞ YAPININ PORTRESİNİ İSTEMİYORUZ. Üretilen görseller Ayasofya'nın
minare sayısını, Galata'nın külahını hep hafif yanlış çiziyor; seyahat
rehberinde yanlış detay güven kaybıdır. Onun yerine o yapının ATMOSFERİ
isteniyor: çevresi, dokusu, ışığı.

HASSAS KONU FİLTRESİ. "bath / spa / massage" kelimeleri güvenlik süzgecini
tetikliyor (13.09.2026, hamam yazısı reddedildi). Hamam konularında istem
mimariye daraltılıyor: boş mermer, buhar, ışık, bakır tas, hiç insan yok.
"""
from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.request

API = "https://api.openai.com/v1/images/generations"

# response_format GÖNDERİLMİYOR: yeni uçta bilinmeyen parametre hatası veriyor.
# Cevap b64_json ya da url olarak gelebiliyor, ikisi de okunuyor.
# Kalite gönderi türüne göre. OpenAI rehberi: yakın plan portreler ve
# kimlik hassasiyeti olan kareler için "high" öneriliyor — Instagram
# gönderisinde fotoğraf tam ekran ve insan sık sık kadrajda, orada kalite
# doğrudan görünüyor. Blogun öne çıkan görseli daha küçük ve yatay, orada
# "medium" yeterli. Adet farkı 0,063 → 0,25 USD.
MODELS = [
    {"model": "gpt-image-1", "quality": "medium"},
    {"model": "dall-e-3"},
]
MODELS_YUKSEK = [
    {"model": "gpt-image-1", "quality": "high"},
    {"model": "dall-e-3"},
]
SIZE_YATAY = "1536x1024"      # blog öne çıkan görseli
SIZE_KARE = "1024x1024"       # artik kullanilmiyor, geriye donuk
SIZE_DIKEY = "1024x1536"      # Instagram: hesabin butun gonderileri 4:5 dikey
SIZE_DALLE_YATAY = "1792x1024"
SIZE_DALLE_DIKEY = "1024x1792"

# İstemin toplam uzunluğu. dall-e-3 4.000 karakterde kesiyor; yazıyı 2.500'de
# kırpınca kısıt metniyle birlikte ikisine de sığıyor.
MAX_METIN = 2500


class PhotoError(RuntimeError):
    pass


LANDMARK = {
    "hagia sophia", "ayasofya", "blue mosque", "sultanahmet", "topkapi",
    "galata tower", "galata", "dolmabahce", "suleymaniye", "basilica cistern",
    "camlica", "maiden's tower", "kiz kulesi", "grand bazaar", "spice bazaar",
    "pamukkale", "ephesus", "cappadocia", "goreme",
}

SENSITIVE = {"hamam", "bath", "bathing", "spa", "massage", "sauna", "steam",
             "scrub", "towel", "thermal", "hot spring", "wellness"}

# Hesabın fotoğraf dili. Her istemin sonuna aynen ekleniyor ki gönderiler
# birbirinin devamı gibi dursun.
# FOTOĞRAFIN GERÇEK GÖRÜNMESİ — 15.09.2026, Evren: "fotograf gercek bir
# fotograf gibi gozukmuyor sanki cizim gibi."
#
# Eski istem "Editorial travel photography" diyordu ve model bunu bir ÜSLUP
# olarak okuyup illüstratif, cilalı, mumsu bir görüntü üretiyordu.
#
# OpenAI'nin kendi gpt-image rehberi: "real photograph", "taken on a real
# camera" gibi ifadeler modelin fotogerçekçi kipini ayrıntılı kamera
# ayarlarından DAHA GÜÇLÜ tetikliyor. "photorealistic" kelimesi ise artık
# işe yaramıyor — eğitim verisinde milyarlarca kez geçtiği için ayırt edici
# değil; yerine fotoğrafçıların kendi sözlüğü kullanılıyor: gövde, objektif,
# diyafram, ISO, ışık kurulumu.
#
# İkinci mesele DOKU. Yapay görüntünün ele veren yanı pürüzsüzlüğü: gözeneksiz
# ten, katlanmayan kumaş, çiziksiz mermer. İstem bunları açıkça istiyor ve
# "plastik ten, aşırı yumuşatma, HDR parlaması, CGI cilası" negatif olarak
# yasaklanıyor.
STYLE = (
    "A real photograph, taken on a real camera. Not an illustration, not a "
    "3D render, not digital art, not a painting, not a matte painting. "
    "Shot on a Canon EOS R5 with a 35mm lens at f/2.8, ISO 400, handheld, "
    "available light only and no flash. Warm directional daylight, natural "
    "colour balance, soft contrast, shallow depth of field, a little 35mm "
    "film grain, slight vignetting at the corners. "
    "Editorial travel photography for an Istanbul travel guide: inviting and "
    "calm, a visitor's point of view, one clear subject, no busy collage. "
    "If people appear they are seen from behind or in profile, never a face "
    "in sharp focus; where skin shows it keeps its natural texture and "
    "visible pores. Fabric shows its weave and hangs under its own weight. "
    "Stone, marble and metal keep their chips, stains and wear. "
    "No plastic skin, no over-smoothing, no HDR glow, no waxy highlights, "
    "no perfect symmetry, no CGI sheen, no airbrushing. "
    "Absolutely no text, no letters, no numbers, no signage, no logos, no "
    "watermarks, no borders, no frames."
)

GOREV = (
    "Read the travel article below and produce ONE photograph that could "
    "illustrate it — a picture a reader would accept as the photo at the top "
    "of this exact article. Choose the subject yourself from what the article "
    "actually describes. Keep it simple and plain: one ordinary, believable "
    "moment, not a montage and not a dramatic composition."
)

KISIT_YAPI = (
    "Do NOT make a portrait of the famous monument itself. Photograph the "
    "atmosphere around it instead: textures, street life, light, water, "
    "materials. The building may only appear blurred and far in the "
    "background, if at all."
)

KISIT_HASSAS = (
    "Architecture only. Show an empty historic interior: marble, a domed "
    "ceiling, shafts of daylight, a copper bowl, folded cloth on a ledge. "
    "Absolutely no people, no bodies, no skin."
)

_ETIKET = re.compile(r"<[^>]+>")
_BOSLUK = re.compile(r"[ \t]*\n[ \t]*")


def duz_metin(html: str) -> str:
    """HTML gövdesini modele verilecek düz metne çevirir."""
    if not html:
        return ""
    metin = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    metin = re.sub(r"(?i)</(p|h[1-6]|li|tr|div)>", "\n", metin)
    metin = _ETIKET.sub(" ", metin)
    metin = (metin.replace("&nbsp;", " ").replace("&amp;", "&")
             .replace("&#8217;", "'").replace("&quot;", '"')
             .replace("&lt;", "<").replace("&gt;", ">"))
    metin = re.sub(r"[ \t]{2,}", " ", metin)
    metin = _BOSLUK.sub("\n", metin).strip()
    return re.sub(r"\n{3,}", "\n\n", metin)


def is_sensitive(konu: str) -> bool:
    k = (konu or "").lower()
    return any(w in k for w in SENSITIVE)


def build_prompt(site, konu: str, baslik: str, metin: str = "") -> str:
    """Yazının kendisinden istem kurar.

    Sahne tarifi YOK. Modele görev, yazı ve teknik kısıtlar gidiyor; neyi
    fotoğraflayacağına yazıya bakarak model karar veriyor.
    """
    sehir = site.raw.get("business", {}).get("primary_city") or "Istanbul, Türkiye"
    govde = duz_metin(metin)[:MAX_METIN]

    parcalar = [GOREV, f"Location: {sehir}."]
    if govde:
        parcalar.append(f"ARTICLE — title: {baslik}\n\n{govde}\n\nEND OF ARTICLE.")
    else:
        # Lokasyon ve mevsimlik gönderilerde yazı yok; elimizde yalnızca konu
        # var. Sahne yine uydurulmuyor, konunun kendisi veriliyor.
        parcalar.append(f"Subject: {konu}.")

    if is_sensitive(konu) or is_sensitive(baslik):
        parcalar.append(KISIT_HASSAS)
    elif any(ad in (konu or "").lower() or ad in (baslik or "").lower()
             for ad in LANDMARK):
        parcalar.append(KISIT_YAPI)

    parcalar.append(STYLE)
    # Panelden yazilan gorsel talimati. STYLE'dan SONRA geliyor ki hesabin
    # kendi tercihi son sozu soylesin; ama fotogercekcilik ve hassas konu
    # kisitlari yukarida ve onlari kaldirmiyor.
    from . import talimat
    ev = talimat.blok(site, "sosyal gorsel")
    if ev:
        parcalar.append(ev)
    return "\n\n".join(parcalar)


# Nötr yedek: güvenlik süzgeci yazıya özel istemi reddederse bu gidiyor.
NEUTRAL = (
    "Subject: a quiet morning in Istanbul, Türkiye. Early light on old stone "
    "architecture, a narrow cobbled street, tulip-shaped tea glasses on a "
    "copper tray, the water and seagulls soft in the distance. No people. "
    + STYLE
)


def _cagir(ayar: dict, istem: str, boyut: str, anahtar: str, timeout: int) -> dict:
    govde = json.dumps({**ayar, "size": boyut, "prompt": istem, "n": 1}).encode()
    req = urllib.request.Request(API, data=govde, method="POST")
    req.add_header("Authorization", f"Bearer {anahtar}")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "travelpedia-content-engine/1.0")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _baytlar(veri: dict, timeout: int) -> bytes:
    kayit = (veri.get("data") or [{}])[0]
    b64 = kayit.get("b64_json")
    if b64:
        return base64.b64decode(b64)
    url = kayit.get("url")
    if url:
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "travelpedia-content-engine/1.0")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    raise PhotoError(f"görsel dönmedi: {str(veri)[:200]}")


def generate(site, konu: str, baslik: str, *, square: bool = False,
             dikey: bool = False, metin: str = "", timeout: int = 180) -> tuple[bytes, str, str, str]:
    """PNG baytları, alt metin, KULLANILAN MODEL ve boyut.

    `metin` yazının HTML gövdesi. Verilirse fotoğrafın konusunu model yazıdan
    çıkarıyor; verilmezse elde yalnızca konu başlığı var demektir (lokasyon ve
    mevsimlik gönderiler).

    Model ve boyut maliyet kaydı için lazım: gpt-image-1 ile dall-e-3'ün
    adet fiyatı farklı, hangisinin cevap verdiğini bilmeden fatura
    hesaplanamıyor.
    """
    anahtar = os.environ.get(
        (site.raw.get("images", {}) or {}).get("token_env", "OPENAI_API_KEY"), "")
    if not anahtar:
        raise PhotoError("OPENAI_API_KEY yok")

    istemler = [build_prompt(site, konu, baslik, metin), NEUTRAL]
    hatalar: list[str] = []
    for ayar in (MODELS_YUKSEK if dikey else MODELS):
        dalle = ayar["model"] == "dall-e-3"
        if dikey:
            # 15.09.2026: Instagram gonderileri 1080x1350 dikey. Kare
            # uretip dikey tuvale koymak fotografin yarisini kirpiyordu.
            boyut = SIZE_DALLE_DIKEY if dalle else SIZE_DIKEY
        elif square:
            boyut = SIZE_KARE
        else:
            boyut = SIZE_DALLE_YATAY if dalle else SIZE_YATAY
        for sira, istem in enumerate(istemler):
            try:
                veri = _cagir(ayar, istem, boyut, anahtar, timeout)
            except urllib.error.HTTPError as exc:
                govde_hata = exc.read().decode(errors="replace")[:200]
                hatalar.append(f"{ayar['model']}/{sira} {exc.code}: "
                               f"{govde_hata.replace(anahtar, '***')}")
                # 400 güvenlik reddiyse yedek istem denenir; 401/404/429'da
                # istem değiştirmek işe yaramıyor.
                if exc.code != 400:
                    break
                continue
            except Exception as exc:
                hatalar.append(f"{ayar['model']}/{sira} {type(exc).__name__}: {exc}")
                break
            # Alt metin erişilebilirlik alanı; "AI ile üretildi" demiyor.
            # Üretim bilgisi kayıtta photo_source olarak tutuluyor.
            # Model adına kalite ekleniyor: gpt-image-1'in "high" adedi
            # "medium"un dört katı, maliyet kaydı ikisini ayırmalı.
            etiket = ayar["model"]
            if ayar.get("quality") == "high":
                etiket += "/high"
            return (_baytlar(veri, timeout), f"{baslik} — {konu}"[:120],
                    etiket, boyut)

    raise PhotoError(" | ".join(hatalar))
