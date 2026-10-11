"""Firestore AsyncClient singleton on the "aira" database (never "(default)").

Set FIRESTORE_EMULATOR_HOST=localhost:8085 for local runs. The project id comes from env GCP_PROJECT.
"""
from google.cloud import firestore

from aira_live import config

_client: firestore.AsyncClient | None = None


def db() -> firestore.AsyncClient:
    global _client
    if _client is None:
        settings = config.load()
        _client = firestore.AsyncClient(project=settings.gcp_project, database=settings.firestore_database)
    return _client
