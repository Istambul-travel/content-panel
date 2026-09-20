"""Görsel üretimi — fact base'den çizilen özgün grafikler.

Neden fotoğraf değil de grafik:
  · Bu ortamda fotoğraf üretilemiyor; üretilebilse bile sitenin kendi kuralı
    (avoid: "No AI-generated images") AI fotoğrafı yasaklıyor. Gerçek yapıların
    yapay fotoğrafı hep hafif yanlış çıkıyor ve seyahat rehberinde bu güven
    kaybı demek.
  · Stok fotoğraf istenmedi.
  · Geriye gerçekten üretilebilecek ve gerçekten değerli olan şey kalıyor:
    yazının kendi doğrulanmış verisinin görselleştirilmesi. Rota şeması,
    "bir bakışta" kartı, karşılaştırma tablosu. Bunlar siteye özgü, telifsiz,
    ve stok fotoğrafın aksine alıntılanabilir bilgi taşıyor.

Her grafik SADECE facts.yaml'da verified: true olan veriden çiziliyor. Kaynakta
olmayan rakam görsele de giremez — kuralın metinde olduğu gibi görselde de
geçerli olması gerekiyor, yoksa kapı boşuna çalışıyor.

SVG üretilip cairosvg ile PNG'ye çevriliyor: Actions üzerinde tarayıcı kurmaya
gerek yok, tek pip paketi yetiyor.
"""
from __future__ import annotations

import html
import io
import re
from dataclasses import dataclass, field
from datetime import date

FONT = "DejaVu Sans, Liberation Sans, Arial, sans-serif"

# Panelle aynı palet — çıktı tek bir markanın işi gibi dursun
INK = "#0f2e28"
DEEP = "#0d3b33"
BRAND = "#146353"
ACCENT = "#c8a24a"
PAPER = "#f6f4ef"
MUTED = "#7c8b86"

# --- Instagram marka sistemi -------------------------------------------------
# Renkler ve düzen @istambulguide hesabındaki mevcut gönderilerden alındı:
# krem zemin, kırmızı organik blok, ağır sıkışık büyük harf başlık, italik
# serif alt satır, altta ISTAMBUL.COM kelime markası. Panelin yeşil paleti
# burada KULLANILMIYOR — hesabın tarzı kırmızı/krem.
IG_RED = "#d6203c"
IG_CREAM = "#f2eee6"
IG_INK = "#14181c"
HEAD_FONT = "DejaVu Sans Condensed, Liberation Sans Narrow, DejaVu Sans, sans-serif"

# Hesap her markada farklı bir vurgu rengi kullanıyor: Boğaz gönderilerinde
# mavi, hamamda altın, Haliç'te turkuaz, Çamlıca ve genel İstanbul
# gönderilerinde kırmızı. Renk hesabın kendi gönderilerinden okundu, uydurma
# değil. Eşleşme yoksa kırmızı.
BRAND_ACCENT = {
    "bosporuscruise": "#29a3e3",
    "goldenhorncruise": "#d8a13a",
    "istanbulhamam": "#c9972f",
    "istanbulturkishbath": "#c9972f",
    "camlicatower": "#d6203c",
    "cisternoftheodosius": "#8a5a2b",
}
SERIF_FONT = "DejaVu Serif, Liberation Serif, serif"
LINE = "#dcd8cf"


@dataclass
class Visual:
    kind: str
    svg: str
    alt: str
    width: int
    height: int
    caption: str = ""

    def png(self) -> bytes:
        import cairosvg
        buf = io.BytesIO()
        cairosvg.svg2png(bytestring=self.svg.encode("utf-8"), write_to=buf,
                         output_width=self.width, output_height=self.height)
        return buf.getvalue()

    def jpeg(self, kalite: int = 88, azami_mb: float = 1.5) -> bytes:
        """Instagram'a JPEG gidiyor, PNG değil.

        Ölçülen kural (Instagram görsel rehberi 2026): JPEG kalite %85-90 en
        iyisi — %100 DAHA KÖTÜ sonuç veriyor, çünkü büyüyen dosya
        Instagram'ın ikinci sıkıştırma geçişini tetikliyor. 1,5 MB üstü
        dosyalar da aynı geçişi yiyor. 1080x1350 PNG bizde 2 MB'ı aşıyordu,
        yani her gönderi iki kez sıkıştırılıyordu.

        Renk profili sRGB: Instagram başka profili atıyor ve renkler soluk
        çıkıyor. Pillow RGB'ye çevirince zaten sRGB kabul ediliyor.
        """
        from PIL import Image
        im = Image.open(io.BytesIO(self.png())).convert("RGB")
        for q in (kalite, 82, 76, 70):
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=q, optimize=True,
                    progressive=True, subsampling=0)
            if buf.tell() <= azami_mb * 1024 * 1024:
                return buf.getvalue()
        return buf.getvalue()


def esc(text) -> str:
    return html.escape(str(text), quote=True)


def fit(text: str, max_px: float, font_size: float) -> str:
    """Metni sütuna sığdır, sığmıyorsa kısalt.

    SVG'de metin taşması diye bir uyarı yok: uzun bir hamam adı sessizce yan
    sütunun üstüne biniyor ve tablo okunmaz hâle geliyordu. DejaVu Sans'ta
    ortalama karakter genişliği ~0.55em; kaba ama bu iş için yeterli.
    """
    text = str(text)
    budget = int(max_px / (font_size * 0.55))
    if len(text) <= budget:
        return text
    return text[: max(budget - 1, 1)].rstrip(" ,-") + "…"


def wrap(text: str, per_line: int) -> list[str]:
    """Kaba ama öngörülebilir satır bölme. SVG'de otomatik sarma yok."""
    words, lines, current = str(text).split(), [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > per_line and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def skyline(y: int, width: int, opacity: float = 0.16) -> str:
    """Soyut kubbe-minare silüeti.

    Belirli bir yapının kopyası değil: geometrik, jenerik bir siluet. Amaç
    kartın boş durmaması, bir binayı taklit etmek değil.
    """
    parts = [f'<g fill="#ffffff" opacity="{opacity}">']
    x = -40
    step = 104          # daha sık: aralıklı dizilince mezar taşı gibi duruyordu
    i = 0
    while x < width + 60:
        dome_r = 46 if i % 2 == 0 else 32
        base_h = 96 if i % 2 == 0 else 64
        parts.append(
            f'<rect x="{x}" y="{y - base_h}" width="{dome_r * 2}" '
            f'height="{base_h}" rx="4"/>'
            f'<path d="M{x} {y - base_h} a{dome_r} {dome_r} 0 0 1 {dome_r * 2} 0 z"/>'
        )
        if i % 2 == 0:
            parts.append(
                f'<rect x="{x - 14}" y="{y - base_h - 62}" width="7" '
                f'height="{base_h + 62}" rx="3"/>'
                f'<path d="M{x - 14} {y - base_h - 62} l3.5 -16 l3.5 16 z"/>')
        x += step
        i += 1
    parts.append("</g>")
    return "".join(parts)


def hero(site, title: str, label: str) -> Visual:
    """Öne çıkan görsel. 1200x630 — sosyal paylaşım ve arama önizleme ölçüsü."""
    w, h = 1200, 630
    lines = wrap(title, 30)[:3]
    y0 = 300 - (len(lines) - 1) * 34
    body = "".join(
        f'<text x="80" y="{y0 + i * 74}" font-family="{FONT}" font-size="60" '
        f'font-weight="700" fill="#ffffff">{esc(line)}</text>'
        for i, line in enumerate(lines))
    return Visual(
        kind="hero",
        width=w, height=h,
        alt=f"{title} — title card for {site.domain}",
        svg=f'''<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}"
  viewBox="0 0 {w} {h}">
  <defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="{DEEP}"/><stop offset="1" stop-color="{INK}"/>
  </linearGradient></defs>
  <rect width="{w}" height="{h}" fill="url(#g)"/>
  {skyline(h, w)}
  <rect x="0" y="{h - 74}" width="{w}" height="74" fill="{INK}" opacity="0.55"/>
  <rect x="80" y="86" width="54" height="4" fill="{ACCENT}"/>
  <text x="80" y="130" font-family="{FONT}" font-size="21" letter-spacing="3"
    fill="{ACCENT}">{esc(fit(label.upper(), 900, 21))}</text>
  {body}
  <text x="80" y="{h - 28}" font-family="{FONT}" font-size="24"
    fill="#ffffff" opacity="0.9">{esc(site.domain)}</text>
</svg>''')


def facts_card(site, heading: str, rows: list[tuple[str, str]],
               note: str = "") -> Visual:
    """'Bir bakışta' kartı — doğrulanmış verinin tablosu."""
    w = 1200
    h = 240 + len(rows) * 92 + (60 if note else 0)
    body = []
    y = 250
    for label, value in rows:
        body.append(
            f'<text x="80" y="{y}" font-family="{FONT}" font-size="23" '
            f'fill="{MUTED}">{esc(label)}</text>'
            f'<text x="{w - 80}" y="{y}" font-family="{FONT}" font-size="34" '
            f'font-weight="700" text-anchor="end" fill="{INK}">{esc(value)}</text>'
            f'<line x1="80" y1="{y + 30}" x2="{w - 80}" y2="{y + 30}" '
            f'stroke="{LINE}" stroke-width="2"/>')
        y += 92
    if note:
        body.append(
            f'<text x="80" y="{y + 18}" font-family="{FONT}" font-size="21" '
            f'fill="{MUTED}">{esc(note)}</text>')
    alt_bits = ", ".join(f"{a}: {b}" for a, b in rows[:4])
    return Visual(
        kind="facts", width=w, height=h,
        alt=f"{heading} — {alt_bits}",
        caption=heading,
        svg=f'''<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}"
  viewBox="0 0 {w} {h}">
  <rect width="{w}" height="{h}" fill="{PAPER}"/>
  <rect x="0" y="0" width="{w}" height="10" fill="{BRAND}"/>
  <text x="80" y="120" font-family="{FONT}" font-size="42" font-weight="700"
    fill="{INK}">{esc(heading)}</text>
  <rect x="80" y="150" width="54" height="4" fill="{ACCENT}"/>
  {"".join(body)}
</svg>''')


def compare_card(site, heading: str, columns: list[str],
                 rows: list[list[str]]) -> Visual:
    """Karşılaştırma tablosu. Cevap motorları tabloyu doğrudan çekiyor."""
    w = 1200
    h = 210 + len(rows) * 78
    # İlk sütun ada ayrılıyor (geniş), sayısal sütunlar dar ve eşit. Eşit
    # bölünce uzun adlar fiyat sütununun üstüne biniyordu.
    name_w = 560
    rest = len(columns) - 1
    span = (w - 160 - name_w) / max(rest, 1)
    col_x = [80] + [int(80 + name_w + span * i) for i in range(rest)]
    col_w = [name_w - 20] + [span - 20] * rest

    head = "".join(
        f'<text x="{col_x[i]}" y="185" font-family="{FONT}" font-size="21" '
        f'letter-spacing="1" fill="{MUTED}">{esc(c.upper())}</text>'
        for i, c in enumerate(columns))
    body = []
    y = 250
    for r, row in enumerate(rows):
        if r % 2 == 0:
            body.append(f'<rect x="60" y="{y - 40}" width="{w - 120}" '
                        f'height="70" rx="8" fill="#ffffff"/>')
        for i, cell in enumerate(row[:len(columns)]):
            weight = "700" if i == 0 else "400"
            size = 28 if i == 0 else 26
            fill = INK if i == 0 else "#33463f"
            body.append(
                f'<text x="{col_x[i]}" y="{y}" font-family="{FONT}" '
                f'font-size="{size}" font-weight="{weight}" fill="{fill}">'
                f'{esc(fit(cell, col_w[i], size))}</text>')
        y += 78
    alt_bits = "; ".join(" — ".join(r) for r in rows[:3])
    return Visual(
        kind="compare", width=w, height=h,
        alt=f"{heading}: {alt_bits}",
        caption=heading,
        svg=f'''<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}"
  viewBox="0 0 {w} {h}">
  <rect width="{w}" height="{h}" fill="{PAPER}"/>
  <rect x="0" y="0" width="{w}" height="10" fill="{BRAND}"/>
  <text x="80" y="112" font-family="{FONT}" font-size="42" font-weight="700"
    fill="{INK}">{esc(heading)}</text>
  {head}
  <line x1="80" y1="205" x2="{w - 80}" y2="205" stroke="{LINE}" stroke-width="2"/>
  {"".join(body)}
</svg>''')


def route_card(site, heading: str, stops: list[str], turning: str = "") -> Visual:
    """Rota şeması — iskeleler ve dönüş noktası."""
    w = 1200
    shown = stops[:7]
    h = 420
    gap = (w - 200) / max(len(shown) - 1, 1)
    dots, labels = [], []
    for i, stop in enumerate(shown):
        x = int(100 + gap * i)
        big = i in (0, len(shown) - 1)
        dots.append(f'<circle cx="{x}" cy="230" r="{13 if big else 8}" '
                    f'fill="{BRAND if big else ACCENT}"/>')
        text_lines = wrap(stop, 14)[:2]
        for j, line in enumerate(text_lines):
            labels.append(
                f'<text x="{x}" y="{285 + j * 26}" font-family="{FONT}" '
                f'font-size="21" text-anchor="middle" fill="{INK}">'
                f'{esc(line)}</text>')
    turn = ""
    if turning:
        turn = (f'<text x="{w // 2}" y="170" font-family="{FONT}" font-size="23" '
                f'text-anchor="middle" fill="{MUTED}">'
                f'{esc("turns at " + turning)}</text>')
    return Visual(
        kind="route", width=w, height=h,
        alt=f"{heading}: " + " → ".join(shown),
        caption=heading,
        svg=f'''<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}"
  viewBox="0 0 {w} {h}">
  <rect width="{w}" height="{h}" fill="{PAPER}"/>
  <rect x="0" y="0" width="{w}" height="10" fill="{BRAND}"/>
  <text x="80" y="105" font-family="{FONT}" font-size="40" font-weight="700"
    fill="{INK}">{esc(heading)}</text>
  {turn}
  <line x1="100" y1="230" x2="{w - 100}" y2="230" stroke="{BRAND}"
    stroke-width="4" stroke-linecap="round" opacity="0.35"/>
  {"".join(dots)}
  {"".join(labels)}
</svg>''')


# ---------------------------------------------------------------------------
# FACT BASE → GRAFİK
# Hangi grafiğin çizileceğine konu karar veriyor. Veri yoksa grafik de yok:
# boş bir "bir bakışta" kartı koymaktansa hiç koymamak doğru.
# ---------------------------------------------------------------------------

def _brand_block(site, topic: str) -> tuple[str, dict] | tuple[None, None]:
    """Konunun eşleştiği kendi markanın fact base bloğu."""
    from . import linkindex
    brand = linkindex.own_brand_for(site, topic)
    if not brand:
        return None, None
    host = re.sub(r"^https?://|/$", "", brand.get("url", ""))
    key = host.split(".")[0]
    block = (site.facts.get("own_products") or {}).get(key)
    return (key, block) if isinstance(block, dict) else (None, None)


def _verified(node) -> bool:
    return isinstance(node, dict) and node.get("verified") is True


def _price(product: dict) -> str:
    for field_name in ("price_from_eur", "price_from_eur_per_hour"):
        if field_name in product:
            suffix = "/hour" if "per_hour" in field_name else ""
            return f"from €{product[field_name]}{suffix}"
    return ""


def _duration(product: dict) -> str:
    if "duration_hours" in product:
        hours = product["duration_hours"]
        return f"{hours:g} hours" if hours != 1 else "1 hour"
    if "duration_minutes" in product:
        return f"{product['duration_minutes']} min"
    lo, hi = product.get("duration_min_minutes"), product.get("duration_max_minutes")
    if lo and hi:
        return f"{lo}-{hi} min"
    return ""


def _best_product(products: list[dict], article, topic: str) -> dict:
    """Yazının konusuna en yakın ürünü seçer.

    Önceden `products[0]` alınıyordu ve bu sessizce yanlış grafik basıyordu:
    14.09.2026'da yayınlanan "Bosphorus Dinner Cruise" yazısının metni üç
    saatlik akşam turunu anlatırken "at a glance" kartı fact base'deki ilk
    ürünü — iki saatlik 12 avroluk gündüz turunu — gösterdi. Aynı sayfada
    metin "three hours", kart "2 hours" diyordu. Seyahat rehberinde kendi
    kendiyle çelişen sayfa, yanlış sayfadan daha kötüdür.
    """
    if not products:
        return {}
    metin = f"{getattr(article, 'title', '')} {topic}".lower()
    kelimeler = set(re.findall(r"[a-z]{4,}", metin))
    dolgu = {"with", "from", "tour", "and", "the", "for"}
    en_iyi, en_iyi_anahtar = products[0], (-1, 0)
    for p in products:
        ad = set(re.findall(r"[a-z]{4,}", str(p.get("name", "")).lower())) - dolgu
        if not ad:
            continue
        anahtar = (len(ad & kelimeler), -len(ad - kelimeler))
        if anahtar > en_iyi_anahtar:
            en_iyi, en_iyi_anahtar = p, anahtar
    return en_iyi


def build_for(site, article, topic: str) -> list[Visual]:
    """Yazı için grafik seti: 1 öne çıkan + en fazla 2 veri grafiği."""
    key, block = _brand_block(site, topic)
    label = (block or {}).get("brand_name") or "Istanbul guide"
    out = [hero(site, article.title, label)]
    if not block:
        return out

    products = [p for p in block.get("products", []) if _verified(p)]
    venues = [v for v in block.get("venues", []) if _verified(v)]

    # Karşılaştırma: birden fazla doğrulanmış seçenek varsa en değerli grafik bu
    rows = []
    for item in (venues or products):
        name = item.get("name", "")
        price, dur = _price(item), _duration(item)
        if name and (price or dur):
            rows.append([name, price or "—", dur or "—"])
    if len(rows) >= 3:
        out.append(compare_card(site, f"{label}: your options",
                                ["Option", "Price", "Duration"], rows[:7]))

    # Bir bakışta: tek ürünün künyesi
    glance = []
    departures = block.get("departures")
    if _verified(departures):
        times = departures.get("times") or []
        if times:
            glance.append(("Departures", ", ".join(times)))
        piers = departures.get("boarding_piers") or []
        if piers:
            glance.append(("Piers", ", ".join(piers)))
    if products:
        first = _best_product(products, article, topic)
        if _duration(first):
            glance.append(("Duration", _duration(first)))
        if _price(first):
            glance.append(("Price", _price(first)))
    policies = block.get("policies")
    if _verified(policies) and policies.get("free_cancellation_hours"):
        glance.append(("Free cancellation",
                       f"up to {policies['free_cancellation_hours']}h before"))
    opening = block.get("opening_hours_24h")
    if opening and block.get("opening_hours_verified"):
        glance.append(("Opening hours", opening))
    if len(glance) >= 3 and len(out) < 3:
        secilen = _best_product(products, article, topic) if products else {}
        baslik = str(secilen.get("name", "")).strip() or label
        out.append(facts_card(site, f"{baslik} — at a glance", glance[:6],
                              note="Figures published by the operator"))

    # Rota: yalnızca doğrulanmış güzergâh varsa
    route = block.get("route")
    if _verified(route) and len(out) < 3:
        stops = (route.get("landmarks_outbound") or [])[:7]
        if len(stops) >= 4:
            out.append(route_card(site, f"{label} — route", stops,
                                  route.get("turning_point", "")))
    return out[:3]


def data_html(site, article, topic: str) -> list[str]:
    """Yazının doğrulanmış verisini GERÇEK HTML olarak döndürür.

    14.09.2026'ya kadar bu veri SVG'den PNG'ye çevrilip gövdeye resim olarak
    konuyordu. İki sebeple kaldırıldı:

      1. ARAMA MOTORU RESMİ OKUYAMIYOR. Fiyat, süre ve saat tablosunu resme
         gömmek, sayfanın en alıntılanabilir bilgisini Google'dan ve cevap
         motorlarından saklamak demekti. AEO hedefiyle taban tabana zıt.
      2. Çizim bozuktu. Uzun ürün adları fiyat sütununun üstüne biniyordu,
         başlık kırpılıyordu ("... — at a g"). SVG'de metin taşması diye bir
         uyarı yok; hata ancak yayında görülüyor.

    HTML tablo hem okunuyor, hem responsive, hem kopyalanabiliyor, hem de
    taşma diye bir sorunu yok. Kural aynı kalıyor: yalnızca facts.yaml'da
    verified: true olan veri yazılıyor.
    """
    key, block = _brand_block(site, topic)
    if not block:
        return []
    label = block.get("brand_name") or "Istanbul guide"
    out: list[str] = []

    products = [p for p in block.get("products", []) if _verified(p)]
    venues = [v for v in block.get("venues", []) if _verified(v)]

    satirlar = []
    for item in (venues or products):
        ad = str(item.get("name", "")).strip()
        fiyat, sure = _price(item), _duration(item)
        if ad and (fiyat or sure):
            satirlar.append((ad, fiyat or "—", sure or "—"))
    if len(satirlar) >= 3:
        govde = "".join(
            f"<tr><td>{esc(a)}</td><td>{esc(f)}</td><td>{esc(d)}</td></tr>"
            for a, f, d in satirlar[:7])
        out.append(
            f"<figure class=\"wp-block-table tce-table\">"
            f"<table><caption>{esc(label)}: your options</caption>"
            f"<thead><tr><th>Option</th><th>Price</th><th>Duration</th></tr>"
            f"</thead><tbody>{govde}</tbody></table>"
            f"<figcaption>Figures published by the operator, "
            f"{date.today().strftime('%B %Y')}.</figcaption></figure>")

    glance: list[tuple[str, str]] = []
    departures = block.get("departures")
    if _verified(departures):
        times = departures.get("times") or []
        if times:
            glance.append(("Departures", ", ".join(str(t) for t in times)))
        piers = departures.get("boarding_piers") or []
        if piers:
            glance.append(("Boarding", ", ".join(str(p) for p in piers)))
    if products and not out:
        first = _best_product(products, article, topic)
        ad = str(first.get("name", "")).strip()
        if ad:
            glance.append(("Option", ad))
        if _duration(first):
            glance.append(("Duration", _duration(first)))
        if _price(first):
            glance.append(("Price", _price(first)))
    policies = block.get("policies")
    if _verified(policies) and policies.get("free_cancellation_hours"):
        glance.append(("Free cancellation",
                       f"up to {policies['free_cancellation_hours']}h before"))
    opening = block.get("opening_hours_24h")
    if opening and block.get("opening_hours_verified"):
        glance.append(("Opening hours", opening))
    if len(glance) >= 3:
        govde = "".join(f"<tr><th scope=\"row\">{esc(k)}</th><td>{esc(v)}</td></tr>"
                        for k, v in glance[:6])
        out.append(
            f"<figure class=\"wp-block-table tce-table\">"
            f"<table><caption>{esc(label)} at a glance</caption>"
            f"<tbody>{govde}</tbody></table>"
            f"<figcaption>Figures published by the operator, "
            f"{date.today().strftime('%B %Y')}.</figcaption></figure>")

    route = block.get("route")
    if _verified(route):
        stops = [str(x) for x in (route.get("landmarks_outbound") or [])][:8]
        if len(stops) >= 4:
            donus = route.get("turning_point", "")
            maddeler = "".join(f"<li>{esc(x)}</li>" for x in stops)
            if donus:
                maddeler += f"<li><strong>{esc(donus)}</strong> — turning point</li>"
            out.append(f"<h3>What you pass, in order</h3><ol>{maddeler}</ol>")

    return out


def insert_figures(html_body: str, figures: list[str]) -> str:
    """Grafikleri H2 bölümlerinin altına yerleştirir.

    SSS başlığı atlanıyor: soru-cevap dizisini bölmek hem okumayı bozuyor hem
    cevap motorunun bloğu bütün hâlde çekmesini zorlaştırıyor.
    """
    if not figures:
        return html_body
    spots = [m for m in re.finditer(r"<h2\b[^>]*>(.*?)</h2>",
                                    html_body, re.IGNORECASE | re.DOTALL)
             if "frequently asked" not in re.sub(r"<[^>]+>", " ",
                                                 m.group(1)).lower()]
    if not spots:
        return html_body
    out = html_body
    # sondan başa: önceki eklemeler sonraki konumları kaydırmasın
    for figure, match in reversed(list(zip(figures, spots))):
        out = out[: match.end()] + "\n" + figure + "\n" + out[match.end():]
    return out


def design(site) -> dict:
    """Sitenin sosyal gönderi tasarım kiti.

    istambul.com'un değerleri @istambulguide hesabındaki MEVCUT gönderilerden
    okundu. YENİ bir marka ve boş bir hesap eklendiğinde bu blok o markanın
    kendi sitesinden doldurulmalı (logo rengi, kâğıt tonu, kelime markası) —
    varsayılanlar istambul.com'un kırmızısı, başka bir markada yanlış durur.
    """
    d = (site.raw.get("social", {}) or {}).get("design", {}) or {}
    return {
        "accent": d.get("accent", IG_RED),
        "paper": d.get("paper", IG_CREAM),
        # "auto": düzen sıraya göre dönüyor. Tek bir düzen adı yazılırsa
        # o sabitleniyor — yeni bir markada tek iskeletle başlamak
        # istenebilir.
        "layout": d.get("layout", "auto"),
        "wordmark": d.get("wordmark", site.domain),
        "brand_accent": d.get("brand_accent", {}),
        "note_style": d.get("note_style", "card"),
    }


def brand_lockup(site, konu: str) -> tuple[str, str, bool]:
    """Konuya denk gelen markanın alan adı, vurgu rengi ve portal mı bilgisi.

    Hesaptaki gönderilerde altlık her zaman istambul.com değil: Çamlıca
    gönderisinde camlicatower.com, Haliç gönderisinde Golden Horn Cruise
    kilidi var. Satışı yapan siteyi göstermek ticari olarak da doğrusu.

    Üçüncü değer kilidin BİÇİMİNİ belirliyor: portal gönderilerinde altta
    yalnızca ISTAMBUL.COM kelime markası var, markalı gönderilerde marka
    adının altında hap içinde alan adı duruyor.
    """
    try:
        from . import linkindex
        marka = linkindex.own_brand_for(site, konu)
    except Exception:
        marka = None
    kit = design(site)
    if not marka:
        return kit["wordmark"], kit["accent"], True
    host = re.sub(r"^https?://|/$", "", marka.get("url", ""))
    anahtar = host.split(".")[0]
    renk = (kit["brand_accent"] or {}).get(anahtar) \
        or BRAND_ACCENT.get(anahtar) or kit["accent"]
    return host, renk, host == kit["wordmark"]


def brand_name(site, alan: str) -> str:
    """Kilitte yazacak marka adı.

    "GOLDENHORNCRUISE" okunmuyor; hesapta "Golden Horn Cruise" yazıyor. Ad
    sites/<domain>.yaml → link_map.own_brands[].name alanından geliyor. Alan
    yoksa alan adının ilk parçası kullanılıyor — yanlış değil, yalnızca çirkin;
    yeni marka eklenirken `name` yazılmalı.
    """
    for entry in (site.raw.get("link_map", {}) or {}).get("own_brands", []):
        host = re.sub(r"^https?://|/$", "", entry.get("url", ""))
        if host == alan and entry.get("name"):
            return str(entry["name"])
    return alan.split(".")[0]


# 15.09.2026 — DÜZENLER YENİDEN YAZILDI.
#
# Önceki dörtlü (overlay / band / frame / split) hesaptan alınmış diye
# yazılıydı ama `frame` ve `split` uydurmaydı; Evren reddetti. Aşağıdaki
# dördü 15.09.2026'da @istambulguide'daki son 12 gönderi tek tek açılarak
# çıkarıldı ve her birinin kaynağı yanında yazılı.
#
# Hesabın DEĞİŞMEYEN iskeleti: tam sayfa dikey fotoğraf → üst üçte birde
# iki renkli iki satır başlık → ortada TAM BİR TANE içerik öğesi → altta
# ORTALANMIŞ marka kilidi. Düzenler yalnızca ortadaki öğede ayrışıyor.
LAYOUTS = ("kart", "liste", "not", "altbaslik")

#: 1080x1350 — hesaptaki 10 gönderinin 10'u dikey.
POST_W, POST_H = 1080, 1350


def pick_layout(sira: int, hafta: int = 0) -> str:
    """Gönderi düzenini seçer.

    Tek iskeletle üretilen feed monoton görünüyor; hesabın kendisinde dört
    farklı içerik öğesi var. Seçim RASTGELE DEĞİL, sıraya bağlı: bir turda
    çıkan gönderiler birbirinden farklı düzen alıyor, hafta numarası da
    eklendiği için aynı slot her hafta aynı düzene düşmüyor.
    """
    return LAYOUTS[(sira + hafta) % len(LAYOUTS)]


def _baslik_iki_renk(metin: str, line: str, x: int, y0: int, vurgu: str,
                     genis: int = 920):
    """İki satır, İKİ RENK — hesabın her gönderisinde bu var.

    İlk satır marka renginde ve beyaz konturlu; ikinci satır beyaz ve hafif
    sağa kaydırılmış. Kaydırma uydurma değil: "MOSQUE / ETIQUETTE",
    "5 DAYS / IN ISTANBUL ITINERARY" ve "EMINONU / KARAKOY" gönderilerinin
    üçünde de ikinci satır bu şekilde duruyor. Kontur da öyle: Çamlıca ve
    Bosphorus Cruise gönderilerinde kırmızı/mavi başlığın beyaz konturu var,
    çünkü altındaki fotoğraf karışık.

    İki satırdan fazlası yok — hesapta üç satırlık başlık hiç yok.
    """
    # Son iki katman 15.09.2026'da eklendi: "Types of Boat Trips and Cruises
    # in Istanbul" üç satıra taşıyor ve [:2] ile kesilip görsele "TYPES OF
    # BOAT TRIPS / AND CRUISES IN" diye yarım başlık basılmıştı. Yarım başlık
    # yerine küçük punto.
    for genislik, punto, aralik in ((12, 108, 112), (16, 86, 94),
                                    (21, 68, 76), (27, 56, 62),
                                    (34, 46, 52)):
        satirlar = wrap(metin.upper(), genislik)
        if len(satirlar) <= 2:
            break
    satirlar = satirlar[:2]
    parcalar = []
    for i, s in enumerate(satirlar):
        if i == 0:
            renk, kontur, dx = vurgu, ('stroke="#ffffff" stroke-width="3" '
                                       'paint-order="stroke" '), 0
        else:
            renk, kontur, dx = "#ffffff", "", 46
        parcalar.append(
            f'<text x="{x + dx}" y="{y0 + i * aralik}" '
            f'font-family="{HEAD_FONT}" font-size="{punto}" font-weight="700" '
            f'fill="{renk}" {kontur}letter-spacing="-1">{esc(s)}</text>')
    alt_y = y0 + (len(satirlar) - 1) * aralik
    alt = ""
    if line:
        # Alt satır KIRPILMIYOR, sarılıyor. Önceki sürüm fit() ile kesiyordu
        # ve "What to know before you step ins…" diye yayına çıkacaktı.
        ls = wrap(line, 32)
        # İki satıra sığmıyorsa hiç basılmıyor: kırpmak yarım cümle demek.
        if len(ls) > 2:
            ls = []
        alt = "".join(
            f'<text x="{x + 46}" y="{alt_y + 74 + i * 54}" '
            f'font-family="{SERIF_FONT}" font-style="italic" font-size="44" '
            f'fill="#ffffff" opacity="0.96">{esc(s)}</text>'
            for i, s in enumerate(ls))
        if ls:
            alt_y += (len(ls) - 1) * 54
    return "".join(parcalar) + alt, alt_y + (96 if alt else 36)


def _cumle(notlar, adet: int = 3) -> str:
    """Not satırlarını okunur tek metne çevirir.

    Satırlar cümle değil, madde: sonlarında nokta yok. Düz birleştirince
    "Bring sunscreen; there's little shade on deck The Horn's name comes
    from its curved shape" diye çıkıyordu (15.09.2026, Haliç gönderisi).
    """
    parcalar = []
    for n in (notlar or [])[:adet]:
        n = (n or "").strip()
        if not n:
            continue
        parcalar.append(n if n[-1] in ".!?" else n + ".")
    return " ".join(parcalar)


def _oge_kart(notlar, x: int, y: int, en: int, vurgu: str):
    """A — marka renginde yuvarlak kart, beyaz metin.

    Kaynak: "THE HIGHEST POINT IN ISTANBUL" (Çamlıca). Kısa bir paragraf,
    kartın içinde beyaz.
    """
    metin = _cumle(notlar, 2)
    if not metin:
        return "", 0
    satirlar = wrap(metin, 32)[:5]
    yuk = 54 + len(satirlar) * 44
    govde = "".join(
        f'<text x="{x + 38}" y="{y + 58 + i * 44}" font-family="{FONT}" '
        f'font-size="30" fill="#ffffff">{esc(s)}</text>'
        for i, s in enumerate(satirlar))
    return (f'<rect x="{x}" y="{y}" width="{en}" height="{yuk}" rx="34" '
            f'fill="{vurgu}"/>{govde}'), yuk


def _oge_liste(notlar, x: int, y: int, en: int, vurgu: str, kagit: str):
    """B — krem kart, ✓ işaretli maddeler.

    Kaynak: "MOSQUE ETIQUETTE". Üç madde, her birinin başında marka renginde
    daire içinde tik.
    """
    kisa = [fit(n, en - 140, 29) for n in (notlar or []) if n.strip()][:3]
    if not kisa:
        return "", 0
    yuk = 58 + len(kisa) * 72
    satir = ""
    for i, t in enumerate(kisa):
        cy = y + 66 + i * 72
        satir += (
            f'<circle cx="{x + 62}" cy="{cy - 10}" r="19" fill="none" '
            f'stroke="{vurgu}" stroke-width="3"/>'
            f'<path d="M{x + 53},{cy - 11} l7,8 l14,-16" fill="none" '
            f'stroke="{vurgu}" stroke-width="4" stroke-linecap="round" '
            f'stroke-linejoin="round"/>'
            f'<text x="{x + 98}" y="{cy}" font-family="{FONT}" font-size="29" '
            f'fill="{IG_INK}">{esc(t)}</text>')
    # Yırtık alt kenar — hesabın imzası ("Mosque Etiquette" kartı).
    adim = 13
    zig = f'M{x},{y + yuk - 14} '
    for k in range(en // adim):
        zig += f'l{adim},{14 if k % 2 == 0 else -14} '
    zig += f'L{x + en},{y + yuk - 14} L{x + en},{y} L{x},{y} Z'
    return (f'<path d="{zig}" fill="{kagit}" opacity="0.97"/>{satir}'), yuk


def _oge_not(notlar, x: int, y: int, vurgu: str, kagit: str):
    """C — hafif eğik yapışkan not, tek bilgi.

    Kaynak: "FAST FACTS" (Sarnıç). Kart eğik duruyor ve üstünde marka
    renginde bir raptiye var.
    """
    # Yapışkan notta TEK bilgi var; hesaptaki "Fast Facts" gönderisi de öyle.
    metin = _cumle(notlar, 1)
    if not metin:
        return "", 0
    satirlar = wrap(metin, 24)[:4]
    en = 520
    yuk = 62 + len(satirlar) * 42
    govde = "".join(
        f'<text x="{x + 36}" y="{y + 68 + i * 42}" font-family="{FONT}" '
        f'font-size="27" fill="{IG_INK}">{esc(s)}</text>'
        for i, s in enumerate(satirlar))
    return (f'<g transform="rotate(-4 {x + en // 2} {y + yuk // 2})">'
            f'<rect x="{x}" y="{y}" width="{en}" height="{yuk}" rx="6" '
            f'fill="{kagit}"/>'
            f'<circle cx="{x + en // 2}" cy="{y + 24}" r="9" fill="{vurgu}"/>'
            f'{govde}</g>'), yuk


def _oge_altbaslik(notlar, x: int, y: int, vurgu: str):
    """D — kart yok; dikey vurgu çizgisi + iki satır beyaz metin.

    Kaynak: "EMINONU VS. KARAKOY — Where Should You Board Your Cruise?" ve
    "BOSPHORUS CRUISE — Which one would you prefer?". Fotoğrafın üstünde
    kart olmadan duruyor, solunda ince dikey çizgi var.
    """
    metin = _cumle(notlar, 1)
    if not metin:
        return "", 0
    satirlar = wrap(metin, 26)[:3]
    yuk = len(satirlar) * 54
    govde = "".join(
        f'<text x="{x + 34}" y="{y + 42 + i * 54}" font-family="{FONT}" '
        f'font-size="38" font-weight="600" fill="#ffffff">{esc(s)}</text>'
        for i, s in enumerate(satirlar))
    return (f'<rect x="{x}" y="{y}" width="8" height="{yuk + 12}" '
            f'fill="{vurgu}"/>{govde}'), yuk


def _kilit_orta(alan: str, ad: str, portal: bool, w: int, y: int,
                vurgu: str) -> str:
    """Marka kilidi — alt ORTA, sola değil.

    İki biçim var, ikisi de hesaptan okundu:
    · portal gönderileri → yalnızca kelime markası: ISTAMBUL kalın + .COM ince
    · markalı gönderiler → marka adı, altında marka renginde hap içinde alan
      adı (Golden Horn Cruise, Bosporus Cruise, Istanbul Hamam gönderileri)
    """
    if portal:
        # tspan KULLANILMIYOR: cairosvg tspan'ı text-anchor="middle" ile
        # yanlış konumlandırıyor ve "ISTAMBUL .COM" diye ayrık çıkıyor
        # (dx ile de düzelmiyor). İki ayrı metin, ortalaması elle hesaplı.
        # Katsayılar DejaVu Sans Condensed'da ölçüldü: kalın büyük harf
        # ~0.65em, ince ~0.58em.
        kok, _, uzanti = alan.partition(".")
        p1, p2 = 46, 32
        g1 = len(kok) * p1 * 0.65
        g2 = (len(uzanti) + 1) * p2 * 0.58
        x0 = (w - (g1 + g2)) / 2
        return (f'<text x="{x0:.0f}" y="{y + 16}" font-family="{HEAD_FONT}" '
                f'font-size="{p1}" font-weight="700" fill="#ffffff">'
                f'{esc(kok.upper())}</text>'
                f'<text x="{x0 + g1:.0f}" y="{y + 16}" font-family="{HEAD_FONT}" '
                f'font-size="{p2}" font-weight="400" fill="#ffffff" '
                f'opacity="0.88">.{esc(uzanti.upper())}</text>')
    en = int(len(alan) * 14.5) + 58
    return (f'<text x="{w // 2}" y="{y - 22}" text-anchor="middle" '
            f'font-family="{HEAD_FONT}" font-size="42" font-weight="700" '
            f'fill="#ffffff" letter-spacing="1">{esc(ad.upper())}</text>'
            f'<rect x="{(w - en) // 2}" y="{y}" width="{en}" height="50" '
            f'rx="25" fill="{vurgu}"/>'
            f'<text x="{w // 2}" y="{y + 34}" text-anchor="middle" '
            f'font-family="{FONT}" font-size="25" fill="#ffffff">'
            f'{esc(alan)}</text>')


def _foto_hazirla(photo: bytes, w: int, h: int,
                  odak: float = 0.5) -> tuple[bytes, str]:
    """Fotoğrafı tuval ölçüsüne kırpar ve keskin yeniden örnekler.

    Ham baytları SVG'ye gömüp <image> ile ölçeklemek yumuşak sonuç veriyor;
    cairosvg'nin ölçekleyicisi kaliteli değil. Pillow ile önce doğru orana
    kırpıp sonra LANCZOS ile tam ölçüye getiriyoruz — kırpma zaten SVG
    tarafında "slice" ile yapılıyordu, farkı yeniden örnekleme kalitesi.

    Pillow yoksa ya da dosya okunamazsa ham bayt geri dönüyor: gönderi
    çıkmaması, biraz yumuşak çıkmasından kötü.
    """
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(photo)).convert("RGB")
        hedef = w / h
        gen, yuk = im.size
        # odak: kırpma penceresinin nereye oturacağı (0 sol/üst, 1 sağ/alt).
        # Carousel slaytları aynı fotoğrafın farklı kadrajını kullanıyor;
        # üç karede birebir aynı görüntü kaydırmayı anlamsız kılıyordu.
        odak = min(max(odak, 0.0), 1.0)
        if gen / yuk > hedef:
            yeni = int(yuk * hedef)
            x0 = int((gen - yeni) * odak)
            im = im.crop((x0, 0, x0 + yeni, yuk))
        else:
            yeni = int(gen / hedef)
            y0 = int((yuk - yeni) * odak)
            im = im.crop((0, y0, gen, y0 + yeni))
        im = im.resize((w, h), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=92, optimize=True)
        return buf.getvalue(), "jpeg"
    except Exception:
        return photo, "png"


def social_post(site, title: str, kicker: str, photo: bytes,
                line: str = "", notes: list[str] | None = None,
                sira: int = 0, odak: float = 0.5) -> Visual:
    """Instagram gönderisi — 1080x1350 DİKEY, fotoğraf + marka tipografisi.

    Kurgu 15.09.2026'da @istambulguide'daki son 12 gönderi tek tek açılarak
    çıkarıldı. Her gönderide aynı dört katman var:

      1. gerçek fotoğraf, tam sayfa
      2. üst üçte birde İKİ RENKLİ İKİ SATIR başlık
      3. ortada TAM BİR TANE içerik öğesi — dördü dönüşümlü
      4. altta ORTALANMIŞ marka kilidi

    İçerik öğeleri ve kaynakları:
      kart      — Çamlıca "The Highest Point": marka renginde yuvarlak kart
      liste     — "Mosque Etiquette": krem kart, ✓ işaretli üç madde
      not       — "Fast Facts": hafif eğik yapışkan not
      altbaslik — "Eminönü vs Karaköy": kart yok, dikey çizgi + iki satır

    TİPOGRAFİYİ MOTOR ÇİZİYOR, MODEL DEĞİL: görsel modelleri harfleri hâlâ
    bozuk yazıyor ("ISTAMBUL" yerine "ISTMABUL").

    FOTOĞRAF ZORUNLU. Fotoğrafsız gönderi çıkmıyor.
    """
    import base64

    if not photo:
        raise ValueError("social_post fotoğrafsız çağrılamaz")

    w, h = POST_W, POST_H
    # odak: kadrajın dikey merkezi. Düz düzende gönderi gönderi değişiyor
    # (STANDART 3, format rotasyonu) — aynı fotoğraf bile farklı kadrajla
    # farklı bir kare oluyor. Kartlı düzenlerde 0.5 sabit.
    ham, tur = _foto_hazirla(photo, w, h, odak)
    veri = base64.b64encode(ham).decode()
    kit = design(site)
    kagit = kit["paper"]
    alan, vurgu, portal = brand_lockup(site, f"{title} {kicker}")

    # DÜZ DÜZEN — fotoğrafın üstünde HİÇBİR ŞEY yok.
    #
    # 16.09.2026: @istanbulhammam hesabının 136 gönderisi incelendi. Hepsi düz
    # fotoğraf: yazı yok, kart yok, başlık tipografisi yok, marka kilidi bile
    # yok. istambul.com'un dört düzenli kart kurgusunu bu hesaba taşımak
    # UYDURMAK olurdu — Evren'in defalarca reddettiği şey ("artik senin bir
    # sey uydurmani istemiyorum").
    #
    # Bu hesapta bilgiyi caption taşıyor, görsel yalnızca fotoğraf. Kural
    # site bazlı: sites/<domain>.yaml → social.design.layout: duz
    if str(kit.get("layout") or "").lower() in ("duz", "plain"):
        return Visual(
            kind="social", width=w, height=h,
            alt=f"{title} — {alan}",
            svg=f'''<svg xmlns="http://www.w3.org/2000/svg"
  xmlns:xlink="http://www.w3.org/1999/xlink" width="{w}" height="{h}"
  viewBox="0 0 {w} {h}">
  <image x="0" y="0" width="{w}" height="{h}"
    preserveAspectRatio="xMidYMid slice"
    xlink:href="data:image/{tur};base64,{veri}"/>
</svg>''')

    temiz = [n.strip() for n in (notes or []) if n and n.strip()]
    duzen = kit["layout"] if kit["layout"] in LAYOUTS else pick_layout(
        sira, date.today().isocalendar()[1])
    # Üç maddelik liste iki notla yarım kalıyor; öğesiz gönderi de olmuyor.
    if duzen == "liste" and len(temiz) < 2:
        duzen = "kart"
    if not temiz:
        duzen = "altbaslik"

    # Blog başlığı ayraçtan bölünüyor, kalanı italik alt satır oluyor.
    # UZUNLUK ŞARTI YOK: "The Highest Point: In Istanbul" tam 30 karakter ve
    # eski eşik (>30) tutmadığı için başlık "THE HIGHEST POINT: IN / ISTANBUL"
    # diye iki noktayı ortada bırakarak bölünüyordu.
    metin = title.strip()
    for ayrac in (":", " — ", " - ", " | "):
        if ayrac in metin:
            bas, _, kalan = metin.partition(ayrac)
            if len(bas.strip()) >= 8 and kalan.strip():
                # Başlığın kendi devamı öncelikli; çağıranın verdiği satır
                # yalnızca başlıkta ayraç yoksa devreye giriyor.
                metin = bas.strip()
                line = kalan.strip()
            break

    tanim = f'''<defs>
    <linearGradient id="ust" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#0b0f14" stop-opacity="0.66"/>
      <stop offset="1" stop-color="#0b0f14" stop-opacity="0"/>
    </linearGradient>
    <linearGradient id="alt" x1="0" y1="1" x2="0" y2="0">
      <stop offset="0" stop-color="#0b0f14" stop-opacity="0.70"/>
      <stop offset="1" stop-color="#0b0f14" stop-opacity="0"/>
    </linearGradient>
  </defs>'''

    baslik, sonra = _baslik_iki_renk(metin, line, 80, 250, vurgu)

    # Öğe ALTA hizalı: kilidin biraz üstünde bitiyor. Sabit bir y ile
    # denendi ve başlıkla öğe arasında koca bir boşluk kalıyordu; hesapta
    # kart her zaman alt üçte birde, kilide yakın duruyor.
    kilit_y = h - 120
    alt_sinir = kilit_y - 130
    olcu = {"kart": _oge_kart, "liste": _oge_liste,
            "not": _oge_not, "altbaslik": _oge_altbaslik}[duzen]
    if duzen == "kart":
        _, yuk = olcu(temiz, 80, 0, 920, vurgu)
    elif duzen == "liste":
        _, yuk = olcu(temiz, 80, 0, 920, vurgu, kagit)
    elif duzen == "not":
        _, yuk = olcu(temiz, 80, 0, vurgu, kagit)
    else:
        _, yuk = olcu(temiz, 80, 0, vurgu)
    oge_y = max(sonra + 40, alt_sinir - yuk)

    if duzen == "kart":
        oge, _ = _oge_kart(temiz, 80, oge_y, 920, vurgu)
    elif duzen == "liste":
        oge, _ = _oge_liste(temiz, 80, oge_y, 920, vurgu, kagit)
    elif duzen == "not":
        oge, _ = _oge_not(temiz, 80, oge_y, vurgu, kagit)
    else:
        oge, _ = _oge_altbaslik(temiz, 80, oge_y, vurgu)

    govde = (
        f'<rect width="{w}" height="{h}" fill="{kagit}"/>'
        + f'<image x="0" y="0" width="{w}" height="{h}" '
          f'preserveAspectRatio="xMidYMid slice" '
          f'xlink:href="data:image/{tur};base64,{veri}"/>'
        + f'<rect width="{w}" height="620" fill="url(#ust)"/>'
        + f'<rect y="{h - 420}" width="{w}" height="420" fill="url(#alt)"/>'
        + baslik
        + oge
        + _kilit_orta(alan, brand_name(site, alan), portal, w, kilit_y, vurgu))

    return Visual(
        kind="social",
        width=w, height=h,
        alt=f"{title} — {alan}",
        svg=f'''<svg xmlns="http://www.w3.org/2000/svg"
  xmlns:xlink="http://www.w3.org/1999/xlink" width="{w}" height="{h}"
  viewBox="0 0 {w} {h}">
  {tanim}
  {govde}
</svg>''')


def slide(site, baslik: str, satirlar, photo: bytes,
          odak: float = 0.5) -> Visual:
    """Carousel İÇ slaytı — 1080x1350.

    15.09.2026 — Evren: "carusel'de fotografin uzerine boyle yazilar kaba
    oluyor, ve fotograf kayboluyor."

    Kart artık ORTADA değil, ALTA yaslı ve dar: fotoğrafın üst üçte ikisi
    açık kalıyor, konu görünüyor. Önceki sürüm tuvalin ortasına neredeyse tam
    genişlikte opak bir blok koyuyordu ve Galata Kulesi'nin gövdesi kartın
    arkasında kalmıştı.

    Kurgu yine hesaptaki "Bosphorus Cruise — Daytime / Evening" carousel'inden:
    fotoğraf zemin, üstte ortada marka adı, beyaz kart, kartta marka renginde
    başlık ve "Etiket: değer" satırları. Oradaki kart da fotoğrafın konusunu
    kapatmıyordu.
    """
    import base64

    if not photo:
        raise ValueError("slide fotoğrafsız çağrılamaz")

    w, h = POST_W, POST_H
    ham, tur = _foto_hazirla(photo, w, h, odak)
    veri = base64.b64encode(ham).decode()
    kit = design(site)
    alan, vurgu, portal = brand_lockup(site, baslik)

    temiz = [(str(a).strip().rstrip(":"), str(b).strip())
             for a, b in (satirlar or []) if str(b).strip()][:3]

    kart_x, kart_en = 100, 880
    ic, y = [], 0
    for etiket, deger in temiz:
        parcalar = wrap(deger, 44)[:2]
        ic.append((etiket, parcalar))
        y += 36 + len(parcalar) * 36

    ust_baslik = wrap(baslik.upper(), 20)[:2]
    ek = 54 if len(ust_baslik) > 1 else 0
    kart_yuk = 118 + y + ek
    # Kart ALTA yaslı; üst üçte iki fotoğrafa kalıyor.
    kart_y = h - 132 - kart_yuk

    govde = ""
    yy = kart_y + 106 + ek
    for etiket, parcalar in ic:
        govde += (f'<text x="{kart_x + 40}" y="{yy}" font-family="{FONT}" '
                  f'font-size="26" font-weight="700" fill="{vurgu}">'
                  f'{esc(etiket)}:</text>')
        for i, s in enumerate(parcalar):
            govde += (f'<text x="{kart_x + 40}" y="{yy + 34 + i * 34}" '
                      f'font-family="{FONT}" font-size="28" fill="{IG_INK}">'
                      f'{esc(s)}</text>')
        yy += 36 + len(parcalar) * 36

    baslik_svg = "".join(
        f'<text x="{kart_x + 40}" y="{kart_y + 62 + i * 54}" '
        f'font-family="{HEAD_FONT}" font-size="52" font-weight="700" '
        f'fill="{vurgu}" letter-spacing="-1">{esc(s)}</text>'
        for i, s in enumerate(ust_baslik))

    marka = alan.split(".")[0] if portal else brand_name(site, alan)

    return Visual(
        kind="social",
        width=w, height=h,
        alt=f"{baslik} — {alan}",
        svg=f'''<svg xmlns="http://www.w3.org/2000/svg"
  xmlns:xlink="http://www.w3.org/1999/xlink" width="{w}" height="{h}"
  viewBox="0 0 {w} {h}">
  <defs>
    <linearGradient id="ust2" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#0b0f14" stop-opacity="0.5"/>
      <stop offset="1" stop-color="#0b0f14" stop-opacity="0"/>
    </linearGradient>
    <linearGradient id="alt2" x1="0" y1="1" x2="0" y2="0">
      <stop offset="0" stop-color="#0b0f14" stop-opacity="0.5"/>
      <stop offset="1" stop-color="#0b0f14" stop-opacity="0"/>
    </linearGradient>
  </defs>
  <rect width="{w}" height="{h}" fill="{kit["paper"]}"/>
  <image x="0" y="0" width="{w}" height="{h}"
    preserveAspectRatio="xMidYMid slice"
    xlink:href="data:image/{tur};base64,{veri}"/>
  <rect width="{w}" height="260" fill="url(#ust2)"/>
  <rect y="{h - 420}" width="{w}" height="420" fill="url(#alt2)"/>
  <text x="{w // 2}" y="120" text-anchor="middle" font-family="{HEAD_FONT}"
    font-size="38" font-weight="700" fill="#ffffff"
    letter-spacing="1">{esc(marka.upper())}</text>
  <rect x="{kart_x}" y="{kart_y}" width="{kart_en}" height="{kart_yuk}"
    rx="20" fill="#ffffff" opacity="0.95"/>
  {baslik_svg}
  {govde}
</svg>''')

def social_card(site, title: str, label: str, line: str = "") -> Visual:
    """Instagram kartı — 1080x1080 kare.

    hero() 1200x630 yatay; Instagram onu kırpıyor ve yazı kesiliyor. Sosyal
    için ayrı ölçü gerekiyordu.

    Bu bir AI FOTOĞRAF DEĞİL: markanın kendi paletiyle çizilmiş tipografik
    kart. Gerçek yapının sahte fotoğrafını basmıyoruz — o, seyahat rehberinde
    güven kaybı olurdu. Kütüphanede konuya uyan gerçek fotoğraf bulunamadığında
    gönderinin tamamen düşmesi yerine bu kart çıkıyor.
    """
    w = h = 1080
    satirlar = wrap(title, 18)[:4]
    y0 = 520 - (len(satirlar) - 1) * 40
    govde = "".join(
        f'<text x="90" y="{y0 + i * 92}" font-family="{FONT}" font-size="76" '
        f'font-weight="700" fill="#ffffff">{esc(s)}</text>'
        for i, s in enumerate(satirlar))
    alt_satir = ""
    if line:
        alt_satir = (f'<text x="90" y="{y0 + len(satirlar) * 92 + 30}" '
                     f'font-family="{FONT}" font-size="34" fill="#ffffff" '
                     f'opacity="0.82">{esc(fit(line, 880, 34))}</text>')
    return Visual(
        kind="social",
        width=w, height=h,
        alt=f"{title} — {site.domain}",
        svg=f'''<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}"
  viewBox="0 0 {w} {h}">
  <defs><linearGradient id="sg" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="{DEEP}"/><stop offset="1" stop-color="{INK}"/>
  </linearGradient></defs>
  <rect width="{w}" height="{h}" fill="url(#sg)"/>
  {skyline(h, w, 0.20)}
  <rect x="90" y="150" width="72" height="6" fill="{ACCENT}"/>
  <text x="90" y="212" font-family="{FONT}" font-size="28" letter-spacing="4"
    fill="{ACCENT}">{esc(fit(label.upper(), 860, 28))}</text>
  {govde}
  {alt_satir}
  <rect x="0" y="{h - 96}" width="{w}" height="96" fill="{INK}" opacity="0.6"/>
  <text x="90" y="{h - 38}" font-family="{FONT}" font-size="32"
    fill="#ffffff" opacity="0.92">{esc(site.domain)}</text>
</svg>''')
