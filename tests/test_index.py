from fastapi.testclient import TestClient
from main import app


def test_portada_y_oportunidades_cargan():
    client = TestClient(app)
    assert client.get("/").status_code == 200
    assert client.get("/oportunidades").status_code == 200
