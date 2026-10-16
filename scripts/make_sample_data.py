"""Create data/tasks.xlsx: a sample employee task tracker.

Deadlines are generated relative to today so there are always overdue,
due-soon and future tasks. A few rows are deliberately messy (inconsistent
status names, a missing deadline, odd priority casing) so the agent has to
cope with imperfect data.
"""
import os
import random
from datetime import date, timedelta

import pandas as pd

random.seed(42)
today = date.today()

TASKS = [
    ("Prepare payroll for the month", "HR", "Priya Sharma"),
    ("Schedule onboarding for new hires", "HR", "Priya Sharma"),
    ("Update employee handbook", "HR", "Rahul Verma"),
    ("Conduct exit interviews", "HR", "Rahul Verma"),
    ("Follow up with Q3 leads", "Sales", "Ankit Desai"),
    ("Send proposal to Acme Corp", "Sales", "Ankit Desai"),
    ("Update CRM contact records", "Sales", "Neha Kulkarni"),
    ("Prepare monthly sales forecast", "Sales", "Neha Kulkarni"),
    ("Renew vendor contracts", "Operations", "Suresh Patil"),
    ("Audit warehouse inventory", "Operations", "Suresh Patil"),
    ("Fix delivery routing sheet", "Operations", "Meera Joshi"),
    ("Review supplier invoices", "Operations", "Meera Joshi"),
    ("Book Q4 board meeting room", "Admin", "Karan Mehta"),
    ("Renew office insurance policy", "Admin", "Karan Mehta"),
    ("Order office supplies", "Admin", "Divya Nair"),
    ("Organize team offsite travel", "Admin", "Divya Nair"),
    ("Reconcile bank statements", "Finance", "Amit Bhosale"),
    ("File GST returns", "Finance", "Amit Bhosale"),
    ("Prepare quarterly budget review", "Finance", "Sneha Pawar"),
    ("Process vendor payments", "Finance", "Sneha Pawar"),
    ("Patch internal servers", "IT", "Vikram Rao"),
    ("Set up laptops for new joiners", "IT", "Vikram Rao"),
    ("Renew SSL certificates", "IT", "Pooja Iyer"),
    ("Migrate shared drive to cloud", "IT", "Pooja Iyer"),
]

STATUS_POOL = ["Pending", "In Progress", "Completed", "Blocked"]
MESSY_STATUS = ["done", "WIP", "not started", "On Hold", "in progress"]
PRIORITIES = ["High", "Medium", "Low"]

rows = []
for i, (task, dept, owner) in enumerate(TASKS, start=1):
    status = random.choice(STATUS_POOL)
    deadline = today + timedelta(days=random.randint(-20, 14))
    priority = random.choice(PRIORITIES)
    rows.append(
        {
            "task_id": f"T{i:03d}",
            "task": task,
            "department": dept,
            "owner": owner,
            "deadline": deadline,
            "status": status,
            "priority": priority,
        }
    )

# Deliberate data-quality problems
rows[2]["status"] = random.choice(MESSY_STATUS)
rows[5]["status"] = "WIP"
rows[9]["status"] = "done"
rows[13]["priority"] = "high"
rows[17]["deadline"] = None  # missing deadline
rows[21]["priority"] = "URGENT"  # not a standard priority

# Make sure the demo always contains clear overdue open work
rows[0].update(deadline=today - timedelta(days=12), status="Pending", priority="High")
rows[8].update(deadline=today - timedelta(days=7), status="In Progress", priority="High")
rows[16].update(deadline=today - timedelta(days=3), status="Blocked", priority="Medium")

os.makedirs("data", exist_ok=True)
pd.DataFrame(rows).to_excel("data/tasks.xlsx", index=False)
print(f"Wrote data/tasks.xlsx with {len(rows)} tasks (today = {today})")
