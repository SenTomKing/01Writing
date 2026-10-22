"""配置加载与按需检查模型密钥的测试。"""

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from writing_assistant.cli import build_workflow
from writing_assistant.config import AppConfig, load_config
from writing_assistant.errors import ConfigurationError


class ConfigTests(unittest.TestCase):
    def test_document_features_can_load_config_without_api_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project_root = Path(directory)
            with patch.dict(os.environ, {}, clear=True):
                config = load_config(project_root=project_root)

        self.assertIsNone(config.api_key)
        self.assertEqual(config.data_file, project_root / "data" / "revisions.json")

    def test_ai_workflow_still_requires_api_key(self) -> None:
        config = AppConfig(
            api_key=None,
            base_url="https://api.deepseek.com",
            model="test-model",
            data_file=Path("project.json"),
            max_input_chars=100,
            request_timeout_seconds=10,
        )

        with self.assertRaisesRegex(ConfigurationError, "DEEPSEEK_API_KEY"):
            build_workflow(config)


if __name__ == "__main__":
    unittest.main()
