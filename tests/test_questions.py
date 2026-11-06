"""Hand-calculated question acceptance cases, independent of Superstore."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import pandas as pd
from pandas.testing import assert_frame_equal
from src.questions import (AnalysisFilters,apply_filters,summarize_sales,rank_segments,compare_periods,
    compare_previous_period,previous_period_bounds,monthly_trend,rank_product_losses,compare_discounts,run_question)
from src.importing import ImportConfig
from src.question_io import answer_file_question
from src.general_reports import build_analysis,write_report,render_html
from src.cli import main

def fixture():
    return pd.DataFrame({'date':pd.to_datetime(['2025-01-01','2025-01-31','2026-01-01','2026-01-31 23:59:59','2026-02-01','2026-02-28','2026-04-01'],format='mixed'),
        'sales':[100.,100.,100.,200.,600.,0.,-100.],'profit':[10.,10.,20.,-40.,60.,-10.,-5.],
        'category':['A','B','A','B','A','B','B'],'region':['East','West','East','East','West','West','West'],
        'product_id':['P1','P2','P1','P2','P1','P3','P3'],'product':['One','Two','One','Two','One','Three','Three'],
        'order_id':['O1','O2','O3','O4','O5','O6','O7'],'discount':[0.,0.,.1,.4,.1,.5,.5]})

YEAR={'start':'2026-01-01','end':'2026-12-31'}
WINDOWS={'baseline_start':'2026-01-01','baseline_end':'2026-01-31','current_start':'2026-02-01','current_end':'2026-02-28'}

class FilterTests(unittest.TestCase):
    def test_inclusive_end_day_and_exact_segment_selection(self):
        result=apply_filters(fixture(),{'start':'2026-01-01','end':'2026-01-31','segments':{'region':'East'}})
        self.assertEqual(len(result),2);self.assertEqual(result.sales.sum(),300)
    def test_filters_do_not_mutate_data_or_dictionary(self):
        frame=fixture();original=frame.copy(deep=True);filters={'segments':{'region':['East']}}
        apply_filters(frame,filters);assert_frame_equal(frame,original);self.assertEqual(filters,{'segments':{'region':['East']}})
    def test_invalid_filter_parameters(self):
        for value in [{'start':'01/02/2026'},{'start':'2026-02-30'},{'start':'2026-03-01','end':'2026-01-01'},
                      {'segments':{'region':[]}},{'segments':{'region':[1]}},{'segments':{'state':['X']}},{'wrong':1},{'start':''}]:
            with self.subTest(value=value),self.assertRaises(ValueError):AnalysisFilters.from_dict(value)
    def test_timezone_requires_explicit_business_normalization(self):
        frame=fixture();frame.date=frame.date.dt.tz_localize('UTC')
        self.assertEqual(summarize_sales(frame,filters=YEAR)['status'],'unavailable')

    def test_missing_filter_field_is_explicit(self):
        with self.assertRaises(ValueError):apply_filters(fixture().drop(columns='date'),YEAR)
        result=summarize_sales(fixture().drop(columns='date'),filters=YEAR)
        self.assertEqual(result['status'],'unavailable')
    def test_empty_scope_is_not_zero_activity(self):
        result=summarize_sales(fixture(),filters={'segments':{'region':'Unknown'}})
        self.assertEqual(result['status'],'empty');self.assertEqual(result['results'][0]['metrics'],{})
        self.assertEqual(result['results'][0]['scope']['selected_records'],0)

class SummaryAndRankingTests(unittest.TestCase):
    def test_filtered_summary_exact_values_and_reconciliation(self):
        result=summarize_sales(fixture(),filters=YEAR,unit='TRY')['results'][0];k=result['metrics']
        self.assertEqual(k['sales'],800);self.assertEqual(k['profit'],25);self.assertEqual(k['gross_loss'],55)
        self.assertEqual(k['positive_profit']-k['gross_loss'],k['profit']);self.assertAlmostEqual(k['margin_percent'],3.125)
        self.assertEqual(result['scope']['input_records'],7);self.assertEqual(result['scope']['selected_records'],5)
        self.assertEqual(result['units']['amount'],'TRY');self.assertEqual(result['filters']['start'],'2026-01-01')
    def test_sales_only_does_not_infer_other_metrics(self):
        result=summarize_sales(fixture()[['sales']])['results'][0]
        self.assertNotIn('profit',result['metrics']);self.assertNotIn('unique_order_ids',result['metrics'])
        self.assertTrue(result['warnings'])
    def test_rank_lowest_margin_and_aggregate_weighting(self):
        result=rank_segments(fixture(),'region',sort_by='margin_percent',filters=YEAR)['results'][0]
        self.assertEqual(result['records'][0]['region'],'East')
        self.assertAlmostEqual(result['records'][0]['margin_percent'],-20/300*100)
        self.assertTrue(result['ranking']['ascending'])
    def test_ranking_limit_does_not_change_loss_denominator(self):
        result=rank_segments(fixture(),'region',sort_by='gross_loss',limit=1,filters=YEAR)['results'][0]
        row=result['records'][0];self.assertEqual(row['gross_loss'],40)
        self.assertAlmostEqual(row['gross_loss_share_percent'],40/55*100)
        self.assertEqual(result['ranking']['total_groups'],2)
    def test_min_records_keeps_scope_denominator(self):
        result=rank_segments(fixture(),'region',sort_by='gross_loss',min_records=3,filters=YEAR)['results'][0]
        self.assertEqual(result['records'][0]['region'],'West')
        self.assertAlmostEqual(result['records'][0]['gross_loss_share_percent'],15/55*100)
        self.assertEqual(result['scope']['selected_records'],5);self.assertTrue(result['warnings'])
    def test_empty_ranking_is_distinct_from_empty_scope(self):
        result=rank_segments(fixture(),'region',min_records=99)['results'][0]
        self.assertEqual(result['status'],'empty_ranking');self.assertEqual(result['records'],[])
        self.assertEqual(result['scope']['selected_records'],7)
    def test_multiple_group_keys_are_preserved(self):
        result=rank_segments(fixture(),['region','category'],filters=YEAR)['results'][0]
        self.assertEqual(result['ranking']['by'],['region','category'])
        self.assertTrue(all('region' in r and 'category' in r for r in result['records']))
    def test_missing_labels_outside_selection_do_not_block_selected_group(self):
        frame=fixture();frame.loc[0,'category']=None
        self.assertEqual(rank_segments(frame,'category',filters=YEAR)['status'],'ok')
        self.assertEqual(rank_segments(frame,'category')['status'],'unavailable')
    def test_margin_ranking_excludes_undefined_rates(self):
        result=rank_segments(fixture(),'product',sort_by='margin_percent',filters=YEAR)['results'][0]
        self.assertNotIn('Three',[r['product'] for r in result['records']])
        self.assertTrue(any('undefined' in s for s in result['warnings']))

    def test_missing_profit_ranking_returns_unavailable(self):
        result=rank_segments(fixture().drop(columns='profit'),'region',sort_by='profit')
        self.assertEqual(result['status'],'unavailable')

    def test_nonpositive_segment_sales_margin_is_undefined(self):
        result=rank_segments(fixture(),'product',sort_by='gross_loss',filters=YEAR)['results'][0]
        row=next(r for r in result['records'] if r['product']=='Three')
        self.assertIsNone(row['margin_percent']);self.assertEqual(row['gross_loss'],15)
    def test_multiple_currencies_are_not_combined(self):
        frame=fixture();frame['currency']=['USD','USD','TRY','TRY','TRY','TRY','TRY']
        result=summarize_sales(frame)
        self.assertEqual({r['units']['amount']:r['metrics']['sales'] for r in result['results']},{'TRY':800,'USD':200})
        self.assertNotIn('metrics',result)
        with self.assertRaises(ValueError):summarize_sales(frame,unit='USD')
    def test_all_result_envelopes_are_json_safe_and_scoped(self):
        for answer in [summarize_sales(fixture()),rank_segments(fixture(),'region'),monthly_trend(fixture()),
                       rank_product_losses(fixture()),compare_discounts(fixture()),compare_periods(fixture(),WINDOWS)]:
            json.dumps(answer,allow_nan=False)
            for entry in answer['results']:
                for key in ['schema_version','analysis','status','scope','filters','units','metrics','records','warnings','definitions']:
                    self.assertIn(key,entry)

class ComparisonTests(unittest.TestCase):
    def test_absolute_growth_and_margin_pp_hand_calculated(self):
        result=compare_periods(fixture(),WINDOWS,unit='USD')['results'][0]
        rows={r['metric']:r for r in result['records']}
        self.assertEqual(rows['sales']['baseline'],300);self.assertEqual(rows['sales']['current'],600)
        self.assertEqual(rows['sales']['absolute_change'],300);self.assertEqual(rows['sales']['relative_change_percent'],100)
        self.assertEqual(rows['profit']['absolute_change'],70);self.assertIsNone(rows['profit']['relative_change_percent'])
        self.assertAlmostEqual(rows['margin_percent']['margin_change_pp'],15)
        self.assertIsNone(rows['margin_percent']['relative_change_percent'])
        self.assertTrue(any('lengths differ' in s for s in result['warnings']))
    def test_zero_baseline_growth_is_not_infinite(self):
        frame=fixture();frame.loc[frame.date.dt.to_period('M').eq(pd.Period('2026-01')),'sales']=0
        rows={r['metric']:r for r in compare_periods(frame,WINDOWS)['results'][0]['records']}
        self.assertIsNone(rows['sales']['relative_change_percent']);self.assertEqual(rows['sales']['absolute_change'],600)
    def test_empty_period_is_insufficient_not_zero(self):
        settings={**WINDOWS,'current_start':'2026-03-01','current_end':'2026-03-31'}
        answer=compare_periods(fixture(),settings)
        self.assertEqual(answer['status'],'insufficient_data');self.assertEqual(answer['results'][0]['metrics'],{})
    def test_overlapping_reversed_and_incomplete_bounds_rejected(self):
        for windows in [{**WINDOWS,'baseline_end':'2026-02-01'},{**WINDOWS,'current_end':'2025-01-01'}, {'current_start':'2026-02-01'}]:
            with self.subTest(windows=windows),self.assertRaises(ValueError):compare_periods(fixture(),windows)
        with self.assertRaises(ValueError):compare_periods(fixture(),WINDOWS,filters=YEAR)
    def test_observed_dates_do_not_prove_complete_period(self):
        frame=fixture().loc[lambda x:x.date.ne(pd.Timestamp('2026-02-28'))]
        result=compare_periods(frame,WINDOWS)['results'][0]
        self.assertTrue(any('full-period coverage' in s for s in result['warnings']))
    def test_previous_and_yoy_bounds_include_leap_year(self):
        previous=previous_period_bounds('2024-03')
        self.assertEqual(previous['baseline_start'],'2024-02-01');self.assertEqual(previous['baseline_end'],'2024-02-29')
        yearly=previous_period_bounds('2026','Y','year_over_year');self.assertEqual(yearly['baseline_end'],'2025-12-31')
        result=compare_previous_period(fixture(),'2026-01',mode='year_over_year')['results'][0]
        rows={r['metric']:r for r in result['records']};self.assertEqual(rows['sales']['relative_change_percent'],50)
    def test_invalid_period_labels_rejected(self):
        for args in [('2026-13','M','previous'),('2026/01','M','previous'),('2026','Q','previous'),('2026','Y','future')]:
            with self.assertRaises(ValueError):previous_period_bounds(*args)

class OtherQuestionTests(unittest.TestCase):
    def test_monthly_trend_does_not_fill_missing_month(self):
        result=monthly_trend(fixture(),filters=YEAR)['results'][0]
        self.assertEqual([r['month'] for r in result['records']],['2026-01','2026-02','2026-04'])
        self.assertTrue(any('1 calendar months' in s for s in result['warnings']))
    def test_loss_ranking_keeps_full_denominator_and_outside(self):
        result=rank_product_losses(fixture(),limit=1,filters=YEAR)['results'][0]
        self.assertEqual(result['metrics']['total_gross_loss'],55)
        self.assertEqual(result['metrics']['top_gross_loss'],40);self.assertEqual(result['metrics']['outside_top_gross_loss'],15)
        self.assertAlmostEqual(result['metrics']['top_share_percent'],40/55*100)
    def test_no_loss_scope_returns_zero_loss_not_savings_claim(self):
        frame=fixture();frame.profit=frame.profit.abs()
        result=rank_product_losses(frame)['results'][0]
        self.assertEqual(result['metrics']['total_gross_loss'],0);self.assertEqual(result['records'],[])
    def test_discount_summary_and_missing_product_requirement(self):
        result=compare_discounts(fixture(),filters=YEAR)['results'][0]
        loss=next(r for r in result['records'] if r['profit_status']=='Loss')
        self.assertAlmostEqual(loss['mean_discount_percent'],(40+50+50)/3)
        self.assertEqual(rank_product_losses(fixture().drop(columns=['product','product_id']))['status'],'unavailable')
    def test_unsupported_intent_and_unused_parameters_rejected(self):
        for call in [lambda:run_question(fixture(),'predict'),lambda:summarize_sales(fixture(),by='region'),
                     lambda:rank_segments(fixture(),'currency'),lambda:rank_segments(fixture(),'region',limit=0),
                     lambda:rank_segments(fixture(),'region',sort_by='imaginary')]:
            with self.assertRaises(ValueError):call()
    def test_question_functions_do_not_mutate_input(self):
        frame=fixture();original=frame.copy(deep=True)
        for call in [summarize_sales,monthly_trend,rank_product_losses,compare_discounts]:call(frame,filters=YEAR)
        rank_segments(frame,'region');compare_periods(frame,WINDOWS);assert_frame_equal(frame,original)

class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.root=Path(self.directory.name);self.path=self.root/'data.csv'
        frame=fixture();frame.date=frame.date.dt.strftime('%Y-%m-%d');frame.to_csv(self.path,index=False)
        self.config=ImportConfig(mapping={c:c for c in frame.columns},mapping_reviewed=True,allow_returns=True,currency='TRY')
    def test_report_filters_and_segment_profitability_match_questions(self):
        answer=build_analysis(self.path,self.config,filters=YEAR)
        self.assertEqual(answer['selection']['selected_records'],5)
        part=answer['partitions'][0]
        self.assertEqual(part['metrics']['profit'],25);self.assertIn('region_profitability',part['tables'])
        self.assertEqual(part['question_results']['summary']['results'][0]['scope']['input_records'],7)
        self.assertEqual(part['question_results']['summary']['results'][0]['scope']['selected_records'],5)
        self.assertTrue(any(c['title']=='Profitability by region' for c in part['charts']))
    def test_report_comparison_is_scoped_and_rendered(self):
        result=build_analysis(self.path,self.config,comparison=WINDOWS)
        self.assertIn('period_comparison',result['partitions'][0]['tables'])
        self.assertIn('margin_change_pp',render_html(result))
    def test_invalid_comparison_is_rejected_even_if_selection_is_empty(self):
        with self.assertRaises(ValueError):build_analysis(self.path,self.config,filters={'segments':{'region':'Absent'}},comparison={})

    def test_empty_report_selection_is_explicit(self):
        result=build_analysis(self.path,self.config,filters={'segments':{'region':'Absent'}})
        self.assertEqual(result['selection']['status'],'empty');self.assertEqual(result['partitions'],[])
        self.assertIn('selected: 0',render_html(result))
    def test_file_question_keeps_quality_source_and_exclusions(self):
        result=answer_file_question(self.path,self.config,{'analysis':'summary','filters':YEAR})
        self.assertEqual(result['status'],'ok');self.assertIn('sha256',result['source'])
        self.assertEqual(result['answer']['results'][0]['scope']['raw_input_records'],7)
        self.assertEqual(result['answer']['results'][0]['metrics']['profit'],25)
    def test_cli_question_and_filter_commands(self):
        self.config.save(self.root/'config.json')
        (self.root/'request.json').write_text(json.dumps({'analysis':'segment_performance','by':'region','sort_by':'gross_loss','filters':YEAR}),encoding='utf-8')
        (self.root/'filters.json').write_text(json.dumps(YEAR),encoding='utf-8')
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['questions']),0)
            self.assertEqual(main(['ask',str(self.path),'--config',str(self.root/'config.json'),'--request',str(self.root/'request.json'),'--output',str(self.root/'answer.json')]),0)
            self.assertEqual(main(['analyze',str(self.path),'--config',str(self.root/'config.json'),'--filters',str(self.root/'filters.json'),'--output',str(self.root/'report.html')]),0)
            self.assertEqual(main(['ask',str(self.path),'--config',str(self.root/'config.json'),'--request',str(self.root/'request.json'),'--output',str(self.root/'request.json')]),1)
        answer=json.loads((self.root/'answer.json').read_text(encoding='utf-8'))
        self.assertEqual(answer['answer']['results'][0]['records'][0]['region'],'East')

if __name__=='__main__':unittest.main()
