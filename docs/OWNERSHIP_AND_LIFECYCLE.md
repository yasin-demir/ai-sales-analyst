# Yerel API: oturum sahipliği ve veri yaşam döngüsü

## Çalıştırma

```powershell
.\.venv\Scripts\python.exe -m src.api
```

Sunucu yalnızca `127.0.0.1:8000` üzerinde, tek API süreciyle başlar. Etkileşimli sözleşmeler `http://127.0.0.1:8000/docs` adresindedir. Varsayılan özel depo `work/api-store`; `--store` ve `--port` yalnızca sunucu çalıştırma ayarlarıdır. İstemciden sunucu dosya yolu alınan bir endpoint yoktur.

Depoya ikinci API nesnesi/süreci açılması işletim sistemi dosya kilidiyle reddedilir. Süreç kapanınca veya öldüğünde kilit işletim sistemi tarafından bırakılır. Hazırlama hizmetinin oturumlu sınırı `OwnedDatasetService`, HTTP uygulaması `create_app` içindedir.

## Kimlik nasıl doğrulanır?

`POST /v1/sessions` boş bir JSON nesnesi alır ve sunucunun ürettiği 256 bit rastgele Bearer token döndürür. Sonraki istekler `Authorization: Bearer ...` başlığı taşır. Sunucu token özetini SQLite üzerinden doğrular ve kendi `SessionContext` nesnesini üretir. İstemcinin `user_id`, sahiplik veya oturum kimliği bildirmesi bir yetki sağlamaz. İstek modellerinde bu alanlar kabul edilmez.

Bu, yerel prototip için doğrulanmış token sahipliğine dayalı oturum kimliğidir; gerçek kişi hesabı/parola girişi değildir. İki ayrı token iki ayrı oturum oluşturur. Aynı tokenı kullanan istemciler aynı oturumu temsil eder. Bu token OpenAI anahtarı değildir ve dış hizmet ücreti gerektirmez.

Tokenın kendisi diske yazılmaz; SHA-256 özeti, sunucunun ürettiği oturum kimliği, süre sonu ve iptal durumu `.sessions.sqlite3` içinde tutulur. Kimliği doğrulanmış bağlam her hizmet işleminin başında ve sonucu teslim etmeden önce tekrar kontrol edilir. Oturum kapatma `DELETE /v1/session` ile yapılır; oturumun veri kümeleri de temizlemeye alınır.

Başka oturumun veya bulunmayan bir veri kümesinin kimliğinde aynı `404` yaklaşımı kullanılır. Kaynak, önizleme, eşleştirme, hazırlama, sorgu, geçmiş, rapor oluşturma/indirme ve silme aynı sahiplik kontrolünden geçer. Hazırlama ve rapor kimliği ayrıca URL'deki veri kümesine ait olmalıdır.

## Yaşam döngüsü kararı

| Durum | Davranış |
|---|---|
| Normal yükleme | Sahip, doğrulanmış oturumdan alınır; veri kimliğini sunucu üretir. |
| Veri süresi | Yüklemeden itibaren 60 dakika; sorgu veya yeniden başlatma bu süreyi uzatmaz. |
| Oturum süresi | Varsayılan 24 saat; veri süresi oturum süresini geçemez. |
| Süre doldu | Yeni erişim hemen reddedilir; dosyalar bir sonraki temizlikte veya veri erişimi kontrolünde temizlenir. |
| Açık silme, çalışan iş yok | Kaynak, ham/hazırlanmış önbellek, meta veri ve veri kümesinin raporları silinir; HTTP `204`. |
| Açık silme, çalışan iş var | Erişim hemen kapanır ve önbellek kaydı atılır; HTTP `202`. Son aktif hizmet işlemi çıkınca fiziksel temizlik yapılır. |
| İş sürerken silme/süre sonu | İş zorla kesilmez; hesaplama tamamlansa da sonucu teslim edilmez (`404`). Son işlem kaynakları bıraktığında dosyalar silinir. |
| İş sürerken oturum iptali | Sonuç teslim edilmez (`401`); son işlem çıktığında temizlik tamamlanır. |
| Uygulama yeniden başladı | Geçerli token ve sahiplik korunur. Süresi dolmuş, oturumu iptal edilmiş veya silinmiş veri temizlenir. |
| Disk/temizlik hatası | İşlem başarılı silme olarak bildirilmez (`503`). Erişim kapalı kalır; temizlik yeniden denenir. |

Temizlik API yaşam döngüsünde başlangıçta ve varsayılan her 30 saniyede çalışır. Aktif işlerin tuttuğu kopyaların bırakılması beklenir; bu paket işçi süreciyle zorla işlem sonlandırma veya süre garantisi sağlamaz. Daha önce teslim edilmiş/indirilmiş bir kopya geri alınamaz. İndirmeler tamponlanır; silinen dosyaya bağlı açık bir streaming dosya yanıtı kullanılmaz.

Silme kararı önce SQLite silme günlüğüne yazılır. Dosya meta verisine yazma veya fiziksel silme yarıda kalsa bile yeniden başlatmada bu kimlikler temizlenir; eski veri tekrar erişime açılmaz. Günlükte yalnızca silinmiş veri kimliği kalır; kaynak içerik, tablo veya rapor tutulmaz. Süresi dolmuş/iptal edilmiş oturum kayıtları temizlik sırasında kaldırılır.

## Depolama ve raporlar

Kaynak ve onay/hazırlama geçmişi önceki hizmet kurallarını korur. Sahiplik ve süre sonu sunucu meta verisinde saklanır. Hazırlanmış tablo çağırana verilmez; sorgu hesaplaması bağımsız kopyayla çalışır. Silme tüm hazırlama sürümlerinin önbelleğini boşaltır.

API raporları bu sürümde JSON'dur. Dosyalar veri kümesinin kendi `reports/` klasöründe, sunucunun ürettiği rapor kimliğiyle saklanır. Rapor ismi/yolu istemciden alınmaz. Kaynak ve rapor indirmeleri kimlik doğrulama gerektirir; yanıtlar `Cache-Control: no-store` taşır. Veri kümesi silindiğinde bu raporlar da kaldırılır. Ayrı yerel CLI komutlarıyla depo dışında oluşturulmuş kişisel dosyalar API yaşam döngüsüne dahil değildir.

Eski, sahiplik bilgisi olmayan çevrimdışı veri kayıtları yeni bir oturuma otomatik atanmaz ve API'den görünmez. Yerel `DatasetService` güvenilir iç depo/CLI uyumluluk katmanıdır; HTTP route'ları yalnızca `OwnedDatasetService` kullanır.

Temizlik hedefi özel deponun doğrulanmış doğrudan alt dizini olmak zorundadır. Kaynak salt okunur olduğu için silmede gerekli dosya izinleri kontrollü olarak açılır. Depo dışındaki yollar veya üst düzey sembolik bağlantılar silme hedefi olamaz. Kaynak/meta verinin yerel dosya sistemi yöneticisine karşı korunması bu oturum sınırının kapsamı değildir.

## Endpoint'ler

| İşlem | Endpoint |
|---|---|
| Oturum oluştur / iptal et | `POST /v1/sessions`, `DELETE /v1/session` |
| İkili dosya yükle | `POST /v1/datasets` (`X-Filename` başlığı, gövdede dosya baytları) |
| Durum, önizleme ve doğrulanmış alanlar | `GET /v1/datasets/{dataset_id}` |
| Önizleme | `POST /v1/datasets/{dataset_id}/preview` |
| Eşleştirmeyi onayla | `PUT /v1/datasets/{dataset_id}/mapping` |
| Hazırla / sürümleri listele | `POST .../prepare`, `GET .../preparations` |
| Sorgu | `POST /v1/datasets/{dataset_id}/questions` |
| Kaynak indir | `GET /v1/datasets/{dataset_id}/source` |
| JSON / grafik içeren HTML rapor oluştur / indir | `POST .../reports`, `GET .../reports/{report_id}` |
| Veri kümesini sil | `DELETE /v1/datasets/{dataset_id}` |

Sorgu gövdesi `preparation_id` ve önceki paketlerde tanımlanan kesin `request` modelini içerir. Metrik hesaplama sözleşmeleri değişmedi. Yükleme, satır/sütun, bellek/depolama, sonuç, iş süresi ve kuyruk kotaları [kaynak profili](RESOURCE_LIMITS.md) ile uygulanır. Host kontrolü ve aynı-origin kontrolü uygulanır; geniş CORS izni verilmez. Yerel prototip internete açık çok kullanıcılı servis olarak değerlendirilmemelidir.

İlk istekleri PowerShell'den yapmak için:

```powershell
$localSession = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/v1/sessions' -ContentType 'application/json' -Body '{}'
$sessionHeaders = @{ Authorization = "Bearer $($localSession.access_token)"; 'X-Filename' = 'completed_months_sales.csv' }
$uploadedDataset = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/v1/datasets' -Headers $sessionHeaders -ContentType 'application/octet-stream' -InFile '.\examples\completed_months_sales.csv'
$uploadedDataset.dataset_id
```

Bu örnekte `-InFile` istemcinin kendi bilgisayarındaki dosyayı okur; sunucuya dosya yolu değil baytlar gider. Tokenı sunucudaki kod dosyalarına veya sohbet mesajına koymak gerekmez.

## Doğrulama

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_owned_api.py -v
.\.venv\Scripts\python.exe -X utf8 tools/verify_local_api.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
.\.venv\Scripts\python.exe tools/export_question_schemas.py --check
.\.venv\Scripts\python.exe -m pip check
```

HTTP testleri iki oturum arasında bütün veri işlemlerinin ayrımını, sahte sahiplik alanlarını, geçersiz tokenları, veri/hazırlama/rapor bağlarını, açık silmeyi, boşta süre sonu temizliğini, oturum iptalini, yeniden başlatmayı, eşzamanlı sorgu/silme/süre sonunu, disk hatasını ve silme günlüğünü kapsar. Zaman testleri beklemek yerine denetlenebilir UTC saatini kullanır. TCP kontrolü geçici bir Uvicorn süreci başlatır, gerçek HTTP üzerinden akışı doğrular ve kendi sürecini kapatır.

Resmi başvuru: [FastAPI güvenlik araçları](https://fastapi.tiangolo.com/reference/security/), [API yaşam döngüsü](https://fastapi.tiangolo.com/advanced/events/).
