"""Authenticated dataset boundary and lifecycle leases for one local API process."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timedelta
from pathlib import Path
import stat
from uuid import uuid4

from .contracts import parse_question_request
from .dataset_contracts import ReportInfo
from .datasets import DatasetNotFound, DatasetService, SourceIntegrityError, _ID
from .sessions import APIStoreLock, SessionStore, utc_now
from .resources import ResourceProfile, ResourceLimit, BoundedWorker, check_response, execute
from .report_jobs import render_report

_upload_reservation = ContextVar('upload_reservation', default=None)


class OwnedDatasetService:
    def __init__(self, root, *, dataset_ttl=None, session_ttl=86400, clock=utc_now, limits=None):
        self.limits = limits or ResourceProfile()
        dataset_ttl = self.limits.dataset_ttl if dataset_ttl is None else dataset_ttl
        if type(dataset_ttl) is not int or dataset_ttl <= 0:
            raise ValueError('Dataset TTL must be a positive integer.')
        self._clock = clock
        self._ttl = dataset_ttl
        self.worker = BoundedWorker(self.limits)
        self._reservations = {}
        self._store_lock = APIStoreLock(Path(root) / '.api.lock')
        try:
            self.sessions = SessionStore(Path(root) / '.sessions.sqlite3', ttl_seconds=session_ttl, clock=clock)
            self._repository = DatasetService(root, clock=clock, _deleted_check=self.sessions.is_deleted, limits=self.limits)
            self._repository.storage_check = self._check_storage
            # Startup only: no upload can be in flight before the API accepts work.
            for directory in self._repository._root.iterdir():
                if _ID.fullmatch(directory.name) and directory.is_dir() and not (directory / 'metadata.json').exists():
                    self._repository._remove_directory(directory.name)
            self.cleanup()
        except Exception:
            if hasattr(self, 'sessions'):
                self.sessions.close()
            self._store_lock.close()
            raise

    def close(self):
        self.worker.close()
        self.sessions.close()
        self._store_lock.close()

    def _check_storage(self, owner, added):
        own = total = 0
        for directory in self._repository._root.iterdir():
            if not _ID.fullmatch(directory.name) or directory.is_symlink() or not directory.is_dir():
                continue
            record = self._repository._datasets.get(directory.name)
            directory_owner = record.manifest.owner_session_id if record else owner
            size = sum(p.stat().st_size for p in directory.rglob('*') if p.is_file() and not p.is_symlink())
            total += size
            if directory_owner == owner:
                own += size
        current = _upload_reservation.get()
        for key, (session, size) in self._reservations.items():
            if key == current:
                continue
            total += size
            if session == owner:
                own += size
        if own + added > self.limits.session_storage_bytes or total + added > self.limits.total_storage_bytes:
            raise ResourceLimit('Managed storage quota exceeded.')

    @contextmanager
    def reserve_upload(self, context):
        self.sessions.require(context)
        key = uuid4().hex
        # Reserve both the HTTP spool and the managed copy before receiving bytes.
        with self._repository._lock:
            size = 2 * self.limits.upload_bytes
            self._check_storage(context.session_id, size)
            self._reservations[key] = (context.session_id, size)
        token = _upload_reservation.set(key)
        try:
            yield
        finally:
            _upload_reservation.reset(token)
            with self._repository._lock:
                self._reservations.pop(key, None)

    @contextmanager
    def _computation(self, context, seconds):
        self.sessions.require(context)
        with self.worker.admission(seconds, lambda: self._repository.memory_usage(context.session_id)):
            self.sessions.require(context)
            yield

    def _delete_record(self, record):
        """Journal revocation first; storage errors cannot reopen in-memory access."""
        try:
            self.sessions.mark_deleted(record.manifest.dataset_id)
            return self._repository._request_deletion(record)
        except Exception:
            record.manifest = record.manifest.model_copy(update={'deletion_requested': True})
            self._repository._evict(record)
            raise

    def _owned(self, context, dataset_id):
        record = self._repository._get(dataset_id)
        if record.manifest.owner_session_id != context.session_id:
            raise DatasetNotFound('Dataset not found.')
        if record.manifest.expires_at is None or record.manifest.expires_at <= self._clock():
            self._delete_record(record)
            raise DatasetNotFound('Dataset not found.')
        return record

    @contextmanager
    def _operation(self, context, dataset_id):
        self.sessions.require(context)
        repository = self._repository
        with repository._lock:
            record = self._owned(context, dataset_id)
            record.active_operations += 1
        try:
            yield record
            self.sessions.require(context)
            with repository._lock:
                if record.manifest.deletion_requested:
                    raise DatasetNotFound('Dataset not found.')
                if record.manifest.expires_at <= self._clock():
                    self._delete_record(record)
                    raise DatasetNotFound('Dataset not found.')
        finally:
            with repository._lock:
                record.active_operations -= 1
                expired = record.manifest.expires_at <= self._clock()
                if expired or not self.sessions.is_active(context.session_id):
                    self._delete_record(record)
                elif record.manifest.deletion_requested:
                    repository._purge(record)

    def upload(self, context, stream, *, filename):
        session_expires = self.sessions.require(context)
        expires = min(self._clock() + timedelta(seconds=self._ttl), session_expires)
        with self._repository._lock:
            uploaded = self._repository.upload(stream, filename=filename,
                                               _owner_session_id=context.session_id, _expires_at=expires)
        try:
            self.sessions.require(context)
            with self._repository._lock:
                self._owned(context, uploaded.dataset_id)
        except Exception:
            with self._repository._lock:
                record = self._repository._datasets.get(uploaded.dataset_id)
                if record is not None:
                    self._delete_record(record)
            raise
        return uploaded

    def get_dataset(self, context, dataset_id):
        with self._operation(context, dataset_id):
            return self._repository.get_dataset(dataset_id)

    def describe(self, context, dataset_id):
        with self._computation(context, self.limits.prepare_seconds), self._operation(context, dataset_id):
            result = self._repository.describe(dataset_id)
            check_response(result, self.limits)
            return result

    def preview(self, context, dataset_id, *, options=None, rows=5):
        with self._computation(context, self.limits.prepare_seconds), self._operation(context, dataset_id):
            result = self._repository.preview(dataset_id, options=options, rows=rows)
            check_response(result, self.limits)
            return result

    def approve_mapping(self, context, dataset_id, config):
        with self._computation(context, self.limits.prepare_seconds), self._operation(context, dataset_id):
            return self._repository.approve_mapping(dataset_id, config)

    def prepare(self, context, dataset_id, *, mapping_revision=None):
        with self._computation(context, self.limits.prepare_seconds), self._operation(context, dataset_id):
            result = self._repository.prepare(dataset_id, mapping_revision=mapping_revision)
            check_response(result, self.limits)
            return result

    def list_preparations(self, context, dataset_id):
        with self._operation(context, dataset_id):
            return self._repository.list_preparations(dataset_id)

    @staticmethod
    def _require_preparation(record, preparation_id):
        if not any(p.preparation_id == preparation_id for p in record.manifest.preparations):
            raise DatasetNotFound('Preparation not found.')

    def query(self, context, dataset_id, preparation_id, request):
        request = parse_question_request(request)
        if getattr(request, 'limit', 0) > self.limits.result_rows:
            raise ResourceLimit('Requested result row limit exceeds the service profile.')
        with self._computation(context, self.limits.query_seconds), self._operation(context, dataset_id) as record:
            self._require_preparation(record, preparation_id)
            result = self._repository.query(preparation_id, request)
            check_response(result, self.limits)
            return result

    def download_source(self, context, dataset_id):
        with self._operation(context, dataset_id) as record:
            with self._repository._lock:
                self._repository._ensure_source(record)
                content = self._repository._source_path(record).read_bytes()
                return record.manifest.source.file, content

    def create_report(self, context, dataset_id, preparation_id, request, *, format='json', theme='light'):
        if format not in ('json', 'html') or theme not in ('light', 'dark'):
            raise ValueError('Unsupported report format or theme.')
        request = parse_question_request(request)
        if getattr(request, 'limit', 0) > self.limits.result_rows:
            raise ResourceLimit('Requested result row limit exceeds the service profile.')
        seconds = self.limits.render_seconds if format == 'html' else self.limits.query_seconds
        with self._computation(context, seconds), self._operation(context, dataset_id) as record:
            self._require_preparation(record, preparation_id)
            answer = self._repository.query(preparation_id, request)
            check_response(answer, self.limits)
            content = (execute(render_report, answer.model_dump(mode='json'), theme, self.limits)
                       if format == 'html' else answer.model_dump_json(indent=2).encode('utf-8'))
            if len(content) > self.limits.response_bytes:
                raise ResourceLimit('Report byte limit exceeded.')
            with self._repository._lock:
                self._repository._get(dataset_id)
                self._check_storage(context.session_id, len(content))
                directory = self._repository._root / dataset_id / 'reports'
                directory.mkdir(exist_ok=True)
                if directory.is_symlink() or directory.resolve().parent != self._repository._root / dataset_id:
                    raise SourceIntegrityError('Report directory is not a managed directory.')
                report_id = uuid4().hex
                path = directory / (report_id + '.' + format)
                try:
                    with path.open('xb') as handle:
                        handle.write(content)
                    path.chmod(stat.S_IREAD)
                    info = ReportInfo(dataset_id=dataset_id, report_id=report_id,
                                      preparation_id=preparation_id, created_at=self._clock(), size_bytes=len(content),
                                      media_type='text/html' if format == 'html' else 'application/json')
                    manifest = record.manifest.model_copy(update={'reports': [*record.manifest.reports, info]})
                    self._repository._persist(manifest)
                    record.manifest = manifest
                except Exception:
                    if path.exists():
                        path.chmod(stat.S_IREAD | stat.S_IWRITE)
                        path.unlink()
                    raise
            return info.model_copy(deep=True)

    def download_report(self, context, dataset_id, report_id):
        with self._operation(context, dataset_id) as record:
            with self._repository._lock:
                report = next((r for r in record.manifest.reports if r.report_id == report_id), None)
                if report is None:
                    raise DatasetNotFound('Report not found.')
                extension = '.html' if report.media_type == 'text/html' else '.json'
                directory = self._repository._root / dataset_id / 'reports'
                path = directory / (report_id + extension)
                if directory.is_symlink() or path.is_symlink() or path.resolve().parent != directory:
                    raise SourceIntegrityError('Report path is not managed.')
                return 'analysis-' + report_id + extension, path.read_bytes()

    def delete(self, context, dataset_id):
        self.sessions.require(context)
        with self._repository._lock:
            record = self._owned(context, dataset_id)
            return self._delete_record(record)

    def revoke_session(self, context):
        self.sessions.revoke(context)
        self.cleanup()

    def cleanup(self):
        repository = self._repository
        deleted = 0
        with repository._lock:
            for record in list(repository._datasets.values()):
                manifest = record.manifest
                if manifest.owner_session_id is None:
                    continue  # Legacy offline data is never assigned to an API session.
                if (manifest.deletion_requested or self.sessions.is_deleted(manifest.dataset_id) or manifest.expires_at is None or
                        manifest.expires_at <= self._clock() or not self.sessions.is_active(manifest.owner_session_id)):
                    deleted += self._delete_record(record)
        self.sessions.prune()
        return deleted
