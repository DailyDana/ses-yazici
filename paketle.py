"""Dağıtım paketini üretir: python paketle.py  ->  ..\\Ses-Yazici.zip
Sürüm çıkarırken: SURUM'u artır, commit + push, paketle, `gh release create vX.Y.Z ..\\Ses-Yazici.zip`."""
import zipfile
from pathlib import Path

KOK = Path(__file__).resolve().parent
DOSYALAR = ["Baslat.cmd", "calistir.cmd", "app.py", "surec_ses.py", "guncelle.py", "requirements.txt",
            "requirements-nvidia.txt", "OKU-BENI.txt", "SURUM", "LICENSE", "web/index.html", "ses_yonlendir.py"]
CRLF = {"Baslat.cmd", "calistir.cmd", "OKU-BENI.txt"}

hedef = KOK.parent / "Ses-Yazici.zip"
with zipfile.ZipFile(hedef, "w", zipfile.ZIP_DEFLATED) as z:
    for ad in DOSYALAR:
        veri = (KOK / ad).read_bytes()
        if ad in CRLF:
            veri = veri.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        z.writestr("Ses-Yazici/" + ad, veri)
print(hedef, hedef.stat().st_size, "bayt, sürüm", (KOK / "SURUM").read_text().strip())
