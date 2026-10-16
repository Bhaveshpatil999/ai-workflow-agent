# AI Business Workflow Automation Agent

An LLM agent that reads a business task tracker (Excel/CSV), decides which tools to use,
inspects each result, and keeps going until the goal is done. Anything that changes data
waits for human approval, and every step is logged.

Example goal: *"Identify overdue tasks and prepare a weekly operations report."*

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env              # add your GEMINI_API_KEY
python scripts/make_sample_data.py
python agent.py "Identify overdue tasks and prepare a weekly operations report"   # terminal mode
uvicorn app:app --reload                                                          # dashboard at http://127.0.0.1:8000
pytest                                                                            # no API key needed
```

## Project layout

| File | Purpose |
|---|---|
| `scripts/make_sample_data.py` | Phase 1: builds `data/tasks.xlsx` (with deliberately messy rows) |
| `tools.py` | Phase 3: the tools plus a registry the LLM chooses from |
| `agent.py` | Phases 2, 4, 5: Gemini decision loop, approval queue, JSONL logging |
| `app.py` + `static/index.html` | Phase 6: FastAPI endpoints and dashboard |
| `tests/` | Phase 6: tool tests and scripted agent-loop tests |

## How it works

1. The goal and the tool definitions go to the model.
2. The model replies with a tool call (or a final answer).
3. We run the tool and send the result back, so the model can pick the next step.
4. Tools marked `requires_approval` (currently `update_task_status`) are queued, not run.
   The model is told the action is pending; a human approves or rejects it later.
5. Every goal, tool call, result, error, queued action and approval decision is appended to `logs/agent_log.jsonl`.

### Tools

| Tool | Approval needed | What it does |
|---|---|---|
| `read_tasks` | no | Filtered view of the tracker |
| `get_overdue_tasks` | no | Open tasks past deadline, days overdue, rows with missing deadlines |
| `summarize_by_department` | no | Status and overdue counts per department |
| `draft_notification` | no | Drafts a reminder (never sends) |
| `save_report` | no | Writes the Markdown report to `reports/` |
| `update_task_status` | **yes** | Changes a status (a timestamped backup is made first) |
| `send_notification` | **yes** | Delivers a message (simulated: written to `outbox/`) |

See `docs/PROJECT_REPORT.md` for the architecture diagram, safety design, test results and interview talking points.

### Why this is an agent and not a script

- The model decides which tools to call and in what order; there is no fixed pipeline.
- Tool results (including errors) return to the model, which adapts. See `test_tool_error_goes_back_to_model_and_agent_recovers`.
- Guardrails are fixed in code, not left to the model: approval gate, step limit, unknown-tool handling.

## Test cases covered

| Case | Expected behaviour |
|---|---|
| Status written as `done`, `WIP`, `on hold` | Normalised to standard statuses |
| Unrecognised status ("waiting on vendor") | Marked `Unknown`, reported, no crash |
| Missing or invalid deadline | Excluded from overdue math, listed separately |
| Completed task past its deadline | Not counted as overdue |
| Invalid filter / bad date argument | Tool returns an error the model can react to |
| Missing file / missing columns | Clear error message |
| Model requests an unknown tool | Error returned to the model, which recovers |
| Model asks to update a task | Queued; file untouched until approved |
| Action rejected / approved twice | Nothing changes / second decision blocked |
| LLM API failure | Run marked `failed` with the reason |
| Model never stops calling tools | Step limit ends the run |

## Known limitations / next steps

- Runs are saved to `runs/*.json`, so pending approvals survive a server restart.
- `send_notification` is simulated (writes to `outbox/`). Replace its body with SMTP/Slack/Teams for real delivery; the approval gate stays.
- No authentication on the API; add it before any real deployment.
- Google Sheets support (via the Sheets API) would be a natural extension of `load_tasks` and `update_task_status`.

## Demo mode (no API key)

To see the whole flow without Gemini access, run in demo mode with no key.

**Windows PowerShell:**
```powershell
$env:DEMO_MODE = "1"
uvicorn app:app --reload        # or: python -m uvicorn app:app --reload
```

**macOS / Linux:**

```bash
DEMO_MODE=1 python agent.py "Identify overdue tasks and prepare a weekly operations report"
DEMO_MODE=1 uvicorn app:app --reload
```

`demo_client.py` replaces the LLM with a fixed script. It is **not** a real agent: it always calls the same tools in the same order, though it does read each tool result to build the report, draft and approval request. Use it for UI demos and screenshots; set `GEMINI_API_KEY` for genuine model-driven decisions.

## Using your own Excel file, live

The dashboard and the agent can work from any Excel (`.xlsx`) or `.csv` tracker on your computer.

1. Open the dashboard and, under **Data source**, paste the full path of your file (in Windows Explorer: right-click the file, **Copy as path**) and press **Use this file**. The choice is remembered in `data/source.json`; **Reset to default** goes back to `data/tasks.xlsx`.
2. Your file needs these columns: `task_id, task, department, owner, deadline, status, priority`.
3. **Edit and save in Excel (Ctrl+S).** The page checks the file every 3 seconds and refreshes itself when it changes. The agent re-reads the file on every tool call, so it always works from the latest saved version.

Things to know:

- Updates appear when you **save**, not as you type. Excel does not write unsaved edits to disk.
- If the agent's approved change cannot be written because the file is open in Excel, the approval stays pending with the reason shown. Save and close the file, then press **Approve** again. A timestamped backup is made before every write.
- If you keep the file open in Excel while the agent changes it, Excel's open copy is stale. Close and reopen it, or you may overwrite the agent's change when you save.
- Keep the server on its default `127.0.0.1` address. The data-source setting accepts paths on the machine running the server and has no login, so do not expose it to a network.
- For true multi-user live editing (several people, no saving step), the next step is Google Sheets through its API instead of a local file.

## Bottleneck analysis and Excel report

Two tools were added so the agent can answer "what is slowing us down?" and hand back a spreadsheet.

| Tool | Approval needed | What it does |
|---|---|---|
| `analyze_bottlenecks` | no | Open workload per owner, blocked tasks (high priority first), overdue counts by priority, average and maximum days overdue per department, and a ranked "needs attention first" list. Ties for "most overloaded owner" break on high-priority work. |
| `save_excel_report` | no | Writes `reports/<title>_<date>.xlsx` with sheets Summary, Departments, Overdue, Owner workload, Blocked, Delay by department. Header row, filters and frozen panes are set. |

How they stay honest: both are plain pandas calculations. In `save_excel_report` every number comes from the tracker, and the model only supplies a short written commentary for the Summary sheet, so it cannot invent figures. The model's role is deciding to call the tools, reading the results and explaining them. The same analysis appears on the dashboard under **Bottlenecks** (`GET /api/bottlenecks`).

Try it: `Which team is slowing us down, and who is overloaded? Save an Excel report.`
If the report file is open in Excel when the agent tries to save it again, close it and rerun.
