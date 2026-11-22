from pathlib import Path

from app.api.v1.endpoints.integrations import get_integrations_overview
from app.services.outro_service import OutroConfig, outro_service


def test_integrations_overview_reports_configured_services_without_crashing(client, auth_headers):
    response = client.get("/api/v1/integrations/overview", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 7
    assert {item["id"] for item in data["integrations"]} == {
        "buffer", "youtube", "tiktok", "drive", "database", "redis", "llm"
    }
    llm = next(item for item in data["integrations"] if item["id"] == "llm")
    assert "key_configured" in llm["details"]
    assert llm["details"]["connected"] is False


def test_health_endpoint_is_mounted_under_api_v1(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] in {"healthy", "degraded"}


def test_outro_eligibility_fails_closed_without_explicit_campaign_permissions(tmp_path, monkeypatch):
    asset = tmp_path / "outro.mp4"
    asset.write_bytes(b"test asset")
    config = OutroConfig(enabled=True, outro_path=str(asset), promo_text="Join our WhatsApp channel", destination_url="https://example.com", duration_seconds=4)
    monkeypatch.setattr(outro_service, "get_config", lambda: config)
    monkeypatch.setattr(outro_service, "probe_video_duration", lambda _path: config.duration_seconds)

    eligible, reasons = outro_service.check_campaign_outro_eligibility({"raw_rules": "Create a short video"}, 20)
    assert eligible is False
    assert any("explicitly permit promotional" in reason for reason in reasons)
    assert any("explicitly permit external links" in reason for reason in reasons)
    assert any("explicitly permit WhatsApp" in reason for reason in reasons)


def test_outro_eligibility_requires_explicit_permissions_and_respects_duration(tmp_path, monkeypatch):
    asset = tmp_path / "outro.mp4"
    asset.write_bytes(b"test asset")
    config = OutroConfig(enabled=True, outro_path=str(asset), promo_text="Join our WhatsApp channel", destination_url="https://example.com", duration_seconds=4)
    monkeypatch.setattr(outro_service, "get_config", lambda: config)
    monkeypatch.setattr(outro_service, "probe_video_duration", lambda _path: config.duration_seconds)
    campaign = {
        "raw_rules": "Promotional outro permitted. External links permitted. WhatsApp promotion permitted.",
        "max_clip_duration_seconds": 25,
    }

    eligible, reasons = outro_service.check_campaign_outro_eligibility(campaign, 22)
    assert eligible is False
    assert any("exceeds maximum" in reason for reason in reasons)

    eligible, reasons = outro_service.check_campaign_outro_eligibility(campaign, 20)
    assert eligible is True
    assert reasons == []
