import json, pathlib
from datetime import datetime

CONFIG_PATH = pathlib.Path(__file__).parent / "config.json"

DEFAULT_CONFIG = {
    "github_owner": "your-username",
    "github_repo": "your-repo",
    "default_branch": "main",
    "bot_committer_name": "syston-agent-bot",
    "bot_committer_email": "bot@example.com",
    "paths": {
        "apps_script": "../apps_script",
        "site": "../site",
        "workflows": "../.github/workflows"
    }
}

def load_config():
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return DEFAULT_CONFIG

def summarize_run():
    print(f"[{datetime.utcnow().isoformat()}] Summary: no failures detected (scaffold).")

def main():
    cfg = load_config()
    print("Loaded config:", json.dumps(cfg, indent=2))
    summarize_run()

if __name__ == "__main__":
    main()
