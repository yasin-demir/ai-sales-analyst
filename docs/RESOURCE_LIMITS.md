# Yerel API kaynak profili

`ResourceProfile` servis katmanında uygulanır; HTTP endpoint'leri aynı servisi kullanır. Doğrudan `OwnedDatasetService` çağrıları da dosya, veri, depolama, bellek ve hesaplama sınırlarına tabidir. Eski güvenilir çevrimdışı `DatasetService`/CLI kullanımı bu HTTP profilinin dışındadır.

```powershell
.\.venv\Scripts\python.exe -m src.api --limits examples/resource-limits.json
```

JSON dosyasında yalnızca değiştirmek istediğiniz alanlar bulunabilir. Bilinmeyen alanlar, yanlış türler, sıfır/negatif bütçeler ve sonlu olmayan süreler reddedilir. Tam örnek: [resource-limits.json](../examples/resource-limits.json).

| Kaynak | Başlangıç değeri |
|---|---:|
| Kaynak dosya | 20 MiB |
| Veri tablosu | 100.000 kayıt, 100 sütun |
| Analitik sonuç | Tüm para birimleri toplamında 1.000 sonuç satırı |
| JSON yanıt / rapor | 2 MiB |
| XLSX açılmış ZIP içeriği | 100 MiB, en fazla 2.048 ZIP girdisi |
| Yönetilen depolama / oturum | 100 MiB |
| Yönetilen depolama / toplam | 500 MiB |
| Bellek / oturum | 512 MiB |
| Bellek / toplam | 1 GiB |
| Aktif hesaplama | 1 iş |
| Bekleyen hesaplama | 4 iş |
| Kuyrukta bekleme | 60 saniye |
| Okuma, önizleme, eşleştirme ve hazırlama | 60 saniye |
| Sayısal sorgu ve JSON rapor hesaplaması | 15 saniye |
| Grafik içeren HTML raporu | 30 saniye, en fazla 8 figür |
| Veri ömrü | 60 dakika; oturumun bitişi daha erkense o an |

## Yükleme ve okuma

HTTP gövdesi alınmadan önce oturum/toplam depolama kotasından iki dosyalık yer ayrılır: geçici HTTP dosyası ve yönetilen kaynak kopyası. Bu muhafazakâr rezervasyon küçük yüklemeler için de geçerlidir; oturum kotası `2 * upload_bytes` değerinden küçükse HTTP yüklemesi kabul edilmez. Rezervasyon hata, bağlantı kesilmesi veya başarı sonunda bırakılır. Bayt sınırı her HTTP parçasında ve servis kopyalama döngüsünde kontrol edilir. Başarısız kaynak yüklemeleri kendi yarım dosyalarını temizler.

Depolama hesabı kaynak, rapor ve meta veri dosyalarını kapsar; diğer devam eden yüklemelerin rezervasyonlarını da içerir. Rapor/meta veri yazılmadan önce ek alan kontrol edilir. Meta veri atomik yazıldığı için geçici kopya da hesaba katılır. Silme işaretleri kota doluyken de yazılabilir; silme ve süre sonu temizliği çalışmaya devam eder. Oturum SQLite dosyası ve işletim sistemi geçici alanındaki diğer uygulama dosyaları veri kotasına dahil değildir. Yeniden başlatmada disk üzerindeki mevcut kullanım sayılır.

CSV satırları tek geçişte doğrulanıp sınır kontrolünden sonra tabloya eklenir; başlık kayıt sayısına dahil değildir. Tırnak içindeki satır sonları yeni kayıt sayılmaz. Sütun, kayıt ve tutulan hücrelerin tahmini bellek bütçesi kontrol edilir. XLSX için ZIP bildirimleri kontrol edilir, her girdi akışla açılarak gerçek açılmış baytlar sayılır; ardından openpyxl salt okuma modunda satırlar sınırla alınır. Sıkıştırılmış boyuta güvenilmez. Boş veya hatalı tablo hata verir.

## İşçi ve bellek

FIFO kabul kapısı, kilit/büyük hesaplamaya girmeden önce bir işi kabul eder ve dört işi bekletir. Kuyruk dolduğunda yeni hesaplama başlatılmaz. Her okuma, hazırlama ve analitik hesaplama ayrı bir `spawn` alt sürecinde yürütülür; aynı anda bir alt süreç çalışır. Bir isteğin okuma ve hazırlama alt adımları aynı toplam süre bütçesini paylaşır. Süre bütçesine süreç açılışı, soğuk verinin hazırlanması ve veri aktarımı dahildir; kuyruk bekleme süresi ayrı sınırlıdır.

Süre aşımında alt süreç sonlandırılıp `join` ile kapanması beklenir; gerekirse `kill` uygulanır. Slot ancak kapanma sonrasında serbest kalır. IPC alımı ayrı iş parçacığında yapılır; büyük sonuç aktarımı süre/bellek gözetimini durdurmaz. İşçi kalıcı depo meta verisi yazmaz, dolayısıyla zorla durdurulan hesaplama yarım hazırlama sürümü yayımlamaz. Hazırlanmış veri API sürecinin özel önbelleğinde kalır; sıcak sorgu kaynak dosyayı yeniden okumaz, hazırlamaz veya hash hesaplamaz. Alt süreçlere sorgu için özel kopya aktarılır.

Önbelleğe alınacak ham/hazırlanmış DataFrame'ler `memory_usage(deep=True)` ile oturum ve toplam kotaya karşı kontrol edilir. İşçi çalışırken yaklaşık 10 ms aralıklarla RSS izlenir: oturum için o oturumun tutulan DataFrame belleği + işçi RSS'i; toplam için API süreci RSS'i + işçi RSS'i sayılır. İşçi RSS'i kütüphane yüklemelerini de kapsar. Aşımda işçi kapatılır. Bu bir işletim sistemi Job Object/cgroup sert bellek sınırı değildir; örnekleme arasında kısa süreli aşım olabilir. Python/HTTP'nin tüm geçici nesneleri oturuma ayrı ayrı faturalandırılmaz; toplam RSS gözetimine dahildir. Daha sıkı izolasyon için sonraki dağıtımda OS bellek sınırı eklenmelidir.

## Yanıtlar ve hatalar

Analitik sonuçlar tüm para birimlerinde toplam satır sınırına tabidir. Sonuç sessizce kırpılmaz; kullanıcı filtre/dönem veya sıralama limitini daraltmalıdır. Büyük analitik JSON ve hazırlama kalite bilgisi alt süreçten aktarılmadan önce kontrol edilir. Servis birleşik yanıtı tekrar kontrol eder; HTTP katmanı JSON uzunluğunu ayrıca sınırlar. Kaynak indirme bu analitik JSON sınırına tabi değildir; dosya boyutu yükleme sınırıyla sınırlıdır. Raporlar ayrıca yazılacak gerçek bayt uzunluğuyla kontrol edilir.

| HTTP | Anlam |
|---|---|
| 413 | Dosya/veri/sonuç/bayt/depolama/bellek sınırı |
| 429 | Kuyruk dolu veya kuyrukta bekleme süresi bitti; `Retry-After: 1` |
| 504 | Hesaplama süre aşımı; alt süreç kapatıldı |
| 422 | Geçersiz dosya/eşleştirme/istek |

Sahiplik, veri ömrü ve çalışan sorguda silme politikaları [yaşam döngüsü belgesindeki](OWNERSHIP_AND_LIFECYCLE.md) gibi devam eder. Metrikler ve hesaplama formülleri bu paketle değişmedi.

Grafik üretimi sayısal endpoint'ten ayrıdır. HTML rapor işleri ayrı alt süreçte ve süreç içinde kilitle seri çalışır; ayrıntılar [render belgesindedir](RENDERING.md).

## Doğrulama

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
.\.venv\Scripts\python.exe tools/verify_local_api.py
```

Kaynak testleri CSV/XLSX sınırlarını, sıkıştırılmış küçük/açılmış büyük ZIP'i, oturum ve toplam disk kotalarını, yeniden başlatmayı, gerçek alt süreç ölümünü, yazma yan etkisinin durmasını, işçi bellek aşımını, kuyruk kapasitesini, kuyruk zaman aşımını ve HTTP hata durumlarını kapsar. Profil başlangıç değeridir; temsilî dosyalarda gecikme/RSS ölçülerek ayarlanmalıdır.
