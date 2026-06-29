import requests
import csv
import json
import time
from datetime import datetime, timezone
from collections import Counter

# ─────────────────────────────────────────────
# CONFIG — set via GitHub Secrets
# ─────────────────────────────────────────────
import os
COMPANY_NAME = os.environ.get("PROOFHUB_COMPANY", "srvmedia")
API_KEY      = os.environ.get("PROOFHUB_API_KEY", "YOUR_API_KEY_HERE")

BASE_URL = f"https://{COMPANY_NAME}.proofhub.com/api/v3"
HEADERS  = {
    "X-API-KEY":  API_KEY,
    "User-Agent": "ZohoIntegration (ashish.kate@srvmedia.com)",
    "Accept":     "application/json"
}

# ─────────────────────────────────────────────
# DATE FILTER
# ─────────────────────────────────────────────
DATE_FROM = datetime(2025, 4,  1,  0,  0,  0, tzinfo=timezone.utc)
DATE_TO   = datetime(2026, 3, 31, 23, 59, 59, tzinfo=timezone.utc)


def in_date_range(date_str):
    if not date_str:
        return False
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return DATE_FROM <= dt <= DATE_TO
    except Exception:
        return False


def extract_list(data, *keys):
    if isinstance(data, list):
        return [i for i in data if isinstance(i, dict)]
    if isinstance(data, dict):
        for key in list(keys) + ["projects", "tasks", "todolists", "data", "items", "results"]:
            if key in data and isinstance(data[key], list):
                return data[key]
        for v in data.values():
            if isinstance(v, list):
                return v
    return []


def safe_get(url, params=None, retries=3, delay=5):
    """GET with retry on connection errors and 500s."""
    for attempt in range(retries):
        try:
            response = requests.get(url, headers=HEADERS, params=params, timeout=30)
            if response.status_code == 200:
                return response
            elif response.status_code == 500:
                print(f"     ⚠ 500 error (attempt {attempt+1}/{retries}), retrying in {delay}s...")
                time.sleep(delay)
            elif response.status_code == 429:
                print(f"     ⚠ Rate limited, waiting 15s...")
                time.sleep(15)
            else:
                print(f"     ❌ HTTP {response.status_code}: {response.text[:100]}")
                return None
        except requests.exceptions.ConnectionError as e:
            print(f"     ⚠ Connection error (attempt {attempt+1}/{retries}): {str(e)[:80]}")
            time.sleep(delay * (attempt + 1))  # increasing backoff
        except requests.exceptions.Timeout:
            print(f"     ⚠ Timeout (attempt {attempt+1}/{retries}), retrying...")
            time.sleep(delay)
    print(f"     ❌ Failed after {retries} attempts, skipping.")
    return None


# ─────────────────────────────────────────────
# STEP 1 — Get ALL projects (single call)
# ─────────────────────────────────────────────
def get_all_projects():
    print("Fetching all projects...")
    response = safe_get(f"{BASE_URL}/projects")
    if not response:
        return []

    data = response.json()
    # Extract name correctly — ProofHub returns projects as list of dicts
    projects = extract_list(data, "projects")

    # Ensure each project has a proper name field
    result = []
    for p in projects:
        if isinstance(p, dict) and p.get("id"):
            result.append({
                "id":   p.get("id"),
                "name": p.get("name") or p.get("title") or str(p.get("id"))
            })

    print(f"✅ Total projects found: {len(result)}\n")
    return result


# ─────────────────────────────────────────────
# STEP 2 — Get todolists for a project
# ─────────────────────────────────────────────
def get_todolists(project_id):
    response = safe_get(f"{BASE_URL}/projects/{project_id}/todolists")
    if not response:
        return []
    return extract_list(response.json(), "todolists")


# ─────────────────────────────────────────────
# STEP 3 — Get ALL tasks (open + completed)
# ─────────────────────────────────────────────
def get_all_tasks(project_id, todolist_id):
    url      = f"{BASE_URL}/projects/{project_id}/todolists/{todolist_id}/tasks"
    seen_ids = set()
    all_tasks = []

    for params in [{}, {"completed": "true"}]:
        response = safe_get(url, params=params)
        if response:
            for task in extract_list(response.json(), "tasks"):
                tid = task.get("id")
                if tid and tid not in seen_ids:
                    seen_ids.add(tid)
                    all_tasks.append(task)
        time.sleep(0.5)  # pause between open/completed calls

    return all_tasks


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
projects = get_all_projects()

if not projects:
    print("❌ No projects found. Check API key.")
    exit(1)

all_rows   = []
skipped    = 0

for idx, proj in enumerate(projects, 1):
    project_id   = proj["id"]
    project_name = proj["name"]

    print(f"[{idx}/{len(projects)}] 📁 '{project_name}' (ID: {project_id})")

    todolists = get_todolists(project_id)
    if not todolists:
        print(f"   (no todolists or error)\n")
        skipped += 1
        time.sleep(0.5)
        continue

    print(f"   Todolists: {len(todolists)}")

    for tl in todolists:
        tl_id   = tl.get("id")
        tl_name = tl.get("title") or tl.get("name", "Unknown List")

        tasks   = get_all_tasks(project_id, tl_id)
        matched = 0

        for task in tasks:
            if not in_date_range(task.get("created_at", "")):
                continue

            stage         = task.get("stage") or {}
            stage_name    = stage.get("name", "") if isinstance(stage, dict) else ""
            workflow      = task.get("workflow") or {}
            workflow_name = workflow.get("name", "") if isinstance(workflow, dict) else ""
            assigned_ids  = task.get("assigned", [])

            all_rows.append({
                "Project":      project_name,
                "Project ID":   project_id,
                "Task List":    tl_name,
                "Task Title":   task.get("title", ""),
                "Stage":        stage_name,
                "Workflow":     workflow_name,
                "Completed":    task.get("completed", False),
                "Created At":   task.get("created_at", ""),
                "Start Date":   task.get("start_date") or "",
                "Due Date":     task.get("due_date") or "",
                "Assigned IDs": ", ".join(str(i) for i in assigned_ids),
                "Task ID":      task.get("id", ""),
                "Ticket #":     task.get("ticket", ""),
            })
            matched += 1

        if matched:
            print(f"   ✅ '{tl_name}': {matched} tasks in range")

    print()
    time.sleep(0.8)  # pause between projects to avoid rate limiting

# ─────────────────────────────────────────────
# SAVE OUTPUT
# ─────────────────────────────────────────────
csv_file  = "output/proofhub_all_tasks.csv"
json_file = "output/proofhub_all_tasks.json"

os.makedirs("output", exist_ok=True)

if all_rows:
    fieldnames = [
        "Project", "Project ID", "Task List", "Task Title", "Stage", "Workflow",
        "Completed", "Created At", "Start Date", "Due Date",
        "Assigned IDs", "Task ID", "Ticket #"
    ]

    with open(csv_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_rows)

    with open(json_file, "w", encoding="utf-8") as f:
        json.dump(all_rows, f, indent=2, default=str)

    print(f"\n✅ {len(all_rows)} tasks saved to: {csv_file}")
    print(f"📁 Raw JSON saved to:              {json_file}")
    print(f"⚠  Projects skipped (error/empty): {skipped}")

    print(f"\n📊 Tasks by Stage:")
    for stage, count in sorted(Counter(r["Stage"] for r in all_rows).items(), key=lambda x: -x[1]):
        print(f"   {stage or '(no stage)'}: {count}")

    print(f"\n📊 Top 10 Projects by Task Count:")
    for proj, count in Counter(r["Project"] for r in all_rows).most_common(10):
        print(f"   {proj}: {count}")

else:
    print("\n⚠ No tasks matched the date range.")

print("\n" + "=" * 50)
print("DONE")
print("=" * 50)
