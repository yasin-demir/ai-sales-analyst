"""Single-writer prepared-data service; managed source files and durable metadata.

Frames stay private and in memory. A cold cache is reconstructed once from the
immutable source; ordinary queries do not read, prepare or hash the source.
"""
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import stat
from threading import RLock
from uuid import uuid4

import numpy as np
import pandas as pd
from pydantic import TypeAdapter

from .contracts import DataQuality, ImportSettings, Text, parse_question_request
from .dataset_contracts import (DatasetDetail, DatasetInfo, MappingInfo, PreparedAnswer, PreparedInfo,
                                PreviewInfo, ReaderOptions, StoreManifest, UploadedSource)
from .importing import ImportConfig, prepare, read_input, suggest_mapping
from .question_io import answer_prepared_question
from .resources import execute, ResourceLimit


PREPARER_VERSION = 'sales-import-v1'
_CHUNK_BYTES = 1024 * 1024
_ID = re.compile(r'^[0-9a-f]{32}$')


class DatasetNotFound(LookupError):
    pass


class MappingNotApproved(ValueError):
    pass


class SourceIntegrityError(RuntimeError):
    pass


class PreparationVersionMismatch(RuntimeError):
    pass


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def _fingerprint(value):
    return hashlib.sha256(_canonical(value).encode('utf-8')).hexdigest()


def _filename(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('An original file name is required.')
    name = PureWindowsPath(value).name
    if not name.strip() or '\x00' in name or Path(name).suffix.lower() not in ('.csv', '.xlsx'):
        raise ValueError('Only CSV and XLSX source uploads are supported.')
    return name


def _settings(config):
    if isinstance(config, ImportConfig):
        value = asdict(config)
    elif isinstance(config, ImportSettings):
        value = config.model_dump()
    elif isinstance(config, dict):
        value = {**asdict(ImportConfig()), **deepcopy(config)}
    else:
        raise ValueError('Mapping settings must be ImportConfig, ImportSettings or a dictionary.')
    return ImportSettings.model_validate(value)


def _reader_options(settings):
    return ReaderOptions(delimiter=settings.delimiter, encoding=settings.encoding, sheet=settings.sheet)


def _preview_cell(value):
    if value is None or pd.isna(value):
        return None
    if isinstance(value, (datetime, date, np.datetime64)):
        return pd.Timestamp(value).isoformat()
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not np.isfinite(value):
        return str(value)
    return value if isinstance(value, (str, int, float, bool)) else str(value)


@dataclass
class _StoredDataset:
    manifest: StoreManifest
    raw_cache: dict[str, pd.DataFrame] = field(default_factory=dict)
    active_operations: int = 0


class DatasetService:
    """One service instance/writer per private storage root; no HTTP/auth layer."""

    def __init__(self, root, *, preparer_version=PREPARER_VERSION, clock=None, _deleted_check=None, limits=None):
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._preparer_version = TypeAdapter(Text).validate_python(preparer_version, strict=True)
        self._lock = RLock()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._deleted_check = _deleted_check
        self.limits = limits
        self.storage_check = None
        self._datasets: dict[str, _StoredDataset] = {}
        self._preparations: dict[str, str] = {}
        self._frames: dict[str, pd.DataFrame] = {}
        self._load_existing()

    def _source_path(self, record):
        source = record.manifest.source
        return self._root / record.manifest.dataset_id / ('source' + Path(source.file).suffix.lower())

    def _get(self, dataset_id):
        if not isinstance(dataset_id, str) or not _ID.fullmatch(dataset_id):
            raise ValueError('dataset_id must be a server-generated identifier.')
        try:
            record = self._datasets[dataset_id]
        except KeyError:
            raise DatasetNotFound('Dataset not found.') from None
        if record.manifest.deletion_requested:
            raise DatasetNotFound('Dataset not found.')
        return record

    def _ensure_source(self, record):
        path = self._source_path(record)
        try:
            info = path.stat()
        except OSError:
            raise SourceIntegrityError('Managed source is missing or inaccessible.') from None
        if path.is_symlink() or info.st_size != record.manifest.source.size_bytes or info.st_mtime_ns != record.manifest.source_mtime_ns:
            raise SourceIntegrityError('Managed source changed after upload; create a new upload.')

    def _persist(self, manifest):
        directory = self._root / manifest.dataset_id
        if self.storage_check is not None and not manifest.deletion_requested:
            self.storage_check(manifest.owner_session_id, len(manifest.model_dump_json(indent=2).encode('utf-8')) + 1)
        temporary = directory / ('.metadata-' + uuid4().hex + '.tmp')
        try:
            with temporary.open('x', encoding='utf-8') as handle:
                handle.write(manifest.model_dump_json(indent=2) + '\n')
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, directory / 'metadata.json')
        finally:
            temporary.unlink(missing_ok=True)

    def _validate_manifest(self, manifest, directory):
        if manifest.dataset_id != directory.name or _filename(manifest.source.file) != manifest.source.file:
            raise ValueError('Invalid managed dataset identity.')
        mappings = manifest.mappings
        if [m.revision for m in mappings] != list(range(1, len(mappings) + 1)):
            raise ValueError('Invalid mapping revision history.')
        for mapping in mappings:
            if mapping.dataset_id != manifest.dataset_id or mapping.config_sha256 != _fingerprint(mapping.config.model_dump()):
                raise ValueError('Invalid mapping provenance.')
        if [p.revision for p in manifest.preparations] != list(range(1, len(manifest.preparations) + 1)):
            raise ValueError('Invalid preparation revision history.')
        ids, versions = set(), set()
        for prepared in manifest.preparations:
            if prepared.mapping_revision > len(mappings):
                raise ValueError('Preparation references an unknown mapping.')
            mapping = mappings[prepared.mapping_revision - 1]
            version = (prepared.mapping_revision, prepared.preparer_version)
            if (prepared.dataset_id != manifest.dataset_id or prepared.source != manifest.source or
                    prepared.config_sha256 != mapping.config_sha256 or prepared.preparation_id in ids or version in versions or
                    prepared.preparation_key != self._preparation_key(manifest.source, mapping, prepared.preparer_version)):
                raise ValueError('Invalid preparation provenance.')
            ids.add(prepared.preparation_id)
            versions.add(version)
        if (manifest.owner_session_id is None) != (manifest.expires_at is None):
            raise ValueError('Owned datasets require an expiry.')
        report_ids = set()
        for report in manifest.reports:
            if report.dataset_id != manifest.dataset_id or report.preparation_id not in ids or report.report_id in report_ids:
                raise ValueError('Invalid report provenance.')
            report_ids.add(report.report_id)

    def _load_existing(self):
        for directory in sorted(self._root.iterdir()):
            if not directory.is_dir() or not _ID.fullmatch(directory.name):
                continue
            if directory.is_symlink():
                raise SourceIntegrityError('Managed dataset directories cannot be symbolic links.')
            if self._deleted_check is not None and self._deleted_check(directory.name):
                self._remove_directory(directory.name)
                continue
            path = directory / 'metadata.json'
            if not path.exists():
                continue  # Incomplete upload, never published; lifecycle cleanup is separate.
            manifest = StoreManifest.model_validate_json(path.read_text(encoding='utf-8'))
            self._validate_manifest(manifest, directory)
            record = _StoredDataset(manifest)
            expired = manifest.expires_at is not None and manifest.expires_at <= self._clock()
            if not manifest.deletion_requested and not expired:
                self._ensure_source(record)
            self._datasets[manifest.dataset_id] = record
            for prepared in manifest.preparations:
                if prepared.preparation_id in self._preparations:
                    raise ValueError('Duplicate preparation identifier in storage.')
                self._preparations[prepared.preparation_id] = manifest.dataset_id

    @staticmethod
    def _preparation_key(source, mapping, version):
        return _fingerprint({'source_sha256': source.sha256, 'config_sha256': mapping.config_sha256,
                             'preparer_version': version})

    def upload(self, stream, *, filename, _owner_session_id=None, _expires_at=None):
        """Copy a binary upload and hash the same bytes incrementally, once."""
        filename = _filename(filename)
        if not callable(getattr(stream, 'read', None)):
            raise ValueError('Upload requires a readable binary stream.')
        dataset_id = uuid4().hex
        directory = self._root / dataset_id
        directory.mkdir()
        staging = directory / 'upload.part'
        source_path = directory / ('source' + Path(filename).suffix.lower())
        try:
            digest, size = hashlib.sha256(), 0
            with staging.open('xb') as handle:
                while True:
                    chunk = stream.read(_CHUNK_BYTES)
                    if not isinstance(chunk, bytes):
                        raise ValueError('Upload stream must return bytes.')
                    if not chunk:
                        break
                    if self.limits is not None:
                        if size + len(chunk) > self.limits.upload_bytes:
                            raise ResourceLimit('Upload byte limit exceeded.')
                        if self.storage_check is not None:
                            self.storage_check(_owner_session_id, len(chunk))
                    handle.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            if size == 0:
                raise ValueError('An empty source cannot be uploaded.')
            os.replace(staging, source_path)
            source_path.chmod(stat.S_IREAD)
            source = UploadedSource(file=filename, sha256=digest.hexdigest(), size_bytes=size)
            manifest = StoreManifest(schema_version=1, dataset_id=dataset_id, source=source,
                                     source_mtime_ns=source_path.stat().st_mtime_ns,
                                     uploaded_at=self._clock(), owner_session_id=_owner_session_id,
                                     expires_at=_expires_at)
            self._persist(manifest)
            with self._lock:
                self._datasets[dataset_id] = _StoredDataset(manifest)
                return self.get_dataset(dataset_id)
        except Exception:
            # Only this newly allocated directory and known files are cleaned up.
            for path in (staging, source_path, directory / 'metadata.json'):
                if path.exists():
                    path.chmod(stat.S_IREAD | stat.S_IWRITE)
                    path.unlink()
            directory.rmdir()
            raise

    def upload_path(self, path):
        """Local adapter; future HTTP clients provide streams, never server paths."""
        path = Path(path)
        with path.open('rb') as stream:
            return self.upload(stream, filename=path.name)

    def get_dataset(self, dataset_id):
        with self._lock:
            record = self._get(dataset_id)
            manifest = record.manifest
            mapping = manifest.mappings[-1] if manifest.mappings else None
            current = next((p for p in reversed(manifest.preparations)
                            if mapping and p.mapping_revision == mapping.revision and
                            p.preparer_version == self._preparer_version), None)
            status = ('ready' if current.quality.status == 'ready' else 'needs_attention') if current else ('mapped' if mapping else 'uploaded')
            return DatasetInfo(dataset_id=dataset_id, source=manifest.source.model_copy(deep=True),
                               uploaded_at=manifest.uploaded_at, status=status,
                               latest_mapping_revision=mapping.revision if mapping else None,
                               latest_preparation_revision=manifest.preparations[-1].revision if manifest.preparations else None,
                               expires_at=manifest.expires_at)

    def _raw(self, record, options):
        self._ensure_source(record)
        key = _canonical(options.model_dump())
        if key not in record.raw_cache:
            config = ImportConfig(**options.model_dump())
            raw = (read_input(self._source_path(record), config) if self.limits is None else
                   execute(read_input, self._source_path(record), config, self.limits))
            self._check_memory(record, raw)
            record.raw_cache[key] = raw
        return record.raw_cache[key]

    def describe(self, dataset_id):
        """Inspect using approved reader options, without approving or preparing."""
        with self._lock:
            record = self._get(dataset_id)
            info = self.get_dataset(dataset_id)
            mapping = record.manifest.mappings[-1] if record.manifest.mappings else None
            options = _reader_options(mapping.config) if mapping else ReaderOptions()
            preview = self.preview(dataset_id, options=options)
            prepared = next((p for p in reversed(record.manifest.preparations)
                             if mapping and p.mapping_revision == mapping.revision and
                             p.preparer_version == self._preparer_version), None)
            fields = ([field for field in mapping.config.mapping if field not in prepared.quality.blocked_fields]
                      if prepared and prepared.quality.status == 'ready' else [])
            return DatasetDetail(**info.model_dump(), preview=preview,
                                 preparation_id=prepared.preparation_id if prepared else None,
                                 available_fields=fields,
                                 quality=prepared.quality.model_copy(deep=True) if prepared else None)

    def memory_usage(self, owner=None):
        total = 0
        frames = self._frames.copy()
        for record in list(self._datasets.values()):
            if owner is None or record.manifest.owner_session_id == owner:
                total += sum(int(f.memory_usage(index=True, deep=True).sum()) for f in list(record.raw_cache.values()))
                total += sum(int(frames[p.preparation_id].memory_usage(index=True, deep=True).sum())
                             for p in record.manifest.preparations if p.preparation_id in frames)
        return total

    def _check_memory(self, record, frame):
        if self.limits is None:
            return
        added = int(frame.memory_usage(index=True, deep=True).sum())
        if (self.memory_usage() + added > self.limits.total_memory_bytes or
                self.memory_usage(record.manifest.owner_session_id) + added > self.limits.session_memory_bytes):
            raise ResourceLimit('Prepared data memory quota exceeded.')

    def preview(self, dataset_id, *, options=None, rows=5):
        if type(rows) is not int or not 1 <= rows <= 50:
            raise ValueError('Preview rows must be an integer from 1 to 50.')
        if isinstance(options, ReaderOptions):
            options = options.model_dump()
        options = ReaderOptions.model_validate({} if options is None else options)
        with self._lock:
            record = self._get(dataset_id)
            raw = self._raw(record, options)
            preview = [{str(key): _preview_cell(value) for key, value in row.items()}
                       for row in raw.head(rows).to_dict(orient='records')]
            return PreviewInfo(dataset_id=dataset_id, source=record.manifest.source.model_copy(deep=True),
                               reader_options=options, rows=len(raw), columns=list(raw.columns),
                               preview=preview, **suggest_mapping(raw.columns))

    def approve_mapping(self, dataset_id, config):
        """Explicit reviewed settings; suggestions alone never approve a mapping."""
        settings = _settings(config)
        config_sha256 = _fingerprint(settings.model_dump())
        with self._lock:
            record = self._get(dataset_id)
            raw = self._raw(record, _reader_options(settings))
            if set(settings.mapping.values()) - set(raw.columns):
                raise ValueError('Reviewed mapping refers to columns absent from this upload.')
            previous = record.manifest.mappings[-1] if record.manifest.mappings else None
            if previous and previous.config_sha256 == config_sha256:
                return previous.model_copy(deep=True)
            info = MappingInfo(dataset_id=dataset_id, revision=len(record.manifest.mappings) + 1,
                               config_sha256=config_sha256, config=settings,
                               approved_at=self._clock())
            manifest = record.manifest.model_copy(update={'mappings': [*record.manifest.mappings, info]})
            self._persist(manifest)
            record.manifest = manifest
            return info.model_copy(deep=True)

    @staticmethod
    def _mapping(record, revision):
        if revision is None:
            revision = len(record.manifest.mappings)
        if type(revision) is not int or revision < 1:
            raise MappingNotApproved('Approve a mapping before preparation.')
        if revision > len(record.manifest.mappings):
            raise MappingNotApproved('Mapping revision not found.')
        return record.manifest.mappings[revision - 1]

    def get_mapping(self, dataset_id, *, revision=None):
        with self._lock:
            return self._mapping(self._get(dataset_id), revision).model_copy(deep=True)

    def _compute(self, record, mapping):
        raw = self._raw(record, _reader_options(mapping.config))
        config = ImportConfig(**mapping.config.model_dump())
        quality = execute(prepare, raw.copy(deep=True), config)
        frame = quality.pop('frame')
        self._check_memory(record, frame)
        return frame, DataQuality.model_validate(quality)

    def _materialize(self, record, info):
        if info.preparer_version != self._preparer_version:
            raise PreparationVersionMismatch('This preparation belongs to a different importer version; prepare a new revision.')
        if info.preparation_id not in self._frames:
            mapping = self._mapping(record, info.mapping_revision)
            frame, quality = self._compute(record, mapping)
            if quality != info.quality:
                raise SourceIntegrityError('Reconstructed data quality differs from the saved preparation.')
            self._frames[info.preparation_id] = frame
        return self._frames[info.preparation_id]

    def prepare(self, dataset_id, *, mapping_revision=None):
        with self._lock:
            record = self._get(dataset_id)
            mapping = self._mapping(record, mapping_revision)
            self._ensure_source(record)
            previous = next((p for p in record.manifest.preparations
                             if p.mapping_revision == mapping.revision and p.preparer_version == self._preparer_version), None)
            if previous:
                self._materialize(record, previous)
                return previous.model_copy(deep=True)
            frame, quality = self._compute(record, mapping)
            info = PreparedInfo(dataset_id=dataset_id, preparation_id=uuid4().hex,
                                revision=len(record.manifest.preparations) + 1,
                                mapping_revision=mapping.revision,
                                source=record.manifest.source.model_copy(deep=True),
                                config_sha256=mapping.config_sha256,
                                preparer_version=self._preparer_version,
                                preparation_key=self._preparation_key(record.manifest.source, mapping, self._preparer_version),
                                created_at=self._clock(), quality=quality)
            manifest = record.manifest.model_copy(update={'preparations': [*record.manifest.preparations, info]})
            self._persist(manifest)
            record.manifest = manifest
            self._frames[info.preparation_id] = frame
            self._preparations[info.preparation_id] = dataset_id
            return info.model_copy(deep=True)

    def list_preparations(self, dataset_id):
        with self._lock:
            return [p.model_copy(deep=True) for p in self._get(dataset_id).manifest.preparations]

    def query(self, preparation_id, request):
        """Execute a typed question against an explicitly pinned preparation."""
        request = parse_question_request(request)
        if not isinstance(preparation_id, str) or not _ID.fullmatch(preparation_id):
            raise ValueError('preparation_id must be a server-generated identifier.')
        with self._lock:
            try:
                dataset_id = self._preparations[preparation_id]
            except KeyError:
                raise DatasetNotFound('Preparation not found.') from None
            record = self._get(dataset_id)
            info = next(p for p in record.manifest.preparations if p.preparation_id == preparation_id)
            frame = self._materialize(record, info)
            mapping = self._mapping(record, info.mapping_revision)
            config = ImportConfig(**mapping.config.model_dump())
            source = {'file': info.source.file, 'sha256': info.source.sha256}
            quality = info.quality.model_dump()
        answer = answer_prepared_question(frame, source=source, config=config, quality=quality, request=request)
        return PreparedAnswer(dataset_id=dataset_id, preparation_id=preparation_id,
                              preparation_revision=info.revision, result=answer)

    def _evict(self, record):
        record.raw_cache.clear()
        for prepared in record.manifest.preparations:
            self._frames.pop(prepared.preparation_id, None)
            self._preparations.pop(prepared.preparation_id, None)

    def _remove_directory(self, dataset_id):
        """Delete only a verified direct child of the private store, never a link."""
        if not isinstance(dataset_id, str) or not _ID.fullmatch(dataset_id):
            raise SourceIntegrityError('Invalid deletion target.')
        target = self._root / dataset_id
        if not target.exists() and not target.is_symlink():
            return
        attributes = target.lstat()
        reparse = getattr(attributes, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)
        if target.is_symlink() or reparse or target.resolve().parent != self._root:
            raise SourceIntegrityError('Deletion target must be a managed directory, not a link.')
        def writable_retry(function, path, error):
            info = os.lstat(path)
            if stat.S_ISLNK(info.st_mode) or (getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)):
                raise error[1]
            os.chmod(path, stat.S_IRWXU)
            function(path)
        shutil.rmtree(target, onerror=writable_retry)

    def _purge(self, record):
        if record.active_operations:
            return False
        self._evict(record)
        self._remove_directory(record.manifest.dataset_id)
        self._datasets.pop(record.manifest.dataset_id, None)
        return True

    def _request_deletion(self, record):
        if not record.manifest.deletion_requested:
            manifest = record.manifest.model_copy(update={'deletion_requested': True})
            self._persist(manifest)
            record.manifest = manifest
        self._evict(record)
        return self._purge(record)
