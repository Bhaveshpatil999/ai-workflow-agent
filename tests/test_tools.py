"""Tests for the tools: normal cases and messy / unexpected input."""
import os
import sys
from datetime import date, timedelta

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import tools  # noqa: E402

TODAY = date.today()


@pytest.fixture
def tracker(tmp_path, monkeypatch):
    rows = [
        # id, task, dept, owner, deadline, status, priority
        ("T001", "Late open task", "HR", "Priya Sharma", TODAY - timedelta(days=10), "Pending", "High"),
        ("T002", "Late but done", "HR", "Priya Sharma", TODAY - timedelta(days=5), "done", "Low"),
        ("T003", "Future task", "Sales", "Ankit Desai", TODAY + timedelta(days=5), "WIP", "medium"),
        ("T004", "No deadline", "Sales", "Ankit Desai", None, "Pending", "High"),
        ("T005", "Weird status", "IT", "Vikram Rao", TODAY - timedelta(days=2), "waiting on vendor", "URGENT"),
        ("T006", "Bad date", "IT", "Vikram Rao", "not a date", "Blocked", "Low"),
    ]
    df = pd.DataFrame(rows, columns=tools.COLUMNS)
    path = tmp_path / "tasks.xlsx"
    df.to_excel(path, index=False)
    monkeypatch.setenv("TASKS_FILE", str(path))
    monkeypatch.chdir(tmp_path)  # backups/reports go to a temp dir
    return path


def test_status_aliases_are_normalised(tracker):
    df = tools.load_tasks()
    assert df.set_index("task_id").loc["T002", "status"] == "Completed"
    assert df.set_index("task_id").loc["T003", "status"] == "In Progress"
    assert df.set_index("task_id").loc["T005", "status"] == "Unknown"


def test_overdue_excludes_completed_and_future(tracker):
    result = tools.get_overdue_tasks()
    ids = {t["task_id"] for t in result["overdue_tasks"]}
    assert "T001" in ids and "T005" in ids
    assert "T002" not in ids  # completed
    assert "T003" not in ids  # not due yet


def test_missing_and_invalid_deadlines_are_reported_not_crashed(tracker):
    result = tools.get_overdue_tasks()
    missing = {t["task_id"] for t in result["tasks_with_missing_deadline"]}
    assert missing == {"T004", "T006"}
    assert result["tasks_with_unrecognised_status"] == 1


def test_overdue_bad_date_argument(tracker):
    assert "error" in tools.get_overdue_tasks(as_of="13/10/2026")


def test_read_tasks_filters(tracker):
    assert tools.read_tasks(department="hr")["count"] == 2
    assert tools.read_tasks(owner="Ankit Desai")["count"] == 2
    assert "error" in tools.read_tasks(status="banana")


def test_summary_counts(tracker):
    s = tools.summarize_by_department()["departments"]
    assert s["HR"]["overdue"] == 1 and s["HR"]["completed"] == 1


def test_update_status_changes_file_and_creates_backup(tracker):
    result = tools.update_task_status("T001", "in progress")
    assert result["new_status"] == "In Progress"
    assert os.path.exists(result["backup"])
    assert tools.load_tasks().set_index("task_id").loc["T001", "status"] == "In Progress"


def test_update_status_rejects_bad_input(tracker):
    assert "error" in tools.update_task_status("T001", "finished-ish")
    assert "error" in tools.update_task_status("T999", "Completed")


def test_draft_notification_does_not_send(tracker):
    result = tools.draft_notification("Priya Sharma", ["T001"])
    assert result["sent"] is False and "T001" in result["body"]
    assert "error" in tools.draft_notification("Priya Sharma", ["T003"])  # not her task


def test_missing_file_gives_error_not_exception(tmp_path, monkeypatch):
    monkeypatch.setenv("TASKS_FILE", str(tmp_path / "nope.xlsx"))
    assert "error" in tools.get_overdue_tasks()


def test_missing_columns_detected(tmp_path, monkeypatch):
    path = tmp_path / "bad.xlsx"
    pd.DataFrame({"task": ["x"], "owner": ["y"]}).to_excel(path, index=False)
    monkeypatch.setenv("TASKS_FILE", str(path))
    assert "missing required columns" in tools.read_tasks()["error"]


def test_save_report(tracker):
    assert "error" in tools.save_report("Empty", "  ")
    out = tools.save_report("Weekly Ops Report", "All good")
    assert os.path.exists(out["saved_to"])
