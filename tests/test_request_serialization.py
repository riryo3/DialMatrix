"""Exercise handler methods directly with fake HID; no HTTP server or app is started."""
import io
import json
import os
import sys
import threading
import time
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
fake_hid = types.ModuleType("hid")
fake_hid.enumerate = lambda *_: (_ for _ in ()).throw(AssertionError("physical HID forbidden"))
sys.modules["hid"] = fake_hid
import revomate_app as app
import revomate_device as device


class HandlerSerializationTests(unittest.TestCase):
    def handler(self, path, body=None):
        handler = object.__new__(app.StudioHandler)
        handler.path = path
        handler._local_request_ok = lambda **_: True
        handler._send_json = lambda data, status=200: data
        raw = json.dumps(body or {}).encode()
        handler.rfile = io.BytesIO(raw)
        handler.headers = {"Content-Length": str(len(raw))}
        return handler

    def test_state_open_read_decode_close_all_hold_transaction(self):
        events = []
        class Dev:
            def close(self):
                events.append("close")
                self.assert_busy()
            def assert_busy(self):
                if not device.device_busy():
                    raise AssertionError("device operation escaped transaction")
        dev = Dev()
        def opened():
            dev.assert_busy(); events.append("open"); return dev
        def read(*args):
            dev.assert_busy(); events.append("read"); return bytes(app.SECTOR_SIZE)
        def decode(*args):
            dev.assert_busy(); events.append("decode"); return []
        with mock.patch.object(app, "try_open_vendor", side_effect=opened), mock.patch.object(app, "read_range", side_effect=read), mock.patch.object(app, "decode_all_buttons", side_effect=decode):
            out = self.handler("/api/state").do_GET()
        self.assertTrue(out["ok"])
        self.assertEqual(events, ["open", "read", "decode", "close"])
        self.assertFalse(device.device_busy())

    def test_two_state_requests_do_not_overlap_device_lifetimes(self):
        active = 0
        peak = 0
        errors = []
        counter_lock = threading.Lock()
        class Dev:
            def close(self):
                nonlocal active
                with counter_lock:
                    active -= 1
        def opened():
            nonlocal active, peak
            with counter_lock:
                active += 1
                peak = max(peak, active)
            return Dev()
        def read(*args):
            time.sleep(0.025)
            return bytes(app.SECTOR_SIZE)
        def run():
            try:
                self.assertTrue(self.handler("/api/state").do_GET()["ok"])
            except BaseException as exc:
                errors.append(exc)
        with mock.patch.object(app, "try_open_vendor", side_effect=opened), mock.patch.object(app, "read_range", side_effect=read), mock.patch.object(app, "decode_all_buttons", return_value=[]):
            threads = [threading.Thread(target=run) for _ in range(3)]
            for thread in threads: thread.start()
            for thread in threads: thread.join(3)
        self.assertEqual(errors, [])
        self.assertEqual(peak, 1)
        self.assertEqual(active, 0)

    def test_write_failure_returns_recovery_metadata_and_closes_handle(self):
        closed = []
        class Dev:
            def close(self):
                closed.append(device.device_busy())
        failure = app.WriteFailure(IOError("fake verify failure"), ["full.bin"], [2], 1, ["planned"])
        with mock.patch.object(app, "try_open_vendor", return_value=Dev()), mock.patch.object(app, "write_config", side_effect=failure):
            out = self.handler("/api/write", {"configState": {}, "plan_token": "token"}).do_POST()
        self.assertEqual(closed, [True])
        self.assertTrue(out["recovery_required"])
        self.assertEqual(out["backups"], ["full.bin"])
        self.assertEqual(out["completed_sectors"], [2])
        self.assertEqual(out["applied_buttons"], [])
        self.assertFalse(device.device_busy())


if __name__ == "__main__":
    unittest.main()
