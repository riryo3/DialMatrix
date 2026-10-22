import importlib
import sys
import threading
import time
import types
import unittest

import revomate_device as device


class DeviceLockTests(unittest.TestCase):
    def test_operations_serialize_busy_and_release_after_exception(self):
        entered = threading.Event()
        release = threading.Event()
        second_entered = threading.Event()
        errors = []

        def first():
            try:
                with device.device_operation():
                    self.assertTrue(device.device_busy())
                    entered.set()
                    release.wait(2)
                    raise RuntimeError("expected")
            except RuntimeError:
                pass
            except BaseException as exc:
                errors.append(exc)

        def second():
            try:
                with device.device_operation():
                    second_entered.set()
            except BaseException as exc:
                errors.append(exc)

        a = threading.Thread(target=first)
        a.start()
        self.assertTrue(entered.wait(1))
        b = threading.Thread(target=second)
        b.start()
        time.sleep(0.05)
        self.assertFalse(second_entered.is_set())
        self.assertTrue(device.device_busy())
        release.set()
        a.join(2)
        b.join(2)
        self.assertTrue(second_entered.is_set())
        self.assertFalse(device.device_busy())
        self.assertEqual(errors, [])

    def test_nested_depth_and_registered_close(self):
        calls = []
        callback = lambda: calls.append("close")
        device.register_close(callback)
        with device.device_operation():
            with device.device_operation():
                self.assertTrue(device.device_busy())
        self.assertFalse(device.device_busy())
        self.assertEqual(calls, ["close", "close"])

    def test_preview_cannot_open_or_resume_during_operation(self):
        # Install an explicit fake module; no real HID library/device is loaded or accessed.
        fake_hid = types.ModuleType("hid")
        fake_hid.enumerate = lambda *_: (_ for _ in ()).throw(AssertionError("physical enumerate"))
        sys.modules["hid"] = fake_hid
        preview = importlib.import_module("revomate_ledpreview")
        old_deadline = preview._paused_until
        preview._paused_until = time.time() + 100
        try:
            with device.device_operation():
                preview.resume()
                self.assertTrue(device.device_busy())
                self.assertGreater(preview._paused_until, time.time())
                self.assertFalse(preview.show([1, 2, 3], 100))
                self.assertIsNone(preview._dev)
        finally:
            preview._paused_until = old_deadline
            sys.modules.pop("hid", None)


if __name__ == "__main__":
    unittest.main()
