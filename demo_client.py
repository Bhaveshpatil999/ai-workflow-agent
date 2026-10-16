"""Offline stand-in for the Gemini client, used when no API key is set (DEMO_MODE=1).

It is NOT an LLM: it follows a fixed script, but it reads each tool result that
the agent loop sends back, so the log, approval queue and report look like a real
run. Use it to try the dashboard and the approval flow without any API access.
The real decision-making happens when GEMINI_API_KEY is set.
"""
from types import SimpleNamespace

from google.genai import types


def _results(contents) -> dict:
    out = {}
    for c in contents:
        for p in c.parts or []:
            fr = getattr(p, "function_response", None)
            if fr:
                out[fr.name] = fr.response["result"]
    return out


def _call(name, args):
    content = types.Content(role="model", parts=[types.Part.from_function_call(name=name, args=args)])
    return SimpleNamespace(
        function_calls=[SimpleNamespace(name=name, args=args)],
        candidates=[SimpleNamespace(content=content)],
        text=None,
    )


def _text(msg):
    content = types.Content(role="model", parts=[types.Part(text=msg)])
    return SimpleNamespace(function_calls=[], candidates=[SimpleNamespace(content=content)], text=msg)


def _build_report(res) -> str:
    overdue = res["get_overdue_tasks"]
    summary = res["summarize_by_department"]["departments"]
    lines = [f"Report date: {overdue['as_of']}", "", "## Department summary", "",
             "| Department | Total | Done | In progress | Pending | Blocked | Overdue |", "|---|---|---|---|---|---|---|"]
    for d, s in summary.items():
        lines.append(f"| {d} | {s['total']} | {s['completed']} | {s['in_progress']} | {s['pending']} | {s['blocked']} | {s['overdue']} |")
    lines += ["", f"## Overdue tasks ({overdue['overdue_count']})", "",
              "| ID | Task | Owner | Days overdue | Status | Priority |", "|---|---|---|---|---|---|"]
    for t in overdue["overdue_tasks"]:
        lines.append(f"| {t['task_id']} | {t['task']} | {t['owner']} | {t['days_overdue']} | {t['status']} | {t['priority']} |")
    b = res["analyze_bottlenecks"]
    lines += ["", "## Bottlenecks", "",
              f"- Most overloaded owner: {b['highlights']['most_overloaded_owner'] or 'none'}",
              f"- Slowest department (average days overdue): {b['highlights']['slowest_department'] or 'none'}",
              "- Overdue by priority: " + ", ".join(f"{k} {v}" for k, v in b["overdue_by_priority"].items())]
    if b["blocked_tasks"]:
        lines += ["", "Blocked tasks:"] + [f"- {t['task_id']} {t['task']} ({t['owner']}, {t['priority']})" for t in b["blocked_tasks"]]
    missing = overdue["tasks_with_missing_deadline"]
    if missing:
        lines += ["", "## Data quality", ""]
        lines += [f"- {t['task_id']} ({t['task']}) has no valid deadline and could not be checked." for t in missing]
    if overdue["tasks_with_unrecognised_status"]:
        lines.append(f"- {overdue['tasks_with_unrecognised_status']} task(s) have an unrecognised status.")
    return "\n".join(lines)


class DemoClient:
    def __init__(self):
        self.models = SimpleNamespace(generate_content=self._generate)

    def _generate(self, model, contents, config):
        res = _results(contents)
        if "get_overdue_tasks" not in res:
            return _call("get_overdue_tasks", {})
        if "summarize_by_department" not in res:
            return _call("summarize_by_department", {})
        if "analyze_bottlenecks" not in res:
            return _call("analyze_bottlenecks", {})
        overdue = res["get_overdue_tasks"]
        if "draft_notification" not in res and overdue["overdue_tasks"]:
            worst = overdue["overdue_tasks"][0]
            ids = [t["task_id"] for t in overdue["overdue_tasks"] if t["owner"] == worst["owner"]]
            return _call("draft_notification", {"owner": worst["owner"], "task_ids": ids, "tone": "urgent"})
        pending_old = [t for t in overdue["overdue_tasks"] if t["status"] == "Pending"]
        if "update_task_status" not in res and pending_old:
            return _call("update_task_status", {"task_id": pending_old[0]["task_id"], "new_status": "Blocked"})
        if "save_report" not in res:
            return _call("save_report", {"title": "Weekly Operations Report", "markdown": _build_report(res)})
        if "save_excel_report" not in res:
            hl = res["analyze_bottlenecks"]["highlights"]
            note = (f"{overdue['overdue_count']} open tasks are overdue. Most overloaded owner: {hl['most_overloaded_owner'] or 'none'}. "
                    f"Slowest department: {hl['slowest_department'] or 'none'}. (Scripted demo commentary.)")
            return _call("save_excel_report", {"title": "Weekly Operations Report", "narrative": note})
        n = overdue["overdue_count"]
        return _text(
            f"[DEMO MODE - scripted, not a real LLM] Found {n} overdue open task(s). "
            "Weekly report saved to the reports folder as Markdown and Excel. A reminder was drafted for the owner with the most "
            "overdue work, and one status change was proposed and is waiting for your approval."
        )
