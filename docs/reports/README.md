# Paylaşılabilir analiz çıktıları

[sales-eda.html](sales-eda.html), ana analiz notebook'unun kod, açıklama, özet tablolar ve dokuz Matplotlib grafiğini içeren HTML kopyasıdır. Grafikleri görüntülemek için Python veya API sunucusu gerekmez. GitHub HTML dosyasını uygulama olarak çalıştırmaz; dosyayı indirip tarayıcıda açın. GitHub üzerinde doğrudan incelemek için [notebook'u](../../notebooks/01_sales_eda.ipynb) kullanabilirsiniz.

Rapor tarihsel Superstore vaka çalışmasıdır; kendi işletmenizin güncel verisi değildir. Kaynak dosyanın parmak izi ve analiz kapsamı raporda yer alır. Ham müşteri/sipariş satırları yerine sütun/tür bilgisi ve toplulaştırılmış sonuçlar paylaşılır. Kaynak veri dosyası, `.env`, API oturumları ve özel kullanıcı raporları repoya dahil edilmez. Kaynak verinin özgün indirme adresi ve yeniden dağıtım lisansı doğrulanmış değildir; bu HTML kaynak CSV'nin dağıtımı yerine geçmez.

Yeniden üretme ve doğrulama:

```powershell
.\.venv\Scripts\python.exe tools/verify_notebook.py --html docs/reports/sales-eda.html
```

Bu komut notebook'u iki çalışma dizininden yeniden çalıştırır; metrik uzlaşmalarını, dokuz grafiği ve bölüm bağlantılarını kontrol eder. Özel dosyalarla üretilen raporlar `work/` içinde yerelde tutulmalıdır; bu klasöre otomatik kopyalanmaz.
