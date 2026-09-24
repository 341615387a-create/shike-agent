"""Local-first server with an isolated anonymous workspace mode for public previews."""
import argparse
import hashlib
import hmac
import json
import mimetypes
import os
import secrets
import sys
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from agent.model import ModelError, Settings
from agent.service import Conflict, Service

ROOT = Path(__file__).resolve().parent


def make_handler(service, settings, port, public_mode=False, allowed_origins=None, allowed_hosts=None,
                 cookie_secret="", hourly_turn_limit=20):
    allowed_origins = allowed_origins or set()
    allowed_hosts = allowed_hosts or set()
    class Handler(BaseHTTPRequestHandler):
        rate_lock = threading.Lock()
        rate_windows = {}

        def log_message(self, fmt, *args):
            pass  # No request bodies, credentials or dialogue in access logs.

        def send(self, status, value, content_type="application/json; charset=utf-8"):
            data = json.dumps(value, ensure_ascii=False).encode("utf-8") if isinstance(value, (dict, list)) else value
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            if getattr(self, "new_workspace_cookie", None):
                secure = "; Secure" if public_mode else ""
                self.send_header("Set-Cookie", f"shike_workspace={self.new_workspace_cookie}; Path=/; Max-Age=31536000; HttpOnly; SameSite=Lax{secure}")
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def check_host(self):
            if public_mode:
                if self.headers.get("Host") not in allowed_hosts:
                    raise PermissionError("访问地址无效")
                origin = self.headers.get("Origin")
                if origin and origin.rstrip("/") not in allowed_origins:
                    raise PermissionError("不接受其他网页发起的请求")
                return
            valid = {f"127.0.0.1:{port}", f"localhost:{port}"}
            if self.headers.get("Host") not in valid:
                raise PermissionError("仅支持本机访问")
            origin = self.headers.get("Origin")
            if origin and origin not in {"http://" + host for host in valid}:
                raise PermissionError("不接受其他网页发起的请求")

        def owner_id(self):
            if not public_mode:
                return "default"
            try:
                raw = SimpleCookie(self.headers.get("Cookie", "")).get("shike_workspace")
            except Exception:
                raw = None
            value = raw.value if raw else ""
            try:
                workspace, signature = value.split(".", 1)
            except ValueError:
                workspace, signature = "", ""
            expected = hmac.new(cookie_secret.encode(), workspace.encode(), hashlib.sha256).hexdigest()
            if len(workspace) == 32 and hmac.compare_digest(signature, expected):
                return workspace
            workspace = secrets.token_hex(16)
            signature = hmac.new(cookie_secret.encode(), workspace.encode(), hashlib.sha256).hexdigest()
            self.new_workspace_cookie = workspace + "." + signature
            return workspace

        def check_rate(self):
            if not public_mode:
                return
            forwarded = self.headers.get("X-Forwarded-For", "").split(",")[-1].strip()
            key = forwarded or self.client_address[0]
            current = time.time()
            with self.rate_lock:
                recent = [stamp for stamp in self.rate_windows.get(key, []) if current - stamp < 3600]
                if len(recent) >= hourly_turn_limit:
                    raise OverflowError(f"这个网络每小时最多生成 {hourly_turn_limit} 轮，请稍后再试。")
                recent.append(current)
                self.rate_windows[key] = recent

        def dispatch(self, method):
            try:
                path = urlsplit(self.path).path
                if method == "GET" and path == "/healthz":
                    return self.send(200, {"ok": True})
                self.check_host()
                owner_id = self.owner_id()
                payload = None
                if method == "POST":
                    if self.headers.get_content_type() != "application/json":
                        raise ValueError("请使用 JSON 请求")
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= 128000:
                        raise ValueError("请求大小无效")
                    payload = json.loads(self.rfile.read(size))
                    if not isinstance(payload, dict):
                        raise ValueError("请求须为对象")
                if method == "GET" and path in {"/", "/app.js", "/demo-api.js", "/style.css"}:
                    file = ROOT / "web" / ("index.html" if path == "/" else path[1:])
                    return self.send(200, file.read_bytes(), (mimetypes.guess_type(file)[0] or "text/plain") + "; charset=utf-8")
                if method == "GET" and path == "/api/config":
                    return self.send(200, {"app": "shike-tree", "version": 2, "build": "local-guidance-2",
                                           "patch": "public-render-1", "public_mode": public_mode,
                                           "settings": settings.public()})
                if method == "POST" and path == "/api/restart":
                    if public_mode:
                        raise PermissionError("公开服务不允许远程重启")
                    if any(service.get(s["id"], owner_id)["request_status"] == "GENERATING"
                           for s in service.list(owner_id)):
                        raise Conflict("正在生成回复，请等本轮完成后再应用更新。")
                    self.server.restart_requested = True
                    self.send(202, {"restarting": True})
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return
                if method == "POST" and path == "/api/settings":
                    if public_mode:
                        raise PermissionError("公开服务的模型由服务器统一配置")
                    return self.send(200, settings.connect(payload))
                if method == "GET" and path == "/api/example":
                    return self.send(200, json.loads((ROOT / "examples" / "tree.json").read_text(encoding="utf-8")))
                if path == "/api/sessions":
                    if method == "GET":
                        return self.send(200, service.list(owner_id))
                    self.check_rate()
                    return self.send(201, service.create(payload.get("text"), payload.get("request_id"), owner_id))
                if method == "GET" and path == "/api/cards":
                    return self.send(200, service.cards(owner_id))
                if path == "/api/profile":
                    if method == "GET":
                        return self.send(200, service.profile(owner_id))
                if method == "POST" and path == "/api/profile/events":
                    return self.send(200, service.profile_event(payload, owner_id))
                parts = path.strip("/").split("/")
                if len(parts) == 3 and parts[:2] == ["api", "sessions"] and method == "GET":
                    return self.send(200, service.get(parts[2], owner_id))
                if len(parts) == 4 and parts[:2] == ["api", "sessions"] and parts[3] == "events" and method == "POST":
                    if payload.get("action") in {"message", "focus", "change", "skip", "reflect", "retry", "resume"}:
                        self.check_rate()
                    return self.send(200, service.event(parts[2], payload, owner_id))
                self.send(404, {"error": "页面不存在"})
            except KeyError:
                self.send(404, {"error": "会话不存在"})
            except Conflict as exc:
                self.send(409, {"error": str(exc)})
            except PermissionError as exc:
                self.send(403, {"error": str(exc)})
            except OverflowError as exc:
                self.send(429, {"error": str(exc)})
            except (ValueError, ModelError) as exc:
                self.send(400, {"error": str(exc)})
            except Exception:
                self.send(500, {"error": "服务未能完成操作。请保留输入后重试。"})

        def do_GET(self):
            self.dispatch("GET")

        def do_POST(self):
            self.dispatch("POST")

    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8782")))
    parser.add_argument("--data", type=Path, default=Path(os.getenv("DATA_DIR", ROOT / "data")))
    args = parser.parse_args()
    public_mode = os.getenv("PUBLIC_MODE", "").lower() in {"1", "true", "yes"} or bool(os.getenv("RENDER"))
    public_urls = [value.strip().rstrip("/") for value in os.getenv("PUBLIC_ORIGIN", "").split(",") if value.strip()]
    render_url = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
    if render_url:
        public_urls.append(render_url)
    allowed_origins = set(public_urls)
    allowed_hosts = {urlsplit(value).netloc for value in allowed_origins}
    cookie_secret = os.getenv("COOKIE_SECRET", "")
    hourly_turn_limit = max(1, int(os.getenv("HOURLY_TURN_LIMIT", "20")))
    if public_mode and (not allowed_hosts or len(cookie_secret) < 24 or not os.getenv("LLM_API_KEY")):
        raise SystemExit("公开模式需要 RENDER_EXTERNAL_URL/PUBLIC_ORIGIN、COOKIE_SECRET（至少24字符）和 LLM_API_KEY。")
    # A one-shot handoff lets a restricted/background service stop itself so a
    # freshly authorized launcher can take over the same port. The marker is
    # consumed before opening the database or binding a socket.
    handoff = args.data / ".handoff-stop-once"
    if handoff.exists():
        handoff.unlink()
        return
    settings = Settings(args.data / "settings.json")
    service = Service(args.data / "shike.sqlite3", settings)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(
        service, settings, args.port, public_mode, allowed_origins, allowed_hosts, cookie_secret,
        hourly_turn_limit))
    print(f"Shike tree ready: {args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    if getattr(server, "restart_requested", False):
        os.execv(sys.executable, [sys.executable, str(ROOT / "app.py"), *sys.argv[1:]])


if __name__ == "__main__":
    main()
