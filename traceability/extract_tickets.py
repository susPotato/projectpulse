"""Pull the 173 unkeyed feature Tasks out of the Jira general_report sheet."""
import json, sys
import openpyxl

wb = openpyxl.load_workbook(sys.argv[1], read_only=True)
ws = wb.worksheets[0]
rows = [list(r) for r in ws.iter_rows(values_only=True)]
hdr = next(r for r in rows if r and r[0] == "Project")
ci = {h: i for i, h in enumerate(hdr) if h}

def g(row, col):
    i = ci.get(col)
    return row[i] if i is not None and i < len(row) else None

tickets, parent = [], None
for r in rows:
    if not r or r[0] != "CoWorkLocal":
        continue
    if g(r, "Key"):
        parent = g(r, "Key")
        continue
    tickets.append({
        "parent": parent,
        "summary": str(g(r, "Summary") or ""),
        "description": str(g(r, "Description") or ""),
        "component": str(g(r, "Component/s") or "None"),
        "status": str(g(r, "Status") or ""),
        "fix_version": str(g(r, "Fix Version/s") or ""),
    })

Path = __import__("pathlib").Path
Path(sys.argv[2]).write_text(json.dumps(tickets, ensure_ascii=False), encoding="utf-8")
print(f"tickets={len(tickets)}")
