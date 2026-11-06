"""Local desktop workflow. No server, account, upload, or external model required."""
from pathlib import Path
from dataclasses import asdict
import json
import queue
import threading
import tkinter as tk
from tkinter import ttk,filedialog,messagebox
import webbrowser
from .importing import ImportConfig,FIELDS,read_input,suggest_mapping
from .general_reports import write_report
from .questions import AnalysisFilters

DATE_CHOICES={'2026-04-03 (yıl-ay-gün)':'%Y-%m-%d','03/04/2026 (gün/ay/yıl)':'%d/%m/%Y','04/03/2026 (ay/gün/yıl)':'%m/%d/%Y','03.04.2026 (gün.ay.yıl)':'%d.%m.%Y'}
DISCOUNT_CHOICES={'0–1 (örnek: 0.20)':'fraction','0–100 (örnek: %20)':'percent'}
POLICY_CHOICES={'Sorun varsa analizi durdur':'stop','Sorunlu kayıtları raporlayarak dışla':'exclude'}

LABELS={'sales':'Satış tutarı (gerekli)','profit':'Kâr / zarar','date':'İşlem tarihi','product':'Ürün adı',
        'product_id':'Ürün kodu','category':'Kategori','region':'Bölge','order_id':'Sipariş kimliği',
        'quantity':'Miktar','discount':'İndirim oranı','currency':'Para birimi sütunu'}

class SalesApp:
    def __init__(self,root):
        self.root=root;root.title('AI Sales Analyst — Yerel veri analizi');root.geometry('1100x850')
        self.report_filters=AnalysisFilters();self.report_comparison=None
        self.source=None;self.config=ImportConfig();self.mapping={};self.events=queue.Queue();self.busy=False
        outer=ttk.Frame(root,padding=15);outer.pack(fill='both',expand=True)
        ttk.Label(outer,text='Dosyanızdan açıklamalı satış raporu',font=('Segoe UI',18,'bold')).pack(anchor='w')
        ttk.Label(outer,text='CSV veya XLSX • Dosya bilgisayarınızda işlenir • Alanlar ve biçimler rapor öncesinde gözden geçirilir.').pack(anchor='w',pady=(4,12))
        bar=ttk.Frame(outer);bar.pack(fill='x')
        ttk.Button(bar,text='1. Dosya seç',command=self.choose_file).pack(side='left')
        ttk.Button(bar,text='Ayar yükle',command=self.load_config).pack(side='left',padx=8)
        self.path_label=ttk.Label(bar,text='Henüz dosya seçilmedi');self.path_label.pack(side='left',padx=10)
        options=ttk.LabelFrame(outer,text='2. Dosya biçimi',padding=10);options.pack(fill='x',pady=10)
        self.settings={}
        choices={'delimiter':(',', ';', '\t'),'encoding':('utf-8-sig','cp1254','cp1252','latin1'),
                 'decimal':('.',','),'thousands':('Yok',',','.',' '),
                 'date_format':tuple(DATE_CHOICES),
                 'discount_unit':tuple(DISCOUNT_CHOICES),'invalid_rows':tuple(POLICY_CHOICES)}
        titles={'delimiter':'CSV ayırıcı','encoding':'Kodlama','decimal':'Ondalık','thousands':'Binlik ayırıcı',
                'date_format':'Tarih gösterimi','discount_unit':'İndirim gösterimi','invalid_rows':'Sorunlu kayıtlar',
                'currency':'Tek para birimi (isteğe bağlı)','sheet':'Excel sayfa adı / 0 tabanlı sıra'}
        for i,key in enumerate(titles):
            row,col=divmod(i,3);cell=ttk.Frame(options);cell.grid(row=row,column=col,sticky='ew',padx=8,pady=4)
            ttk.Label(cell,text=titles[key]).pack(anchor='w')
            value=getattr(self.config,key)
            if key in ('date_format','discount_unit','invalid_rows'):
                options_map={'date_format':DATE_CHOICES,'discount_unit':DISCOUNT_CHOICES,'invalid_rows':POLICY_CHOICES}[key]
                value=next((label for label,internal in options_map.items() if internal==value),value)
            value='Yok' if key=='thousands' and value is None else ('' if value is None else str(value))
            var=tk.StringVar(value=value);self.settings[key]=var
            widget=ttk.Combobox(cell,textvariable=var,values=choices[key],width=27) if key in choices else ttk.Entry(cell,textvariable=var,width=29)
            widget.pack(fill='x')
        self.returns=tk.BooleanVar(value=False)
        ttk.Checkbutton(options,text='Negatif satışlar iade olarak dahil edilsin (iş tanımı doğrulanmış olmalı)',variable=self.returns).grid(row=3,column=0,columnspan=3,sticky='w',padx=8)
        ttk.Button(outer,text='Biçimi uygula ve önizlemeyi yenile',command=self.preview).pack(anchor='w')
        self.maps=ttk.LabelFrame(outer,text='3. Sütun eşleştirme — boş seçim bu analizi kapsam dışı bırakır',padding=8);self.maps.pack(fill='x',pady=8)
        for i,key in enumerate(FIELDS):
            row,col=divmod(i,3);cell=ttk.Frame(self.maps);cell.grid(row=row,column=col,sticky='ew',padx=8,pady=3)
            ttk.Label(cell,text=LABELS[key]).pack(anchor='w')
            var=tk.StringVar();box=ttk.Combobox(cell,textvariable=var,values=[''],state='readonly',width=29);box.pack()
            self.mapping[key]=(var,box)
        self.reviewed=tk.BooleanVar(value=False)
        ttk.Checkbutton(outer,text='Sütun anlamlarını, tarih/sayı biçimini ve para birimini gözden geçirdim.',variable=self.reviewed).pack(anchor='w')
        self.preview_text=tk.Text(outer,height=5,wrap='none',font=('Consolas',9));self.preview_text.pack(fill='both',expand=True,pady=8)
        actions=ttk.Frame(outer);actions.pack(fill='x')
        ttk.Button(actions,text='Ayarları kaydet',command=self.save_config).pack(side='left')
        ttk.Button(actions,text='Analiz kapsamı / dönem karşılaştırması',command=self.scope_dialog).pack(side='left',padx=8)
        self.run_button=ttk.Button(actions,text='4. HTML raporu oluştur',command=self.generate);self.run_button.pack(side='right')
        self.status=ttk.Label(outer,text='Rapor, veri kalitesini ve yapılabilen/yapılamayan analizleri birlikte açıklar.');self.status.pack(anchor='w',pady=6)
        for var in list(self.settings.values())+[item[0] for item in self.mapping.values()]+[self.returns]:
            var.trace_add('write',lambda *_:self.reviewed.set(False))

    def gather(self):
        settings={key:var.get() for key,var in self.settings.items()}
        for key,labels in [('date_format',DATE_CHOICES),('discount_unit',DISCOUNT_CHOICES),('invalid_rows',POLICY_CHOICES)]:
            settings[key]=labels.get(settings[key],settings[key])
        settings['thousands']=None if settings['thousands']=='Yok' else settings['thousands']
        settings['currency']=settings['currency'].strip() or None
        if settings['sheet'].isdigit():settings['sheet']=int(settings['sheet'])
        return ImportConfig(mapping={key:var.get() for key,(var,_) in self.mapping.items() if var.get()},
                            mapping_reviewed=self.reviewed.get(),allow_returns=self.returns.get(),**settings)

    def choose_file(self):
        if self.busy:return
        selected=filedialog.askopenfilename(filetypes=[('Satış verisi','*.csv *.xlsx')])
        if selected:
            self.report_filters=AnalysisFilters();self.report_comparison=None
            self.source=Path(selected);self.path_label.config(text=self.source.name);self.reviewed.set(False);self.preview()

    def preview(self,use_saved=False):
        if not self.source:return
        try:
            config=self.gather();raw=read_input(self.source,config);suggest=suggest_mapping(raw.columns)
            for key,(var,box) in self.mapping.items():
                previous=var.get();box.config(values=['']+list(raw.columns))
                var.set((self.config.mapping.get(key,'') if self.config.mapping.get(key,'') in raw.columns else '') if use_saved else (previous if previous in raw.columns else suggest['suggestions'].get(key,'')))
            self.reviewed.set(False)
            text=f'{len(raw):,} veri kaydı; {len(raw.columns)} sütun. Eşleştirmeler öneridir.\n'
            if suggest['ambiguous']:text+='Birden fazla aday: '+json.dumps(suggest['ambiguous'],ensure_ascii=False)+'\n'
            text+=raw.head(5).to_string(index=False)
            self.preview_text.delete('1.0','end');self.preview_text.insert('end',text)
        except (ValueError,OSError,LookupError,ImportError) as exc:messagebox.showerror('Dosya okunamadı',str(exc))

    def load_config(self):
        if self.busy:return
        name=filedialog.askopenfilename(filetypes=[('Eşleştirme ayarı','*.json')])
        if not name:return
        try:
            self.config=ImportConfig.load(name)
            for key,var in self.settings.items():
                value=getattr(self.config,key)
                if key in ('date_format','discount_unit','invalid_rows'):
                    choices_map={'date_format':DATE_CHOICES,'discount_unit':DISCOUNT_CHOICES,'invalid_rows':POLICY_CHOICES}[key]
                    value=next((label for label,internal in choices_map.items() if internal==value),value)
                var.set('Yok' if key=='thousands' and value is None else ('' if value is None else str(value)))
            for key,(var,_) in self.mapping.items():var.set(self.config.mapping.get(key,''))
            self.returns.set(self.config.allow_returns)
            if self.source:self.preview(use_saved=True)
            matches={key:var.get() for key,(var,_) in self.mapping.items() if var.get()}==self.config.mapping
            self.reviewed.set(self.config.mapping_reviewed and matches)
        except (ValueError,OSError) as exc:messagebox.showerror('Ayar okunamadı',str(exc))

    def save_config(self):
        try:
            config=self.gather()
            if not config.mapping_reviewed:raise ValueError('Önce alan anlamları ve biçimler için gözden geçirme kutusu işaretlenmeli.')
            name=filedialog.asksaveasfilename(defaultextension='.json',filetypes=[('Eşleştirme ayarı','*.json')])
            if name:
                if self.source and Path(name).resolve()==self.source.resolve():raise ValueError('Kaynak dosyanın üzerine ayar yazılamaz.')
                config.save(name);self.status.config(text='Eşleştirme ayarları kaydedildi.')
        except (ValueError,OSError) as exc:messagebox.showerror('Ayar kaydedilemedi',str(exc))

    def scope_dialog(self):
        if self.busy:return
        window=tk.Toplevel(self.root);window.title('Analiz kapsamı');window.geometry('650x580')
        body=ttk.Frame(window,padding=16);body.pack(fill='both',expand=True)
        ttk.Label(body,text='Tarih örneği: 2026-03-31. Boş alan tüm mevcut kapsamı korur.').pack(anchor='w')
        ttk.Label(body,text='İki dönem karşılaştırmasında genel başlangıç/bitiş alanları boş kalır.').pack(anchor='w')
        values={}
        labels={'start':'Genel başlangıç tarihi','end':'Genel bitiş tarihi','category':'Kategori (tam eşleşme)',
                'region':'Bölge (tam eşleşme)','product':'Ürün adı (tam eşleşme)',
                'baseline_start':'Önceki dönem başlangıcı','baseline_end':'Önceki dönem bitişi',
                'current_start':'Güncel dönem başlangıcı','current_end':'Güncel dönem bitişi'}
        grid=ttk.Frame(body);grid.pack(fill='x',pady=12)
        for i,(key,label) in enumerate(labels.items()):
            initial=(self.report_filters.start or '') if key=='start' else (self.report_filters.end or '') if key=='end' else '|'.join(self.report_filters.segments.get(key,[])) if key in ('category','region','product') else (self.report_comparison or {}).get(key,'')
            values[key]=tk.StringVar(value=initial)
            ttk.Label(grid,text=label).grid(row=i,column=0,sticky='w',padx=5,pady=5)
            ttk.Entry(grid,textvariable=values[key],width=35).grid(row=i,column=1,sticky='ew',padx=5,pady=5)
        ttk.Label(body,text='Birden fazla segment değeri | ile ayrılabilir.\nDönemler çakışamaz. Veri olmayan dönem sıfır satış kabul edilmez.').pack(anchor='w')
        def save():
            try:
                from .questions import validate_comparison
                fields={key:var.get().strip() for key,var in values.items()}
                filters=AnalysisFilters(start=fields['start'] or None,end=fields['end'] or None,
                    segments={key:fields[key].split('|') for key in ('category','region','product') if fields[key]})
                comp={key:fields[key] for key in ('baseline_start','baseline_end','current_start','current_end')}
                comp=comp if any(comp.values()) else None
                if comp:
                    validate_comparison(comp)
                    if filters.start or filters.end:raise ValueError('Dönem karşılaştırması için genel tarih alanları boş olmalı.')
                self.report_filters=filters;self.report_comparison=comp
                self.status.config(text='Analiz kapsamı kaydedildi; raporda filtreler ve dönem sınırları gösterilecek.');window.destroy()
            except ValueError as exc:messagebox.showerror('Kapsam geçersiz',str(exc),parent=window)
        ttk.Button(body,text='Kapsamı uygula',command=save).pack(anchor='e',pady=12)

    def generate(self):
        if self.busy:return
        try:
            if not self.source:raise ValueError('Bir veri dosyası seçilmeli.')
            config=self.gather()
            if not config.mapping_reviewed:raise ValueError('Alan anlamları ve biçimler gözden geçirilmeli.')
            output=filedialog.asksaveasfilename(defaultextension='.html',filetypes=[('HTML raporu','*.html')])
            if not output:return
            source=self.source
            filters=self.report_filters;comparison=self.report_comparison
            self.busy=True;self.run_button.config(state='disabled');self.status.config(text='Rapor hazırlanıyor…')
            def worker():
                try:self.events.put(('done',write_report(source,config,output,filters=filters,comparison=comparison),output))
                except Exception as exc:self.events.put(('error',str(exc),output))
            threading.Thread(target=worker,daemon=True).start();self.root.after(100,self.poll)
        except ValueError as exc:messagebox.showerror('Rapor hazırlanamadı',str(exc))

    def poll(self):
        try:kind,result,output=self.events.get_nowait()
        except queue.Empty:self.root.after(100,self.poll);return
        self.busy=False;self.run_button.config(state='normal')
        if kind=='error':messagebox.showerror('Rapor hazırlanamadı',result);self.status.config(text='Rapor oluşturulamadı; kaynak dosya korunuyor.')
        else:
            status=result['quality']['status']
            self.status.config(text=f'Rapor kaydedildi. Veri durumu: {status}.')
            webbrowser.open(Path(output).resolve().as_uri())

def main():
    import sys
    root=tk.Tk()
    if '--smoke-test' in sys.argv:root.withdraw()
    app=SalesApp(root)
    if '--smoke-test' in sys.argv:
        root.update_idletasks();assert len(app.mapping)==len(FIELDS);app.gather();root.destroy();print('Desktop UI construction: PASS');return
    root.mainloop()

if __name__=='__main__':main()
