"""feedback_worker の E2E テスト（mock プロバイダ・実画像ベース）。

検証すること:
  - 複数送信をキュー順に自動処理し、成功分だけ ack する
  - 個別 rebuild + 添付、global 指示の全スライド展開、版数の増え方
  - 失敗を含む送信は dead-letter（feedback_history/failed/）へ移り、ack されない
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

import review_deck  # noqa: E402
from PIL import Image  # noqa: E402


def _make_slide_png(path: Path) -> None:
    """検証（30KB以上・非単色・16:9）を通るテスト画像を作る。"""
    import random
    rnd = random.Random(42)
    img = Image.new("RGB", (1280, 720))
    px = img.load()
    for x in range(0, 1280, 16):
        for y in range(0, 720, 16):
            c = (rnd.randrange(256), rnd.randrange(256), rnd.randrange(256))
            for dx in range(16):
                for dy in range(16):
                    px[x + dx, y + dy] = c
    img.save(path, format="PNG", compress_level=0)


class FeedbackWorkerE2ETest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.sess = Path(self.temp.name) / "slides_output" / "sess"
        (self.sess / "images" / "raw").mkdir(parents=True)
        (self.sess / "prompts").mkdir()
        src = Path(self.temp.name) / "src.png"
        _make_slide_png(src)
        slides = {}
        for i, base in enumerate(["00_alpha_01", "01_beta_01"]):
            img = self.sess / "images" / f"{base}.png"
            shutil.copy(src, img)
            (self.sess / "prompts" / f"{base}.txt").write_text(
                f"{base} のプロンプト", encoding="utf-8")
            slides[base] = {"state": "validated", "current_image": str(img),
                            "versions": [str(img)],
                            "prompt_file": str(self.sess / "prompts" / f"{base}.txt")}
        (self.sess / "manifest.json").write_text(
            json.dumps({"version": 1, "slides": slides}), encoding="utf-8")
        self.src = src

    def tearDown(self):
        self.temp.cleanup()

    def _run_worker(self, env_extra=None):
        env = dict(os.environ)
        # ワーカー子プロセスの save_manifest が実台帳（~/.tekion-slides）を汚さないように
        env["TEKION_SLIDES_HOME"] = str(Path(self.temp.name) / "slides-home")
        env.update(env_extra or {})
        return subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "feedback_worker.py"),
             "--session-dir", str(self.sess), "--provider", "mock"],
            capture_output=True, text=True, timeout=180, env=env)

    def test_queue_processing_and_ack(self):
        import base64
        svc = review_deck.DashboardService(str(self.sess))
        png_b64 = base64.b64encode(self.src.read_bytes()).decode()
        svc.save_feedback({
            "feedback": {"00_alpha_01":
                         "【作り直し】前の画像を参照せず、ゼロから再生成する。\n明るく"},
            "rebuild": ["00_alpha_01"],
            "attachments": {"00_alpha_01": [{"name": "ref.png", "data_b64": png_b64}]}})
        svc.save_feedback({"feedback": {}, "rebuild": [], "attachments": {},
                           "global": "余白を増やす", "global_keep_reference": False})
        self.assertEqual(len(review_deck.pending_feedback(str(self.sess))), 2)

        result = self._run_worker()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(review_deck.pending_feedback(str(self.sess)), [])
        self.assertEqual(review_deck.failed_feedback(str(self.sess)), [])
        manifest = json.loads((self.sess / "manifest.json").read_text(encoding="utf-8"))
        # 00: v1 + 個別rebuild + global、01: v1 + global
        self.assertEqual(len(manifest["slides"]["00_alpha_01"]["versions"]), 3)
        self.assertEqual(len(manifest["slides"]["01_beta_01"]["versions"]), 2)
        status = json.loads((self.sess / "session_status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["stage"], "done")

    def test_failed_payload_goes_to_dead_letter(self):
        svc = review_deck.DashboardService(str(self.sess))
        svc.save_feedback({"feedback": {"00_alpha_01": "壊れるはず"},
                           "rebuild": ["00_alpha_01"], "attachments": {}})
        # mock provider の障害注入で対象スライドを常に失敗させる
        result = self._run_worker({"TEKION_MOCK_FAIL_SLIDES": "00_alpha",
                                   "TEKION_MOCK_FAIL_TIMES": "99"})
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(review_deck.pending_feedback(str(self.sess)), [])
        failed = review_deck.failed_feedback(str(self.sess))
        self.assertEqual(len(failed), 1)
        payload = json.loads(Path(failed[0]).read_text(encoding="utf-8"))
        self.assertIn("00_alpha_01", payload["feedback"])
        status = json.loads((self.sess / "session_status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["stage"], "attention")
        # --ack-feedback で dead-letter がアーカイブされる
        self.assertEqual(review_deck.ack_feedback(str(self.sess)), 1)
        self.assertEqual(review_deck.failed_feedback(str(self.sess)), [])


if __name__ == "__main__":
    unittest.main()
