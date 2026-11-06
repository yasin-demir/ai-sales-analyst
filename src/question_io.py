"""File-level bridge to the deterministic question functions, ready for an API layer."""
from pathlib import Path
import hashlib
from copy import deepcopy
from dataclasses import asdict
from .importing import read_input,prepare
from .questions import run_question
from pydantic import ValidationError
from .contracts import parse_question_request, FileQuestionResponse, ContractViolation
from .resources import execute


def _checked_response(payload):
    try:
        FileQuestionResponse.model_validate(payload)
    except ValidationError as exc:
        raise ContractViolation('Generated question response failed the version-one contract.') from exc
    # Validate without materializing absent optional fields or changing legacy JSON.
    return payload

def answer_file_question(path,config,request):
    request=parse_question_request(request).model_dump(exclude_none=True)
    path=Path(path);quality=prepare(read_input(path,config),config);frame=quality.pop('frame')
    source={'file':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    return answer_prepared_question(frame,source=source,config=config,quality=quality,request=request)


def answer_prepared_question(frame,*,source,config,quality,request):
    """Query an already validated snapshot; no source reads, preparation or hashing.

    The defensive copies protect a service's cached frame and quality metadata.
    The legacy file adapter above retains its existing one-shot behavior.
    """
    request=parse_question_request(request).model_dump(exclude_none=True)
    quality=deepcopy(quality)
    payload={'schema_version':1,'source':deepcopy(source),
             'import_config':asdict(config),'quality':quality,'status':quality['status'],'answer':None}
    if quality['status']!='ready':return _checked_response(payload)
    answer=execute(run_question,frame.copy(deep=True),unit=config.currency,**request)
    for entry in answer['results']:
        entry['warnings'] += [issue['message'] for issue in quality['issues'] if issue['severity']=='warning']
        entry['scope']['raw_input_records']=quality['input_rows']
        entry['scope']['explicitly_excluded_records']=len(quality['excluded_records'])
    payload.update(status=answer['status'],answer=answer)
    return _checked_response(payload)
