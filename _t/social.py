"""Sosyal medya gönderisi planlama ve üretimi.

Soro'nun yaptığı hata: makale = tek gönderi. Haftada üç yazı çıkarsa haftada üç
gönderi olur ve hesap ölü görünür. Burada karışım var (sites/<domain>.yaml →
social.mix):

  article_derived    o hafta çıkan yazıdan türetilen gönderi
  location_spotlight 199 lokasyon sayfasından görsel odaklı gönderi
  seasonal_practical mevsim/etkinliğe bağlı pratik bilgi

GÖRSEL KURALI — pazarlık konusu değil:
`require_real_photo: true`. Instagram'a üretilmiş grafik çıkmaz. Blogda
kullandığımız SVG kartlar veri taşıyor ve orada yerinde; Instagram'da bir
seyahat hesabının feed'i fotoğraf demek. Konuya uyan gerçek fotoğraf
bulunamazsa gönderi ÜRETİLMEZ — yanlış fotoğrafla gönderi atmaktansa o hafta
bir gönderi eksik çıkar.

Fotoğraflar WordPress medya kütüphanesinden geliyor: 325 görselin 204'ü
Instagram için yeterince büyük ve hepsi zaten Evren'in kendi sitesinin
görselleri. Instagram Graph API görseli URL'den kendisi çekiyor, bizim ayrıca
yüklememiz gerekmiyor.
"""
from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass, field, fields as dc_fields
from datetime import date, timedelta
from pathlib import Path

# FOTOĞRAF KALİTESİ — 15.09.2026, Evren: "instagramda cikan posttdaki fotonun
# kalitesi cok kotu, boyle canli kaliteli bir foto olmasi laazim. bu kalitede
# bir fotograf olmamamli."
#
# Gönderi 1080x1350 DİKEY. Yatay bir fotoğraf bu tuvale oturtulurken
# YÜKSEKLİK belirleyici: kütüphanedeki 1200x576'lık bir görsel 1350'ye
# ölçeklenirken 2,3 kat büyüyor ve bulanıklaşıyor. Haliç gönderisindeki
# kalite kaybı buydu.
#
# Kütüphanedeki 183 gerçek fotoğrafın yalnızca 31'i bu eşiği geçiyor.
# Geçemeyen konular AI fotoğraf katmanına düşüyor; orada görsel zaten
# 1024x1536 üretiliyor, yani tuvale neredeyse birebir oturuyor ve keskin.
# Bulanık gerçek fotoğraf yerine keskin üretilmiş fotoğraf tercih ediliyor.
MIN_WIDTH = 1080
MIN_HEIGHT = 1350
STOP = {"the", "a", "an", "of", "in", "on", "at", "to", "and", "for", "from",
        "turkey", "turkiye", "istanbul"}

# Yer adlarında geçen ama YERİ AYIRT ETMEYEN kelimeler. Bunlar tek başına
# eşleşme sayılırsa saçma sonuçlar çıkıyor: "Derinkuyu Underground City"
# konusuna Kapalıçarşı fotoğrafı seçilmişti, tek ortak kelime "city" olduğu
# için. "Göreme Open Air Museum" da Selçuk'un açık hava müzesiyle eşleşti.
# Eşleşmenin geçerli sayılması için EN AZ BİR ayırt edici kelime gerekiyor —
# yani özel ad: Derinkuyu, Göreme, Uçhisar.
GENERIC_PLACE = {
    "city", "town", "village", "centre", "center", "museum", "castle",
    "fortress", "house", "home", "mosque", "church", "cathedral", "palace",
    "tower", "bridge", "gate", "wall", "walls", "ancient", "old", "new",
    "underground", "open", "air", "national", "park", "beach", "coast",
    "island", "valley", "mountain", "mount", "hill", "peninsula", "region",
    "province", "district", "great", "greatest", "grand", "big", "little",
    "north", "south", "east", "west", "upper", "lower", "site", "ruins",
    "area", "view", "viewpoint", "panoramic", "guide", "tour", "tours",
    # Ulaşım noktaları ayırt edici değil: "Airport" kelimesi Sabiha Gökçen
    # fotoğrafını "Izmir Adnan Menderes Airport" gönderisine eşleştirdi.
    "airport", "terminal", "station", "port", "harbour", "harbor", "otogar",
    "square", "street", "avenue", "road", "bus", "metro", "tram",
}

# Sosyal medyada gönderi konusu OLMAYAN yerler. Havaalanı fotoğrafı kimseyi
# tura sokmaz, hesabın akışını bozar. İlk gerçek turda motor "Izmir Adnan
# Menderes Airport" gönderisi planladı; bu liste tekrarını engelliyor.
SKIP_SUBJECT = {
    "airport", "terminal", "station", "otogar", "ferry", "hospital",
    "university", "stadium", "hotel", "hostel",
}


@dataclass
class Post:
    kind: str                    # article | location | seasonal
    caption: str = ""
    image_url: str = ""
    image_alt: str = ""
    link: str = ""               # Facebook'a gider, Instagram'a gitmez
    page_url: str = ""           # gonderinin anlattigi blog/lokasyon sayfasi
    brand_url: str = ""          # konunun ait oldugu marka sitesi
    location: str = ""           # konum etiketi icin aranacak yer adi
    slides: list = field(default_factory=list)  # carousel ic slaytlari
    image_urls: list = field(default_factory=list)  # carousel gorselleri
    subject: str = ""
    source: str = ""
    status: str = "planned"      # planned | ready | published | skipped
    scheduled_for: str = ""
    skip_reason: str = ""
    needs_card: bool = False     # kütüphanede gerçek foto yok → AI fotoğraf
    photo_source: str = ""       # kutuphane | ai — panelde görünsün
    notes: list = field(default_factory=list)   # görseldeki not kartı
    published: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, ham: dict) -> "Post":
        """Kuyruktaki sozlugu geri Post nesnesine cevirir.

        Onay modunda gonderi BIR turda uretiliyor, BASKA bir turda
        yayinlaniyor. Iki tur arasindaki tek tasiyici
        data/<domain>/social.json. Gorseller o ilk turda uretilip
        WordPress medya kutuphanesine yuklendigi icin burada yeniden
        uretilmiyor: onay sonrasi yayin tek kurus model masrafi
        cikarmiyor.

        to_dict bos alanlari atiyor, o yuzden geri donusum eksik
        anahtarlara dayanikli olmali; tanimsiz anahtarlar da eleniyor
        (panel kayda edited_by gibi kendi alanlarini ekliyor).
        """
        alanlar = {f.name for f in dc_fields(cls)}
        veri = {k: v for k, v in (ham or {}).items() if k in alanlar}
        veri.setdefault("kind", "article")
        return cls(**veri)

    def to_dict(self) -> dict:
        return {k: v for k, v in vars(self).items() if v not in ("", {}, None)}


# -- görsel eşleştirme -------------------------------------------------------
def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {w for w in words if w not in STOP and len(w) > 2}


_OWN_GRAPHIC = re.compile(
    r"^social-|^\d{4}-\d{2}-\d{2}-|-(hero|facts|compare|route|card|photo)$")


def _own_graphic(slug: str) -> bool:
    """Motorun kendi ürettiği ve kütüphaneye yüklediği görsel mi?

    13 Eylül turunda Boğaz turu yazısının öne çıkan görseli, bir önceki turda
    bizim ürettiğimiz "september in istanbul" sosyal kartı oldu. Kendi
    çıktımız gerçek fotoğraf sayılmaz: sayarsak motor kendi grafiklerini geri
    dönüştürür ve AI fotoğraf katmanı hiç çalışmaz.
    """
    return bool(_OWN_GRAPHIC.search((slug or "").lower()))


def pick_photo(media: list[dict], subject: str,
               used: set[str] | None = None) -> dict | None:
    """Konuya en iyi uyan gerçek fotoğrafı seçer.

    Eşleşme hem alt metne hem dosya adına bakıyor: kütüphanedeki 325 görselin
    yalnızca 42'sinde alt metin var, sadece ona güvenmek kullanılabilir havuzu
    dokuza katlıyordu.

    Eşleşme yoksa None döner — çağıran taraf gönderiyi atlar. Rastgele bir
    İstanbul fotoğrafı koymak, Çamlıca yazısının altına Ayasofya basmak demek.
    """
    used = used or set()
    want = _tokens(subject)
    if not want:
        return None

    en_iyi, en_iyi_puan = None, 0.0
    for m in media:
        url = m.get("source_url") or ""
        if not url or url in used:
            continue
        if (m.get("width") or 0) < MIN_WIDTH:
            continue
        # Yükseklik alanı olmayan eski kayıtlar da eleniyor: ölçüsünü
        # bilmediğimiz bir görseli dikey tuvale koymak kumar.
        if (m.get("height") or 0) < MIN_HEIGHT:
            continue
        if _own_graphic(m.get("slug", "")):
            continue
        have = _tokens(m.get("alt_text", "")) | _tokens(m.get("slug", ""))
        if not have:
            continue
        ortak = want & have
        if not ortak:
            continue
        # Ortak kelimelerin hepsi genel ise bu eşleşme tesadüf: iki farklı
        # yerin ikisi de "ancient city" olabilir.
        if not (ortak - GENERIC_PLACE):
            continue
        # Kesişimin konuya oranı: "bosphorus" tek kelimelik bir konuda tam
        # isabet, üç kelimelik bir konuda üçte bir. Alt metni olan görsel
        # eşit puanda önce geliyor, çünkü doğruluğu insan eliyle yazılmış.
        ayirt = ortak - GENERIC_PLACE
        # Puanı ayırt edici kelimeler üzerinden hesaplıyoruz; genel kelimeler
        # yalnızca beraberlik bozuyor.
        istenen = want - GENERIC_PLACE or want
        puan = (len(ayirt) / len(istenen)
                + 0.05 * len(ortak & GENERIC_PLACE)
                + (0.15 if m.get("alt_text") else 0))
        if puan > en_iyi_puan:
            en_iyi, en_iyi_puan = m, puan

    # Zayıf eşleşme kabul edilmiyor. Eşik 15.09.2026'da 0.34'ten 0.5'e
    # çıkarıldı: "Types of Boat Trips and Cruises in Istanbul" gönderisine
    # tek kelime tutan bir kıyı fotoğrafı seçildi ve konuyla ilgisi yoktu.
    # Eşik altında kalan konular AI fotoğraf katmanına düşüyor — orada
    # fotoğraf yazının kendi metninden üretiliyor, yani konuya uyuyor.
    return en_iyi if en_iyi_puan >= 0.5 else None


# -- gönderi planı -----------------------------------------------------------
def plan_week(site, published: list[dict], links: list[dict],
              gecmis: list[dict], media: list[dict] | None = None,
              today: date | None = None,
              adet: int = 0,
              yalniz_makale: bool = False) -> list[dict]:
    """Bu haftanın gönderi konularını seçer — henüz metin yazmadan.

    Önce ne hakkında yazacağımıza karar veriyoruz, sonra model çağırıyoruz.
    Tersi olursa üretilen metnin yarısı çöpe gidiyor.
    """
    today = today or date.today()
    social = site.raw.get("social", {}) or {}
    mix = social.get("mix", {}) or {}
    if yalniz_makale:
        # Blog turunun sonunda çağrıldığında YALNIZCA o yazının gönderisi
        # çıkıyor. O gün yazı yoksa — ya da yazının gönderisi zaten
        # çıkmışsa — plan boş dönüyor ve tur sessizce bitiyor. Soro'nun
        # modeli bu: blog varsa gönderi var, yoksa yok. Lokasyon ve
        # mevsimlik doldurucular bu turda devre dışı; onlar sosyal turun
        # kendi işi.
        mix = {"article_derived": mix.get("article_derived", 2),
               "location_spotlight": 0, "seasonal_practical": 0}

    # Son 8 haftada kullanılan konular tekrar gelmesin — 26 gönderilik bir
    # hesapta aynı yeri iki kez göstermek fark ediliyor.
    # YALNIZCA GERÇEKTEN YAYINLANANLAR sayılıyor. Önceden kuyruktaki her kayıt
    # "yakında kullanıldı" kabul ediliyordu; yayın hatası yüzünden atlanan bir
    # konu da kalıcı olarak yanıyordu. 14.09.2026'da sarnıç yazısının gönderisi
    # fotoğraf hatası yüzünden atlandı ve konu bir daha hiç planlanmadı.
    # Instagram başarısı artık doğru kaydedildiği için "published" güvenilir.
    # 19.09.2026 EKLENDI: kuyrukta DURAN kayit da sayiliyor. Yalnizca
    # "published" bakmak ters yonde bir delik acmisti: onay modunda uretilen
    # gonderi "ready" olarak bekliyor, planlayici onu gormuyor ve bir sonraki
    # tur ayni konuyu bastan planliyordu — ikinci model cagrisi, ikinci AI
    # fotograf, kuyrukta ikiz kayit. 17.09.2026'da "Bosphorus Boat Trip"
    # tam bunu yasadi: 7 dakika arayla iki kayit, biri yayinlandi, oteki
    # kuyrukta asili kaldi. "skipped" bilerek DISARIDA — atlanan konu geri
    # gelebilmeli, 14.09'daki duzeltmenin sebebi oydu.
    KUYRUKTA = ("published", "ready", "approved")
    yakin = {p.get("subject", "").lower() for p in gecmis
             if p.get("status") in KUYRUKTA
             and p.get("scheduled_for", "") >= (today - timedelta(weeks=8)).isoformat()}

    plan: list[dict] = []

    # 1) O hafta çıkan yazılardan
    hafta_basi = (today - timedelta(days=today.weekday())).isoformat()
    # 15.09.2026 — Evren: "cikilan blog ne ise, post da onunla ilgili olmali".
    # Yayin sirasi degil, EN YENI YAZI once. Onceki turda Halic yazisi cikarken
    # gonderi bir onceki Bogaz yazisindan turetildi ve ikisi tutmadi.
    taze = sorted([p for p in published if p.get("date", "") >= hafta_basi],
                  key=lambda p: p.get("date", ""), reverse=True)
    # "yakin" filtresi ÖNCEDEN yalnızca lokasyonlara uygulanıyordu; makale ve
    # mevsimlik gönderiler her turda yeniden planlanıyordu. 14.09.2026'da
    # Instagram'da aynı "September in Istanbul" kartı iki kez yayınlandı.
    # Tekrar kontrolü artık üç kaynağın üçünde de var.
    for row in taze:
        if len([x for x in plan if x["kind"] == "article"]) >= mix.get("article_derived", 2):
            break
        if row.get("title", "").lower() in yakin:
            continue
        plan.append({"kind": "article", "subject": row.get("title", ""),
                     "link": row.get("url", ""), "source": row.get("slug", "")})

    # 2) Lokasyon sayfalarından — YALNIZCA fotoğrafı olanlar
    #
    # 198 lokasyonun yaklaşık üçte biri kütüphanede eşleşen fotoğraf bulamıyor.
    # Rastgele seçip sonra "fotoğraf yok" diye atlarsak her üç slottan biri
    # boşa gidiyor ve hafta eksik çıkıyor. Seçimi baştan fotoğrafı olanlarla
    # sınırlamak aynı işi tam kadroyla bitiriyor.
    #
    # Seçim RASTGELE DEĞİL. İlk gerçek turda rastgele seçim "Izmir Adnan
    # Menderes Airport" ve "Mount Nemrut Tumulus" gönderileri planladı —
    # biri havaalanı, diğeri İstanbul'a 1.000 km uzakta. 199 lokasyonun
    # yalnızca 22'si İstanbul'da ve sattığımız ürünler orada. Sıralama:
    #   1. kendi markamıza denk gelen İstanbul yerleri (Boğaz, Haliç, Çamlıca)
    #   2. diğer İstanbul yerleri
    #   3. İstanbul dışı — yalnızca ilk ikisi tükenirse
    lokasyon = [l for l in links if l.get("kind") == "location"
                and l.get("label", "").lower() not in yakin
                and not (set(l.get("label", "").lower().split()) & SKIP_SUBJECT)]
    random.shuffle(lokasyon)

    def _oncelik(l: dict) -> int:
        url = (l.get("url") or "").lower()
        etiket = (l.get("label") or "").lower()
        istanbul = "/istanbul/" in url
        marka = False
        for entry in (site.raw.get("link_map", {}) or {}).get("own_brands", []):
            if any(str(p).lower() in etiket for p in entry.get("match", [])):
                marka = True
                break
        if istanbul and marka:
            return 0
        if istanbul:
            return 1
        return 2

    lokasyon.sort(key=_oncelik)
    # İstanbul dışı yerler sıralamanın sonunda değil, TAMAMEN dışarıda.
    # Sadece sona atmak yetmedi: İstanbul'daki 22 yerin bir kısmının
    # fotoğrafı yok, kota dolmayınca sıra Anıtkabir'e geldi ve Ankara'daki
    # bir anıt İstanbul tur hesabından yayınlandı. Sattığımız her ürün
    # İstanbul'da; kota dolmuyorsa eksik gönderi doğru olanı.
    if not social.get("allow_outside_istanbul", False):
        lokasyon = [l for l in lokasyon if _oncelik(l) < 2]
    secilen, gereken = [], mix.get("location_spotlight", 2)
    for l in lokasyon:
        if len(secilen) >= gereken:
            break
        if media is not None and not pick_photo(media, l.get("label", "")):
            continue
        secilen.append(l)
    for l in secilen:
        plan.append({"kind": "location", "subject": l.get("label", ""),
                     "link": l.get("url", ""), "source": l.get("slug", "")})

    # 3) Mevsimlik — konusu takvimden geliyor, metni modelden
    mevsimlik = [k for k in seasonal_subjects(today) if k.lower() not in yakin]
    for konu in mevsimlik[: mix.get("seasonal_practical", 1)]:
        plan.append({"kind": "seasonal", "subject": konu, "link": "", "source": "takvim"})

    # 15.09.2026 — BIR TURDA KAC GONDERI.
    # Eski davranış: plan ne kadarsa hepsi aynı anda üretilip aynı anda
    # yayınlanıyordu. scheduled_for yalnızca kayıt içindi, yayını
    # geciktirmiyordu; 14.09.2026'da Instagram'a bir günde sekiz gönderi
    # çıkmasının sebebi buydu. Artık çağıran kaç gönderi istediğini
    # söylüyor ve sıra makaleden başlıyor: o gün çıkan yazının gönderisi
    # her zaman ilk sırada.
    if adet > 0:
        plan = plan[:adet]

    # Haftanın günlerine dağıt: hepsi aynı gün çıkarsa akış tıkanıyor
    gunler = _spread(len(plan), today)
    for post, gun in zip(plan, gunler):
        post["scheduled_for"] = gun.isoformat()
    return plan


def _spread(adet: int, today: date) -> list[date]:
    if adet <= 0:
        return []
    # Dağıtım BUGÜNDEN başlıyor, hafta başından değil: hafta ortasında
    # çalışan bir tur geçmişe tarih atıyordu.
    aralik = max(1, 7 // adet)
    return [today + timedelta(days=min(6, i * aralik)) for i in range(adet)]


SEASONAL = {
    1: ["Istanbul in winter: what stays open"],
    2: ["Shoulder-season travel: fewer crowds at the big sites"],
    3: ["Tulip season in Istanbul parks"],
    4: ["Spring on the Bosphorus: what changes on the water"],
    5: ["Long daylight, warm evenings: the best months for a sunset cruise"],
    6: ["Beating the summer heat: early starts and shaded routes"],
    7: ["Peak season crowds: when to visit the big sites"],
    8: ["Late summer on the Aegean coast"],
    9: ["September in Istanbul: the quiet best month"],
    10: ["Autumn light on the Golden Horn"],
    11: ["Rainy-day Istanbul: cisterns, hamams and covered markets"],
    12: ["New Year in Istanbul: what is open and what is not"],
}


def seasonal_subjects(today: date) -> list[str]:
    return SEASONAL.get(today.month, ["Planning a first visit to Istanbul"])


# -- metin üretimi -----------------------------------------------------------
# Metin standardı, 14.09.2026'da araştırmayla güncellendi. Dayanakları:
#   · Facebook'ta gönderi ~80 karakterden sonra "See More" ile kırpılıyor ve
#     80 karakterin altındaki gönderiler belirgin biçimde daha çok etkileşim
#     alıyor (Hootsuite, sosyal medya gönderi uzunluğu ölçümü).
#   · Instagram'da organik metnin tatlı noktası 138-150 karakter; akışta
#     görünen kısım ilk satır, gerisi "daha fazla"nın arkasında kalıyor.
#   · Etiket sayısı 3-5, her biri 24 karakterin altında ve KONUYU tarif eden
#     etiketler; #travel #love gibi genel etiketler değersiz.
#   · Her gönderide tek bir "sonraki adım" olmalı.
# Bizim eklediğimiz kural: Instagram'da tıklanabilir link YOK, o yüzden
# markanın alan adı metne DÜZ YAZI olarak kendi satırında giriyor. Facebook'a
# gerçek link zaten motor tarafından ekleniyor.
#
# 15.09.2026 — Evren'in kuralı: "cikilan blog ne ise, post da onunla ilgili
# olmali. postun detayli halini gormesi icin insanlari blog sayfasina
# yonlendirmen lazim ve de hangi sitenin urunu ya da konusu ile ilgiliyse o
# websitesinin linki vermen lazim postun detay kisminda."
# Yani her metinde İKİ satır var: yazının kendisi ve konunun ait olduğu marka
# sitesi. Bu satırları modele bırakmıyoruz — istem yine istiyor ama son sözü
# ensure_links() söylüyor, çünkü model unutursa kural delinmiş oluyor.


def brand_domain(site, konu: str) -> str:
    """Konunun ait oldugu marka sitesinin alan adi; yoksa portal alan adi."""
    try:
        from . import linkindex
        marka = linkindex.own_brand_for(site, konu)
    except Exception:
        marka = None
    if not marka:
        return site.domain
    return re.sub(r"^https?://|/$", "", marka.get("url", "")) or site.domain


# Çağrı cümlesi markaya göre değişiyor. Uydurma değil: @istambulguide'daki
# mevcut gönderilerden alındı — Golden Horn "Book your tickets at", hamam
# "Book your authentic experience at", Çamlıca "Plan your visit at",
# Boğaz "Plan your journey at", portalın kendisi "Discover more travel
# guides at" diyor.
CTA = (
    ("cruise", "Book your tickets at"),
    ("hamam", "Book your authentic experience at"),
    ("bath", "Book your authentic experience at"),
    ("tower", "Plan your visit at"),
    ("cistern", "Plan your visit at"),
)


def cta_for(brand_url: str, portal: str = "") -> str:
    """Markaya göre çağrı cümlesi.

    16.09.2026'da sıra değişti. Önceden portal kontrolü ÜSTTEYDİ: alan adı
    sitenin kendi alan adına eşitse "Discover more travel guides at" dönüyordu.
    istambul.com bir portal olduğu için orada doğruydu. Ama istanbulhamam.com
    hem site hem marka: ilk canlı gönderisinde hamam rezervasyonu satan bir
    sayfaya "Discover more travel guides at istanbulhamam.com" yazdı — yanlış
    çağrı, satış cümlesi değil.

    Artık ÖNCE markaya özel eşleşmeye bakılıyor; portal cümlesi yalnızca
    hiçbir marka anahtarı tutmayan portal alan adları için kalıyor.
    """
    alan = (brand_url or "").lower()
    for anahtar, cumle in CTA:
        if anahtar in alan:
            return cumle
    if portal and alan == portal.lower():
        return "Discover more travel guides at"
    return "Plan your visit at"


def ensure_links(caption: str, page_url: str, brand_url: str,
                 portal: str = "", carousel: bool = False) -> str:
    """Metnin sonuna iki link satırını koyar; modelin yazdıklarını temizler.

    Sıra @istambulguide'daki gönderilerle aynı: gövde, boş satır, link
    satırları, boş satır, etiketler. Model linkleri yazdıysa kendi satırları
    atılıp yeniden yazılıyor, böylece biçim her gönderide birebir aynı.
    """
    satirlar = [x.rstrip() for x in (caption or "").splitlines()]

    # Etiket bloğu en sonda; onu ayırıp linkleri araya alıyoruz.
    i = len(satirlar)
    while i > 0 and (not satirlar[i - 1].strip()
                     or satirlar[i - 1].lstrip().startswith("#")):
        i -= 1
    govde, etiketler = satirlar[:i], [x for x in satirlar[i:] if x.strip()]

    sayfa = (page_url or "").rstrip("/")
    marka = (brand_url or "").strip().lower().rstrip("/")

    def _link_satiri(x: str) -> bool:
        t = x.strip().lstrip("\U0001F310\U0001F4D6").strip()
        if not t:
            return False
        if sayfa and sayfa in t:
            return True
        if re.match(r"^(full guide|read the full|more at)\b", t, re.I):
            return True
        if re.match(r"^swipe\b", t, re.I):
            return True
        # Modelin tek başına bıraktığı çıplak alan adı satırı.
        sade = re.sub(r"^https?://", "", t.lower()).rstrip("/")
        if marka and (sade == marka or t.lower().endswith(" " + marka)):
            return True
        return False

    govde = [x for x in govde if not _link_satiri(x)]
    while govde and not govde[-1].strip():
        govde.pop()

    out = list(govde)
    if carousel:
        # Kaydırma ipucu: hesabın kendi carousel gönderilerinde de var
        # ("Swipe left to compare..."). Kaydırma, carousel'in kaydetme
        # avantajının çalışması için gereken hareket.
        out.append("")
        out.append("Swipe for both sides \u2192")
    if page_url or brand_url:
        out.append("")
    if page_url:
        out.append(f"Full guide: {page_url}")
    if brand_url:
        out.append(f"\U0001F310 {cta_for(brand_url, portal)} {brand_url}")
    if etiketler:
        out.append("")
        out.extend(etiketler)
    return "\n".join(out).strip()


# -- Facebook ---------------------------------------------------------------
# 15.09.2026'ya kadar Facebook'a Instagram metninin AYNISI gidiyordu. Yanlıştı:
# "Swipe for both sides →" Facebook'ta kaydırılacak bir şey olmadığı için
# anlamsız, "Save this for your trip 💾" bir Instagram deyimi, 5 etiket
# Facebook'ta ölçülen olarak zarar veriyor (3-5 etiketli gönderiler 416
# etkileşim, 10+ etiketliler 188 — Sendible 2026), ve en önemlisi metindeki
# tıklanabilir link Facebook'un en çok cezalandırdığı şey (%0,05 etkileşim,
# fotoğrafın üçte biri — Socialinsider 2026, 25M gönderi).
#
# Metin yeniden ÜRETİLMİYOR, Instagram metninden türetiliyor: ikinci bir model
# çağrısı gönderi başına maliyeti ikiye katlardı ve iki metnin konusunun
# ayrışması riskini doğururdu. Kural seti: claude/facebook-kural-seti.md

_FB_AT = re.compile(
    r"^(save\s+this|swipe\b|full\s+guide\b|read\s+the\s+full\b|more\s+at\b|"
    r"link\s+in\s+bio|tap\s+the\s+link)", re.I)
_FB_URL = re.compile(r"https?://\S+|\bwww\.\S+")
_FB_ALAN = re.compile(r"\b[a-z0-9][a-z0-9-]*\.(?:com|net|org|co|travel|info)\b",
                      re.I)
_FB_ISARET = "\U0001F310\U0001F4D6\U0001F4BE\U0001F517→ "


def _fb_ilk_satir(satirlar: list[str]) -> list[str]:
    """İlk satır 80 karakteri aşıyorsa CÜMLE SINIRINDAN böler.

    Facebook gönderiyi ~80 karakterde "See More" ile kesiyor. Kelime ortasından
    kesmek yerine, 82 karaktere kadar bir cümle sonu varsa orada bölüyoruz;
    yoksa satıra dokunmuyoruz — yarım cümle bırakmak kesilmekten kötü.
    """
    if not satirlar:
        return satirlar
    ilk = satirlar[0]
    if len(ilk) <= 80:
        return satirlar
    kes = -1
    for m in re.finditer(r"[.!?]\s", ilk):
        if m.end() <= 82:
            kes = m.end()
        else:
            break
    if kes > 20:
        return [ilk[:kes].rstrip(), ilk[kes:].lstrip()] + satirlar[1:]
    return satirlar


def facebook_caption(caption: str, azami_etiket: int = 3) -> str:
    """Instagram metninden Facebook metnini üretir.

    Atılanlar: kaydet çağrısı, kaydırma satırı, link satırları, metindeki her
    URL ve çıplak alan adı. Kalan: kanca, gövde, soru. Etiket en fazla 3.
    """
    govde: list[str] = []
    etiketler: list[str] = []
    for ham in (caption or "").splitlines():
        t = ham.strip()
        if not t:
            govde.append("")
            continue
        if t.startswith("#"):
            etiketler.extend(x for x in t.split() if x.startswith("#"))
            continue
        if _FB_AT.match(t.lstrip(_FB_ISARET).strip()):
            continue
        if _FB_URL.search(t) or _FB_ALAN.search(t):
            continue
        govde.append(ham.rstrip())

    while govde and not govde[0].strip():
        govde.pop(0)
    while govde and not govde[-1].strip():
        govde.pop()
    govde = _fb_ilk_satir(govde)

    metin = re.sub(r"\n{3,}", "\n\n", "\n".join(govde)).strip()
    if etiketler:
        metin = f"{metin}\n\n" + " ".join(etiketler[:azami_etiket])
    return metin.strip()


def facebook_comment(page_url: str, brand_url: str, portal: str = "") -> str:
    """Gönderinin ALTINA yazılacak ilk yorum — linkler burada.

    Meta 17.12.2025'ten beri Sayfalara aylık link sınırı test ediyor ve kendi
    açıklamasında yorumdaki linkleri sınırın dışında tutuyor. Gönderi de böylece
    saf fotoğraf/albüm gönderisi olarak kalıyor.
    """
    satir = []
    if page_url:
        satir.append(f"Full guide → {page_url}")
    marka = (brand_url or "").strip().rstrip("/")
    if marka and marka.lower() not in (page_url or "").lower():
        adres = marka if marka.startswith("http") else f"https://{marka}"
        satir.append(f"{cta_for(marka, portal)} → {adres}")
    return "\n".join(satir)


PROMPT = """You write the social posts for {handle}, the Instagram and Facebook
account of {domain} — an English-language travel guide to Türkiye.

Write one caption for each subject below. Each subject line ends with the page that
subject is about and the website it belongs to; use exactly those in that
caption.

SHAPE — follow it exactly, four parts separated by blank lines:

1. HOOK. One line. AIM FOR UNDER 80 CHARACTERS; 125 is the hard limit.
   Under 80 matters because the same first line goes to Facebook, where the
   post is cut after about 80 characters and posts that stay under it are
   measured getting markedly more engagement. Instagram hides anything past
   125 behind "more". This is the only line most people ever see. Make it a concrete, useful statement — a time, a
   distance, what you actually see, what surprises people. Never a slogan,
   never "Discover the magic of...", never a question that means nothing.
2. BODY. One or two short sentences, 150-300 characters in total. Add the
   detail the hook promised: what happens, what it costs you in time, who it
   suits, what to do first.
3. ENGAGEMENT. Two short lines, a blank line apart, in this order:
      a question about this subject that a traveller would actually answer,
      ending with 👇
      Save this for your trip 💾   (vary the wording)
   These two earn the comments and the saves. Saves are the second heaviest
   ranking signal on Instagram and sends are the heaviest, so the question
   must be worth answering, not "what do you think?".
4. THE LINKS. Two lines, in this order, nothing else on either line:
      Full guide: <the page given for that subject>
      🌐 <a short call to action> <the domain given for that subject>
   The first line sends people to the article that explains the subject in
   full; the second sends them to the brand site that subject belongs to.
   If a subject has no page, write only the second line. Instagram captions
   cannot carry clickable links, so both are plain text. Fit the call to
   action to the subject: "Plan your visit at", "Book your tickets at",
   "Plan your journey at", "Discover more travel guides at".
5. HASHTAGS. Three to five, each under 24 characters, each naming the place,
   the neighbourhood, the activity or the city. Specific labels only —
   no #travel, #love, #instagood, #viral.

Then, AFTER the caption, TWO lines that each start with SLAYT: — these are
the two carousel slides:

  SLAYT: <title, one to three words> | <Label>: <value> | <Label>: <value>

The two slides are the two halves of the subject: the two options, the two
ways of doing it, the two stages, or before and after. Two or three
"Label: value" pairs each.

EVERY VALUE MUST BE SOMETHING A TRAVELLER CAN ACT ON — a route, a time, a
door to use, a thing to bring, a mistake to avoid, a number that changes a
decision. Write the sentence a friend who lives there would say.

Good labels: "Getting there", "Time needed", "Best time", "Watch for",
"Start at", "Bring", "Skip if", "Pair with", "Book ahead".
BANNED labels — they only ever produce filler: "The vibe", "Atmosphere",
"Mood", "Overall", "Why go", "Highlights", "Experience".

Good values:
  Getting there: Tunel funicular from Karakoy, then five minutes uphill
  Watch for: The last lift up is 30 minutes before closing
  Start at: Eminonu pier 3, the one furthest from the spice market
Rejected values — vague, could be said about anywhere:
  Tourists everywhere / Sacred and peaceful / A must-see / Worth the trip
  Beautiful views / Very popular / Great atmosphere

Each value 25-70 characters. Same ban as the image card: no prices, no
ticket costs. A time of day or a "board an hour before sunset" tip is fine;
a price list is not.
If the subject genuinely has no two halves, write the two slides as two
different angles on it rather than forcing a split. If you cannot fill a
slide with values this concrete, write no SLAYT lines at all — the post
then goes out as a single image, which is better than a carousel of filler.

Then a line containing only KART: followed by one to three
short lines that will be printed ON THE IMAGE. Each line starts with "- " and
is under 55 characters.

What belongs on the image: a practical tip, a rule of etiquette, a surprising
fact, a "know before you go" note. Examples of the right register:
  - Remove your shoes before stepping on carpets
  - Keep voices low, cameras silent
  - The Medusa heads were set upside down on purpose
  - Ferries leave from two different piers

What must NEVER go on the image: prices, ticket costs, opening hours, durations
or any booking detail. That is the website's job, not a social post's — a price
list printed on a photograph looks like an advertisement and dates instantly.
If you have nothing genuinely useful or interesting to say, write KART: and
leave it empty rather than filling it.

Hard rules:
- Never invent a fact, price, opening time, duration or claim. If you do not
  know a number, describe what the place IS instead of stating one.
- At most one emoji in the hook and body together, and only if it adds
  meaning. The 🌐 on the link line does not count.
- No "click here", no pasted URL beyond the plain domain line, no "link in
  bio" more than once across the whole set.
- Each caption must be clearly different from the others in wording, opening
  and rhythm. Do not start two captions the same way.
- NEVER write the words HOOK, BODY, THE LINKS or HASHTAGS in the caption.
  They name the parts for you; they are not part of the text. Write the
  caption as a reader would see it.

{ev_kurallari}
Subjects:
{subjects}

Return one block per subject, in the same order, separated by a line containing
only ---
"""


# Ölçü iddiası kalıpları. Sosyal metinde kalite kapısı YOKTU ve bu bir boşluk
# olarak kaldı: 14.09.2026'da Çamlıca gönderisi "360 meters" (doğrusu 369) ve
# "three continents" (İstanbul iki kıtada) diyerek yayına çıktı. Blogdaki
# doğrulanmamış rakam kuralının sosyal tarafta karşılığı yoktu.
_OLCU = re.compile(
    r"\b(\d[\d.,]*)\s*(meters?|metres?|m\b|km|kilometers?|minutes?|mins?\b|"
    r"hours?|hrs?\b|floors?|storeys?|stories|steps|years?)", re.I)
_KITA = re.compile(r"\b(one|two|three|four|five|\d+)\s+continents?\b", re.I)


def caption_issues(site, subject: str, caption: str,
                   kaynak: str = "") -> list[str]:
    """Metindeki doğrulanmamış ölçü ve olgu iddialarını döndürür.

    Kural blogdakiyle aynı: fact base'de karşılığı olmayan rakam yayına
    çıkamaz. Burada ek olarak "kaç kıta" iddiası da denetleniyor — İstanbul
    iki kıtada, ve bu seyahat metinlerinde sık yapılan bir hata.
    """
    sorunlar: list[str] = []

    # İstanbul iki kıtada. Bu rakam fact base'e bakmadan bilinir ve
    # yanlışı doğrudan güven kaybıdır.
    for m in _KITA.finditer(caption):
        if m.group(1).lower() not in ("two", "2"):
            sorunlar.append(f"kıta sayısı yanlış: {m.group(0)}")

    # Ölçü iddiaları: markanın doğrulanmış bloğunda geçmiyorsa reddedilir.
    try:
        from . import linkindex
        marka = linkindex.own_brand_for(site, subject)
    except Exception:
        marka = None
    # Yazının kendi metni de doğrulama kaynağı. Blog kalite kapısından
    # geçerek yayınlandı; orada duran bir rakamı sosyal metinde reddetmek
    # gönderiyi boşuna düşürüyor.
    dogrulanmis = re.sub(r"<[^>]+>", " ", kaynak or "")
    if marka:
        anahtar = re.sub(r"^https?://|/$", "", marka.get("url", "")).split(".")[0]
        blok = (site.facts.get("own_products") or {}).get(anahtar)
        if isinstance(blok, dict):
            # default=str: fact base'de tarih nesneleri var, JSON'a
            # dökülemiyor; metne çevirmek yeterli, karşılaştırma
            # zaten dize arama.
            dogrulanmis += " " + json.dumps(blok, ensure_ascii=False,
                                            default=str)
    for m in _OLCU.finditer(caption):
        sayi = m.group(1).replace(",", "")
        if sayi not in dogrulanmis:
            sorunlar.append(f"doğrulanmamış ölçü: {m.group(0).strip()}")
    return sorunlar


def build_request(site, plan: list[dict], model: str | None = None) -> dict:
    from . import talimat
    social = site.raw.get("social", {}) or {}
    def _alan(konu: str) -> str:
        return brand_domain(site, konu)

    listing = "\n".join(
        f"{i+1}. [{p['kind']}] {p['subject']}"
        + (f" — page: {p['link']}" if p.get("link") else "")
        + f" — site: {_alan(p['subject'])}"
        for i, p in enumerate(plan))
    return {
        "model": model or site.globals["models"]["utility"],
        "max_tokens": 1500,
        "messages": [{"role": "user", "content": PROMPT.format(
            handle="@" + social.get("instagram_handle", ""),
            domain=site.domain, subjects=listing,
            # Panelden yazilan site talimati. Sert kurallardan SONRA giriyor
            # ve kalite kapisi ondan sonra da calisiyor: talimat bicimi
            # degistirir, dogrulamayi kaldirmaz.
            ev_kurallari=talimat.blok(site, "sosyal metin"))}],
    }


_BOLUM = re.compile(
    r"^\s*(?:\d[\.\)]\s*)?(?:HOOK|BODY|THE LINKS|LINKS|HASHTAGS|"
    r"CAPTION|CTA|THE WEBSITE)\s*(?:[:.\-\u2013]\s*|$)", re.I)


def strip_labels(caption: str) -> str:
    """Modelin metne yazdığı bölüm başlıklarını atar.

    15.09.2026'da Instagram'a başında "HOOK" ve "BODY" satırları olan bir
    gönderi çıktı: istemdeki bölüm adları metnin parçası sanılmıştı. İstem
    artık açıkça yasaklıyor ama son söz burada — model unutursa temizleniyor.
    """
    out = []
    for satir in (caption or "").splitlines():
        temiz = _BOLUM.sub("", satir)
        # Yalnızca etiketten ibaret satır tamamen düşüyor.
        if not temiz.strip() and satir.strip():
            continue
        out.append(temiz)
    return "\n".join(out).strip()


# "a Istanbul" → "an Istanbul". 15.09.2026'da yayına "sits beneath a Istanbul
# street corner" çıktı. Kural kesin olduğu için modele bırakılmıyor. "u" ile
# başlayan kelimeler dışarıda: "a university", "a unique view" doğru. "eu-",
# "one" ve "once" de öyle: "a European city", "a one-way ticket".
_ARTIKEL = re.compile(r"\b([Aa]) (?=(?![Ee]u|[Oo]ne\b|[Oo]nce\b)[aeioAEIO])")


def fix_articles(caption: str) -> str:
    """Sesli harften önce "a" yerine "an"."""
    return _ARTIKEL.sub(
        lambda m: ("an " if m.group(1) == "a" else "An "), caption or "")


def _mesafe(a: str, b: str) -> int:
    """Levenshtein — yalnızca yazım hatası ayıklamak için, kısa kelimelerde."""
    onceki = list(range(len(b) + 1))
    for i, ka in enumerate(a, 1):
        simdi = [i]
        for j, kb in enumerate(b, 1):
            simdi.append(min(onceki[j] + 1, simdi[j - 1] + 1,
                             onceki[j - 1] + (ka != kb)))
        onceki = simdi
    return onceki[-1]


_ETIKET_KELIME = re.compile(r"[A-Z]?[a-z]+|\d+")
_GOVDE_KELIME = re.compile(r"[A-Za-zÀ-ÿĞÜŞİÖÇğüşıöç]{4,}")


def fix_hashtags(caption: str, metin: str) -> str:
    """Etiketlerdeki yazım hatasını yazının metnine bakarak düzeltir.

    15.09.2026'da "#UndergoundArchitecture" yayına çıktı; gövdede
    "underground" doğru yazılıydı. Etiketteki bir kelime metinde yoksa ama
    metinde ona bir-iki harf uzaklıkta bir kelime varsa, doğrusu metindeki
    kabul ediliyor. Metinde benzeri de yoksa etikete DOKUNULMUYOR: semt ve
    yer adları (#Fatih) gövdede geçmeyebilir ve yanlış da değildir.
    """
    havuz = {w.lower() for w in _GOVDE_KELIME.findall(metin or "")}
    if not havuz:
        return caption or ""

    def _duzelt(m):
        # ÖNCE BÜTÜN ETİKETE BAK. 16.09.2026'da "#SultansHmet" yayına çıktı:
        # kelime ayırıcı etiketi "Sultans" + "Hmet" diye ikiye böldüğü için iki
        # parça da metindeki "sultanahmet" kelimesine uzak kaldı ve hiçbiri
        # düzeltilmedi. Bütün etiket tek bir kelimeye yakınsa doğrusu odur.
        tam = m.group(1).lower()
        if tam not in havuz and len(tam) >= 6:
            yakin_tam = [w for w in havuz if abs(len(w) - len(tam)) <= 3
                         and _mesafe(tam, w) <= 3]
            if yakin_tam:
                d = min(yakin_tam, key=lambda w: _mesafe(tam, w))
                return "#" + d.capitalize()
        yeni = []
        for k in _ETIKET_KELIME.findall(m.group(1)):
            if len(k) < 6 or k.lower() in havuz:
                yeni.append(k)
                continue
            yakin = [w for w in havuz if abs(len(w) - len(k)) <= 2
                     and _mesafe(k.lower(), w) <= 2]
            if yakin:
                d = min(yakin, key=lambda w: _mesafe(k.lower(), w))
                yeni.append(d.capitalize() if k[:1].isupper() else d)
            else:
                yeni.append(k)
        return "#" + "".join(yeni)

    return re.sub(r"#([A-Za-z0-9]+)", _duzelt, caption or "")


_SWIPE = re.compile(r"^[ \t]*swipe\b.*$", re.I | re.M)


def fix_swipe(caption: str, carousel: bool) -> str:
    """Kaydırma satırını GERÇEK kare sayısına göre düzeltir.

    16.09.2026: tek kareli bir gönderide "Swipe for both sides →" satırı
    yayına çıktı. Sebep sıralama: satır metin kurulurken ekleniyor, ama o anda
    slaytlar henüz üretilmemiş oluyor. Düz düzende slayt hiç basılmayınca
    metin olmayan bir kareye işaret etti.

    Bu yüzden karar görseller hazırlandıktan SONRA veriliyor: kare sayısı
    ikiden azsa satır atılıyor.
    """
    metin = caption or ""
    if not carousel and _SWIPE.search(metin):
        metin = _SWIPE.sub("", metin)
        metin = re.sub(r"\n{3,}", "\n\n", metin)
    return metin.strip()


_SLAYT = re.compile(r"^\s*SLAYT:\s*(.+)$", re.I | re.M)

# 15.09.2026 — Evren: "bu yazilarini icerigini pek begenmedim".
# Slaytta "The vibe: Tourists everywhere" gibi satırlar çıktı: hiçbir
# yolcunun işine yaramayan, her yer için söylenebilecek dolgu. İstem artık
# somut örneklerle anlatıyor ama son söz burada: boş etiket ve kısa değer
# slayttan atılıyor, hiç geçerli satır kalmazsa slayt hiç basılmıyor ve
# gönderi tek görsel çıkıyor.
_BOS_ETIKET = re.compile(
    r"^(the\s+)?(vibe|atmosphere|mood|overall|why\s+go|highlights?|"
    r"experience|feel|feeling|summary)$", re.I)
_BOS_DEGER = re.compile(
    r"^(a\s+)?(must[- ]see|must[- ]do|worth (it|the trip|a visit)|"
    r"very popular|beautiful views?|great atmosphere|unforgettable|"
    r"tourists everywhere|crowded|busy|quiet|peaceful)\.?$", re.I)


def slayt_satiri_gecerli(etiket: str, deger: str) -> bool:
    """Slayt satırı yolcunun işine yarıyor mu?"""
    etiket, deger = (etiket or "").strip(), (deger or "").strip()
    if not etiket or _BOS_ETIKET.match(etiket):
        return False
    if _BOS_DEGER.match(deger):
        return False
    # 25 karakterin altındaki değer bir cümle değil, bir sıfat.
    return len(deger) >= 25


def split_slides(block: str) -> tuple[str, list[dict]]:
    """SLAYT: satırlarını ayırır ve bloktan çıkarır.

    Carousel iç slaytları. Metricool'un 2026 ölçümünde carousel gönderiler
    tek görsele göre dokuz kat fazla kaydetme alıyor; kaydetme
    Instagram'ın en ağır ikinci sıralama sinyali.
    """
    slaytlar = []
    for ham in _SLAYT.findall(block or ""):
        parcalar = [p.strip() for p in ham.split("|") if p.strip()]
        if len(parcalar) < 2:
            continue
        satirlar = []
        for p in parcalar[1:]:
            etiket, _, deger = p.partition(":")
            if slayt_satiri_gecerli(etiket, deger):
                satirlar.append([etiket.strip(), deger.strip()])
        # En az iki geçerli satır yoksa slayt dolgu demektir.
        if len(satirlar) >= 2:
            slaytlar.append({"title": parcalar[0][:40], "rows": satirlar[:3]})
    return _SLAYT.sub("", block or "").strip(), slaytlar[:2]


def split_note(block: str) -> tuple[str, list[str]]:
    """Blok metnini (caption, görsele basılacak not satırları) diye ayırır."""
    parca = re.split(r"\n\s*KART:\s*", block, maxsplit=1)
    # Model son blogu "---" ile kapatirsa parse bunu ayiramiyor ve ayirac
    # metnin sonunda yayina cikiyor (14.09.2026, Bogaz aksam turu gonderisi).
    caption = re.sub(r"\n\s*[-\u2014]{2,}\s*$", "", parca[0].strip()).strip()
    caption = strip_labels(caption)
    notlar: list[str] = []
    if len(parca) > 1:
        for satir in parca[1].splitlines():
            satir = satir.strip().lstrip("-•").strip()
            if satir:
                notlar.append(satir[:70])
    # Eleme sonrası üçe iniyor: burada kesersek yasaklı satırlar
    # iyi satırların yerini yiyor.
    return caption, notlar[:6]


# Görsele fiyat, saat ve süre basılmaz. Model kuralı unutursa burada eleniyor:
# 14.09.2026'da çıkan Çamlıca gönderisinde kartta "4 hours · from €159" ve
# "Open 10:00-22:00" yazıyordu.
_KART_YASAK = re.compile(
    r"(€|\$|£|\bTL\b|\bfrom \d|\bprice|\bticket|\bcost|\d{1,2}[:.]\d{2}"
    r"|\bopen\b|\bhours?\b|\bminutes?\b|\bmins?\b)", re.I)


def clean_notes(notlar: list[str]) -> list[str]:
    return [n for n in notlar if not _KART_YASAK.search(n)][:3]


def parse_captions(text: str, plan: list[dict]) -> list[str]:
    parcalar = [p.strip() for p in re.split(r"\n\s*---\s*\n", text) if p.strip()]
    # Model bir bloğu atlarsa sırayla eşleştirmek bütün listeyi kaydırır;
    # eksik olanı boş bırakıp çağırana bildiriyoruz.
    return parcalar + [""] * (len(plan) - len(parcalar))


def assemble(site, plan: list[dict], captions: list[str],
             media: list[dict]) -> list[Post]:
    """Plan + metin + fotoğraf → gönderi. Fotoğrafı olmayan gönderi atlanır."""
    gerekli = (site.raw.get("social", {}) or {}).get("require_real_photo", True)
    kullanilan: set[str] = set()
    out: list[Post] = []
    for item, caption in zip(plan, captions):
        post = Post(kind=item["kind"], subject=item["subject"],
                    link=item.get("link", ""), source=item.get("source", ""),
                    scheduled_for=item.get("scheduled_for", ""),
                    page_url=item.get("link", ""),
                    brand_url=brand_domain(site, item["subject"]),
                    location=item.get("location", "") or item["subject"],
                    caption=caption.strip())
        post.caption, post.slides = split_slides(post.caption)
        post.caption, post.notes = split_note(post.caption)
        post.notes = clean_notes(post.notes)
        if not post.caption:
            post.status, post.skip_reason = "skipped", "model metin vermedi"
            out.append(post)
            continue
        # Linkler metin boş DEĞİLSE ekleniyor: önce eklersek boş metin de
        # dolu görünür ve içi yalnız iki linkten ibaret gönderi yayına çıkar.
        post.caption = ensure_links(post.caption, post.page_url,
                                    post.brand_url, site.domain,
                                    carousel=bool(post.slides))
        foto = pick_photo(media, item["subject"], kullanilan)
        if foto:
            post.image_url = foto.get("source_url", "")
            post.image_alt = foto.get("alt_text", "") or item["subject"]
            kullanilan.add(post.image_url)
            post.status = "ready"
        elif gerekli:
            post.status = "skipped"
            post.skip_reason = ("konuya uyan gerçek fotoğraf bulunamadı — "
                                "üretilmiş görsel Instagram'a çıkmıyor")
        else:
            # Fotoğraf yoksa gönderiyi düşürmek yerine markalı kart basılıyor.
            # image_url'ü BOŞ bırakıp "ready" demek olmaz: Instagram görselsiz
            # gönderi kabul etmiyor, yayın adımında hata alırdık. Kartı
            # run_social üretip WordPress'e yüklüyor, URL oradan geliyor.
            post.status = "ready"
            post.needs_card = True
        out.append(post)
    return out


# -- kuyruk dosyası ----------------------------------------------------------
def load_queue(site) -> dict:
    p: Path = site.data_dir / "social.json"
    if not p.exists():
        return {"posts": [], "history": []}
    return json.loads(p.read_text(encoding="utf-8"))


def save_queue(site, data: dict) -> None:
    (site.data_dir / "social.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
