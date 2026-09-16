"""Host HTTP smoke test: python verify_container.py (start Docker first).

Creates disposable identities; credentials stay in memory and are never printed.
Uses only the standard library and never imports the application or storage.
"""

import argparse
import json
from pathlib import Path
from urllib.request import Request, urlopen
from uuid import uuid4


def verify(base="http://127.0.0.1:8000"):
    base = base.rstrip("/")

    def request(method, path, expected=200, token=None, body=None, headers=None):
        headers = dict(headers or {})
        if token:
            headers["Authorization"] = f"Bearer {token}"
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        with urlopen(Request(base + path, data=data, headers=headers, method=method), timeout=10) as response:
            assert response.status == expected, (method, path, response.status)
            content = response.read().decode()
            if path == "/":
                assert response.headers.get_content_type() == "text/html"
                return content
            return json.loads(content)

    dashboard = request("GET", "/")
    assert dashboard == Path(__file__).with_name("dashboard.html").read_text(encoding="utf-8")
    print("GET /: 200; dashboard matches source, including inline CSS and JavaScript")
    assert request("GET", "/health") == {"status": "ok"}
    print('GET /health: 200 {"status": "ok"}')
    assert request("GET", "/ready") == {"status": "ready"}
    print('GET /ready: 200 {"status": "ready"}')

    suffix = uuid4().hex[:8]
    sender = request("POST", "/api/v1/agents", 201, body={"name": f"q3-alice-{suffix}"})
    recipient = request("POST", "/api/v1/agents", 201, body={"name": f"q3-uppercase-{suffix}"})
    assert sender["agent_id"] != recipient["agent_id"]
    sent = request("POST", "/api/v1/tasks", 201, sender["token"],
                   {"to": recipient["agent_id"], "input": "hello agent relay"},
                   {"Idempotency-Key": f"q3-{suffix}"})
    assert sent["status"] == "queued"
    task_id = sent["task_id"]
    claim = request("POST", "/api/v1/tasks/claim", token=recipient["token"],
                    body={"worker_id": "q3-host-uppercase", "wait_seconds": 0})
    assert claim["task_id"] == task_id
    assert claim["from"] == sender["agent_id"]
    assert claim["input"] == "hello agent relay"
    assert claim["claim_token"]
    completed = request("POST", f"/api/v1/tasks/{task_id}/complete", token=recipient["token"],
                        body={"claim_token": claim["claim_token"], "output": claim["input"].upper()})
    assert completed == {"task_id": task_id, "status": "completed"}
    task = request("GET", f"/api/v1/tasks/{task_id}", token=sender["token"])
    assert task["status"] == "completed"
    assert task["output"] == "HELLO AGENT RELAY"
    assert task["from"] == sender["agent_id"] and task["to"] == recipient["agent_id"]

    # Exercise all endpoints used by the dashboard's Refresh button.
    agents = request("GET", "/api/v1/agents?limit=100", token=sender["token"])
    assert any(a["agent_id"] == sender["agent_id"] for a in agents["items"])
    sent_tasks = request("GET", "/api/v1/tasks?direction=sent&limit=100", token=sender["token"])
    assert any(t["task_id"] == task_id and t["status"] == "completed" for t in sent_tasks["items"])
    received = request("GET", "/api/v1/tasks?direction=received&limit=100", token=sender["token"])
    assert received["items"] == []
    attempts = request("GET", f"/api/v1/tasks/{task_id}/attempts", token=sender["token"])
    assert attempts["items"][0]["outcome"] == "completed"
    print("Dashboard API requests: PASS")
    print(f"Container API acceptance scenario 1: PASS; task_id={task_id}")
    print('Sender observed: status="completed", output="HELLO AGENT RELAY"')


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    verify(parser.parse_args().base_url)
