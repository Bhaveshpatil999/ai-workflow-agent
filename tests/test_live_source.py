"""Live Excel linking: choose any file, notice saves, survive a file locked by Excel."""
import os
import sys
import time
from datetime import date, timedelta

import pandas as pd
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import agent  # noqa: E402
import app as app_module  # noqa: E402
import tools  # noqa: E402

T = date.today()


def make_file(path, rows=None):
    rows = rows or [
        ("T001", "Late task", "HR", "Priya Sharma", T - timedelta(days=5), "Pending", "High"),
        ("T002", "Future task", "IT", "Vikram Rao", T + timedelta(days=5), "Pending", "Low"),
    ]
    df = pd.DataFrame(rows, columns=tools.COLUMNS)
    if str(path).endswith(".csv"):
        df.to_csv(path, index=False)
    else:
        df.to_excel(path, index=False)
    return str(path)


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    os.makedirs("data")
    monkeypatch.setenv("TASKS_FILE", make_file(tmp_path / "data" / "tasks.xlsx"))
    monkeypatch.setattr(agent, "LOG_FILE", str(tmp_path / "log.jsonl"))
    return tmp_path


def test_choose_another_excel_file_then_reset(env):
    other = make_file(env / "mine.xlsx", [("X1", "Only task", "Ops", "Meera Joshi", T - timedelta(days=1), "WIP", "High")])
    result = tools.set_tasks_path(f'"{other}"')  # Windows "Copy as path" adds quotes
    assert result["rows"] == 1 and tools.tasks_path() == os.path.abspath(other)
    assert tools.read_tasks()["tasks"][0]["task_id"] == "X1"
    tools.reset_tasks_path()
    assert tools.tasks_path().endswith("tasks.xlsx") and tools.read_tasks()["count"] == 2


def test_bad_sources_are_rejected_and_do_not_replace_the_active_file(env):
    before = tools.tasks_path()
    bad_cols = env / "bad.xlsx"
    pd.DataFrame({"task": ["x"]}).to_excel(bad_cols, index=False)
    for candidate in ("notes.txt", str(env / "missing.xlsx"), str(bad_cols), ""):
        assert "error" in tools.set_tasks_path(candidate), candidate
    assert tools.tasks_path() == before


def test_file_status_changes_when_excel_is_saved(env):
    first = tools.file_status()
    assert first["exists"]
    time.sleep(0.05)
    make_file(first["path"], [("T009", "Edited in Excel", "HR", "Priya Sharma", T, "Pending", "Low")])
    os.utime(first["path"], (first["modified"] + 5, first["modified"] + 5))
    second = tools.file_status()
    assert second["modified"] != first["modified"]
    assert tools.read_tasks()["tasks"][0]["task_id"] == "T009"  # agent sees the saved edit immediately


def test_missing_file_status():
    os.environ["TASKS_FILE"] = "nope.xlsx"
    assert tools.file_status()["exists"] is False


def test_csv_source_is_written_back_as_csv(env):
    csv_path = make_file(env / "tracker.csv")
    tools.set_tasks_path(csv_path)
    result = tools.update_task_status("T001", "Blocked")
    assert result["backup"].endswith(".csv")
    raw = open(csv_path, encoding="utf-8").read()
    assert raw.startswith("task_id,") and "Blocked" in raw  # real CSV text, not an xlsx blob


def test_locked_read_gives_friendly_error(monkeypatch):
    def locked(*a, **k):
        raise PermissionError("locked")

    monkeypatch.setattr(pd, "read_excel", locked)
    assert "locked" in tools.read_tasks()["error"]


def test_locked_write_is_retryable_and_leaves_file_untouched(monkeypatch):
    real_replace = os.replace
    monkeypatch.setattr(os, "replace", lambda *a, **k: (_ for _ in ()).throw(PermissionError("in use")))
    result = tools.update_task_status("T001", "Completed")
    assert result["retryable"] and "open in Excel" in result["error"]
    assert not any(".tmp" in f for f in os.listdir("data"))  # temp file cleaned up
    assert tools.load_tasks().set_index("task_id").loc["T001", "status"] == "Pending"

    # same approval, retried through the agent, once Excel is closed
    run = agent.AgentRun("x")
    run.pending.append({"id": "A1", "tool": "update_task_status", "status": "pending", "description": "d",
                        "args": {"task_id": "T001", "new_status": "Completed"}})
    run.status = "awaiting_approval"
    action = run.approve("A1")
    assert action["status"] == "pending" and "open in Excel" in action["last_error"]
    monkeypatch.setattr(os, "replace", real_replace)
    action = run.approve("A1")
    assert action["status"] == "approved" and "last_error" not in action
    assert tools.load_tasks().set_index("task_id").loc["T001", "status"] == "Completed"


def test_source_api_roundtrip(env):
    c = TestClient(app_module.app)
    assert c.get("/api/source").json()["exists"] is True
    other = make_file(env / "other.xlsx")
    assert c.post("/api/source", json={"path": other}).json()["rows"] == 2
    assert c.get("/api/source").json()["path"] == os.path.abspath(other)
    assert c.post("/api/source", json={"path": "nope.txt"}).status_code == 400
    assert c.delete("/api/source").json()["path"].endswith("tasks.xlsx")
