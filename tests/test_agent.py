"""Agent-loop tests using a scripted fake Gemini client (no API key needed)."""
import os
import sys
from datetime import date, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest
from google.genai import types

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import agent  # noqa: E402
import tools  # noqa: E402


class FakeClient:
    """Plays back a fixed script of model turns: ('call', name, args) or ('text', str)."""

    def __init__(self, script):
        self.script = list(script)
        self.models = SimpleNamespace(generate_content=self._generate)
        self.seen_results = []

    def _generate(self, model, contents, config):
        last = contents[-1]
        for p in last.parts or []:
            if p.function_response:
                self.seen_results.append(p.function_response.response["result"])
        kind, *rest = self.script.pop(0)
        if kind == "text":
            content = types.Content(role="model", parts=[types.Part(text=rest[0])])
            return SimpleNamespace(function_calls=[], candidates=[SimpleNamespace(content=content)], text=rest[0])
        name, args = rest
        content = types.Content(role="model", parts=[types.Part.from_function_call(name=name, args=args)])
        call = SimpleNamespace(name=name, args=args)
        return SimpleNamespace(function_calls=[call], candidates=[SimpleNamespace(content=content)], text=None)


@pytest.fixture(autouse=True)
def tracker(tmp_path, monkeypatch):
    today = date.today()
    df = pd.DataFrame(
        [
            ("T001", "Late task", "HR", "Priya Sharma", today - timedelta(days=9), "Pending", "High"),
            ("T002", "Fine task", "HR", "Priya Sharma", today + timedelta(days=3), "Pending", "Low"),
        ],
        columns=tools.COLUMNS,
    )
    path = tmp_path / "tasks.xlsx"
    df.to_excel(path, index=False)
    monkeypatch.setenv("TASKS_FILE", str(path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(agent, "LOG_FILE", str(tmp_path / "log.jsonl"))


def test_multi_step_loop_reads_results_then_finishes():
    client = FakeClient(
        [
            ("call", "get_overdue_tasks", {}),
            ("call", "draft_notification", {"owner": "Priya Sharma", "task_ids": ["T001"]}),
            ("text", "Done: 1 overdue task, reminder drafted."),
        ]
    )
    run = agent.run_agent("Find overdue tasks and draft reminders", client=client)
    assert run.status == "completed"
    assert "1 overdue" in run.final_answer
    assert client.seen_results[0]["overdue_count"] == 1  # result went back to the model
    kinds = [e["type"] for e in run.log]
    assert kinds.count("tool_call") == 2 and "final" in kinds


def test_consequential_tool_is_queued_not_executed():
    client = FakeClient(
        [
            ("call", "update_task_status", {"task_id": "T001", "new_status": "Blocked"}),
            ("text", "Proposed marking T001 as Blocked."),
        ]
    )
    run = agent.run_agent("Mark T001 blocked", client=client)
    assert run.status == "awaiting_approval"
    assert tools.load_tasks().set_index("task_id").loc["T001", "status"] == "Pending"  # untouched

    run.approve("A1")
    assert tools.load_tasks().set_index("task_id").loc["T001", "status"] == "Blocked"
    assert run.status == "completed"


def test_rejected_action_changes_nothing():
    client = FakeClient([("call", "update_task_status", {"task_id": "T001", "new_status": "Completed"}), ("text", "ok")])
    run = agent.run_agent("Complete T001", client=client)
    run.reject("A1")
    assert tools.load_tasks().set_index("task_id").loc["T001", "status"] == "Pending"
    assert run.pending[0]["status"] == "rejected"


def test_cannot_decide_twice():
    client = FakeClient([("call", "update_task_status", {"task_id": "T001", "new_status": "Blocked"}), ("text", "ok")])
    run = agent.run_agent("x", client=client)
    run.approve("A1")
    with pytest.raises(ValueError):
        run.approve("A1")


def test_tool_error_goes_back_to_model_and_agent_recovers():
    client = FakeClient(
        [
            ("call", "read_tasks", {"status": "banana"}),  # error
            ("call", "read_tasks", {"status": "Pending"}),  # recovery
            ("text", "Recovered."),
        ]
    )
    run = agent.run_agent("List pending", client=client)
    assert run.status == "completed"
    assert "error" in client.seen_results[0]
    assert any(e["type"] == "error" for e in run.log)


def test_unknown_tool_is_handled():
    client = FakeClient([("call", "launch_rocket", {}), ("text", "Sorry, no such tool.")])
    run = agent.run_agent("x", client=client)
    assert run.status == "completed"
    assert "Unknown tool" in client.seen_results[0]["error"]


def test_llm_failure_marks_run_failed():
    class Broken:
        models = SimpleNamespace(generate_content=lambda **kw: (_ for _ in ()).throw(RuntimeError("quota exceeded")))

    run = agent.run_agent("x", client=Broken())
    assert run.status == "failed" and "quota exceeded" in run.final_answer


def test_step_limit_prevents_infinite_loop(monkeypatch):
    monkeypatch.setattr(agent, "MAX_STEPS", 3)
    client = FakeClient([("call", "summarize_by_department", {})] * 10)
    run = agent.run_agent("loop forever", client=client)
    assert "maximum number of steps" in run.final_answer
