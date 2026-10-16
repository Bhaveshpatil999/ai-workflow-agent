"""The offline demo client must drive the real agent loop end to end."""
import os
import sys
from datetime import date, timedelta

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import agent  # noqa: E402
import tools  # noqa: E402
from demo_client import DemoClient  # noqa: E402


@pytest.fixture(autouse=True)
def tracker(tmp_path, monkeypatch):
    t = date.today()
    df = pd.DataFrame(
        [
            ("T001", "Late pending", "HR", "Priya Sharma", t - timedelta(days=9), "Pending", "High"),
            ("T002", "Late wip", "IT", "Vikram Rao", t - timedelta(days=2), "WIP", "Low"),
            ("T003", "No date", "IT", "Vikram Rao", None, "Pending", "Low"),
        ],
        columns=tools.COLUMNS,
    )
    path = tmp_path / "tasks.xlsx"
    df.to_excel(path, index=False)
    monkeypatch.setenv("TASKS_FILE", str(path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(agent, "LOG_FILE", str(tmp_path / "log.jsonl"))


def test_demo_run_produces_report_draft_and_pending_action(tmp_path):
    run = agent.run_agent("Identify overdue tasks and prepare a weekly operations report", client=DemoClient())
    assert run.status == "awaiting_approval"
    used = [e["tool"] for e in run.log if e["type"] == "tool_call"]
    assert used == ["get_overdue_tasks", "summarize_by_department", "analyze_bottlenecks", "draft_notification", "update_task_status", "save_report", "save_excel_report"]
    report = next(tmp_path.glob("reports/*.md")).read_text()
    assert "T001" in report and "no valid deadline" in report and "## Bottlenecks" in report
    assert next(tmp_path.glob("reports/*.xlsx")).exists()
    assert tools.load_tasks().set_index("task_id").loc["T001", "status"] == "Pending"  # not changed yet


def test_demo_mode_env_switch(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("DEMO_MODE", "1")
    assert isinstance(agent._make_client(), DemoClient)
    monkeypatch.delenv("DEMO_MODE")
    with pytest.raises(RuntimeError):
        agent._make_client()
