"""Test root and health endpoints."""

def test_root_endpoint(client):
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "online"
    assert "GTA 6 Shorts Engine" in data["service"]
    assert "/health" in data["endpoints"]["health"]


def test_health_check(client):
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "redis" in data
    assert "supabase" in data
