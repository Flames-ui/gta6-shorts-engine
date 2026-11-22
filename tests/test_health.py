"""Test root dashboard, static assets, and health endpoints."""

def test_root_dashboard_serves_html(client):
    """Verify that GET / returns the HTML dashboard frontend, not JSON."""
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert "<title>GTA 6 Shorts Studio</title>" in response.text
    assert "/static/styles.css" in response.text


def test_static_assets_serve_correctly(client):
    """Verify that CSS and JavaScript assets load properly from /static/."""
    # Test CSS
    css_res = client.get("/static/styles.css")
    assert css_res.status_code == 200
    assert "text/css" in css_res.headers.get("content-type", "")
    assert len(css_res.text) > 100

    # Test JavaScript
    js_res = client.get("/static/app.js")
    assert js_res.status_code == 200
    assert "javascript" in js_res.headers.get("content-type", "")
    assert len(js_res.text) > 100


def test_health_check(client):
    """Verify standard /health endpoint remains healthy."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "redis" in data
    assert "supabase" in data


def test_service_status_endpoint(client):
    """Verify /status endpoint serves service diagnostic info."""
    response = client.get("/status")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "online"
    assert "GTA 6 Shorts Engine" in data["service"]
    assert "/health" in data["endpoints"]["health"]
