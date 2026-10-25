"""When another program already holds the app's port, the packaged app picks a free one (and never shows the other program's page)."""
import os, subprocess, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = 'import sys, os, socket, threading, time, urllib.request\nsys.path.insert(0, "__ROOT__")\nos.environ["REVOMATE_NO_DIALOG"] = "1"\nsys.frozen = True; sys._MEIPASS = "__ROOT__"\nsys.argv = ["x", "--edition", "lite", "--no-browser"]\nimport revomate_app as a\na.other_instance_running = lambda: None\nsquat = socket.socket(); squat.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); squat.bind(("0.0.0.0", a.PORT)); squat.listen(1)   # another program holds the port\nt = threading.Thread(target=a.main, daemon=True); t.start(); time.sleep(1.5)\nprint("preferred", 8765, "now", a.PORT)\nr = urllib.request.urlopen(f"http://127.0.0.1:{a.PORT}/revomate_studio.html", timeout=5).read()\nprint("serves our page:", b"DialMatrix" in r)\n'


class PortTaken(unittest.TestCase):
    def test_a_free_port_is_used_when_the_preferred_one_is_taken(self):
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
            f.write(SCRIPT.replace("__ROOT__", ROOT.replace("\\", "/")))      # backslashes of a Windows path would be read as escapes
            path = f.name
        try:
            out = subprocess.run([sys.executable, "-I", path], capture_output=True, text=True, timeout=60,
                                 env={k: v for k, v in os.environ.items() if k != "REVOMATE_PORT"}).stdout
        finally:
            os.unlink(path)
        self.assertIn("serves our page: True", out, out)
        self.assertNotIn("now 8765", out, out)


if __name__ == "__main__":
    unittest.main()
