#!/usr/bin/env python3
"""Command line client for the FEniCSx CAE server. Standard library only, so it
runs on Windows without installing anything. The BlendFEA Blender extension ships a
copy of this file (its build copies it from here), so this one is the source of truth.

  python fxclient.py login --server https://fea.example.com   # once; asks for the token
  python fxclient.py submit spec.json part.msh --wait --get out/
  python fxclient.py list
  python fxclient.py status <job>
  python fxclient.py get <job> out_dir         # download + unzip results
  python fxclient.py keep <job>                # never auto-delete this job
  python fxclient.py info                      # limits + disk usage
  python fxclient.py materials

Server/token: --server/--token, else env FX_SERVER/FX_TOKEN, else the saved
login (~/.config/fenicsx/client.json, %APPDATA%\\fenicsx\\client.json on Windows).
Meshes are gzipped before upload (3-5x smaller).
"""

from __future__ import annotations

import argparse
import getpass
import gzip
import json
import os
import ssl
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import zipfile

__version__ = "0.3.0"
API_VERSION = 1          # must match fxserver.API_VERSION
USER_AGENT = f"fxclient/{__version__}"   # urllib's default UA is blocked by some proxies
DEFAULT_SERVER = "https://fea.roboticos.co"


class FxError(RuntimeError):
    """Error with a message meant for the user. `.code`: HTTP status, or
    'unreachable' / 'ssl' for connection problems."""

    def __init__(self, msg, code=None):
        super().__init__(msg)
        self.code = code


def _friendly(code: int, detail: str, server: str) -> str:
    if code == 401:
        return ("The server rejected the API token (missing or wrong). "
                "Copy FX_TOKEN from the server's .env into your settings.")
    if code == 403:
        return (f"Blocked before reaching the server (HTTP 403: {detail[:120]}). "
                "A proxy/firewall such as Cloudflare Access or bot protection refused the request.")
    if code == 413:
        return (f"Mesh too large to upload ({detail}). Use a coarser mesh, or raise "
                "FX_MAX_UPLOAD_MB on the server (the internet tunnel allows 100 MB at most).")
    if code == 429:
        return f"Server is busy: {detail}"
    if code == 507:
        return f"Server is out of disk space: {detail}. Delete old jobs or free space on the server."
    if code in (502, 503, 504, 520, 521, 522, 523, 524, 530):
        return (f"{server} answered, but the FEniCSx server behind it did not (HTTP {code}). "
                "Is the container running on the server (docker compose up -d) and the tunnel up?")
    return f"HTTP {code}: {detail}"


# ---------------------------------------------------------------- saved login
def config_path() -> str:
    base = os.environ.get("APPDATA") if os.name == "nt" else \
        os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    return os.path.join(base or os.path.expanduser("~"), "fenicsx", "client.json")


def load_saved() -> dict:
    try:
        with open(config_path()) as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def save_login(server: str, token: str) -> str:
    path = config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump({"server": server, "token": token}, fh)
    return path


# ---------------------------------------------------------------- upload body
class _Body:
    """File-like multipart body: streams the parts and reports progress."""

    def __init__(self, parts: list[bytes], progress=None):
        self.parts, self.i, self.off = parts, 0, 0
        self.total = sum(len(p) for p in parts)
        self.sent, self.progress = 0, progress

    def __len__(self):
        return self.total

    def read(self, n=-1):
        if self.i >= len(self.parts):
            return b""
        n = 1 << 16 if n is None or n < 0 else n
        part = self.parts[self.i]
        chunk = part[self.off:self.off + n]
        self.off += len(chunk)
        if self.off >= len(part):
            self.i, self.off = self.i + 1, 0
        self.sent += len(chunk)
        if self.progress:
            self.progress(self.sent, self.total)
        return chunk


class Client:
    def __init__(self, server: str, token: str = "", timeout: float = 60):
        self.server = (server or DEFAULT_SERVER).rstrip("/")
        self.token = token
        self.timeout = timeout

    def _open(self, method, path, body=None, headers=None):
        h = {"Accept": "application/json", "User-Agent": USER_AGENT, **(headers or {})}
        if self.token:
            h["X-API-Key"] = self.token
        req = urllib.request.Request(self.server + path, data=body, method=method, headers=h)
        try:
            return urllib.request.urlopen(req, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors="replace")
            try:
                msg = json.loads(msg).get("detail", msg)
            except Exception:
                pass
            raise FxError(_friendly(e.code, str(msg), self.server), e.code) from None
        except urllib.error.URLError as e:
            reason = e.reason
            if isinstance(reason, ssl.SSLError):
                raise FxError(f"TLS/certificate problem talking to {self.server}: {reason}",
                              "ssl") from None
            raise FxError(f"Can't reach {self.server} ({reason}). Check the server URL and "
                          "that the server is running.", "unreachable") from None
        except (TimeoutError, OSError) as e:
            raise FxError(f"Can't reach {self.server} ({e}).", "unreachable") from None

    def _req(self, method, path, body=None, headers=None, raw=False):
        with self._open(method, path, body, headers) as r:
            data = r.read()
        return data if raw else json.loads(data or b"null")

    # ---- connection
    def health(self):
        return self._req("GET", "/api/health")

    def info(self):
        return self._req("GET", "/api/info")

    def check(self) -> tuple[str, str, dict]:
        """('ok'|'auth'|'unreachable'|'error', message, info)."""
        try:
            health = self.health()
            info = self.info()
        except FxError as e:
            kind = "auth" if e.code == 401 else ("unreachable" if e.code in ("unreachable", "ssl", 502, 503, 504, 530) else "error")
            return kind, str(e), {}
        d = info.get("disk", {})
        msg = (f"Connected to {self.server} (v{info.get('version')}, "
               f"{d.get('free_gb', 0):.0f} GB free)")
        srv_api = health.get("api_version", 1)
        if srv_api != API_VERSION:
            newer = "client" if srv_api > API_VERSION else "server"
            msg += (f". Warning: server API v{srv_api}, client API v{API_VERSION}; "
                    f"update the {newer}")
        return "ok", msg, info

    def materials(self):
        return self._req("GET", "/api/materials")

    # ---- jobs
    def submit(self, spec: dict, mesh_path: str, compress: bool = True, progress=None):
        """progress(sent_bytes, total_bytes) is called while uploading."""
        with open(mesh_path, "rb") as fh:
            mesh = fh.read()
        name = os.path.basename(mesh_path)
        if compress and not name.lower().endswith(".gz"):
            mesh = gzip.compress(mesh, compresslevel=6)
            name += ".gz"
        boundary = uuid.uuid4().hex
        body = _Body([
            f'--{boundary}\r\nContent-Disposition: form-data; name="spec"\r\n'
            f"Content-Type: application/json\r\n\r\n".encode() + json.dumps(spec).encode() + b"\r\n",
            f'--{boundary}\r\nContent-Disposition: form-data; name="mesh"; filename="{name}"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n".encode(),
            mesh,
            f"\r\n--{boundary}--\r\n".encode(),
        ], progress)
        old = self.timeout
        self.timeout = max(old, 600)       # slow uplinks
        try:
            return self._req("POST", "/api/jobs", body, {
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Content-Length": str(len(body))})
        finally:
            self.timeout = old

    def list(self):
        return self._req("GET", "/api/jobs")

    def status(self, job_id):
        return self._req("GET", f"/api/jobs/{job_id}")

    def result(self, job_id):
        return self._req("GET", f"/api/jobs/{job_id}/result")

    def log(self, job_id):
        return self._req("GET", f"/api/jobs/{job_id}/log", raw=True).decode(errors="replace")

    def cancel(self, job_id):
        return self._req("POST", f"/api/jobs/{job_id}/cancel")

    def keep(self, job_id, keep=True):
        return self._req("POST", f"/api/jobs/{job_id}/keep?keep={'true' if keep else 'false'}")

    def delete(self, job_id):
        return self._req("DELETE", f"/api/jobs/{job_id}")

    def download(self, job_id, out_dir, progress=None):
        """Stream results.zip to disk and unzip. progress(received, total_or_0)."""
        os.makedirs(out_dir, exist_ok=True)
        fd, tmp = tempfile.mkstemp(suffix=".zip", dir=out_dir)
        try:
            with os.fdopen(fd, "wb") as fh, self._open("GET", f"/api/jobs/{job_id}/results.zip") as r:
                total = int(r.headers.get("Content-Length") or 0)
                got = 0
                while chunk := r.read(1 << 16):
                    fh.write(chunk)
                    got += len(chunk)
                    if progress:
                        progress(got, total)
            with zipfile.ZipFile(tmp) as zf:
                zf.extractall(out_dir)
        finally:
            os.unlink(tmp)
        return out_dir

    def report_url(self, job_id):
        # No token in URLs (browser history / proxy logs); browsers sign in at /login.
        return f"{self.server}/api/jobs/{job_id}/files/report.html"

    def wait(self, job_id, poll=2.0, callback=None):
        while True:
            st = self.status(job_id)
            if callback:
                callback(st)
            if st["state"] not in ("queued", "running"):
                return st
            time.sleep(poll)


def _mb(n):
    return f"{n / 1e6:.1f} MB"


def _bar(prefix):
    last = [0.0]

    def cb(done, total):
        now = time.time()
        if now - last[0] < 0.3 and done < total:
            return
        last[0] = now
        pct = f" {100 * done / total:3.0f}%" if total else ""
        print(f"\r  {prefix} {_mb(done)}" + (f" / {_mb(total)}" if total else "") + pct,
              end="", flush=True, file=sys.stderr)
        if total and done >= total:
            print(file=sys.stderr)
    return cb


def _print_summary(st):
    s = st.get("summary") or {}
    print(f"{st['id']}  {st['state']}  {st.get('name', '')}")
    if st.get("error"):
        print("  error:", st["error"])
    if s.get("headline"):
        print("  " + s["headline"])
    for k, v in s.items():
        if k != "headline":
            print(f"  {k:34s} {v}")
    for w in st.get("warnings", []):
        print("  ! " + w)


def main(argv=None):
    saved = load_saved()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--server", default=os.environ.get("FX_SERVER") or saved.get("server") or DEFAULT_SERVER)
    ap.add_argument("--token", default=os.environ.get("FX_TOKEN") or saved.get("token", ""))
    sub = ap.add_subparsers(dest="cmd", required=True)
    lg = sub.add_parser("login", help="save server + token for later commands (token is prompted)")
    lg.add_argument("--server", dest="login_server", help="server URL to save")
    sub.add_parser("logout", help="forget the saved token")
    p = sub.add_parser("submit")
    p.add_argument("spec")
    p.add_argument("mesh")
    p.add_argument("--wait", action="store_true")
    p.add_argument("--get", metavar="DIR", help="download results to DIR when done")
    for c in ("status", "log", "cancel", "delete", "result"):
        sub.add_parser(c).add_argument("job")
    g = sub.add_parser("get")
    g.add_argument("job")
    g.add_argument("dir")
    k = sub.add_parser("keep", help="exempt a job from automatic cleanup")
    k.add_argument("job")
    k.add_argument("--off", action="store_true", help="allow cleanup again")
    sub.add_parser("list")
    sub.add_parser("materials")
    sub.add_parser("health")
    sub.add_parser("info")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "login":
            server = (a.login_server or a.server).rstrip("/")
            token = os.environ.get("FX_TOKEN") or getpass.getpass("API token (FX_TOKEN): ").strip()
            kind, msg, _ = Client(server, token).check()
            print(msg)
            if kind != "ok":
                return 1
            print("saved to", save_login(server, token))
            return 0
        if a.cmd == "logout":
            save_login(saved.get("server", a.server), "")
            print("token removed from", config_path())
            return 0

        c = Client(a.server, a.token)
        if a.cmd == "submit":
            with open(a.spec) as fh:
                spec = json.load(fh)
            spec.setdefault("api_version", API_VERSION)
            st = c.submit(spec, a.mesh, progress=_bar("uploading"))
            print("submitted", st["id"])
            if a.wait or a.get:
                last = [None]

                def cb(s):
                    stage = (s.get("progress") or {}).get("stage", s["state"])
                    if stage != last[0]:
                        print("  ..", stage, flush=True)
                        last[0] = stage
                st = c.wait(st["id"], callback=cb)
                _print_summary(st)
                if a.get and st["state"] == "done":
                    print("results in", c.download(st["id"], a.get, progress=_bar("downloading")))
                return 0 if st["state"] == "done" else 1
        elif a.cmd == "list":
            for st in c.list():
                s = st.get("summary") or {}
                star = "*" if st.get("keep") else " "
                print(f"{st['id']}{star} {st['state']:9s} {s.get('verdict', ''):8s} {st.get('name', '')}")
        elif a.cmd == "status":
            _print_summary(c.status(a.job))
        elif a.cmd == "result":
            print(json.dumps(c.result(a.job), indent=2))
        elif a.cmd == "log":
            print(c.log(a.job))
        elif a.cmd == "get":
            print("results in", c.download(a.job, a.dir, progress=_bar("downloading")))
        elif a.cmd == "cancel":
            print(c.cancel(a.job)["state"])
        elif a.cmd == "delete":
            c.delete(a.job)
        elif a.cmd == "materials":
            for name, m in c.materials().items():
                print(f"{name:16s} {m['description']}")
        elif a.cmd == "health":
            print(c.check()[1])
        elif a.cmd == "info":
            print(json.dumps(c.info(), indent=2))
        elif a.cmd == "keep":
            st = c.keep(a.job, not a.off)
            print(f"{st['id']} keep={st.get('keep')}")
    except FxError as e:
        print("error:", e, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
