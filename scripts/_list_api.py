import json, subprocess, sys

r = subprocess.run(['curl', '-s', 'http://127.0.0.1:8000/openapi.json'], capture_output=True, text=True)
if r.returncode != 0:
    print(f'curl failed: {r.stderr}')
    sys.exit(1)

data = json.loads(r.stdout)
paths = data.get('paths', {})

print('=== 接口清单 ===\n')
for path in sorted(paths.keys()):
    methods = paths[path]
    for method in sorted(methods.keys()):
        if method not in ('get','post','put','delete','patch'):
            continue
        info = methods[method]
        summary = info.get('summary','')
        tags = ', '.join(info.get('tags',[]))
        print(f'{method.upper():6s} {path:45s} [{tags}] {summary}')

count = sum(1 for p in paths for m in paths[p] if m in ('get','post','put','delete','patch'))
print(f'\n共 {count} 个接口')
