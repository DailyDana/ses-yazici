"""
Ses-Yazıcı güncelleyici. Baslat.cmd her açılışta çalıştırır (yalnız standart kütüphane).

- GitHub'daki son sürümü (release) yerel SURUM dosyasıyla karşılaştırır.
- Yenisi varsa kaynak arşivini indirir, program dosyalarının üzerine yazar.
- İnternet yoksa ya da bir şey ters giderse sessizce vazgeçer; program eski sürümle açılır.
- Paket listesi değiştiyse .venv\\.kurulu işaretini siler, Baslat.cmd paketleri yeniden kurar.
- calistir.cmd çalışırken üzerine yazılamayacağı için yenisi calistir.cmd.yeni olarak bırakılır;
  Baslat.cmd bir sonraki açılışta yerine koyar. Baslat.cmd hiç güncellenmez.

Modeller, .venv ve kayıtlar güncellemeden etkilenmez.
"""
import hashlib
import io
import json
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

DEPO = "DailyDana/ses-yazici"
KOK = Path(__file__).resolve().parent
# Güncellemenin dokunabileceği dosyalar; bunların dışındaki hiçbir şeye yazılmaz
DOSYALAR = ("app.py", "surec_ses.py", "guncelle.py", "requirements.txt", "requirements-nvidia.txt",
            "OKU-BENI.txt", "SURUM", "LICENSE", "README.md", "web/index.html", "calistir.cmd")


def surum_tuple(s):
    parcalar = []
    for p in s.strip().lstrip("vV").split("."):
        rakam = "".join(c for c in p if c.isdigit())
        parcalar.append(int(rakam) if rakam else 0)
    return tuple(parcalar)


def paket_ozeti():
    h = hashlib.sha256()
    for ad in ("requirements.txt", "requirements-nvidia.txt"):
        f = KOK / ad
        h.update(f.read_bytes().replace(b"\r\n", b"\n") if f.exists() else b"")
    return h.hexdigest()


def indir(url, sinir=20 * 1024 * 1024):
    istek = urllib.request.Request(url, headers={"User-Agent": "ses-yazici-guncelleyici",
                                                 "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(istek, timeout=8) as r:
        veri = r.read(sinir + 1)
    if len(veri) > sinir:
        raise ValueError("beklenenden büyük dosya")
    return veri


def guncelle():
    yerel = (KOK / "SURUM").read_text(encoding="utf-8").strip() if (KOK / "SURUM").exists() else "0"
    son = json.loads(indir(f"https://api.github.com/repos/{DEPO}/releases/latest"))
    etiket = son.get("tag_name", "")
    if not etiket or surum_tuple(etiket) <= surum_tuple(yerel):
        return None
    print(f"Yeni sürüm bulundu: {yerel} -> {etiket}. Güncelleniyor...")
    arsiv = zipfile.ZipFile(io.BytesIO(indir(son["zipball_url"])))
    kok_ad = arsiv.namelist()[0].split("/")[0] + "/"

    # önce hepsini belleğe al: eksik/bozuk indirmede yarım güncelleme olmasın
    yeni = {}
    for ad in DOSYALAR:
        try:
            yeni[ad] = arsiv.read(kok_ad + ad)
        except KeyError:
            pass
    if "app.py" not in yeni or "SURUM" not in yeni:
        raise ValueError("arşivde beklenen dosyalar yok")

    yedek = KOK / ".yedek"
    shutil.rmtree(yedek, ignore_errors=True)
    for ad, icerik in yeni.items():
        hedef = KOK / ad
        if hedef.exists():
            (yedek / ad).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(hedef, yedek / ad)
        if ad == "calistir.cmd":
            icerik = icerik.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")  # cmd CRLF ister
            if not hedef.exists() or hedef.read_bytes() != icerik:
                (KOK / "calistir.cmd.yeni").write_bytes(icerik)
            continue
        hedef.parent.mkdir(parents=True, exist_ok=True)
        gecici = hedef.with_name(hedef.name + ".tmp")
        gecici.write_bytes(icerik)
        gecici.replace(hedef)
    return etiket


def main():
    isaret = KOK / ".venv" / ".kurulu"
    if "--isaretle" in sys.argv:  # paketler kuruldu: hangi listeyle kurulduğunu kaydet
        isaret.write_text(paket_ozeti(), encoding="utf-8")
        return 0
    try:
        etiket = guncelle()
        if etiket:
            print(f"Güncellendi: {etiket}")
    except Exception as e:  # çevrimdışı, GitHub sınırı vb.: eski sürümle devam
        print(f"Güncelleme denetlenemedi ({type(e).__name__}); mevcut sürümle devam ediliyor.")
    if isaret.exists() and isaret.read_text(encoding="utf-8").strip() != paket_ozeti():
        isaret.unlink()  # paket listesi değişti: calistir.cmd yeniden kuracak
    return 0


if __name__ == "__main__":
    sys.exit(main())
