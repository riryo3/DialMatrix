import sys, os, threading, time, http.server, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PORT = int(sys.argv[1]); EDITION = sys.argv[2]; W, H = int(sys.argv[3]), int(sys.argv[4])
os.environ["REVOMATE_PORT"] = str(PORT); sys.argv = ["x", "--edition", EDITION]
import revomate_app as a, webview
srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), a.StudioHandler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
win = webview.create_window("t", f"http://127.0.0.1:{PORT}/", width=W, height=H)
JS = """(function(){const d=document.documentElement,b=document.body;
const out={zoom:b.style.zoom,inner:innerWidth+'x'+innerHeight,docScrollH:d.scrollHeight,bodyScrollH:b.scrollHeight,bodyRectH:Math.round(b.getBoundingClientRect().height),bodyMinH:b.style.minHeight,scrollY:scrollY,docClientH:d.clientHeight};
let worst=null;document.querySelectorAll('body *').forEach(e=>{const r=e.getBoundingClientRect();if(r.height>0&&(!worst||r.bottom>worst.b)){worst={b:Math.round(r.bottom),tag:e.tagName,id:e.id,cls:(e.className||'').toString().slice(0,40)}}});
out.lowest=worst;return JSON.stringify(out)})()"""
def probe():
    time.sleep(6)
    print(win.evaluate_js(JS), flush=True)
    os._exit(0)
threading.Thread(target=probe, daemon=True).start()
webview.start()
