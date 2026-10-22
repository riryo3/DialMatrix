import glob
import http.server
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["REVOMATE_PORT"] = "19400"
sys.argv = ["test"]
import revomate_app as a            # noqa: E402
import revomate_scripts as rs       # noqa: E402

SZ = a.SECTOR_SIZE
DOCS = next((d for d in (os.path.expanduser("~/Documents/DialMatrix バックアップ"), os.path.expanduser("~/Documents/Rev-O-mate Studio バックアップ")) if os.path.isdir(d)), os.path.expanduser("~/Documents/DialMatrix バックアップ"))


def find_image():
    for pat in (os.path.join(DOCS, "factory.mbf"), os.path.join(ROOT, "revomate_backup_*.bin"), os.path.join(DOCS, "revomate_full_backup_*.bin")):
        for p in sorted(glob.glob(pat)):
            if os.path.getsize(p) == SZ * 32:
                return p
    return None


class FakeDev:
    def close(self):
        pass


@unittest.skipUnless(find_image(), "no 2 MB flash image (factory.mbf or a backup .bin) to make a fake device from")
class DeviceFunctions(unittest.TestCase):
    def setUp(self):
        self.orig = open(find_image(), "rb").read()
        self.img = bytearray(self.orig)
        self.tmp = tempfile.mkdtemp()
        self.saved = (a.DATA_DIR, a.read_range, a.write_sector, a.push_runtime_settings)
        a.DATA_DIR = self.tmp
        a.read_range = lambda d, addr, n: bytes(self.img[addr:addr + n])
        a.write_sector = lambda d, s, data: self.img.__setitem__(slice(s * SZ, (s + 1) * SZ), data)
        a.push_runtime_settings = lambda d, sec0: None

    def tearDown(self):
        a.DATA_DIR, a.read_range, a.write_sector, a.push_runtime_settings = self.saved

    def test_a_dry_run_writes_nothing(self):
        cfg = {"modes": [{"switches": [{"sw": 1, "role": "none", "scriptDirty": True}], "dialFuncs": []}]}
        out = a.write_config(FakeDev(), cfg, dry_run=True)
        self.assertTrue(out["ok"] and out["dry_run"])
        self.assertEqual(bytes(self.img), self.orig)


    def test_a_script_less_button_can_be_cleared(self):
        cfg = {"modes": [{"switches": [{"sw": 1, "role": "none", "scriptDirty": True}], "dialFuncs": []}]}
        out = a.write_config(FakeDev(), cfg, dry_run=True)
        self.assertTrue(any("未設定" in c for c in out["changes"]))


class Http(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = a.PORT
        a.PORT = 19400                  # the handler's host check uses the app's port; other test modules may have changed it
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 19400), a.StudioHandler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        a.PORT = cls.port

    def post(self, path, body, origin="http://127.0.0.1:19400"):
        req = urllib.request.Request("http://127.0.0.1:19400" + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "Origin": origin})
        try:
            return json.load(urllib.request.urlopen(req))
        except urllib.error.HTTPError as e:
            return json.load(e)

    def test_a_request_from_another_origin_is_refused(self):
        self.assertEqual(self.post("/api/save-file", {"name": "x.json", "text": "{}"}, origin="http://evil.example")["error"], "forbidden")

    def test_save_file_only_json_and_bin_and_only_the_file_name(self):
        saved = (a.ASK_SAVE_PATH, a.DATA_DIR, a.subprocess.Popen)
        a.ASK_SAVE_PATH = None
        a.DATA_DIR = tempfile.mkdtemp()
        a.subprocess.Popen = lambda *x, **k: None               # no Finder window
        try:
            self.assertEqual(self.post("/api/save-file", {"name": "x.sh", "text": "hi"})["error"], "bad file type")
            r = self.post("/api/save-file", {"name": "../../evil.json", "text": "{}"})
            self.assertTrue(r["ok"])
            self.assertEqual(os.path.dirname(os.path.dirname(r["path"])), a.DATA_DIR)      # inside the export folder, whatever the name said
        finally:
            a.ASK_SAVE_PATH, a.DATA_DIR, a.subprocess.Popen = saved

    def test_the_notices_are_served(self):
        with urllib.request.urlopen("http://127.0.0.1:19400/api/licenses") as r:
            text = json.load(r)["text"]
        self.assertIn("Assembly Desk License", text)
        self.assertIn("pywebview", text)

    def test_led_preview_checks_the_colour(self):
        self.assertEqual(self.post("/api/led-preview", {"color": "red", "ms": 500})["error"], "bad colour")


if __name__ == "__main__":
    unittest.main()
