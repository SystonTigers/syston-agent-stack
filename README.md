# Syston Automation — Agent-Built Hybrid Stack (Option B, Full)

This repo contains:
- **/agent** — Python scaffold for a builder/supervisor agent
- **/apps_script** — Apps Script hook to connect your current script to Make.com
- **/.github/workflows** — GitHub Actions for GitHub Pages deploy + scheduled jobs
- **/site** — Yellow/black starter site that reads JSON from `/site/data`
- **/make** — Make.com scenario blueprint (stub)

Quick start:
1) Push these files.  
2) Settings → Pages → Source = GitHub Actions.  
3) In Apps Script, add Script Property `MAKE_WEBHOOK_URL` (your Make webhook).  
4) Copy `apps_script/agent_hooks.gs` into your Apps Script project if you don’t already have `postToMake()`.  
