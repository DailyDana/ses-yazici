"""Bir uygulamanın sesini başka bir ses çıkışına yönlendirir ("Ben duymayayım" özelliği).

Windows'un Ayarlar > Ses > Birim karıştırıcı ekranındaki "Çıkış cihazı" seçimiyle aynı işi yapar.
Bunun belgelenmiş bir API'si yok; EarTrumpet gibi programların kullandığı
Windows.Media.Internal.AudioPolicyConfig arayüzü kullanılıyor. Bir Windows güncellemesi bunu
bozarsa işlevler False döner, kayıt yine çalışır (yalnızca ses kullanıcıya da gelir).
"""
import ctypes
from ctypes import HRESULT, POINTER, WINFUNCTYPE, byref, c_int, c_uint, c_void_p, c_wchar_p

from comtypes import GUID

SINIF = "Windows.Media.Internal.AudioPolicyConfig"
# Windows 10 21H2+ / 11, ardından daha eski sürümler
IIDLER = ("{ab3d4648-e242-459f-b02f-541c70306324}", "{2a59116d-6c4f-45e0-a74f-707e3fef9258}")
ON_EK = "\\\\?\\SWD#MMDEVAPI#"
SON_EK = "#{e6327cad-dcec-4949-ae8a-991e976a79d2}"  # ses çıkışı (render) arayüzü
# vtable: IUnknown (3) + IInspectable (3) + 19 kullanılmayan yöntem
SIRA_AYARLA = 25
SIRA_OKU = 26
E_RENDER = 0
ROLLER = (0, 1)  # eConsole, eMultimedia

_combase = ctypes.WinDLL("combase")
_combase.WindowsCreateString.argtypes = [c_wchar_p, c_uint, POINTER(c_void_p)]
_combase.WindowsCreateString.restype = HRESULT
_combase.WindowsDeleteString.argtypes = [c_void_p]
_combase.WindowsGetStringRawBuffer.argtypes = [c_void_p, POINTER(c_uint)]
_combase.WindowsGetStringRawBuffer.restype = c_wchar_p
_combase.RoGetActivationFactory.argtypes = [c_void_p, POINTER(GUID), POINTER(c_void_p)]
_combase.RoGetActivationFactory.restype = HRESULT


def _hstring(metin):
    h = c_void_p()
    _combase.WindowsCreateString(metin, len(metin), byref(h))
    return h


def _com_baslat():
    """Çağıran iş parçacığında COM'u başlat (zaten başlatılmışsa dokunma)."""
    try:
        ctypes.windll.ole32.CoInitializeEx(None, 0)
    except OSError:
        pass


def _fabrika():
    _com_baslat()
    sinif = _hstring(SINIF)
    try:
        for iid in IIDLER:
            p = c_void_p()
            try:
                _combase.RoGetActivationFactory(sinif, byref(GUID(iid)), byref(p))
            except OSError:
                continue
            if p.value:
                return p
    finally:
        _combase.WindowsDeleteString(sinif)
    return None


def _yontem(fabrika, sira, *argtipleri):
    vtable = ctypes.cast(fabrika, POINTER(POINTER(c_void_p))).contents
    return WINFUNCTYPE(HRESULT, c_void_p, *argtipleri)(vtable[sira])


def _ayarla(pidler, cihaz_id):
    """cihaz_id None ise uygulama Windows varsayılanına döner. En az bir süreçte başarılıysa True."""
    try:
        fabrika = _fabrika()
        if fabrika is None:
            return False
        ayarla = _yontem(fabrika, SIRA_AYARLA, c_uint, c_int, c_int, c_void_p)
        h = _hstring(ON_EK + cihaz_id + SON_EK) if cihaz_id else c_void_p()
        basarili = False
        try:
            for pid in pidler:
                for rol in ROLLER:
                    try:
                        ayarla(fabrika, pid, E_RENDER, rol, h)
                        basarili = True
                    except OSError:
                        pass
        finally:
            if cihaz_id:
                _combase.WindowsDeleteString(h)
        return basarili
    except Exception:
        return False


def yonlendir(pidler, cihaz_id):
    return _ayarla(pidler, cihaz_id)


def geri_al(pidler):
    return _ayarla(pidler, None)


def simdiki(pid):
    """Sürecin şu anki özel çıkışı ('' = Windows varsayılanı). Deneme/tanı için."""
    try:
        fabrika = _fabrika()
        oku = _yontem(fabrika, SIRA_OKU, c_uint, c_int, c_int, POINTER(c_void_p))
        h = c_void_p()
        oku(fabrika, pid, E_RENDER, 1, byref(h))
        if not h.value:
            return ""
        metin = _combase.WindowsGetStringRawBuffer(h, None) or ""
        _combase.WindowsDeleteString(h)
        return metin
    except Exception:
        return ""


def sessiz_cikislar():
    """Kullanıcının o an dinlemediği ses çıkışları: [{id, ad}]. En uygun aday başta."""
    import warnings

    import soundcard as sc

    warnings.filterwarnings("ignore", category=sc.SoundcardRuntimeWarning)
    try:
        varsayilan = sc.default_speaker().id
        adaylar = [{"id": s.id, "ad": s.name} for s in sc.all_speakers() if s.id != varsayilan]
    except Exception:
        return []

    def puan(c):
        ad = c["ad"].lower()
        if any(k in ad for k in ("dijital", "digital", "spdif", "s/pdif", "cable", "virtual", "sanal")):
            return 0  # büyük olasılıkla hiçbir şeye bağlı değil ya da sanal
        if any(k in ad for k in ("kulaklık", "headphone", "headset", "hoparlör", "speaker")):
            return 2  # gerçekten ses verebilir
        return 1  # monitör çıkışları vb.

    adaylar.sort(key=lambda c: (puan(c), c["ad"]))
    return adaylar
