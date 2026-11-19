"""
Ses-Yazıcı: seçilen uygulamanın (ör. tarayıcının) sesini dinleyip canlı olarak yazıya döker.

- Arayüz: http://127.0.0.1:<port> (açılışta tarayıcıda kendiliğinden açılır)
- Motor: OpenVINO GenAI Whisper; Intel GPU varsa onu, yoksa işlemciyi kullanır
- Modeller ilk kullanımda ./modeller altına indirilir
- Kayıtlar: Belgeler\\Ses-Yazici\\<tarih>_<uygulama>.txt
"""
import surec_ses  # comtypes'tan ÖNCE içe aktarılmalı (COM çok iş parçacıklı mod)

try:
    import ses_yonlendir  # "Ben duymayayım": uygulamanın sesini başka çıkışa alır
except Exception:  # dosya eksik (yarım güncelleme) ya da bu Windows'ta desteklenmiyor
    ses_yonlendir = None

import datetime as dt
import json
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np

KOK = Path(__file__).resolve().parent
MODEL_KOK = KOK / "modeller"
WEB = KOK / "web"
SURUM = (KOK / "SURUM").read_text(encoding="utf-8").strip() if (KOK / "SURUM").exists() else "?"
def belgeler_klasoru():
    """Kullanıcının gerçek Belgeler klasörü (OneDrive'a taşınmış olabilir)."""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as k:
            return Path(os.path.expandvars(winreg.QueryValueEx(k, "Personal")[0]))
    except OSError:
        return Path(os.path.expanduser("~")) / "Documents"


CIKTI_KOK = belgeler_klasoru() / "Ses-Yazici"
SR = 16000

MODELLER = {
    "turbo": {"ad": "Yüksek doğruluk (önerilen)",
              "ov": ("OpenVINO/whisper-large-v3-turbo-int8-ov", 830),
              "fw": ("dropbox-dash/faster-whisper-large-v3-turbo", 1620)},
    "small": {"ad": "Hızlı – zayıf bilgisayarlar için",
              "ov": ("OpenVINO/whisper-small-int8-ov", 260),
              "fw": ("Systran/faster-whisper-small", 490)},
}


def nvidia_var():
    try:
        return subprocess.run(["nvidia-smi", "-L"], capture_output=True, timeout=10,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).returncode == 0
    except Exception:
        return False


def cuda_dll_yollari():
    """pip ile gelen nvidia-cublas/cudnn DLL'lerini ctranslate2'nin bulabilmesi için yola ekle."""
    import site

    for sp in site.getsitepackages() + [site.getusersitepackages()]:
        nv = Path(sp) / "nvidia"
        if nv.is_dir():
            for b in nv.glob("*/bin"):
                os.add_dll_directory(str(b))
                os.environ["PATH"] = str(b) + os.pathsep + os.environ.get("PATH", "")


def motor_turu():
    """'fw' (NVIDIA + faster-whisper kuruluysa) ya da 'ov' (OpenVINO: Intel GPU / CPU).
    SES_YAZICI_MOTOR=ov|fw ortam değişkeni seçimi zorlar (sorun giderme için)."""
    zorla = os.environ.get("SES_YAZICI_MOTOR", "").lower()
    if zorla in ("ov", "fw"):
        return zorla
    if nvidia_var():
        try:
            import faster_whisper  # noqa: F401
            return "fw"
        except ImportError:
            pass
    return "ov"


MOTOR_TURU = motor_turu()
TARAYICILAR = {
    "firefox.exe": "Firefox", "chrome.exe": "Google Chrome", "msedge.exe": "Microsoft Edge",
    "brave.exe": "Brave", "opera.exe": "Opera", "opera_gx.exe": "Opera GX", "vivaldi.exe": "Vivaldi",
    "yandex.exe": "Yandex Browser", "zen.exe": "Zen Browser", "librewolf.exe": "LibreWolf",
}
SISTEM_DIS = {"svchost.exe", "audiodg.exe", "system", "explorer.exe", "python.exe", "pythonw.exe",
              "shellexperiencehost.exe", "searchhost.exe", "applicationframehost.exe"}

# parçalama ve filtre ayarları (C:\tools\Ses-Yazi'de denenmiş değerler)
MIN_SN, MAX_SN, ARAMA_SN = 25, 40, 8
SESSIZ_ESIK = 0.003
UYDURMA = ("izlediğiniz için teşekkür", "altyazı m.k", "altyazı:", "abone olmayı unutmayın", "abone ol",
           "bir sonraki videoda görüşmek", "thank you for watching", "thanks for watching", "subtitles by")


# ------------------------------------------------------------------ olay yayını (SSE)
class Yayin:
    def __init__(self):
        self.kuyruklar = []
        self.kilit = threading.Lock()
        self.son_durum = {}

    def abone(self):
        q = queue.Queue()
        with self.kilit:
            self.kuyruklar.append(q)
        return q

    def birak(self, q):
        with self.kilit:
            if q in self.kuyruklar:
                self.kuyruklar.remove(q)

    def gonder(self, tur, **veri):
        olay = {"tur": tur, **veri}
        if tur == "durum":
            self.son_durum = olay
        with self.kilit:
            for q in self.kuyruklar:
                q.put(olay)


YAYIN = Yayin()


def gunluk(metin):
    """Hataları program klasöründeki hata.log'a yaz (kullanıcı gönderebilsin diye)."""
    try:
        with open(KOK / "hata.log", "a", encoding="utf-8") as f:
            f.write(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} v{SURUM} {MOTOR_TURU}: {metin}\n")
    except OSError:
        pass


def durum(metin, **ek):
    YAYIN.gonder("durum", metin=metin, kayit=KAYIT.calisiyor, **ek)


# ------------------------------------------------------------------ model
class Motor:
    def __init__(self):
        self.pipe = None
        self.anahtar = None
        self.cihaz = None
        self.kilit = threading.Lock()

    @staticmethod
    def repo(anahtar):
        return MODELLER[anahtar][MOTOR_TURU][0]

    @staticmethod
    def mb(anahtar):
        return MODELLER[anahtar][MOTOR_TURU][1]

    def klasor(self, anahtar):
        return MODEL_KOK / self.repo(anahtar).split("/")[1]

    def indirilmis_mi(self, anahtar):
        return (self.klasor(anahtar) / ".tamam").exists()

    def indir(self, anahtar):
        from huggingface_hub import HfApi, hf_hub_download

        repo = self.repo(anahtar)
        hedef = self.klasor(anahtar)
        hedef.mkdir(parents=True, exist_ok=True)
        bilgi = HfApi().model_info(repo, files_metadata=True)
        dosyalar = [(s.rfilename, s.size or 0) for s in bilgi.siblings]
        toplam = sum(b for _, b in dosyalar) or 1
        bitti = 0
        for ad, boyut in dosyalar:
            yol = hedef / ad
            if yol.exists() and yol.stat().st_size == boyut:
                bitti += boyut
                continue
            from tqdm import tqdm

            baz, son = bitti, [0.0]

            class Ilerleme(tqdm):
                """İndirilen bayt sayısını arayüze bildirir. (Geçici dosya boyutuna bakmak Xet
                indirmelerinde işe yaramıyor: veri sona kadar diske yazılmıyor.)"""

                def update(self, n=1):
                    sonuc = super().update(n)
                    if (self.total or 0) > 1024 * 1024 and time.time() - son[0] > 0.4:
                        son[0] = time.time()
                        yuzde = min(99.0, 100 * (baz + self.n) / toplam)
                        YAYIN.gonder("indirme", yuzde=round(yuzde, 1), dosya=ad)
                    return sonuc

            self._dayanikli_indir(repo, ad, hedef, Ilerleme)
            bitti += boyut
            YAYIN.gonder("indirme", yuzde=round(100 * bitti / toplam, 1), dosya=ad)
        (hedef / ".tamam").write_text("ok")

    @staticmethod
    def _dayanikli_indir(repo, ad, hedef, ilerleme):
        """Bağlantı kopmasına karşı 4 deneme. 2. denemeden itibaren Xet kapatılır: bazı ağlarda
        (kurum/yurt ağı, güvenlik duvarı) Xet sunucularına erişilemiyor, düz HTTPS çalışıyor."""
        from huggingface_hub import constants, hf_hub_download

        son_hata = None
        for deneme in range(4):
            if deneme >= 1:
                constants.HF_HUB_DISABLE_XET = True
                YAYIN.gonder("uyari", metin=f"İndirme kesildi, yeniden deneniyor ({deneme + 1}/4)…")
                time.sleep(2 * deneme)
            try:
                return hf_hub_download(repo, ad, local_dir=str(hedef), tqdm_class=ilerleme)
            except Exception as e:
                son_hata = e
                gunluk(f"indirme hatası ({repo}/{ad}, deneme {deneme + 1}): {e!r}")
        raise RuntimeError(f"Model indirilemedi: {son_hata}. İnternet bağlantısını kontrol edip tekrar deneyin.")

    def hazirla(self, anahtar):
        with self.kilit:
            if self.pipe is not None and self.anahtar == anahtar:
                return
            if not self.indirilmis_mi(anahtar):
                durum(f"Model indiriliyor: {MODELLER[anahtar]['ad']} (~{self.mb(anahtar)} MB, bir kerelik)")
                self.indir(anahtar)
            durum("Model yükleniyor…")
            if MOTOR_TURU == "fw":
                cuda_dll_yollari()
                from faster_whisper import WhisperModel

                hata = None
                for cihaz, tur in (("cuda", "float16"), ("cpu", "int8")):
                    try:
                        self.pipe = WhisperModel(str(self.klasor(anahtar)), device=cihaz, compute_type=tur)
                        # CUDA kütüphanesi eksikse hata ilk çözümlemede çıkar: kısa sessizlikle dene
                        list(self.pipe.transcribe(np.zeros(SR, np.float32), language="tr")[0])
                        self.anahtar, self.cihaz = anahtar, ("NVIDIA GPU" if cihaz == "cuda" else "CPU")
                        return
                    except Exception as e:
                        hata = e
                        self.pipe = None
                raise RuntimeError(f"Model yüklenemedi: {hata}")
            import openvino as ov
            import openvino_genai as ov_genai

            aygitlar = ov.Core().available_devices
            denenecek = (["GPU"] if any(a.startswith("GPU") for a in aygitlar) else []) + ["CPU"]
            hata = None
            for cihaz in denenecek:
                try:
                    self.pipe = ov_genai.WhisperPipeline(str(self.klasor(anahtar)), cihaz)
                    self.anahtar, self.cihaz = anahtar, ("Intel GPU" if cihaz == "GPU" else "CPU")
                    return
                except Exception as e:  # GPU sürücüsü uygun değilse işlemciye düş
                    hata = e
            raise RuntimeError(f"Model yüklenemedi: {hata}")

    def yaziya_dok(self, ses, dil):
        n = int(0.03 * SR)
        cer = ses[: len(ses) // n * n].reshape(-1, n)
        if len(cer) == 0 or (np.sqrt((cer**2).mean(axis=1)) > SESSIZ_ESIK * 3).mean() < 0.15:
            return ""
        if MOTOR_TURU == "fw":
            parcalar, _ = self.pipe.transcribe(ses.astype(np.float32), language=None if dil == "auto" else dil,
                                               beam_size=5, condition_on_previous_text=False)
            metin = " ".join(p.text.strip() for p in parcalar).strip()
        else:
            cfg = self.pipe.get_generation_config()
            if dil != "auto":
                cfg.language = f"<|{dil}|>"
            cfg.task = "transcribe"
            cfg.return_timestamps = False
            metin = str(self.pipe.generate(ses.astype(np.float32).tolist(), cfg)).strip()

        def kucuk(c):
            return c.replace("İ", "i").replace("I", "ı").lower()

        cumleler = re.split(r"(?<=[.!?])\s+", metin)
        return " ".join(c for c in cumleler if c and not any(u in kucuk(c) for u in UYDURMA)).strip()


MOTOR = Motor()


def arka_planda_hazirla(anahtar):
    """Modeli kayıt beklemeden indirip yükle (sayfa açılınca ve model değişince çağrılır)."""
    def calis():
        try:
            MOTOR.hazirla(anahtar)
            if not KAYIT.calisiyor:
                durum(f"Model hazır ({MOTOR.cihaz}). Bir uygulama seçip kaydı başlatın.")
            YAYIN.gonder("model_hazir", model=anahtar, cihaz=MOTOR.cihaz)
        except Exception as e:
            traceback.print_exc()
            gunluk(traceback.format_exc())
            YAYIN.gonder("hata", metin=f"Model hazırlanamadı: {e}")

    threading.Thread(target=calis, daemon=True).start()


# ------------------------------------------------------------------ ses kaynakları
def kaynaklari_listele():
    """Ses çalan uygulamalar + açık tarayıcılar. [{id, ad, calıyor}]"""
    import comtypes
    import psutil

    comtypes.CoInitializeEx(0)
    calanlar = set()
    try:
        from pycaw.pycaw import AudioUtilities

        for s in AudioUtilities.GetAllSessions():
            if s.Process and s.State == 1:  # 1 = etkin (ses çalıyor)
                calanlar.add(s.Process.name().lower())
            elif s.Process:
                calanlar.add("~" + s.Process.name().lower())
    except Exception:
        pass

    acik = {}
    for p in psutil.process_iter(["name"]):
        ad = (p.info["name"] or "").lower()
        if ad:
            acik[ad] = True

    sonuc = []
    gorulen = set()
    for exe, ad in TARAYICILAR.items():
        if exe in acik:
            sonuc.append({"id": exe, "ad": ad, "caliyor": exe in calanlar})
            gorulen.add(exe)
    for c in sorted(calanlar):
        exe = c.lstrip("~")
        if exe in gorulen or exe in SISTEM_DIS:
            continue
        gorulen.add(exe)
        sonuc.append({"id": exe, "ad": exe.removesuffix(".exe").capitalize(), "caliyor": not c.startswith("~")})
    sonuc.sort(key=lambda k: (not k["caliyor"], k["id"] not in TARAYICILAR, k["ad"]))
    sonuc.append({"id": "*sistem*", "ad": "Bilgisayarın tüm sesi", "caliyor": True})
    return sonuc


# ------------------------------------------------------------------ "Ben duymayayım"
YON_ISARET = KOK / ".yonlendirme"  # kayıt sürerken hangi uygulamanın sesi başka çıkışa alındı


def agac_pidleri(kok_pid):
    import psutil

    try:
        kok = psutil.Process(kok_pid)
        return [kok_pid] + [c.pid for c in kok.children(recursive=True)]
    except psutil.Error:
        return [kok_pid]


def sessiz_cikislar():
    if ses_yonlendir is None:
        return []
    try:
        return ses_yonlendir.sessiz_cikislar()
    except Exception:
        return []


def yonlendirmeyi_geri_al():
    """Uygulamanın sesini Windows varsayılanına döndür. Program kayıt sırasında kapandıysa
    işaret dosyası kalır; bir sonraki açılışta buradan düzeltilir."""
    if ses_yonlendir is None or not YON_ISARET.exists():
        return
    try:
        exe = YON_ISARET.read_text(encoding="utf-8").strip()
        pid = surec_ses.kok_pid_bul(exe)
        if pid is None:
            return  # uygulama kapalı: işaret kalsın, açıkken yeniden denenir
        if ses_yonlendir.geri_al(agac_pidleri(pid)):
            YON_ISARET.unlink(missing_ok=True)
    except Exception:
        gunluk(traceback.format_exc())


# ------------------------------------------------------------------ kayıt
class Kayit:
    def __init__(self):
        self.calisiyor = False
        self.dur = threading.Event()
        self.dosya = None
        self.th = None
        self.kilit = threading.Lock()

    def baslat(self, kaynak, model, dil, sessiz=""):
        with self.kilit:
            if self.calisiyor:
                return False
            self.calisiyor = True
        self.dur.clear()
        self.th = threading.Thread(target=self._calis, args=(kaynak, model, dil, sessiz), daemon=True)
        self.th.start()
        return True

    def durdur(self):
        self.dur.set()

    def _calis(self, kaynak, model, dil, sessiz=""):
        try:
            MOTOR.hazirla(model)
            CIKTI_KOK.mkdir(parents=True, exist_ok=True)
            etiket = "sistem" if kaynak == "*sistem*" else kaynak.removesuffix(".exe")
            self.dosya = CIKTI_KOK / f"{dt.datetime.now():%Y-%m-%d_%H-%M}_{etiket}.txt"

            if kaynak == "*sistem*":
                import warnings

                import soundcard as sc

                warnings.filterwarnings("ignore", category=sc.SoundcardRuntimeWarning)
                hop = sc.default_speaker()
                kaydedici = sc.get_microphone(id=str(hop.name), include_loopback=True).recorder(
                    samplerate=SR, channels=1, blocksize=1024)
                kaynak_ad = f"tüm ses ({hop.name})"
                izlenen_pid = None
            else:
                pid = surec_ses.kok_pid_bul(kaynak)
                if pid is None:
                    raise RuntimeError(f"{kaynak} çalışmıyor. Önce uygulamayı açın.")
                kaydedici = surec_ses.SurecKaydedici(pid)
                kaynak_ad = kaynak.removesuffix(".exe")
                izlenen_pid = pid
                if sessiz and ses_yonlendir is not None:
                    yonlendirmeyi_geri_al()  # önceki kayıttan kalan varsa önce onu düzelt
                    YON_ISARET.write_text(kaynak, encoding="utf-8")
                    if ses_yonlendir.yonlendir(agac_pidleri(pid), sessiz):
                        kaynak_ad += " (ses size verilmiyor)"
                    else:
                        YON_ISARET.unlink(missing_ok=True)
                        YAYIN.gonder("uyari", metin="Ses başka çıkışa alınamadı; kayıt sürüyor ama sesi "
                                                    "siz de duyacaksınız.")

            durum(f"Kayıt sürüyor: {kaynak_ad} • {MOTOR.cihaz} • kayıt dosyası hazır", dosya=str(self.dosya))
            YAYIN.gonder("basladi", dosya=str(self.dosya), cihaz=MOTOR.cihaz)

            is_kuyrugu = queue.Queue()

            def isci():
                while True:
                    oge = is_kuyrugu.get()
                    if oge is None:
                        return
                    zaman, ses = oge
                    try:
                        metin = MOTOR.yaziya_dok(ses, dil)
                    except Exception as e:
                        YAYIN.gonder("hata", metin=f"Yazıya dökme hatası: {e}")
                        continue
                    if metin:
                        with open(self.dosya, "a", encoding="utf-8") as f:
                            f.write(f"[{zaman}] {metin}\n\n")
                        YAYIN.gonder("satir", zaman=zaman, metin=metin)

            th = threading.Thread(target=isci, daemon=True)
            th.start()

            buf = np.zeros(0, dtype=np.float32)
            bas = dt.datetime.now()
            pencere = int(0.3 * SR)
            import psutil

            tur = 0
            uyarildi = False
            kaynak_kapandi = False
            with kaydedici as rec:
                while not self.dur.is_set():
                    veri = rec.record(numframes=SR // 2).reshape(-1).astype(np.float32)
                    tur += 1
                    if tur % 6 == 0:  # ~3 sn'de bir
                        if izlenen_pid is not None and not psutil.pid_exists(izlenen_pid):
                            kaynak_kapandi = True
                            break
                        bekleyen = is_kuyrugu.qsize()
                        if bekleyen >= 3 and not uyarildi:
                            uyarildi = True
                            YAYIN.gonder("uyari", metin=f"Yazıya dökme geride kalıyor ({bekleyen} parça bekliyor). "
                                                        'Sonraki kayıtta "Hızlı" modeli deneyin.')
                    YAYIN.gonder("seviye", rms=float(np.sqrt(np.mean(veri**2))))
                    buf = np.concatenate([buf, veri])
                    n = len(buf)
                    if n >= MIN_SN * SR:
                        b0 = max(0, n - ARAMA_SN * SR)
                        enerji = np.convolve(buf[b0:] ** 2, np.ones(pencere) / pencere, mode="valid")
                        i = int(np.argmin(enerji))
                        if enerji[i] < (SESSIZ_ESIK**2) * 4 or n >= MAX_SN * SR:
                            k = b0 + i + pencere // 2
                            is_kuyrugu.put((bas.strftime("%H:%M:%S"), buf[:k].copy()))
                            buf, bas = buf[k:], dt.datetime.now()
            if len(buf) > SR:
                is_kuyrugu.put((bas.strftime("%H:%M:%S"), buf))
            durum("Kalan ses yazıya dökülüyor…")
            is_kuyrugu.put(None)
            th.join()
            if kaynak_kapandi:
                YAYIN.gonder("hata", metin=f"{kaynak_ad} kapandı; kayıt durduruldu. O ana kadarki metin kaydedildi. "
                                           "Uygulamayı açıp kaydı yeniden başlatın.")
            else:
                durum("Kayıt durdu.", dosya=str(self.dosya))
        except Exception as e:
            traceback.print_exc()
            gunluk(traceback.format_exc())
            YAYIN.gonder("hata", metin=f"Hata: {e}")
        finally:
            yonlendirmeyi_geri_al()
            self.calisiyor = False
            YAYIN.gonder("bitti", dosya=str(self.dosya) if self.dosya else "")


KAYIT = Kayit()


# ------------------------------------------------------------------ HTTP
class Isleyici(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _izinli(self):
        """Yalnız bu programın kendi sayfasından gelen istekleri kabul et.
        Host denetimi DNS rebinding'i, Origin denetimi başka sitelerden gelen istekleri engeller."""
        port = self.server.server_address[1]
        gecerli = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.headers.get("Host", "") not in gecerli:
            return False
        origin = self.headers.get("Origin")
        if origin is not None and origin not in {f"http://{h}" for h in gecerli}:
            return False
        if self.headers.get("Sec-Fetch-Site") in ("cross-site", "same-site"):
            return False
        return True

    def _json(self, veri, kod=200):
        govde = json.dumps(veri, ensure_ascii=False).encode()
        self.send_response(kod)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(govde)))
        self.end_headers()
        self.wfile.write(govde)

    def do_GET(self):
        if not self._izinli():
            return self.send_error(403)
        if self.path in ("/", "/index.html"):
            govde = (WEB / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(govde)
        elif self.path == "/kaynaklar":
            self._json(kaynaklari_listele())
        elif self.path == "/cikislar":
            self._json(sessiz_cikislar())
        elif self.path == "/bilgi":
            self._json({
                "modeller": [{"id": k, "ad": v["ad"], "mb": MOTOR.mb(k), "inmis": MOTOR.indirilmis_mi(k)}
                             for k, v in MODELLER.items()],
                "motor": "NVIDIA (faster-whisper)" if MOTOR_TURU == "fw" else "OpenVINO (Intel GPU / CPU)",
                "kayit": KAYIT.calisiyor, "klasor": str(CIKTI_KOK), "surum": SURUM, "uygulama": "ses-yazici",
                "durum": YAYIN.son_durum.get("metin", ""),
            })
        elif self.path == "/olaylar":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            q = YAYIN.abone()
            try:
                while True:
                    try:
                        olay = q.get(timeout=15)
                        self.wfile.write(f"data: {json.dumps(olay, ensure_ascii=False)}\n\n".encode())
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                pass
            finally:
                YAYIN.birak(q)
        else:
            self.send_error(404)

    def do_POST(self):
        if not self._izinli():
            return self.send_error(403)
        uzunluk = min(int(self.headers.get("Content-Length") or 0), 65536)
        try:
            veri = json.loads(self.rfile.read(uzunluk) or b"{}")
        except ValueError:
            return self.send_error(400)
        if not isinstance(veri, dict):
            return self.send_error(400)
        if self.path == "/baslat":
            model = veri.get("model", "turbo")
            if model not in MODELLER:
                return self._json({"ok": False, "hata": "Bilinmeyen model"})
            sessiz = str(veri.get("sessiz") or "")
            if sessiz and sessiz not in {c["id"] for c in sessiz_cikislar()}:
                return self._json({"ok": False, "hata": "Seçilen ses çıkışı artık yok; listeyi yenileyin."})
            if not KAYIT.baslat(str(veri.get("kaynak", "*sistem*")), model, str(veri.get("dil", "tr")), sessiz):
                return self._json({"ok": False, "hata": "Kayıt zaten sürüyor"})
            self._json({"ok": True})
        elif self.path == "/hazirla":
            model = veri.get("model", "turbo")
            if model not in MODELLER:
                return self._json({"ok": False, "hata": "Bilinmeyen model"})
            if not KAYIT.calisiyor:
                arka_planda_hazirla(model)
            self._json({"ok": True})
        elif self.path == "/durdur":
            KAYIT.durdur()
            self._json({"ok": True})
        elif self.path == "/klasor":
            CIKTI_KOK.mkdir(parents=True, exist_ok=True)
            hedef = veri.get("dosya")
            # yalnız kendi kayıt klasörümüzdeki dosyalar gösterilir
            if hedef and Path(hedef).is_file() and Path(hedef).resolve().parent == CIKTI_KOK.resolve():
                subprocess.Popen(f'explorer /select,"{Path(hedef).resolve()}"')
            else:
                os.startfile(CIKTI_KOK)
            self._json({"ok": True})
        elif self.path == "/kapat":
            self._json({"ok": True})
            KAYIT.durdur()

            def kapat():
                if KAYIT.th is not None:
                    KAYIT.th.join(timeout=120)  # kalan ses dosyaya yazılsın
                time.sleep(0.5)
                os._exit(0)

            threading.Thread(target=kapat, daemon=True).start()
        else:
            self.send_error(404)


def bos_port(tercih=8790):
    for p in range(tercih, tercih + 20):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    return 0


def zaten_acik():
    """Program zaten çalışıyorsa adresini döndür (iki kopya aynı sesi iki kez kaydetmesin)."""
    import urllib.request

    for p in range(8790, 8794):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{p}/bilgi", timeout=0.7) as r:
                if json.loads(r.read()).get("uygulama") == "ses-yazici":
                    return f"http://127.0.0.1:{p}/"
        except Exception:
            continue
    return None


def main():
    acik = zaten_acik()
    if acik:
        print(f"Ses-Yazıcı zaten çalışıyor: {acik}")
        if "--tarayici-acma" not in sys.argv:
            webbrowser.open(acik)
        return
    yonlendirmeyi_geri_al()  # önceki oturum kayıt sırasında kapandıysa sesi geri ver
    port = bos_port()
    srv = ThreadingHTTPServer(("127.0.0.1", port), Isleyici)
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    print(f"Ses-Yazıcı çalışıyor: {url}")
    print("Bu pencereyi kapatırsanız program da kapanır.")
    if "--tarayici-acma" not in sys.argv:
        webbrowser.open(url)
    srv.serve_forever()


if __name__ == "__main__":
    main()
