"""上传文件名过一遍转义之后，三套接收端各自的读数。

跑法：env -u http_proxy -u https_proxy -u no_proxy PYTHONPATH=src .venv/bin/python repro/multipart-escape.py
"""

from __future__ import annotations

import importlib.util
import os

spec = importlib.util.spec_from_file_location(
    "mp_panel", os.path.join(os.path.dirname(__file__), "multipart-receivers.py")
)
mp = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mp)

from urllib3.filepost import encode_multipart_formdata  # noqa: E402

NAMES = [
    "plain",
    "back\\slash",
    "trail\\",
    'quo"te',
    "nl\nnl",
    "pct%0Alit",
    "pct%5Clit",
    "e-é",
    "semi;colon",
]


def main() -> None:
    print(f"{'input':15} | {'email':17} | {'strict':21} | werkzeug")
    for name in NAMES:
        body, ctype = encode_multipart_formdata(
            [("up", (name, b"DATA", "text/plain"))], boundary=mp.BOUNDARY
        )

        def v(rec: object) -> str:
            return "ok" if rec == name else f"BAD {rec!r}"

        try:
            e = mp.parts_from_email(body)[0][1]
        except Exception as ex:  # noqa: BLE001
            e = f"ERR {type(ex).__name__}"
        notes = ""
        try:
            s_rows, s_notes = mp.parts_from_strict(body)
            s = s_rows[0][1]
            notes = f" [{'/'.join(s_notes)}]" if s_notes else ""
        except Exception as ex:  # noqa: BLE001
            s = f"ERR {type(ex).__name__}"
        try:
            w_form, w_files = mp.parts_from_werkzeug(body)
            if "up" in w_files:
                w: object = w_files["up"][0]
                if w_files["up"][1] != b"DATA":
                    w = f"BAD-PAYLOAD {w_files['up'][1]!r}"
            elif "up" in w_form:
                w = f"DEMOTED-TO-FORM {w_form['up']!r}"
            else:
                w = f"PART-DROPPED form={w_form}"
        except Exception as ex:  # noqa: BLE001
            w = f"ERR {type(ex).__name__}"
        cd_line = body.split(b"\r\n")[1]
        print(f"{name!r:15} | {v(e):17} | {v(s) + notes:21} | {v(w)}")
        print(f"{'':15}   wire: {cd_line!r}")


if __name__ == "__main__":
    main()
