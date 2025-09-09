import os, base64, json, datetime, sys, requests, yaml, re

# ---------- Environment ----------
repo_env = os.getenv("GITHUB_REPOSITORY", "")
env_owner, env_repo = (repo_env.split("/", 1) + ["", ""])[:2]
GH_OWNER = os.getenv("GH_OWNER") or env_owner or "your-username"
GH_REPO  = os.getenv("GH_REPO")  or env_repo  or "your-repo"
TOKEN    = os.getenv("AGENT_GH_TOKEN", "")
API      = "https://api.github.com"

SESSION = requests.Session()
if TOKEN:
    SESSION.headers.update({"Authorization": f"Bearer {TOKEN}"})
SESSION.headers.update({"Accept": "application/vnd.github+json"})

CFG = {}
def load_cfg():
    """Load optional agent/config.yml to customize behavior."""
    global CFG
    try:
        with open("agent/config.yml", "r", encoding="utf-8") as f:
            CFG = yaml.safe_load(f) or {}
    except Exception:
        CFG = {}

# ---------- GitHub helpers ----------
def api(method, path, **kwargs):
    url = f"{API}/repos/{GH_OWNER}/{GH_REPO}{path}"
    return SESSION.request(method, url, **kwargs)

def get_repo():
    r = SESSION.get(f"{API}/repos/{GH_OWNER}/{GH_REPO}")
    r.raise_for_status()
    return r.json()

def get_branch_sha(branch):
    r = api("GET", f"/git/ref/heads/{branch}")
    return r.json()["object"]["sha"] if r.status_code == 200 else None

def create_branch(from_branch, new_branch):
    sha = get_branch_sha(from_branch)
    if not sha:
        raise RuntimeError(f"Base branch not found: {from_branch}")
    r = api("POST", "/git/refs", json={"ref": f"refs/heads/{new_branch}", "sha": sha})
    r.raise_for_status()

def get_contents(path, ref=None):
    params = {"ref": ref} if ref else {}
    return api("GET", f"/contents/{path}", params=params)

def put_contents(path, content_bytes, message, branch):
    b64 = base64.b64encode(content_bytes).decode("ascii")
    r = api("PUT", f"/contents/{path}", json={
        "message": message, "content": b64, "branch": branch
    })
    r.raise_for_status()
    return r.json()

def open_pr(head_branch, base_branch, title, body=""):
    r = api("POST", "/pulls", json={
        "title": title, "head": head_branch, "base": base_branch, "body": body
    })
    r.raise_for_status()
    return r.json()

def comment_issue(issue_number, body):
    r = api("POST", f"/issues/{issue_number}/comments", json={"body": body})
    r.raise_for_status()

def latest_pages_build():
    r = SESSION.get(f"{API}/repos/{GH_OWNER}/{GH_REPO}/pages/builds/latest")
    return r.json() if r.status_code == 200 else None

# ---------- Make webhook test ----------
def post_to_make(payload: dict):
    url = os.getenv("MAKE_WEBHOOK_URL")
    if not url:
        return False, "MAKE_WEBHOOK_URL secret missing"
    r = requests.post(url, json=payload, timeout=20)
    return (200 <= r.status_code < 300), f"HTTP {r.status_code}"

def handle_wire_make(issue_number: int):
    ok, msg = post_to_make({
        "type": "test_ping",
        "from": "GitHubAgent",
        "repo": f"{GH_OWNER}/{GH_REPO}",
        "ts": datetime.datetime.utcnow().isoformat() + "Z"
    })
    if ok:
        comment_issue(issue_number, f"✅ Make webhook reached successfully ({msg}).")
    else:
        comment_issue(issue_number, f"❌ Could not reach Make webhook ({msg}). Add repo secret `MAKE_WEBHOOK_URL`.")

# ---------- Site ensure helpers ----------
def ensure_file(default_branch, rel_path, starter_obj):
    """Ensure a file exists on the default branch; open a PR to add it if missing."""
    resp = get_contents(rel_path, ref=default_branch)
    if resp.status_code == 200:
        return "exists", None

    branch = f"agent/init-{datetime.datetime.utcnow().strftime('%Y%m%d-%H%M')}"
    try:
        create_branch(default_branch, branch)
    except Exception:
        # branch may already exist from a prior step; ignore
        pass

    put_contents(
        rel_path,
        json.dumps(starter_obj, indent=2).encode("utf-8"),
        f"chore(agent): add starter {rel_path}",
        branch
    )
    pr = open_pr(
        branch,
        default_branch,
        f"Agent: add starter {os.path.basename(rel_path)}",
        f"Adds minimal `{rel_path}` so the site widget renders."
    )
    return "pr_opened", pr.get("html_url")

def ensure_site(default_branch):
    """Ensure core site data files exist (configurable via agent/config.yml)."""
    load_cfg()
    want = CFG.get("site", {}).get("ensure_files", ["site/data/table.json", "site/data/live.json"])
    results = []
    for path in want:
        starter = {"updated": datetime.datetime.utcnow().isoformat() + "Z"}
        if path.endswith("table.json"):
            starter["rows"] = [{
                "team": "Syston Town Tigers",
                "p": 0, "w": 0, "d": 0, "l": 0,
                "gf": 0, "ga": 0, "gd": 0, "pts": 0
            }]
        if path.endswith("live.json"):
            starter["text"] = "Waiting for next match…"
        state, pr_url = ensure_file(default_branch, path, starter)
        results.append((path, state, pr_url))
    return results  # list of tuples: (path, state, pr_url_or_None)

# ---------- Help text ----------
HELP_TEXT = """**Agent commands**
- `/help` — show this help
- `/status` — repo + Pages status
- `/ensure site` — ensure site/data/table.json and site/data/live.json (opens PRs if missing)
- `/wire make` (alias `/test make`) — send a test_ping to your Make webhook
- `/gotm open` — (placeholder) open Goal of the Month voting
- `/gotm close` — (placeholder) close voting and compute winner

Tip: run commands as the first line of a new issue body, or as a comment on any issue.
"""

# ---------- Command handling ----------
def handle_command(cmd: str, issue_number: int):
    repo = get_repo()
    default_branch = repo.get("default_branch", "main")
    load_cfg()

    c = (cmd or "").strip().lower()

    if c in ("/help", "help"):
        comment_issue(issue_number, HELP_TEXT)
        return

    if c in ("/status", "status"):
        pages = latest_pages_build()
        msg = [
            f"**Agent status for `{GH_OWNER}/{GH_REPO}`**",
            f"- Default branch: `{default_branch}`",
            f"- Pages build: `{pages.get('status')}` at `{pages.get('updated_at')}`" if pages else "- Pages build: (not available yet)"
        ]
        comment_issue(issue_number, "\n".join(msg))
        return

    if c in ("/ensure", "/ensure site"):
        res = ensure_site(default_branch)  # list of (path, state, pr)
        lines = ["**Ensure site files**"]
        for path, state, pr in res:
            lines.append(f"✅ `{path}` already exists." if state == "exists" else f"🆕 `{path}` added — PR: {pr}")
        comment_issue(issue_number, "\n".join(lines))
        return

    if c in ("/wire make", "/test make"):
        handle_wire_make(issue_number)
        return

    if c.startswith("/gotm open"):
        window = CFG.get("gotm", {}).get("vote_window_days", 7)
        comment_issue(issue_number, f"📣 GOTM: will open voting for this month (window {window} days). Hook Make/Apps Script to execute.")
        return

    if c.startswith("/gotm close"):
        comment_issue(issue_number, "⛔ GOTM: will close voting and compute winner. Hook Make/Apps Script to execute.")
        return

    # Fallback
    comment_issue(issue_number, "Unknown command. Try `/help`.")

# ---------- Entrypoint ----------
def main():
    mode = (sys.argv[sys.argv.index("--mode")+1] if "--mode" in sys.argv else "").strip()

    if mode == "listen":
        # Respond to ANY issue open/edit or ANY new comment (no label required)
        event_path = os.getenv("GITHUB_EVENT_PATH")
        if not event_path or not os.path.exists(event_path):
            print("No event payload found.")
            return
        with open(event_path, "r", encoding="utf-8") as f:
            event = json.load(f)

        # Any new comment → treat comment text as a command
        if "comment" in event:   # issue_comment
            body = (event["comment"].get("body") or "").strip()
            issue_number = event["issue"]["number"]
            handle_command(body, issue_number)
            return

        # Any issue open/edit → first line of body is the command (if any)
        if "issue" in event:   # issues opened/edited
            first_line = ((event["issue"].get("body") or "").splitlines() or [""])[0]
            handle_command(first_line, event["issue"]["number"])
            return

        return

    # default: scheduled run (site checks)
    repo = get_repo()
    default_branch = repo.get("default_branch", "main")
    results = ensure_site(default_branch)
    for path, state, pr in results:
        print(path, state, pr or "")
    pages = latest_pages_build()
    print("Pages:", (pages or {}).get("status"))

if __name__ == "__main__":
    try:
        load_cfg()
        main()
    except Exception as e:
        print("❌ Agent error:", e)
        sys.exit(1)
