import sys, csv, html

def main():
    if len(sys.argv) < 2:
        sys.exit(1)
    path = sys.argv[1]
    parts = [
        "<!doctype html><html><head><meta charset=utf-8><style>"
        "body{font-family:system-ui,sans-serif;font-size:13px;margin:0;padding:8px;background:#f8fafc;color:#0f172a;}"
        "table{border-collapse:collapse;width:max-content;min-width:100%;}"
        "th{background:#e2e8f0;padding:5px 10px;border:1px solid #cbd5e1;text-align:left;white-space:nowrap;}"
        "td{padding:4px 10px;border:1px solid #e2e8f0;white-space:nowrap;max-width:320px;overflow:hidden;text-overflow:ellipsis;}"
        "tr:nth-child(even) td{background:#f1f5f9;}"
        "</style></head><body><div style='overflow:auto'><table>",
    ]
    try:
        with open(path, newline="", encoding="utf-8-sig", errors="replace") as f:
            reader = csv.reader(f)
            first = True; col_count = 1
            for i, row in enumerate(reader):
                if i >= 2001:
                    parts.append(f'<tr><td colspan="{col_count}" style="color:#64748b;">&#x2026; truncated at 2000 rows &#x2026;</td></tr>')
                    break
                if first: col_count = max(len(row), 1)
                tag = "th" if first else "td"
                parts.append("<tr>"+"".join(f"<{tag}>{html.escape(c)}</{tag}>" for c in row)+"</tr>")
                first = False
    except Exception as e:
        print(f"ERROR:{e}", file=sys.stderr); sys.exit(3)
    parts.append("</table></div></body></html>")
    sys.stdout.write("".join(parts))

if __name__ == "__main__":
    main()
