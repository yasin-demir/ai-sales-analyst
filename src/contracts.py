"""Version-one question contracts, shared by the file bridge and a future API.

Strict Python/JSON scalar types, closed objects, explicit analysis variants and
finite numbers. These models do not perform analysis or define HTTP behavior.
"""
from datetime import date
import re
from typing import Annotated, Literal, Union

from pydantic import (AfterValidator, BaseModel, ConfigDict, Field, StrictBool,
                      StrictInt, TypeAdapter, field_validator, model_validator)


Analysis = Literal['summary', 'segment_performance', 'period_comparison',
                   'monthly_trend', 'loss_concentration', 'discount_comparison']
Dimension = Literal['category', 'region', 'product_id', 'product', 'order_id', 'currency']
GroupDimension = Literal['category', 'region', 'product_id', 'product', 'order_id']
SourceField = Literal['sales', 'profit', 'date', 'product', 'product_id', 'category',
                      'region', 'order_id', 'quantity', 'discount', 'currency']
Metric = Literal['sales', 'records', 'sales_share_percent', 'profit', 'gross_loss',
                 'positive_profit', 'loss_records', 'margin_percent',
                 'loss_record_rate_percent', 'gross_loss_share_percent']
Status = Literal['ok', 'partial', 'empty', 'empty_ranking', 'unavailable', 'insufficient_data']
PartitionStatus = Literal['ok', 'empty', 'empty_ranking', 'unavailable', 'insufficient_data']
Number = Union[StrictInt, Annotated[float, Field(strict=True, allow_inf_nan=False)]]
Count = Annotated[int, Field(strict=True, ge=0)]
PositiveInt = Annotated[int, Field(strict=True, gt=0)]


def _nonblank(value):
    if not value.strip():
        raise ValueError('A nonblank string is required.')
    return value


def _iso_date(value):
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('Date bounds must use YYYY-MM-DD.')
    date.fromisoformat(value)
    return value


def _month(value):
    if not re.fullmatch(r'\d{4}-\d{2}', value):
        raise ValueError('Month must use YYYY-MM.')
    date.fromisoformat(value + '-01')
    return value


Text = Annotated[str, Field(min_length=1, pattern=r'\S'), AfterValidator(_nonblank)]
ISODate = Annotated[str, Field(pattern=r'^\d{4}-\d{2}-\d{2}$', json_schema_extra={'format': 'date'}),
                    AfterValidator(_iso_date)]
Month = Annotated[str, Field(pattern=r'^\d{4}-(0[1-9]|1[0-2])$'), AfterValidator(_month)]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False,
                              validate_default=True)

    @field_validator('schema_version', mode='before', check_fields=False)
    @classmethod
    def strict_version(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError('schema_version must be the integer 1.')
        return value


class DateRange(ContractModel):
    start: ISODate | None = None
    end: ISODate | None = None

    @model_validator(mode='after')
    def ordered(self):
        if self.start and self.end and self.start > self.end:
            raise ValueError('Start date must not exceed end date.')
        return self


class FilterSpec(DateRange):
    segments: dict[Dimension, Text | Annotated[list[Text], Field(min_length=1)]] = Field(default_factory=dict)


class ResolvedFilters(DateRange):
    start: ISODate | None
    end: ISODate | None
    segments: dict[Dimension, Annotated[list[Text], Field(min_length=1)]]


class ResponseDates(DateRange):
    start: ISODate | None
    end: ISODate | None


class ComparisonWindows(ContractModel):
    baseline_start: ISODate
    baseline_end: ISODate
    current_start: ISODate
    current_end: ISODate

    @model_validator(mode='after')
    def ordered(self):
        if not self.baseline_start <= self.baseline_end < self.current_start <= self.current_end:
            raise ValueError('Comparison windows must be ordered and nonoverlapping.')
        return self


class BaseRequest(ContractModel):
    filters: FilterSpec | None = None


class SummaryRequest(BaseRequest):
    analysis: Literal['summary']


class SegmentRequest(BaseRequest):
    analysis: Literal['segment_performance']
    by: GroupDimension | Annotated[list[GroupDimension], Field(min_length=1)]
    sort_by: Metric | None = None
    ascending: StrictBool | None = None
    limit: PositiveInt = 10
    min_records: PositiveInt = 1

    @field_validator('by')
    @classmethod
    def unique_dimensions(cls, value):
        if isinstance(value, list) and len(value) != len(set(value)):
            raise ValueError('Grouping dimensions must be unique.')
        return value


class ComparisonRequest(BaseRequest):
    analysis: Literal['period_comparison']
    comparison: ComparisonWindows

    @model_validator(mode='after')
    def separate_windows(self):
        if self.filters and (self.filters.start or self.filters.end):
            raise ValueError('Comparison windows cannot be combined with date filters.')
        return self


class MonthlyRequest(BaseRequest):
    analysis: Literal['monthly_trend']


class LossRequest(BaseRequest):
    analysis: Literal['loss_concentration']
    limit: PositiveInt = 10


class DiscountRequest(BaseRequest):
    analysis: Literal['discount_comparison']


QuestionRequest = Annotated[Union[SummaryRequest, SegmentRequest, ComparisonRequest,
                                 MonthlyRequest, LossRequest, DiscountRequest],
                            Field(discriminator='analysis')]
REQUEST_ADAPTER = TypeAdapter(QuestionRequest)


def parse_question_request(value):
    return REQUEST_ADAPTER.validate_python(value)


class Scope(ContractModel):
    input_records: Count
    selected_records: Count
    input_observed_dates: ResponseDates
    selected_observed_dates: ResponseDates
    requested_dates: ResponseDates
    comparison_window_records: Count | None = None
    raw_input_records: Count | None = None
    explicitly_excluded_records: Count | None = None

    @model_validator(mode='after')
    def counts(self):
        if self.selected_records > self.input_records:
            raise ValueError('Selected records cannot exceed input records.')
        if self.comparison_window_records is not None and self.comparison_window_records > self.selected_records:
            raise ValueError('Comparison records cannot exceed selected records.')
        return self


class Units(ContractModel):
    amount: Text
    rates: Literal['percent']
    margin_change: Literal['percentage_points']
    counts: Literal['records']


class Definitions(ContractModel):
    margin_percent: Text
    gross_loss: Text
    share_denominator: Text


class EmptyMetrics(ContractModel):
    pass


class SummaryMetrics(ContractModel):
    records: Count
    sales: Number | None
    profit: Number | None = None
    gross_loss: Number | None = None
    positive_profit: Number | None = None
    loss_records: Count | None = None
    loss_record_rate_percent: Number | None = None
    margin_percent: Number | None = None
    unique_order_ids: Count | None = None
    average_order_value: Number | None = None

    @model_validator(mode='after')
    def available_metric_groups(self):
        groups = [
            {'profit', 'gross_loss', 'positive_profit', 'loss_records', 'loss_record_rate_percent', 'margin_percent'},
            {'unique_order_ids', 'average_order_value'},
        ]
        for group in groups:
            present = group.intersection(self.model_fields_set)
            if present and present != group:
                raise ValueError('Available financial/order metric groups must be complete.')
        if self.loss_records is not None and self.loss_records > self.records:
            raise ValueError('Loss record count cannot exceed record count.')
        if self.unique_order_ids is not None and self.unique_order_ids > self.records:
            raise ValueError('Distinct order count cannot exceed record count.')
        return self


class SegmentRecord(ContractModel):
    category: Text | None = None
    region: Text | None = None
    product_id: Text | None = None
    product: Text | None = None
    order_id: Text | None = None
    sales: Number | None
    records: Count
    sales_share_percent: Number | None
    profit: Number | None = None
    gross_loss: Number | None = None
    positive_profit: Number | None = None
    loss_records: Count | None = None
    margin_percent: Number | None = None
    loss_record_rate_percent: Number | None = None
    gross_loss_share_percent: Number | None = None


class Ranking(ContractModel):
    by: Annotated[list[GroupDimension], Field(min_length=1)]
    sort_by: Metric
    ascending: StrictBool
    limit: PositiveInt
    min_records: PositiveInt
    total_groups: Count
    eligible_groups: Count
    returned_groups: Count

    @model_validator(mode='after')
    def counts(self):
        if not self.returned_groups <= self.eligible_groups <= self.total_groups or self.returned_groups > self.limit:
            raise ValueError('Inconsistent ranking counts.')
        if len(self.by) != len(set(self.by)):
            raise ValueError('Grouping dimensions must be unique.')
        return self


class MonthlyRecord(SummaryMetrics):
    month: Month


class PeriodInfo(DateRange):
    start: ISODate
    end: ISODate
    calendar_days: PositiveInt
    records: Count

    @model_validator(mode='after')
    def days(self):
        if self.calendar_days != (date.fromisoformat(self.end) - date.fromisoformat(self.start)).days + 1:
            raise ValueError('Calendar days must match the period boundaries.')
        return self


class Periods(ContractModel):
    baseline: PeriodInfo
    current: PeriodInfo

    @model_validator(mode='after')
    def ordered(self):
        if self.baseline.end >= self.current.start:
            raise ValueError('Response periods cannot overlap.')
        return self


class ComparisonMetrics(ContractModel):
    baseline: SummaryMetrics
    current: SummaryMetrics


class ComparisonRecord(ContractModel):
    metric: Literal['sales', 'profit', 'gross_loss', 'records', 'unique_order_ids',
                    'average_order_value', 'margin_percent']
    baseline: Number | None
    current: Number | None
    absolute_change: Number | None
    relative_change_percent: Number | None
    margin_change_pp: Number | None
    relative_change_note: Text | None

    @model_validator(mode='after')
    def rate_units(self):
        if self.metric == 'margin_percent':
            if self.relative_change_percent is not None:
                raise ValueError('Margin changes use percentage points, not relative percent.')
        elif self.margin_change_pp is not None:
            raise ValueError('Only margin changes use percentage points.')
        if (self.baseline is None or self.baseline <= 0) and self.relative_change_percent is not None:
            raise ValueError('Relative change is undefined for nonpositive/missing baselines.')
        return self


class LossMetrics(ContractModel):
    total_gross_loss: Number
    selected_product_groups: Count
    top_groups_returned: Count
    top_gross_loss: Number
    top_share_percent: Number
    outside_top_gross_loss: Number


class LossRecord(ContractModel):
    product_id: Text | None = None
    product: Text | None = None
    gross_loss: Number
    share_percent: Number
    cumulative_share_percent: Number

    @model_validator(mode='after')
    def product_key(self):
        if self.product_id is None and self.product is None:
            raise ValueError('A loss record must identify a product.')
        return self


class DiscountRecord(ContractModel):
    profit_status: Literal['Loss', 'Positive profit', 'Break-even']
    records: Count
    mean_discount_percent: Number
    median_discount_percent: Number
    q1_discount_percent: Number
    q3_discount_percent: Number


class BaseResult(ContractModel):
    schema_version: Literal[1]
    status: PartitionStatus
    scope: Scope
    filters: ResolvedFilters
    units: Units
    warnings: list[Text]
    definitions: Definitions

    @model_validator(mode='after')
    def status_shape(self):
        if self.status == 'empty_ranking' and self.analysis != 'segment_performance':
            raise ValueError('empty_ranking applies only to segment performance.')
        if self.status == 'insufficient_data' and self.analysis != 'period_comparison':
            raise ValueError('insufficient_data applies only to period comparison.')
        if self.status in ('empty', 'unavailable', 'insufficient_data'):
            if not isinstance(self.metrics, EmptyMetrics) or self.records:
                raise ValueError('An unavailable/empty comparison cannot contain computed values.')
        elif self.analysis != 'discount_comparison' and isinstance(self.metrics, EmptyMetrics):
            raise ValueError('Computed metrics are required for this status.')
        if self.status == 'empty' and self.scope.selected_records != 0:
            raise ValueError('An empty selection must have zero selected records.')
        if self.status in ('ok', 'empty_ranking', 'insufficient_data') and self.scope.selected_records == 0:
            raise ValueError('This status requires selected records.')
        if isinstance(self.metrics, SummaryMetrics) and self.metrics.records != self.scope.selected_records:
            raise ValueError('Summary record count must match the selected scope.')
        return self


class SummaryResult(BaseResult):
    analysis: Literal['summary']
    metrics: SummaryMetrics | EmptyMetrics
    records: Annotated[list[SummaryMetrics], Field(max_length=0)]


class SegmentResult(BaseResult):
    analysis: Literal['segment_performance']
    metrics: SummaryMetrics | EmptyMetrics
    records: list[SegmentRecord]
    ranking: Ranking | None = None

    @model_validator(mode='after')
    def grouping(self):
        if self.status in ('ok', 'empty_ranking'):
            if self.ranking is None or self.ranking.returned_groups != len(self.records):
                raise ValueError('Ranking metadata must match returned records.')
            dimensions = {'category', 'region', 'product_id', 'product', 'order_id'}
            for record in self.records:
                if dimensions.intersection(record.model_fields_set) != set(self.ranking.by):
                    raise ValueError('Record dimensions must match ranking.by.')
                if any(getattr(record, name) is None for name in self.ranking.by):
                    raise ValueError('Ranked dimensions cannot be null.')
            if self.status == 'empty_ranking' and self.records:
                raise ValueError('An empty ranking cannot have records.')
        return self


class ComparisonResult(BaseResult):
    analysis: Literal['period_comparison']
    metrics: ComparisonMetrics | EmptyMetrics
    records: list[ComparisonRecord]
    periods: Periods | None = None

    @model_validator(mode='after')
    def comparison_shape(self):
        if self.status in ('ok', 'insufficient_data') and self.periods is None:
            raise ValueError('Comparison period metadata is required.')
        if len({r.metric for r in self.records}) != len(self.records):
            raise ValueError('Comparison metrics cannot be repeated.')
        if self.status == 'ok' and not self.records:
            raise ValueError('A successful comparison needs metric records.')
        return self


class MonthlyResult(BaseResult):
    analysis: Literal['monthly_trend']
    metrics: SummaryMetrics | EmptyMetrics
    records: list[MonthlyRecord]

    @model_validator(mode='after')
    def month_order(self):
        months = [record.month for record in self.records]
        if months != sorted(set(months)):
            raise ValueError('Monthly records must be unique and chronological.')
        if self.status == 'ok' and not self.records:
            raise ValueError('A successful monthly series needs records.')
        return self


class LossResult(BaseResult):
    analysis: Literal['loss_concentration']
    metrics: LossMetrics | EmptyMetrics
    records: list[LossRecord]

    @model_validator(mode='after')
    def record_count(self):
        if isinstance(self.metrics, LossMetrics) and self.metrics.top_groups_returned != len(self.records):
            raise ValueError('Loss ranking count must match its records.')
        return self


class DiscountResult(BaseResult):
    analysis: Literal['discount_comparison']
    metrics: EmptyMetrics
    records: list[DiscountRecord]

    @model_validator(mode='after')
    def unique_outcomes(self):
        outcomes = [record.profit_status for record in self.records]
        if len(set(outcomes)) != len(outcomes):
            raise ValueError('Discount outcomes cannot be repeated.')
        if self.status == 'ok' and not self.records:
            raise ValueError('A successful discount analysis needs records.')
        return self


class BaseResponse(ContractModel):
    schema_version: Literal[1]
    question: Text
    status: Status

    @model_validator(mode='after')
    def overall_status(self):
        statuses = {result.status for result in self.results}
        expected = ('ok' if statuses == {'ok'} else 'partial' if 'ok' in statuses else
                    next(iter(statuses)) if len(statuses) == 1 else 'insufficient_data')
        if self.status != expected:
            raise ValueError('Envelope status does not match currency result statuses.')
        amounts = [result.units.amount for result in self.results]
        if len(amounts) != len(set(amounts)):
            raise ValueError('Currency partitions must have distinct unit labels.')
        return self


class SummaryResponse(BaseResponse):
    analysis: Literal['summary']
    results: Annotated[list[SummaryResult], Field(min_length=1)]


class SegmentResponse(BaseResponse):
    analysis: Literal['segment_performance']
    results: Annotated[list[SegmentResult], Field(min_length=1)]


class ComparisonResponse(BaseResponse):
    analysis: Literal['period_comparison']
    results: Annotated[list[ComparisonResult], Field(min_length=1)]


class MonthlyResponse(BaseResponse):
    analysis: Literal['monthly_trend']
    results: Annotated[list[MonthlyResult], Field(min_length=1)]


class LossResponse(BaseResponse):
    analysis: Literal['loss_concentration']
    results: Annotated[list[LossResult], Field(min_length=1)]


class DiscountResponse(BaseResponse):
    analysis: Literal['discount_comparison']
    results: Annotated[list[DiscountResult], Field(min_length=1)]


QuestionResponse = Annotated[Union[SummaryResponse, SegmentResponse, ComparisonResponse,
                                  MonthlyResponse, LossResponse, DiscountResponse],
                             Field(discriminator='analysis')]
RESPONSE_ADAPTER = TypeAdapter(QuestionResponse)


def parse_question_response(value):
    return RESPONSE_ADAPTER.validate_python(value)


class Source(ContractModel):
    file: Text
    sha256: Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]


class ImportSettings(ContractModel):
    mapping: dict[SourceField, Text]
    mapping_reviewed: StrictBool
    delimiter: Annotated[str, Field(min_length=1, max_length=1)]
    encoding: Text
    decimal: Literal['.', ',']
    thousands: Literal['.', ',', ' '] | None
    date_format: Text
    discount_unit: Literal['fraction', 'percent']
    currency: Text | None
    allow_returns: StrictBool
    invalid_rows: Literal['stop', 'exclude']
    sheet: StrictInt | Text

    @model_validator(mode='after')
    def reviewed_mapping(self):
        if not self.mapping_reviewed or 'sales' not in self.mapping:
            raise ValueError('A file answer requires a reviewed sales mapping.')
        if len(set(self.mapping.values())) != len(self.mapping):
            raise ValueError('Source columns cannot have multiple meanings.')
        if self.decimal == self.thousands:
            raise ValueError('Decimal and thousands separators must differ.')
        return self


class QualityIssue(ContractModel):
    severity: Literal['warning', 'error']
    field: Text
    data_record: PositiveInt | None
    message: Text


class Capability(ContractModel):
    available: StrictBool
    missing_or_invalid_fields: list[Text]


class Capabilities(ContractModel):
    sales_summary: Capability
    time_sales: Capability
    product_sales: Capability
    category_sales: Capability
    region_sales: Capability
    profitability: Capability
    product_losses: Capability
    discount_comparison: Capability


class DataQuality(ContractModel):
    status: Literal['ready', 'needs_attention']
    issues: list[QualityIssue]
    capabilities: Capabilities
    input_rows: Count
    analyzed_rows: Count
    excluded_records: list[PositiveInt]
    duplicate_records_retained: Count
    blocked_fields: list[SourceField]

    @model_validator(mode='after')
    def counts(self):
        excluded = self.excluded_records
        if len(set(excluded)) != len(excluded) or any(row > self.input_rows for row in excluded):
            raise ValueError('Excluded record numbers must be unique and within the source.')
        expected = self.input_rows - len(excluded) if self.status == 'ready' else 0
        if self.analyzed_rows != expected:
            raise ValueError('Analyzed row count does not match quality status/exclusions.')
        return self


class FileQuestionResponse(ContractModel):
    schema_version: Literal[1]
    source: Source
    import_config: ImportSettings
    quality: DataQuality
    status: Literal['ok', 'partial', 'empty', 'empty_ranking', 'unavailable',
                    'insufficient_data', 'needs_attention']
    answer: QuestionResponse | None

    @model_validator(mode='after')
    def readiness(self):
        if self.quality.status == 'needs_attention':
            if self.answer is not None or self.status != 'needs_attention':
                raise ValueError('Unready data cannot contain an analytical answer.')
        elif self.answer is None or self.status != self.answer.status:
            raise ValueError('Ready data must carry an answer with a matching status.')
        return self


class ContractViolation(RuntimeError):
    """An internally generated response failed its contract, not a bad request."""
