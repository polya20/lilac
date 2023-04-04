"""Tests for the pandas source."""
import pathlib

import pandas as pd

from ...schema import UUID_COLUMN, Field, Schema
from .pandas_source import PandasSource
from .source import SourceProcessResult


async def test_simple_dataframe(tmp_path: pathlib.Path) -> None:
  df = pd.DataFrame({'name': ['a', 'b', 'c'], 'age': [1, 2, 3]})
  source = PandasSource(df)

  async def shards_loader(shard_infos: list[dict]) -> list[dict]:
    return [source.process_shard(x) for x in shard_infos]

  result = await source.process(str(tmp_path), shards_loader)
  expected_result = SourceProcessResult(data_schema=Schema(
      fields={
          UUID_COLUMN: Field(dtype='binary'),
          'name': Field(dtype='string'),
          'age': Field(dtype='int64')
      }),
                                        num_items=3,
                                        filepaths=[])

  # Validate except for the filepaths, which are not deterministic.
  expected_result.filepaths = result.filepaths
  assert result == expected_result
  assert len(result.filepaths) == 1