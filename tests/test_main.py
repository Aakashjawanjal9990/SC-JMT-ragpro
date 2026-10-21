from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_home_endpoint():
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {
        "message": "CI/CD demo application is running"
    }


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}
