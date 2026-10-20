# Tests

Run all of them with the app's Python (it has the Mac libraries):

    build_app/.venv/bin/python -m unittest discover -s tests -v

- `test_server.py`   the device functions (plan / write / preview / save-file) against a fake device made from a saved flash image
- `test_pro_page.py` the pages in headless Chrome (import checks, colour picker, button numbers, Esc, ...)
- `test_sound.py`    the completion sound (saved choice, endpoints, the question window)
- `test_port.py`     a free port is used when the usual one is taken by another program

Notes
- The page tests build their test page from the CURRENT page file every time (a stale copy once hid a bug).
- Headless Chrome is not the app's WebKit: for layout / zoom use `build_app/webkit_fit_check.py`.
- `test_server.py` needs a 2 MB flash image: `factory.mbf` or a `revomate_backup_*.bin` in the project folder or in `~/Documents/DialMatrix バックアップ/`（前の名前のフォルダでも可）. It is skipped without one.
