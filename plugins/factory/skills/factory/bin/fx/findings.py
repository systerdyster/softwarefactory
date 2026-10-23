"""`findings`: one list of what a round's reviews ask for, so the builder answers each issue once.

Every bullet or numbered item under a review's "## Findings" is a finding: BLOCKING ones must be resolved, everything
else is a note. Findings from different reviewers that name the same `path:line` are one item, with
each reviewer's words kept side by side, so a duplicate is fixed once and a contradiction is visible.
The list is derived from the review files and rewritten whenever it is asked for."""

import re

from . import jobs

_LOCATION = re.compile(r"`([^`\s]+\.[A-Za-z0-9]+:\d[\d,\-–]*)`")


def parse(text):
    return [{"blocking": jobs.is_blocking(item), "text": item, "locations": set(_LOCATION.findall(item))}
            for item in jobs.finding_items(text)]


def merge(by_reviewer):
    """by_reviewer: {name: [finding, …]}. Returns groups of findings that share a location."""
    groups = []
    for name, found in by_reviewer.items():
        for f in found:
            entry = {**f, "reviewer": name}
            hits = [g for g in groups if f["locations"] and g["locations"] & f["locations"]]
            merged = {"locations": set(f["locations"]), "entries": [entry]}
            for g in hits:
                merged["locations"] |= g["locations"]
                merged["entries"] = g["entries"] + merged["entries"]
                groups.remove(g)
            groups.append(merged)
    for g in groups:
        g["blocking"] = any(e["blocking"] for e in g["entries"])
        g["reviewers"] = sorted({e["reviewer"] for e in g["entries"]})
    return groups


def _render(group):
    if len(group["entries"]) == 1:
        e = group["entries"][0]
        return [f"**{e['reviewer']}** — {e['text']}"]
    lines = [f"**{', '.join(group['reviewers'])}** — one item, named by {len(group['entries'])} findings:"]
    lines += [f"   - {e['reviewer']}: {e['text']}" for e in group["entries"]]
    return lines


def write(paths, job_id, round_number):
    rd = paths.round_dir(job_id, round_number)
    by_reviewer = {}
    for f in sorted(rd.glob("review-*.md")):
        by_reviewer[f.stem[len("review-"):]] = parse(f.read_text(encoding="utf-8"))
    groups = merge(by_reviewer)
    blocking = [g for g in groups if g["blocking"]]
    notes = [g for g in groups if not g["blocking"]]
    lines = [f"# Round {round_number} — findings", "",
             f"Merged from: {', '.join(sorted(by_reviewer)) or 'no reviews'}. Findings that name the same "
             "`path:line` are one item.", "",
             "## Blocking — fix each, or answer it in build.md with the evidence that it is wrong", ""]
    for i, g in enumerate(blocking, 1):
        first, *rest = _render(g)
        lines += [f"{i}. {first}", *rest]
    if not blocking:
        lines.append("None.")
    lines += ["", "## Notes — fix where cheap; the rest goes to the human with the PR", ""]
    for g in notes:
        first, *rest = _render(g)
        lines += [f"- {first}", *rest]
    if not notes:
        lines.append("None.")
    out = rd / "findings.md"
    jobs.write_atomic(out, "\n".join(lines) + "\n")
    return {"file": out, "blocking": len(blocking), "notes": len(notes)}


def remaining(paths, job_id):
    """What the reviewers still say when the job reaches the developer: each reviewer's findings from
    its latest review (a fix round's review lists again whatever it holds as not resolved), as
    [(reviewer, finding)]. Notes the builder fixed are gone because their reviewer no longer lists them."""
    latest = {}
    for n in jobs.rounds_on_disk(paths.job(job_id)):
        for f in sorted(paths.round_dir(job_id, n).glob("review-*.md")):
            latest[f.stem[len("review-"):]] = jobs.finding_items(f.read_text(encoding="utf-8"))
    return [(name, item) for name, items in sorted(latest.items()) for item in items]
