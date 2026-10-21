"""`pr open|status|comments|close`: the hand-back through a pull request, with gh (GitHub),
az (Azure DevOps) or `other` (push only; a human opens the PR). Only the orchestrator runs this,
and it is the only place the factory pushes."""

import json
import re
import subprocess
import tempfile

from . import gitutil, jobs, schedule
from .errors import FactoryError
from .paths import branch_name

AZ_STATE = {"active": "OPEN", "completed": "MERGED", "abandoned": "CLOSED"}


def _cli(args, cwd, timeout=300):
    try:
        result = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise FactoryError(f"{args[0]} is not installed")
    if result.returncode != 0:
        raise FactoryError(f"{' '.join(args[:3])} failed: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout


def _section(text, heading):
    m = re.search(rf"^##\s+{re.escape(heading)}\s*$(.*?)(?=^##\s|\Z)", text or "", re.S | re.M | re.I)
    return m.group(1).strip() if m else ""


def _summary(spec):
    summary = _section(spec, "Summary")
    if summary:
        return summary
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", spec or "") if p.strip() and not p.strip().startswith("#")]
    return paragraphs[0] if paragraphs else ""


def body(paths, cfg, job_id):
    jd = paths.job(job_id)
    meta = jobs.read_json(jd / "job.json") or {}
    spec = jobs.read(jd / "spec.md") or ""
    tests = jobs.read(jd / "tests.md") or ""
    decision = jobs.read(jd / "decision.md") or ""
    human = jobs.read(jd / "human-approval.md")
    rounds = jobs.rounds_on_disk(jd)
    verdicts = schedule.latest_verdicts(paths, job_id, max(rounds, default=0))
    fallback = any(r.get("fallback") for n in rounds for r in jobs.reviews(paths.round_dir(job_id, n)).values())
    lines = [f"## {meta.get('title', job_id)}", ""]
    if meta.get("jira"):
        lines += [f"Jira: {meta['jira']}", ""]
    lines += ["### Summary", "", _summary(spec) or "_spec.md has no summary_", ""]
    lines += ["### Reviews", "", "| Reviewer | Verdict |", "|---|---|"]
    lines += [f"| {name} | {v} |" for name, v in sorted(verdicts.items())]
    lines += ["", f"Rounds: {len(rounds)}"]
    if fallback:
        lines += ["", "**Codex was unavailable; the codex review was written by the Claude fallback reviewer "
                      "(FALLBACK: claude).**"]
    exemptions = _section(tests, "Exemptions")
    lines += ["", "### Test exemptions", "", exemptions or "None."]
    lines += ["", "### Approver", "", jobs.first_line(decision) or "—"]
    least = _section(decision, "Least sure")
    lines += ["", "**Least sure is correct:**", "", least or "Nothing named."]
    if human:
        lines += ["", f"**Approved by a human over the factory's verdict:** {jobs.field(human, 'Was') or ''}"]
    lines += ["", f"_Built by the software factory, job `{job_id}`._"]
    return "\n".join(lines) + "\n"


def _push(paths, cfg, job_id):
    if not cfg.remote:
        raise FactoryError("project.md has no remote; pr mode needs one")
    branch = branch_name(job_id)
    gitutil.run(["push", "--force-with-lease", cfg.remote, f"{branch}:refs/heads/{branch}"], paths.root, timeout=600)
    return gitutil.rev(branch, paths.root)


def _gh_view(paths, ref):
    return json.loads(_cli(["gh", "pr", "view", str(ref), "--json", "number,url,state,headRefOid"], paths.root))


def _az_show(paths, number):
    return json.loads(_cli(["az", "repos", "pr", "show", "--id", str(number), "--output", "json"], paths.root))


def _az_url(data):
    repo = data.get("repository") or {}
    web = repo.get("webUrl")
    return f"{web}/pullrequest/{data['pullRequestId']}" if web else None


def open_or_update(paths, cfg, job_id):
    jd = paths.job(job_id)
    meta = jobs.meta(jd)
    existing = jobs.read_json(jd / "pr.json")
    head = _push(paths, cfg, job_id)
    text = body(paths, cfg, job_id)
    title = f"{meta['jira']}: {meta['title']}" if meta.get("jira") else meta["title"]
    branch = branch_name(job_id)
    tool = cfg.pr_tool
    auto = cfg.merge_mode == "auto-merge"
    record = dict(existing or {}, tool=tool, head=head, branch=branch, auto=auto, updated_at=jobs.now())
    if tool == "other":
        record.update(state="MANUAL", url=None)
        jobs.write_json(jd / "pr.json", record)
        return {**record, "manual": f"pushed {branch}; open the PR into {cfg.target_branch} by hand"}
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as handle:
        handle.write(text)
        body_file = handle.name
    if tool == "gh":
        if existing and existing.get("state") == "OPEN":
            _cli(["gh", "pr", "edit", str(existing["number"]), "--body-file", body_file], paths.root)
        else:
            _cli(["gh", "pr", "create", "--base", cfg.target_branch, "--head", branch,
                  "--title", title, "--body-file", body_file], paths.root)
        view = _gh_view(paths, branch)
        record.update(number=view["number"], url=view["url"], state=view["state"])
        if auto:
            _cli(["gh", "pr", "merge", str(view["number"]), "--auto", "--merge"], paths.root)
    else:
        description = text if len(text) <= 3900 else text[:3800] + "\n\n_(truncated; see the job folder)_\n"
        if existing and existing.get("state") == "OPEN":
            data = json.loads(_cli(["az", "repos", "pr", "update", "--id", str(existing["number"]),
                                    "--description", description, "--output", "json"], paths.root))
        else:
            data = json.loads(_cli(["az", "repos", "pr", "create", "--source-branch", branch,
                                    "--target-branch", cfg.target_branch, "--title", title,
                                    "--description", description, "--output", "json"], paths.root))
        record.update(number=data["pullRequestId"], url=_az_url(data), state=AZ_STATE.get(data.get("status"), "OPEN"))
        if auto:
            _cli(["az", "repos", "pr", "update", "--id", str(data["pullRequestId"]), "--auto-complete", "true",
                  "--output", "json"], paths.root)
    record["head"] = head
    jobs.write_json(jd / "pr.json", record)
    return record


def status(paths, cfg, job_id):
    jd = paths.job(job_id)
    record = jobs.read_json(jd / "pr.json")
    if not record:
        raise FactoryError(f"{job_id} has no PR")
    if record["tool"] == "gh":
        view = _gh_view(paths, record["number"])
        record["state"] = view["state"]
    elif record["tool"] == "az":
        data = _az_show(paths, record["number"])
        record["state"] = AZ_STATE.get(data.get("status"), "OPEN")
    else:
        if cfg.remote:
            gitutil.run(["fetch", cfg.remote], paths.root, timeout=300)
        if gitutil.is_ancestor(record["head"], cfg.base_ref, paths.root):
            record["state"] = "MERGED"
    record["checked_at"] = jobs.now()
    jobs.write_json(jd / "pr.json", record)
    return record


def comments(paths, cfg, job_id):
    record = jobs.read_json(paths.job(job_id) / "pr.json") or {}
    if record.get("tool") == "gh":
        data = json.loads(_cli(["gh", "pr", "view", str(record["number"]), "--json", "comments,reviews"], paths.root))
        items = [(c.get("author", {}).get("login", "?"), c.get("body", "")) for c in data.get("comments", [])]
        items += [(r.get("author", {}).get("login", "?"), r.get("body", "")) for r in data.get("reviews", []) if r.get("body")]
        try:
            inline = json.loads(_cli(["gh", "api", f"repos/{{owner}}/{{repo}}/pulls/{record['number']}/comments"], paths.root))
            items += [(c.get("user", {}).get("login", "?"), f"{c.get('path')}:{c.get('line') or c.get('original_line')} — {c.get('body', '')}")
                      for c in inline]
        except FactoryError:
            pass
    elif record.get("tool") == "az":
        pr_data = _az_show(paths, record["number"])
        repo = pr_data.get("repository") or {}
        threads = json.loads(_cli(["az", "devops", "invoke", "--area", "git", "--resource", "pullRequestThreads",
                                   "--route-parameters", f"project={repo.get('project', {}).get('name')}",
                                   f"repositoryId={repo.get('id')}", f"pullRequestId={record['number']}",
                                   "--api-version", "7.0", "--output", "json"], paths.root))
        items = []
        for t in threads.get("value", []):
            where = (t.get("threadContext") or {}).get("filePath") or ""
            for c in t.get("comments", []):
                if c.get("commentType") == "text" and not t.get("isDeleted"):
                    items.append((c.get("author", {}).get("displayName", "?"), f"{where} — {c.get('content', '')}".strip(" —")))
    else:
        return ""
    return "\n".join(f"- {who}: {text.strip()}" for who, text in items if text and text.strip())


def close(paths, cfg, job_id):
    jd = paths.job(job_id)
    record = jobs.read_json(jd / "pr.json") or {}
    if record.get("tool") == "gh" and record.get("number"):
        _cli(["gh", "pr", "close", str(record["number"])], paths.root)
    elif record.get("tool") == "az" and record.get("number"):
        _cli(["az", "repos", "pr", "update", "--id", str(record["number"]), "--status", "abandoned",
              "--output", "json"], paths.root)
    record["state"] = "CLOSED"
    record["closed_at"] = jobs.now()
    jobs.write_json(jd / "pr.json", record)
    return {"closed": record.get("url") or record.get("branch")}


def merge_local(paths, cfg, job_id):
    """local-merge: a --no-ff merge commit on the local target branch, built with plumbing so no
    checkout is touched. Refuses when the target branch is checked out anywhere, because moving
    the ref under a checkout would leave that checkout looking reverted."""
    jd = paths.job(job_id)
    branch = branch_name(job_id)
    target = cfg.target_branch
    holders = gitutil.worktrees_on_branch(target, paths.root)
    head = gitutil.rev(branch, paths.root)
    if holders:
        record = {"merged": False, "head": head, "at": jobs.now(),
                  "blocked": f"{target} is checked out in {holders[0]}; merge it there with `git merge --no-ff {branch}`"}
        jobs.write_json(jd / "merge.json", record)
        return record
    old = gitutil.rev(target, paths.root)
    if not old:
        raise FactoryError(f"local branch {target} does not exist")
    if not gitutil.is_ancestor(old, head, paths.root):
        raise FactoryError(f"{branch} is not on top of local {target}; rebase again", stale=True)
    meta = jobs.meta(jd)
    tree = gitutil.out(["rev-parse", f"{head}^{{tree}}"], paths.root)
    message = f"Merge {branch}: {meta['title']}"
    commit = gitutil.out(["commit-tree", tree, "-p", old, "-p", head, "-m", message], paths.root)
    gitutil.run(["update-ref", f"refs/heads/{target}", commit, old], paths.root)
    record = {"merged": True, "commit": commit, "target": target, "at": jobs.now()}
    jobs.write_json(jd / "merge.json", record)
    return record
