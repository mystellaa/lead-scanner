import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import database
import phone_extract
import ui


class Value:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class EntryModesTest(unittest.TestCase):
    def app(self, mode, source):
        app = SimpleNamespace(
            entry_mode=Value(mode), source_var=Value(source), worker=None,
            sources=["platform-a", "platform-b"], files=[Path("a.png")],
            source_combo=Mock(), progress=Mock(), btn_start=Mock(),
            _scan_worker=Mock(), _fresh_batch=lambda: {},
        )
        app._current_source = lambda: ui.LeadScannerApp._current_source(app)
        return app

    def test_delegate_can_start_twice_without_source(self):
        app = self.app("delegate", "platform-a")
        with patch.object(ui.ocr, "pick_engine_key", return_value="rapid"), \
             patch.object(ui.threading, "Thread") as thread, \
             patch.object(ui.messagebox, "showwarning") as warning:
            thread.return_value.is_alive.return_value = False
            for filename in ("a.png", "b.png"):
                app.files = [Path(filename)]
                ui.LeadScannerApp._start_scan(app)
                self.assertEqual(app.batch["source"], "")
                self.assertEqual(thread.call_args.kwargs["args"][1], "")
            warning.assert_not_called()

    def test_platform_mode_requires_source(self):
        app = self.app("platform", "")
        with patch.object(ui.messagebox, "showwarning") as warning:
            ui.LeadScannerApp._start_scan(app)
            warning.assert_called_once()

    def test_next_batch_keeps_all_platforms(self):
        app = self.app("delegate", "")
        app.source_combo = {"values": ["platform-a"]}
        ui.LeadScannerApp._narrow_sources_by_batch(app)
        self.assertEqual(app.source_combo["values"], app.sources)

    def test_blank_snapshot_does_not_inherit_later_selection(self):
        app = self.app("platform", "platform-b")
        app.batch = dict(source="", images=0, phones=0, customers=set(), new=0, dup=0)
        app.db = database.LeadDatabase(readonly=True)
        app.batch_records = []
        app._insert_row = Mock()
        app._refresh_stats = Mock()
        lead = phone_extract.Lead(phone="13812345678", source_person="彭曦")
        ui.LeadScannerApp._handle_msg(app, ("item", 1, 1, Path("a.png"), [lead], ""))
        self.assertEqual(app.db.records[0].source_platform, "")


if __name__ == "__main__":
    unittest.main()
