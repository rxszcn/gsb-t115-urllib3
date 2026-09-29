"""把 urllib3 编出去的 multipart 头喂给三个独立接收端，看各自能不能原样读回来。

For every input cell we take the bytes urllib3 puts on the wire and feed them
to three independent parsers:

  R1 email        stdlib email parser （多数服务端拿它读体）
  R2 werkzeug     werkzeug.formparser -- the parser the repo's own dummy server
                  (flask) uses to read request bodies
  R3 strict       按 RFC 9110 的 quoted-string 手写的一个分词器

and report, per cell, whether each oracle gets the (name, filename,
content-type, payload) back unchanged, and how many parts it sees.

"""

from __future__ import annotations

import io
import email
import re

from urllib3.fields import RequestField
from urllib3.filepost import encode_multipart_formdata

BOUNDARY = "UFbOuNd1"
CT = f"multipart/form-data; boundary={BOUNDARY}"

NAMES = ["plain", "back\\slash", "trail\\", 'quo"te', "nl\nnl", "cr\rcr", "pct%0Alit",
         "semi;colon", "e-é", "ski ⛷", "nul\x00nul", "sp ace"]


def parts_from_email(body: bytes):
    msg = email.message_from_bytes(
        f"Content-Type: {CT}\r\n\r\n".encode("latin-1") + body
    )
    out = []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        cd = part.get("Content-Disposition")
        if hasattr(cd, "decode"):
            cd = cd.decode("utf-8", "replace")
        out.append(
            (
                part.get_param("name", header="content-disposition"),
                part.get_filename(),
                part.get_content_type(),
                part.get_payload(decode=False),
            )
        )
    return out


STRICT_PARAM = re.compile(r'(?i)(^|;)\s*([a-z-]+)=(?:"((?:[^"\\]|\\.)*)"|([^";]*))')


def strict_tokenize(value: str):
    """RFC 9110: quoted-string = DQUOTE *(qdtext / quoted-pair) DQUOTE"""
    params = {}
    pos = 0
    kind = value.split(";", 1)[0].strip()
    for m in re.finditer(
        r'(?i);\s*([a-z-]+)=(?:"((?:[^"\\]|\\.)*)"|([^";\s][^;]*))', value
    ):
        name = m.group(1).lower()
        if m.group(2) is not None:
            # a well-formed quoted string; unescape quoted-pairs
            params[name] = ("quoted", re.sub(r"\\(.)", r"\1", m.group(2)))
        else:
            params[name] = ("bare", m.group(3).strip())
    # detect an unterminated quoted string
    for m in re.finditer(r'(?i);\s*[a-z-]+="', value):
        tail = value[m.end():]
        try:
            unescaped = re.sub(r"\\(.)", r"\1", tail)
        except Exception:  # noqa: BLE001
            continue
        if '"' not in re.sub(r"\\.", "", tail):
            params["__unterminated__"] = ("error", m.group(0))
    return kind, params, pos


def parts_from_strict(body: bytes):
    text = body.decode("utf-8", "surrogateescape")
    chunks = text.split("--" + BOUNDARY)
    out = []
    notes = []
    for chunk in chunks[1:]:
        if chunk.startswith("--"):
            break
        head, _, data = chunk.lstrip("\r\n").partition("\r\n\r\n")
        data = data.rstrip("\r\n")
        lines = [ln for ln in head.split("\r\n") if ln]
        hdrs: dict[str, list[str]] = {}
        for line in lines:
            key, _, val = line.partition(":")
            hdrs.setdefault(key.strip().lower(), []).append(val.strip())
        cd = ";".join(hdrs.get("content-disposition", []))
        ctype = ";".join(hdrs.get("content-type", []))
        if len(hdrs.get("content-disposition", [])) > 1 or len(
            hdrs.get("content-type", [])) > 1:
            notes.append("duplicate-part-header")
        extra = [k for k in hdrs if k not in ("content-disposition", "content-type",
                                             "content-location")]
        if extra:
            notes.append(f"injected:{','.join(sorted(extra))}")
        kind, params, _ = strict_tokenize(cd)
        name = params.get("name", (None, None))[1]
        fn = params.get("filename", (None, None))[1]
        if "__unterminated__" in params:
            notes.append("unterminated-quoted-string")
        out.append((name, fn, ctype or None, data, kind))
    return out, notes


def parts_from_werkzeug(body: bytes):
    from werkzeug.test import EnvironBuilder
    from werkzeug.wrappers import Request

    builder = EnvironBuilder(method="POST", data=body, content_type=CT)
    req = Request(builder.get_environ())
    form = dict(req.form.items())
    files = {k: (v.filename, v.stream.read()) for k, v in req.files.items()}
    return form, files


def show_cell(label, fields):
    try:
        body, ctype = encode_multipart_formdata(fields, boundary=BOUNDARY)
    except Exception as ex:  # noqa: BLE001
        print(f"--- {label}")
        print(f"    encode RAISED {type(ex).__name__}: {ex}")
        return
    assert ctype == CT, ctype
    print(f"--- {label}")
    print(f"    wire   : {body!r}")
    try:
        e = parts_from_email(body)
        print(f"    email  : {e}")
    except Exception as ex:  # noqa: BLE001
        print(f"    email  : RAISED {type(ex).__name__}: {ex}")
    try:
        s, notes = parts_from_strict(body)
        print(f"    strict : {s} notes={notes}")
    except Exception as ex:  # noqa: BLE001
        print(f"    strict : RAISED {type(ex).__name__}: {ex}")
    try:
        w = parts_from_werkzeug(body)
        print(f"    wergz  : {w}")
    except Exception as ex:  # noqa: BLE001
        print(f"    wergz  : RAISED {type(ex).__name__}: {ex}")


def main() -> None:
    for n in NAMES:
        show_cell(f"filename={n!r}", [("up", (n, b"DATA", "text/plain"))])
    for n in NAMES:
        show_cell(f"field name={n!r}", [(n, "V")])
    show_cell("content-type CRLF", [("f", ("x.txt", b"D", "text/plain\r\nX-Inj: 1"))])
    show_cell("content-type semicolon", [("f", ("x.txt", b"D", 'text/plain; q="1"'))])
    show_cell("guessed ct from weird filename", [("f", ("a.txt\r\nX: 1", b"D"))])
    show_cell("explicit part headers",
              [RequestField("f", b"D", filename="x.txt",
                            headers={"Content-Type": "text/x",
                                     "content-type": "text/y",
                                     "X-Keep": "", "X-Zero": "0"})])
    show_cell("payload carries boundary", [("f", ("x.txt", b"a\r\n--" + BOUNDARY.encode() + b"\r\nb", "text/plain"))])
    show_cell("empty filename vs no filename",
              [("f1", ("", b"D", "text/plain")), ("f2", b"D")])
    show_cell("latin1 bytes filename", [("f", (b"caf\xe9.txt", b"D"))])
    show_cell("same name twice", [("f", "1"), ("f", "2")])


if __name__ == "__main__":
    main()
