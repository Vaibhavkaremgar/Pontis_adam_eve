from app.db.database_url import normalize_database_url


def test_normalize_database_url_adds_sslmode_and_timeout_for_proxy_host():
    url = "postgresql+psycopg2://user:pass@yamanote.proxy.rlwy.net:51654/dbname"

    normalized = normalize_database_url(url)

    assert "sslmode=require" in normalized
    assert "connect_timeout=10" in normalized


def test_normalize_database_url_leaves_non_proxy_urls_unchanged():
    url = "postgresql://user:pass@localhost:5432/dbname"

    assert normalize_database_url(url) == url
