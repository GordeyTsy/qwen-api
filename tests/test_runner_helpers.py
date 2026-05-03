import pathlib
import os
import tempfile
import unittest
from unittest import mock

_test_root = tempfile.TemporaryDirectory()
os.environ.setdefault("QWEN_WORK_DIR", str(pathlib.Path(_test_root.name) / "working"))
os.environ.setdefault("QWEN_TEMP_DIR", str(pathlib.Path(_test_root.name) / "temp"))

from qwen_api import kaggle_runner


class RunnerHelperTests(unittest.TestCase):
    def test_redact_masks_known_secret(self):
        with mock.patch.object(kaggle_runner, "API_KEY", "sk-kaggle-secret"):
            redacted = kaggle_runner.redact("Authorization: Bearer sk-kaggle-secret")
        self.assertNotIn("sk-kaggle-secret", redacted)
        self.assertIn("[REDACTED]", redacted)

    def test_sha256(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "sample.txt"
            path.write_text("abc")
            self.assertEqual(
                kaggle_runner.sha256(path),
                "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
            )

    def test_parse_model_files(self):
        with mock.patch.object(kaggle_runner, "MODEL_FILES", "A:a.gguf,B:b.gguf"):
            self.assertEqual(kaggle_runner.parse_model_files(), [("A", "a.gguf"), ("B", "b.gguf")])

    def test_verify_manifest(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            runtime = pathlib.Path(tmp) / "runtime"
            runtime.mkdir()
            file_path = runtime / "llama-server"
            file_path.write_text("abc")
            (runtime / "SHA256SUMS").write_text(kaggle_runner.sha256(file_path) + "  llama-server\n")
            with mock.patch.object(kaggle_runner, "RUNTIME", runtime):
                kaggle_runner.verify_manifest()


if __name__ == "__main__":
    unittest.main()
