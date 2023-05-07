"""Tests the vector store interface."""

from typing import Type

import numpy as np
import pytest

from .vector_store import VectorStore
from .vector_store_numpy import NumpyVectorStore

ALL_STORES = [NumpyVectorStore]


@pytest.mark.parametrize('store_cls', ALL_STORES)
class VectorStoreSuite:

  def test_get_all(self, store_cls: Type[VectorStore]) -> None:
    store = store_cls()

    store.add([('a',), ('b',), ('c',)], np.array([[1, 2], [3, 4], [5, 6]]))

    np.testing.assert_array_equal(
      store.get([('a',), ('b',), ('c',)]), np.array([[1, 2], [3, 4], [5, 6]]))

  def test_get_subset(self, store_cls: Type[VectorStore]) -> None:
    store = store_cls()

    store.add([('a',), ('b',), ('c',)], np.array([[1, 2], [3, 4], [5, 6]]))

    np.testing.assert_array_equal(store.get([('b',), ('c',)]), np.array([[3, 4], [5, 6]]))

  def test_topk(self, store_cls: Type[VectorStore]) -> None:
    store = store_cls()
    embedding = np.array([[0.45, 0.89], [0.6, 0.8], [0.64, 0.77]])
    query = np.array([0.89, 0.45])
    topk = 3
    store.add([('a',), ('b',), ('c',)], embedding)
    result = store.topk(query, topk)
    assert [key for key, _ in result] == [('c',), ('b',), ('a',)]
    assert [score for _, score in result] == pytest.approx([0.9161, 0.894, 0.801], 1e-3)

  def test_topk_with_restricted_keys(self, store_cls: Type[VectorStore]) -> None:
    store = store_cls()
    embedding = np.array([[0.45, 0.89], [0.6, 0.8], [0.64, 0.77]])
    query = np.array([0.89, 0.45])
    topk = 3
    store.add([('a',), ('b',), ('c',)], embedding)
    result = store.topk(query, topk, keys=[('b',), ('a',)])
    assert [key for key, _ in result] == [('b',), ('a',)]
    assert [score for _, score in result] == pytest.approx([0.894, 0.801], 1e-3)

    result = store.topk(query, topk, keys=[('a',), ('b',)])
    assert [key for key, _ in result] == [('b',), ('a',)]
    assert [score for _, score in result] == pytest.approx([0.894, 0.801], 1e-3)

    result = store.topk(query, topk, keys=[('a',), ('c',)])
    assert [key for key, _ in result] == [('c',), ('a',)]
    assert [score for _, score in result] == pytest.approx([0.9161, 0.801], 1e-3)
