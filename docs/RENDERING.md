# Ayrı ve sınırlı grafik/rapor işleri

`POST /v1/datasets/{id}/questions` yalnızca sayısal analiz yapar. Grafik veya HTML üretmez; API sayısal akışında Matplotlib yüklenmez. Varsayılan JSON rapor davranışı korunur.

Grafik içeren rapor için mevcut rapor endpoint'ine `format: "html"` ekleyin:

```json
{
  "preparation_id": "HAZIRLAMA_KIMLIGI",
  "request": {"analysis": "segment_performance", "by": ["category"], "limit": 10},
  "format": "html",
  "theme": "light"
}
```

`theme` yalnızca `light` veya `dark` olabilir; serbest Matplotlib stil kodu kabul edilmez. `format` belirtilmezse `json` kullanılır. `201` yanıtındaki `report_id` ile `GET /v1/datasets/{id}/reports/{report_id}` çağrısı dosyayı indirir. HTML raporları `text/html`, JSON raporları `application/json` olarak ve `attachment` başlığıyla sunulur. HTML içinde resimler gömülü PNG'dir; dış dosya/URL gerektirmez. Raporun sahipliği, süre sonu ve silinmesi veri kümesinin yaşam döngüsüne bağlıdır.

## Süreç ve thread izolasyonu

HTML isteği, hazırlanmış veriden normal sorguyu çalıştırır; ardından aynı sayısal yanıtın JSON kopyasını ayrı bir `spawn` render alt sürecine gönderir. Render işi kaynak dosyayı okumaz, yeni metrik hesaplamaz veya ana veri önbelleğini değiştirmez. Sayısal sonuç, filtreler, durumlar, uyarılar ve tanımlar HTML'e aynen eklenir.

`src/plotting.py` render modülü başlatılırken, pyplot/figür oluşturulmadan önce `Agg` seçilir. API bu modülü yalnızca render çocuğunda yükler. Ayrıca süreç içindeki bütün render oturumları aynı `RLock` ile seri çalışır. Kilit; stil ayarlarını, figür/artist oluşturmayı, çizimi, PNG kodlamasını ve figür temizliğini kapsar. Mevcut çevrimdışı `general_reports.make_charts` da aynı oturumu kullanır; aynı süreçte iki thread birbirinin stilini değiştiremez.

Bu ayrım gereklidir: [Matplotlib resmi FAQ](https://matplotlib.org/stable/users/faq.html#work-with-threads), thread güvenli olmadığını ve thread kullanımında erişimin kilitle serileştirilmesi gerektiğini belirtir. `Agg` tek başına eşzamanlılık koruması olarak kullanılmaz.

Her iş varsayılan rcParams üzerinden kendi stilini oluşturur ve çıkışta önceki ayarları geri yükler. TeX ve matematik metin yorumlama devre dışıdır. Figür açıkça oluşturulur ve axes oluşturulmadan önce işin sahip olduğu listeye alınır. Kodlama `BytesIO` bağlamında yapılır; başarılı/başarısız kaydetmede figür kapatılır. `finally` bloğu erken artist/axes hatalarında kalan bütün iş figürlerini de kapatır. Başka kodun açtığı figürlere `close('all')` uygulanmaz.

## Sınırlar ve hata sonrası temizlik

Global hesaplama bütçesi korunur: aynı anda 1 aktif iş, en fazla 4 bekleyen iş. Grafik istekleri ayrı ve sınırlı işlerdir; ek bir sınırsız thread havuzu açılmaz. Varsayılan HTML rapor bütçesi `render_seconds: 30.0`, en fazla `render_figures: 8` figürdür. Kuyruk beklemesi ayrı sınırdır; raporun toplam süre bütçesi sayısal alt adımı ve render sürecini birlikte kapsar. Bellek, sonuç satırı, 2 MiB rapor boyutu ve depolama kotaları da uygulanır. Değerler [kaynak profilinden](RESOURCE_LIMITS.md) değiştirilebilir.

Her para birimi için ayrı grafik oluşturulur. Tek grafikte en fazla 50 öğe gösterilir; gösterilen/tam öğe sayısı açıkça belirtilir ve bütün sayısal sonuçlar HTML'de kalır. Boş/yetersiz/kullanılamayan sonuçlar için grafik üretilmez; bu durumlar uyarılarıyla korunur. Rapor kullanıcı metinlerini HTML olarak escape eder, indirirken CSP ve `nosniff` başlıkları uygulanır.

Render tamamlanıp boyutu doğrulanmadan rapor dosyası oluşturulmaz. Render hatasında `500`, kota aşımında `413`, kuyruk doluysa `429`, süre aşımında `504` döner. Süre aşımında hesaplama alt süreci gerçekten kapatılıp beklenir; iş slotu sonrasında bırakılır. Rapor yazımı/meta veri kaydı başarısız olursa oluşturulan rapor dosyası kaldırılır ve rapor kimliği yayımlanmaz. Veri silinir veya oturum geçersizleşirse mevcut yaşam döngüsü kontrolü sonucu yayımlamayı engeller.

## Doğrulama

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_rendering.py -v
.\.venv\Scripts\python.exe tools/verify_local_api.py
```

Testler iki ayrı oturumun aynı anda farklı veri ve temalarla yaptığı gerçek endpoint çağrılarını, PNG arka plan piksellerini ve çapraz oturum indirme engelini kontrol eder. Aynı süreçte iki thread'in render bölgesine aynı anda giremediği ayrıca ölçülür. Encoder, axes, artist ve meta veri yazma hatalarında temizlik; figür/bayt sınırları; gerçek render başladıktan sonra süreç sonlandırma ve sonraki işin çalışması doğrulanır. Ayrı bir temiz Python süreci, sayısal sorgu + JSON rapor akışının Matplotlib'i hiç yüklemediğini kontrol eder.
