"""The agent: an LLM decision loop over tools, with an approval queue and a log.

Flow: goal -> model picks a tool -> we run it (or queue it for approval) ->
result goes back to the model -> repeat until the model answers in plain text.

Run from the terminal:   python agent.py "Identify overdue tasks and prepare a weekly operations report"
"""
import json
import os
import sys
import uuid
from datetime import date, datetime

from dotenv import load_dotenv

import tools

load_dotenv()

MAX_STEPS = 12
LOG_FILE = "logs/agent_log.jsonl"
RUNS_DIR = "runs"

SYSTEM_PROMPT = """You are a Business Workflow Automation Agent for a company's task tracker.
Today's date is {today}.

How to work:
- Always get facts from tools. Never invent task ids, owners, dates or counts.
- Pick the tools you need and call them in whatever order makes sense. Inspect each result before deciding the next step.
- If a tool returns an error or the data looks incomplete (missing deadlines, unknown statuses), say so, adapt, and mention it in your answer.
- update_task_status changes real data, so it is only QUEUED for human approval. Do not claim a change was made; say it has been proposed.
- draft_notification only drafts. To actually deliver a message, call send_notification with the drafted text; it is QUEUED for human approval, so never claim it was sent.
- For questions about priorities, bottlenecks or what is slowing work down, call analyze_bottlenecks and base your explanation on its numbers.
- When asked for a report, write it in clear Markdown and call save_report. For a weekly operations report also call save_excel_report with a short commentary; its figures come from the tracker, so do not retype numbers into it. Then give the user a short final summary.
- Stop calling tools once the goal is complete and answer in plain text."""


class AgentRun:
    def __init__(self, goal: str):
        self.id = uuid.uuid4().hex[:8]
        self.goal = goal
        self.status = "running"  # running | awaiting_approval | completed | failed
        self.final_answer = ""
        self.log: list[dict] = []
        self.pending: list[dict] = []

    def record(self, kind: str, **data):
        entry = {"ts": datetime.now().isoformat(timespec="seconds"), "run_id": self.id, "type": kind, **data}
        self.log.append(entry)
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "goal": self.goal,
            "status": self.status,
            "final_answer": self.final_answer,
            "pending": self.pending,
            "log": self.log,
        }

    def save(self):
        """Persist run state so pending approvals survive a server restart."""
        os.makedirs(RUNS_DIR, exist_ok=True)
        with open(os.path.join(RUNS_DIR, f"{self.id}.json"), "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, default=str, indent=2)

    @classmethod
    def load(cls, run_id: str):
        """Load a saved run, or return None if it does not exist."""
        safe = "".join(c for c in run_id if c.isalnum())
        path = os.path.join(RUNS_DIR, f"{safe}.json")
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        run = cls(data["goal"])
        run.id, run.status = data["id"], data["status"]
        run.final_answer, run.log, run.pending = data["final_answer"], data["log"], data["pending"]
        return run

    def _refresh_status(self):
        if self.status == "awaiting_approval" and not any(a["status"] == "pending" for a in self.pending):
            self.status = "completed"
        self.save()

    def approve(self, action_id: str) -> dict:
        action = self._find(action_id)
        result = tools.TOOLS[action["tool"]]["fn"](**action["args"])
        if "error" in result and result.get("retryable"):
            # e.g. the Excel file is open: leave the action pending so the user can retry
            action["last_error"] = result["error"]
            self.record("error", action_id=action_id, tool=action["tool"], error=result["error"], retryable=True)
            self._refresh_status()
            return action
        action.pop("last_error", None)
        if "error" in result:
            action["status"] = "failed"
            self.record("error", action_id=action_id, tool=action["tool"], error=result["error"])
        else:
            action["status"] = "approved"
            self.record("approval", action_id=action_id, decision="approved", tool=action["tool"], result=result)
        action["result"] = result
        self._refresh_status()
        return action

    def reject(self, action_id: str) -> dict:
        action = self._find(action_id)
        action["status"] = "rejected"
        self.record("approval", action_id=action_id, decision="rejected", tool=action["tool"])
        self._refresh_status()
        return action

    def _find(self, action_id: str) -> dict:
        for a in self.pending:
            if a["id"] == action_id:
                if a["status"] != "pending":
                    raise ValueError(f"Action {action_id} is already {a['status']}")
                return a
        raise KeyError(f"No such action: {action_id}")


def _make_client():
    from google import genai

    key = os.getenv("GEMINI_API_KEY")
    if not key or key == "your-key-here":
        if os.getenv("DEMO_MODE") == "1":
            from demo_client import DemoClient

            return DemoClient()
        raise RuntimeError("GEMINI_API_KEY is not set. Copy .env.example to .env and add your key.")
    return genai.Client(api_key=key)


def run_agent(goal: str, client=None) -> AgentRun:
    from google.genai import types

    run = AgentRun(goal)
    run.record("goal", goal=goal)
    try:
        client = client or _make_client()
        model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        declarations = [
            types.FunctionDeclaration(name=n, description=t["description"], parameters=t["parameters"])
            for n, t in tools.TOOLS.items()
        ]
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT.format(today=date.today().isoformat()),
            tools=[types.Tool(function_declarations=declarations)],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        contents = [types.Content(role="user", parts=[types.Part(text=goal)])]

        for step in range(1, MAX_STEPS + 1):
            response = client.models.generate_content(model=model, contents=contents, config=config)
            calls = response.function_calls or []
            if not calls:
                run.final_answer = response.text or ""
                run.record("final", step=step, answer=run.final_answer)
                break

            contents.append(response.candidates[0].content)
            result_parts = []
            for call in calls:
                args = dict(call.args or {})
                run.record("tool_call", step=step, tool=call.name, args=args)
                spec = tools.TOOLS.get(call.name)

                if spec is None:
                    result = {"error": f"Unknown tool '{call.name}'. Available: {list(tools.TOOLS)}"}
                    run.record("error", step=step, tool=call.name, error=result["error"])
                elif spec["requires_approval"]:
                    action = {
                        "id": f"A{len(run.pending) + 1}",
                        "tool": call.name,
                        "args": args,
                        "status": "pending",
                        "description": f"{call.name}({', '.join(f'{k}={v!r}' for k, v in args.items())})",
                    }
                    run.pending.append(action)
                    result = {
                        "status": "queued_for_approval",
                        "action_id": action["id"],
                        "note": "Not executed. A human must approve it first.",
                    }
                    run.record("queued", step=step, action_id=action["id"], tool=call.name, args=args)
                else:
                    try:
                        result = spec["fn"](**args)
                    except Exception as e:  # tool bug or bad arguments
                        result = {"error": f"{type(e).__name__}: {e}"}
                    if "error" in result:
                        run.record("error", step=step, tool=call.name, error=result["error"])

                run.record("tool_result", step=step, tool=call.name, result=_shorten(result))
                result_parts.append(types.Part.from_function_response(name=call.name, response={"result": result}))

            contents.append(types.Content(role="user", parts=result_parts))
        else:
            run.final_answer = "Stopped: reached the maximum number of steps without finishing."
            run.record("error", error=run.final_answer)

        run.status = "awaiting_approval" if any(a["status"] == "pending" for a in run.pending) else "completed"
    except Exception as e:
        run.status = "failed"
        run.final_answer = f"Agent failed: {type(e).__name__}: {e}"
        run.record("error", error=run.final_answer)
    run.save()
    return run


def _shorten(result, limit=1500):
    text = json.dumps(result, default=str)
    return result if len(text) <= limit else {"truncated": text[:limit] + "..."}


def main():
    goal = " ".join(sys.argv[1:]) or "Identify overdue tasks and prepare a weekly operations report."
    run = run_agent(goal)
    print(f"\n=== Run {run.id}: {run.status} ===\n{run.final_answer}\n")
    for action in run.pending:
        answer = input(f"Approve {action['description']}? [y/N] ").strip().lower()
        if answer == "y":
            print("  ->", run.approve(action["id"]))
        else:
            run.reject(action["id"])
            print("  -> rejected")
    print(f"Log written to {LOG_FILE}")


if __name__ == "__main__":
    main()
