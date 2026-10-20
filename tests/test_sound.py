"""The completion sound: the saved choice is cleaned, only real system sounds can be played, the server endpoints work,
and the question / settings window of both pages opens and saves."""
import http.server
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["REVOMATE_PORT"] = "19401"
sys.argv = ["test"]
TMP = tempfile.mkdtemp()
os.environ["REVOMATE_SOUND_PREFS"] = TMP      # a folder of its own: never the person's real files
import revomate_app as a            # noqa: E402
import revomate_sound as rsnd       # noqa: E402
sys.path.insert(0, os.path.join(ROOT, "tests"))
from test_pages import run_page, CHROME   # noqa: E402


@unittest.skipUnless(rsnd.sounds(), "no macOS system sounds here")
class SoundModule(unittest.TestCase):
    def setUp(self):
        for ed in ("lite", "pro"):
            if os.path.exists(rsnd.prefs_path(ed)):
                os.unlink(rsnd.prefs_path(ed))

    def test_defaults_when_nothing_is_saved(self):
        p = rsnd.load()
        self.assertTrue(p["enabled"])
        self.assertFalse(p["asked"])

    def test_unknown_names_and_odd_values_are_dropped(self):
        p = rsnd.clean({"enabled": "yes", "asked": 1, "ok": "../../etc/passwd", "fail": "NoSuchSound", "extra": 1})
        self.assertEqual(p, rsnd.DEFAULTS)

    def test_save_and_load(self):
        name = rsnd.sounds()[0]
        rsnd.save({"enabled": False, "asked": True, "ok": name, "fail": name})
        p = rsnd.load()
        self.assertEqual((p["enabled"], p["asked"], p["ok"], p["fail"]), (False, True, name, name))

    def test_each_edition_keeps_its_own_choice(self):
        name = rsnd.sounds()[0]
        rsnd.save({"enabled": False, "asked": True, "ok": name, "fail": name}, "lite")
        self.assertTrue(rsnd.load("lite")["asked"])
        self.assertFalse(rsnd.load("pro")["asked"])       # the other app asks for itself

    def test_only_a_system_sound_is_played(self):
        with mock.patch.object(rsnd.subprocess, "Popen") as popen:
            self.assertFalse(rsnd.play("../../bin/ls"))
            self.assertFalse(rsnd.play("nothing"))
            popen.assert_not_called()
            self.assertTrue(rsnd.play(rsnd.sounds()[0]))
            popen.assert_called_once()


@unittest.skipUnless(rsnd.sounds(), "no macOS system sounds here")
class SoundEndpoints(unittest.TestCase):
    def setUp(self):
        self.port = a.PORT
        a.PORT = 19401                      # the host check of the handler uses the app's port

    def tearDown(self):
        a.PORT = self.port

    def test_get_save_and_play(self):
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 19401), a.StudioHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            def call(path, body=None):
                req = urllib.request.Request("http://127.0.0.1:19401" + path, data=None if body is None else json.dumps(body).encode(),
                                             headers={"Content-Type": "application/json", "Origin": "http://127.0.0.1:19401"})
                return json.loads(urllib.request.urlopen(req, timeout=5).read())
            r = call("/api/sound")
            self.assertTrue(r["ok"] and r["sounds"])
            name = r["sounds"][0]
            r = call("/api/sound", {"enabled": False, "asked": True, "ok": name, "fail": "NoSuchSound"})
            self.assertEqual((r["prefs"]["enabled"], r["prefs"]["ok"], r["prefs"]["fail"]), (False, name, rsnd.DEFAULTS["fail"]))
            with mock.patch.object(rsnd.subprocess, "Popen"):
                self.assertFalse(call("/api/sound-play", {"name": "NoSuchSound"})["ok"])
        finally:
            server.shutdown()


WINDOW_SCENARIO = r"""
  doneSoundList=["Basso","Glass"]; doneSoundPrefs={enabled:true,asked:false,ok:"Glass",fail:"Basso"};
  const saved=[]; window.fetch=async(path,opt)=>({json:async()=>{ if(path==="/api/sound"&&opt&&opt.method==="POST"){ saved.push(JSON.parse(opt.body)); return {ok:true,prefs:JSON.parse(opt.body)}; } return {ok:true}; }});
  let asked=false; askDoneSoundOnce().then(()=>{asked=true;}); await tick(300);
  log.push("question shown: "+!!document.getElementById("dsDone"));
  document.getElementById("dsOn").checked=false; document.getElementById("dsFail").value="Glass";
  document.getElementById("dsDone").click(); await tick(300);
  log.push("saved: "+JSON.stringify(saved[saved.length-1]));
  log.push("resolved: "+asked);
  await askDoneSoundOnce(); log.push("not asked twice: "+!document.getElementById("dsDone"));
  openDoneSoundSettings(); await tick(300);
  log.push("settings shown: "+!!document.getElementById("dsDone")+" off kept: "+(document.getElementById("dsOn").checked===false));
  document.dispatchEvent(new KeyboardEvent("keydown",{key:"Escape",bubbles:true})); await tick(300);
  log.push("closed by Esc: "+!document.getElementById("dsDone"));
"""


@unittest.skipUnless(os.path.exists(CHROME), "Google Chrome is not installed")
class SoundWindow(unittest.TestCase):
    def check(self, file):
        log = run_page(WINDOW_SCENARIO, file=file)
        self.assertIn("question shown: true", log)
        self.assertIn('saved: {"enabled":false,"asked":true,"ok":"Glass","fail":"Glass"}', log)
        self.assertIn("resolved: true", log)
        self.assertIn("not asked twice: true", log)
        self.assertIn("settings shown: true off kept: true", log)
        self.assertIn("closed by Esc: true", log)


    def test_studio(self):
        self.check("revomate_studio.html")


if __name__ == "__main__":
    unittest.main()
