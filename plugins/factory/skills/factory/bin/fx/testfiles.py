"""`tests record|check|restore`: the test files test-writer owns, pinned by content hash.

tests.md carries a table of `| file | hash |` rows. test-writer lists the files; `record` fills in
the hashes from the committed blobs on the job branch, so a hash is never typed by an agent. Hashes
are on content, not commits, so they survive a rebase."""

import re
import subprocess
from pathlib import Path

from . import gitutil
from .errors import FactoryError
from .paths import branch_name

_ROW = re.compile(r"^\|\s*`?([^`|]+?)`?\s*\|\s*([0-9a-f]{40,64}|[^|]*?)\s*\|\s*$")
_HEAD = re.compile(r"^Recorded-Head:.*$", re.M)


def tests_md(paths, job_id):
    return paths.job(job_id) / "tests.md"


def parse(text):
    rows = []
    for line in (text or "").splitlines():
        m = _ROW.match(line.strip())
        if not m:
            continue
        path, value = m.group(1).strip(), m.group(2).strip()
        if path.lower() in ("file", "path") or set(path) <= set("-: "):
            continue
        rows.append({"path": path, "hash": value if re.fullmatch(r"[0-9a-f]{40,64}", value) else None})
    return rows


def listed(paths, job_id):
    text = tests_md(paths, job_id).read_text(encoding="utf-8") if tests_md(paths, job_id).exists() else ""
    return parse(text)


def is_recorded(paths, job_id):
    f = tests_md(paths, job_id)
    if not f.exists():
        return False
    text = f.read_text(encoding="utf-8")
    rows = parse(text)
    return bool(rows) and all(r["hash"] for r in rows) and bool(_HEAD.search(text))


def recorded_head(paths, job_id):
    f = tests_md(paths, job_id)
    m = _HEAD.search(f.read_text(encoding="utf-8")) if f.exists() else None
    return m.group(0).split(":", 1)[1].strip() if m else None


def _workspace_on(paths, job_id):
    ws = paths.workspace
    return gitutil.is_worktree(ws) and gitutil.current_branch(ws) == branch_name(paths, job_id)


def record(paths, job_id):
    f = tests_md(paths, job_id)
    if not f.exists():
        raise FactoryError(f"{paths.relative(f)} is missing; test-writer has not run")
    text = f.read_text(encoding="utf-8")
    rows = parse(text)
    if not rows:
        raise FactoryError("tests.md lists no test files (expected a `| file | hash |` table)")
    branch = branch_name(paths, job_id)
    head = gitutil.rev(branch, paths.root)
    if not head:
        raise FactoryError(f"branch {branch} does not exist")
    on_branch = _workspace_on(paths, job_id)
    hashes, problems = {}, []
    for row in rows:
        committed = gitutil.blob(branch, row["path"], paths.root)
        if not committed:
            problems.append(f"{row['path']} is not committed on {branch}")
            continue
        if on_branch and gitutil.hash_file(row["path"], paths.workspace) != committed:
            problems.append(f"{row['path']} has uncommitted changes in the workspace")
            continue
        hashes[row["path"]] = committed
    if problems:
        reason = "; ".join(problems)
        f.write_text(re.sub(r"^Status:.*$", f"Status: record-failed — {reason}", text, flags=re.M), encoding="utf-8")
        raise FactoryError("cannot record test hashes: " + reason, problems=problems)
    out_lines = []
    for line in text.splitlines():
        m = _ROW.match(line.strip())
        if m and m.group(1).strip() in hashes:
            out_lines.append(f"| `{m.group(1).strip()}` | {hashes[m.group(1).strip()]} |")
        else:
            out_lines.append(line)
    new_text = "\n".join(out_lines).rstrip("\n") + "\n"
    if _HEAD.search(new_text):
        new_text = _HEAD.sub(f"Recorded-Head: {head}", new_text)
    else:
        new_text += f"\nRecorded-Head: {head}\n"
    f.write_text(new_text, encoding="utf-8")
    return {"recorded": len(hashes), "head": head, "files": sorted(hashes)}


def check(paths, job_id):
    if not is_recorded(paths, job_id):
        raise FactoryError("test hashes are not recorded yet; run `tests record` after test-writer commits")
    branch = branch_name(paths, job_id)
    on_branch = _workspace_on(paths, job_id)
    mismatches = []
    for row in listed(paths, job_id):
        committed = gitutil.blob(branch, row["path"], paths.root)
        working = gitutil.hash_file(row["path"], paths.workspace) if on_branch else committed
        if committed != row["hash"] or working != row["hash"]:
            mismatches.append({"path": row["path"], "expected": row["hash"],
                               "committed": committed, "working": working})
    return {"ok": not mismatches, "mismatches": mismatches}


def restore(paths, job_id):
    if not _workspace_on(paths, job_id):
        raise FactoryError(f"the workspace is not on {branch_name(paths, job_id)}; switch first")
    if gitutil.rebase_in_progress(paths.workspace):
        raise FactoryError("a rebase is in progress in the workspace")
    result = check(paths, job_id)
    restored = []
    for m in result["mismatches"]:
        content = subprocess.run(["git", "cat-file", "blob", m["expected"]], cwd=str(paths.workspace),
                                 capture_output=True, check=False)
        if content.returncode != 0:
            raise FactoryError(f"recorded blob {m['expected']} for {m['path']} is not in the object store")
        target = Path(paths.workspace, m["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content.stdout)
        gitutil.run(["add", "--", m["path"]], paths.workspace)
        restored.append(m["path"])
    if restored and gitutil.run(["diff", "--cached", "--quiet"], paths.workspace, check=False).returncode != 0:
        gitutil.run(["commit", "-m", "Restore factory-owned test files",
                     "-m", "Changed outside test-writer: " + ", ".join(restored)], paths.workspace)
    after = check(paths, job_id)
    if not after["ok"]:
        raise FactoryError("test files still differ after restore", mismatches=after["mismatches"])
    return {"restored": restored, "ok": True}
