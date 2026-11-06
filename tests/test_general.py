"""General import/report tests use synthetic data only."""
import contextlib
import io
import json
import tempfile
from pathlib import Path
import unittest
from datetime import datetime
import pandas as pd
from pandas.testing import assert_frame_equal
from src.importing import ImportConfig,read_input,suggest_mapping,parse_number,prepare,inspect_file
from src.general_reports import build_analysis,write_report,render_html
from src.cli import main

def config(**kwargs):
    return ImportConfig(mapping_reviewed=True,**kwargs)

class MappingTests(unittest.TestCase):
    def test_turkish_aliases_and_ambiguous_business_labels(self):
        suggestion=suggest_mapping(['Satış Tutarı','İşlem Tarihi','ÜRÜN KODU','Kazanç','Price'])
        self.assertEqual(suggestion['suggestions']['sales'],'Satış Tutarı')
        self.assertEqual(suggestion['suggestions']['date'],'İşlem Tarihi')
        self.assertEqual(suggestion['suggestions']['product_id'],'ÜRÜN KODU')
        self.assertNotIn('profit',suggestion['suggestions'])
        self.assertIn('Kazanç',suggestion['unmapped'])
        self.assertTrue(suggestion['requires_review'])

    def test_multiple_candidates_are_not_selected(self):
        result=suggest_mapping(['Sales','Revenue'])
        self.assertNotIn('sales',result['suggestions']);self.assertEqual(len(result['ambiguous']['sales']),2)

    def test_review_and_sales_mapping_required(self):
        raw=pd.DataFrame({'Revenue':['10']})
        for settings in [ImportConfig(mapping={'sales':'Revenue'}),config(mapping={})]:
            with self.assertRaises(ValueError):prepare(raw,settings)

    def test_config_must_be_an_object(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'bad.json';path.write_text('[]',encoding='utf-8')
            with self.assertRaises(ValueError):ImportConfig.load(path)

    def test_invalid_mapping_and_settings_rejected(self):
        for kwargs in [{'mapping':{'wrong':'X'}},{'mapping':{'sales':'X','profit':'X'}},
                       {'decimal':',','thousands':','},{'delimiter':'::'},{'discount_unit':'unknown'},
                       {'mapping_reviewed':'yes'},{'invalid_rows':'silently_drop'}]:
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):ImportConfig(**kwargs)

    def test_config_roundtrip_and_unknown_option(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'config.json';settings=config(mapping={'sales':'Tutar'},decimal=',',delimiter=';')
            settings.save(path);self.assertEqual(ImportConfig.load(path),settings)
            path.write_text('{"unknown":1}',encoding='utf-8')
            with self.assertRaises(ValueError):ImportConfig.load(path)

class FormatTests(unittest.TestCase):
    def test_localized_and_english_numbers(self):
        self.assertEqual(parse_number('1.234,50',config(decimal=',',thousands='.')),1234.5)
        self.assertEqual(parse_number('1,234.50',config(thousands=',')),1234.5)
        for value in ['1,2','12,34.00','NaN','Infinity','', '1,000,00']:
            with self.subTest(value=value),self.assertRaises(ValueError):parse_number(value,config(thousands=','))

    def test_boolean_is_not_an_amount(self):
        with self.assertRaises(ValueError):parse_number(True,config())

    def test_percent_units_are_explicit(self):
        self.assertEqual(parse_number('%20',config(discount_unit='percent'),True),.2)
        self.assertEqual(parse_number('20%',config(discount_unit='percent'),True),.2)
        self.assertEqual(parse_number('0.20',config(),True),.2)
        with self.assertRaises(ValueError):parse_number('20%',config(),True)
        with self.assertRaises(ValueError):parse_number('20%',config(),False)

    def test_repeated_percent_sign_rejected(self):
        with self.assertRaises(ValueError):parse_number('%%20',config(discount_unit='percent'),True)

    def test_date_direction_is_explicit(self):
        raw=pd.DataFrame({'S':['10'],'D':['03/04/2026']})
        a=prepare(raw,config(mapping={'sales':'S','date':'D'},date_format='%d/%m/%Y'))
        b=prepare(raw,config(mapping={'sales':'S','date':'D'},date_format='%m/%d/%Y'))
        self.assertEqual(a['frame'].date.iloc[0],pd.Timestamp('2026-04-03'))
        self.assertEqual(b['frame'].date.iloc[0],pd.Timestamp('2026-03-04'))
        self.assertEqual(prepare(raw,config(mapping={'sales':'S','date':'D'}))['status'],'needs_attention')

    def test_stop_or_explicit_exclusion_with_audit(self):
        raw=pd.DataFrame({'S':['10','bad','20'],'D':['2026-01-01','2026-01-02','bad']})
        settings=config(mapping={'sales':'S','date':'D'})
        stopped=prepare(raw,settings)
        self.assertEqual(stopped['status'],'needs_attention');self.assertEqual(stopped['analyzed_rows'],0)
        settings.invalid_rows='exclude';selected=prepare(raw,settings)
        self.assertEqual(selected['status'],'ready');self.assertEqual(selected['excluded_records'],[2,3])
        self.assertEqual(selected['frame'].sales.sum(),10)

    def test_returns_require_policy_and_zero_is_allowed(self):
        raw=pd.DataFrame({'S':['100','-100','0']})
        self.assertEqual(prepare(raw,config(mapping={'sales':'S'}))['status'],'needs_attention')
        selected=prepare(raw,config(mapping={'sales':'S'},allow_returns=True))
        self.assertEqual(selected['status'],'ready');self.assertEqual(selected['frame'].sales.sum(),0)

    def test_invalid_discounts_and_noninteger_quantity(self):
        for field,value in [('discount','120'),('quantity','1.5')]:
            raw=pd.DataFrame({'S':['10'],'X':[value]})
            result=prepare(raw,config(mapping={'sales':'S',field:'X'},discount_unit='percent'))
            self.assertEqual(result['status'],'needs_attention')

    def test_missing_dimensions_disable_only_relevant_capability(self):
        raw=pd.DataFrame({'S':['10','20'],'R':['East','']})
        result=prepare(raw,config(mapping={'sales':'S','region':'R'}))
        self.assertEqual(result['status'],'ready');self.assertTrue(result['capabilities']['sales_summary']['available'])
        self.assertFalse(result['capabilities']['region_sales']['available'])

    def test_duplicates_are_visible_and_retained(self):
        raw=pd.DataFrame({'S':['10','10']});original=raw.copy(deep=True)
        result=prepare(raw,config(mapping={'sales':'S'}))
        self.assertEqual(result['duplicate_records_retained'],1);self.assertEqual(result['frame'].sales.sum(),20)
        assert_frame_equal(raw,original)

    def test_excluded_duplicate_records_are_not_counted_as_retained(self):
        raw=pd.DataFrame({'S':['bad','bad','10']})
        result=prepare(raw,config(mapping={'sales':'S'},invalid_rows='exclude'))
        self.assertEqual(result['duplicate_records_retained'],0)
        self.assertEqual(result['excluded_records'],[1,2])

    def test_nonsequential_input_index_is_safe(self):
        raw=pd.DataFrame({'S':['bad','20']},index=[5,9])
        result=prepare(raw,config(mapping={'sales':'S'},invalid_rows='exclude'))
        self.assertEqual(result['excluded_records'],[1]);self.assertEqual(result['frame'].sales.sum(),20)

class FileTests(unittest.TestCase):
    def test_semicolon_and_explicit_encoding(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'turkish.csv';path.write_text('Ürün;Tutar\nÇığ;1.234,50\n',encoding='cp1254')
            settings=config(mapping={'sales':'Tutar','product':'Ürün'},delimiter=';',encoding='cp1254',decimal=',',thousands='.')
            result=prepare(read_input(path,settings),settings)
            self.assertEqual(result['frame'].sales.iloc[0],1234.5)
            with self.assertRaises(ValueError):read_input(path,ImportConfig())

    def test_duplicate_headers_and_ragged_rows_rejected(self):
        for text in ['S,S\n1,2\n','S,R\n1,2,3\n','S,R\n1\n','S\n']:
            with tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/'bad.csv';path.write_text(text,encoding='utf-8')
                with self.subTest(text=text),self.assertRaises(ValueError):read_input(path,ImportConfig())

    def test_excel_sheet_native_numbers_dates_and_text_identifiers(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'data.xlsx'
            with pd.ExcelWriter(path,engine='openpyxl') as writer:
                pd.DataFrame({'S':[99]}).to_excel(writer,sheet_name='Other',index=False)
                pd.DataFrame({'S':[1234.5],'D':[datetime(2026,4,3)],'ID':['0012']}).to_excel(writer,sheet_name='Sales',index=False)
            settings=config(mapping={'sales':'S','date':'D','product_id':'ID'},sheet='Sales',decimal=',',thousands='.')
            result=prepare(read_input(path,settings),settings)
            self.assertEqual(result['status'],'ready');self.assertEqual(result['frame'].sales.iloc[0],1234.5)
            self.assertEqual(result['frame'].product_id.iloc[0],'0012');self.assertEqual(result['frame'].date.iloc[0],pd.Timestamp('2026-04-03'))

    def test_excel_duplicate_headers_and_unknown_extension(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'bad.xlsx';pd.DataFrame([['S','S'],[1,2]]).to_excel(path,index=False,header=False)
            with self.assertRaises(ValueError):read_input(path,ImportConfig())
            with self.assertRaises(ValueError):read_input(Path(directory)/'legacy.xls',ImportConfig())

class ReportTests(unittest.TestCase):
    def report(self,data,settings):
        directory=tempfile.TemporaryDirectory();self.addCleanup(directory.cleanup)
        path=Path(directory.name)/'data.csv';pd.DataFrame(data).to_csv(path,index=False)
        return build_analysis(path,settings)

    def test_sales_only_does_not_invent_profit_orders_or_currency(self):
        result=self.report({'S':[10,20]},config(mapping={'sales':'S'}))
        part=result['partitions'][0];self.assertEqual(part['metrics']['sales'],30)
        self.assertNotIn('profit',part['metrics']);self.assertNotIn('unique_order_ids',part['metrics'])
        self.assertNotIn('product_losses',part['tables']);self.assertIn('unspecified',part['unit'])
        self.assertNotIn('$',render_html(result))

    def test_sparse_three_column_file_gets_only_supported_reports(self):
        result=self.report({'T':['2026-01-01','2026-03-01'],'P':['A','B'],'S':[10,20]},
            config(mapping={'date':'T','product':'P','sales':'S'},currency='TRY'))
        part=result['partitions'][0]
        self.assertEqual(set(part['tables']),{'product_sales','monthly_sales','calendar_month_sales'})
        self.assertEqual(len(part['tables']['monthly_sales']),2)
        self.assertTrue(any('unobserved' in i['message'] for i in result['quality']['issues']))
        self.assertEqual(len(part['charts']),2)

    def test_profit_reconciliation_and_composite_product_keys(self):
        result=self.report({'S':[100,100,100],'P':[-20,30,-10],'ID':['1','1','1'],'Name':['A','A','B']},
            config(mapping={'sales':'S','profit':'P','product_id':'ID','product':'Name'},currency='USD'))
        part=result['partitions'][0];k=part['metrics']
        self.assertEqual(k['positive_profit']-k['gross_loss'],k['profit'])
        self.assertEqual(len(part['tables']['product_losses']),2)
        self.assertAlmostEqual(part['tables']['product_losses'][-1]['cumulative_share_percent'],100)

    def test_no_losses_do_not_generate_pareto(self):
        result=self.report({'S':[10],'P':[1],'Name':['A']},config(mapping={'sales':'S','profit':'P','product':'Name'}))
        part=result['partitions'][0];self.assertEqual(part['metrics']['gross_loss'],0)
        self.assertFalse(part['tables']['product_losses'])
        self.assertFalse(any(c['title']=='Loss concentration' for c in part['charts']))

    def test_discount_groups_include_constant_and_single_observation(self):
        result=self.report({'S':[10,20,30],'P':[-1,2,0],'D':['50%','0%','20%']},
            config(mapping={'sales':'S','profit':'P','discount':'D'},discount_unit='percent'))
        part=result['partitions'][0];self.assertEqual(len(part['tables']['discount_comparison']),3)
        self.assertTrue(any(c['title']=='Discount distribution' for c in part['charts']))

    def test_multiple_currencies_never_have_a_combined_monetary_total(self):
        result=self.report({'S':[10,20,30],'C':['USD','TRY','USD']},config(mapping={'sales':'S','currency':'C'}))
        self.assertEqual({part['unit']:part['metrics']['sales'] for part in result['partitions']},{'TRY':20,'USD':40})
        self.assertNotIn('metrics',result)
        self.assertIn('no combined monetary total',render_html(result))

    def test_missing_and_conflicting_currency_block_report(self):
        for codes,unit in [(['USD',''],'USD'),(['USD','TRY'],'USD')]:
            result=self.report({'S':[10,20],'C':codes},config(mapping={'sales':'S','currency':'C'},currency=unit))
            self.assertEqual(result['quality']['status'],'needs_attention');self.assertFalse(result['partitions'])

    def test_net_zero_or_negative_sales_has_no_margin(self):
        for values in [[100,-100],[100,-200]]:
            result=self.report({'S':values,'P':[10,-5]},config(mapping={'sales':'S','profit':'P'},allow_returns=True))
            self.assertIsNone(result['partitions'][0]['metrics']['margin_percent'])

    def test_invalid_data_produces_quality_report_not_silent_analysis(self):
        result=self.report({'S':['not a number']},config(mapping={'sales':'S'}))
        self.assertEqual(result['quality']['status'],'needs_attention');self.assertEqual(result['partitions'],[])
        self.assertIn('Data readiness',render_html(result))

    def test_user_labels_are_html_escaped(self):
        result=self.report({'S':[10],'P':['<script>alert(1)</script>']},config(mapping={'sales':'S','product':'P'}))
        html=render_html(result)
        self.assertNotIn('<script>alert(1)</script>',html);self.assertIn('&lt;script&gt;',html)

    def test_outputs_do_not_overwrite_source_and_json_has_no_images(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'data.csv';path.write_text('S,P\n10,A\n',encoding='utf-8');original=path.read_bytes()
            settings=config(mapping={'sales':'S','product':'P'})
            with self.assertRaises(ValueError):write_report(path,settings,path)
            html=Path(directory)/'report.html';js=Path(directory)/'report.json'
            write_report(path,settings,html,js)
            payload=json.loads(js.read_text(encoding='utf-8'))
            self.assertNotIn('image',payload['partitions'][0]['charts'][0])
            self.assertEqual(path.read_bytes(),original);self.assertIn('data:image/png;base64',html.read_text(encoding='utf-8'))

    def test_cli_inspect_review_reuse_and_quality_exit(self):
        with tempfile.TemporaryDirectory() as directory,contextlib.redirect_stdout(io.StringIO()):
            root=Path(directory);path=root/'data.csv';path.write_text('Sales,Product\n10,A\n',encoding='utf-8')
            draft=root/'draft.json';reviewed=root/'reviewed.json';report=root/'report.html'
            self.assertEqual(main(['inspect',str(path),'--template',str(draft)]),0)
            self.assertFalse(ImportConfig.load(draft).mapping_reviewed)
            self.assertEqual(main(['analyze',str(path),'--config',str(draft),'--output',str(report)]),1)
            self.assertEqual(main(['analyze',str(path),'--config',str(draft),'--output',str(report),'--confirm-mapping','--save-config',str(reviewed)]),0)
            self.assertTrue(ImportConfig.load(reviewed).mapping_reviewed)
            self.assertEqual(main(['analyze',str(path),'--config',str(reviewed),'--output',str(report)]),0)
            path.write_text('Sales,Product\nbad,A\n',encoding='utf-8')
            self.assertEqual(main(['analyze',str(path),'--config',str(reviewed),'--output',str(report)]),2)
            self.assertEqual(main(['analyze',str(path),'--config',str(reviewed),'--output',str(reviewed)]),1)

if __name__=='__main__':unittest.main()
