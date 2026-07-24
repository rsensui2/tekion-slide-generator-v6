from __future__ import annotations

import json
import io
import os
import socket
import sys
import tempfile
import threading
import unittest
from http.client import parse_headers
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

import hub_server
import review_deck
import session_registry


class FakeHTTPServer:
    """TCP bind が禁止された環境で Handler を直接駆動するための最小サーバ。"""

    def __init__(self, _address, handler_class):
        self.RequestHandlerClass = handler_class
        self.server_address = ("127.0.0.1", 43210)
        self.server_name = "127.0.0.1"
        self.server_port = 43210
        self.shutdown_called = threading.Event()

    def shutdown(self):
        self.shutdown_called.set()

    def server_close(self):
        pass


class HubServerIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.old_db_path = session_registry.DB_PATH
        session_registry.DB_PATH = self.root / "registry" / "sessions.db"
        # テストでは自動ワーカーを起動しない（実生成が走り、キューを ack してしまう）
        os.environ["TEKION_HUB_AUTOWORKER"] = "0"
        self.addCleanup(os.environ.pop, "TEKION_HUB_AUTOWORKER", None)

        self.session_a = self._make_session(
            "session-a",
            "統合テストデッキA",
            ["01_alpha", "02_beta"],
        )
        self.session_b = self._make_session(
            "session-b",
            "統合テストデッキB",
            ["01_gamma"],
        )
        session_registry.upsert(str(self.session_a))
        session_registry.upsert(str(self.session_b))
        self.sid_a = session_registry.session_id(str(self.session_a))
        self.sid_b = session_registry.session_id(str(self.session_b))

        self.network_server = True
        try:
            self.server = hub_server.start_hub(0)
        except PermissionError:
            # Codex の workspace sandbox は TCP bind を EPERM にする。通常環境では
            # 上の実ポートを使い、ここでは同じ HTTP Handler を socketpair で駆動する。
            self.network_server = False
            self.server = object.__new__(hub_server.HubHTTPServer)
            self.server.server_address = ("127.0.0.1", 0)
            self.server.server_name = "127.0.0.1"
            self.server.server_port = 0
            self.server.initialize_runtime_state()
        self.thread = None
        if self.network_server:
            self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self.thread.start()
            self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        if self.network_server:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=3)
        session_registry.DB_PATH = self.old_db_path
        self.temp_dir.cleanup()

    def _make_image(self, path: Path, color: tuple[int, int, int], label: str):
        image = Image.new("RGB", (320, 180), color)
        draw = ImageDraw.Draw(image)
        draw.rectangle((24, 24, 296, 156), outline=(255, 255, 255), width=4)
        draw.text((40, 78), label, fill=(255, 255, 255))
        image.save(path)

    def _make_session(self, name: str, title: str, bases: list[str]) -> Path:
        session = self.root / "slides_output" / name
        images = session / "images"
        plans = session / "json"
        images.mkdir(parents=True)
        plans.mkdir(parents=True)
        slides = {}
        for index, base in enumerate(bases):
            image_path = images / f"{base}.png"
            self._make_image(image_path, (50 + index * 30, 80, 130), base)
            versions = [str(image_path)]
            if index == 0:
                v2 = images / f"{base}_v2.png"
                self._make_image(v2, (100, 70, 150), f"{base} v2")
                versions.append(str(v2))
            slides[base] = {
                "state": "validated",
                "current_image": str(image_path),
                "raw_image": None,
                "versions": versions,
            }
        manifest = {"version": 2, "slides": slides, "slide_order": bases}
        (session / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False),
            encoding="utf-8",
        )
        plan = {
            "slides": [{"title": title}],
            "total_slides": len(bases),
        }
        (plans / "slides_plan.json").write_text(
            json.dumps(plan, ensure_ascii=False),
            encoding="utf-8",
        )
        return session

    def _request(self, path: str, payload: dict | None = None):
        if not self.network_server:
            return self._socket_request(
                hub_server.HubRequestHandler,
                self.server,
                path,
                payload,
            )
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(self.base_url + path, data=data, headers=headers)
        try:
            with urlopen(request, timeout=10) as response:
                return response.status, response.headers, response.read()
        except HTTPError as exc:
            return exc.code, exc.headers, exc.read()

    @staticmethod
    def _socket_request(handler_class, server, path: str, payload: dict | None = None):
        server_socket, client_socket = socket.socketpair()
        body = (
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if payload is not None
            else b""
        )
        method = "POST" if payload is not None else "GET"
        request = (
            f"{method} {path} HTTP/1.1\r\n"
            "Host: 127.0.0.1\r\n"
            "Connection: close\r\n"
            f"Content-Length: {len(body)}\r\n"
            + ("Content-Type: application/json\r\n" if payload is not None else "")
            + "\r\n"
        ).encode("ascii") + body
        thread = threading.Thread(
            target=handler_class,
            args=(server_socket, ("127.0.0.1", 12345), server),
            daemon=True,
        )
        thread.start()
        client_socket.settimeout(10)
        client_socket.sendall(request)
        client_socket.shutdown(socket.SHUT_WR)
        response = bytearray()
        try:
            while True:
                chunk = client_socket.recv(1024 * 1024)
                if not chunk:
                    break
                response.extend(chunk)
                if b"\r\n\r\n" in response:
                    raw_head, response_body = bytes(response).split(b"\r\n\r\n", 1)
                    content_length = None
                    for line in raw_head.split(b"\r\n")[1:]:
                        if line.lower().startswith(b"content-length:"):
                            content_length = int(line.split(b":", 1)[1].strip())
                            break
                    if content_length is not None and len(response_body) >= content_length:
                        break
        except socket.timeout as exc:
            raise TimeoutError(f"HTTP response did not close: {method} {path}") from exc
        client_socket.close()
        thread.join(timeout=10)
        server_socket.close()
        if thread.is_alive():
            raise TimeoutError(f"HTTP handler did not finish: {method} {path}")
        head, response_body = bytes(response).split(b"\r\n\r\n", 1)
        status_line, raw_headers = head.split(b"\r\n", 1)
        status = int(status_line.split()[1])
        headers = parse_headers(io.BytesIO(raw_headers + b"\r\n\r\n"))
        return status, headers, response_body

    def test_hub_routes_mutations_export_and_security(self):
        status, _headers, body = self._request("/healthz")
        self.assertEqual(status, 200)
        health = json.loads(body)
        self.assertTrue(health["ok"])
        self.assertEqual(health["port"], self.server.server_address[1])

        status, _headers, body = self._request("/viewers")
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(body)["active"])

        status, _headers, body = self._request("/")
        self.assertEqual(status, 200)
        home = body.decode("utf-8")
        self.assertIn("統合テストデッキA", home)
        self.assertIn("統合テストデッキB", home)
        self.assertIn(f'href="/s/{self.sid_a}/"', home)
        self.assertIn(f'href="/s/{self.sid_b}/"', home)

        status, _headers, body = self._request("/viewers")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["active"])

        status, _headers, body = self._request(f"/s/{self.sid_a}/viewers")
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(body)["active"])

        status, _headers, body = self._request(f"/s/{self.sid_a}/")
        self.assertEqual(status, 200)
        deck_a = body.decode("utf-8")
        self.assertIn("01_alpha", deck_a)
        self.assertIn(f'const BASE = "/s/{self.sid_a}"', deck_a)
        status, headers, body = self._request(
            f"/s/{self.sid_a}/images/01_alpha.png"
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers.get_content_type(), "image/png")
        self.assertTrue(body.startswith(b"\x89PNG"))
        status, headers, body = self._request(
            f"/s/{self.sid_a}/thumb/images/01_alpha.png?w=160"
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers.get_content_type(), "image/jpeg")
        self.assertTrue(body.startswith(b"\xff\xd8"))

        status, _headers, body = self._request(f"/s/{self.sid_b}/")
        self.assertEqual(status, 200)
        self.assertIn("01_gamma", body.decode("utf-8"))
        self.assertNotIn("01_alpha", body.decode("utf-8"))

        status, _headers, body = self._request(f"/s/{self.sid_a}/status")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["total"], 2)
        status, _headers, body = self._request(f"/s/{self.sid_a}/viewers")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["active"])

        status, _headers, body = self._request(
            f"/s/{self.sid_a}/select-version",
            {"slide": "01_alpha", "image": "images/01_alpha_v2.png"},
        )
        self.assertEqual(status, 200, body)
        manifest = json.loads((self.session_a / "manifest.json").read_text(encoding="utf-8"))
        self.assertTrue(manifest["slides"]["01_alpha"]["current_image"].endswith("_v2.png"))

        status, _headers, body = self._request(
            f"/s/{self.sid_a}/reorder",
            {"order": ["02_beta", "01_alpha"]},
        )
        self.assertEqual(status, 200, body)
        manifest = json.loads((self.session_a / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["slide_order"], ["02_beta", "01_alpha"])

        status, _headers, body = self._request(
            f"/s/{self.sid_a}/delete-slide",
            {"slide": "02_beta"},
        )
        self.assertEqual(status, 200, body)
        manifest = json.loads((self.session_a / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["slides"]["02_beta"]["state"], "removed")

        feedback = {
            "session_dir": str(self.session_a),
            "feedback": {"01_alpha": "見出しを短く"},
            "rebuild": [],
        }
        status, _headers, body = self._request(
            f"/s/{self.sid_a}/feedback",
            feedback,
        )
        self.assertEqual(status, 200, body)
        saved = json.loads(
            (self.session_a / "slide_feedback.json").read_text(encoding="utf-8")
        )
        self.assertEqual(saved["feedback"]["01_alpha"], "見出しを短く")

        # Hub は feedback 保存後も終了せず、未処理バッジを表示する。
        status, _headers, body = self._request("/healthz")
        self.assertEqual(status, 200)
        status, _headers, body = self._request(f"/s/{self.sid_a}/")
        self.assertEqual(status, 200)
        self.assertIn("未処理の修正指示", body.decode("utf-8"))

        status, headers, body = self._request(f"/s/{self.sid_a}/export/pptx")
        self.assertEqual(status, 200, body[:200])
        self.assertEqual(
            headers.get_content_type(),
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        )
        self.assertTrue(body.startswith(b"PK"))

        status, _headers, _body = self._request("/s/000000000000/")
        self.assertEqual(status, 404)
        status, _headers, _body = self._request(
            f"/s/{self.sid_a}/%2e%2e/%2e%2e/etc/passwd"
        )
        self.assertEqual(status, 404)
        status, _headers, _body = self._request(
            f"/s/{self.sid_a}/thumb/%2e%2e/%2e%2e/etc/passwd?w=100"
        )
        self.assertEqual(status, 404)

    def test_legacy_serve_exits_on_feedback(self):
        try:
            handle = review_deck.start_server(
                str(self.session_b),
                timeout=10,
                open_browser=False,
                exit_on_feedback=True,
            )
            real_server = True
        except PermissionError:
            real_server = False

            with mock.patch("http.server.ThreadingHTTPServer", FakeHTTPServer):
                handle = review_deck.start_server(
                    str(self.session_b),
                    timeout=10,
                    open_browser=False,
                    exit_on_feedback=True,
                )

        feedback = {"feedback": {"01_gamma": "色を明るく"}, "rebuild": []}
        if real_server:
            thread = threading.Thread(target=handle.httpd.serve_forever, daemon=True)
            thread.start()
            legacy_base = handle.url.rstrip("/")
            with urlopen(legacy_base + "/", timeout=5) as response:
                self.assertEqual(response.status, 200)
                self.assertIn("01_gamma", response.read().decode("utf-8"))
            payload = json.dumps(feedback, ensure_ascii=False).encode("utf-8")
            request = Request(
                legacy_base + "/feedback",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urlopen(request, timeout=5) as response:
                self.assertEqual(response.status, 200)
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
        else:
            status, _headers, body = self._socket_request(
                handle.httpd.RequestHandlerClass,
                handle.httpd,
                "/",
            )
            self.assertEqual(status, 200)
            self.assertIn("01_gamma", body.decode("utf-8"))
            status, _headers, body = self._socket_request(
                handle.httpd.RequestHandlerClass,
                handle.httpd,
                "/feedback",
                feedback,
            )
            self.assertEqual(status, 200, body)
            self.assertTrue(handle.httpd.shutdown_called.wait(timeout=3))

        handle.timer.cancel()
        handle.httpd.server_close()
        self.assertTrue(handle.received.is_set())
        self.assertEqual(review_deck.report_feedback(handle), 0)

    def test_legacy_persist_and_await_feedback_remain_compatible(self):
        with mock.patch("http.server.ThreadingHTTPServer", FakeHTTPServer):
            handle = review_deck.start_server(
                str(self.session_b),
                timeout=10,
                open_browser=False,
                exit_on_feedback=False,
            )
        feedback = {"feedback": {"01_gamma": "余白を増やす"}, "rebuild": []}
        status, _headers, body = self._socket_request(
            handle.httpd.RequestHandlerClass,
            handle.httpd,
            "/feedback",
            feedback,
        )
        self.assertEqual(status, 200, body)
        self.assertTrue(handle.received.is_set())
        self.assertFalse(handle.httpd.shutdown_called.is_set())
        handle.timer.cancel()

        feedback_path = self.session_b / "slide_feedback.json"
        feedback_path.unlink()

        def write_feedback():
            review_deck.DashboardService(str(self.session_b)).save_feedback(feedback)

        timer = threading.Timer(0.1, write_feedback)
        timer.start()
        try:
            self.assertEqual(review_deck.await_feedback(str(self.session_b), timeout=3), 0)
        finally:
            timer.cancel()
            handle.httpd.server_close()


if __name__ == "__main__":
    unittest.main()
