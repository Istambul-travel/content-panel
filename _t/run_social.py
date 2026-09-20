#!/usr/bin/env python3
"""Haftalık sosyal medya turu: planla → yaz → fotoğraf eşleştir → yayınla.

Blog motorundaki gölge modu burada da geçerli. mode=shadow iken gönderiler
kuyruğa yazılıyor ve panelde görünüyor ama Instagram'a/Facebook'a çıkmıyor.

Kullanım:
    python scripts/run_social.py --site istambul.com [--dry-run] [--discover]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import config, social  # noqa: E402
from engine.publish_wp import WordPress  # noqa: E402


def media_library(wp: WordPress, limit: int = 400) -> list[dict]:
    """Medya kütüphanesini sosyal motorun anlayacağı biçime indirger.

    Yalnızca ihtiyacımız olan alanları alıyoruz: kütüphane 325 kayıt ve tam
    hâliyle çekmek her turda gereksiz megabaytlar demek.
    """
    out: list[dict] = []
    sayfa = 1
    while len(out) < limit:
        try:
            rows = wp._call("GET", f"media?per_page=100&page={sayfa}"
                                   "&media_type=image&_fields=id,slug,alt_text,"
                                   "source_url,media_details")
        except Exception:
            break
        if not rows:
            break
        for r in rows:
            detay = r.get("media_details") or {}
            out.append({"id": r.get("id"), "slug": r.get("slug", ""),
                        "alt_text": r.get("alt_text", ""),
                        "source_url": r.get("source_url", ""),
                        "width": detay.get("width") or 0,
                        "height": detay.get("height") or 0})
        if len(rows) < 100:
            break
        sayfa += 1
    return out


def _indir(url: str) -> bytes | None:
    try:
        istek = urllib.request.Request(url)
        istek.add_header("User-Agent", "travelpedia-content-engine/1.0")
        with urllib.request.urlopen(istek, timeout=60) as cevap:
            return cevap.read()
    except Exception:
        return None

_SAYFA_ONBELLEK: dict[str, str] = {}


def _sayfa_metni(url: str) -> str:
    """Gönderinin anlattığı sayfanın HTML gövdesi.

    İki yerde kullanılıyor: fotoğraf istemi yazının metninden kuruluyor, ve
    metin kalite kapısı rakamları yine yazının metniyle doğruluyor. Aynı
    sayfayı iki kez indirmemek için önbelleğe alınıyor.

    Sayfa çekilemezse boş dönüyor; istem yalnızca konu başlığıyla kuruluyor
    ve kapı yalnızca fact base'e bakıyor.
    """
    if not url:
        return ""
    if url in _SAYFA_ONBELLEK:
        return _SAYFA_ONBELLEK[url]
    ham = _indir(url)
    sonuc = ""
    if ham:
        try:
            sonuc = ham.decode("utf-8", errors="replace")
        except Exception:
            sonuc = ""
    _SAYFA_ONBELLEK[url] = sonuc
    return sonuc


def yayinla(site, hazir: list) -> None:
    """Hazir gonderileri Instagram ve Facebook hesaplarina cikarir.

    Ayri bir islev olmasinin sebebi ONAY MODU. Onay acikken gonderi bir
    turda uretilip kuyrukta "ready" olarak bekliyor, panelden
    onaylandiktan sonra BASKA bir turda buradan yayina cikiyor. Ayni kod
    iki yoldan da calisiyor, yani onayli ve onaysiz yayin arasinda
    davranis farki olmuyor.
    """
    from engine.publish_meta import Meta, MetaError
    meta = Meta(site)
    for p in hazir:
        # Instagram ve Facebook AYRI değerlendiriliyor. Önceden ikisi tek
        # try bloğundaydı: Instagram'a çıkan gönderi, Facebook hata
        # verince "skipped" işaretleniyordu. Kuyruk gönderiyi hiç çıkmamış
        # sayınca bir sonraki tur aynı konuyu yeniden planlıyor ve
        # Instagram'a İKİNCİ kez basıyordu. 14.09.2026'da aynı
        # "September in Istanbul" kartı hesapta iki kez yayınlandı.
        kayit = {"at": date.today().isoformat()}
        try:
            # Konum etiketi: hem Instagram aramasında indeksleniyor hem
            # de ölçülen bir beğeni artışı sağlıyor. Bulunamazsa gönderi
            # konum etiketsiz çıkıyor — etiket yayını engellemiyor.
            yer = ""
            try:
                sabit = (site.raw.get("social", {}) or {}).get(
                    "locations", {}) or {}
                anahtar = (getattr(p, "location", "") or p.subject).lower()
                for ad_, kimlik in sabit.items():
                    if str(ad_).lower() in anahtar:
                        yer = str(kimlik)
                        break
                if not yer:
                    yer = meta.find_location(
                        (getattr(p, "location", "") or p.subject))
                if not yer:
                    # Son çare: şehrin kendisi. Hesabın gönderilerinin
                    # bir kısmı da "Istanbul, Türkiye" etiketli.
                    yer = str((site.raw.get("social", {}) or {}).get(
                        "location_default", "") or "")
                if yer:
                    print(f"    konum etiketi: {yer}")
            except Exception:
                yer = ""

            kareler = [u for u in (getattr(p, "image_urls", []) or
                                   [p.image_url]) if u]
            if len(kareler) >= 2:
                ig = meta.instagram_carousel(kareler, p.caption,
                                             location_id=yer)
            else:
                # alt_text yalnızca tek görselde kabul ediliyor.
                ig = meta.instagram_post(p.image_url, p.caption,
                                         alt_text=p.image_alt,
                                         location_id=yer)
            kayit["instagram"] = ig.get("id")
            p.status = "published"
            print(f"    Instagram: {p.subject[:40]}")
        except MetaError as exc:
            p.status = "skipped"
            p.skip_reason = f"instagram hatası: {exc}"
            print(f"    INSTAGRAM HATASI: {p.subject[:40]} — {exc}")

        # Facebook sayfası tanımlı değilse adım sessizce atlanıyor.
        # istanbulhamam.com böyle: markanın Facebook sayfası hiç açılmamış,
        # hesap yalnızca Instagram'da. Her turda hata basmak yerine atla.
        if not (site.raw.get("social", {}) or {}).get("facebook_page_id"):
            print(f"    Facebook: sayfa tanımlı değil, atlandı")
            if kayit.get("instagram"):
                p.published = kayit
            continue

        try:
            # Facebook artık Instagram'ın kopyası DEĞİL. Üç fark:
            #   1. metin ayrı türetiliyor (kaydet/kaydır satırları ve
            #      linkler çıkıyor, etiket 3'e iniyor)
            #   2. carousel kareleri ALBÜM olarak gidiyor — Facebook'ta en
            #      çok paylaşım ve görüntülenme alan format
            #   3. linkler gönderi metninde değil İLK YORUMDA
            # Gerekçeler ve ölçüm: claude/facebook-kural-seti.md
            fb_kareler = [u for u in (getattr(p, "image_urls", []) or
                                      [p.image_url]) if u]
            fb_metin = social.facebook_caption(p.caption)
            fb_yorum = social.facebook_comment(
                getattr(p, "page_url", "") or p.link,
                getattr(p, "brand_url", ""), site.domain)
            fb = meta.facebook_post(fb_kareler, fb_metin, link=fb_yorum)
            kayit["facebook"] = fb.get("post_id") or fb.get("id")
            if fb.get("comment_error"):
                kayit["facebook_comment_error"] = fb["comment_error"]
            print(f"    Facebook ({fb.get('kare', 1)} kare"
                  f"{'' if fb.get('comment_id') else ', YORUM ATILAMADI'}"
                  f"): {p.subject[:40]}")
        except MetaError as exc:
            # Facebook hatası Instagram gönderisini geçersiz kılmıyor.
            kayit["facebook_error"] = str(exc)[:200]
            print(f"    FACEBOOK HATASI: {p.subject[:40]} — {exc}")

        if kayit.get("instagram") or kayit.get("facebook"):
            p.published = kayit


def bekleyenleri_yayinla(site, kuyruk: dict) -> int:
    """Onay KAPALIYKEN kuyrukta asili kalmis "ready" gonderileri cikarir.

    Onay modu acikken uretilen gonderi "ready" olarak bekler. Onay sonradan
    kapatilirsa o kayit oksuz kaliyor: onaylananlari_yayinla yalnizca
    "approved" bakiyor, plan_week de artik ayni konuyu yeniden planlamiyor.
    Yani gonderi hic cikmiyor — uretildi, parasi odendi, kuyrukta oldu.

    Metni ve gorseli hazir; bu adim model cagrisi yapmiyor, yalnizca Meta.
    Onay ACIKKEN calismiyor: orada bekleyen gonderi beklemeli.
    """
    if site.raw.get("mode", "shadow") != "live":
        return 0
    if bool((site.raw.get("social", {}) or {}).get("approval", False)):
        return 0
    kayitlar = [k for k in (kuyruk.get("posts") or [])
                if k.get("status") == "ready"]
    if not kayitlar:
        return 0
    print(f"[{site.domain}] kuyrukta asili {len(kayitlar)} gonderi yayina aliniyor")
    nesneler = [social.Post.from_dict(k) for k in kayitlar]
    yayinla(site, nesneler)
    for ham, p in zip(kayitlar, nesneler):
        ham.update(p.to_dict())
        ham["status"] = p.status
        if p.skip_reason:
            ham["skip_reason"] = p.skip_reason
    return len([p for p in nesneler if p.status == "published"])


def onaylananlari_yayinla(site, kuyruk: dict) -> int:
    """Panelden onaylanmis gonderileri yayina alir, cikan sayiyi doner.

    Panel data/<domain>/social.json icindeki kaydin status alanini
    "approved" yapiyor (ve gerekirse caption alanini elle duzeltiyor).
    Burada o kayitlar Post nesnesine geri cevrilip yayinlaniyor.
    Gorsel ve metin zaten uretilmis durumda: bu adim model cagrisi
    yapmiyor, yalnizca Meta cagrilari yapiyor.

    Her turun BASINDA calisiyor. Ayri bir is akisi kurmaya gerek yok;
    gunluk tur zaten her sabah donuyor, onayladigin gonderi ertesi
    turda cikiyor. Hemen cikmasini istersen sosyal turu elle tetikle.
    """
    if site.raw.get("mode", "shadow") != "live":
        return 0
    kayitlar = [k for k in (kuyruk.get("posts") or [])
                if k.get("status") == "approved"]
    if not kayitlar:
        return 0
    print(f"[{site.domain}] onaylanmis {len(kayitlar)} gonderi yayina aliniyor")
    nesneler = [social.Post.from_dict(k) for k in kayitlar]
    for p in nesneler:
        p.status = "ready"
    yayinla(site, nesneler)
    for ham, p in zip(kayitlar, nesneler):
        ham.update(p.to_dict())
        ham["status"] = p.status
        if p.skip_reason:
            ham["skip_reason"] = p.skip_reason
    return len([p for p in nesneler if p.status == "published"])


def yorumlari_onar(site, kuyruk: dict) -> int:
    """Yorumu atilamamis Facebook gonderilerini sonradan onarir.

    pages_manage_engagement izni gelmeden once yayinlanan gonderilerde link,
    gonderi metninin sonuna eklenerek kurtarilmisti (facebook_post icindeki
    yedek yol) ve kuyruga facebook_comment_error yazilmisti. Izin geldikten
    sonra o kayitlar duzeltilebilir: yorum atilir ve gonderi metni linksiz
    haline geri cevrilir.

    Her turun basinda calisiyor. Boylece bir izin eksigi yuzunden bozulan
    gonderiler, izin gelir gelmez kendiliginden toparlaniyor — elle
    mudahale gerekmiyor.
    """
    if site.raw.get("mode", "shadow") != "live":
        return 0
    if not (site.raw.get("social", {}) or {}).get("facebook_page_id"):
        return 0
    bozuk = [k for k in (kuyruk.get("posts") or [])
             if (k.get("published") or {}).get("facebook")
             and (k.get("published") or {}).get("facebook_comment_error")]
    if not bozuk:
        return 0

    from engine.publish_meta import Meta, MetaError
    meta = Meta(site)
    onarilan = 0
    for kayit in bozuk:
        pid = str(kayit["published"]["facebook"])
        metin = social.facebook_comment(
            kayit.get("page_url", "") or kayit.get("link", ""),
            kayit.get("brand_url", ""), site.domain)
        if not metin:
            continue
        # KIMLIK BICIMI DENEMESI. Albüm gonderisi /feed ile kuruldugu icin
        # donen kimlik "{sayfa}_{gonderi}" bicimindeydi; tek fotografli
        # gonderilerde /photos yalin bir kimlik donuyor. Yorum ucunun hangi
        # bicimi kabul ettigi gonderi turune gore degisiyor, o yuzden
        # sirayla deneniyor ve ILK basarilida duruluyor.
        adaylar = [pid]
        if "_" in pid:
            adaylar.append(pid.split("_", 1)[1])
        yorum, son_hata = None, ""
        for aday in adaylar:
            try:
                yorum = meta.comment(aday, metin)
                print(f"    yorum kabul edildi, kimlik bicimi: {aday}")
                break
            except MetaError as exc:
                son_hata = str(exc)
                print(f"    denendi {aday}: {str(exc)[:140]}")
        if yorum is None:
            print(f"    YORUM ONARILAMADI {pid}: {son_hata}")
            continue
        kayit["published"]["facebook_comment_id"] = yorum.get("id")
        kayit["published"].pop("facebook_comment_error", None)
        # Metni linksiz haline geri cevir: yedek yol linki sonuna eklemisti.
        try:
            meta.edit_post(pid, social.facebook_caption(kayit.get("caption", "")))
        except MetaError as exc:
            kayit["published"]["metin_geri_alinamadi"] = str(exc)[:200]
            print(f"    metin geri alinamadi {pid}: {exc}")
        onarilan += 1
        print(f"    yorum onarildi: {pid}")
    return onarilan


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--yalniz-makale", action="store_true",
                    dest="yalniz_makale",
                    help="sadece o gün çıkan yazının gönderisi; yazı yoksa "
                         "hiçbir şey üretme. Blog turunun sonunda kullanılır.")
    ap.add_argument("--adet", type=int, default=1,
                    help="bu turda kaç gönderi (0 = planın tamamı). "
                         "Varsayılan 1: bir tur, bir gönderi.")
    ap.add_argument("--publish-approved", action="store_true",
                    dest="publish_approved",
                    help="yalnizca panelden onaylanmis gonderileri yayina "
                         "al ve cik; yeni gonderi uretme")
    ap.add_argument("--discover", action="store_true",
                    help="Meta token'ın gördüğü sayfa ve Instagram kimliklerini yaz")
    args = ap.parse_args()

    site = config.load_site(args.site)

    if args.discover:
        from engine.publish_meta import Meta
        rapor = Meta(site).discover()
        izinler = rapor.get("scopes") or []
        if izinler:
            print("  token izinleri: " + ", ".join(sorted(izinler)))
            for gerekli in ("pages_manage_posts", "pages_manage_engagement",
                            "instagram_content_publish", "instagram_basic"):
                if gerekli not in izinler:
                    print(f"    EKSIK IZIN: {gerekli}")
        sayfa_izin = rapor.get("page_scopes") or []
        if sayfa_izin:
            print("  SAYFA token izinleri (" + str(rapor.get("page_token_type")) 
                  + "): " + ", ".join(sorted(sayfa_izin)))
            if "pages_manage_engagement" not in sayfa_izin:
                print("    SAYFA TOKENINDE pages_manage_engagement YOK")
        elif rapor.get("page_scopes_error"):
            print(f"  sayfa token izinleri OKUNAMADI: {rapor['page_scopes_error']}")
        elif rapor.get("scopes_error"):
            print(f"  token izinleri OKUNAMADI: {rapor['scopes_error']}")
        for p in rapor.get("pages", []):
            print(f"  sayfa: {p['page_name']}  page_id={p['page_id']}")
            gorevler = p.get("tasks") or []
            print("    sayfa gorevleri: " + (", ".join(gorevler) or "—")
                  + ("" if "MODERATE" in gorevler
                     else "   <- MODERATE YOK, ilk yorum atilamaz"))
            print(f"    sayfaya bagli instagram: @{p.get('instagram_username') or '—'}"
                  f"  instagram_user_id={p.get('instagram_user_id') or '—'}")
        if rapor.get("pages_error"):
            print(f"  sayfa listesi HATA: {rapor['pages_error']}")
        for r in rapor.get("owned_instagram", []):
            print(f"  portfoyun sahip oldugu instagram: @{r.get('username')}"
                  f"  instagram_user_id={r.get('instagram_user_id')}")
        if rapor.get("owned_error"):
            print(f"  portfoy instagram listesi HATA: {rapor['owned_error']}")
        # Kritik satir: sayfa baglantisi olmadan da yayin yapabilir miyiz?
        if rapor.get("direct"):
            d = rapor["direct"]
            print(f"  DOGRUDAN ERISIM VAR: @{d.get('username')} ({d.get('id')}) "
                  f"— sayfa baglantisi gerekmiyor")
        elif rapor.get("direct_error"):
            print(f"  DOGRUDAN ERISIM YOK: {rapor['direct_error']}")
        return 0

    kuyruk = social.load_queue(site)

    # ONAY KUYRUGU her turun BASINDA bosaltiliyor: panelde onayladigin
    # gonderi bir sonraki turda cikiyor. --publish-approved ile yalnizca
    # bu adim calisiyor, yeni gonderi uretilmiyor ve model masrafi yok.
    #
    # PROVA TURU: --dry-run hicbir sey yayinlamaz. 20.09.2026'ya kadar bu
    # blok prova turunda da calisiyordu; onay kuyrugundaki ve asili kalan
    # gonderiler "prova" sanilan turda CANLIYA cikiyordu.
    if args.dry_run:
        cikan = asili = onarilan = 0
        print(f"[{site.domain}] prova turu — onay kuyrugu, asili gonderiler "
              "ve yorum onarimi atlandi")
    else:
        cikan = onaylananlari_yayinla(site, kuyruk)
        # Onay kapaliyken kuyrukta kalmis gonderiler de burada cikiyor. Bu
        # turda uretilenler kuyruga en sonda ekleniyor, yani burada yalnizca
        # ONCEKI turlarin kalintilari var — ayni gonderi iki kez cikmiyor.
        asili = bekleyenleri_yayinla(site, kuyruk)
        onarilan = yorumlari_onar(site, kuyruk)
    if cikan or onarilan or asili:
        social.save_queue(site, kuyruk)
    if args.publish_approved:
        print(f"[{site.domain}] onaylanan {cikan} gonderi yayinlandi")
        return 0

    gecmis = kuyruk.get("posts", [])

    pub_path = site.data_dir / "published.json"
    published = json.loads(pub_path.read_text(encoding="utf-8")) if pub_path.exists() else []
    li_path = site.data_dir / "link-index.json"
    links = json.loads(li_path.read_text(encoding="utf-8")) if li_path.exists() else []

    # Medya kütüphanesi plan aşamasında da lazım: fotoğrafı olmayan lokasyon
    # baştan seçilmiyor.
    wp = WordPress(site)
    media = media_library(wp)
    plan = social.plan_week(site, published, links, gecmis, media,
                            adet=max(0, args.adet),
                            yalniz_makale=args.yalniz_makale)
    if not plan:
        print(f"[{site.domain}] planlanacak gönderi yok")
        return 0
    print(f"[{site.domain}] {len(plan)} gönderi planlandı: " +
          ", ".join(f"{p['kind']}/{p['subject'][:28]}" for p in plan))

    if args.dry_run:
        return 0

    from anthropic import Anthropic
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    resp = client.messages.create(**social.build_request(site, plan))
    metin_kullanim = resp.usage.model_dump() if getattr(resp, "usage", None) else {}
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    captions = social.parse_captions(text, plan)

    posts = social.assemble(site, plan, captions, media)

    # SOSYAL KALİTE KAPISI. Blogda doğrulanmamış rakam yayını durduruyordu ama
    # sosyal metinde böyle bir kontrol yoktu. 14.09.2026'da Çamlıca gönderisi
    # "360 meters" (doğrusu 369) ve "three continents" (İstanbul iki kıtada)
    # diyerek Instagram ve Facebook'a çıktı. Artık çıkamıyor.
    for p in posts:
        if p.status != "ready":
            continue
        # Kaynak metin de veriliyor: sosyal metin blogdan türüyor ve blog
        # zaten kendi kalite kapısından geçmiş. Blogda geçen bir rakam
        # sosyal metinde de geçerli. 15.09.2026'da sarnıç gönderisi iki kez
        # reddedildi ("56 meters", "1,000 years") — ikisi de yazının
        # içindeydi, yalnızca fact base'de yoktu.
        sayfa = _sayfa_metni(getattr(p, "page_url", "") or p.link)
        # Dil düzeltmeleri kapıdan ÖNCE: "an" ve etiket yazımı kesin
        # kurallar, gönderiyi düşürmeye gerek yok, düzeltip geçiyoruz.
        p.caption = social.fix_articles(p.caption)
        p.caption = social.fix_hashtags(p.caption, sayfa)
        sorunlar = social.caption_issues(site, p.subject, p.caption,
                                         kaynak=sayfa)
        if sorunlar:
            p.status = "skipped"
            p.skip_reason = "metin kapısı: " + "; ".join(sorunlar[:3])
            print(f"    METIN REDDEDILDI: {p.subject[:36]} — {p.skip_reason}")

    # HER GÖNDERİ FOTOĞRAFLI ÇIKAR. Önceki sürüm fotoğraf bulamayınca düz
    # yazı kartı basıyordu; 14.09.2026'da Instagram'a çıkan o kartlar markanın
    # standardının dışında kaldı. Artık kademe şu:
    #   1. WordPress kütüphanesindeki gerçek fotoğraf
    #   2. AI fotoğraf (engine/photo.py, kare)
    #   3. gönderiyi ATLA — fotoğrafsız yayın yok
    # Fotoğrafın üstüne marka tipografisi visuals.social_post ile basılıyor;
    # hesaptaki mevcut gönderiler de fotoğraf + tipografi düzeninde.
    from engine import visuals  # noqa: E402

    ai_acik = (site.raw.get("images", {}) or {}).get("ai_photo", False)
    ai_model, ai_boyut = "", ""
    # sira: düzen dönüşümü için. Aynı turdaki gönderiler farklı iskeletle
    # çıksın diye sayaç görsel üretilen her gönderide ilerliyor.
    sira = 0
    # Kapak ve slayt fotoğrafları birbirinden farklı olsun diye ortak küme.
    kullanilan: set[str] = set()
    for p in posts:
        if p.status != "ready":
            continue

        foto, kaynak = None, ""
        if p.image_url:
            foto = _indir(p.image_url)
            kaynak = "kutuphane" if foto else ""
            kullanilan.add(p.image_url)
        if foto is None and ai_acik:
            try:
                from engine import photo as _photo
                foto, _alt, _m, _b = _photo.generate(
                    site, p.subject, p.subject, dikey=True,
                    metin=_sayfa_metni(getattr(p, "page_url", "") or p.link))
                kaynak = "ai"
                ai_model, ai_boyut = _m, _b
            except Exception as exc:
                print(f"    FOTO HATASI: {p.subject[:40]} — "
                      f"{type(exc).__name__}: {exc}")
        if foto is None:
            p.status, p.skip_reason = "skipped", "fotoğraf bulunamadı"
            print(f"    atlandı (fotoğraf yok): {p.subject[:40]}")
            continue

        try:
            etiket = {"article": "ISTANBUL GUIDE", "location": "ISTANBUL",
                      "seasonal": "WHEN TO GO"}.get(p.kind, "ISTANBUL")
            # Görseldeki not kartı: ipucu / kural / ilginç bilgi. Fiyat, saat
            # ve süre engine/social.clean_notes ile zaten elenmiş durumda.
            # Alt satır: başlıkta ayraç yoksa metnin ilk cümlesi kullanılıyor.
            # 15.09.2026'da "Bosphorus Cruise vs Golden Horn Cruise Compared"
            # gönderisi görselde yalnızca başlıkla çıktı — model KART'ı boş
            # bırakmıştı ve başlıkta da ayraç yoktu. Hesapta her gönderide
            # başlığın altında ya bir satır ya bir kart var.
            # YARIM CÜMLE GÖRSELE BASILMAZ. 15.09.2026'da sarnıç gönderisinde
            # "Descend 18 meters below Istanbul's streets into a" diye kesik
            # bir alt satır çıktı: cümle 70 karakterde kırpılmıştı.
            # Alt satır ancak TAM ve KISA bir cümleyse basılıyor; not kartı
            # varsa zaten görselde bir öğe var, ikinciye gerek yok.
            ilk_cumle = ""
            if p.caption and not p.notes:
                ilk = p.caption.strip().splitlines()[0]
                cumle = re.split(r"(?<=[.!?])\s", ilk)[0].strip()
                if len(cumle) <= 60:
                    ilk_cumle = cumle
            # FORMAT ROTASYONU — STANDART 3.
            # İki gönderi üst üste aynı görünmesin. Kartlı hesaplarda düzen
            # zaten dönüyor; DÜZ hesapta görselde yazı olmadığı için çeşitlilik
            # KADRAJDAN geliyor: sıra 0 orta, 2 üst, 3 alt. Sıra 1 iki
            # fotoğraflı carousel (aşağıda).
            _tsr = ((site.raw.get("social", {}) or {}).get("design", {}) or {})
            _duz = str(_tsr.get("layout") or "").lower() in ("duz", "plain")
            # ROTASYON SAYACI: tur içindeki sıra DEĞİL, sitenin şimdiye
            # kadar yayınladığı gönderi sayısı. Sebep: her tur tek gönderi
            # çıkarıyor, yani "sira" hep 0. Hafta numarasıyla toplayınca bir
            # hafta boyunca bütün gönderiler AYNI formatta çıkıyordu —
            # rotasyon adı var kendi yok. Sayaç kuyruktan okunuyor.
            _gecmis = len([x for x in (kuyruk.get("posts") or [])
                           if (x.get("published") or {}).get("instagram")])
            _mod = (_gecmis + sira) % 4
            _odak = ({0: 0.5, 1: 0.5, 2: 0.25, 3: 0.75}[_mod]
                     if _duz else 0.5)
            kart = visuals.social_post(site, p.subject, etiket, foto,
                                       line=ilk_cumle, notes=p.notes,
                                       sira=_gecmis + sira,
                                       odak=_odak)
            sira += 1
            ad = re.sub(r"[^a-z0-9]+", "-", p.subject.lower()).strip("-")[:50]
            yuklenen = wp.upload_media(kart.jpeg(), f"social-{ad}.jpg",
                                       kart.alt, "")
            p.image_url = yuklenen.get("source_url", "")
            p.image_alt = kart.alt
            p.photo_source = kaynak
            if not p.image_url:
                raise RuntimeError("yukleme URL dondurmedi")
            p.image_urls = [p.image_url]

            # CAROUSEL SLAYTLARI. Metricool 2026 ölçümü: carousel gönderiler
            # tek görsele göre DOKUZ KAT fazla kaydetme alıyor ve kaydetme
            # Instagram'ın en ağır ikinci sıralama sinyali. Aynı ölçümde tek
            # görsel gönderilerin erişimi bir yılda %22 düşmüş.
            # Slayt fotoğrafı önce kütüphaneden (kapaktan farklı olanı),
            # bulunamazsa kapağın fotoğrafı yeniden kullanılıyor: slayt
            # başına ayrı AI fotoğrafı hem turu uzatıyor hem maliyeti üçe
            # katlıyor, kazancı ise kurgudan geliyor, fotoğraf çeşidinden
            # değil.
            # DÜZ DÜZENDE KARTLI SLAYT YOK — hesabın diline aykırı.
            # Ama carousel tamamen kapalı değil: rotasyonun 1. sırasında
            # gönderi İKİ YAZISIZ FOTOĞRAFTAN kurulu bir carousel oluyor
            # (STANDART 3). Kaydetme avantajı hesabın dilini bozmadan alınıyor.
            if _duz and _mod == 1:
                try:
                    _ikinci = social.pick_photo(media, p.subject, kullanilan)
                    _foto2 = _indir(_ikinci.get("source_url", "")) if _ikinci else None
                    if _foto2:
                        kullanilan.add(_ikinci.get("source_url", ""))
                    _kare2 = visuals.social_post(
                        site, p.subject, etiket, _foto2 or foto,
                        line="", notes=None, sira=_gecmis + sira,
                        odak=0.5 if _foto2 else 0.8)
                    _yuk2 = wp.upload_media(_kare2.jpeg(),
                                            f"social-{ad}-2.jpg",
                                            _kare2.alt, "")
                    if _yuk2.get("source_url"):
                        p.image_urls.append(_yuk2["source_url"])
                except Exception as exc:
                    print(f"    IKINCI KARE HATASI: {type(exc).__name__}: {exc}")

            _slaytlar = [] if _duz else getattr(p, "slides", [])[:2]
            for i, sl in enumerate(_slaytlar):
                try:
                    sfoto = None
                    secim = social.pick_photo(media, sl.get("title", "") or
                                              p.subject, kullanilan)
                    if secim:
                        sfoto = _indir(secim.get("source_url", ""))
                        if sfoto:
                            kullanilan.add(secim.get("source_url", ""))
                    # Kapak fotoğrafı yeniden kullanılıyorsa kadraj
                    # kaydırılıyor: üç karede birebir aynı görüntü
                    # kaydırmayı anlamsız kılıyor.
                    gorsel = visuals.slide(site, sl.get("title", ""),
                                           sl.get("rows", []), sfoto or foto,
                                           odak=0.5 if sfoto
                                           else (0.15 if i == 0 else 0.85))
                    yuk = wp.upload_media(gorsel.jpeg(),
                                          f"social-{ad}-{i + 2}.jpg",
                                          gorsel.alt, "")
                    if yuk.get("source_url"):
                        p.image_urls.append(yuk["source_url"])
                except Exception as exc:
                    print(f"    SLAYT HATASI {i + 2}: "
                          f"{type(exc).__name__}: {exc}")

            # KAYDIRMA SATIRI GERÇEK KARE SAYISINA GÖRE. Metin kurulurken
            # slaytlar henüz üretilmemiş oluyordu; düz düzende slayt hiç
            # basılmayınca "Swipe for both sides →" tek kareli gönderide
            # yayına çıktı (16.09.2026). Karar artık burada, görseller
            # hazırlandıktan sonra veriliyor.
            p.caption = social.fix_swipe(p.caption,
                                         len(p.image_urls or []) >= 2)

            print(f"    gorsel hazir ({kaynak}, "
                  f"{len(p.image_urls)} kare): {p.subject[:40]}")
        except Exception as exc:
            p.status, p.skip_reason = "skipped", f"gorsel uretilemedi: {exc}"
            print(f"    GORSEL HATASI: {p.subject[:40]} — {exc}")

    hazir = [p for p in posts if p.status == "ready"]
    atlanan = [p for p in posts if p.status == "skipped"]
    print(f"  hazır {len(hazir)} · atlanan {len(atlanan)}")
    for p in atlanan:
        print(f"    atlandı: {p.subject[:40]} — {p.skip_reason}")

    mode = site.raw.get("mode", "shadow")
    onay = bool((site.raw.get("social", {}) or {}).get("approval", False))
    if mode == "live" and hazir and not onay:
        yayinla(site, hazir)
    elif mode == "live" and hazir:
        # ONAY MODU. Gonderi uretildi, gorseli hazir, ama yayina
        # cikmiyor: panelde "Onay ve duzelt" ekraninda bekliyor.
        print(f"  onay modu: {len(hazir)} gonderi ONAY BEKLIYOR, "
              f"panelden onaylaninca cikacak")
    elif hazir:
        print(f"  gölge modu: {len(hazir)} gönderi kuyrukta bekliyor, "
              f"sosyal medyaya çıkmadı")

    # Maliyet: tek bir metin çağrısı bütün seti yazıyor, o yüzden metin
    # maliyeti sete ait; görsel maliyeti gönderi başına. Yayınlanmayan
    # gönderinin görseli de üretildiyse parası gitmiştir, kayda giriyor.
    try:
        from engine import cost as _cost
        model = site.globals["models"]["utility"]
        gorsel = [p for p in posts if getattr(p, "photo_source", "") == "ai"]
        _cost.record(site, {
            "kind": "sosyal",
            "result": f"{len([p for p in posts if p.status == 'published'])}/"
                      f"{len(posts)} yayınlandı",
            "subject": ", ".join(p.subject[:28] for p in posts)[:120],
            "model": model,
            "input": metin_kullanim.get("input_tokens", 0),
            "output": metin_kullanim.get("output_tokens", 0),
            "cache_read": metin_kullanim.get("cache_read_input_tokens", 0),
            "text_usd": _cost.text_usd(site, model, metin_kullanim),
            "image_model": ai_model,
            "image_size": ai_boyut,
            "image_count": len(gorsel),
            "image_usd": round(
                _cost.image_usd(ai_model, ai_boyut) * len(gorsel), 6),
        })
    except Exception as exc:
        print(f"    maliyet kaydedilemedi: {type(exc).__name__}: {exc}")

    kuyruk["posts"] = gecmis + [p.to_dict() for p in posts]
    kuyruk["last_run"] = date.today().isoformat()
    social.save_queue(site, kuyruk)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
