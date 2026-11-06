# Hazırlanmış veri hizmeti

HTTP kullanımında bu iç depo artık [oturum sahipliği ve yaşam döngüsü katmanının](OWNERSHIP_AND_LIFECYCLE.md) arkasındadır. API yalnızca doğrulanmış oturum bağlamını kullanan `OwnedDatasetService` üzerinden erişir. Aşağıdaki doğrudan Python örnekleri güvenilir yerel/çevrimdışı kullanım içindir.

`src/datasets.py` içindeki `DatasetService`, yönetilen bir kaynak kopyasından birden fazla sürümlü sorgu çalıştırır. `src/dataset_contracts.py` yükleme, önizleme, onay ve hazırlama meta verilerinin Pydantic modellerini içerir.

## Ayrı adımlar

1. **Yükleme:** `upload(stream, filename=...)` ikili akışı sunucunun oluşturduğu bir kimlikle kendi deposuna kopyalar. SHA-256, diske yazılan aynı parçalar üzerinden yükleme sırasında hesaplanır. Kaynak yeniden açılarak hash hesaplanmaz. İstemcinin dosya adı yalnızca gösterim meta verisidir; depolama yolunu belirleyemez. `upload_path(path)` yerel Python kullanımına yönelik bir yardımcıdır.
2. **Önizleme:** `preview(dataset_id, options=..., rows=...)` sütunları, toplam kayıt sayısını, örnek satırları ve eşleştirme önerilerini döndürür. CSV ayıracı/kodlaması ve Excel sayfası açık okuma ayarlarıdır. Önizleme sayısı varsayılan 5, üst sınırı 50'dir. Bu adım onay veya analitik hazırlama yapmaz.
3. **Onay:** `approve_mapping(dataset_id, config)` yalnızca `mapping_reviewed=True` içeren, geçerli ve o dosyanın sütunlarına karşılık gelen ayarları kabul eder. Öneriler tek başına onay sayılmaz. Eşleştirme ve bütün içe aktarma ayarları kopyalanarak saklanır.
4. **Hazırlama:** `prepare(dataset_id)` son onaylı eşleştirmeyi kullanır. İstenirse `mapping_revision=...` ile eski bir onay seçilebilir. Normalizasyon ve kalite doğrulaması bir kez yapılır. Aynı onay/hazırlayıcı sürümü tekrar istendiğinde aynı hazırlama kimliği döner.
5. **Sorgu:** `query(preparation_id, request)` belirli, değişmez bir hazırlama kaydına bağlı çalışır. Son onay değişse bile eski kimlik eski veriyi ve eski ayarları kullanır. Mevcut altı analiz türü ve kesin soru sözleşmeleri kullanılır.

## Kimlik ve sürüm kuralları

- `dataset_id`: Tek bir yüklemeyi tanımlar. Aynı isimli veya aynı içerikli ikinci yükleme farklı kimlik alır; eski kaynak üzerine yazılmaz.
- `MappingInfo.revision`: Onaylanmış ayarların veri kümesi içindeki sıra numarasıdır. Aynı ayarların tekrar onayı yeni sürüm üretmez; sözlük anahtar sırası sonucu etkilemez.
- `PreparedInfo.revision` / `preparation_id`: Hazırlama kaydının sıra numarası ve sunucunun oluşturduğu kimliğidir. Her yeni eşleştirme onayı için ayrı hazırlama oluşur. A → B → A değişiminde üç kayıt korunur.
- `config_sha256`: Yalnızca sütun eşleştirmesinin değil; tarih/sayı/indirim biçimi, para birimi, iade/geçersiz kayıt politikası, ayıraç/kodlama ve Excel sayfası dahil bütün onaylı ayarların kanonik parmak izidir.
- `preparer_version`: Hazırlama algoritmasının sürümüdür; ilk sürüm `sales-import-v1`. Normalizasyon veya kalite sözleşmesi değiştiğinde bu sürüm bilinçli olarak yükseltilmelidir.
- `preparation_key`: Kaynak SHA-256 + ayar SHA-256 + hazırlayıcı sürümünden üretilir. Aynı ayarlara geri dönüldüğünde içerik anahtarı aynı olabilir; hazırlama kimliği ve sıra numarası yine ayrıdır.

Hizmet aynı veri kümesinin onay/hazırlama işlemlerini kilit altında yapar. Eşzamanlı aynı hazırlama istekleri bir normalizasyon ve bir sürüm üretir. Farklı yüklemelerin önbellekleri paylaşılmaz.

## Kalıcılık ve önbellek

Sunucuya ait özel bir kök dizin altında her veri kümesi için salt okunur `source.csv` veya `source.xlsx` ve `metadata.json` saklanır. Meta veri geçici dosyaya yazılıp atomik olarak değiştirilir; başarısız onay/hazırlama işlemi yeni kayıt yayımlamaz. Başarısız yüklemenin oluşturduğu bilinen dosyalar temizlenir. Kaynak kopyası hizmet API'sinden değiştirilemez.

Dosya kimliği ve onay/hazırlama geçmişi yeniden başlatmadan sonra yüklenir. Ham ve hazırlanmış tablolar bellektedir; DataFrame/pickle diske yazılmaz. İlk soğuk kullanımda kaynak bir kez okunur ve ilgili sürüm yeniden hazırlanır; kalite meta verisi saklanan kayıtla karşılaştırılır. Sonraki sorgular dosya okumaz, normalizasyon veya hash hesaplamaz. Aynı okuma ayarlarıyla alınan önizleme de hazırlamada tekrar kullanılabilir.

Okumadan önce kaynak boyutu/mtime ve sembolik bağlantı kontrol edilir. Bu, güvenilir özel depoda kazara değişikliği yakalamaya yöneliktir; dosya sistemi yöneticisine karşı kriptografik değişmezlik garantisi değildir. Kaynak içerik hash'i her sorguda yeniden hesaplanmaz. Hazırlayıcı sürümü değiştiğinde eski kaydı yeni kodla sessizce yeniden kurmak yerine yeni hazırlama gerekir.

Önbellekteki tablo dışarı verilmez. Sorgu hesaplamasına DataFrame kopyası aktarılır; kalite/ayar meta verileri ve dışarı dönen modeller de ayrılır. Çağıranın bir önizleme, yanıt veya ayar nesnesini değiştirmesi saklanan sürümü değiştirmez.

## Kullanım örneği

```python
from src.datasets import DatasetService
from src.importing import ImportConfig

service = DatasetService('work/my-dataset-store')
uploaded = service.upload_path('examples/completed_months_sales.csv')
preview = service.preview(uploaded.dataset_id)

# Bu profil yalnızca incelenmiş, sentetik örnek dosyaya aittir.
reviewed = ImportConfig.load('examples/completed_months_mapping.json')
mapping = service.approve_mapping(uploaded.dataset_id, reviewed)
prepared = service.prepare(uploaded.dataset_id)

summary = service.query(prepared.preparation_id, {'analysis': 'summary'})
ranking = service.query(prepared.preparation_id, {
    'analysis': 'monthly_trend',
})
```

Kalitesi `needs_attention` olan hazırlama da kalite raporuyla kayıt altına alınır; sorguda analitik hesaplama yapılmadan mevcut kalite yanıtı döner. Sorunu giderecek yeni ayarlar onaylandığında yeni hazırlama oluşturulur.

Hazır gösterim:

```powershell
.\.venv\Scripts\python.exe -X utf8 tools/prepared_data_demo.py
```

Gösterim `work/prepared-data-demo` altında yönetilen kaynak ve meta verileri, ayrıca `summary.json` ve `comparison.json` çıktıları oluşturur. Her çalıştırma yeni bir yükleme kaydı açar. Eylül/Ekim karşılaştırmasında beklenen satış değişimi %20, kâr değişimi %25'tir. Özet sorgusu dosyanın tüm kayıtlarını kapsar; dönem karşılaştırması yalnızca istenen iki ayı kapsar.

## Bu paketin sınırı

Bu, tek kök için tek hizmet nesnesi/yazıcı kullanan iç Python deposudur. Üzerindeki yerel HTTP katmanı oturum sahipliğini, dosya süresini ve silme politikasını uygular; ayrıca aynı API deposuna ikinci süreç açılmasını kilitle engeller. Oturumlu servis bellek/disk/işlem kotalarını [kaynak profili](RESOURCE_LIMITS.md) üzerinden uygular. Genel erişimli yayın bu yerel prototipin kapsamı değildir.

Mevcut tek seferlik `answer_file_question` ve CLI komutları kendi dosya akışını kullanır. Tekrar okumayı ortadan kaldıran yeni yol `DatasetService.query` → `answer_prepared_question` akışıdır. Dosya kopyalama ve ayrıştırma tek bir işlem değildir: CSV okuyucusunun mevcut başlık/satır doğrulaması aynı okuma çağrısı içinde ek geçiş yapabilir; hizmet bu mevcut kuralları korur.

## Testler

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_datasets.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
.\.venv\Scripts\python.exe tools/export_question_schemas.py --check
```

Testler yükleme sırasında hash/kopya tutarlılığını, kaynak izolasyonunu, adım ayrımını, önbellek kullanımını, altı analizde eski dosya yanıtıyla eşitliği, A/B/A ayar geçmişini, yeniden başlatmayı, hazırlayıcı sürüm değişimini, mutasyona karşı korumayı, eşzamanlı sorgu/hazırlamayı, atomik yazma hatalarını, kalite/exclusion durumlarını ve native Excel hücrelerini kapsar. Sıcak sorguda dosya açma, okuma, normalizasyon veya hash çağrısı yapılırsa başarısız olan özel bir test bulunur.
