"""Utils for the python server."""
import asyncio
import functools
import itertools
import logging
import os
import pathlib
import re
import shutil
import threading
import time
import uuid
from asyncio import AbstractEventLoop
from concurrent.futures import Executor, ThreadPoolExecutor
from datetime import timedelta
from functools import partial, wraps
from typing import IO, Any, Awaitable, Callable, Iterable, Optional, TypeVar, Union

import requests
from google.cloud.storage import Blob, Client
from pydantic import BaseModel

from .config import data_path, env
from .schema import Path

GCS_PROTOCOL = 'gs://'
GCS_REGEX = re.compile(f'{GCS_PROTOCOL}(.*?)/(.*)')
GCS_COPY_CHUNK_SIZE = 1_000
IMAGES_DIR_NAME = 'images'
DATASETS_DIR_NAME = 'datasets'


@functools.cache
def _get_storage_client(thread_id: Optional[int] = None) -> Client:
  # The storage client is not thread safe so we use a thread_id to make sure each thread gets a
  # separate storage client.
  del thread_id
  return Client()


def _parse_gcs_path(filepath: str) -> tuple[str, str]:
  # match a regular expression to extract the bucket and filename
  if matches := GCS_REGEX.match(filepath):
    bucket_name, object_name = matches.groups()
    return bucket_name, object_name
  raise ValueError(f'Failed to parse GCS path: {filepath}')


def _get_gcs_blob(filepath: str) -> Blob:
  bucket_name, object_name = _parse_gcs_path(filepath)
  storage_client = _get_storage_client(threading.get_ident())
  bucket = storage_client.bucket(bucket_name)
  return bucket.blob(object_name)


def open_file(filepath: str, mode: str = 'r') -> IO:
  """Open a file handle. It works with both GCS and local paths."""
  if filepath.startswith(GCS_PROTOCOL):
    blob = _get_gcs_blob(filepath)
    return blob.open(mode)

  write_mode = 'w' in mode
  binary_mode = 'b' in mode

  if write_mode:
    base_path = os.path.dirname(filepath)
    os.makedirs(base_path, exist_ok=True)

  encoding = None if binary_mode else 'utf-8'
  return open(filepath, mode=mode, encoding=encoding)


def download_http_files(filepaths: list[str]) -> list[str]:
  """Download files from HTTP(s) URLs."""
  out_filepaths: list[str] = []
  for filepath in filepaths:
    if filepath.startswith(('http://', 'https://')):
      tmp_filename = uuid.uuid4().hex
      tmp_filepath = f'/tmp/{data_path()}/local_cache/{tmp_filename}'
      log(f'Downloading from url {filepath} to {tmp_filepath}')
      dl = requests.get(filepath, timeout=10000, allow_redirects=True)
      with open_file(tmp_filepath, 'wb') as f:
        f.write(dl.content)
      filepath = tmp_filepath

    out_filepaths.append(filepath)

  return out_filepaths


def makedirs(dir_path: str) -> None:
  """Recursively makes the directories. It works with both GCS and local paths."""
  if dir_path.startswith(GCS_PROTOCOL):
    return
  os.makedirs(dir_path, exist_ok=True)


def get_datasets_dir(base_dir: Union[str, pathlib.Path]) -> str:
  """Return the output directory that holds all datasets."""
  return os.path.join(base_dir, DATASETS_DIR_NAME)


def get_dataset_output_dir(base_dir: Union[str, pathlib.Path], namespace: str,
                           dataset_name: str) -> str:
  """Return the output directory for a dataset."""
  return os.path.join(get_datasets_dir(base_dir), namespace, dataset_name)


class DatasetInfo(BaseModel):
  """Information about a dataset."""
  namespace: str
  dataset_name: str
  description: Optional[str]


def list_datasets(base_dir: Union[str, pathlib.Path]) -> list[DatasetInfo]:
  """List the datasets in a data directory."""
  datasets_path = get_datasets_dir(base_dir)

  # Skip if 'datasets' doesn't exist.
  if not os.path.isdir(datasets_path):
    return []

  dataset_infos: list[DatasetInfo] = []
  for namespace in os.listdir(datasets_path):
    dataset_dir = os.path.join(datasets_path, namespace)
    # Skip if namespace is not a directory.
    if not os.path.isdir(dataset_dir):
      continue
    if namespace.startswith('.'):
      continue

    for dataset_name in os.listdir(dataset_dir):
      # Skip if dataset_name is not a directory.
      dataset_path = os.path.join(dataset_dir, dataset_name)
      if not os.path.isdir(dataset_path):
        continue
      if dataset_name.startswith('.'):
        continue

      dataset_infos.append(DatasetInfo(namespace=namespace, dataset_name=dataset_name))

  return dataset_infos


class CopyRequest(BaseModel):
  """A request to copy a file from source to destination path. Used to copy media files to GCS."""
  from_path: str
  to_path: str


def copy_batch(copy_requests: list[CopyRequest]) -> None:
  """Copy a single item from a CopyRequest."""
  storage_client = _get_storage_client(threading.get_ident())
  with storage_client.batch():
    for copy_request in copy_requests:
      from_gcs = False
      if GCS_REGEX.match(copy_request.from_path):
        from_gcs = True
      to_gcs = False
      if GCS_REGEX.match(copy_request.to_path):
        to_gcs = True

      makedirs(os.path.dirname(copy_request.to_path))

      # When both source and destination are local, use the shutil copy.
      if not from_gcs and not to_gcs:
        shutil.copyfile(copy_request.from_path, copy_request.to_path)
        continue

      if from_gcs:
        from_bucket_name, from_object_name = _parse_gcs_path(copy_request.from_path)
        from_bucket = storage_client.bucket(from_bucket_name)
        from_gcs_blob = from_bucket.blob(from_object_name)

      if to_gcs:
        to_bucket_name, to_object_name = _parse_gcs_path(copy_request.to_path)
        to_bucket = storage_client.bucket(to_bucket_name)

      if from_gcs and to_gcs:
        from_bucket.copy_blob(from_gcs_blob, from_bucket, to_object_name)
      elif from_gcs and not to_gcs:
        from_gcs_blob.download_to_filename(copy_request.to_path)
      elif not from_gcs and to_gcs:
        to_gcs_blob = to_bucket.blob(to_object_name)
        to_gcs_blob.upload_from_filename(copy_request.from_path)


def copy_files(copy_requests: Iterable[CopyRequest], input_gcs: bool, output_gcs: bool) -> None:
  """Copy media files from an input gcs path to an output gcs path."""
  start_time = time.time()

  chunk_size = 1
  if output_gcs and input_gcs:
    # When downloading or uploading locally, batching greatly slows down the parallelism as GCS
    # batching with storage.batch() has no effect.
    # When copying files locally, storage.batch() has no effect and it's better to run each copy in
    # separate thread.
    chunk_size = GCS_COPY_CHUNK_SIZE

  batched_copy_requests = chunks(copy_requests, chunk_size)
  with ThreadPoolExecutor() as executor:
    executor.map(copy_batch, batched_copy_requests)

  log(f'Copy took {time.time() - start_time} seconds.')


def delete_file(filepath: str) -> None:
  """Delete a file. It works for both GCS and local paths."""
  if filepath.startswith(GCS_PROTOCOL):
    blob = _get_gcs_blob(filepath)
    blob.delete()
    return

  os.remove(filepath)


def file_exists(filepath: str) -> bool:
  """Return true if the file exists. It works with both GCS and local paths."""
  if filepath.startswith(GCS_PROTOCOL):
    return _get_gcs_blob(filepath).exists()
  return os.path.exists(filepath)


def get_image_path(output_dir: str, path: Path, row_id: bytes) -> str:
  """Return the GCS file path to an image associated with a specific row."""
  path_subdir = '_'.join([str(p) for p in path])
  filename = row_id.hex()
  return os.path.join(output_dir, IMAGES_DIR_NAME, path_subdir, filename)


Tout = TypeVar('Tout')


def async_wrap(func: Callable[..., Tout],
               loop: Optional[AbstractEventLoop] = None,
               executor: Optional[Executor] = None) -> Callable[..., Awaitable[Tout]]:
  """Wrap a sync function into an async function."""

  @wraps(func)
  async def run(*args: Any, **kwargs: Any) -> Any:
    current_loop = loop or asyncio.get_running_loop()
    pfunc: Callable = partial(func, *args, **kwargs)
    return await current_loop.run_in_executor(executor, pfunc)

  return run


Tchunk = TypeVar('Tchunk')


def chunks(iterable: Iterable[Tchunk], size: int) -> Iterable[list[Tchunk]]:
  """Split a list of items into equal-sized chunks. The last chunk might be smaller."""
  it = iter(iterable)
  chunk = list(itertools.islice(it, size))
  while chunk:
    yield chunk
    chunk = list(itertools.islice(it, size))


def log(log_str: str) -> None:
  """Print and logs a message so it shows up in the logs on cloud."""
  if env('DISABLE_LOGS'):
    return

  print(log_str)
  logging.info(log_str)


class DebugTimer:
  """A context manager that prints the time elapsed in a block of code.

  ```py
    with DebugTimer('dot product'):
      np.dot(np.random.randn(1000), np.random.randn(1000))
  ```

  $ dot product took 0.001s.
  """

  def __init__(self, name: str) -> None:
    self.name = name

  def __enter__(self) -> 'DebugTimer':
    """Start a timer."""
    self.start = time.perf_counter()
    return self

  def __exit__(self, *args: list[Any]) -> None:
    """Stop the timer and print the elapsed time."""
    log(f'{self.name} took {(time.perf_counter() - self.start):.3f}s.')


def pretty_timedelta(delta: timedelta) -> str:
  """Pretty-prints a `timedelta`."""
  seconds = delta.total_seconds()
  days, seconds = divmod(seconds, 86400)
  hours, seconds = divmod(seconds, 3600)
  minutes, seconds = divmod(seconds, 60)
  if days > 0:
    return '%dd%dh%dm%ds' % (days, hours, minutes, seconds)
  elif hours > 0:
    return '%dh%dm%ds' % (hours, minutes, seconds)
  elif minutes > 0:
    return '%dm%ds' % (minutes, seconds)
  else:
    return '%ds' % (seconds,)