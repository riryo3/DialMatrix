"""Safety contract tests for configuration writes (no physical HID access)."""
import importlib
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

# Install fail-closed HID before importing application modules.
_fake_hid = types.ModuleType("hid")
def _forbidden_enumerate(*args, **kwargs):
    raise AssertionError("tests must never enumerate physical HID devices")
_fake_hid.enumerate = _forbidden_enumerate
_fake_hid.device = lambda: (_ for _ in ()).throw(AssertionError("physical HID open"))
sys.modules["hid"] = _fake_hid
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
app = importlib.import_module("revomate_app")

class WriteSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data_dir_patch = mock.patch.object(app, "DATA_DIR", self.tmp.name)
        self.data_dir_patch.start(); self.addCleanup(self.data_dir_patch.stop)
        self.flash = bytearray(b"\xff" * (32 * app.SECTOR_SIZE))
        self.flash[app.DIAL_BASE:app.DIAL_BASE + 8] = bytes([9, 0, 0x68, 0, 0, 0, 0, 50])
        self.flash[app.MODE_BASE + 0x16] = 0
        self.dev, self.writes = object(), []
        self.read_mock = mock.Mock(side_effect=self.read_range)
        self.write_mock = mock.Mock(side_effect=self.write_sector)
        for target, value in (("read_range", self.read_mock), ("write_sector", self.write_mock), ("push_runtime_settings", mock.Mock())):
            p = mock.patch.object(app, target, value); p.start(); self.addCleanup(p.stop)

    def read_range(self, dev, addr, size):
        return bytes(self.flash[addr:addr + size])

    def write_sector(self, dev, sector, data):
        self.writes.append(sector)
        self.flash[sector * app.SECTOR_SIZE:(sector + 1) * app.SECTOR_SIZE] = data

    def config(self):
        return {"modes": [{"defaultDialFunc": 0, "dialFuncs": [], "switches": [], "baseLed": None}]}

    def changed_config(self):
        return {"modes": [{"dialFuncs": [{"cw": {"type": 9, "mod": 0, "key": 0x69}, "ccw": {"type": 0}},
                                            {"cw": {"type": 0}, "ccw": {"type": 0}},
                                            {"cw": {"type": 0}, "ccw": {"type": 0}},
                                            {"cw": {"type": 0}, "ccw": {"type": 0}}], "switches": []}]}

    def dry_plan(self, cfg=None):
        return app.write_config(self.dev, cfg or self.changed_config(), dry_run=True)

    def button_config(self, switches):
        cfg = self.config(); cfg["modes"][0]["switches"] = switches; return cfg

    def test_dry_run_is_always_nonwriting_and_returns_plan_metadata(self):
        result = self.dry_plan()
        self.assertTrue(result["dry_run"]); self.assertTrue(result.get("plan_token")); self.assertTrue(result.get("has_changes"))
        self.assertEqual(self.writes, []); self.assertEqual(os.listdir(self.tmp.name), [])

    def test_noop_dry_run_is_still_dry_run_with_token_and_no_backup(self):
        result = app.write_config(self.dev, self.config(), dry_run=True)
        self.assertTrue(result["dry_run"]); self.assertTrue(result.get("plan_token")); self.assertFalse(result.get("has_changes"))
        self.assertEqual(self.writes, []); self.assertEqual(os.listdir(self.tmp.name), [])

    def test_missing_or_stale_plan_token_is_rejected_before_writes(self):
        plan = self.dry_plan()
        for token in (None, "stale"):
            with self.assertRaises(Exception): app.write_config(self.dev, self.changed_config(), plan_token=token)
        self.assertEqual(self.writes, []); self.assertTrue(plan.get("plan_token"))

    def test_config_changed_after_dry_run_is_rejected_before_writes(self):
        original = self.changed_config(); plan = self.dry_plan(original)
        changed = self.changed_config(); changed["modes"][0]["dialFuncs"][0]["cw"]["key"] = 0x70
        with self.assertRaises(Exception): app.write_config(self.dev, changed, plan_token=plan["plan_token"])
        self.assertEqual(self.writes, [])

    def test_success_requires_full_flash_backup_before_first_write(self):
        plan = self.dry_plan(); original = bytes(self.flash); calls = []
        backup = app.save_full_backup
        def backup_then_check(dev, label):
            path = backup(dev, label)
            self.assertEqual(os.path.getsize(path), 2 * 1024 * 1024)
            with open(path, "rb") as f:
                self.assertEqual(f.read(), original)
            self.assertEqual(self.writes, [], "backup must be verified before first write")
            calls.append(path); return path
        with mock.patch.object(app, "save_full_backup", side_effect=backup_then_check):
            result = app.write_config(self.dev, self.changed_config(), plan_token=plan["plan_token"])
        self.assertTrue(calls); self.assertTrue(result.get("ok")); self.assertTrue(self.writes)

    def test_full_backup_read_or_save_failure_prevents_any_write(self):
        plan = self.dry_plan()
        with mock.patch.object(app, "save_full_backup", side_effect=IOError("backup read failed")):
            with self.assertRaises(Exception): app.write_config(self.dev, self.changed_config(), plan_token=plan["plan_token"])
        self.assertEqual(self.writes, [])

    def test_full_flash_read_validates_exact_two_megabytes(self):
        self.assertEqual(len(app.read_full_flash(self.dev)), 2 * 1024 * 1024)
        with mock.patch.object(app, "read_range", return_value=b"short"):
            with self.assertRaises(Exception): app.read_full_flash(self.dev)

    def test_write_failure_contains_recovery_information(self):
        plan = self.dry_plan(); self.write_mock.side_effect = IOError("simulated sector write failure")
        with self.assertRaises(app.WriteFailure) as caught:
            app.write_config(self.dev, self.changed_config(), plan_token=plan["plan_token"])
        result = caught.exception.result
        self.assertTrue(result["backups"]); self.assertTrue(result["recovery_required"]); self.assertEqual(result["applied_buttons"], [])

    def test_shared_script_edit_is_skipped_without_writes(self):
        # Same script number assigned to two buttons; synthetic table/data only.
        info = bytearray(b"\xff" * app.revomate_scripts.SECTOR_SIZE)
        rec = 0x10; addr = 2 * app.SECTOR_SIZE; body = b"\x41\x69\x70\x00\x01\x40\x69"
        info[rec:rec+4] = addr.to_bytes(4, "little"); info[rec+4:rec+8] = len(body).to_bytes(4, "little"); info[rec+8] = 0
        self.flash[app.revomate_scripts.INFO_ADDR:app.revomate_scripts.INFO_ADDR+len(info)] = info
        self.flash[addr:addr+len(body)] = body
        self.flash[app.MODE_BASE] = self.flash[app.MODE_BASE+1] = 1
        cfg = self.button_config([{"scriptDirty": True, "role": "key", "key": 0x70}])
        result = app.write_config(self.dev, cfg, dry_run=True)
        self.assertTrue(any("共有" in x["reason"] for x in result["skipped"]))
        self.assertFalse(any("新しいスクリプト" in x for x in result["changes"]))
        self.assertEqual(self.writes, [])

    def test_invalid_or_missing_key_is_skipped(self):
        for sw in ({"scriptDirty": True, "role": "key", "key": 0}, {"scriptDirty": True, "role": "unknown"}):
            result = self.dry_plan(self.button_config([sw]))
            self.assertTrue(result["skipped"])
        self.assertEqual(self.writes, [])

    def test_applied_buttons_excludes_skipped_button(self):
        cfg = self.button_config([{"scriptDirty": True, "role": "special", "special": 1},
                                  {"scriptDirty": True, "role": "key", "key": 0}])
        plan = self.dry_plan(cfg)
        result = app.write_config(self.dev, cfg, plan_token=plan["plan_token"])
        self.assertEqual([b["sw"] for b in result["applied_buttons"]], [1])
        self.assertTrue(any(x.get("sw") == 2 for x in result["skipped"]))

    def test_existing_macro_overwrite_warns(self):
        info = bytearray(b"\xff" * app.revomate_scripts.SECTOR_SIZE); off = 0x10; addr = 2 * app.SECTOR_SIZE
        body = b"\x41\x69\x70\x00\x01\x40\x69"
        info[off:off+4] = addr.to_bytes(4, "little"); info[off+4:off+8] = len(body).to_bytes(4, "little")
        self.flash[app.revomate_scripts.INFO_ADDR:app.revomate_scripts.INFO_ADDR+len(info)] = info
        self.flash[addr:addr+len(body)] = body; self.flash[app.MODE_BASE] = 1
        self.flash[addr:addr+len(body)] = b"\x41\xe0\x41\x69\x40\xe0\x40\x69"
        result = self.dry_plan(self.button_config([{"scriptDirty": True, "role": "key", "key": 0x70}]))
        self.assertTrue(any("上書き" in warning for warning in result["warnings"]))

    def test_invalid_led_and_sensitivity_are_reported_as_skips(self):
        cfg = self.changed_config(); df = cfg["modes"][0]["dialFuncs"][0]
        df["led"] = "not-a-color"; df["sensCw"] = 0
        cfg["modes"][0]["baseLed"] = "bad"
        result = self.dry_plan(cfg)
        fields = {item["field"] for item in result["skipped"]}
        self.assertIn("dialFuncs.0.led", fields); self.assertIn("baseLed", fields); self.assertIn("dialFuncs.0.sensCw", fields)

    def test_device_readset_changed_after_preview_rejects_plan(self):
        plan = self.dry_plan(); self.flash[app.DIAL_BASE + 1] ^= 1
        with self.assertRaises(Exception): app.write_config(self.dev, self.changed_config(), plan_token=plan["plan_token"])
        self.assertEqual(self.writes, [])

    def test_expired_token_is_rejected(self):
        with mock.patch.object(app, "_PLAN_TTL", 0):
            plan = self.dry_plan()
            with mock.patch.object(app.time, "time", return_value=int(app.time.time()) + 10):
                with self.assertRaises(Exception): app.write_config(self.dev, self.changed_config(), plan_token=plan["plan_token"])
        self.assertEqual(self.writes, [])

    def test_backup_read_diverging_from_confirmed_readset_blocks_write(self):
        plan = self.dry_plan(); original = app.save_full_backup
        def diverged(dev, label):
            self.flash[app.DIAL_BASE + 2] ^= 1
            return original(dev, label)
        with mock.patch.object(app, "save_full_backup", side_effect=diverged):
            with self.assertRaises(IOError): app.write_config(self.dev, self.changed_config(), plan_token=plan["plan_token"])
        self.assertEqual(self.writes, [])

    def test_backup_readback_corruption_blocks_write(self):
        plan = self.dry_plan(); real_open = open
        def corrupting_open(path, mode="r", *args, **kwargs):
            if isinstance(path, (str, bytes, os.PathLike)) and str(path).endswith(".bin") and "r" in mode:
                fh = real_open(path, mode, *args, **kwargs)
                data = bytearray(fh.read()); fh.close(); data[0] ^= 1
                return mock.mock_open(read_data=bytes(data))()
            return real_open(path, mode, *args, **kwargs)
        with mock.patch("builtins.open", side_effect=corrupting_open):
            with self.assertRaises(Exception): app.write_config(self.dev, self.changed_config(), plan_token=plan["plan_token"])
        self.assertEqual(self.writes, [])

    def test_fsync_failure_blocks_write(self):
        plan = self.dry_plan()
        with mock.patch.object(app.os, "fsync", side_effect=OSError("disk failure")):
            with self.assertRaises(Exception): app.write_config(self.dev, self.changed_config(), plan_token=plan["plan_token"])
        self.assertEqual(self.writes, [])

    def test_post_write_verification_failure_reports_completed_sector(self):
        plan = self.dry_plan(); self.write_mock.side_effect = None
        def corrupt_after_write(dev, sector, data):
            self.writes.append(sector)
            self.flash[sector*app.SECTOR_SIZE:(sector+1)*app.SECTOR_SIZE] = data
            if sector == 0: self.flash[app.DIAL_BASE] ^= 1
        self.write_mock.side_effect = corrupt_after_write
        with self.assertRaises(app.WriteFailure) as caught:
            app.write_config(self.dev, self.changed_config(), plan_token=plan["plan_token"])
        self.assertEqual(caught.exception.result["completed_sectors"], [])
        self.assertEqual(caught.exception.result["failed_sector"], 0)
        self.assertTrue(caught.exception.result["recovery_required"])

    def test_data_sectors_are_written_before_sector_one_then_zero(self):
        cfg = self.button_config([{"scriptDirty": True, "role": "key", "key": 0x70}])
        plan = self.dry_plan(cfg)
        app.write_config(self.dev, cfg, plan_token=plan["plan_token"])
        self.assertEqual(self.writes, [2, 1, 0])

    def test_failure_on_table_reports_verified_data_sector_only(self):
        cfg = self.button_config([{"scriptDirty": True, "role": "key", "key": 0x70}])
        plan = self.dry_plan(cfg)
        def fail_table(dev, sector, data):
            if sector == 1:
                raise IOError("table failure")
            self.write_sector(dev, sector, data)
        self.write_mock.side_effect = fail_table
        with self.assertRaises(app.WriteFailure) as caught:
            app.write_config(self.dev, cfg, plan_token=plan["plan_token"])
        result = caught.exception.result
        self.assertEqual(result["completed_sectors"], [2])
        self.assertEqual(result["failed_sector"], 1)
        self.assertEqual(result["applied_buttons"], [])
        self.assertEqual(self.writes, [2])

    def test_unchanged_special_not_reported_but_changed_special_is(self):
        self.flash[app.MODE_BASE + 11] = 1
        cfg = self.button_config([{"role": "special", "special": 1}])
        plan = self.dry_plan(cfg)
        self.assertFalse(plan["has_changes"])
        self.assertEqual(plan["eligible_buttons"], [])
        cfg["modes"][0]["switches"][0]["special"] = 2
        plan = self.dry_plan(cfg)
        self.assertEqual(plan["eligible_buttons"][0]["status"], "planned")
        out = app.write_config(self.dev, cfg, plan_token=plan["plan_token"])
        self.assertEqual(out["applied_buttons"][0]["status"], "applied")

    def test_already_matching_edit_distinguished_from_written_edit(self):
        self.flash[app.MODE_BASE] = self.flash[app.MODE_BASE + 11] = 0
        cfg = self.button_config([{"role": "none", "scriptDirty": True}])
        plan = self.dry_plan(cfg)
        self.assertFalse(plan["has_changes"])
        self.assertEqual(plan["eligible_buttons"][0]["status"], "already_current")
        out = app.write_config(self.dev, cfg, plan_token=plan["plan_token"])
        self.assertEqual(out["applied_buttons"][0]["status"], "already_current")
        self.assertEqual(self.writes, [])

    # --- a write that stops partway (device vanishes, I/O error): what the caller is told ---
    def fail_on(self, bad_sector, error):
        def write(dev, sector, data):
            if sector == bad_sector:
                raise error
            self.write_sector(dev, sector, data)
        self.write_mock.side_effect = write

    def failed_write(self, cfg, bad_sector, error=OSError("device disconnected")):
        plan = self.dry_plan(cfg); self.fail_on(bad_sector, error)
        with self.assertRaises(app.WriteFailure) as caught:
            app.write_config(self.dev, cfg, plan_token=plan["plan_token"])
        return caught.exception.result

    def script_config(self):
        return self.button_config([{"scriptDirty": True, "role": "key", "key": 0x70}])

    def test_failure_on_first_data_sector_reports_nothing_completed(self):
        result = self.failed_write(self.script_config(), 2)
        self.assertEqual(result["completed_sectors"], [])
        self.assertEqual(result["failed_sector"], 2)
        self.assertEqual(self.writes, [])

    def test_failure_on_sector_zero_reports_data_and_table_completed(self):
        result = self.failed_write(self.script_config(), 0)
        self.assertEqual(result["completed_sectors"], [2, 1])
        self.assertEqual(result["failed_sector"], 0)
        self.assertEqual(self.writes, [2, 1])
        self.assertEqual(result["applied_buttons"], [])

    def test_failure_stops_all_later_sectors(self):
        self.failed_write(self.script_config(), 1)
        self.assertNotIn(0, self.writes)

    def test_failure_message_and_recovery_flag_are_reported(self):
        result = self.failed_write(self.script_config(), 1, OSError("device disconnected"))
        self.assertFalse(result["ok"])
        self.assertIn("device disconnected", result["error"])
        self.assertTrue(result["recovery_required"])
        self.assertEqual(result["changes"], [])

    def test_backup_named_in_failure_is_the_untouched_original_flash(self):
        original = bytes(self.flash)
        result = self.failed_write(self.script_config(), 0)
        self.assertEqual(len(result["backups"]), 1)
        found = [os.path.join(d, n) for d, _, ns in os.walk(self.tmp.name) for n in ns if n == result["backups"][0]]
        self.assertEqual(len(found), 1)
        with open(found[0], "rb") as f:
            self.assertEqual(f.read(), original)

    def test_restoring_from_the_reported_backup_recovers_the_flash(self):
        original = bytes(self.flash)
        result = self.failed_write(self.script_config(), 0)
        self.assertNotEqual(bytes(self.flash), original)   # sectors 2 and 1 were already changed
        found = [os.path.join(d, n) for d, _, ns in os.walk(self.tmp.name) for n in ns if n == result["backups"][0]]
        with open(found[0], "rb") as f:
            backup = f.read()
        for sector in range(32):
            self.write_sector(self.dev, sector, backup[sector * app.SECTOR_SIZE:(sector + 1) * app.SECTOR_SIZE])
        self.assertEqual(bytes(self.flash), original)

if __name__ == "__main__":
    unittest.main()
