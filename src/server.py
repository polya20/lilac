"""Serves the agile model server."""

import os
from typing import Any

from fastapi import APIRouter, Depends, FastAPI
from fastapi.responses import HTMLResponse
from fastapi.routing import APIRoute
from fastapi.staticfiles import StaticFiles

from . import db_deprecated, router_concept, router_data_loader, router_dataset
from .server_api import (
    AddDatasetOptions,
    AddExamplesOptions,
    GetModelInfoOptions,
    LoadModelOptions,
    SaveModelOptions,
    SearchExamplesOptions,
)
from .tasks import TaskManifest, task_manager

DIST_PATH = os.path.abspath(os.path.join('dist'))

tags_metadata: list[dict[str, Any]] = [
    {
        'name': 'datasets',
        'description': 'API for querying a dataset.',
    },
    {
        'name': 'concepts',
        'description': 'API for managing concepts.',
    },
    {
        'name': 'data_loaders',
        'description': 'API for loading data.',
    },
]


def custom_generate_unique_id(route: APIRoute) -> str:
  """Generate the name for the API endpoint."""
  return route.name


app = FastAPI(generate_unique_id_function=custom_generate_unique_id, openapi_tags=tags_metadata)

v1_router = APIRouter()
v1_router.include_router(router_dataset.router, prefix='/datasets', tags=['datasets'])
v1_router.include_router(router_concept.router, prefix='/concepts', tags=['concepts'])
v1_router.include_router(router_data_loader.router, prefix='/data_loaders', tags=['data_loaders'])
app.include_router(v1_router, prefix='/api/v1')

# For static files generated by webpack, including bundle.js.
# NOTE: We use check_dir=False because the server can start before files exist.
app.mount('/static',
          StaticFiles(directory=os.path.join(DIST_PATH, 'static'), check_dir=False),
          name='static')
# For hot updated with webpack dev server.
app.mount('/hot',
          StaticFiles(directory=os.path.join(DIST_PATH, 'hot'), check_dir=False),
          name='hot')


@app.on_event('shutdown')
def shutdown_event() -> None:
  """Kill the task manager when FastAPI shuts down."""
  task_manager().stop()


@app.get('/tasks')
def get_task_manifest() -> TaskManifest:
  """Get the tasks, both completed and pending."""
  return task_manager().manifest()


@app.get('/db/list_models')
def list_models() -> dict:
  """List the models."""
  model_infos = db_deprecated.list_models()
  return model_infos.dict()


@app.get('/db/model_info')
def model_info(options: GetModelInfoOptions = Depends()) -> dict:
  """List the models."""
  model_infos = db_deprecated.get_model_info(options)
  return model_infos.dict()


@app.get('/db/load_model')
def load_model(options: LoadModelOptions = Depends()) -> dict:
  """List the models."""
  load_model_response = db_deprecated.load_model(options)
  return load_model_response.dict()


@app.post('/db/save_model')
def save_model(options: SaveModelOptions) -> dict:
  """Save cached model to GCS."""
  db_deprecated.save_model(options)
  return {}


@app.post('/db/add_examples')
def add_examples(options: AddExamplesOptions) -> dict:
  """Save cached model to GCS."""
  db_deprecated.add_examples(options)
  return {}


@app.get('/db/search_examples')
def search_examples(options: SearchExamplesOptions = Depends()) -> dict:
  """Search exmaples."""
  return db_deprecated.search_examples(options).dict()


@app.post('/db/add_dataset')
def add_data(options: AddDatasetOptions) -> dict:
  """Add data to a model."""
  db_deprecated.add_dataset(options)
  return {}


@app.get('/{full_path:path}', response_class=HTMLResponse)
def read_index() -> str:
  """Return the index.html file."""
  with open(os.path.join(DIST_PATH, 'index.html')) as f:
    return f.read()
