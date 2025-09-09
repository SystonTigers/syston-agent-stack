import os, base64, json, datetime, sys
import requests

"""
Agent capabilities in this scaffold:
- Checks GitHub Pages readiness.
- Ensures /site/data/table.json exists; if missing, opens a PR to add it.
- Prints a run summary (visible in Actions logs).

Requires repo secret: AGENT_GH_TOKEN (PAT with Contents & PR permissions).
"""

GH_OWNER = os.getenv("GH_OWNER") or "your-username"
GH_REPO  = os.getenv("GH_REPO")  or "your-repo"
TOKEN    = os.getenv("AGENT_GH_TOKEN", "")
API      = f"https://api.github.com"
SESSION  = requests.Session()

if TOKEN:
    SESSION.headers.update({"Authorization": f"Bearer {TOKEN}"})
SESSION.headers.update({"Accept": "application/vnd.github+json"})

def api(method, path, **kwargs):
    url = f"{API}/repos/{GH_OWNER}/{GH_REPO}{path}"
    r = SESSION.request(method, url, **kwargs)
    if r.status_code >= 400:
        print(f"[API {method} {path}] {r.status_code}: {r.text[:300]}")
    return r

def get_repo():
    r = SESSION.get(f"{API}/repos/{GH_OWNER}/{GH_REPO}")
    r.raise_for_status()
    return r.json()

def get_branch_sha(branch):
    r = api("GET", f"/git/ref/heads/{branch}")
    if r.status_code == 200:
        return r.json()["object"]["sha"]
    return None

def create_branch(from_branch, new_branch):
    sha = get_branch_sha(from_branch)
    if not sha:
        raise RuntimeError(f"Base branch not found: {from_branch}")
    r = api("POST", "/git/refs", json={"ref": f"refs/heads/{new_branch}", "sha": sha})
    r.raise_for_status()
    return True

def get_contents(path, ref=None):
    params = {"ref": ref} if ref else {}
    r = api("GET", f"/contents/{path}", params=params)
    return r

def put_contents(path, content_bytes, message, branch):
    b64 = base64.b64encode(content_bytes).decode("ascii")
    r = api("PUT", f"/contents/{path}", json={
        "message": message,
        "content": b64,
        "branch": branch
    })
    r.raise_for_status()
    return r.json()

def open_pr(head_branch, base_branch, title, body=""):
    r = api("POST", "/pulls", json={
        "title": title,
        "head": head_branch,
        "base": base_branch,
        "body": body
    })
    r.raise_for_status()
    return r.json()

def latest_pages_build():
    r = SESSION.get(f"{API}/repos/{GH_OWNER}/{GH_REPO}/pages/builds/latest")
    if r.status_code == 200:
        return r.json()
    return None

def ensure_table_json(default_branch):
    """Ensure /site/data/table.json exists; if not, create a PR adding a minimal file."""
    target_path = "site/data/table.json"
    check = get_contents(target_path, ref=default_branch)
    if check.status_code == 200:
        print("✅ table.json already exists.")
        return "exists"

    print("ℹ️ table.json missing — preparing PR to add a starter file.")
    branch = f"agent/init-data-{datetime.datetime.utcnow().strftime('%Y%m%d-%H%M')}"
    create_branch(default_branch, branch)

    starter = {
        "updated": datetime.datetime.utcnow().isoformat() + "Z",
        "rows": [
            {"team":"Syston Town Tigers","p":0,"w":0,"d":0,"l":0,"gf":0,"ga":0,"gd":0,"pts":0}
        ]
    }
    put_contents(
        target_path,
        json.dumps(starter, indent=2).encode("utf-8"),
        "chore(agent): add starter site/data/table.json",
        branch
    )
    pr = open_pr(branch, default_branch,
                 "Agent: add starter table.json for site",
                 "This PR adds a minimal /site/data/table.json so the league table iframe renders.")
    print(f"✅ PR opened: #{pr.get('number')} {pr.get('html_url')}")
    return "pr_opened"

def main():
    print(f"Agent starting for {GH_OWNER}/{GH_REPO}")
    if not TOKEN:
        print("⚠️ No AGENT_GH_TOKEN provided. Agent will run read-only.")
    repo = get_repo()
    default_branch = repo.get("default_branch", "main")
    print(f"- Default branch: {default_branch}")

    # Check Pages build status (informational)
    pages = latest_pages_build()
    if pages:
        print(f"- Pages latest build: {pages.get('status')} at {pages.get('updated_at')}")
    else:
        print("- Pages build info not available yet (enable Pages via Settings → Pages).")

    # Ensure data file exists (opens PR if missing)
    outcome = ensure_table_json(default_branch)

    print(f"Run complete. Outcome: {outcome}")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("❌ Agent error:", e)
        sys.exit(1)
