"""Reads the files in factory/jobs/<id>/. Each file's first line, or a "Key: value" line, is
the contract between the agent that writes it and the scripts that read it."""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .errors import FactoryError

JOB_ID = re.compile(r"^(?:[A-Z][A-Z0-9]+-\d+|\d{3}-[a-z0-9]+(?:-[a-z0-9]+)*)$")
ISSUE_KEY = re.compile(r"^[A-Z][A-Z0-9]+-\d+$")
_SECTION = re.compile(r"^## Round (\d+)(?: \(attempt (\d+)\))?\s*$", re.M)


def now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def check_id(job_id):
    if not JOB_ID.match(job_id or ""):
        raise FactoryError(f"invalid job id {job_id!r}: expected an issue key (OPT-123) or NNN-slug")
    return job_id


def read(path):
    path = Path(path)
    return path.read_text(encoding="utf-8") if path.exists() else None


def write_atomic(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def read_json(path):
    text = read(path)
    if text is None:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise FactoryError(f"{path} is not valid JSON: {e}")


def write_json(path, data):
    write_atomic(path, json.dumps(data, indent=2, sort_keys=True) + "\n")


def first_line(text):
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def field(text, key):
    match = re.search(rf"^\s*{re.escape(key)}:\s*(.+?)\s*$", text or "", re.M | re.I)
    return match.group(1) if match else None


def section(text, heading):
    m = re.search(rf"^##\s+{re.escape(heading)}\s*$(.*?)(?=^##\s|\Z)", text or "", re.S | re.M | re.I)
    return m.group(1).strip() if m else ""


_BLOCKING = re.compile(r"^\s*[-*]\s*(?:🔴\s*)?\**BLOCKING\b", re.M)


def verdict(text):
    """None until the review is finished (line 1 is a VERDICT line). Then CHANGES exactly when the
    Findings section holds a BLOCKING finding — the verdict word the reviewer wrote does not decide."""
    if not re.match(r"^VERDICT:\s*(PASS|CHANGES)\b", first_line(text), re.I):
        return None
    return "CHANGES" if _BLOCKING.search(section(text, "Findings")) else "PASS"


def decision(text):
    match = re.match(r"^\**(CANCELLED)\b", first_line(text), re.I)
    return match.group(1).upper() if match else None


def ruling(text):
    match = re.match(r"^RULING:\s*(CONTINUE|NEEDS-HUMAN)\b", first_line(text), re.I)
    return match.group(1).upper() if match else None


def build_sections(text):
    """build.md is append-only: one "## Round N" section per builder run, "(attempt K)" for reruns
    within a round. Each section ends with "Status: done" or "Status: objection"."""
    sections = []
    matches = list(_SECTION.finditer(text or ""))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.end():end]
        status = field(body, "Status")
        status = status.lower().split()[0] if status else None
        kind = field(body, "Objection")
        sections.append({
            "round": int(m.group(1)),
            "attempt": int(m.group(2) or 1),
            "status": status if status in ("done", "objection") else None,
            "objection": (kind or "").lower().split()[0] if kind else None,
        })
    return sections


def rounds_on_disk(job_dir):
    found = []
    for d in Path(job_dir).glob("round-*"):
        if d.is_dir() and d.name[6:].isdigit():
            found.append(int(d.name[6:]))
    return sorted(found)


def reviews(round_dir):
    result = {}
    for f in Path(round_dir).glob("review-*.md"):
        result[f.stem[len("review-"):]] = {
            "verdict": verdict(f.read_text(encoding="utf-8")),
            "fallback": "FALLBACK: claude" in f.read_text(encoding="utf-8"),
        }
    return result


def scheduled(round_dir):
    text = read(Path(round_dir) / "scheduled.md")
    if text is None:
        return None
    names = field(text, "Reviewers") or ""
    previous = field(text, "Previous-Head")
    return {
        "reviewers": [n.strip() for n in names.split(",") if n.strip()],
        "head": field(text, "Head"),
        "base": field(text, "Base"),
        "previous": previous if previous and previous != "-" else None,
    }


def reworks(job_dir):
    result = []
    for f in sorted(Path(job_dir).glob("rework-*.md"), key=lambda p: int(p.stem.split("-")[1])):
        text = f.read_text(encoding="utf-8")
        start = field(text, "Start-Round")
        result.append({"file": f.name, "number": int(f.stem.split("-")[1]),
                       "start_round": int(start) if start and start.isdigit() else None})
    return result


def meta(job_dir):
    data = read_json(Path(job_dir) / "job.json")
    if data is None:
        raise FactoryError(f"{job_dir}/job.json is missing; the job was not queued with `queue add`")
    return data
