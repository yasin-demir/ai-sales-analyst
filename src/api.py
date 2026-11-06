"""Local single-process HTTP prototype with authenticated session ownership."""
from contextlib import asynccontextmanager
import asyncio
import logging
import sqlite3
from pathlib import Path
from tempfile import SpooledTemporaryFile
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import Depends, FastAPI, Header, Request, Response
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.concurrency import run_in_threadpool
from pydantic import Field, StrictBool, StrictInt

from .contracts import (ContractModel, ImportSettings, PositiveInt, QuestionRequest, Text,
                        ContractViolation)
from .dataset_contracts import (DatasetDetail, DatasetId, DatasetInfo, MappingInfo, PreparedAnswer,
                                PreparedInfo, PreviewInfo, ReaderOptions, ReportInfo)
from .datasets import (DatasetNotFound, MappingNotApproved, SourceIntegrityError,
                       PreparationVersionMismatch)
from .owned_datasets import OwnedDatasetService
from .sessions import AuthenticationRequired, SessionCredentials, SessionContext, utc_now
from .resources import ResourceProfile, ResourceLimit, QueueFull, ComputationTimeout
from .report_jobs import RenderFailure


class EmptyBody(ContractModel):
    pass


class MappingBody(ImportSettings):
    delimiter: Annotated[str, Field(min_length=1, max_length=1)] = ','
    encoding: Text = 'utf-8-sig'
    decimal: Literal['.', ','] = '.'
    thousands: Literal['.', ',', ' '] | None = None
    date_format: Text = '%Y-%m-%d'
    discount_unit: Literal['fraction', 'percent'] = 'fraction'
    currency: Text | None = None
    allow_returns: StrictBool = False
    invalid_rows: Literal['stop', 'exclude'] = 'stop'
    sheet: StrictInt | Text = 0


class PrepareBody(ContractModel):
    mapping_revision: PositiveInt | None = None


class QueryBody(ContractModel):
    preparation_id: DatasetId
    request: QuestionRequest


class ReportBody(QueryBody):
    format: Literal['json', 'html'] = 'json'
    theme: Literal['light', 'dark'] = 'light'


class PreviewBody(ContractModel):
    options: ReaderOptions = ReaderOptions()
    rows: Annotated[int, Field(ge=1, le=50)] = 5


def create_app(root=None, *, clock=utc_now, dataset_ttl=None, session_ttl=86400,
               cleanup_interval=30, upload_limit=None, limits=None):
    root = root or Path(__file__).resolve().parents[1] / 'work' / 'api-store'
    limits = ResourceProfile.model_validate(limits or {}) if not isinstance(limits, ResourceProfile) else limits
    if upload_limit is not None:
        limits = ResourceProfile.model_validate({**limits.model_dump(), 'upload_bytes': upload_limit})
    upload_limit = limits.upload_bytes
    if cleanup_interval <= 0 or type(upload_limit) is not int or upload_limit <= 0:
        raise ValueError('Cleanup interval and upload limit must be positive.')
    service = OwnedDatasetService(root, clock=clock, dataset_ttl=dataset_ttl, session_ttl=session_ttl, limits=limits)

    @asynccontextmanager
    async def lifespan(app):
        stop = asyncio.Event()
        async def reaper():
            while not stop.is_set():
                try:
                    await asyncio.wait_for(stop.wait(), timeout=cleanup_interval)
                except asyncio.TimeoutError:
                    try:
                        await asyncio.to_thread(service.cleanup)
                    except (OSError, RuntimeError):
                        logging.getLogger(__name__).warning('Dataset cleanup failed; a later sweep will retry.')
        task = None
        try:
            service.cleanup()
            task = asyncio.create_task(reaper())
            yield
        finally:
            stop.set()
            if task is not None:
                await task
            service.close()

    app = FastAPI(title='Local Sales Analyst', version='0.3.0', lifespan=lifespan)
    app.state.datasets = service
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', '[::1]'])

    @app.middleware('http')
    async def local_origin(request, call_next):
        origin = request.headers.get('origin')
        if origin is not None and origin != str(request.base_url).rstrip('/'):
            return JSONResponse({'detail': 'Cross-origin requests are not allowed.'}, status_code=403)
        response = await call_next(request)
        if ('application/json' in response.headers.get('content-type', '') and
                int(response.headers.get('content-length', '0')) > limits.response_bytes):
            response = JSONResponse({'detail': 'Response byte limit exceeded.'}, status_code=413)
        response.headers['Cache-Control'] = 'no-store'
        return response

    def error_handler(status, message=None, headers=None):
        async def handler(request, exc):
            return JSONResponse({'detail': message or str(exc)}, status_code=status, headers=headers)
        return handler

    app.add_exception_handler(AuthenticationRequired, error_handler(401, headers={'WWW-Authenticate': 'Bearer'}))
    app.add_exception_handler(ResourceLimit, error_handler(413))
    app.add_exception_handler(QueueFull, error_handler(429, headers={'Retry-After': '1'}))
    app.add_exception_handler(ComputationTimeout, error_handler(504))
    app.add_exception_handler(RenderFailure, error_handler(500, 'Report rendering failed; no artifact was published.'))
    app.add_exception_handler(DatasetNotFound, error_handler(404))
    app.add_exception_handler(MappingNotApproved, error_handler(409))
    app.add_exception_handler(SourceIntegrityError, error_handler(409, 'Managed source or report is unavailable.'))
    app.add_exception_handler(PreparationVersionMismatch, error_handler(409))
    app.add_exception_handler(ContractViolation, error_handler(500, 'Internal response contract failure.'))
    app.add_exception_handler(ValueError, error_handler(422, 'Invalid dataset settings or request.'))
    app.add_exception_handler(OSError, error_handler(503, 'Storage operation failed.'))
    app.add_exception_handler(sqlite3.Error, error_handler(503, 'Session storage is unavailable.'))

    bearer = HTTPBearer(auto_error=False)
    def current(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
        if credentials is None or credentials.scheme.lower() != 'bearer':
            raise AuthenticationRequired('A valid local session is required.')
        return service.sessions.authenticate(credentials.credentials)
    Auth = Annotated[SessionContext, Depends(current)]

    @app.get('/health')
    def health():
        return {'status': 'ok'}

    @app.post('/v1/sessions', status_code=201, response_model=SessionCredentials)
    def session(body: EmptyBody = EmptyBody()):
        return service.sessions.issue()

    @app.delete('/v1/session', status_code=204)
    def revoke(context: Auth):
        service.revoke_session(context)
        return Response(status_code=204)

    @app.post('/v1/datasets', status_code=201, response_model=DatasetInfo,
              openapi_extra={'requestBody': {'required': True, 'content': {
                  'application/octet-stream': {'schema': {'type': 'string', 'format': 'binary'}}}}})
    async def upload(request: Request, context: Auth,
                     filename: Annotated[str, Header(alias='X-Filename', min_length=1, max_length=255)]):
        received = 0
        with service.reserve_upload(context), SpooledTemporaryFile(max_size=min(1024 * 1024, upload_limit), mode='w+b') as temporary:
            async for chunk in request.stream():
                received += len(chunk)
                if received > upload_limit:
                    return JSONResponse({'detail': 'Upload exceeds the configured byte limit.'}, status_code=413)
                temporary.write(chunk)
            temporary.seek(0)
            return await run_in_threadpool(service.upload, context, temporary, filename=filename)

    @app.get('/v1/datasets/{dataset_id}', response_model=DatasetDetail)
    def info(dataset_id: DatasetId, context: Auth):
        return service.describe(context, dataset_id)

    @app.post('/v1/datasets/{dataset_id}/preview', response_model=PreviewInfo)
    def preview(dataset_id: DatasetId, body: PreviewBody, context: Auth):
        return service.preview(context, dataset_id, options=body.options, rows=body.rows)

    @app.put('/v1/datasets/{dataset_id}/mapping', response_model=MappingInfo)
    def mapping(dataset_id: DatasetId, body: MappingBody, context: Auth):
        return service.approve_mapping(context, dataset_id, body)

    @app.post('/v1/datasets/{dataset_id}/prepare', response_model=PreparedInfo)
    def prepare(dataset_id: DatasetId, body: PrepareBody, context: Auth):
        return service.prepare(context, dataset_id, mapping_revision=body.mapping_revision)

    @app.get('/v1/datasets/{dataset_id}/preparations', response_model=list[PreparedInfo])
    def preparations(dataset_id: DatasetId, context: Auth):
        return service.list_preparations(context, dataset_id)

    @app.post('/v1/datasets/{dataset_id}/questions', response_model=PreparedAnswer)
    def question(dataset_id: DatasetId, body: QueryBody, context: Auth):
        return service.query(context, dataset_id, body.preparation_id, body.request)

    def attachment(name, content, media_type):
        return Response(content=content, media_type=media_type,
                        headers={'Content-Disposition': "attachment; filename*=UTF-8''" + quote(name, safe='')})

    @app.get('/v1/datasets/{dataset_id}/source')
    def source(dataset_id: DatasetId, context: Auth):
        name, content = service.download_source(context, dataset_id)
        return attachment(name, content, 'application/octet-stream')

    @app.post('/v1/datasets/{dataset_id}/reports', response_model=ReportInfo, status_code=201)
    def report(dataset_id: DatasetId, body: ReportBody, context: Auth):
        return service.create_report(context, dataset_id, body.preparation_id, body.request, format=body.format, theme=body.theme)

    @app.get('/v1/datasets/{dataset_id}/reports/{report_id}')
    def download_report(dataset_id: DatasetId, report_id: DatasetId, context: Auth):
        name, content = service.download_report(context, dataset_id, report_id)
        response = attachment(name, content, 'text/html' if name.endswith('.html') else 'application/json')
        if name.endswith('.html'):
            response.headers['Content-Security-Policy'] = "default-src 'none'; img-src data:; style-src 'unsafe-inline'"
            response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    @app.delete('/v1/datasets/{dataset_id}', response_model=None)
    def delete(dataset_id: DatasetId, context: Auth):
        if service.delete(context, dataset_id):
            return Response(status_code=204)
        return JSONResponse({'dataset_id': dataset_id, 'status': 'deletion_pending'}, status_code=202)

    return app


def main():
    import argparse
    import uvicorn
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--store', type=Path)
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--limits', type=Path, help='JSON ResourceProfile overrides')
    args = parser.parse_args()
    limits = ResourceProfile.model_validate_json(args.limits.read_text(encoding='utf-8')) if args.limits else None
    uvicorn.run(create_app(args.store, limits=limits), host='127.0.0.1', port=args.port, workers=1)


if __name__ == '__main__':
    main()
