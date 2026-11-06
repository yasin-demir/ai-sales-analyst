"""Local intent classification through Ollama; no key or cloud fallback."""
import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, ProxyHandler, build_opener

from .intent_ai import INSTRUCTIONS, DECISION_SCHEMA
from .sales_assistant import validate_decision, execute_decision


LOCAL_RULES = """\nTürkçe karar önceliği:
1. Kullanıcı dönemi açıkça belirtiyorsa ve bu dönem tam iki bitmiş takvim ayı
değilse unsupported seç. Özellikle 'bu ayın ilk üç günü', 'bugüne kadar',
'aynı gün sayısı' veya ayın bir bölümünü karşılaştırma isteği açık bir dönemdir;
desteklenmez ve açıklama sorusu gerektirmez.
2. Açık bir segment/kategori/bölge filtresi veya özel ay/yıl varsa unsupported.
3. Sadece hangi dönem olduğu belirsizse needs_clarification. Örneğin 'geçen
aya göre' ifadesi tek başına son iki bitmiş ayı mı yoksa kısmi ayları mı kastettiğini
belirtmez. Kullanıcının açıkça belirttiği dönemleri belirsiz sayma.
4. Yalnızca son iki bitmiş/tamamlanmış takvim ayının genel satış/kâr
karşılaştırması ready olabilir. Bu kurallar hesaplama veya veri onayı değildir.
"""


EXAMPLES = [
    ('En son tamamlanan ay ile ondan önceki tamamlanmış ayın genel satış ve kârını kıyasla.',
     {'status': 'ready', 'analysis': 'period_comparison', 'period_rule': 'last_two_complete_months'}),
    ('Önceki aya kıyasla kârım ne durumda?',
     {'status': 'needs_clarification', 'question': 'Son iki tamamlanmış ayı mı, bu ayın geçen kısmını mı karşılaştırmak istiyorsunuz?'}),
    ('Bu ayın ilk on günündeki satışları önceki ayın ilk on günüyle kıyasla.',
     {'status': 'unsupported', 'message': 'Kısmi ay karşılaştırması desteklenmiyor; yalnızca son iki tamamlanmış ay karşılaştırılabilir.'}),
    ('Ay başından beri yaptığım satışları bir önceki ayın aynı süresiyle kıyasla.',
     {'status': 'unsupported', 'message': 'Döneminiz açık fakat kısmi ayları kapsıyor. Bu sürüm yalnızca son iki tamamlanmış ayı destekler.'}),
]


def _send(payload):
    request = Request('http://127.0.0.1:11434/api/chat',
                      data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                      headers={'Content-Type': 'application/json'}, method='POST')
    try:
        # Loopback requests must not go through an externally configured proxy.
        with build_opener(ProxyHandler({})).open(request, timeout=120) as response:
            return json.loads(response.read().decode('utf-8'))
    except HTTPError as exc:
        status = exc.code
        exc.close()
        raise RuntimeError(f'Local Ollama request failed (HTTP {status}); check that the model is installed.') from None
    except (URLError, TimeoutError, OSError, ValueError):
        raise RuntimeError('Cannot reach local Ollama or response is invalid. Start Ollama; no cloud fallback is used.') from None


def interpret_question(question, *, model=None, transport=None):
    if not isinstance(question, str) or not 1 <= len(question.strip()) <= 4000:
        raise ValueError('Question must contain 1–4000 characters.')
    model = model if model is not None else os.environ.get('OLLAMA_MODEL', 'qwen3:4b')
    if not isinstance(model, str) or not model.strip() or 'cloud' in model.lower():
        raise ValueError('Use a nonempty local model name; cloud models are not supported here.')
    payload = {
        'model': model.strip(), 'stream': False, 'think': False,
        'messages': [
            {'role': 'system', 'content': INSTRUCTIONS + LOCAL_RULES + '\nJSON schema: ' + json.dumps(DECISION_SCHEMA)},
        ],
        'format': DECISION_SCHEMA,
        'options': {'temperature': 0, 'num_ctx': 4096, 'num_predict': 512},
    }
    for example_question, decision in EXAMPLES:
        payload['messages'].extend([
            {'role': 'user', 'content': example_question},
            {'role': 'assistant', 'content': json.dumps({'decision': decision}, ensure_ascii=False)},
        ])
    payload['messages'].append({'role': 'user', 'content': question.strip()})
    response = (transport or _send)(payload)
    if not isinstance(response, dict) or response.get('done') is not True or response.get('done_reason') not in (None, 'stop'):
        raise ValueError('Local model response is incomplete; no analysis was executed.')
    message = response.get('message')
    if not isinstance(message, dict) or not isinstance(message.get('content'), str):
        raise ValueError('Local model response has no valid text.')
    try:
        parsed = json.loads(message['content'])
    except (ValueError, TypeError):
        raise ValueError('Local model returned invalid JSON; no analysis was executed.') from None
    if not isinstance(parsed, dict) or set(parsed) != {'decision'}:
        raise ValueError('Local model returned an unexpected decision envelope.')
    return validate_decision(parsed['decision'])


def answer_natural_language(path, config, question, reference_date, *, model=None, transport=None):
    decision = interpret_question(question, model=model, transport=transport)
    return execute_decision(path, config, decision, reference_date)
