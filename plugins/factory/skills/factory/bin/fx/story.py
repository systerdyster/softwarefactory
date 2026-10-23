"""What the developer reads first about a job: `story` — what has happened, one short line per round
or event — and `decision` — what they must decide now, when the job waits for them. Both are read
from the job folder; nothing here is judged by an agent."""

import re

from . import findings, jobs

ITEM_LIMIT = 3
SHORT = 110
CONFIDENCE = re.compile(r"^Confidence:\s*(\d+)\s*/\s*10", re.MULTILINE)


def _short(text):
    text = " ".join((text or "").split())
    return text if len(text) <= SHORT else text[:SHORT].rsplit(" ", 1)[0] + "…"


def _first_line(text):
    return next((l.strip() for l in (text or "").splitlines() if l.strip() and not l.startswith("#")), "")


def _items(text, heading):
    return [l.split(None, 1)[1].strip() for l in jobs.section(text, heading).splitlines()
            if jobs._ITEM.match(l) and len(l.split(None, 1)) > 1]


def _review_part(rd):
    parts = []
    for name, r in sorted(jobs.reviews(rd).items()):
        if r["verdict"] == "CHANGES":
            blocking = [i for i in jobs.finding_items(jobs.read(rd / f"review-{name}.md")) if jobs.is_blocking(i)]
            parts.append(f"{name} CHANGES ({len(blocking)} blocking)")
        elif r["verdict"]:
            parts.append(f"{name} {r['verdict']}")
    return "review: " + ", ".join(parts) if parts else ""


def story(paths, job_id, s):
    jd = paths.job(job_id)
    if not jd.exists():
        return []
    lines = []
    sections = jobs.build_sections(jobs.read(jd / "build.md"))
    reworks = {r["start_round"]: r for r in jobs.reworks(jd)}
    for n in sorted({sec["round"] for sec in sections} | set(jobs.rounds_on_disk(jd))):
        rd = paths.round_dir(job_id, n)
        if n in reworks:
            note = jobs.section(jobs.read(jd / reworks[n]["file"]), "Note")
            lines.append(f"Rework {reworks[n]['number']}: {_short(_first_line(note))}")
        attempts = [sec for sec in sections if sec["round"] == n]
        parts = []
        if attempts:
            red = []
            for f in sorted(rd.glob("verify-[0-9]*.json")):
                v = jobs.read_json(f) or {}
                if not v.get("green"):
                    red.append(v.get("failed") or "checks")
            parts.append("build" + (f": {len(attempts)} attempts" if len(attempts) > 1 else "")
                         + (f" ({', '.join(red)} red)" if red else ""))
        if n == 1 and (rd / "tidy.md").exists():
            tidy = jobs.read(rd / "tidy.md")
            discarded = jobs.field(tidy, "Discarded")
            changed = len(_items(tidy, "Removed")) + len(_items(tidy, "Simplified"))
            parts.append(f"tidy discarded ({discarded})" if discarded else f"tidy: {changed} changes")
        for f in sorted(rd.glob("ruling-*.md")):
            text = jobs.read(f)
            a = f.stem.rsplit("-", 1)[1]
            if jobs.ruling(text) == "CONTINUE":
                parts.append("objection ruled within the plan")
            else:
                answer = jobs.field(jobs.read(rd / f"answer-{a}.md"), "Answer")
                later = next((r for start, r in sorted(reworks.items()) if start and start > n), None)
                parts.append(f"objection → you decided: {_short(answer)}" if answer
                             else f"objection → you decided in rework {later['number']}" if later
                             else "objection → waits for your decision")
        review = _review_part(rd)
        if review:
            parts.append(review)
        if parts:
            lines.append(f"Round {n} · " + " · ".join(parts))
    rebases = (jobs.read_json(jd / "rebase.json") or {}).get("history", [])
    if rebases:
        last = rebases[-1]
        lines.append(f"Rebased on the target {len(rebases)}× · last {last.get('result')}")
    human = jobs.read(jd / "human-approval.md")
    if human:
        lines.append(f"Approved by {jobs.field(human, 'Approved-By') or 'a human'}")
    pr = jobs.read_json(jd / "pr.json")
    if pr and pr.get("url"):
        lines.append(f"PR {pr.get('state', '').lower()}: {pr['url']}")
    return lines


def confidence(paths, job_id):
    """The main reviewer's `Confidence: n/10` from its latest round that has one; None without."""
    for n in reversed(jobs.rounds_on_disk(paths.job(job_id))):
        found = CONFIDENCE.search(jobs.read(paths.round_dir(job_id, n) / "review-code.md") or "")
        if found and 1 <= int(found.group(1)) <= 10:
            return int(found.group(1))
    return None


def decision(paths, job_id, s):
    """None unless the job waits for the developer; then what they decide and how they answer."""
    if s.get("stage") != "needs-human":
        return None
    nxt = s.get("next") or {}
    jd = paths.job(job_id)
    if nxt.get("question"):
        rd = paths.round_dir(job_id, nxt["round"])
        q = jobs.question(jobs.read(rd / f"ruling-{nxt['attempt']}.md"))
        return {"kind": "question", **q}
    if nxt.get("ready"):
        rounds = jobs.rounds_on_disk(jd)
        reviews = [jobs.read(paths.round_dir(job_id, n) / "review-code.md") for n in reversed(rounds)]
        least = next((_items(t, "Least sure") for t in reviews if _items(t, "Least sure")), [])
        exemptions = [e for e in _items(jobs.read(jd / "tests.md"), "Exemptions") if e.lower() != "none"]
        return {"kind": "approve", "summary": jobs.section(jobs.read(jd / "spec.md"), "Summary"),
                "checks": s.get("checks"), "least_sure": least[:ITEM_LIMIT], "exemptions": exemptions,
                "findings": [{"reviewer": name, "text": item} for name, item in findings.remaining(paths, job_id)],
                "unrendered": [{"reviewer": name, "why": why} for name, why in findings.unrendered(paths, job_id)]}
    return {"kind": "stopped", "reason": s.get("reason")}
