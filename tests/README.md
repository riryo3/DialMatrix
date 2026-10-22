# Tests

## 追加した安全性テスト（実機・アプリ起動なし）

プロジェクトのルートで、システムPythonとNode.jsを使います。HIDは明示的に偽物へ差し替え、アプリのmainやHTTPサーバーは起動しません。ビルド済みアプリ・同梱依存環境も使用しません。

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_write_safety tests.test_device_lock tests.test_request_serialization -v
node --test tests/test_write_ui.js
```

詳細は `../TEST_RESULTS.md` を参照してください。下記は従来のmacOS向けテストです。全件discoverにはアプリ起動を伴うテストも含まれるため、本修正作業では全件実行していません。

## 従来のテスト

Run all of them with the app's Python (it has the Mac libraries):

    build_app/.venv/bin/python -m unittest discover -s tests -v

- `test_server.py`   the device functions (plan / write / preview / save-file) against a fake device made from a saved flash image
- `test_pages.py` the pages in headless Chrome (import checks, colour picker, button numbers, Esc, ...)
- `test_sound.py`    the completion sound (saved choice, endpoints, the question window)
- `test_port.py`     a free port is used when the usual one is taken by another program

Notes
- The page tests build their test page from the CURRENT page file every time (a stale copy once hid a bug).
- Headless Chrome is not the app's WebKit: for layout / zoom use `build_app/webkit_fit_check.py`.
- `test_server.py` needs a 2 MB flash image: `factory.mbf` or a `revomate_backup_*.bin` in the project folder or in `~/Documents/DialMatrix バックアップ/`（前の名前のフォルダでも可）. It is skipped without one.
