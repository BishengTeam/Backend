import json

with open('/tmp/openapi.json') as f:
    data = json.load(f)

paths = data.get('paths', {})
short_desc = []

for path, methods in sorted(paths.items()):
    for method in ['get', 'post', 'put', 'delete', 'patch']:
        if method in methods:
            op = methods[method]
            desc = (op.get('description') or '').strip()
            summary = (op.get('summary') or '').strip()
            tags = op.get('tags', [])
            tag = tags[0] if tags else ''

            if desc and len(desc) < 20:
                short_desc.append(f"  {method.upper():6s} {path:45s} tag={tag}  len={len(desc)}  desc=\"{desc}\"")

if short_desc:
    print(f"=== description 过短 <20 字 ({len(short_desc)}) ===")
    for m in short_desc:
        print(m)
else:
    print("所有 description 长度 >= 20 字 ✅")
