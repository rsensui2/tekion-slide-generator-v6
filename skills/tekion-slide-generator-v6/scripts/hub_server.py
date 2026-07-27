#!/usr/bin/env python3
"""TEKION Slides Hub - 台帳駆動の固定ポート常駐ダッシュボード。"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from review_deck import DashboardService, build_html
from session_registry import list_sessions, resolve_session_id, session_id


DEFAULT_PORT = 7799
VIEWER_WINDOW_SECONDS = 10.0
MAX_JSON_BODY = 512 * 1024 * 1024
SESSION_ROUTE = re.compile(r"^/s/([0-9a-f]{12})(/.*)?$")


def _plugin_version() -> str:
    """このランタイムの版（プラグイン版 + 実体のハッシュ）。

    インストーラが焼き込んだ値を優先し、無ければ自分自身の scripts/ から計算する。
    固定の版番号を名乗ってはいけない — 古いランタイムが「最新である」と
    主張したまま動き続けることになる（実際にそうなっていた）。
    """
    override = os.environ.get("TEKION_HUB_VERSION")
    if override:
        return override
    try:
        from hub_version import fingerprint
        return fingerprint()
    except Exception:
        return "unknown"


HUB_VERSION = _plugin_version()


class HubHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address):
        super().__init__(address, HubRequestHandler)
        self.initialize_runtime_state()

    def initialize_runtime_state(self) -> None:
        """bind 後の可変状態を初期化する（bind 禁止環境の HTTP テストでも再利用）。"""
        self._state_lock = threading.Lock()
        self._manifest_locks: dict[str, threading.Lock] = {}
        self._services: dict[str, DashboardService] = {}
        self._last_seen: dict[str, float] = {}
        slides_home = Path(
            os.environ.get("TEKION_SLIDES_HOME", "~/.tekion-slides")
        ).expanduser()
        self.home_session_dir = str(
            slides_home / "sessions" / "slides_output" / "_hub_home"
        )

    def service_for(self, sid: str, path: str) -> DashboardService:
        with self._state_lock:
            service = self._services.get(sid)
            if service is None or service.session_dir != path:
                lock = self._manifest_locks.setdefault(sid, threading.Lock())
                service = DashboardService(path, lock, restrict_paths=True)
                self._services[sid] = service
            return service

    def note_view(self, key: str) -> None:
        with self._state_lock:
            self._last_seen[key] = time.monotonic()

    def viewer_active(self, key: str) -> bool:
        with self._state_lock:
            seen = self._last_seen.get(key, 0.0)
        return (time.monotonic() - seen) <= VIEWER_WINDOW_SECONDS


class HubRequestHandler(BaseHTTPRequestHandler):
    server: HubHTTPServer

    def _host_allowed(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        hostname = host.rsplit(":", 1)[0].strip("[]")
        return hostname in ("127.0.0.1", "localhost")

    def _respond_json(self, obj, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _respond_html(self, text: str, status: int = 200) -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json_body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("invalid Content-Length") from exc
        if length < 0 or length > MAX_JSON_BODY:
            raise ValueError("request body too large")
        data = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("JSON body must be an object")
        return data

    def _session_route(self):
        parsed = urlsplit(self.path)
        match = SESSION_ROUTE.fullmatch(parsed.path)
        if not match:
            return None
        sid = match.group(1)
        session_dir = resolve_session_id(sid)
        if not session_dir:
            return None
        route = match.group(2) or "/"
        return sid, route, parsed.query, self.server.service_for(sid, session_dir)

    def _send_download(self, path: str, filename: str, mime: str) -> None:
        size = os.path.getsize(path)
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header(
            "Content-Disposition",
            f"attachment; filename*=UTF-8''{quote(filename)}",
        )
        self.send_header("Content-Length", str(size))
        self.end_headers()
        with open(path, "rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)

    def _send_inline_file(self, path: str, mime: str) -> None:
        size = os.path.getsize(path)
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "max-age=3600")
        self.end_headers()
        with open(path, "rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)

    def _send_thumbnail(self, body: bytes | None, cache_seconds: int) -> None:
        if body is None:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", f"max-age={cache_seconds}")
        self.end_headers()
        self.wfile.write(body)

    def _send_static(self, service: DashboardService, route: str) -> None:
        relative = unquote(route.lstrip("/"))
        if "\x00" in relative:
            self.send_error(404)
            return
        target = os.path.realpath(os.path.join(service.session_dir, relative))
        if (not target.startswith(service.session_dir + os.sep)
                or not os.path.isfile(target)):
            self.send_error(404)
            return
        mime = mimetypes.guess_type(target)[0] or "application/octet-stream"
        self._send_inline_file(target, mime)

    def _root_page(self) -> str:
        return build_html(
            self.server.home_session_dir,
            use_thumbs=True,
            page="home",
            hub_mode=True,
        )

    def do_GET(self) -> None:
        if not self._host_allowed():
            self.send_error(403)
            return
        parsed = urlsplit(self.path)
        if parsed.path == "/healthz":
            self._respond_json({
                "ok": True,
                "version": HUB_VERSION,
                "pid": os.getpid(),
                "port": self.server.server_address[1],
            })
            return
        if parsed.path == "/viewers":
            self._respond_json({"active": self.server.viewer_active("hub")})
            return
        if parsed.path == "/status":
            self.server.note_view("hub")
            self._respond_json({
                "total": 0,
                "counts": {},
                "slides": [],
                "session": {},
            })
            return
        if parsed.path == "/":
            self.server.note_view("hub")
            self._respond_html(self._root_page())
            return

        resolved = self._session_route()
        if not resolved:
            self.send_error(404)
            return
        sid, route, query, service = resolved
        params = parse_qs(query)
        base_path = f"/s/{sid}"

        if route == "/viewers":
            self._respond_json({"active": self.server.viewer_active(sid)})
        elif route in ("/", "/review.html"):
            self._respond_html(
                build_html(
                    service.session_dir,
                    use_thumbs=True,
                    page="deck",
                    base_path=base_path,
                    hub_mode=True,
                )
            )
        elif route == "/home":
            self.send_response(302)
            self.send_header("Location", "/")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif route == "/status":
            # viewers の確認アクセスではなく、実際のデッキ画面のポーリングだけを記録する。
            self.server.note_view(sid)
            self._respond_json(service.status())
        elif route.startswith("/thumb/"):
            rel = unquote(route[len("/thumb/"):])
            try:
                width = int((params.get("w") or ["1600"])[0])
                body = service.thumbnail(rel, width)
            except (OSError, ValueError):
                body = None
            self._send_thumbnail(body, 86400)
        elif route == "/session-thumb":
            try:
                width = int((params.get("w") or ["480"])[0])
                body = service.thumbnail("", width, cover=True)
            except (OSError, ValueError):
                body = None
            self._send_thumbnail(body, 3600)
        elif route == "/prompt":
            result, status = service.prompt((params.get("slide") or [""])[0])
            self._respond_json(result, status)
        elif route == "/script":
            result, status = service.script((params.get("slide") or [""])[0])
            self._respond_json(result, status)
        elif route == "/sessions":
            rows = [row for row in list_sessions(limit=15) if row["exists"]]
            for row in rows:
                row["url"] = f"/s/{row['sid']}/"
            self._respond_json({"sessions": rows})
        elif route in ("/export/pptx", "/export/pdf"):
            kind = route.rsplit("/", 1)[1]
            out_path, filename = service.export(kind)
            if not out_path:
                self.send_error(500, "export failed")
                return
            mime = (
                "application/vnd.openxmlformats-officedocument.presentationml.presentation"
                if kind == "pptx"
                else "application/pdf"
            )
            self._send_download(out_path, filename, mime)
        else:
            self._send_static(service, route)

    def _spawn_feedback_worker(self, session_dir: str) -> None:
        """修正指示の自動処理ワーカーを切り離しで起動する（チャット不要の赤入れループ）。

        ハブは launchd 常駐でサンドボックス外のため、ワーカーは送信元の
        エージェント/タブと無関係に生き続ける。多重起動はワーカー側の
        .worker.lock が防ぐので、送信のたびに起動を試みてよい。
        TEKION_HUB_AUTOWORKER=0 で無効化（チャット主導に戻す）。
        """
        if os.environ.get("TEKION_HUB_AUTOWORKER", "1") == "0":
            return
        worker = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "feedback_worker.py")
        if not os.path.exists(worker):
            return
        try:
            import subprocess
            log_path = os.path.join(session_dir, "feedback_worker.log")
            with open(log_path, "a", encoding="utf-8") as log:
                subprocess.Popen([sys.executable, "-u", worker,
                                  "--session-dir", session_dir],
                                 stdout=log, stderr=log,
                                 start_new_session=True)
        except OSError as exc:
            print(f"⚠️  feedback worker の起動に失敗: {exc}")

    def do_POST(self) -> None:
        if not self._host_allowed():
            self.send_error(403)
            return
        parsed = urlsplit(self.path)
        resolved = self._session_route()
        root_import = parsed.path == "/import"
        if not resolved and not root_import:
            self.send_error(404)
            return
        try:
            payload = self._json_body()
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            self.send_error(400)
            return

        if root_import:
            route = "/import"
            sid = ""
            service = DashboardService(self.server.home_session_dir, restrict_paths=True)
        else:
            sid, route, _query, service = resolved

        try:
            if route == "/select-version":
                result, status = service.select_version(
                    str(payload.get("slide", "")),
                    str(payload.get("image", "")),
                )
                self._respond_json(result, status)
            elif route == "/reorder":
                result, status = service.reorder(payload.get("order"))
                self._respond_json(result, status)
            elif route in ("/delete-slide", "/restore-slide"):
                result, status = service.set_removed(
                    str(payload.get("slide", "")),
                    removed=route == "/delete-slide",
                )
                self._respond_json(result, status)
            elif route == "/feedback":
                service.save_feedback(payload)
                self._spawn_feedback_worker(service.session_dir)
                self._respond_json({"ok": True})
            elif route == "/script":
                slide = str(payload.get("slide", ""))
                result, status = service.save_script(slide, payload.get("script"))
                # 「保存して描き直す」= 通常の作り直し依頼として同じキューに積む。
                # ワーカーがプロファイルのエディタを呼び、脚本からプロンプトを引き直す
                if status == 200 and payload.get("regenerate"):
                    service.save_feedback({
                        "feedback": {slide: "脚本を書き換えたので、新しい脚本の内容で描き直す。"},
                        "rebuild": [slide],
                    })
                    self._spawn_feedback_worker(service.session_dir)
                self._respond_json(result, status)
            elif route == "/import":
                new_session = root_import or payload.get("mode") == "new_session"
                result = service.import_files(payload.get("files") or [], new_session)
                target = result.pop("target_dir")
                result["url"] = None
                if new_session and result["added"] > 0:
                    new_sid = session_id(target)
                    if resolve_session_id(new_sid) != os.path.realpath(target):
                        raise RuntimeError("取り込みセッションを台帳から解決できません")
                    result["url"] = f"/s/{new_sid}/"
                self._respond_json(result)
            elif route == "/open-session":
                target_sid = session_id(str(payload.get("path", "")))
                target = resolve_session_id(target_sid)
                if not target:
                    self._respond_json({"ok": False, "error": "session not found"}, 404)
                else:
                    self._respond_json({"ok": True, "url": f"/s/{target_sid}/"})
            else:
                self.send_error(404)
        except Exception as exc:
            self._respond_json(
                {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                500,
            )

    def log_message(self, _format, *args) -> None:
        pass


def start_hub(port: int | None = None) -> HubHTTPServer:
    if port is None:
        try:
            port = int(os.environ.get("TEKION_DASHBOARD_PORT", str(DEFAULT_PORT)))
        except ValueError:
            port = DEFAULT_PORT
    # セキュリティ不変条件: 外部 IF や全 IF を選べる引数は設けない。
    return HubHTTPServer(("127.0.0.1", port))


def main() -> int:
    parser = argparse.ArgumentParser(description="TEKION Slides Hub")
    parser.add_argument(
        "--port",
        type=int,
        help="待受ポート（既定: TEKION_DASHBOARD_PORT または 7799、0 はテスト用）",
    )
    parser.add_argument("--url-file", help="起動 URL をファイルへ書く（統合テスト用）")
    args = parser.parse_args()

    server = start_hub(args.port)
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}/"
    if args.url_file:
        with open(args.url_file, "w", encoding="utf-8") as handle:
            handle.write(url)
    print(f"🌐 TEKION Slides Hub: {url}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
