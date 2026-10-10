"""The test suite.

Before any test module creates its QApplication, the Qt attributes the page snapshots' web engine
needs are set (snapshot_engine.prepare_application: QtCore only, nothing is started), exactly as
the app does before its own QApplication.
"""

try:
    from briefing_reader.snapshot_engine import prepare_application

    prepare_application()
except Exception:  # noqa: BLE001 - a test run without Qt still runs its Qt-free tests
    pass
