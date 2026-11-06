"""Export or check the version-one question JSON schemas; no server required."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.contracts import REQUEST_ADAPTER, RESPONSE_ADAPTER, FileQuestionResponse


def schemas():
    return {
        'question-request-v1.schema.json': REQUEST_ADAPTER.json_schema(),
        'question-response-v1.schema.json': RESPONSE_ADAPTER.json_schema(),
        'file-question-response-v1.schema.json': FileQuestionResponse.model_json_schema(),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Check saved schemas without modifying them')
    args = parser.parse_args(argv)
    directory = ROOT / 'docs' / 'schemas'
    for name, schema in schemas().items():
        target = directory / name
        if args.check:
            if not target.exists() or json.loads(target.read_text(encoding='utf-8')) != schema:
                print(f'Schema differs: {name}')
                return 1
        else:
            directory.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('Version-one question schemas: MATCH' if args.check else 'Version-one question schemas: EXPORTED')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
