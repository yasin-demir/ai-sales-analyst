"""Synthetic fixtures with hand-checkable totals. No real customer data required."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from decimal import Decimal
import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal
from src.data import validate_sales, load_sales
from src.analysis import (summarize_csv, overall_metrics, segment_performance, discount_profile,
    product_gross_losses, loss_concentration, category_region_losses, time_performance,
    calendar_month_performance, loss_pool_overlap, loss_pool_products, margin_bridge)
from src.main import main

def fixture():
    specs=[(100,20,0,'Furniture','Central','A','2016'),
           (100,-30,.5,'Furniture','Central','A','2016'),
           (800,80,.2,'Technology','East','B','2016'),
           (100,10,.1,'Furniture','Central','A','2017'),
           (900,90,.2,'Technology','East','B','2017'),
           (100,0,.2,'Furniture','Central','C','2017')]
    return pd.DataFrame([{'Row ID':i, 'Order ID':f'O{i//2}', 'Order Date':f'01/01/{year}',
        'Ship Date':f'01/03/{year}', 'Product ID':product, 'Product Name':f'Product {product}',
        'Category':category,'Sub-Category':category+' sub','Region':region,'State':'Example',
        'Postal Code':'00123','Sales':sales,'Profit':profit,'Quantity':1,'Discount':discount}
        for i,(sales,profit,discount,category,region,product,year) in enumerate(specs,1)])

class DataTests(unittest.TestCase):
    def test_validation_copies_and_preserves_postal_code(self):
        raw=fixture(); original=raw.copy(deep=True)
        checked=validate_sales(raw)
        assert_frame_equal(raw,original)
        self.assertEqual(checked['Postal Code'].iloc[0],'00123')
        self.assertTrue(pd.api.types.is_datetime64_any_dtype(checked['Order Date']))

    def test_missing_columns_and_empty_data_rejected(self):
        for frame in [fixture().drop(columns='Region'),fixture().iloc[:0]]:
            with self.subTest(columns=list(frame)),self.assertRaises(ValueError):validate_sales(frame)

    def test_bad_identifiers_rejected(self):
        for col,value in [('Product ID',None),('Region','  '),('Row ID',2)]:
            raw=fixture();raw.loc[0,col]=value
            with self.subTest(col=col),self.assertRaises(ValueError):validate_sales(raw)

    def test_bad_numeric_values_rejected(self):
        for col,value in [('Sales',0),('Sales',-1),('Profit',np.inf),('Profit',np.nan),
                          ('Quantity',1.5),('Quantity',0),('Discount',1.1),('Discount',-.1)]:
            raw=fixture();raw[col]=raw[col].astype(float);raw.loc[0,col]=value
            with self.subTest(col=col,value=value),self.assertRaises(ValueError):validate_sales(raw)

    def test_malformed_numeric_rejected(self):
        raw=fixture();raw['Sales']=raw['Sales'].astype(object);raw.loc[0,'Sales']='unknown'
        with self.assertRaises(ValueError):validate_sales(raw)

    def test_invalid_missing_and_reversed_dates_rejected(self):
        for col,value in [('Order Date','not a date'),('Ship Date',None),('Ship Date','12/31/2015')]:
            raw=fixture();raw.loc[0,col]=value
            with self.subTest(value=value),self.assertRaises(ValueError):validate_sales(raw)

    def test_loader_encoding_and_postal_code(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'sample.csv';raw=fixture();raw.loc[0,'Product Name']='Caf\u00e9'
            raw.to_csv(path,index=False,encoding='cp1252')
            result=load_sales(path)
            self.assertEqual(result['Product Name'].iloc[0],'Caf\u00e9')
            self.assertEqual(result['Postal Code'].iloc[0],'00123')

class MetricTests(unittest.TestCase):
    def setUp(self): self.frame=validate_sales(fixture())

    def test_overall_and_profit_reconciliation(self):
        result=overall_metrics(self.frame)
        self.assertEqual(result['sales'],2100)
        self.assertEqual(result['profit'],170)
        self.assertEqual(result['gross_loss'],30)
        self.assertEqual(result['positive_profit'],200)
        self.assertEqual(result['positive_profit']-result['gross_loss'],result['profit'])
        self.assertEqual(result['orders'],4)
        self.assertAlmostEqual(result['average_order_value'],525)

    def test_segment_margin_uses_aggregates_not_row_average(self):
        result=segment_performance(self.frame,'Category')
        self.assertEqual(result.loc['Furniture','Total_Sales'],400)
        self.assertEqual(result.loc['Furniture','Total_Profit'],0)
        self.assertEqual(result.loc['Furniture','Profit_Margin'],0)
        all_result=segment_performance(self.frame.assign(All='all'),'All')
        self.assertAlmostEqual(all_result.loc['all','Profit_Margin'],170/2100*100)
        self.assertNotAlmostEqual(all_result.loc['all','Profit_Margin'],(self.frame.Profit/self.frame.Sales*100).mean())

    def test_zero_denominator_is_undefined(self):
        raw=self.frame.copy();raw['Sales']=0
        result=segment_performance(raw,'Category')
        self.assertTrue(result['Profit_Margin'].isna().all())

    def test_composite_product_key_and_positive_offsets(self):
        raw=self.frame.copy();raw.loc[0,'Profit']=-5;raw.loc[0,'Product Name']='Alternate A'
        losses=product_gross_losses(raw)
        self.assertEqual(list(losses),[30,5])
        self.assertEqual(len(losses),2)
        self.assertEqual(losses.sum(),35)

    def test_top_n_shares_and_outside_partition(self):
        result=loss_concentration(self.frame,limits=(1,50))
        self.assertEqual(result.loc['Top 50 product groups','Groups_Included'],1)
        self.assertEqual(result.loc['Top 1 product groups','Loss_Share'],100)
        self.assertEqual(result.loc['Outside top 50','Gross_Loss'],0)
        self.assertEqual(result.loc['Top 50 product groups','Gross_Loss']+result.loc['Outside top 50','Gross_Loss'],30)

    def test_no_losses_have_zero_shares(self):
        raw=self.frame.copy();raw['Profit']=raw['Profit'].abs()
        self.assertTrue(loss_concentration(raw)['Loss_Share'].eq(0).all())
        self.assertTrue(category_region_losses(raw)['Loss_Share'].eq(0).all())
        self.assertTrue(loss_pool_overlap(raw).empty)

    def test_pool_and_product_shares_reconcile(self):
        pools=category_region_losses(self.frame)
        self.assertEqual(pools.Gross_Loss.sum(),30)
        self.assertEqual(pools.Loss_Share.sum(),100)
        overlap=loss_pool_overlap(self.frame,top_n=1)
        self.assertEqual(overlap.Top_Product_Loss.sum(),30)
        self.assertTrue(overlap.Top_Product_Loss.le(overlap.Gross_Loss).all())
        self.assertEqual(loss_pool_products(self.frame).Gross_Loss.sum(),30)

    def test_discount_groups_include_zero_separately(self):
        result=discount_profile(self.frame)
        self.assertEqual(result.loc['Loss','Order_Lines'],1)
        self.assertEqual(result.loc['Loss','Average_Discount'],50)
        self.assertEqual(result.loc['Break-even','Order_Lines'],1)
        self.assertAlmostEqual(result.loc['Positive profit','Average_Discount'],12.5)

    def test_calendar_means_use_monthly_totals_not_rows(self):
        result=calendar_month_performance(self.frame)
        self.assertEqual(result.loc['January','Years_Observed'],2)
        self.assertEqual(result.loc['January','Mean_Monthly_Sales'],1050)
        self.assertEqual(len(time_performance(self.frame)),2)
        self.assertEqual(len(time_performance(self.frame,'Y')),2)

    def test_missing_month_is_not_silently_zero_filled(self):
        raw=self.frame.copy();raw.loc[raw['Order Date'].dt.year.eq(2017),'Order Date']=pd.Timestamp('2017-03-01')
        result=time_performance(raw)
        self.assertNotIn(pd.Period('2017-02'),result.index)
        cal=calendar_month_performance(raw)
        self.assertEqual(cal.loc['January','Years_Observed'],1)

    def test_bridge_exactly_reconciles_margin_change(self):
        bridge=margin_bridge(self.frame)
        metrics=time_performance(self.frame,'Y')
        delta=metrics.Profit_Margin.iloc[1]-metrics.Profit_Margin.iloc[0]
        self.assertAlmostEqual(bridge[['Mix_Contribution_pp','Within_Contribution_pp']].to_numpy().sum(),delta)

    def test_bridge_pure_mix_case(self):
        raw=self.frame.iloc[[0,2,3,4]].copy()
        raw['Sales']=[1000,1000,1500,500];raw['Profit']=[100,400,150,200]
        bridge=margin_bridge(raw)
        self.assertAlmostEqual(bridge.Mix_Contribution_pp.sum(),-7.5)
        self.assertAlmostEqual(bridge.Within_Contribution_pp.sum(),0)

    def test_invalid_parameters_rejected(self):
        for call in [lambda:loss_concentration(self.frame,(0,)),lambda:loss_pool_overlap(self.frame,0),
                     lambda:segment_performance(self.frame,[]),lambda:time_performance(self.frame,'Q'),
                     lambda:margin_bridge(self.frame,start=2016,end=2016),
                     lambda:margin_bridge(self.frame,start=2014,end=2017)]:
            with self.assertRaises(ValueError):call()

    def test_functions_do_not_mutate_input(self):
        original=self.frame.copy(deep=True)
        for call in [overall_metrics,discount_profile,product_gross_losses,loss_concentration,
                     category_region_losses,time_performance,calendar_month_performance,loss_pool_overlap,margin_bridge]:
            call(self.frame)
        assert_frame_equal(original,self.frame)

class CommandTests(unittest.TestCase):
    def test_exact_decimal_legacy_summary_and_optional_profit(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'tiny.csv';path.write_text('Sales\n0.1\n0.2\n',encoding='utf-8')
            result=summarize_csv(path)
            self.assertEqual(result['sales'],Decimal('0.3'));self.assertIsNone(result['profit'])

    def test_summary_invalid_inputs(self):
        for data in ['Other\n1\n','Sales\nNaN\n','Sales\nInfinity\n','Sales\n','Sales\nwrong\n']:
            with tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/'bad.csv';path.write_text(data,encoding='utf-8')
                with self.subTest(data=data),self.assertRaises(ValueError):summarize_csv(path)

    def test_all_report_json_contracts(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'sample.csv';fixture().to_csv(path,index=False)
            for report in ['summary','category','region','losses','time','diagnostics']:
                output=io.StringIO()
                with contextlib.redirect_stdout(output):status=main(['--data',str(path),'--report',report,'--json'])
                with self.subTest(report=report):
                    self.assertEqual(status,0)
                    payload=json.loads(output.getvalue());self.assertEqual(payload['report'],report)
                    self.assertIn('units',payload)
                    if report!='summary':self.assertEqual(payload['scope']['rows'],6)

    def test_missing_file_returns_failure(self):
        with tempfile.TemporaryDirectory() as directory,contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['--data',str(Path(directory)/'missing.csv')]),1)

if __name__=='__main__':unittest.main()
