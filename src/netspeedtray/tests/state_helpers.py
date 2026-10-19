"""
Synchronous database setup for tests that build a ``WidgetState`` without starting its worker thread.

* :func:`prepare_v8` - what the worker's ``run()`` does at startup, on the CALLING thread:
  ``_prepare_storage()`` (recover, create / migrate, open, WriterV8 bound to this thread, session
  started), then ``ready`` is set so the readers open. Tasks can then be run with
  ``worker._execute_task(...)`` from the same thread.
* :func:`attach_v7` - a v7 file written by 2.1.x's worker (``LegacyV7Worker``), presented to the
  WidgetState the way a 2.2 session reads one whose upgrade was deferred: ``reader_version = 7``.
  For tests of the v7 read backend through WidgetState; 2.2 itself never writes such a file.
"""

from pathlib import Path


def prepare_v8(ws, db_path: Path = None):
    w = ws.db_worker
    if db_path is not None:
        ws._db_path = Path(db_path)
    w.db_path = ws._db_path
    w._prepare_storage()
    w.ready.set()
    ws._history_reader = None
    return w


def attach_v7(ws, db_path: Path = None, read_only: bool = False):
    from netspeedtray.tests.legacy_v7_worker import LegacyV7Worker
    if db_path is not None:
        ws._db_path = Path(db_path)
    legacy = LegacyV7Worker(ws._db_path)
    legacy._initialize_connection()
    legacy._check_and_create_schema()
    legacy.reader_version = 7
    legacy.readers_read_only = read_only
    legacy.ready.set()
    ws.db_worker = legacy
    ws._history_reader = None
    return legacy
