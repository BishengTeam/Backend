import json
from collections import Counter

d = json.load(open('/tmp/openapi.json'))

tag_count = Counter()
for path, methods in d['paths'].items():
    for method, info in methods.items():
        if method in ('parameters',):
            continue
        for t in info.get('tags', []):
            tag_count[t] += 1

admin_tags = []
user_tags = []
for tag, count in sorted(tag_count.items()):
    if tag.startswith('管理后台-'):
        admin_tags.append((tag, count))
    else:
        user_tags.append((tag, count))

print('=== 管理后台 tag ===')
for tag, count in admin_tags:
    print(f'  {count:>3d}  {tag}')

print(f'\n=== 用户端 tag ({len(user_tags)}) ===')
for tag, count in user_tags:
    print(f'  {count:>3d}  {tag}')
