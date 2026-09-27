import sys, html

def main():
    if len(sys.argv) < 2:
        sys.exit(1)
    path = sys.argv[1]
    with open(path, 'rb') as f:
        if f.read(4)[:2] != b'PK':
            sys.exit(2)
    import openpyxl
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(max_row=2000, values_only=True))
        wb.close()
    except Exception as e:
        print(f"ERROR:{e}", file=sys.stderr); sys.exit(3)
    if not rows:
        sys.stdout.write("<p>Empty spreadsheet.</p>"); return
    head, *body = rows
    parts = [
        "<!doctype html><html><head><meta charset=utf-8><style>"
        "body{font-family:system-ui,sans-serif;font-size:13px;margin:0;padding:8px;background:#f8fafc;color:#0f172a;}"
        "table{border-collapse:collapse;width:max-content;min-width:100%;}"
        "th{background:#e2e8f0;padding:5px 10px;border:1px solid #cbd5e1;text-align:left;white-space:nowrap;}"
        "td{padding:4px 10px;border:1px solid #e2e8f0;white-space:nowrap;max-width:320px;overflow:hidden;text-overflow:ellipsis;}"
        "tr:nth-child(even) td{background:#f1f5f9;}"
        "</style></head><body><div style='overflow:auto'><table><thead><tr>",
    ]
    for c in head:
        parts.append(f"<th>{html.escape(str(c) if c is not None else '')}</th>")
    parts.append("</tr></thead><tbody>")
    for row in body:
        parts.append("<tr>"+"".join(f"<td>{html.escape(str(c) if c is not None else '')}</td>" for c in row)+"</tr>")
    parts.append("</tbody></table></div></body></html>")
    sys.stdout.write("".join(parts))

if __name__ == "__main__":
    main()
