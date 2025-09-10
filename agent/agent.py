import os, base64, json, datetime, sys, requests, yaml

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

def get_file_sha(path, ref):
    """Return current blob SHA for a file on a branch (needed to update)."""
    r = get_contents(path, ref=ref)
    if r.status_code == 200:
        return r.json().get("sha")
    return None

def update_file_text(path, text, message, branch):
    """Create or update a text file on a branch (UTF-8)."""
    existing_sha = get_file_sha(path, branch)
    b64 = base64.b64encode(text.encode("utf-8")).decode("ascii")
    body = {"message": message, "content": b64, "branch": branch}
    if existing_sha:
        body["sha"] = existing_sha
    r = api("PUT", f"/contents/{path}", json=body)
    r.raise_for_status()
    return r.json()

def update_file_json(path, obj, message, branch):
    """Validate + pretty-write JSON to path."""
    # ensure obj is JSON-serializable
    text = json.dumps(obj, indent=2, ensure_ascii=False)
    return update_file_text(path, text, message, branch)

def extract_json_after_command(raw: str):
    """
    Accept either:
      /update live { ...json... }
      /update live  (followed by a fenced code block):
        ```json
        { ... }
        ```
    Returns: (obj, error_message_or_None)
    """
    s = (raw or "").strip()

    # Prefer fenced code block first
    if "```" in s:
        # split only on the first two fences to keep payload intact
        parts = s.split("```", 2)
        if len(parts) >= 3:
            first = parts[1].strip().lower()
            # handle ```json ... ``` or plain ``` ... ```
            if first.startswith("json"):
                payload = parts[2]
            else:
                payload = parts[1]
            # if a trailing fence exists, trim to it
            if "```" in payload:
                payload = payload.split("```", 1)[0]
            try:
                return json.loads(payload), None
            except Exception as e:
                return None, f"Invalid JSON in code block: {e}"

    # Inline JSON on the same line as the command
    start = s.find("{")
    end = s.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(s[start:end+1]), None
        except Exception as e:
            return None, f"Invalid JSON after command: {e}"

    return None, "No JSON found. Include JSON after the command, e.g. /update live { ... } or use a fenced code block: three backticks, the word json, newline, { ... }, newline, three backticks.”

def open_pr(head_branch, base_branch, title, body=""):
    r = api("POST", "/pulls", json={
        "title": title, "head": head_branch, "base": base_branch, "body": body
    })
    r.raise_for_status()
    return r.json()

def merge_pr(pr_number, commit_title=None):
    """Attempt to merge the PR (squash). Returns (merged_bool, status_text)."""
    payload = {"merge_method": "squash"}
    if commit_title:
        payload["commit_title"] = commit_title
    r = api("PUT", f"/pulls/{pr_number}/merge", json=payload)
    if r.status_code in (200, 201):
        return True, "merged"
    return False, f"{r.status_code}: {r.text[:150]}"

def dispatch_workflow(workflow_filename: str, ref_branch: str):
    """Trigger a workflow_dispatch on the given workflow file."""
    r = api("POST", f"/actions/workflows/{workflow_filename}/dispatches",
            json={"ref": ref_branch})
    return r.status_code in (201, 204), r.status_code

def find_issue_by_title(title):
    r = api("GET", f"/issues", params={"state": "open"})
    if r.status_code == 200:
        for it in r.json():
            if it.get("title", "") == title:
                return it
    return None

def create_issue(title, body):
    r = api("POST", "/issues", json={"title": title, "body": body})
    r.raise_for_status()
    return r.json()

def update_issue_body(number, body):
    r = api("PATCH", f"/issues/{number}", json={"body": body})
    r.raise_for_status()

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
    comment_issue(issue_number,
        f"✅ Make webhook reached successfully ({msg})." if ok
        else f"❌ Could not reach Make webhook ({msg}). Add repo secret `MAKE_WEBHOOK_URL`."
    )

# ---------- Site ensure helpers ----------
def ensure_file(default_branch, rel_path, starter_obj, auto_merge=True):
    """
    Ensure a file exists on the default branch; if missing, open a PR adding it.
    If auto_merge=True, immediately merge the PR (requires PR permissions).
    Returns tuple: (state, pr_url_or_None) where state in {'exists','pr_opened','merged'}.
    """
    resp = get_contents(rel_path, ref=default_branch)
    if resp.status_code == 200:
        return "exists", None

    branch = f"agent/init-{datetime.datetime.utcnow().strftime('%Y%m%d-%H%M')}"
    try:
        create_branch(default_branch, branch)
    except Exception:
        pass  # branch may already exist

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
    pr_url = pr.get("html_url")
    if auto_merge:
        merged, _status = merge_pr(pr.get("number"))
        return ("merged" if merged else "pr_opened"), pr_url
    return "pr_opened", pr_url

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
                "p": 0, "w": 0, "d": 0, "l": 0, "gf": 0, "ga": 0, "gd": 0, "pts": 0
            }]
        if path.endswith("live.json"):
            starter["text"] = "Waiting for next match…"
        state, pr_url = ensure_file(default_branch, path, starter, auto_merge=True)
        results.append((path, state, pr_url))
    return results  # [(path, 'exists'|'pr_opened'|'merged', pr_url|None)]

# ---------- Help text (reads config) ----------
def render_help():
    load_cfg()
    tz = CFG.get("timezone", "Europe/London")
    files = CFG.get("site", {}).get("ensure_files", ["site/data/table.json", "site/data/live.json"])
    gotm = CFG.get("gotm", {}) or {}
    window = gotm.get("vote_window_days", 7)
    channels = ", ".join(gotm.get("channels", [])) or "—"
    return (
        "**Agent commands**\n"
        "- `/help` — show this help\n"
        "- `/status` — repo + Pages status\n"
        "- `/ensure site` — ensure site data files (opens PRs if missing; auto-merges)\n"
        "- `/wire make` — send a test_ping to your Make webhook\n"
        "- `/gotm open` — (placeholder) open Goal of the Month voting\n"
        "- `/gotm close` — (placeholder) close voting & compute winner\n\n"
        "**Current config**\n"
        f"- timezone: `{tz}`\n"
        f"- ensure_files: `{', '.join(files)}`\n"
        f"- GOTM window: `{window}` days\n"
        f"- GOTM channels: `{channels}`\n"
    )

# ---------- Command handling ----------
def handle_command(cmd: str, issue_number: int):
    repo = get_repo()
    default_branch = repo.get("default_branch", "main")
    load_cfg()

    c = (cmd or "").strip().lower()

    if c in ("/help", "help"):
        comment_issue(issue_number, render_help()); return

    if c in ("/status", "status"):
        pages = latest_pages_build()
        msg = [
            f"**Agent status for `{GH_OWNER}/{GH_REPO}`**",
            f"- Default branch: `{default_branch}`",
            f"- Pages build: `{pages.get('status')}` at `{pages.get('updated_at')}`" if pages else "- Pages build: (not available yet)"
        ]
        comment_issue(issue_number, "\n".join(msg)); return

    if c in ("/ensure", "/ensure site"):
        res = ensure_site(default_branch)
        out = ["**Ensure site files**"]
        for path, state, pr in res:
            if state == "exists":
                out.append(f"✅ `{path}` exists")
            elif state == "merged":
                out.append(f"✅ `{path}` added & merged")
            else:
                out.append(f"🆕 `{path}` added — PR: {pr}")
        # trigger deploy after ensuring files
        ok, code = dispatch_workflow("site-deploy.yml", default_branch)
        out.append(f"\nDeploy trigger: {'✅ sent' if ok else f'❌ ({code})'}")
        comment_issue(issue_number, "\n".join(out)); return

    if c in ("/wire make", "/test make"):
        handle_wire_make(issue_number); return

    if c.startswith("/gotm open"):
        window = CFG.get("gotm", {}).get("vote_window_days", 7)
        comment_issue(issue_number, f"📣 GOTM: will open voting for this month (window {window} days). Wire Make/Apps Script to execute."); return

    if c.startswith("/gotm close"):
        comment_issue(issue_number, "⛔ GOTM: will close voting and compute winner. Wire Make/Apps Script to execute."); return

# /update live  {json}  or with a ```json``` block
    if c.startswith("/update live"):
        obj, err = extract_json_after_command(cmd)
        if err:
            comment_issue(issue_number, f"❌ {err}")
            return
        repo = get_repo(); default_branch = repo.get("default_branch", "main")
        try:
            update_file_json(
                "site/data/live.json",
                obj,
                "chore(agent): update live.json via issue command",
                default_branch
            )
            # (optional) trigger deploy
            dispatch_workflow("site-deploy.yml", default_branch)
            comment_issue(issue_number, "✅ Updated `site/data/live.json` and triggered deploy.")
        except Exception as e:
            comment_issue(issue_number, f"❌ Failed to update live.json: {e}")
        return

    # /update table  {json}  (expects whole table.json object)
    if c.startswith("/update table"):
        obj, err = extract_json_after_command(cmd)
        if err:
            comment_issue(issue_number, f"❌ {err}")
            return
        repo = get_repo(); default_branch = repo.get("default_branch", "main")
        try:
            update_file_json(
                "site/data/table.json",
                obj,
                "chore(agent): update table.json via issue command",
                default_branch
            )
            dispatch_workflow("site-deploy.yml", default_branch)
            comment_issue(issue_number, "✅ Updated `site/data/table.json` and triggered deploy.")
        except Exception as e:
            comment_issue(issue_number, f"❌ Failed to update table.json: {e}")
        return
    
    comment_issue(issue_number, "Unknown command. Try `/help`.")

# ---------- Bootstrap (one-click bring-up) ----------
def bootstrap():
    """Auto-fix everything we can, auto-merge, trigger deploy, then post/update a 'Launch checklist' issue."""
    repo = get_repo()
    default_branch = repo.get("default_branch", "main")
    load_cfg()

    ensured = ensure_site(default_branch)  # auto-merges when needed

    # Trigger deploy regardless (safe if unchanged)
    deploy_ok, deploy_code = dispatch_workflow("site-deploy.yml", default_branch)

    # Test Make webhook
    make_ok, make_msg = post_to_make({
        "type": "test_ping",
        "from": "GitHubAgent",
        "repo": f"{GH_OWNER}/{GH_REPO}",
        "ts": datetime.datetime.utcnow().isoformat() + "Z"
    })

    # Pages status (informational)
    pages = latest_pages_build()
    pages_line = f"- Pages build: `{pages.get('status')}` at `{pages.get('updated_at')}`" if pages else "- Pages build: (not available yet)"

    # Build checklist body
    lines = []
    lines.append("## 🚀 Launch checklist")
    lines.append(f"- Repo: `{GH_OWNER}/{GH_REPO}`  |  Default branch: `{default_branch}`")
    lines.append(pages_line)
    lines.append("")
    lines.append("### Site data")
    for path, state, pr in ensured:
        if state == "exists":
            lines.append(f"- ✅ `{path}` — exists")
        elif state == "merged":
            lines.append(f"- ✅ `{path}` — added & merged")
        else:
            lines.append(f"- 🆕 `{path}` — PR: {pr}")
    lines.append("")
    lines.append("### Pages deploy")
    lines.append(f"- Trigger sent: {'✅' if deploy_ok else f'❌ ({deploy_code})'}")
    lines.append("")
    lines.append("### Make webhook")
    lines.append(f"- {'✅ Reachable' if make_ok else '❌ Not reachable'} ({make_msg})")
    if not make_ok:
        lines.append("  - Add repo secret **MAKE_WEBHOOK_URL** with your Custom Webhook URL.")
    lines.append("")
    lines.append("### Config (from agent/config.yml)")
    tz = CFG.get("timezone", "Europe/London")
    files = ", ".join(CFG.get("site", {}).get("ensure_files", [])) or "site/data/table.json, site/data/live.json"
    gotm = CFG.get("gotm", {}) or {}
    window = gotm.get("vote_window_days", 7)
    channels = ", ".join(gotm.get("channels", [])) or "—"
    lines.append(f"- timezone: `{tz}`")
    lines.append(f"- ensure_files: `{files}`")
    lines.append(f"- GOTM window: `{window}` days")
    lines.append(f"- GOTM channels: `{channels}`")

    title = "Agent: Launch checklist"
    existing = find_issue_by_title(title)
    body = "\n".join(lines)
    if existing:
        update_issue_body(existing["number"], body)
        comment_issue(existing["number"], "🔁 Checklist updated.")
    else:
        create_issue(title, body)

# ---------- Entrypoint ----------
def main():
    mode = (sys.argv[sys.argv.index("--mode")+1] if "--mode" in sys.argv else "").strip()

    if mode == "listen":
        # Respond to ANY issue open/edit or ANY new comment (no label required)
        event_path = os.getenv("GITHUB_EVENT_PATH")
        if not event_path or not os.path.exists(event_path):
            print("No event payload found."); return
        with open(event_path, "r", encoding="utf-8") as f:
            event = json.load(f)

        if "comment" in event:   # issue_comment
            body = (event["comment"].get("body") or "").strip()
            issue_number = event["issue"]["number"]
            handle_command(body, issue_number); return

        if "issue" in event:     # issues opened/edited
            first_line = ((event["issue"].get("body") or "").splitlines() or [""])[0]
            handle_command(first_line, event["issue"]["number"]); return

        return

    if mode == "bootstrap":
        bootstrap(); return

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
