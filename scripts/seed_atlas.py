import os

import httpx

base = os.getenv("ATLAS_TEST_CONTROL_URL", "http://localhost:8080/__test")
token = os.environ["ATLAS_TEST_CONTROL_TOKEN"]
headers = {"x-test-control-token": token}
with httpx.Client(timeout=10, headers=headers) as client:
    client.post(f"{base}/reset").raise_for_status()
    client.post(f"{base}/seed", params={"seed": 41021}).raise_for_status()
print("Atlas reset and seeded with 41021")
