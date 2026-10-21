"""
Git merge driver for outreach_queue.csv: merges row by row (keyed on company name)
instead of line by line, so the daily bot, the hosted dashboard and your PC can all
edit the queue without conflicts.

Git calls it as:  python merge_queue.py %O %A %B
  %O = common ancestor, %A = our version (result is written here), %B = their version

Rules per field: if only one side changed it, keep that change. If both changed it,
'status' keeps whichever is further along the pipeline (e.g. Approved beats Pending,
Sent beats Approved); other fields prefer the non-empty / newer-side value.
Registered in .gitattributes; enable locally once with:
  git config merge.queue.driver "python merge_queue.py %O %A %B"
"""
import csv
import sys

STATUS_RANK = {
    "Needs Contact": -1, "Pending": 0, "Skipped": 1, "Approved": 2, "Failed": 3, "Sent": 4, "Followup-Sent": 5,
    "Bounced": 6, "Replied": 7, "Rejected": 8, "Interview": 9, "Offer": 10,
}


def read(path):
    try:
        with open(path, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            return list(reader.fieldnames or []), rows
    except FileNotFoundError:
        return [], []


def key(row):
    return (row.get("company_name") or "").strip().lower()


def merge_field(field, base, ours, theirs):
    if ours == theirs:
        return ours
    if ours == base:
        return theirs
    if theirs == base:
        return ours
    # both sides changed it differently
    if field == "status":
        return max((ours, theirs), key=lambda s: STATUS_RANK.get(s, 0))
    return ours or theirs


def merge(base_path, ours_path, theirs_path):
    base_h, base_rows = read(base_path)
    ours_h, ours_rows = read(ours_path)
    theirs_h, theirs_rows = read(theirs_path)

    header = list(ours_h)
    for h in theirs_h + base_h:
        if h not in header:
            header.append(h)

    base = {key(r): r for r in base_rows}
    theirs = {key(r): r for r in theirs_rows}
    seen = set()
    out = []
    for o in ours_rows:
        k = key(o)
        seen.add(k)
        t = theirs.get(k)
        if t is None:
            if k in base:   # they deleted it; respect that only if we didn't change it
                if all((o.get(h) or "") == (base[k].get(h) or "") for h in header):
                    continue
            out.append(o)
            continue
        b = base.get(k, {})
        out.append({h: merge_field(h, b.get(h, "") or "", o.get(h, "") or "", t.get(h, "") or "") for h in header})
    for t in theirs_rows:
        k = key(t)
        if k not in seen and k not in base:  # new row added on their side
            out.append(t)
            seen.add(k)

    with open(ours_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        w.writeheader()
        w.writerows(out)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(2)
    sys.exit(merge(*sys.argv[1:4]))
