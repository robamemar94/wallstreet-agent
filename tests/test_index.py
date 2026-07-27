from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

response_index = client.get("/")
assert response_index.status_code == 200
with open("test_out.html", "wb") as f:
    f.write(response_index.content)

response_opp = client.get("/oportunidades")
assert response_opp.status_code == 200
with open("test_opp_out.html", "wb") as f:
    f.write(response_opp.content)
