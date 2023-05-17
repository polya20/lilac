"""Tests for dataset.stats()."""

from typing import Any, cast

import pytest
from pytest_mock import MockerFixture

from ..schema import UUID_COLUMN, Item, schema
from . import dataset_duckdb
from .dataset import StatsResult
from .dataset_test_utils import TestDataMaker

SIMPLE_ITEMS: list[Item] = [{
  UUID_COLUMN: '1',
  'str': 'a',
  'int': 1,
  'bool': False,
  'float': 3.0
}, {
  UUID_COLUMN: '2',
  'str': 'b',
  'int': 2,
  'bool': True,
  'float': 2.0
}, {
  UUID_COLUMN: '3',
  'str': 'b',
  'int': 2,
  'bool': True,
  'float': 1.0
}]


def test_simple_stats(make_test_data: TestDataMaker) -> None:
  dataset = make_test_data(SIMPLE_ITEMS)

  result = dataset.stats(leaf_path='str')
  assert result == StatsResult(total_count=3, approx_count_distinct=2, avg_text_length=1)

  result = dataset.stats(leaf_path='float')
  assert result == StatsResult(total_count=3, approx_count_distinct=3, min_val=1.0, max_val=3.0)

  result = dataset.stats(leaf_path='bool')
  assert result == StatsResult(total_count=3, approx_count_distinct=2)

  result = dataset.stats(leaf_path='int')
  assert result == StatsResult(total_count=3, approx_count_distinct=2, min_val=1, max_val=2)


def test_nested_stats(make_test_data: TestDataMaker) -> None:
  nested_items: list[Item] = [
    {
      'name': 'Name1',
      'addresses': [{
        'zips': [5, 8]
      }]
    },
    {
      'name': 'Name2',
      'addresses': [{
        'zips': [3]
      }, {
        'zips': [11, 8]
      }]
    },
    {
      'name': 'Name2',
      'addresses': []
    },  # No addresses.
    {
      'name': 'Name2',
      'addresses': [{
        'zips': []
      }]
    }  # No zips in the first address.
  ]
  nested_schema = schema({
    UUID_COLUMN: 'string',
    'name': 'string',
    'addresses': [{
      'zips': ['int32']
    }]
  })
  dataset = make_test_data(nested_items, schema=nested_schema)

  result = dataset.stats(leaf_path='name')
  assert result == StatsResult(total_count=4, approx_count_distinct=2, avg_text_length=5)

  result = dataset.stats(leaf_path='addresses.*.zips.*')
  assert result == StatsResult(total_count=5, approx_count_distinct=4, min_val=3, max_val=11)


def test_stats_approximation(make_test_data: TestDataMaker, mocker: MockerFixture) -> None:
  sample_size = 5
  mocker.patch(f'{dataset_duckdb.__name__}.SAMPLE_SIZE_DISTINCT_COUNT', sample_size)

  nested_items: list[Item] = [{'feature': str(i)} for i in range(sample_size * 10)]
  nested_schema = schema({UUID_COLUMN: 'string', 'feature': 'string'})
  dataset = make_test_data(nested_items, schema=nested_schema)

  result = dataset.stats(leaf_path='feature')
  assert result == StatsResult(total_count=50, approx_count_distinct=50, avg_text_length=1)


def test_error_handling(make_test_data: TestDataMaker) -> None:
  dataset = make_test_data(SIMPLE_ITEMS)

  with pytest.raises(ValueError, match='leaf_path must be provided'):
    dataset.stats(cast(Any, None))

  with pytest.raises(ValueError, match='Leaf "\\(\'unknown\',\\)" not found in dataset'):
    dataset.stats(leaf_path='unknown')