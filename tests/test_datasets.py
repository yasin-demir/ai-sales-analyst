"""Managed uploads and prepared snapshots: lifecycle, provenance and cache isolation."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from io import BytesIO, StringIO
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd
from pydantic import ValidationError

from src import datasets
from src.dataset_contracts import ReaderOptions
from src.datasets import (DatasetNotFound, DatasetService, MappingNotApproved,
                          PreparationVersionMismatch, SourceIntegrityError)
from src.importing import ImportConfig
from src.question_io import answer_file_question
from src.questions import run_question


CSV = (b'date,sales,alternative_sales,profit,category,region,product_id,product,order_id,discount\n'
       b'2026-01-01,100,1000,20,A,East,P1,One,O1,0.1\n'
       b'2026-01-31,200,2000,-40,B,East,P2,Two,O2,0.4\n'
       b'2026-02-01,600,6000,60,A,West,P1,One,O3,0.1\n'
       b'2026-02-28,0,0,-10,B,West,P3,Three,O4,0.5\n'
       b'2026-04-01,-100,-1000,-5,B,West,P3,Three,O5,0.5\n')
WINDOWS = {'baseline_start': '2026-01-01', 'baseline_end': '2026-01-31',
           'current_start': '2026-02-01', 'current_end': '2026-02-28'}
REQUESTS = [{'analysis': 'summary'}, {'analysis': 'segment_performance', 'by': 'region'},
            {'analysis': 'period_comparison', 'comparison': WINDOWS}, {'analysis': 'monthly_trend'},
            {'analysis': 'loss_concentration', 'limit': 1}, {'analysis': 'discount_comparison'}]


def config(**changes):
    values = {'mapping': {name: name for name in ('date', 'sales', 'profit', 'category', 'region',
                                                 'product_id', 'product', 'order_id', 'discount')},
              'mapping_reviewed': True, 'allow_returns': True, 'currency': 'TRY'}
    values.update(changes)
    return ImportConfig(**values)


def sales(answer):
    return answer.result.answer.results[0].metrics.sales


class DatasetTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.store = self.root / 'store'
        self.service = DatasetService(self.store)
        self.path = self.root / 'sales.csv'
        self.path.write_bytes(CSV)

    def upload(self):
        return self.service.upload_path(self.path)

    def prepared(self):
        uploaded = self.upload()
        self.service.approve_mapping(uploaded.dataset_id, config())
        return self.service.prepare(uploaded.dataset_id)

    def source_path(self, dataset_id, suffix='.csv'):
        return self.store / dataset_id / ('source' + suffix)

    def test_upload_copies_hashes_without_parsing_or_preparing(self):
        with patch('src.datasets.read_input') as read, patch('src.datasets.prepare') as prepare:
            uploaded = self.upload()
            read.assert_not_called()
            prepare.assert_not_called()
        self.assertEqual(uploaded.status, 'uploaded')
        self.assertEqual(uploaded.source.sha256, hashlib.sha256(CSV).hexdigest())
        self.assertEqual(uploaded.source.size_bytes, len(CSV))
        managed = self.source_path(uploaded.dataset_id)
        self.assertEqual(managed.read_bytes(), CSV)
        self.assertFalse(managed.stat().st_mode & stat.S_IWUSR)
        self.assertNotIn(str(self.store), uploaded.model_dump_json())

    def test_original_changes_and_deletion_do_not_change_managed_source(self):
        uploaded = self.upload()
        self.path.write_bytes(b'sales\n999999\n')
        self.path.unlink()
        self.service.approve_mapping(uploaded.dataset_id, config())
        prepared = self.service.prepare(uploaded.dataset_id)
        self.assertEqual(sales(self.service.query(prepared.preparation_id, REQUESTS[0])), 800)
        self.assertEqual(self.source_path(uploaded.dataset_id).read_bytes(), CSV)

    def test_same_name_and_same_content_never_overwrite_another_upload(self):
        first, second = self.upload(), self.upload()
        third = self.service.upload(BytesIO(b'sales\n9\n'), filename='sales.csv')
        self.assertEqual(len({first.dataset_id, second.dataset_id, third.dataset_id}), 3)
        self.assertEqual(first.source.sha256, second.source.sha256)
        self.assertNotEqual(first.source.sha256, third.source.sha256)
        self.assertEqual(self.source_path(first.dataset_id).read_bytes(), CSV)

    def test_streaming_hash_uses_exact_received_bytes_and_caller_keeps_stream(self):
        content = b'sales\n' + b'1\n' * (1024 * 1024)
        class SmallChunks(BytesIO):
            def read(self, size=-1):
                return super().read(min(size, 8192))
        stream = SmallChunks(content)
        uploaded = self.service.upload(stream, filename='large.csv')
        self.assertFalse(stream.closed)
        self.assertEqual(uploaded.source.sha256, hashlib.sha256(content).hexdigest())
        self.assertEqual(uploaded.source.size_bytes, len(content))

    def test_upload_names_cannot_control_storage_paths(self):
        uploaded = self.service.upload(BytesIO(CSV), filename='../../outside.csv')
        self.assertEqual(uploaded.source.file, 'outside.csv')
        self.assertTrue(self.source_path(uploaded.dataset_id).exists())
        self.assertFalse((self.root / 'outside.csv').exists())
        for filename in ('', 'file.xls', 'file.exe', 'x\x00.csv'):
            with self.subTest(filename=filename), self.assertRaises(ValueError):
                self.service.upload(BytesIO(CSV), filename=filename)

    def test_failed_upload_is_not_published_and_leaves_no_partial_files(self):
        class BrokenStream:
            calls = 0
            def read(self, _):
                self.calls += 1
                if self.calls == 1:
                    return b'partial'
                raise OSError('interrupted upload')
        for stream in (BytesIO(), StringIO('sales\n1\n'), BrokenStream()):
            with self.subTest(stream=type(stream).__name__), self.assertRaises((ValueError, OSError)):
                self.service.upload(stream, filename='sales.csv')
            self.assertEqual(list(self.store.iterdir()), [])

    def test_atomic_metadata_failure_cleans_an_unpublished_upload(self):
        replace = os.replace
        def fail_metadata(source, target):
            if Path(target).name == 'metadata.json':
                raise OSError('metadata publication failed')
            return replace(source, target)
        with patch('src.datasets.os.replace', side_effect=fail_metadata):
            with self.assertRaises(OSError):
                self.upload()
        self.assertEqual(list(self.store.iterdir()), [])

    def test_preview_approval_and_prepare_are_separate_and_reuse_raw_read(self):
        uploaded = self.upload()
        with patch('src.datasets.read_input', wraps=datasets.read_input) as read, \
                patch('src.datasets.prepare', wraps=datasets.prepare) as prepare:
            preview = self.service.preview(uploaded.dataset_id, rows=2)
            self.assertEqual(len(preview.preview), 2)
            self.assertEqual(preview.rows, 5)
            self.assertTrue(preview.requires_review)
            self.assertEqual(self.service.get_dataset(uploaded.dataset_id).status, 'uploaded')
            self.assertEqual(prepare.call_count, 0)
            with self.assertRaises(MappingNotApproved):
                self.service.prepare(uploaded.dataset_id)
            mapping = self.service.approve_mapping(uploaded.dataset_id, config())
            self.assertEqual(mapping.revision, 1)
            self.assertEqual(self.service.get_dataset(uploaded.dataset_id).status, 'mapped')
            self.assertEqual(prepare.call_count, 0)
            prepared = self.service.prepare(uploaded.dataset_id)
            self.assertEqual(prepared.quality.status, 'ready')
            self.assertEqual(read.call_count, 1)
            self.assertEqual(prepare.call_count, 1)
        self.assertEqual(self.service.get_dataset(uploaded.dataset_id).status, 'ready')

    def test_suggestions_never_approve_a_mapping(self):
        uploaded = self.upload()
        preview = self.service.preview(uploaded.dataset_id)
        for values in ({'mapping': preview.suggestions},
                       {'mapping': preview.suggestions, 'mapping_reviewed': 'true'},
                       {'mapping': preview.suggestions, 'mapping_reviewed': True, 'extra': 1}):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                self.service.approve_mapping(uploaded.dataset_id, values)
        with self.assertRaises(MappingNotApproved):
            self.service.prepare(uploaded.dataset_id)

    def test_mapping_must_refer_to_this_uploads_columns(self):
        uploaded = self.upload()
        with self.assertRaises(ValueError):
            self.service.approve_mapping(uploaded.dataset_id, {'mapping': {'sales': 'absent'}, 'mapping_reviewed': True})
        self.assertEqual(self.service.get_dataset(uploaded.dataset_id).status, 'uploaded')

    def test_preview_options_and_limit_are_validated(self):
        uploaded = self.upload()
        for rows in (0, 51, True, '5'):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                self.service.preview(uploaded.dataset_id, rows=rows)
        with self.assertRaises(ValidationError):
            self.service.preview(uploaded.dataset_id, options={'encoding': 1})
        self.assertEqual(self.service.preview(uploaded.dataset_id, options=ReaderOptions()).rows, 5)

    def test_reader_options_are_part_of_raw_cache_identity(self):
        uploaded = self.service.upload(BytesIO(b'date;sales\n2026-01-01;1,5\n'), filename='local.csv')
        with patch('src.datasets.read_input', wraps=datasets.read_input) as read:
            with self.assertRaises(ValueError):
                self.service.preview(uploaded.dataset_id)
            second = self.service.preview(uploaded.dataset_id, options={'delimiter': ';'})
            self.assertEqual(second.columns, ['date', 'sales'])
            self.service.approve_mapping(uploaded.dataset_id, {'mapping': {'date': 'date', 'sales': 'sales'},
                'mapping_reviewed': True, 'delimiter': ';', 'decimal': ',', 'currency': 'TRY'})
            prepared = self.service.prepare(uploaded.dataset_id)
            self.assertEqual(read.call_count, 2)
        self.assertEqual(sales(self.service.query(prepared.preparation_id, REQUESTS[0])), 1.5)

    def test_distinct_valid_reader_settings_use_separate_raw_cache_entries(self):
        uploaded = self.upload()
        with patch('src.datasets.read_input', wraps=datasets.read_input) as read, \
                patch('src.datasets.prepare', wraps=datasets.prepare) as prepare:
            self.service.preview(uploaded.dataset_id)
            self.service.preview(uploaded.dataset_id, options={'encoding': 'utf-8'})
            self.service.preview(uploaded.dataset_id)
            self.service.approve_mapping(uploaded.dataset_id, config())
            first = self.service.prepare(uploaded.dataset_id)
            self.service.approve_mapping(uploaded.dataset_id, config(encoding='utf-8'))
            second = self.service.prepare(uploaded.dataset_id)
            self.assertEqual(read.call_count, 2)
            self.assertEqual(prepare.call_count, 2)
        self.assertEqual(first.source.sha256, second.source.sha256)
        self.assertNotEqual(first.config_sha256, second.config_sha256)

    def test_repeated_prepare_and_identical_approval_are_idempotent(self):
        uploaded = self.upload()
        mapping = self.service.approve_mapping(uploaded.dataset_id, config())
        prepared = self.service.prepare(uploaded.dataset_id)
        same = config()
        same.mapping = dict(reversed(list(same.mapping.items())))
        with patch('src.datasets.read_input', side_effect=AssertionError('Unexpected read')), \
                patch('src.datasets.prepare', side_effect=AssertionError('Unexpected normalization')):
            self.assertEqual(self.service.approve_mapping(uploaded.dataset_id, same).revision, mapping.revision)
            self.assertEqual(self.service.prepare(uploaded.dataset_id), prepared)
        self.assertEqual(len(self.service.list_preparations(uploaded.dataset_id)), 1)

    def test_every_analysis_matches_legacy_file_answer(self):
        prepared = self.prepared()
        for request in REQUESTS:
            with self.subTest(analysis=request['analysis']):
                answer = self.service.query(prepared.preparation_id, request)
                legacy = answer_file_question(self.path, config(), request)
                self.assertEqual(answer.result.model_dump(mode='json', exclude_unset=True), legacy)

    def test_warm_queries_do_not_read_prepare_hash_or_open_files(self):
        prepared = self.prepared()
        with patch('src.datasets.read_input', side_effect=AssertionError('Unexpected read')), \
                patch('src.datasets.prepare', side_effect=AssertionError('Unexpected normalization')), \
                patch('src.datasets.hashlib.sha256', side_effect=AssertionError('Unexpected fingerprint')), \
                patch.object(Path, 'open', side_effect=AssertionError('Unexpected file open')):
            for request in REQUESTS * 2:
                self.assertEqual(self.service.query(prepared.preparation_id, request).result.status, 'ok')

    def test_new_mapping_creates_new_preparation_and_preserves_old_results(self):
        first = self.prepared()
        changed = config()
        changed.mapping['sales'] = 'alternative_sales'
        approved = self.service.approve_mapping(first.dataset_id, changed)
        self.assertEqual(approved.revision, 2)
        self.assertEqual(self.service.get_dataset(first.dataset_id).status, 'mapped')
        second = self.service.prepare(first.dataset_id)
        self.assertEqual(second.revision, 2)
        self.assertEqual(second.mapping_revision, 2)
        self.assertNotEqual(first.preparation_id, second.preparation_id)
        self.assertNotEqual(first.config_sha256, second.config_sha256)
        self.assertNotEqual(first.preparation_key, second.preparation_key)
        self.assertEqual(first.source.sha256, second.source.sha256)
        self.assertEqual(sales(self.service.query(first.preparation_id, REQUESTS[0])), 800)
        self.assertEqual(sales(self.service.query(second.preparation_id, REQUESTS[0])), 8000)

    def test_returning_to_old_settings_creates_revision_not_overwrite(self):
        first = self.prepared()
        self.service.approve_mapping(first.dataset_id, config(currency='USD'))
        second = self.service.prepare(first.dataset_id)
        self.service.approve_mapping(first.dataset_id, config())
        third = self.service.prepare(first.dataset_id)
        self.assertEqual([p.revision for p in self.service.list_preparations(first.dataset_id)], [1, 2, 3])
        self.assertNotEqual(third.preparation_id, first.preparation_id)
        self.assertEqual(third.preparation_key, first.preparation_key)
        self.assertNotEqual(second.preparation_key, first.preparation_key)
        self.assertEqual(self.service.prepare(first.dataset_id, mapping_revision=1), first)

    def test_locale_and_policy_changes_also_create_new_versions(self):
        first = self.prepared()
        changed = config(date_format='%d/%m/%Y')
        self.service.approve_mapping(first.dataset_id, changed)
        second = self.service.prepare(first.dataset_id)
        self.assertEqual(second.quality.status, 'needs_attention')
        self.assertEqual(second.mapping_revision, 2)
        self.assertEqual(sales(self.service.query(first.preparation_id, REQUESTS[0])), 800)
        self.assertEqual(self.service.query(second.preparation_id, REQUESTS[0]).result.status, 'needs_attention')

    def test_preview_config_info_and_answer_mutations_cannot_change_cache(self):
        uploaded = self.upload()
        preview = self.service.preview(uploaded.dataset_id)
        preview.preview[0]['sales'] = '999999'
        settings = config()
        mapping = self.service.approve_mapping(uploaded.dataset_id, settings)
        settings.mapping['sales'] = 'alternative_sales'
        mapping.config.mapping['sales'] = 'alternative_sales'
        prepared = self.service.prepare(uploaded.dataset_id)
        prepared.quality.issues.append({'injected': True})
        answer = self.service.query(prepared.preparation_id, REQUESTS[0])
        self.assertEqual(sales(answer), 800)
        answer.result.answer.results[0].metrics.sales = 999999
        answer.result.import_config.mapping['sales'] = 'alternative_sales'
        answer.result.quality.issues.clear()
        self.assertEqual(sales(self.service.query(prepared.preparation_id, REQUESTS[0])), 800)
        self.assertEqual(self.service.get_mapping(uploaded.dataset_id).config.mapping['sales'], 'sales')

    def test_faulty_query_handler_cannot_mutate_the_prepared_frame(self):
        prepared = self.prepared()
        def mutating_handler(frame, **kwargs):
            frame.loc[:, 'sales'] = 0
            return run_question(frame, **kwargs)
        with patch('src.question_io.run_question', side_effect=mutating_handler):
            self.assertEqual(sales(self.service.query(prepared.preparation_id, REQUESTS[0])), 0)
        self.assertEqual(sales(self.service.query(prepared.preparation_id, REQUESTS[0])), 800)

    def test_concurrent_queries_use_independent_frames(self):
        prepared = self.prepared()
        with ThreadPoolExecutor(max_workers=4) as pool:
            answers = list(pool.map(lambda _: self.service.query(prepared.preparation_id, REQUESTS[0]), range(12)))
        self.assertEqual([sales(answer) for answer in answers], [800] * 12)

    def test_concurrent_preparation_publishes_only_one_revision(self):
        uploaded = self.upload()
        self.service.approve_mapping(uploaded.dataset_id, config())
        with patch('src.datasets.prepare', wraps=datasets.prepare) as prepare:
            with ThreadPoolExecutor(max_workers=4) as pool:
                snapshots = list(pool.map(lambda _: self.service.prepare(uploaded.dataset_id), range(8)))
            self.assertEqual(prepare.call_count, 1)
        self.assertEqual(len({snapshot.preparation_id for snapshot in snapshots}), 1)
        self.assertEqual(len(self.service.list_preparations(uploaded.dataset_id)), 1)

    def test_mapping_revision_types_and_missing_revisions_are_controlled(self):
        uploaded = self.upload()
        self.service.approve_mapping(uploaded.dataset_id, config())
        for revision in (False, '1', [], {}, 0, -1, 99):
            with self.subTest(revision=revision), self.assertRaises(MappingNotApproved):
                self.service.prepare(uploaded.dataset_id, mapping_revision=revision)

    def test_unknown_and_invalid_identifiers_are_controlled(self):
        for value in ([], {}, None, '../source.csv', 'not-an-id'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.service.get_dataset(value)
        with self.assertRaises(DatasetNotFound):
            self.service.get_dataset('0' * 32)
        with self.assertRaises(DatasetNotFound):
            self.service.query('0' * 32, REQUESTS[0])
        with self.assertRaises(ValidationError):
            self.service.query('0' * 32, {'analysis': []})

    def test_invalid_requests_are_rejected_before_cold_cache_restore(self):
        prepared = self.prepared()
        restored = DatasetService(self.store)
        with patch('src.datasets.read_input') as read, patch('src.datasets.prepare') as prepare:
            with self.assertRaises(ValidationError):
                restored.query(prepared.preparation_id, {'analysis': 'segment_performance', 'by': 'region', 'sort_by': []})
            read.assert_not_called()
            prepare.assert_not_called()

    def test_restart_preserves_metadata_and_restores_frame_once(self):
        prepared = self.prepared()
        restored = DatasetService(self.store)
        self.assertEqual(restored.list_preparations(prepared.dataset_id), [prepared])
        with patch('src.datasets.read_input', wraps=datasets.read_input) as read, \
                patch('src.datasets.prepare', wraps=datasets.prepare) as prepare:
            self.assertEqual(sales(restored.query(prepared.preparation_id, REQUESTS[0])), 800)
            for request in REQUESTS:
                restored.query(prepared.preparation_id, request)
            self.assertEqual(read.call_count, 1)
            self.assertEqual(prepare.call_count, 1)

    def test_importer_version_change_requires_a_new_preparation(self):
        first = self.prepared()
        upgraded = DatasetService(self.store, preparer_version='sales-import-v2')
        self.assertEqual(upgraded.get_dataset(first.dataset_id).status, 'mapped')
        with self.assertRaises(PreparationVersionMismatch):
            upgraded.query(first.preparation_id, REQUESTS[0])
        second = upgraded.prepare(first.dataset_id)
        self.assertEqual(second.revision, 2)
        self.assertEqual(second.mapping_revision, 1)
        self.assertNotEqual(first.preparation_key, second.preparation_key)
        self.assertEqual(sales(upgraded.query(second.preparation_id, REQUESTS[0])), 800)

    def test_saved_mapping_versions_stay_queryable_after_restart(self):
        first = self.prepared()
        changed = config()
        changed.mapping['sales'] = 'alternative_sales'
        self.service.approve_mapping(first.dataset_id, changed)
        second = self.service.prepare(first.dataset_id)
        restored = DatasetService(self.store)
        self.assertEqual(sales(restored.query(first.preparation_id, REQUESTS[0])), 800)
        self.assertEqual(sales(restored.query(second.preparation_id, REQUESTS[0])), 8000)

    def test_quality_only_and_exclusion_versions_preserve_provenance(self):
        uploaded = self.service.upload(BytesIO(b'sales,profit\n100,10\nbad,2\n'), filename='bad.csv')
        values = {'mapping': {'sales': 'sales', 'profit': 'profit'}, 'mapping_reviewed': True, 'currency': 'TRY'}
        self.service.approve_mapping(uploaded.dataset_id, values)
        first = self.service.prepare(uploaded.dataset_id)
        self.assertEqual(first.quality.status, 'needs_attention')
        result = self.service.query(first.preparation_id, REQUESTS[0])
        self.assertIsNone(result.result.answer)
        self.assertEqual(self.service.get_dataset(uploaded.dataset_id).status, 'needs_attention')
        self.service.approve_mapping(uploaded.dataset_id, {**values, 'invalid_rows': 'exclude'})
        second = self.service.prepare(uploaded.dataset_id)
        self.assertEqual(second.quality.excluded_records, [2])
        result = self.service.query(second.preparation_id, REQUESTS[0])
        self.assertEqual(sales(result), 100)
        self.assertEqual(result.result.answer.results[0].scope.raw_input_records, 2)
        self.assertEqual(result.result.answer.results[0].scope.explicitly_excluded_records, 1)

    def test_failed_preparation_or_metadata_write_does_not_publish_revision(self):
        uploaded = self.upload()
        self.service.approve_mapping(uploaded.dataset_id, config())
        with patch('src.datasets.prepare', side_effect=ValueError('preparation failed')):
            with self.assertRaises(ValueError):
                self.service.prepare(uploaded.dataset_id)
        self.assertEqual(self.service.list_preparations(uploaded.dataset_id), [])
        with patch.object(self.service, '_persist', side_effect=OSError('disk failed')):
            with self.assertRaises(OSError):
                self.service.prepare(uploaded.dataset_id)
        self.assertEqual(self.service.list_preparations(uploaded.dataset_id), [])
        self.assertEqual(self.service.prepare(uploaded.dataset_id).revision, 1)

    def test_failed_approval_preserves_previous_mapping_on_disk(self):
        prepared = self.prepared()
        with patch.object(self.service, '_persist', side_effect=OSError('disk failed')):
            with self.assertRaises(OSError):
                self.service.approve_mapping(prepared.dataset_id, config(currency='USD'))
        self.assertEqual(self.service.get_mapping(prepared.dataset_id).revision, 1)
        restored = DatasetService(self.store)
        self.assertEqual(restored.get_mapping(prepared.dataset_id).revision, 1)

    def test_failed_atomic_metadata_replace_keeps_old_manifest(self):
        prepared = self.prepared()
        metadata = self.store / prepared.dataset_id / 'metadata.json'
        original = metadata.read_bytes()
        with patch('src.datasets.os.replace', side_effect=OSError('replace failed')):
            with self.assertRaises(OSError):
                self.service.approve_mapping(prepared.dataset_id, config(currency='USD'))
        self.assertEqual(metadata.read_bytes(), original)
        self.assertEqual(list(metadata.parent.glob('.metadata-*.tmp')), [])
        self.assertEqual(self.service.get_mapping(prepared.dataset_id).revision, 1)

    def test_changed_managed_source_is_rejected_before_new_read(self):
        uploaded = self.upload()
        source = self.source_path(uploaded.dataset_id)
        previous = source.stat()
        os.utime(source, ns=(previous.st_atime_ns, previous.st_mtime_ns + 1000000000))
        with patch('src.datasets.read_input') as read:
            with self.assertRaises(SourceIntegrityError):
                self.service.preview(uploaded.dataset_id)
            read.assert_not_called()

    def test_corrupt_mapping_metadata_is_not_loaded(self):
        prepared = self.prepared()
        metadata = self.store / prepared.dataset_id / 'metadata.json'
        value = json.loads(metadata.read_text(encoding='utf-8'))
        value['mappings'][0]['config_sha256'] = '0' * 64
        metadata.write_text(json.dumps(value), encoding='utf-8')
        with self.assertRaises(ValueError):
            DatasetService(self.store)

    def test_cold_restore_rejects_different_quality_without_poisoning_cache(self):
        prepared = self.prepared()
        restored = DatasetService(self.store)
        original_prepare = datasets.prepare
        def changed_quality(raw, settings):
            result = original_prepare(raw, settings)
            result['issues'].append({'severity': 'warning', 'field': 'records', 'data_record': None, 'message': 'Changed quality'})
            return result
        with patch('src.datasets.prepare', side_effect=changed_quality):
            with self.assertRaises(SourceIntegrityError):
                restored.query(prepared.preparation_id, REQUESTS[0])
        self.assertEqual(sales(restored.query(prepared.preparation_id, REQUESTS[0])), 800)

    def test_excel_native_cells_sheets_and_json_safe_preview(self):
        path = self.root / 'native.xlsx'
        with pd.ExcelWriter(path, engine='openpyxl') as writer:
            pd.DataFrame({'date': [pd.Timestamp('2026-01-01')], 'sales': [100]}).to_excel(writer, index=False, sheet_name='First')
            pd.DataFrame({'date': pd.to_datetime(['2026-01-01', '2026-01-02']), 'sales': [1.25, 2.5]}).to_excel(writer, index=False, sheet_name='Second')
        uploaded = self.service.upload_path(path)
        preview = self.service.preview(uploaded.dataset_id, options={'sheet': 'Second'})
        self.assertEqual(preview.preview[0]['sales'], 1.25)
        self.assertTrue(preview.preview[0]['date'].startswith('2026-01-01'))
        json.loads(preview.model_dump_json())
        self.service.approve_mapping(uploaded.dataset_id, {'mapping': {'date': 'date', 'sales': 'sales'},
            'mapping_reviewed': True, 'sheet': 'Second', 'decimal': ',', 'currency': 'TRY'})
        prepared = self.service.prepare(uploaded.dataset_id)
        self.assertEqual(sales(self.service.query(prepared.preparation_id, REQUESTS[0])), 3.75)


if __name__ == '__main__':
    unittest.main()
