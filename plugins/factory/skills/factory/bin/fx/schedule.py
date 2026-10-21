"""`schedule <job-id>`: decides which reviewers run in the current round and writes
round-N/scheduled.md.

Round 1 of a cycle: every enabled reviewer. Later rounds: security, code and codex (if enabled)
whenever code changed; every reviewer whose latest verdict is CHANGES; and every reviewer whose
path patterns in project.md match a file changed since the previous round."""

import fnmatch
import re

from . import gitutil, jobs
from .config import ALWAYS_ON_CODE_CHANGE
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


def decide(enabled, first_round, changed_files, diff_text, verdicts, patterns):
    if first_round:
        return {name: "first round" for name in enabled}
    chosen = {}
    if changed_files:
        for name in ALWAYS_ON_CODE_CHANGE:
            if name in enabled:
                chosen[name] = "code changed"
    for name in enabled:
        if name not in chosen and verdicts.get(name) == "CHANGES":
            chosen[name] = "said CHANGES"
        elif name not in chosen and name not in verdicts:
            chosen[name] = "has not reviewed this cycle"
    for name in enabled:
        if name in chosen:
            continue
        hit = next((f"{p} matches {f}" for p in patterns.get(name, []) for f in changed_files
                    if not p.startswith("re:") and matches(p, f)), None)
        if not hit:
            hit = next((f"{p} matches the diff" for p in patterns.get(name, [])
                        if p.startswith("re:") and matches(p, "", diff_text)), None)
        if hit:
            chosen[name] = hit
    return {name: chosen[name] for name in enabled if name in chosen}


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
    changed, diff_text = [], ""
    if prev:
        changed = gitutil.changed_files(prev, head, paths.root)
        diff_text = gitutil.out(["diff", prev, head], paths.root) if changed else ""
    verdicts = latest_verdicts(paths, job_id, round_number - 1, cycle_start)
    patterns = {name: cfg.path_patterns(name) for name in cfg.enabled_reviewers}
    chosen = decide(cfg.enabled_reviewers, first, changed, diff_text, verdicts, patterns)
    if not chosen:
        chosen = {name: "nothing changed; re-confirming open verdicts" for name in ["code"]}
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
