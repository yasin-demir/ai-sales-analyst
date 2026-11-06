"""Explicit schema mapping and locale-aware import, independent of Superstore."""
from dataclasses import dataclass, field, asdict, fields
from pathlib import Path
import csv
from datetime import datetime, date
import hashlib
import json
import re
import unicodedata
import numpy as np
import pandas as pd

FIELDS = ('sales','profit','date','product','product_id','category','region','order_id','quantity','discount','currency')
ALIASES = {
 'sales': ('sales','revenue','sales amount','net sales','satış','satış tutarı','satış geliri'),
 'profit': ('profit','net profit','kâr','kar','net kâr','net kar'),
 'date': ('order date','date','transaction date','tarih','sipariş tarihi','işlem tarihi'),
 'product': ('product name','product','ürün','ürün adı'),
 'product_id': ('product id','sku','ürün kodu','ürün kimliği'),
 'category': ('category','kategori'), 'region': ('region','bölge'),
 'order_id': ('order id','sipariş no','sipariş numarası'),
 'quantity': ('quantity','adet','miktar'), 'discount': ('discount','discount rate','indirim','indirim oranı'),
 'currency': ('currency','currency code','para birimi'),
}

def normalized(value):
    value=str(value).strip().lower().replace('ı','i')
    return ''.join(c for c in unicodedata.normalize('NFKD',value) if not unicodedata.combining(c)).replace('_',' ').replace('-',' ')

@dataclass
class ImportConfig:
    mapping: dict = field(default_factory=dict)  # canonical name -> original column
    mapping_reviewed: bool = False
    delimiter: str = ','
    encoding: str = 'utf-8-sig'
    decimal: str = '.'
    thousands: str | None = None
    date_format: str = '%Y-%m-%d'
    discount_unit: str = 'fraction'
    currency: str | None = None
    allow_returns: bool = False
    invalid_rows: str = 'stop'
    sheet: str | int = 0

    def __post_init__(self):
        if not isinstance(self.mapping,dict) or set(self.mapping)-set(FIELDS):
            raise ValueError('Mapping must contain supported canonical field names.')
        if any(not isinstance(v,str) or not v.strip() for v in self.mapping.values()):
            raise ValueError('Mapped columns must be nonempty strings.')
        if len(set(self.mapping.values())) != len(self.mapping):
            raise ValueError('One source column cannot represent multiple analytical fields.')
        if self.decimal not in ('.',',') or self.thousands not in (None,'.',',',' '):
            raise ValueError('Unsupported decimal/thousands separator.')
        if self.decimal == self.thousands:
            raise ValueError('Decimal and thousands separators must differ.')
        if not isinstance(self.delimiter,str) or len(self.delimiter)!=1:
            raise ValueError('CSV delimiter must be a single character.')
        if self.discount_unit not in ('fraction','percent') or self.invalid_rows not in ('stop','exclude'):
            raise ValueError('Invalid discount unit or row policy.')
        if not isinstance(self.mapping_reviewed,bool):
            raise ValueError('mapping_reviewed must be true or false.')
        if not isinstance(self.allow_returns,bool):
            raise ValueError('allow_returns must be true or false.')
        if not isinstance(self.date_format,str) or not self.date_format:
            raise ValueError('An explicit date format is required.')
        if self.currency is not None and (not isinstance(self.currency,str) or not self.currency.strip()):
            raise ValueError('Currency must be a nonempty label or null.')
        if not isinstance(self.sheet,(str,int)) or isinstance(self.sheet,bool):
            raise ValueError('Excel sheet must be its name or zero-based index.')

    def save(self,path):
        path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(asdict(self),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

    @classmethod
    def load(cls,path):
        data=json.loads(Path(path).read_text(encoding='utf-8-sig'))
        if not isinstance(data,dict):raise ValueError('Configuration must be a JSON object.')
        unknown=set(data)-{f.name for f in fields(cls)}
        if unknown:raise ValueError(f'Unknown configuration settings: {sorted(unknown)}')
        return cls(**data)

def _headers(columns):
    values=[str(c).strip() for c in columns]
    if not values or any(not c for c in values):raise ValueError('All input columns need nonempty headers.')
    if len(set(values))!=len(values):raise ValueError('Duplicate column names; rename them before mapping.')
    return values

def read_input(path,config,limits=None):
    path=Path(path)
    if limits is not None:
        import zipfile
        from xml.etree.ElementTree import ParseError
        try:
            return _read_bounded(path, config, limits)
        except (zipfile.BadZipFile, ParseError, KeyError, IndexError) as exc:
            raise ValueError('Invalid workbook or selected sheet.') from exc
    if path.suffix.lower()=='.csv':
        try:
            with path.open(encoding=config.encoding,newline='') as handle:
                reader=csv.reader(handle,delimiter=config.delimiter)
                header=next(reader,None)
                if header is None:raise ValueError('The input file is empty.')
                names=_headers(header)
                # Ragged records must not shift columns or disappear silently.
                for record,row in enumerate(reader,start=1):
                    if len(row)!=len(header):raise ValueError(f'Data record {record}: expected {len(header)} fields, got {len(row)}.')
            raw=pd.read_csv(path,encoding=config.encoding,sep=config.delimiter,dtype=str,
                            keep_default_na=False,skip_blank_lines=False)
            raw.columns=names
        except UnicodeDecodeError as exc:
            raise ValueError('Encoding mismatch. An explicit encoding such as cp1254 or cp1252 is required.') from exc
    elif path.suffix.lower()=='.xlsx':
        try:
            values=pd.read_excel(path,sheet_name=config.sheet,header=None,dtype=object,keep_default_na=False,engine='openpyxl')
        except ImportError as exc:
            raise ValueError('Excel support requires openpyxl; install requirements.txt.') from exc
        if values.empty:raise ValueError('The selected Excel sheet is empty.')
        raw=values.iloc[1:].copy();raw.columns=_headers(values.iloc[0]);raw=raw.reset_index(drop=True)
    else:
        raise ValueError('Supported formats are .csv and .xlsx. Older .xls files must be exported first.')
    if raw.empty:raise ValueError('The selected table contains no data records.')
    return raw


def _read_bounded(path, config, limits):
    """Bound records before constructing a DataFrame, including XLSX ZIP payloads."""
    import sys
    import zipfile
    from openpyxl import load_workbook
    from .resources import ResourceLimit
    if path.stat().st_size > limits.upload_bytes:
        raise ResourceLimit('Source byte limit exceeded.')
    rows, used = [], 0
    def append(row, names):
        nonlocal used
        if len(rows) >= limits.data_rows:
            raise ResourceLimit('Data row limit exceeded.')
        if len(row) != len(names):
            raise ValueError(f'Data record {len(rows)+1}: expected {len(names)} fields, got {len(row)}.')
        used += sys.getsizeof(row) + sum(sys.getsizeof(cell) for cell in row)
        if used > min(limits.session_memory_bytes, limits.total_memory_bytes) // 3:
            raise ResourceLimit('Input table memory budget exceeded.')
        rows.append(row)
    def headers(row):
        if row is None:
            raise ValueError('The input file is empty.')
        if len(row) > limits.data_columns:
            raise ResourceLimit('Data column limit exceeded.')
        return _headers(row)
    if path.suffix.lower() == '.csv':
        with path.open(encoding=config.encoding, newline='') as stream:
            reader = csv.reader(stream, delimiter=config.delimiter)
            names = headers(next(reader, None))
            for row in reader:
                append(row, names)
        raw = pd.DataFrame(rows, columns=names, dtype=object)
    elif path.suffix.lower() == '.xlsx':
        # Do not trust only ZIP declarations: stream every entry through the cap
        # before openpyxl loads shared strings/styles or decompresses worksheet XML.
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > limits.xlsx_entries or sum(i.file_size for i in entries) > limits.xlsx_expanded_bytes:
                raise ResourceLimit('Expanded XLSX limit exceeded.')
            expanded = 0
            for entry in entries:
                with archive.open(entry) as stream:
                    while chunk := stream.read(64 * 1024):
                        expanded += len(chunk)
                        if expanded > limits.xlsx_expanded_bytes:
                            raise ResourceLimit('Expanded XLSX limit exceeded.')
        book = load_workbook(path, read_only=True, data_only=True, keep_links=False)
        try:
            sheet = book.worksheets[config.sheet] if isinstance(config.sheet, int) else book[config.sheet]
            # Ignore forged dimension declarations; inspect actual cells as read.
            sheet.reset_dimensions()
            iterator = sheet.iter_rows(values_only=True)
            names = headers(next(iterator, None))
            for row in iterator:
                if len(row) > limits.data_columns:
                    raise ResourceLimit('Data column limit exceeded.')
                row = list(row)
                row += [None] * max(0, len(names)-len(row))
                append(['' if cell is None else cell for cell in row], names)
            raw = pd.DataFrame(rows, columns=names, dtype=object)
        finally:
            book.close()
    else:
        raise ValueError('Supported formats are .csv and .xlsx.')
    if raw.empty:
        raise ValueError('The selected table contains no data records.')
    return raw

def suggest_mapping(columns):
    suggestions={}; ambiguous={}
    for field_name,aliases in ALIASES.items():
        candidates=[str(c) for c in columns if normalized(c) in {normalized(a) for a in aliases}]
        if len(candidates)==1:suggestions[field_name]=candidates[0]
        elif candidates:ambiguous[field_name]=candidates
    return {'suggestions':suggestions,'ambiguous':ambiguous,
            'unmapped':[str(c) for c in columns if str(c) not in suggestions.values()],
            'requires_review':True,
            'note':'Suggestions are not business definitions. Ambiguous labels such as Amount, Price, and Kazanç need explicit review.'}

def parse_number(value,config,discount=False):
    if isinstance(value,(bool,np.bool_)):raise ValueError('A boolean is not a sales amount.')
    if value is None or pd.isna(value):raise ValueError('Missing number.')
    text=str(value).strip()
    if not text:raise ValueError('Missing number.')
    percent=text.endswith('%') or text.startswith('%')
    if percent:
        if text.count('%')!=1:raise ValueError('Percent notation must have one percent sign.')
        if not discount or config.discount_unit!='percent':
            raise ValueError('Percent notation requires a discount field configured in percent units.')
        text=text.strip('%').strip()
    # Excel stores actual numeric cells independently of the chosen text locale.
    if isinstance(value,(int,float,np.number)):
        number=float(value)
    else:
        sep=re.escape(config.decimal)
        integer=r'\d+'
        if config.thousands is not None:
            group=re.escape(config.thousands)
            integer=rf'(?:\d+|\d{{1,3}}(?:{group}\d{{3}})+)'
        pattern=rf'[+-]?{integer}(?:{sep}\d+)?'
        if not re.fullmatch(pattern,text):raise ValueError('Number does not match the selected decimal/thousands format.')
        if config.thousands is not None:text=text.replace(config.thousands,'')
        number=float(text.replace(config.decimal,'.'))
    if not np.isfinite(number):raise ValueError('Number must be finite.')
    return number/100 if discount and config.discount_unit=='percent' else number

REQUIREMENTS={
 'sales_summary':('sales',), 'time_sales':('sales','date'),
 'product_sales':('sales','product_key'), 'category_sales':('sales','category'),
 'region_sales':('sales','region'), 'profitability':('sales','profit'),
 'product_losses':('profit','product_key'), 'discount_comparison':('discount','profit'),
}

def capabilities(frame,blocked=()):
    blocked=set(blocked);available=set(frame.columns)-blocked
    if 'product_id' in available or 'product' in available:available.add('product_key')
    result={}
    for name,required in REQUIREMENTS.items():
        missing=[c for c in required if c not in available]
        result[name]={'available':not missing,'missing_or_invalid_fields':missing}
    return result

def prepare(raw,config):
    if not config.mapping_reviewed:raise ValueError('Mapping review is required before analysis. Confirm field meanings in the configuration or interface.')
    raw=raw.reset_index(drop=True)
    if 'sales' not in config.mapping:raise ValueError('A reviewed mapping for sales is required.')
    missing=set(config.mapping.values())-set(raw.columns)
    if missing:raise ValueError(f'Mapped source columns not found: {sorted(missing)}')
    frame=pd.DataFrame(index=raw.index)
    issues=[];invalid=set();blocked=set()
    def issue(level,name,index,message):
        issues.append({'severity':level,'field':name,'data_record':int(index)+1 if index is not None else None,'message':message})
    for name,column in config.mapping.items():
        series=raw[column]
        if name in ('sales','profit','quantity','discount'):
            values=[]
            for index,value in enumerate(series):
                try:
                    num=parse_number(value,config,discount=name=='discount')
                    if name=='sales' and num<0 and not config.allow_returns:raise ValueError('Negative sales require explicit return handling.')
                    if name=='quantity' and (num%1!=0 or (num<0 and not config.allow_returns)):
                        raise ValueError('Quantity must be integer; negative quantities require return handling.')
                    if name=='discount' and not 0<=num<=1:raise ValueError('Discount is outside 0–1 after normalization.')
                    values.append(num)
                except ValueError as exc:
                    values.append(np.nan);invalid.add(index);issue('error',name,index,str(exc))
            frame[name]=values
        elif name=='date':
            values=[]
            for index,value in enumerate(series):
                try:
                    if value is None or pd.isna(value) or not str(value).strip():raise ValueError('Missing date.')
                    # Excel dates may already carry an unambiguous timestamp.
                    if isinstance(value,(pd.Timestamp,datetime,date,np.datetime64)):parsed_date=pd.Timestamp(value)
                    else:parsed_date=pd.to_datetime(str(value).strip(),format=config.date_format,errors='raise')
                    if pd.isna(parsed_date):raise ValueError('Missing date.')
                    values.append(parsed_date)
                except (ValueError,TypeError) as exc:
                    values.append(pd.NaT);invalid.add(index);issue('error',name,index,'Date does not match the explicit date format.')
            frame[name]=pd.to_datetime(values)
        else:
            frame[name]=series.astype('string').str.strip().replace('',pd.NA).to_numpy()
            if frame[name].isna().any():
                blocked.add(name);issue('warning',name,None,'Blank labels disable analyses requiring this field; no records are assigned invented labels.')
    duplicate_count=int(raw.duplicated().sum())
    if duplicate_count:issue('warning','records',None,f'{duplicate_count} fully repeated input records detected; no automatic deduplication is applied.')
    excluded=[]
    if invalid and config.invalid_rows=='exclude':
        excluded=sorted(i+1 for i in invalid);frame=frame.drop(index=sorted(invalid)).reset_index(drop=True)
        issue('warning','records',None,f'{len(excluded)} invalid records explicitly excluded. All metrics refer to the remaining records.')
    status='ready' if (not invalid or config.invalid_rows=='exclude') and len(frame)>0 else 'needs_attention'
    if not len(frame):issue('error','records',None,'No analyzable records remain.')
    if 'currency' in frame:
        if frame.currency.isna().any():
            status='needs_attention';issue('error','currency',None,'Currency labels may not be blank when a currency column is mapped.')
        if config.currency and frame.currency.dropna().nunique()>0 and not frame.currency.dropna().eq(config.currency).all():
            status='needs_attention';issue('error','currency',None,'Configured currency conflicts with the source currency column.')
    elif not config.currency:
        issue('warning','currency',None,'Currency is unspecified; amounts are labelled as source units, not dollars.')
    if config.allow_returns and frame.sales.lt(0).any():
        issue('warning','sales',None,'Returns are included as signed sales; negative values are retained and net totals can be zero or negative.')
    if 'order_id' in frame and 'order_id' not in blocked:
        keys=[c for c in ('date','region','currency') if c in frame and c not in blocked]
        for key in keys:
            if frame.groupby('order_id')[key].nunique().gt(1).any():
                issue('warning','order_id',None,f'Some order identifiers span multiple {key} values. Order counts use distinct provided identifiers, not inferred transactions.')
    numeric_invalid={i['field'] for i in issues if i['severity']=='error' and i['data_record'] is not None}
    enabled=capabilities(frame,blocked | (numeric_invalid if config.invalid_rows=='stop' else set()))
    if 'date' in frame and frame.date.notna().all() and len(frame):
        observed=frame.date.dt.to_period('M').nunique()
        expected=len(pd.period_range(frame.date.min().to_period('M'),frame.date.max().to_period('M'),freq='M'))
        if observed<expected:issue('warning','date',None,f'{expected-observed} unobserved calendar months are not filled with zero sales.')
    return {'status':status,'frame':frame,'issues':issues,'capabilities':enabled,
            'input_rows':len(raw),'analyzed_rows':len(frame) if status=='ready' else 0,
            'excluded_records':excluded,'duplicate_records_retained':int(raw.drop(index=sorted(invalid) if config.invalid_rows=='exclude' else []).duplicated().sum()),'blocked_fields':sorted(blocked)}

def inspect_file(path,config=None):
    config=config or ImportConfig()
    raw=read_input(path,config)
    return {'file':Path(path).name,'rows':len(raw),'columns':list(raw.columns),
            'preview':raw.head(5).to_dict(orient='records'),**suggest_mapping(raw.columns)}
