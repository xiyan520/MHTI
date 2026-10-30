"""DATA_DIR 的生效范围。

环境变量长期只写在 Dockerfile / compose 里、代码写死项目根，换挂载点后
SQLite、密钥、日志会各写各的。这里钉住「同一个 DATA_DIR 派生所有持久化路径」。
"""

from pathlib import Path

import server.infrastructure.security as security
from server.infrastructure.config import DATA_DIR, _resolve_data_dir
from server.infrastructure.db.connection import DATABASE_PATH
from server.main import LOG_DIR


def test_env_override_wins(tmp_path: Path) -> None:
    assert _resolve_data_dir(str(tmp_path)) == tmp_path.resolve()


def test_default_is_project_data_dir() -> None:
    project_root = Path(__file__).resolve().parents[2]
    assert _resolve_data_dir(None) == project_root / "data"


def test_database_encryption_key_and_logs_share_data_dir() -> None:
    assert DATABASE_PATH.parent == DATA_DIR
    assert security.KEY_FILE.parent == DATA_DIR
    assert LOG_DIR == DATA_DIR / "logs"
