"""`schedule <job-id>`: decides which reviewers run in the current round and writes
round-N/scheduled.md.

Round 1 of a cycle: the main reviewer (code), plus each specialist with a concrete reason to look —
named in plan.md's `Specialists:` line, or one of its path patterns in project.md matches the change.
Later rounds check the fixes: only the reviewers whose latest verdict is CHANGES."""

import fnmatch
import re

from . import gitutil, jobs
from .config import REQUIRED_REVIEWERS
from .errors import FactoryError
from .paths import branch_name


def glob_to_regex(pattern):
    i, out = 0, ""
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif pattern[i] == "*":
            out += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return re.compile(out + r"\Z")


def matches(pattern, path, diff_text=""):
    if pattern.startswith("re:"):
        return re.search(pattern[3:], diff_text, re.M) is not None
    if "/" not in pattern:
        return fnmatch.fnmatchcase(path.rsplit("/", 1)[-1], pattern)
    return glob_to_regex(pattern).match(path) is not None


def latest_verdicts(paths, job_id, up_to_round, from_round=1):
    result = {}
    for n in jobs.rounds_on_disk(paths.job(job_id)):
        if from_round <= n <= up_to_round:
            for name, review in jobs.reviews(paths.round_dir(job_id, n)).items():
                if review["verdict"]:
                    result[name] = review["verdict"]
    return result


def previous_head(paths, job_id, before_round, from_round):
    for n in sorted(jobs.rounds_on_disk(paths.job(job_id)), reverse=True):
        if from_round <= n < before_round:
            s = jobs.scheduled(paths.round_dir(job_id, n))
            if s and s["head"]:
                return s["head"]
    return None


def _pattern_hit(patterns, changed_files, diff_text):
    hit = next((f"{p} matches {f}" for p in patterns for f in changed_files
                if not p.startswith("re:") and matches(p, f)), None)
    return hit or next((f"{p} matches the diff" for p in patterns
                        if p.startswith("re:") and matches(p, "", diff_text)), None)


def decide(available, first_round, changed_files, diff_text, verdicts, patterns, named=()):
    if not first_round:
        chosen = {name: "said CHANGES" for name in available if verdicts.get(name) == "CHANGES"}
        return chosen or {name: "nothing open; re-confirming" for name in REQUIRED_REVIEWERS}
    chosen = {}
    for name in available:
        if name in REQUIRED_REVIEWERS:
            chosen[name] = "main review"
        elif name in named:
            chosen[name] = "named in plan.md"
        else:
            hit = _pattern_hit(patterns.get(name, []), changed_files, diff_text)
            if hit:
                chosen[name] = hit
    return chosen


def named_specialists(paths, job_id):
    value = jobs.field(jobs.read(paths.job(job_id) / "plan.md"), "Specialists") or ""
    return [n.strip().lower() for n in re.split(r"[,\s]+", value) if n.strip() and n.strip().lower() != "none"]


def schedule(paths, cfg, job_id, round_number, cycle_start):
    branch = branch_name(paths, job_id)
    head = gitutil.rev(branch, paths.root)
    if not head:
        raise FactoryError(f"branch {branch} does not exist")
    base = gitutil.merge_base(cfg.base_ref, head, paths.root)
    first = round_number == cycle_start
    prev = None if first else previous_head(paths, job_id, round_number, cycle_start)
    if not first and not prev:
        first = True
    since = base if first else prev
    changed = gitutil.changed_files(since, head, paths.root)
    diff_text = gitutil.out(["diff", since, head], paths.root) if changed and first else ""
    verdicts = latest_verdicts(paths, job_id, round_number - 1, cycle_start)
    patterns = {name: cfg.path_patterns(name) for name in cfg.enabled_reviewers}
    chosen = decide(cfg.enabled_reviewers, first, changed, diff_text, verdicts, patterns,
                    named_specialists(paths, job_id))
    lines = [
        f"# Round {round_number} — scheduled reviewers",
        "",
        f"Head: {head}",
        f"Base: {base}",
        f"Previous-Head: {prev or '-'}",
        f"Reviewers: {', '.join(chosen)}",
        "",
        *[f"- {name}: {why}" for name, why in chosen.items()],
        "",
        f"Changed since previous round: {len(changed)} file(s)" if prev else "First round of this cycle.",
    ]
    jobs.write_atomic(paths.round_dir(job_id, round_number) / "scheduled.md", "\n".join(lines) + "\n")
    return {"round": round_number, "reviewers": list(chosen), "reasons": chosen, "head": head, "base": base}
