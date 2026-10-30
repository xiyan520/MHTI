"""CORS 行为：默认只放行本机开发端口，且不使用通配符 + 凭据的非法组合。"""

from fastapi.testclient import TestClient

from server.main import app


def _preflight(origin: str):
    return TestClient(app).options(
        "/api/config/system",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )


def test_dev_origin_is_allowed_with_credentials():
    response = _preflight("http://localhost:3000")

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert response.headers["access-control-allow-credentials"] == "true"


def test_unknown_origin_is_not_echoed():
    response = _preflight("https://evil.example.com")

    # 不在白名单时不能回显来源，否则等于对所有站点开放
    assert response.headers.get("access-control-allow-origin") is None


def test_wildcard_is_never_combined_with_credentials():
    """通配符 + 凭据是浏览器会直接拒绝的组合，配置层就不该产生它。"""
    response = _preflight("http://127.0.0.1:3000")

    allow_origin = response.headers.get("access-control-allow-origin")
    assert allow_origin == "http://127.0.0.1:3000"
    assert allow_origin != "*"
