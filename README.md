# Ses-Yazıcı

Seçtiğin uygulamanın (örneğin tarayıcının) sesini dinler ve konuşmaları canlı olarak yazıya döker. Whisper modeliyle bilgisayarında çalışır; ses dışarı gönderilmez.

Windows 10 (2004+) ve Windows 11 içindir.

## Kurulum

1. [Son sürümdeki](https://github.com/DailyDana/ses-yazici/releases/latest) `Ses-Yazici.zip` dosyasını indir, bir klasöre çıkar.
2. `Baslat.cmd` dosyasına çift tıkla.

İlk açılışta Python ve gerekli paketler klasörün içine kurulur. NVIDIA ekran kartı varsa yaklaşık 1 GB CUDA paketi de iner. Model ilk kayıtta indirilir.

## Kullanım

Açılan sayfada dinlenecek uygulamayı seç, "Kaydı başlat"a bas. Metin 25–40 saniyelik parçalar hâlinde gelir ve `Belgeler\Ses-Yazici` klasörüne `.txt` olarak kaydedilir.

- Sekme değil uygulama dinlenir. Firefox seçiliyse Firefox'un bütün sekmelerinin sesi yazılır.
- Tarayıcıyı kapatıp açarsan kaydı yeniden başlat.
- Metin geç geliyorsa "Hızlı" modeli seç.
- "Ben duymayayım" işaretliyse kayıt boyunca uygulamanın sesi kullanmadığın bir ses çıkışına gider, kayıt bitince geri döner. Sessize almak işe yaramaz: sessize alınan ses kayda da gelmez.

## Donanım

| Donanım | Motor |
|---|---|
| NVIDIA ekran kartı | faster-whisper (CUDA) |
| Intel ekran kartı | OpenVINO |
| Diğer | OpenVINO, işlemci |

## Güncelleme

`Baslat.cmd` her açılışta bu depodaki son sürüme bakar, yenisi varsa program dosyalarını günceller. Modeller ve kayıtlar yerinde kalır.

## Lisans

MIT
