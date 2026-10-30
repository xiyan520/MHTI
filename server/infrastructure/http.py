"""共享的 httpx 客户端池。

一次刮削要打 6 个以上请求（搜索 / 详情 / 季详情 / 海报 / 背景 / 缩略图），过去每次
都新建 AsyncClient，等于每次重做一遍 TCP + TLS 握手；走 SOCKS5 代理时代价更高。

这里按 (proxy, timeout) 复用同一实例：连接池与 TLS 会话都能复用，配置改动后
key 自然变化，旧实例在关闭时统一回收。
"""

import logging

import httpx

logger = logging.getLogger(__name__)

# key: (proxy, timeout) —— follow_redirects 走单次请求参数，不进 key
_clients: dict[tuple[str, float], httpx.AsyncClient] = {}


def get_client(
    *,
    proxy: str | None = None,
    timeout: float = 30.0,
) -> httpx.AsyncClient:
    """按 (proxy, timeout) 取共享客户端，首次调用时创建。"""
    key = (proxy or "", float(timeout))

    client = _clients.get(key)
    if client is not None and not client.is_closed:
        return client

    client = httpx.AsyncClient(timeout=timeout, proxy=proxy)
    _clients[key] = client
    return client


async def close_all() -> None:
    """关闭所有缓存的客户端（应用关闭时调用）。"""
    if not _clients:
        return

    for key, client in list(_clients.items()):
        try:
            await client.aclose()
        except Exception as exc:  # noqa: BLE001 - 关闭失败不应阻断退出流程
            logger.warning(f"关闭 HTTP 客户端失败 {key}: {exc}")
    _clients.clear()


def reset_http_clients() -> None:
    """丢弃缓存实例（测试用）。

    测试里常见 patch("httpx.AsyncClient") 的写法：不重置缓存的话，前一个用例
    创建的实例会被后一个用例复用，patch 就落空了。
    """
    _clients.clear()
