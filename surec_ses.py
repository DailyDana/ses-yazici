"""
Tek bir uygulamanın (ve alt süreçlerinin) sesini yakalar: Windows "process loopback" API'si
(Windows 10 2004+). Sekme düzeyinde değil, uygulama düzeyinde çalışır: msedge seçilirse
Edge'in bütün sekmeleri duyulur, Brave/Spotify/oyun sesi duyulmaz.

Kullanım (ses_yazi.py içinden):
    with SurecKaydedici(pid) as rec:
        parca = rec.record(8000)   # 16 kHz mono float32
"""
import sys

sys.coinit_flags = 0  # COINIT_MULTITHREADED; comtypes içe aktarılmadan önce olmalı

import ctypes
import threading
import time
from ctypes import POINTER, Structure, Union, byref, c_int64, c_uint32, c_uint64, c_ubyte, c_void_p
from ctypes import wintypes

import comtypes
import numpy as np
from comtypes import COMMETHOD, GUID, HRESULT, IUnknown, COMObject

SR = 16000

# --- COM arayüzleri -------------------------------------------------------------------------


class IActivateAudioInterfaceAsyncOperation(IUnknown):
    _iid_ = GUID("{72A22D78-CDE4-431D-B8CC-843A71199B6D}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetActivateResult",
                  (["out"], POINTER(HRESULT), "activateResult"),
                  (["out"], POINTER(POINTER(IUnknown)), "activatedInterface")),
    ]


class IActivateAudioInterfaceCompletionHandler(IUnknown):
    _iid_ = GUID("{41D949AB-9862-444A-80F6-C261334DA5EB}")
    _methods_ = [
        COMMETHOD([], HRESULT, "ActivateCompleted",
                  (["in"], POINTER(IActivateAudioInterfaceAsyncOperation), "op")),
    ]


class IAgileObject(IUnknown):
    _iid_ = GUID("{94EA2B94-E9CC-49E0-C0FF-EE64CA8F5B90}")
    _methods_ = []


class WAVEFORMATEX(Structure):
    _fields_ = [
        ("wFormatTag", wintypes.WORD),
        ("nChannels", wintypes.WORD),
        ("nSamplesPerSec", wintypes.DWORD),
        ("nAvgBytesPerSec", wintypes.DWORD),
        ("nBlockAlign", wintypes.WORD),
        ("wBitsPerSample", wintypes.WORD),
        ("cbSize", wintypes.WORD),
    ]


class IAudioClient(IUnknown):
    _iid_ = GUID("{1CB9AD4C-DBFA-4C32-B178-C2F568A703B2}")
    _methods_ = [
        COMMETHOD([], HRESULT, "Initialize",
                  (["in"], ctypes.c_int, "ShareMode"),
                  (["in"], wintypes.DWORD, "StreamFlags"),
                  (["in"], c_int64, "hnsBufferDuration"),
                  (["in"], c_int64, "hnsPeriodicity"),
                  (["in"], POINTER(WAVEFORMATEX), "pFormat"),
                  (["in"], POINTER(GUID), "AudioSessionGuid")),
        COMMETHOD([], HRESULT, "GetBufferSize", (["out"], POINTER(c_uint32), "n")),
        COMMETHOD([], HRESULT, "GetStreamLatency", (["out"], POINTER(c_int64), "l")),
        COMMETHOD([], HRESULT, "GetCurrentPadding", (["out"], POINTER(c_uint32), "p")),
        COMMETHOD([], HRESULT, "IsFormatSupported",
                  (["in"], ctypes.c_int, "m"), (["in"], POINTER(WAVEFORMATEX), "f"),
                  (["out"], POINTER(POINTER(WAVEFORMATEX)), "c")),
        COMMETHOD([], HRESULT, "GetMixFormat", (["out"], POINTER(POINTER(WAVEFORMATEX)), "f")),
        COMMETHOD([], HRESULT, "GetDevicePeriod",
                  (["out"], POINTER(c_int64), "d"), (["out"], POINTER(c_int64), "m")),
        COMMETHOD([], HRESULT, "Start"),
        COMMETHOD([], HRESULT, "Stop"),
        COMMETHOD([], HRESULT, "Reset"),
        COMMETHOD([], HRESULT, "SetEventHandle", (["in"], wintypes.HANDLE, "h")),
        COMMETHOD([], HRESULT, "GetService",
                  (["in"], POINTER(GUID), "riid"), (["out"], POINTER(c_void_p), "ppv")),
    ]


class IAudioCaptureClient(IUnknown):
    _iid_ = GUID("{C8ADBD64-E71E-48A0-A4DE-185C395CD317}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetBuffer",
                  (["out"], POINTER(POINTER(c_ubyte)), "data"),
                  (["out"], POINTER(c_uint32), "frames"),
                  (["out"], POINTER(wintypes.DWORD), "flags"),
                  (["out"], POINTER(c_uint64), "devpos"),
                  (["out"], POINTER(c_uint64), "qpcpos")),
        COMMETHOD([], HRESULT, "ReleaseBuffer", (["in"], c_uint32, "n")),
        COMMETHOD([], HRESULT, "GetNextPacketSize", (["out"], POINTER(c_uint32), "n")),
    ]


# --- etkinleştirme parametreleri ------------------------------------------------------------


class PROCESS_LOOPBACK_PARAMS(Structure):
    _fields_ = [("TargetProcessId", wintypes.DWORD), ("ProcessLoopbackMode", ctypes.c_int)]


class AUDIOCLIENT_ACTIVATION_PARAMS(Structure):
    _fields_ = [("ActivationType", ctypes.c_int), ("ProcessLoopbackParams", PROCESS_LOOPBACK_PARAMS)]


class BLOB(Structure):
    _fields_ = [("cbSize", wintypes.ULONG), ("pBlobData", c_void_p)]


class _PVU(Union):
    _fields_ = [("blob", BLOB), ("_pad", c_ubyte * 16)]


class PROPVARIANT(Structure):
    _fields_ = [("vt", wintypes.USHORT), ("r1", wintypes.WORD), ("r2", wintypes.WORD),
                ("r3", wintypes.WORD), ("u", _PVU)]


VT_BLOB = 65
ACTIVATION_TYPE_PROCESS_LOOPBACK = 1
INCLUDE_TARGET_PROCESS_TREE = 0
AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM = 0x80000000
AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY = 0x08000000
AUDCLNT_BUFFERFLAGS_SILENT = 0x2


class _Tamamlayici(COMObject):
    _com_interfaces_ = [IActivateAudioInterfaceCompletionHandler, IAgileObject]

    def __init__(self):
        super().__init__()
        self.olay = threading.Event()

    def ActivateCompleted(self, op):
        self.olay.set()
        return 0


def _istemci_ac(pid):
    params = AUDIOCLIENT_ACTIVATION_PARAMS()
    params.ActivationType = ACTIVATION_TYPE_PROCESS_LOOPBACK
    params.ProcessLoopbackParams.TargetProcessId = pid
    params.ProcessLoopbackParams.ProcessLoopbackMode = INCLUDE_TARGET_PROCESS_TREE
    pv = PROPVARIANT()
    pv.vt = VT_BLOB
    pv.u.blob.cbSize = ctypes.sizeof(params)
    pv.u.blob.pBlobData = ctypes.cast(byref(params), c_void_p)

    tam = _Tamamlayici()
    handler = tam.QueryInterface(IActivateAudioInterfaceCompletionHandler)
    op = POINTER(IActivateAudioInterfaceAsyncOperation)()
    fn = ctypes.windll.Mmdevapi.ActivateAudioInterfaceAsync
    fn.argtypes = [wintypes.LPCWSTR, POINTER(GUID), POINTER(PROPVARIANT),
                   POINTER(IActivateAudioInterfaceCompletionHandler),
                   POINTER(POINTER(IActivateAudioInterfaceAsyncOperation))]
    fn.restype = HRESULT
    fn("VAD\\Process_Loopback", byref(IAudioClient._iid_), byref(pv), handler, byref(op))
    if not tam.olay.wait(5):
        raise RuntimeError("Ses arayüzü etkinleştirilemedi (zaman aşımı).")
    hr, unk = op.GetActivateResult()
    if hr < 0:
        raise OSError(f"Process loopback etkinleştirme hatası: 0x{hr & 0xFFFFFFFF:08X}")
    return unk.QueryInterface(IAudioClient)


class SurecKaydedici:
    """soundcard kaydedicisiyle aynı arayüz: record(numframes) -> (numframes, 1) float32."""

    def __init__(self, pid):
        self.pid = pid
        self._kuyruk = np.zeros(0, dtype=np.float32)

    def __enter__(self):
        comtypes.CoInitializeEx(0)
        self.client = _istemci_ac(self.pid)
        fmt = WAVEFORMATEX(wFormatTag=1, nChannels=1, nSamplesPerSec=SR, wBitsPerSample=16)
        fmt.nBlockAlign = 2
        fmt.nAvgBytesPerSec = SR * 2
        bayrak = (AUDCLNT_STREAMFLAGS_LOOPBACK | AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM
                  | AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY)
        self.client.Initialize(0, bayrak, 20_000_000, 0, byref(fmt), None)
        ptr = self.client.GetService(byref(IAudioCaptureClient._iid_))
        self.capture = ctypes.cast(ptr, POINTER(IAudioCaptureClient))
        self.client.Start()
        return self

    def __exit__(self, *a):
        try:
            self.client.Stop()
        except Exception:
            pass

    def _bosalt(self):
        parcalar = []
        while self.capture.GetNextPacketSize() > 0:
            data, n, flags, _, _ = self.capture.GetBuffer()
            if n:
                if flags & AUDCLNT_BUFFERFLAGS_SILENT:
                    parcalar.append(np.zeros(n, dtype=np.float32))
                else:
                    ham = np.ctypeslib.as_array(ctypes.cast(data, POINTER(ctypes.c_int16)), shape=(n,))
                    parcalar.append(ham.astype(np.float32) / 32768.0)
            self.capture.ReleaseBuffer(n)
        if parcalar:
            self._kuyruk = np.concatenate([self._kuyruk, *parcalar])

    def record(self, numframes):
        # Hedef uygulama ses çalmıyorsa Windows paket göndermiyor; süre dolunca sessizlikle doldur
        bitis = time.time() + numframes / SR + 0.15
        while len(self._kuyruk) < numframes and time.time() < bitis:
            self._bosalt()
            time.sleep(0.01)
        if len(self._kuyruk) < numframes:
            self._kuyruk = np.concatenate([self._kuyruk, np.zeros(numframes - len(self._kuyruk), np.float32)])
        out, self._kuyruk = self._kuyruk[:numframes], self._kuyruk[numframes:]
        return out.reshape(-1, 1)


def kok_pid_bul(ad):
    """Ada göre uygulamanın kök sürecini bul (ör. 'msedge' -> ana Edge süreci)."""
    import psutil

    ad = ad.lower()
    if not ad.endswith(".exe"):
        ad += ".exe"
    adaylar = [p for p in psutil.process_iter(["pid", "name", "ppid"]) if (p.info["name"] or "").lower() == ad]
    if not adaylar:
        return None
    pidler = {p.info["pid"] for p in adaylar}
    kokler = [p for p in adaylar if p.info["ppid"] not in pidler] or adaylar
    # Birden çok kök varsa (ayrı profiller, arka plan güncelleyicisi) en çok alt süreci olanı seç
    cocuk = {}
    for p in adaylar:
        cocuk[p.info["ppid"]] = cocuk.get(p.info["ppid"], 0) + 1
    return max(kokler, key=lambda p: cocuk.get(p.info["pid"], 0)).info["pid"]
