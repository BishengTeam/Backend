import json, re

with open('/tmp/openapi.json') as f:
    data = json.load(f)

paths = data.get('paths', {})

# Category 1: Missing desc completely
missing_desc = []

# Category 2: desc exists but NOT following Chinese format standard
# (no 页面路径, no 使用场景, or short+English)
non_standard = []

for path, methods in sorted(paths.items()):
    for method in ['get', 'post', 'put', 'delete', 'patch']:
        if method in methods:
            op = methods[method]
            summary = (op.get('summary') or '').strip()
            desc = (op.get('description') or '').strip()
            tags = op.get('tags', [])
            tag = tags[0] if tags else ''

            m = method.upper()
            report = f"{m:6s} {path}"

            if not desc:
                missing_desc.append((report, tag, summary))
                continue

            # Check if standard format: must contain 页面路径 or 使用场景 or be in admin/courses etc batches
            # Or at minimum: has Chinese content with some structure
            has_page_path = '页面路径' in desc or '使用场景' in desc or '查询参数' in desc or '请求参数' in desc or '路径参数' in desc

            # If no standard markers AND (short or English-only)
            is_english_only = bool(re.match(r'^[A-Za-z\s\d.,;:!?()\'\"-]+$', desc))
            is_short = len(desc) < 40

            if not has_page_path and (is_short or is_english_only):
                non_standard.append((report, tag, summary, len(desc), desc[:80]))

print(f"=== 完全缺 description ({len(missing_desc)}) ===")
for r, tag, summary in missing_desc:
    print(f"  {r:55s} tag={tag:16s} summary='{summary}'")

print(f"\n=== 有 description 但格式不标准（缺页面关联信息 + 过短/纯英文）({len(non_standard)}) ===")
for r, tag, summary, length, desc in non_standard:
    print(f"  {r:55s} tag={tag:16s} len={length:>3d}  '{desc}'")

print(f"\n总计问题: {len(missing_desc)} 缺desc + {len(non_standard)} 格式不标准 = {len(missing_desc)+len(non_standard)}")
