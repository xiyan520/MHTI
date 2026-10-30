"""Security utilities for encryption and decryption."""

from cryptography.fernet import Fernet, InvalidToken

from server.infrastructure.config import DATA_DIR

# 使用与数据库相同的数据目录存储密钥文件（跟随 DATA_DIR，避免换挂载点后密钥“丢失”）
KEY_FILE = DATA_DIR / ".secret_key"


def get_encryption_key() -> bytes:
    """Get or generate encryption key."""
    # 确保数据目录存在
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)

    if KEY_FILE.exists():
        return KEY_FILE.read_bytes()
    key = Fernet.generate_key()
    KEY_FILE.write_bytes(key)
    return key


_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    """缓存 Fernet 实例：密钥文件只在首次使用时读一次。

    配置读取几乎每个请求都会触发，过去每次都读文件 + 重建对象；
    密钥在进程生命周期内不会变，缓存掉即可。
    """
    global _fernet
    if _fernet is None:
        _fernet = Fernet(get_encryption_key())
    return _fernet


def encrypt(data: str) -> str:
    """Encrypt a string."""
    return _get_fernet().encrypt(data.encode()).decode()


def decrypt(data: str) -> str | None:
    """Decrypt a string. Returns None if decryption fails."""
    try:
        return _get_fernet().decrypt(data.encode()).decode()
    except InvalidToken:
        return None


# Aliases for backward compatibility
encrypt_value = encrypt
decrypt_value = decrypt
