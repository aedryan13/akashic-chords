#!/usr/bin/env python3
"""GitLab auto-merge robot for Akashic Chords (no AI, no GitLab CI minutes).

Every run:
  1. Finds pushed claude/* branches that have new commits and no open merge request,
     and opens one to the right target (never main). "Delete source branch" stays OFF.
  2. Merges open merge requests from claude/* branches into an allowed target once
     GitLab says they are mergeable. Pipelines are ignored (none run right now).
  3. On a conflict it does nothing except label the merge request "auto-merge: needs fix"
     and leave one comment, so a Claude thread can fix it.
It never deploys, never deletes branches, never touches main.

Mode (env AUTOMERGE_MODE): "on" = act, "off" = do nothing, anything else = dry-run (log only).
Token: env GITLAB_AUTOMERGE_TOKEN (api scope). It is never printed.
"""
import json, os, sys, time, urllib.parse, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta

CFG = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")))
API = "https://gitlab.com/api/v4"
PROJECT = urllib.parse.quote(CFG["project"], safe="")
MODE = (os.environ.get("AUTOMERGE_MODE") or "dry-run").strip().lower()
if MODE not in ("on", "off"):
    MODE = "dry-run"
TOKEN = os.environ.get("GITLAB_AUTOMERGE_TOKEN", "").strip()
NOW = datetime.now(timezone.utc)
SETTLE = timedelta(minutes=CFG["settle_minutes"])
START_AFTER = datetime.fromisoformat(CFG["only_branches_pushed_after"].replace("Z", "+00:00"))
LABEL = CFG["conflict_label"]
TARGETS = set(CFG["allowed_targets"])
LOG = []


def log(msg):
    line = f"{NOW:%Y-%m-%d %H:%M}Z [{MODE}] {msg}"
    LOG.append(line)
    print(line, flush=True)


def call(method, path, params=None, data=None):
    url = f"{API}/projects/{PROJECT}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method, headers={"PRIVATE-TOKEN": TOKEN})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            if e.code >= 500 and attempt < 2:
                time.sleep(3 * (attempt + 1))
                continue
            raise RuntimeError(f"GitLab said {e.code} for {method} {path.split('?')[0]}: {e.read()[:200]!r}")
        except urllib.error.URLError:
            if attempt < 2:
                time.sleep(3 * (attempt + 1))
                continue
            raise


def get_all(path, params=None):
    out, page = [], 1
    while True:
        p = dict(params or {}, per_page=100, page=page)
        chunk = call("GET", path, p)
        out += chunk
        if len(chunk) < 100:
            return out
        page += 1


def when(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def target_for(branch):
    """Which branch a claude/* branch merges into, or None to leave it alone."""
    if not branch.startswith("claude/") or branch in CFG["never_touch_branches"]:
        return None
    if any(branch.endswith(s) for s in CFG["skip_suffixes"]):
        return None
    for rule in CFG["target_rules"]:
        if branch.startswith(rule["prefix"]):
            return rule["target"]
    return CFG["default_target"]


def safe_target(t):
    return t in TARGETS and t not in ("main", "master")


def open_missing_mrs(open_mrs, all_mrs):
    have_open = {m["source_branch"] for m in open_mrs}
    made = 0
    for b in get_all("/repository/branches"):
        name, sha = b["name"], b["commit"]["id"]
        target = target_for(name)
        if not target or name in have_open:
            continue
        if not safe_target(target):
            continue
        pushed = when(b["commit"]["committed_date"])
        if pushed < START_AFTER:
            continue  # older branches are never touched
        if NOW - pushed < SETTLE:
            log(f"wait   {name}: pushed {int((NOW - pushed).total_seconds() // 60)} min ago, waiting for it to settle")
            continue
        if any(m["source_branch"] == name and m["state"] == "closed" and m["sha"] == sha for m in all_mrs):
            continue  # someone closed this exact version on purpose
        cmp = call("GET", "/repository/compare", {"from": target, "to": name, "straight": "false"})
        if not cmp.get("commits"):
            continue  # nothing new, already merged
        if made >= CFG["max_actions_per_run"]:
            log(f"later  {name}: hit this run's limit, next run will open it")
            continue
        title = (cmp["commits"][-1].get("title") or name)[:200]
        if MODE == "on":
            mr = call("POST", "/merge_requests", data={
                "source_branch": name, "target_branch": target, "title": title,
                "remove_source_branch": "false", "squash": "false",
                "description": "Opened automatically by the auto-merge robot. It merges this when GitLab says it can. "
                               "Mark it Draft or add the label \"hold\" to stop it.",
            })
            log(f"opened !{mr['iid']} {name} -> {target}")
        else:
            log(f"WOULD open MR {name} -> {target} ({len(cmp['commits'])} new commits)")
        made += 1


def merge_ready(open_mrs):
    merged = 0
    for m in sorted(open_mrs, key=lambda x: x["iid"]):
        iid, src, tgt = m["iid"], m["source_branch"], m["target_branch"]
        if not src.startswith("claude/") or not safe_target(tgt) or target_for(src) != tgt:
            continue  # not ours (e.g. anything into main) - never touched
        labels = set(m.get("labels") or [])
        if m.get("draft") or m.get("work_in_progress") or labels & set(CFG["hold_labels"]):
            log(f"skip   !{iid} {src}: on hold (draft or hold label)")
            continue
        status = m.get("detailed_merge_status")
        if status in ("unchecked", "checking", "approvals_syncing", "preparing"):
            # ask GitLab to check it; act on the next run
            call("GET", f"/merge_requests/{iid}", {"with_merge_status_recheck": "true"})
            log(f"wait   !{iid} {src}: GitLab is still checking it")
            continue
        if status in ("conflict", "broken_status", "need_rebase"):
            if LABEL in labels:
                continue  # already flagged; a thread will fix it
            if MODE == "on":
                call("PUT", f"/merge_requests/{iid}", data={"add_labels": LABEL})
                call("POST", f"/merge_requests/{iid}/notes", data={"body":
                    f"Auto-merge robot: this cannot merge into `{tgt}` ({status}). "
                    f"A Claude thread needs to merge `{tgt}` into `{src}`, fix the clash and push. "
                    f"The robot retries after the next push."})
            log(f"FLAG   !{iid} {src} -> {tgt}: {status} - needs a thread to fix")
            continue
        if status != "mergeable":
            log(f"wait   !{iid} {src}: GitLab status is {status}")
            continue
        if merged >= CFG["max_actions_per_run"]:
            log(f"later  !{iid} {src}: hit this run's limit")
            continue
        if MODE == "on":
            if LABEL in labels:
                call("PUT", f"/merge_requests/{iid}", data={"remove_labels": LABEL})
            if m.get("force_remove_source_branch"):
                call("PUT", f"/merge_requests/{iid}", data={"remove_source_branch": "false"})
            call("PUT", f"/merge_requests/{iid}/merge", data={
                "should_remove_source_branch": "false", "squash": "false", "sha": m["sha"]})
            log(f"MERGED !{iid} {src} -> {tgt}")
        else:
            log(f"WOULD merge !{iid} {src} -> {tgt}")
        merged += 1


def main():
    if MODE == "off":
        log("switched off (AUTOMERGE_MODE=off) - did nothing")
        return 0
    if not TOKEN:
        log("no GITLAB_AUTOMERGE_TOKEN secret yet - did nothing")
        return 1
    all_mrs = get_all("/merge_requests", {"state": "all", "updated_after": (START_AFTER - timedelta(days=7)).isoformat()})
    open_mrs = [m for m in all_mrs if m["state"] == "opened"]
    merge_ready(open_mrs)
    open_missing_mrs(open_mrs, all_mrs)
    if len(LOG) == 0:
        log("nothing to do")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write("### Auto-merge robot\n\n```\n" + "\n".join(LOG) + "\n```\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # keep the token out of any message
        msg = str(e).replace(TOKEN, "***") if TOKEN else str(e)
        log(f"ERROR {msg}")
        sys.exit(1)
