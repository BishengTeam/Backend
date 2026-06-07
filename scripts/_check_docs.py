import json

with open('/tmp/openapi.json') as f:
    data = json.load(f)

paths = data.get('paths', {})
total = 0
missing_summary = []
summary_only_no_desc = []

for path, methods in sorted(paths.items()):
    for method in ['get', 'post', 'put', 'delete', 'patch']:
        if method in methods:
            total += 1
            op = methods[method]
            summary = (op.get('summary') or '').strip()
            desc = (op.get('description') or '').strip()
            tags = op.get('tags', [])
            tag = tags[0] if tags else ''

            report = f"{method.upper():6s} {path}"

            if not summary and not desc:
                missing_summary.append(f"  {report:55s} tag={tag}")
            elif not summary:
                missing_summary.append(f"  {report:55s} tag={tag}  (有desc缺summary)")
            elif not desc:
                summary_only_no_desc.append(f"  {report:55s} tag={tag}  summary=\"{summary}\"")

print(f"总接口数: {total}")
print()

if missing_summary:
    print(f"=== 缺 summary ({len(missing_summary)}) ===")
    for m in missing_summary:
        print(m)
    print()

if summary_only_no_desc:
    print(f"=== 有 summary 但缺 description ({len(summary_only_no_desc)}) ===")
    for m in summary_only_no_desc:
        print(m)
    print()

if not missing_summary and not summary_only_no_desc:
    print("所有接口 summary + description 完整 ✅")
