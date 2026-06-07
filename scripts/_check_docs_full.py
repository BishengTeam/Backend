import json

with open('/tmp/openapi.json') as f:
    data = json.load(f)

paths = data.get('paths', {})

# Target the quiz/question/exam related paths
quiz_related = []
for path, methods in sorted(paths.items()):
    for method in ['get', 'post', 'put', 'delete', 'patch']:
        if method in methods:
            op = methods[method]
            summary = (op.get('summary') or '').strip()
            desc = (op.get('description') or '').strip()
            tags = op.get('tags', [])
            tag = tags[0] if tags else ''
            
            quiz_related.append({
                'method': method.upper(),
                'path': path,
                'tag': tag,
                'summary': summary,
                'desc_len': len(desc),
                'desc': desc[:120] if desc else '(empty)',
            })

# Show ALL endpoints with their summary/desc status for easy scanning
print(f"{'method':6s} {'path':50s} {'tag':16s} {'summary':20s} {'desc_len':>8s}  desc")
print("-" * 140)
for item in quiz_related:
    flag = ""
    if item['desc_len'] == 0:
        flag = " ❌ 缺desc"
    elif item['desc_len'] < 20:
        flag = " ⚠️ <20"
    print(f"{item['method']:6s} {item['path']:50s} {item['tag']:16s} {item['summary']:20s} {item['desc_len']:>8d}{flag}")
