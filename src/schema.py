"""Item: an individual entry in the dataset."""

from collections import deque
from datetime import datetime
from enum import Enum
from typing import Any, Optional, Union, cast

import numpy as np
import pyarrow as pa
from pydantic import (
    BaseModel,
    Field as pydantic_Field,
    validator,
)

MANIFEST_FILENAME = 'manifest.json'
PARQUET_FILENAME_PREFIX = 'data'

# We choose `__rowid__` inspired by the standard `rowid` pseudocolumn in DBs:
# https://docs.oracle.com/cd/B19306_01/server.102/b14200/pseudocolumns008.htm
UUID_COLUMN = '__rowid__'
PATH_WILDCARD = '*'

TEXT_SPAN_START_FEATURE = 'start'
TEXT_SPAN_END_FEATURE = 'end'

# Python doesn't work with recursive types. These types provide some notion of type-safety.
Scalar = Union[bool, datetime, int, float, str, bytes]
ItemValue = Union[dict, list, np.ndarray, Scalar]
Item = dict[str, ItemValue]
RowKeyedItem = tuple[bytes, Item]
SignalOut = Union[ItemValue, Item]

# Contains a string field name, a wildcard for repeateds, or a specific integer index for repeateds.
# This path represents a path to a particular column.
# Examples:
#  ['article', 'field'] represents {'article': {'field': VALUES}}
#  ['article', '*', 'field'] represents {'article': [{'field': VALUES}, {'field': VALUES}]}
#  ['article', 0, 'field'] represents {'article': [{'field': VALUES}, {'field': UNRELATED}]}
PathTuple = tuple[Union[str, int], ...]
Path = Union[str, PathTuple]

PathKeyedItem = tuple[Path, Item]

# These fields are for for python only and not written to a schema.
RichData = Union[str, bytes]
RowKeyedPath = tuple[bytes, Path]
PathKeyedRichData = tuple[Path, Union[str, bytes]]
RowPathKeyedItem = tuple[RowKeyedPath, Item]
# The str is the signal name.
PathKeyedSignalItem = tuple[str, Path, Item]


class DataType(str, Enum):
  """Enum holding the dtype for a field."""
  STRING = 'string'
  # Contains {start, end} offset integers with a reference_column.
  STRING_SPAN = 'string_span'
  BOOLEAN = 'boolean'

  # Ints.
  INT8 = 'int8'
  INT16 = 'int16'
  INT32 = 'int32'
  INT64 = 'int64'
  UINT8 = 'uint8'
  UINT16 = 'uint16'
  UINT32 = 'uint32'
  UINT64 = 'uint64'

  # Floats.
  FLOAT16 = 'float16'
  FLOAT32 = 'float32'
  FLOAT64 = 'float64'

  ### Time ###
  # Time of day (no time zone).
  TIME = 'time'
  # Calendar date (year, month, day), no time zone.
  DATE = 'date'
  # An "Instant" stored as number of microseconds (µs) since 1970-01-01 00:00:00+00 (UTC time zone).
  TIMESTAMP = 'timestamp'
  # Time span, stored as microseconds.
  INTERVAL = 'interval'

  STRUCT = 'struct'
  LIST = 'list'
  BINARY = 'binary'

  def __repr__(self) -> str:
    return self.value


class EnrichmentType(str, Enum):
  """Enum holding the enrichment type for a signal."""
  TEXT = 'text'
  IMAGE = 'image'

  def __repr__(self) -> str:
    return self.value


class Field(BaseModel):
  """Holds information for a field in the schema."""
  repeated_field: Optional['Field']
  fields: Optional[dict[str, 'Field']]
  dtype: Optional[DataType]
  enriched: Optional[bool]
  # When defined, this field points to another column.
  refers_to: Optional[Path]

  @validator('fields')
  def either_fields_or_repeated_field_is_defined(cls, fields: dict[str, 'Field'],
                                                 values: dict[str, Any]) -> dict[str, 'Field']:
    """Error if both `fields` and `repeated_fields` are defined."""
    if fields and values.get('repeated_field'):
      raise ValueError('Both "fields" and "repeated_field" should not be defined')
    return fields

  @validator('refers_to', always=True)
  def refers_to_is_defined_for_string_spans(cls, refers_to: Optional[Path],
                                             values: dict[str, Any]) -> Optional[Path]:
    """Error if both `fields` and `repeated_fields` are defined."""
    if values.get('dtype') == DataType.STRING_SPAN and refers_to is None:
      raise ValueError('refers_to must be defined for DataType.STRING_SPAN')
    return refers_to

  @validator('dtype', always=True)
  def infer_default_dtype(cls, dtype: Optional[DataType], values: dict[str, Any]) -> DataType:
    """Infers the default value for dtype if not explicitly provided."""
    if dtype:
      if values.get('fields') and dtype != DataType.STRUCT:
        raise ValueError('dtype needs to be STRUCT when fields is defined')
      if values.get('repeated_field') and dtype != DataType.LIST:
        raise ValueError('dtype needs to be LIST when repeated_field is defined')
      return dtype
    elif values.get('repeated_field'):
      return DataType.LIST
    elif values.get('fields'):
      return DataType.STRUCT
    else:
      raise ValueError('"dtype" is required when both "repeated_field" and "fields" are not set')

  def __str__(self) -> str:
    return _str_field(self, indent=0)

  def __repr__(self) -> str:
    return f' {self.__class__.__name__}::{self.json(exclude_none=True, indent=2)}'


class Schema(BaseModel):
  """Database schema."""
  fields: dict[str, Field]
  # We exclude the computed property `leafs` from the dict() and json() serialization.
  leafs: dict[PathTuple, Field] = pydantic_Field(exclude=True, default={})

  @validator('leafs', pre=True, always=True)
  def compute_leafs(cls, leafs: dict[PathTuple, Field],
                    values: dict[str, Any]) -> dict[PathTuple, Field]:
    """Return all the leaf fields in the schema (a leaf holds a primitive value)."""
    if leafs:
      return leafs
    fields = cast(dict[str, Field], values.get('fields'))
    result: dict[PathTuple, Field] = {}
    q: deque[tuple[PathTuple, Field]] = deque([((), Field(fields=fields))])
    while q:
      path, field = q.popleft()
      if field.dtype == DataType.STRING_SPAN:
        # String spans act as leafs.
        result[path] = field
      elif field.fields:
        for name, child_field in field.fields.items():
          child_path = (*path, name)
          q.append((child_path, child_field))
      elif field.repeated_field:
        child_path = (*path, PATH_WILDCARD)
        q.append((child_path, field.repeated_field))
      else:
        result[path] = field
    return result

  def __str__(self) -> str:
    return _str_fields(self.fields, indent=0)

  def __repr__(self) -> str:
    return self.json(exclude_none=True, indent=2)


def TextSpan(start: int, end: int) -> Item:
  """Return the span item from start and end character offets."""
  return {TEXT_SPAN_START_FEATURE: start, TEXT_SPAN_END_FEATURE: end}


def child_item_from_column_path(item: Item, path: Path) -> Item:
  """Return the last (child) item from a column path."""
  child_item_value = item
  for path_part in path:
    if path_part == PATH_WILDCARD:
      raise ValueError(
          'child_item_from_column_path cannot be called with a path that contains a repeated '
          f'wildcard: "{path}"')
    # path_part can either be an integer or a string for a dictionary, both of which we can
    # directly index with.
    child_item_value = child_item_value[path_part]  # type: ignore
  return child_item_value


def validate_path_against_schema(path: Path, schema: Schema, msg: str) -> None:
  """Raise an error if the path is invalid for this schema."""
  if len(path) == 0:
    raise ValueError(f'The length of path must not be 0. {msg}')

  # Wrap in a field for convenience.
  field = Field(fields=schema.fields)
  for i, path_part in enumerate(path):
    if isinstance(path_part, int) or path_part == PATH_WILDCARD:
      if field.dtype != DataType.LIST:
        raise ValueError(
            f'Path part "{path_part}" for path "{path}" at index {i} represents an list, but the '
            f'field at this path is "{field}". '
            f'{msg}')
      field = cast(Field, field.repeated_field)
    elif isinstance(path_part, str):
      if field.dtype != DataType.STRUCT:
        raise ValueError(
            f'Path part "{path_part}" for path "{path}" at index {i} represents a field of a '
            f'struct, but the field at this path is "{field}". '
            f'{msg}')

      if path_part not in cast(dict, field.fields):
        raise ValueError(f'Path part "{path_part}" for path "{path}" at index {i} represents a '
                         'field of struct that does not exist. Schema field: {field}. {msg}')
      field = cast(dict[str, Field], field.fields)[path_part]

  if field.dtype in (DataType.STRUCT, DataType.LIST):
    raise ValueError(
        f'The path "{path}" specifies {field} but should specify a leaf primitive. {msg}')


def column_paths_match(path_match: Path, specific_path: Path) -> bool:
  """Test whether two column paths match.

  Args:
    path_match: A column path that contains wildcards, and sub-paths. This path will be used for
       testing the second specific path.
    specific_path: A column path that specifically identifies an field.

  Returns
    Whether specific_path matches the path_match. This will only match when the
    paths are equal length. If a user wants to enrich everything with an array, they must use the
    path wildcard '*' in their patch match.
  """
  if isinstance(path_match, str):
    path_match = (path_match,)
  if isinstance(specific_path, str):
    specific_path = (specific_path,)

  if len(path_match) != len(specific_path):
    return False

  for path_match_p, specific_path_p in zip(path_match, specific_path):
    if path_match_p == PATH_WILDCARD:
      continue

    if path_match_p != specific_path_p:
      return False

  return True


def path_to_alias(path: Path) -> str:
  """Convert a path to a string."""
  return '.'.join([str(path_comp) for path_comp in path])


def normalize_path(path: Path) -> PathTuple:
  """Normalize a path."""
  if isinstance(path, str):
    return tuple(path.split('.'))
  return path


def is_repeated_path_part(path_component: Union[str, int]) -> bool:
  """Return True if the path component is a repeated path part."""
  return isinstance(path_component, int) or path_component == PATH_WILDCARD


class ImageInfo(BaseModel):
  """Info about an individual image."""
  path: Path


class SourceManifest(BaseModel):
  """The manifest that describes the dataset run, including schema and parquet files."""
  # List of a parquet filepaths storing the data. The paths can be relative to `manifest.json`.
  files: list[str]
  # The data schema.
  data_schema: Schema

  # Image information for the dataset.
  images: Optional[list[ImageInfo]]


def _str_fields(fields: dict[str, Field], indent: int) -> str:
  prefix = ' ' * indent
  out: list[str] = []
  for name, field in fields.items():
    out.append(f'{prefix}{name}:{_str_field(field, indent=indent + 2)}')
  return '\n'.join(out)


def _str_field(field: Field, indent: int) -> str:
  if field.fields:
    prefix = '\n' if indent > 0 else ''
    return f'{prefix}{_str_fields(field.fields, indent)}'
  if field.repeated_field:
    return f' list({_str_field(field.repeated_field, indent)})'
  return f' {cast(DataType, field.dtype)}'


def dtype_to_arrow_dtype(dtype: DataType) -> pa.DataType:
  """Convert the dtype to an arrow dtype."""
  if dtype == DataType.STRING:
    return pa.string()
  elif dtype == DataType.BOOLEAN:
    return pa.bool_()
  elif dtype == DataType.FLOAT16:
    return pa.float16()
  elif dtype == DataType.FLOAT32:
    return pa.float32()
  elif dtype == DataType.FLOAT64:
    return pa.float64()
  elif dtype == DataType.INT8:
    return pa.int8()
  elif dtype == DataType.INT16:
    return pa.int16()
  elif dtype == DataType.INT32:
    return pa.int32()
  elif dtype == DataType.INT64:
    return pa.int64()
  elif dtype == DataType.UINT8:
    return pa.uint8()
  elif dtype == DataType.UINT16:
    return pa.uint16()
  elif dtype == DataType.UINT32:
    return pa.uint32()
  elif dtype == DataType.UINT64:
    return pa.uint64()
  elif dtype == DataType.BINARY:
    return pa.binary()
  elif dtype == DataType.TIME:
    return pa.time64()
  elif dtype == DataType.DATE:
    return pa.date64()
  elif dtype == DataType.TIMESTAMP:
    return pa.timestamp('us')
  elif dtype == DataType.INTERVAL:
    return pa.duration('us')
  else:
    raise ValueError(f'Can not convert dtype "{dtype}" to arrow dtype')


def schema_to_arrow_schema(schema: Schema) -> pa.Schema:
  """Convert our schema to arrow schema."""
  arrow_schema = cast(pa.Schema, _schema_to_arrow_schema_impl(schema))
  arrow_fields = {field.name: field.type for field in arrow_schema}
  # Since UUID is a fixed size binary of 16 bytes, we mark it as such in the schema -- leads to
  # optimizations by DuckDB and aligns with DuckDB's own UUID dtype.
  if UUID_COLUMN in arrow_fields:
    arrow_fields[UUID_COLUMN] = pa.binary(16)
  return pa.schema(arrow_fields)


def _schema_to_arrow_schema_impl(schema: Union[Schema, Field]) -> Union[pa.Schema, pa.DataType]:
  """Convert a schema to an apache arrow schema."""
  if schema.fields:
    arrow_fields = {
        name: _schema_to_arrow_schema_impl(field) for name, field in schema.fields.items()
    }
    return pa.schema(arrow_fields) if isinstance(schema, Schema) else pa.struct(arrow_fields)
  field = cast(Field, schema)
  if field.repeated_field:
    return pa.list_(_schema_to_arrow_schema_impl(field.repeated_field))
  if field.dtype == DataType.STRING_SPAN:
    return pa.struct({TEXT_SPAN_START_FEATURE: pa.int32(), TEXT_SPAN_END_FEATURE: pa.int32()})
  return dtype_to_arrow_dtype(cast(DataType, field.dtype))


def arrow_dtype_to_dtype(arrow_dtype: pa.DataType) -> DataType:
  """Convert arrow dtype to our dtype."""
  # Ints.
  if arrow_dtype == pa.int8():
    return DataType.INT8
  elif arrow_dtype == pa.int16():
    return DataType.INT16
  elif arrow_dtype == pa.int32():
    return DataType.INT32
  elif arrow_dtype == pa.int64():
    return DataType.INT64
  elif arrow_dtype == pa.uint8():
    return DataType.UINT8
  elif arrow_dtype == pa.uint16():
    return DataType.UINT16
  elif arrow_dtype == pa.uint32():
    return DataType.UINT32
  elif arrow_dtype == pa.uint64():
    return DataType.UINT64
  # Floats.
  elif arrow_dtype == pa.float16():
    return DataType.FLOAT16
  elif arrow_dtype == pa.float32():
    return DataType.FLOAT32
  elif arrow_dtype == pa.float64():
    return DataType.FLOAT64
  # Time.
  elif pa.types.is_time(arrow_dtype):
    return DataType.TIME
  elif pa.types.is_date(arrow_dtype):
    return DataType.DATE
  elif pa.types.is_timestamp(arrow_dtype):
    return DataType.TIMESTAMP
  elif pa.types.is_duration(arrow_dtype):
    return DataType.INTERVAL
  # Others.
  elif arrow_dtype == pa.string():
    return DataType.STRING
  elif pa.types.is_binary(arrow_dtype) or pa.types.is_fixed_size_binary(arrow_dtype):
    return DataType.BINARY
  elif pa.types.is_boolean(arrow_dtype):
    return DataType.BOOLEAN
  else:
    raise ValueError(f'Can not convert arrow dtype "{arrow_dtype}" to our dtype')


def arrow_schema_to_schema(schema: pa.Schema) -> Schema:
  """Convert arrow schema to our schema."""
  return cast(Schema, _arrow_schema_to_schema_impl(schema))


def _arrow_schema_to_schema_impl(schema: Union[pa.Schema, pa.DataType]) -> Union[Schema, Field]:
  """Convert an apache arrow schema to our schema."""
  if isinstance(schema, (pa.Schema, pa.StructType)):
    fields: dict[str, Field] = {
        field.name: cast(Field, _arrow_schema_to_schema_impl(field.type)) for field in schema
    }
    return Schema(fields=fields) if isinstance(schema, pa.Schema) else Field(fields=fields)
  elif isinstance(schema, pa.ListType):
    return Field(repeated_field=cast(Field, _arrow_schema_to_schema_impl(schema.value_field.type)))
  else:
    return Field(dtype=arrow_dtype_to_dtype(schema))


def is_float(dtype: DataType) -> bool:
  """Check if a dtype is a float dtype."""
  return dtype in [DataType.FLOAT16, DataType.FLOAT32, DataType.FLOAT64]


def is_integer(dtype: DataType) -> bool:
  """Check if a dtype is an integer dtype."""
  return dtype in [
      DataType.INT8, DataType.INT16, DataType.INT32, DataType.INT64, DataType.UINT8,
      DataType.UINT16, DataType.UINT32, DataType.UINT64
  ]


def is_temporal(dtype: DataType) -> bool:
  """Check if a dtype is a temporal dtype."""
  return dtype in [DataType.TIME, DataType.DATE, DataType.TIMESTAMP, DataType.INTERVAL]


def is_ordinal(dtype: DataType) -> bool:
  """Check if a dtype is an ordinal dtype."""
  return is_float(dtype) or is_integer(dtype) or is_temporal(dtype)