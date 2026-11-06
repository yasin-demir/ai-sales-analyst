"""Offline local-provider contract checks, before connecting it to analytics."""
import json
import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.intent_ollama import interpret_question, _send
from src.sales_assistant import READY


def response(decision=READY):
    return {'done': True, 'done_reason': 'stop', 'message': {'content': json.dumps({'decision': decision})}}


class OllamaTests(unittest.TestCase):
    def test_local_cli_reaches_analytics_without_key_or_cloud_fallback(self):
        from src.cli import main
        examples = Path(__file__).resolve().parents[1] / 'examples'
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True), \
                patch('src.intent_ollama._send', return_value=response()) as local, \
                patch('src.intent_ai._send') as cloud, contextlib.redirect_stdout(io.StringIO()) as stdout:
            output = Path(directory) / 'answer.json'
            code = main(['ask-ai', str(examples / 'completed_months_sales.csv'),
                         '--config', str(examples / 'completed_months_mapping.json'),
                         '--reference-date', '2026-11-06', '--provider', 'ollama',
                         '--question', 'Son iki tamamlanmış ayı karşılaştır', '--output', str(output)])
            self.assertEqual(code, 0)
            self.assertIn('%20,00', stdout.getvalue())
            self.assertIn('%25,00', stdout.getvalue())
            self.assertEqual(json.loads(output.read_text(encoding='utf-8'))['status'], 'ok')
            local.assert_called_once()
            cloud.assert_not_called()

    def test_without_api_key_and_bounded_memory_payload(self):
        calls = []
        with patch.dict(os.environ, {}, clear=True):
            result = interpret_question('Son iki tamamlanmış ayı karşılaştır',
                                        transport=lambda payload: calls.append(payload) or response())
        self.assertEqual(result, READY)
        self.assertEqual(calls[0]['model'], 'qwen3:4b')
        self.assertFalse(calls[0]['think'])
        self.assertFalse(calls[0]['stream'])
        self.assertEqual(calls[0]['options']['num_ctx'], 4096)
        self.assertEqual(calls[0]['format']['type'], 'object')

    def test_clarification_and_unsupported_are_preserved(self):
        for decision in ({'status': 'needs_clarification', 'question': 'Hangi dönem?'},
                         {'status': 'unsupported', 'message': 'Bu kapsam desteklenmiyor.'}):
            self.assertEqual(interpret_question('Soru', transport=lambda *_: response(decision)), decision)

    def test_invalid_and_truncated_outputs_fail_closed(self):
        for bad in ({}, {'done': False}, {'done': True, 'done_reason': 'length'},
                    {'done': True, 'message': {'content': 'not json'}},
                    response({**READY, 'filters': {}}),
                    {'done': True, 'message': {'content': '[]'}}):
            with self.subTest(response=bad), self.assertRaises(ValueError):
                interpret_question('Soru', transport=lambda *_: bad)

    def test_invalid_input_and_cloud_model_blocked_before_transport(self):
        for question, model in [('', 'qwen3:4b'), ('x' * 4001, 'qwen3:4b'),
                                ('Soru', 'model:cloud'), ('Soru', 'model-cloud'), ('Soru', '')]:
            with self.subTest(model=model), self.assertRaises(ValueError):
                interpret_question(question, model=model, transport=lambda *_: self.fail('Unexpected network call'))

    def test_http_destination_is_loopback_without_authentication_or_proxy(self):
        with patch('src.intent_ollama.build_opener') as factory:
            factory.return_value.open.return_value.__enter__.return_value.read.return_value = b'{}'
            self.assertEqual(_send({'model': 'qwen3:4b'}), {})
            request = factory.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url, 'http://127.0.0.1:11434/api/chat')
            self.assertIsNone(request.get_header('Authorization'))
            self.assertEqual(factory.call_args.args[0].proxies, {})


if __name__ == '__main__':
    unittest.main()
