"""共享 httpx 客户端池：同 key 复用、配置变化重建、关闭时回收。"""

import pytest

from server.infrastructure import http as http_pool


class _FakeClient:
    """最小替身：只关心构造参数与关闭状态。"""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.is_closed = False
        self.aclosed = False

    async def aclose(self) -> None:
        self.aclosed = True
        self.is_closed = True


@pytest.fixture
def fake_clients(monkeypatch: pytest.MonkeyPatch) -> list[_FakeClient]:
    created: list[_FakeClient] = []

    def _factory(**kwargs):
        client = _FakeClient(**kwargs)
        created.append(client)
        return client

    monkeypatch.setattr(http_pool.httpx, "AsyncClient", _factory)
    return created


def test_same_key_reuses_one_instance(fake_clients: list[_FakeClient]) -> None:
    first = http_pool.get_client(proxy="socks5://127.0.0.1:1080", timeout=30)
    second = http_pool.get_client(proxy="socks5://127.0.0.1:1080", timeout=30)

    assert first is second
    assert len(fake_clients) == 1
    # 参数确实透传给了 httpx，而不是被默默丢掉
    assert fake_clients[0].kwargs["proxy"] == "socks5://127.0.0.1:1080"
    assert fake_clients[0].kwargs["timeout"] == 30


def test_different_key_creates_another_instance(fake_clients: list[_FakeClient]) -> None:
    http_pool.get_client(proxy=None, timeout=30)
    http_pool.get_client(proxy=None, timeout=60)
    http_pool.get_client(proxy="http://127.0.0.1:10808", timeout=30)

    assert len(fake_clients) == 3


def test_closed_instance_is_replaced(fake_clients: list[_FakeClient]) -> None:
    first = http_pool.get_client(proxy=None, timeout=30)
    first.is_closed = True

    second = http_pool.get_client(proxy=None, timeout=30)

    assert second is not first
    assert len(fake_clients) == 2


@pytest.mark.asyncio
async def test_close_all_closes_and_clears(fake_clients: list[_FakeClient]) -> None:
    http_pool.get_client(proxy=None, timeout=30)
    http_pool.get_client(proxy=None, timeout=60)

    await http_pool.close_all()

    assert [client.aclosed for client in fake_clients] == [True, True]
    # 缓存已清空：再取会重新创建
    http_pool.get_client(proxy=None, timeout=30)
    assert len(fake_clients) == 3


def test_reset_drops_cache_without_closing(fake_clients: list[_FakeClient]) -> None:
    http_pool.get_client(proxy=None, timeout=30)

    http_pool.reset_http_clients()

    assert fake_clients[0].aclosed is False
    http_pool.get_client(proxy=None, timeout=30)
    assert len(fake_clients) == 2
