"""Typed metadata for managed uploads, reviewed mappings and prepared snapshots."""
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, StrictBool, StrictInt

from .contracts import (ContractModel, Count, DataQuality, FileQuestionResponse,
                        ImportSettings, Number, PositiveInt, Source, SourceField, Text)


DatasetId = Annotated[str, Field(pattern=r'^[0-9a-f]{32}$')]
Fingerprint = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]


class ReaderOptions(ContractModel):
    delimiter: Annotated[str, Field(min_length=1, max_length=1)] = ','
    encoding: Text = 'utf-8-sig'
    sheet: StrictInt | Text = 0


class UploadedSource(Source):
    size_bytes: PositiveInt


class DatasetInfo(ContractModel):
    schema_version: Literal[1] = 1
    dataset_id: DatasetId
    source: UploadedSource
    uploaded_at: AwareDatetime
    status: Literal['uploaded', 'mapped', 'ready', 'needs_attention']
    latest_mapping_revision: PositiveInt | None
    latest_preparation_revision: PositiveInt | None
    expires_at: AwareDatetime | None = None


class PreviewInfo(ContractModel):
    schema_version: Literal[1] = 1
    dataset_id: DatasetId
    source: UploadedSource
    reader_options: ReaderOptions
    rows: Count
    columns: list[Text]
    preview: list[dict[str, StrictBool | Number | str | None]]
    suggestions: dict[SourceField, Text]
    ambiguous: dict[SourceField, list[Text]]
    unmapped: list[Text]
    requires_review: Literal[True]
    note: Text


class DatasetDetail(DatasetInfo):
    """Source preview and fields validated by the current approved preparation."""
    preview: PreviewInfo
    preparation_id: DatasetId | None
    available_fields: list[SourceField]
    quality: DataQuality | None


class MappingInfo(ContractModel):
    dataset_id: DatasetId
    revision: PositiveInt
    config_sha256: Fingerprint
    config: ImportSettings
    approved_at: AwareDatetime


class PreparedInfo(ContractModel):
    schema_version: Literal[1] = 1
    dataset_id: DatasetId
    preparation_id: DatasetId
    revision: PositiveInt
    mapping_revision: PositiveInt
    source: UploadedSource
    config_sha256: Fingerprint
    preparer_version: Text
    preparation_key: Fingerprint
    created_at: AwareDatetime
    quality: DataQuality


class PreparedAnswer(ContractModel):
    schema_version: Literal[1] = 1
    dataset_id: DatasetId
    preparation_id: DatasetId
    preparation_revision: PositiveInt
    result: FileQuestionResponse


class ReportInfo(ContractModel):
    dataset_id: DatasetId
    report_id: DatasetId
    preparation_id: DatasetId
    created_at: AwareDatetime
    size_bytes: PositiveInt
    media_type: Literal['application/json', 'text/html'] = 'application/json'


class StoreManifest(ContractModel):
    """Private server metadata. No pickle or executable frame serialization."""
    schema_version: Literal[1]
    dataset_id: DatasetId
    source: UploadedSource
    source_mtime_ns: Count
    uploaded_at: AwareDatetime
    mappings: list[MappingInfo] = Field(default_factory=list)
    preparations: list[PreparedInfo] = Field(default_factory=list)
    owner_session_id: DatasetId | None = None
    expires_at: AwareDatetime | None = None
    deletion_requested: StrictBool = False
    reports: list[ReportInfo] = Field(default_factory=list)
