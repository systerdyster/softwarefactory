"""`pr open|status|comments|close`: the hand-back through a pull request, with gh (GitHub),
az (Azure DevOps) or `other` (push only; a human opens the PR). Only the orchestrator runs this,
and it is the only place the factory pushes."""

import json
import os
import re
import subprocess
import tempfile

from . import findings, gitutil, jobs, schedule
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
    """The PR description: what changed and why, kept as short as a commit body — Azure DevOps and a
    GitHub merge put it into the merge commit. The review facts go in a comment (`details`)."""
    jd = paths.job(job_id)
    meta = jobs.read_json(jd / "job.json") or {}
    lines = [_summary(jobs.read(jd / "spec.md") or "") or "_spec.md has no summary_"]
    if meta.get("jira") and cfg.jira_link in ("body", "both"):
        lines += ["", jira_line(cfg, meta["jira"])]
    return "\n".join(lines) + "\n"


def details(paths, cfg, job_id):
    """The review facts a PR's reviewers want and the git history doesn't: posted as one PR comment."""
    jd = paths.job(job_id)
    tests = jobs.read(jd / "tests.md") or ""
    human = jobs.read(jd / "human-approval.md")
    rounds = jobs.rounds_on_disk(jd)
    main_reviews = [jobs.read(paths.round_dir(job_id, n) / "review-code.md") for n in rounds]
    least = next((_section(t, "Least sure") for t in reversed(main_reviews) if _section(t, "Least sure")), "")
    verdicts = schedule.latest_verdicts(paths, job_id, max(rounds, default=0))
    fallback = any(r.get("fallback") for n in rounds for r in jobs.reviews(paths.round_dir(job_id, n)).values())
    lines = [f"## Software factory — job `{job_id}`", ""]
    lines += ["### Reviews", "", "| Reviewer | Verdict |", "|---|---|"]
    lines += [f"| {name} | {v} |" for name, v in sorted(verdicts.items())]
    lines += ["", f"Rounds: {len(rounds)}"]
    if fallback:
        lines += ["", "**Codex was unavailable; the codex review was written by the Claude fallback reviewer "
                      "(FALLBACK: claude).**"]
    exemptions = _section(tests, "Exemptions")
    lines += ["", "### Test exemptions", "", exemptions or "None."]
    tidy = jobs.read(paths.round_dir(job_id, 1) / "tidy.md")
    if tidy:
        discarded = jobs.field(tidy, "Discarded")
        done = "\n".join(s for s in (_section(tidy, "Removed"), _section(tidy, "Simplified")) if s)
        lines += ["", "### Tidy pass", "", f"Discarded: {discarded}" if discarded else (done or "Nothing changed.")]
    lines += ["", "### Least sure is correct", "", least or "Nothing named."]
    note = (jobs.read(jd / "test-note.md") or "").strip()
    if note:
        lines += ["", "### Test note", "", note]
    unseen = findings.unrendered(paths, job_id)
    if unseen:
        lines += ["", "### Not looked at in a browser", "",
                  "These reviewers' verdicts rest on reading the code; check the change by eye:", ""]
        lines += [f"- **{name}** — {why}" for name, why in unseen]
    left = findings.remaining(paths, job_id)
    lines += ["", "### Findings the reviewers left", ""]
    lines += [f"- **{name}** — {item}" for name, item in left] or ["None."]
    if human:
        lines += ["", f"Approved by {jobs.field(human, 'Approved-By') or 'a human'} at "
                      f"`{(jobs.field(human, 'Head') or '')[:12]}`."]
    return "\n".join(lines) + "\n"


def _post_details(paths, record, text):
    """Posts the details comment, or rewrites the one posted before, so the PR carries one."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as handle:
        if record["tool"] == "gh":
            json.dump({"body": text}, handle)
        else:
            json.dump({"comments": [{"parentCommentId": 0, "content": text, "commentType": 1}], "status": "closed"}
                      if not record.get("details_thread") else {"content": text}, handle)
        payload = handle.name
    try:
        _send_details(paths, record, payload)
    finally:
        os.unlink(payload)


def _send_details(paths, record, payload):
    if record["tool"] == "gh":
        if record.get("details_comment"):
            _cli(["gh", "api", "-X", "PATCH", f"repos/{{owner}}/{{repo}}/issues/comments/{record['details_comment']}",
                  "--input", payload], paths.root)
        else:
            data = json.loads(_cli(["gh", "api", "-X", "POST", f"repos/{{owner}}/{{repo}}/issues/{record['number']}/comments",
                                    "--input", payload], paths.root))
            record["details_comment"] = data["id"]
        return
    repo = (_az_show(paths, record["number"]).get("repository") or {})
    route = [f"project={repo.get('project', {}).get('name')}", f"repositoryId={repo.get('id')}",
             f"pullRequestId={record['number']}"]
    if record.get("details_thread"):
        _cli(["az", "devops", "invoke", "--area", "git", "--resource", "pullRequestThreadComments",
              "--route-parameters", *route, f"threadId={record['details_thread']}", "commentId=1",
              "--http-method", "PATCH", "--in-file", payload, "--api-version", "7.0", "--output", "json"], paths.root)
    else:
        data = json.loads(_cli(["az", "devops", "invoke", "--area", "git", "--resource", "pullRequestThreads",
                                "--route-parameters", *route, "--http-method", "POST", "--in-file", payload,
                                "--api-version", "7.0", "--output", "json"], paths.root))
        record["details_thread"] = data["id"]


def _push(paths, cfg, job_id):
    if not cfg.remote:
        raise FactoryError("project.md has no remote; pr mode needs one")
    branch = branch_name(paths, job_id)
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


def jira_line(cfg, key):
    return f"Jira: [{key}]({cfg.jira_url.rstrip('/')}/{key})" if cfg.jira_url else f"Jira: {key}"


def final_body(paths, cfg, job_id):
    """pr-body.md, written by the orchestrator in the project's language and PR template from the
    facts in `pr body`, when it exists; otherwise the facts themselves."""
    jd = paths.job(job_id)
    written = jobs.read(jd / "pr-body.md")
    if not written:
        return body(paths, cfg, job_id)
    if jobs.attribution(written):
        raise FactoryError("pr-body.md carries AI attribution (Co-Authored-By / Generated with); remove it — the "
                           "description becomes the merge commit's message")
    meta = jobs.meta(jd)
    if meta.get("jira") and cfg.jira_link in ("body", "both") and meta["jira"] not in written:
        written = written.rstrip("\n") + "\n\n" + jira_line(cfg, meta["jira"]) + "\n"
    return written


def title_for(cfg, meta):
    if meta.get("jira") and cfg.jira_link in ("title", "both"):
        return f"{meta['jira']}: {meta['title']}"
    return meta["title"]


def create_flags(cfg, meta, tool):
    reviewers = cfg.pr_reviewers
    required = cfg.pr_required_reviewers
    if tool == "gh":
        flags = ["--draft"] if cfg.pr_draft else []
        everyone = reviewers + required
        return flags + (["--reviewer", ",".join(everyone)] if everyone else [])
    flags = ["--draft", "true"] if cfg.pr_draft else []
    if reviewers:
        flags += ["--reviewers", *reviewers]
    if required:
        flags += ["--required-reviewers", *required]
    if cfg.link_work_items and meta.get("work_item"):
        flags += ["--work-items", str(meta["work_item"])]
    return flags


def open_or_update(paths, cfg, job_id):
    jd = paths.job(job_id)
    meta = jobs.meta(jd)
    existing = jobs.read_json(jd / "pr.json")
    text = final_body(paths, cfg, job_id)
    head = _push(paths, cfg, job_id)
    title = title_for(cfg, meta)
    branch = branch_name(paths, job_id)
    tool = cfg.pr_tool
    auto = cfg.merge_mode == "auto-merge" or (jobs.field(jobs.read(jd / "human-approval.md"), "Auto-Complete") == "yes")
    record = dict(existing or {}, tool=tool, head=head, branch=branch, auto=auto, updated_at=jobs.now())
    if tool == "other":
        record.update(state="MANUAL", url=None)
        jobs.write_json(jd / "pr.json", record)
        return {**record, "manual": f"pushed {branch}; open the PR into {cfg.target_branch} by hand"}
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as handle:
        handle.write(text)
        body_file = handle.name
    try:
        _open_with(paths, cfg, record, existing, meta, branch, title, text, body_file, auto)
    finally:
        os.unlink(body_file)
    try:
        _post_details(paths, record, details(paths, cfg, job_id))
        record.pop("details_error", None)
    except FactoryError as e:
        record["details_error"] = str(e)
    record["head"] = head
    jobs.write_json(jd / "pr.json", record)
    return record


def _open_with(paths, cfg, record, existing, meta, branch, title, text, body_file, auto):
    tool = cfg.pr_tool
    if tool == "gh":
        if existing and existing.get("state") == "OPEN":
            _cli(["gh", "pr", "edit", str(existing["number"]), "--body-file", body_file], paths.root)
        else:
            _cli(["gh", "pr", "create", "--base", cfg.target_branch, "--head", branch,
                  "--title", title, "--body-file", body_file, *create_flags(cfg, meta, "gh")], paths.root)
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
                                    "--description", description, *create_flags(cfg, meta, "az"),
                                    "--output", "json"], paths.root))
        record.update(number=data["pullRequestId"], url=_az_url(data), state=AZ_STATE.get(data.get("status"), "OPEN"))
        if auto:
            _cli(["az", "repos", "pr", "update", "--id", str(data["pullRequestId"]), "--auto-complete", "true",
                  "--output", "json"], paths.root)


def status(paths, cfg, job_id):
    jd = paths.job(job_id)
    record = jobs.read_json(jd / "pr.json")
    if not record:
        raise FactoryError(f"{job_id} has no PR")
    if cfg.remote:
        gitutil.run(["fetch", cfg.remote], paths.root, timeout=300)
    if record["tool"] == "gh":
        view = _gh_view(paths, record["number"])
        record["state"] = view["state"]
    elif record["tool"] == "az":
        data = _az_show(paths, record["number"])
        record["state"] = AZ_STATE.get(data.get("status"), "OPEN")
    else:
        if gitutil.is_ancestor(record["head"], cfg.base_ref, paths.root):
            record["state"] = "MERGED"
    record["checked_at"] = jobs.now()
    jobs.write_json(jd / "pr.json", record)
    return record


def check_merged(paths, cfg, job_id):
    """After the target moved: is this open PR merged? Returns "merged", "suspected" or None.
    The PR tool decides for gh and az; git only says when to ask. Without a PR tool, an ancestor
    is proof, while the branch's content being in the target is only a suspicion: the same change
    can arrive another way, and the branch is deleted once the job finishes. Reads the refs as
    they are; the caller fetches."""
    jd = paths.job(job_id)
    record = jobs.read_json(jd / "pr.json")
    if not record or record.get("state") not in ("OPEN", "MANUAL") or not record.get("head"):
        return None
    ancestor = gitutil.is_ancestor(record["head"], cfg.base_ref, paths.root)
    if record["tool"] in ("gh", "az"):
        if not ancestor and not gitutil.has_content_in(record["head"], cfg.base_ref, paths.root):
            return None
        try:
            return "merged" if status(paths, cfg, job_id)["state"] == "MERGED" else None
        except FactoryError:
            return None
    if ancestor:
        return "merged" if status(paths, cfg, job_id)["state"] == "MERGED" else None
    if record.get("merge_suspected") or not gitutil.has_content_in(record["head"], cfg.base_ref, paths.root):
        return None
    record["merge_suspected"] = jobs.now()
    jobs.write_json(jd / "pr.json", record)
    return "suspected"


def confirm_merged(paths, cfg, job_id):
    """The developer said the PR was merged (the one case git can't settle): mark it so."""
    jd = paths.job(job_id)
    record = jobs.read_json(jd / "pr.json")
    if not record or record.get("state") not in ("OPEN", "MANUAL"):
        raise FactoryError(f"{job_id} has no open PR to confirm")
    record.update(state="MERGED", merge_confirmed_by="human", checked_at=jobs.now())
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
    branch = branch_name(paths, job_id)
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
