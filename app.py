"""Local single-user server. Python standard library only."""
import argparse
import json
import mimetypes
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from agent.model import ModelError, Settings
from agent.service import Conflict, Service

ROOT = Path(__file__).resolve().parent


def make_handler(service, settings, port):
    class Handler(BaseHTTPRequestHandler):
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
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def check_host(self):
            valid = {f"127.0.0.1:{port}", f"localhost:{port}"}
            if self.headers.get("Host") not in valid:
                raise PermissionError("仅支持本机访问")
            origin = self.headers.get("Origin")
            if origin and origin not in {"http://" + host for host in valid}:
                raise PermissionError("不接受其他网页发起的请求")

        def dispatch(self, method):
            try:
                self.check_host()
                path = urlsplit(self.path).path
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
                if method == "GET" and path in {"/", "/app.js", "/style.css"}:
                    file = ROOT / "web" / ("index.html" if path == "/" else path[1:])
                    return self.send(200, file.read_bytes(), (mimetypes.guess_type(file)[0] or "text/plain") + "; charset=utf-8")
                if method == "GET" and path == "/api/config":
                    return self.send(200, {"app": "shike-tree", "version": 2, "build": "local-guidance-2", "patch": "summary-card-1", "settings": settings.public()})
                if method == "POST" and path == "/api/restart":
                    if any(service.get(s["id"])["request_status"] == "GENERATING" for s in service.list()):
                        raise Conflict("正在生成回复，请等本轮完成后再应用更新。")
                    self.server.restart_requested = True
                    self.send(202, {"restarting": True})
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return
                if method == "POST" and path == "/api/settings":
                    return self.send(200, settings.connect(payload))
                if method == "GET" and path == "/api/example":
                    return self.send(200, json.loads((ROOT / "examples" / "tree.json").read_text(encoding="utf-8")))
                if path == "/api/sessions":
                    if method == "GET":
                        return self.send(200, service.list())
                    return self.send(201, service.create(payload.get("text"), payload.get("request_id")))
                if method == "GET" and path == "/api/cards":
                    return self.send(200, service.cards())
                if path == "/api/profile":
                    if method == "GET":
                        return self.send(200, service.profile())
                if method == "POST" and path == "/api/profile/events":
                    return self.send(200, service.profile_event(payload))
                parts = path.strip("/").split("/")
                if len(parts) == 3 and parts[:2] == ["api", "sessions"] and method == "GET":
                    return self.send(200, service.get(parts[2]))
                if len(parts) == 4 and parts[:2] == ["api", "sessions"] and parts[3] == "events" and method == "POST":
                    return self.send(200, service.event(parts[2], payload))
                self.send(404, {"error": "页面不存在"})
            except KeyError:
                self.send(404, {"error": "会话不存在"})
            except Conflict as exc:
                self.send(409, {"error": str(exc)})
            except PermissionError as exc:
                self.send(403, {"error": str(exc)})
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
    parser.add_argument("--port", type=int, default=8782)
    parser.add_argument("--data", type=Path, default=ROOT / "data")
    args = parser.parse_args()
    # A one-shot handoff lets a restricted/background service stop itself so a
    # freshly authorized launcher can take over the same port. The marker is
    # consumed before opening the database or binding a socket.
    handoff = args.data / ".handoff-stop-once"
    if handoff.exists():
        handoff.unlink()
        return
    settings = Settings(args.data / "settings.json")
    service = Service(args.data / "shike.sqlite3", settings)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(service, settings, args.port))
    print(f"Shike tree ready: http://127.0.0.1:{args.port}", flush=True)
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
