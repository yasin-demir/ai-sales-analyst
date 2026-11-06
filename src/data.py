"""Loading and explicit validation for the Superstore analytical contract."""
from pathlib import Path
import numpy as np
import pandas as pd

REQUIRED_COLUMNS = (
    'Row ID', 'Order ID', 'Order Date', 'Ship Date', 'Product ID', 'Product Name',
    'Category', 'Sub-Category', 'Region', 'State', 'Postal Code',
    'Sales', 'Profit', 'Quantity', 'Discount',
)
IDENTIFIERS = ('Row ID', 'Order ID', 'Product ID', 'Product Name', 'Category', 'Sub-Category', 'Region', 'State', 'Postal Code')

def read_sales_csv(path: str | Path) -> pd.DataFrame:
    """Read raw CSV, preserving postal codes; validation is a separate explicit step."""
    for encoding in ('utf-8-sig', 'cp1252', 'latin1'):
        try:
            raw = pd.read_csv(path, encoding=encoding, dtype={'Postal Code': 'string'})
            return raw
        except UnicodeDecodeError:
            continue
    raise ValueError('The CSV encoding could not be read.')

def load_sales(path: str | Path) -> pd.DataFrame:
    """Return a fully validated copy of the local sales extract."""
    return validate_sales(read_sales_csv(path))

def validate_sales(frame: pd.DataFrame) -> pd.DataFrame:
    """Return a validated copy. No deduplication, imputation, or silent row removal.

    Sales and integer quantities must be positive; discounts are fractions in [0,1].
    Profit may be negative or zero. Dates use US month/day/year when supplied as text.
    Multiple names per Product ID remain separate composite product groups.
    """
    missing = sorted(set(REQUIRED_COLUMNS) - set(frame.columns))
    if missing:
        raise ValueError(f'Missing required columns: {", ".join(missing)}')
    if frame.empty:
        raise ValueError('The sales dataset contains no order lines.')
    result = frame.copy(deep=True)
    for column in IDENTIFIERS:
        values = result[column].astype('string')
        if (values.isna() | values.str.strip().eq('')).any():
            raise ValueError(f'{column}: missing or blank identifiers are not allowed.')
    if result['Row ID'].duplicated().any():
        raise ValueError('Row ID must be unique; no duplicate rows were removed.')
    for column in ('Sales', 'Profit', 'Quantity', 'Discount'):
        try:
            result[column] = pd.to_numeric(result[column], errors='raise')
        except (ValueError, TypeError) as exc:
            raise ValueError(f'{column}: expected numeric values.') from exc
        if not np.isfinite(result[column].to_numpy(dtype=float)).all():
            raise ValueError(f'{column}: missing or non-finite numeric values.')
    if result['Sales'].le(0).any():
        raise ValueError('Sales must be positive under this dataset contract.')
    if (result['Quantity'].le(0) | result['Quantity'].mod(1).ne(0)).any():
        raise ValueError('Quantity must contain positive integers.')
    if not result['Discount'].between(0, 1).all():
        raise ValueError('Discount must be a fraction between 0 and 1.')
    for column in ('Order Date', 'Ship Date'):
        try:
            if not pd.api.types.is_datetime64_any_dtype(result[column]):
                result[column] = pd.to_datetime(result[column], format='%m/%d/%Y', errors='raise')
        except (ValueError, TypeError) as exc:
            raise ValueError(f'{column}: expected valid month/day/year dates.') from exc
        if result[column].isna().any():
            raise ValueError(f'{column}: dates may not be missing.')
    if result['Ship Date'].lt(result['Order Date']).any():
        raise ValueError('Ship Date may not precede Order Date.')
    result['Postal Code'] = result['Postal Code'].astype('string').str.zfill(5)
    return result
