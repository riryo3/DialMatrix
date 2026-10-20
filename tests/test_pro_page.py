"""The pages in headless Chrome. Each test runs a small script inside the page and checks what it reports."""
import html
import json
import os
import re
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

HELPERS = r"""
const $=id=>document.getElementById(id); const tick=(ms=90)=>new Promise(r=>setTimeout(r,ms)); const log=[];
const popEl=()=>[...document.documentElement.children].find(e=>e.querySelector&&(e.querySelector("#cpBody")||e.querySelector("#cpHex")));
const dlgBtn=t=>[...document.querySelectorAll("#pdBtns button")].find(b=>b.textContent===t);
window.confirm=m=>{log.push("NATIVE CONFIRM"); return true}; window.alert=m=>log.push("ALERT:"+m); window.prompt=()=>{log.push("NATIVE PROMPT"); return null};
window.addEventListener("error",e=>log.push("ERROR "+e.message));
const done=()=>document.body.setAttribute("data-log", JSON.stringify(log));
"""


DEFAULT_PAGE = "revomate_studio.html"
ZOOM_PAGE = None


def run_page(scenario, size=(1500, 1000), file=None):
    file = file or DEFAULT_PAGE
    with open(os.path.join(ROOT, file), encoding="utf-8") as fh:
        page = fh.read()                                                  # always the current file
    script = "<script>" + HELPERS + "setTimeout(async()=>{ " + ("setZoom('1.0');" if file == ZOOM_PAGE else "") + "\n" + scenario + "\ndone(); },900);</script></body>"
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as f:
        f.write(page.replace("</body>", script, 1))
        path = f.name
    try:
        out = subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--window-size=%d,%d" % size, "--virtual-time-budget=12000",
                              "--dump-dom", "file://" + path], capture_output=True, text=True, timeout=90).stdout
    finally:
        os.unlink(path)
    m = re.search(r'data-log="([^"]*)"', out)
    if not m:
        raise AssertionError("the page did not report")
    return json.loads(html.unescape(m.group(1)))





@unittest.skipUnless(os.path.exists(CHROME), "Google Chrome is not installed")
class StudioPage(unittest.TestCase):
    def test_led_colour_uses_the_same_picker_as_the_pro_page(self):
        log = run_page(r"""
          const colour=()=>$("dialLedColor").value; const before=colour();
          setColorMode("palette"); $("dialLedColor").click(); await tick();
          log.push("palette chips="+popEl().querySelectorAll("button[data-c]").length);
          popEl().querySelector('button[data-c="#ff0000"]').click(); await tick(); log.push("click decides: closed="+!popEl()+" colour="+colour());
          setColorMode("hsl"); $("dialLedColor").click(); await tick(); const h=popEl().querySelector("#cp_h"); h.value=120; h.dispatchEvent(new Event("input",{bubbles:true})); await tick();
          log.push("hsl moved to "+colour()); document.body.click(); await tick(); log.push("outside click undoes: "+(colour()==="#ff0000"));
          setColorMode("hex"); $("dialLedColor").click(); await tick(); const hx=popEl().querySelector("#cpHex"); hx.value="#f80"; hx.dispatchEvent(new Event("input"));
          hx.dispatchEvent(new KeyboardEvent("keydown",{key:"Enter",bubbles:true,cancelable:true})); await tick(); log.push("hex: "+colour()+" closed="+!popEl());
        """, file="revomate_studio.html")
        self.assertIn("palette chips=59", log)
        self.assertIn("click decides: closed=true colour=#ff0000", log)
        self.assertIn("hsl moved to #00ff00", log)
        self.assertIn("outside click undoes: true", log)
        self.assertIn("hex: #ff8800 closed=true", log)
        self.assertFalse([l for l in log if l.startswith("ERROR")])


    def test_number_boxes_and_esc_are_the_same_as_the_pro_page(self):
        log = run_page(r"""
          const esc=()=>document.dispatchEvent(new KeyboardEvent("keydown",{key:"Escape",bubbles:true,cancelable:true}));
          const vis=id=>!$(id).classList.contains("hidden");
          const rs=[...document.querySelectorAll('input[type="range"]:not(.cp-range)')]; log.push("boxes="+rs.filter(r=>r.closest(".range-wrap")).length+"/"+rs.length);
          const nb=$("dialSensCwInput").parentNode.querySelector(".range-num"); nb.value="500"; nb.dispatchEvent(new Event("change")); await tick(); log.push("clamped="+$("dialSensCwInput").value);
          openBackupModal(); await tick(); esc(); await tick(); log.push("backup closed="+!vis("backupModal"));
          await openDryRunModal(); await tick(); esc(); await tick(); log.push("dry run closed="+!vis("dryRunModal"));
        """, file="revomate_studio.html")
        self.assertIn("boxes=2/2", log)
        self.assertIn("clamped=100", log)
        self.assertIn("backup closed=true", log)
        self.assertIn("dry run closed=true", log)

BUTTON_SCENARIO = r"""
  const labels=()=>[...new Set([...document.querySelectorAll("#deviceSvg text")].map(t=>t.textContent.trim()).filter(x=>/^[0-9]+$/.test(x)))];
  log.push("numbers="+labels().sort((a,b)=>a-b).join(","));
  const clone=document.body.cloneNode(true); clone.querySelectorAll("script,style").forEach(e=>e.remove());      // the text of the page, hidden windows too
  const leftovers=(clone.textContent.match(/(^|[^A-Za-z0-9])[RLB][1-4](?![A-Za-z0-9])|C1(?![A-Za-z0-9])/g)||[]);
  log.push("position names left on the page="+leftovers.length);
  const pos=n=>{ const c=document.querySelector("#node-SW"+n+" circle"); return [Math.round(c.getAttribute("cx")), Math.round(c.getAttribute("cy"))]; };
  const upright=()=>[...document.querySelectorAll("#swButtonsGroup text")].every(t=>!t.getAttribute("transform")||!/rotate\(180/.test(t.getAttribute("transform")));
  setOrientation("0"); await tick(); const a1=pos(1), a6=pos(6); const up0=upright();
  setOrientation("180"); await tick(); const b1=pos(1), b6=pos(6);
  log.push("button 1: "+a1+" -> "+b1+" | button 6: "+a6+" -> "+b6);
  log.push("moved to the opposite side: "+(a1[0]>240&&b1[0]<240&&a6[1]>b6[1]));
  log.push("text stays upright: "+(up0&&upright()));
  log.push("numbers still there after turning: "+labels().sort((a,b)=>a-b).join(","));
  setOrientation("0");
  // linked hover: a row lights its button, a button lights its row
  const row=document.querySelector('tr[data-target="SW3"]'); const node=document.getElementById("node-SW3");
  row.dispatchEvent(new MouseEvent("mouseenter")); await tick(60); const nodeLit=node.className.baseVal||node.getAttribute("class"); const rowLitBefore=row.className;
  row.dispatchEvent(new MouseEvent("mouseleave")); await tick(60);
  log.push("row hover lights the button: "+/hover|link|hl/i.test(nodeLit));
  node.dispatchEvent(new MouseEvent("mouseenter")); await tick(60); log.push("button hover lights the row: "+/hover|link|hl/i.test(row.className));
  node.dispatchEvent(new MouseEvent("mouseleave")); await tick(60); log.push("and it goes out: "+!/hover|link|hl/i.test(row.className));
"""


def check_buttons(test, log):
    test.assertIn("numbers=1,2,3,4,5,6,7,8,9,10,11", log)
    test.assertIn("position names left on the page=0", log)
    test.assertIn("moved to the opposite side: true", log)
    test.assertIn("text stays upright: true", log)
    test.assertIn("numbers still there after turning: 1,2,3,4,5,6,7,8,9,10,11", log)
    test.assertIn("row hover lights the button: true", log)
    test.assertIn("button hover lights the row: true", log)
    test.assertIn("and it goes out: true", log)
    test.assertFalse([l for l in log if l.startswith("ERROR")])


@unittest.skipUnless(os.path.exists(CHROME), "Google Chrome is not installed")
class ButtonNumbers(unittest.TestCase):

    def test_studio_page(self):
        check_buttons(self, run_page(BUTTON_SCENARIO, file="revomate_studio.html"))


GAP_SCENARIO = r"""
  const card=document.getElementById("previewCard")||document.querySelector(".preview-col .card"); const res=[];
  for (const o of ["0","180"]) { setOrientation(o); await tick(150);
    const c=card.getBoundingClientRect(); const kids=[...card.querySelectorAll("*")].filter(e=>e.id!=="baseLedLine"&&e.getBoundingClientRect().height>0&&getComputedStyle(e).visibility!=="hidden");
    const top=Math.min(...kids.map(e=>e.getBoundingClientRect().top)), bottom=Math.max(...kids.map(e=>e.getBoundingClientRect().bottom));
    res.push(Math.abs(Math.round((top-c.top)-(c.bottom-bottom)))); }
  log.push("difference between the room above and below: "+res.join(","));
"""


@unittest.skipUnless(os.path.exists(CHROME), "Google Chrome is not installed")
class PreviewIsCentred(unittest.TestCase):
    def check(self, file):
        log = run_page(GAP_SCENARIO, file=file)
        diffs = [int(x) for x in [l for l in log if l.startswith("difference")][0].split(": ")[1].split(",")]
        self.assertTrue(all(d <= 4 for d in diffs), log)      # the drawing sits in the middle of its card in both orientations


    def test_studio(self):
        self.check("revomate_studio.html")





if __name__ == "__main__":
    unittest.main()
