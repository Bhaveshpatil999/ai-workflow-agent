"""Tests for send_notification, run persistence and the API restart path."""
import os
import sys
from datetime import date, timedelta

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))
import agent  # noqa: E402
import tools  # noqa: E402
from test_agent import FakeClient  # noqa: E402


@pytest.fixture(autouse=True)
def tracker(tmp_path, monkeypatch):
    today = date.today()
    df = pd.DataFrame(
        [("T001", "Late task", "HR", "Priya Sharma", today - timedelta(days=9), "Pending", "High")],
        columns=tools.COLUMNS,
    )
    path = tmp_path / "tasks.xlsx"
    df.to_excel(path, index=False)
    monkeypatch.setenv("TASKS_FILE", str(path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(agent, "LOG_FILE", str(tmp_path / "log.jsonl"))


def test_send_notification_writes_outbox():
    result = tools.send_notification("Priya Sharma", "Reminder", "Please update T001")
    assert os.path.exists(result["outbox_file"])
    assert os.path.exists("outbox/sent_log.jsonl")


def test_send_notification_validates_input():
    assert "error" in tools.send_notification("Nobody Here", "s", "b")
    assert "error" in tools.send_notification("Priya Sharma", "", "b")


def test_agent_queues_send_notification_until_approved():
    client = FakeClient(
        [
            ("call", "draft_notification", {"owner": "Priya Sharma", "task_ids": ["T001"]}),
            ("call", "send_notification", {"owner": "Priya Sharma", "subject": "Reminder", "body": "Please update T001"}),
            ("text", "Drafted and proposed sending a reminder to Priya."),
        ]
    )
    run = agent.run_agent("Remind owners of overdue tasks", client=client)
    assert run.status == "awaiting_approval"
    assert not os.path.exists("outbox")  # nothing delivered yet
    run.approve("A1")
    assert os.path.exists("outbox/sent_log.jsonl")


def test_run_survives_restart_and_can_be_approved():
    client = FakeClient([("call", "update_task_status", {"task_id": "T001", "new_status": "Blocked"}), ("text", "ok")])
    run = agent.run_agent("Block T001", client=client)
    reloaded = agent.AgentRun.load(run.id)  # simulates a fresh process
    assert reloaded.status == "awaiting_approval" and reloaded.pending[0]["status"] == "pending"
    reloaded.approve("A1")
    assert tools.load_tasks().set_index("task_id").loc["T001", "status"] == "Blocked"
    assert agent.AgentRun.load(run.id).status == "completed"


def test_load_unknown_or_malicious_run_id():
    assert agent.AgentRun.load("does-not-exist") is None
    assert agent.AgentRun.load("../../etc/passwd") is None


def test_api_serves_run_after_memory_cleared():
    from fastapi.testclient import TestClient

    import app as app_module

    client = FakeClient([("call", "update_task_status", {"task_id": "T001", "new_status": "Blocked"}), ("text", "ok")])
    run = agent.run_agent("Block T001", client=client)
    app_module.RUNS.clear()  # simulates a server restart
    api = TestClient(app_module.app)
    assert api.get(f"/api/runs/{run.id}").json()["status"] == "awaiting_approval"
    r = api.post(f"/api/runs/{run.id}/actions/A1/approve")
    assert r.status_code == 200 and r.json()["run"]["status"] == "completed"
    assert api.post(f"/api/runs/{run.id}/actions/A1/approve").status_code == 409
    assert api.get("/api/runs/nope").status_code == 404
