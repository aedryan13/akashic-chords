# GitLab auto-merge robot

Runs every 10 minutes on GitHub Actions (free for this public repo). No AI, no GitLab CI minutes.

What it does on `aedryan-gantt-music-group/akashic-chords-source`:

- Opens a merge request for any `claude/*` branch pushed after the start date that has new commits and no open merge request.
  `claude/rewrite-*` goes to `claude/rewrite`; every other `claude/*` branch goes to `claude/tips-tidy`. "Delete source branch" is always off.
- Merges open `claude/*` merge requests into those two targets once GitLab says "mergeable". Pipelines are ignored.
- On a conflict it only adds the label `auto-merge: needs fix` and one comment. A Claude thread fixes it and pushes; the robot retries.
- Waits 10 minutes after the last push before opening a merge request, and does at most 5 of each action per run.

It never touches `main`, never deploys, never deletes branches. Branches `claude/tips-tidy`, `claude/rewrite`, `claude/black-ink-sync` and any branch ending in `-wip` or `-hold` are left alone.
Draft merge requests and ones labelled `hold` are skipped.

Switch: repository variable `AUTOMERGE_MODE` = `on`, `off`, or `dry-run` (the default when unset; logs only).
Token: repository secret `GITLAB_AUTOMERGE_TOKEN` (GitLab personal access token, scope `api`). Never printed.
Settings live in `config.json`. Every run's log is on the Actions tab.
