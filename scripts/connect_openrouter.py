"""Official OpenRouter PKCE consent; key is exchanged and saved without clipboard/chat."""

import argparse
import base64
from datetime import datetime, timezone
import hashlib
from http.server import HTTPServer, BaseHTTPRequestHandler
import json
from pathlib import Path
import secrets
import subprocess
import sys
import time
from urllib.parse import urlencode, urlparse, parse_qs
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status-file", required=True)
    parser.add_argument("--minutes", type=int, default=15)
    args = parser.parse_args()
    status_path = Path(args.status_file)
    status_path.parent.mkdir(parents=True, exist_ok=True)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = secrets.token_urlsafe(32)
    result = {"status": "starting", "created_at": datetime.now(timezone.utc).isoformat()}

    def update(**values):
        result.update(values)
        status_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *unused):
            pass  # OAuth code in the callback URL must never be logged.

        def respond(self, code, text):
            data = ("<!doctype html><meta charset=utf-8><title>O!Travel OpenRouter</title>"
                    "<body style='font-family:sans-serif;max-width:680px;margin:80px auto'>"
                    "<h1>O!Travel Agent</h1><p>" + text + "</p></body>").encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path != "/callback":
                self.respond(404, "Локальный обработчик подключения OpenRouter.")
                return
            if self.headers.get("Host") not in {f"localhost:{self.server.server_port}", f"127.0.0.1:{self.server.server_port}"}:
                self.respond(400, "Неверный адрес callback.")
                return
            query = parse_qs(parsed.query)
            if not secrets.compare_digest(query.get("state", [""])[0], state):
                self.respond(403, "Подключение не подтверждено. Откройте актуальную ссылку из чата.")
                return
            code = query.get("code", [""])[0]
            if not code or len(code) > 4096 or result["status"] == "saved":
                self.respond(400, "Нет нового кода авторизации.")
                return
            try:
                body = json.dumps({"code": code, "code_verifier": verifier, "code_challenge_method": "S256"}).encode()
                request = Request("https://openrouter.ai/api/v1/auth/keys", data=body,
                                  headers={"Content-Type": "application/json", "User-Agent": "OTravel-local-setup"}, method="POST")
                with urlopen(request, timeout=30) as response:
                    payload = json.load(response)
                key = payload.get("key")
                if not isinstance(key, str) or not key.startswith("sk-or-"):
                    raise ValueError("unexpected_key")
                script = str(Path(__file__).with_name("configure_keys.py").resolve())
                if sys.platform == "win32":
                    script = "/mnt/" + script[0].lower() + script[2:].replace("\\", "/")
                    command = ["wsl.exe", "-d", "AegML-Hackathon", "-u", "aegml", "--", "python3", script, "--write-stdin"]
                else:
                    command = [sys.executable, script, "--write-stdin"]
                saved = subprocess.run(command, input=json.dumps({"OPENROUTER_API_KEY": key, "LLM_PROVIDER": "openrouter"}).encode(),
                                       capture_output=True, timeout=40, check=False)
                if saved.returncode:
                    raise RuntimeError("save_failed")
                update(status="saved", saved_at=datetime.now(timezone.utc).isoformat(),
                       provider="openrouter", model="google/gemma-4-31b-it:free", key_displayed=False)
                self.respond(200, "OpenRouter подключён. Ключ сохранён локально в WSL .env и не выводится в чат. Можно вернуться в Codex; платные модели в приложении запрещены.")
            except HTTPError as exc:
                update(status="exchange_failed", error="http_" + str(exc.code))
                self.respond(502, "OpenRouter отклонил обмен кода. Ключ не показан. Сообщите об этом в Codex.")
            except (URLError, OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                update(status="connection_failed", error="oauth_or_local_save_failed")
                self.respond(502, "Подключение не завершилось. Секретные данные не выводятся; сообщите об ошибке в Codex.")

    server = HTTPServer(("127.0.0.1", 0), Handler)
    server.timeout = 1
    callback = f"http://localhost:{server.server_port}/callback"
    auth_url = "https://openrouter.ai/auth?" + urlencode({"callback_url": callback,
        "code_challenge": challenge, "code_challenge_method": "S256", "state": state,
        "key_label": "OTravel-Hackathon-FreeOnly"})
    update(status="waiting_for_user", authorization_url=auth_url, callback_port=server.server_port,
           expires_minutes=args.minutes, key_label="OTravel-Hackathon-FreeOnly")
    print("OAuth consent listener ready. No key is printed.", flush=True)
    until = time.monotonic() + max(2, min(30, args.minutes)) * 60
    try:
        while time.monotonic() < until and result["status"] != "saved":
            server.handle_request()
        if result["status"] != "saved":
            update(status="expired")
        else:
            print("OpenRouter key saved locally.", flush=True)
    finally:
        server.server_close()
    return 0 if result["status"] == "saved" else 2


if __name__ == "__main__":
    raise SystemExit(main())
