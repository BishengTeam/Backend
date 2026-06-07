import json, subprocess, sys

result = subprocess.run(['curl', '-s', 'http://localhost:8000/openapi.json'], capture_output=True, text=True)
data = json.loads(result.stdout)
paths = data.get('paths', {})

total = 0
no_summary = 0
no_desc = 0
short_desc = 0

for path, methods in paths.items():
    if not path.startswith('/admin') and not path.startswith('/api'):
        continue
    for method, info in methods.items():
        if method not in ('get','post','put','delete','patch'):
            continue
        total += 1
        summary = info.get('summary', '')
        desc = info.get('description', '')
        if not summary:
            no_summary += 1
            print(f'MISSING SUMMARY: {method.upper()} {path}')
        if not desc:
            no_desc += 1
            print(f'MISSING DESC:   {method.upper()} {path}')
        elif len(desc) < 20:
            short_desc += 1
            print(f'SHORT DESC:     {method.upper()} {path} — "{desc}"')

print(f'\n=== 统计 ===')
print(f'总路由: {total}')
print(f'缺 summary: {no_summary}')
print(f'缺 description: {no_desc}')
print(f'描述过短(<20字): {short_desc}')
