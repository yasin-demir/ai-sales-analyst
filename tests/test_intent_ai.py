"""Offline provider-contract tests; these do not measure real-model interpretation."""
import contextlib
from datetime import date
import io
import json
import os
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from src.cli import main
from src.importing import ImportConfig
from src.intent_ai import interpret_question, answer_natural_language, _send
from src.sales_assistant import READY


def response(decision=READY):
    return {'status': 'completed', 'output': [{'type': 'message', 'content': [
        {'type': 'output_text', 'text': json.dumps({'decision': decision})}]}]}


class AITests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {'OPENAI_API_KEY': 'test-key-not-real', 'OPENAI_MODEL': 'test-model'})
        env.start()
        self.addCleanup(env.stop)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        examples = Path(__file__).resolve().parents[1] / 'examples'
        self.path = self.root / 'data.csv'
        self.path.write_bytes((examples / 'completed_months_sales.csv').read_bytes())
        self.config = ImportConfig.load(examples / 'completed_months_mapping.json')
        self.config_path = self.root / 'config.json'
        self.config.save(self.config_path)

    def test_ready_uses_existing_engine_and_only_question_goes_to_provider(self):
        calls = []
        def transport(payload, key):
            calls.append(payload)
            self.assertEqual(key, 'test-key-not-real')
            return response()
        result = answer_natural_language(self.path, self.config,
            'Son iki tamamlanmış ayda satış ve kâr nasıl değişti?', date(2026, 11, 6), transport=transport)
        self.assertEqual(result['status'], 'ok')
        self.assertIn('%20,00', result['text'])
        self.assertIn('%25,00', result['text'])
        self.assertNotIn('999999', result['text'])
        self.assertTrue(calls[0]['text']['format']['strict'])
        self.assertFalse(calls[0]['store'])
        sent = json.dumps(calls[0])
        self.assertNotIn('40000', sent)
        self.assertNotIn(str(self.path), sent)
        self.assertNotIn('test-key-not-real', sent)

    def test_clarification_and_unsupported_do_not_call_analysis(self):
        for decision in ({'status': 'needs_clarification', 'question': 'Hangi dönem?'},
                         {'status': 'unsupported', 'message': 'Bu kapsam desteklenmiyor.'}):
            with patch('src.sales_assistant.answer_file_question') as engine:
                result = answer_natural_language('missing.csv', self.config, 'Soru', date(2026, 11, 6),
                                                 transport=lambda *_: response(decision))
                engine.assert_not_called()
                self.assertEqual(result['status'], decision['status'])

    def test_incomplete_refusal_malformed_and_injected_responses_fail_closed(self):
        bad = [response({**READY, 'comparison': {}}), {'status': 'incomplete'},
               {'status': 'completed', 'output': [{'content': [{'type': 'refusal'}]}]},
               {'status': 'completed', 'output': [{'content': [{'type': 'output_text', 'text': 'not json'}]}]},
               {'status': 'completed', 'output': None},
               {'status': 'completed', 'output': [{'content': [{'type': 'output_text', 'text': None}]}]}]
        for value in bad:
            with self.subTest(value=value), patch('src.sales_assistant.answer_file_question') as engine:
                with self.assertRaises(ValueError):
                    answer_natural_language(self.path, self.config, 'Soru', date(2026, 11, 6), transport=lambda *_: value)
                engine.assert_not_called()

    def test_credentials_model_and_question_validation_before_network(self):
        for key in ('OPENAI_API_KEY', 'OPENAI_MODEL'):
            with patch.dict(os.environ, {key: ''}), patch('src.intent_ai._send') as network:
                with self.assertRaises(ValueError):
                    interpret_question('Soru')
                network.assert_not_called()
        for question in ('', ' ' * 5, 'x' * 4001, None):
            with self.assertRaises(ValueError):
                interpret_question(question, transport=lambda *_: self.fail('Network must not run'))

    def test_provider_errors_do_not_leak_credentials_or_bodies(self):
        for error in (HTTPError('https://api.openai.com', 401, 'secret-body', {}, None), URLError('secret-body')):
            with patch('src.intent_ai.urlopen', side_effect=error):
                with self.assertRaises(RuntimeError) as caught:
                    _send({}, 'secret-api-key')
                self.assertNotIn('secret', str(caught.exception))

    def test_cli_without_ai_and_with_mocked_ai(self):
        for command in ('compare-months', 'ask-ai'):
            args = [command, str(self.path), '--config', str(self.config_path),
                    '--reference-date', '2026-11-06', '--output', str(self.root / 'answer.json')]
            if command == 'ask-ai':
                args += ['--question', 'Son iki tamamlanmış ayı karşılaştır']
            with patch('src.intent_ai._send', return_value=response()), contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(main(args), 0)
            self.assertIn('%25,00', out.getvalue())
            result = json.loads((self.root / 'answer.json').read_text(encoding='utf-8'))
            self.assertEqual(result['request']['comparison']['current_end'], '2026-10-31')

    def test_cli_protects_source_before_network(self):
        original = self.path.read_bytes()
        with patch('src.intent_ai._send') as network, contextlib.redirect_stdout(io.StringIO()):
            code = main(['ask-ai', str(self.path), '--config', str(self.config_path),
                         '--question', 'Soru', '--reference-date', '2026-11-06', '--output', str(self.path)])
            self.assertEqual(code, 1)
            network.assert_not_called()
        self.assertEqual(self.path.read_bytes(), original)

    def test_real_cli_supports_windows_turkish_output_encoding(self):
        result = subprocess.run([sys.executable, '-m', 'src.cli', 'compare-months', str(self.path),
                                 '--config', str(self.config_path), '--reference-date', '2026-11-06'],
                                env={**os.environ, 'PYTHONIOENCODING': 'cp1254'},
                                cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True, encoding='cp1254', timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('%25,00', result.stdout)


if __name__ == '__main__':
    unittest.main()
