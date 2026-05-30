"""完整扫描所有API路由，包括prefix组合"""
import re, os, ast, sys

api_dir = 'app/api'
# 存储每个文件的信息
results = []

for root, dirs, files in os.walk(api_dir):
    for f in sorted(files):
        if f.endswith('.py') and f != '__init__.py':
            path = os.path.join(root, f).replace('\\', '/')
            
            # 找 router 对象的创建: router = APIRouter(prefix='...', tags=['...'])
            router_prefix = ''
            router_tags = ''
            
            with open(path, 'r', encoding='utf-8') as fh:
                lines = fh.readlines()
            
            content = ''.join(lines)
            
            # 提取 router prefix
            pm = re.search(r'APIRouter\s*\([^)]*prefix\s*=\s*[\"\']([^\"\']+)[\"\']', content)
            if pm:
                router_prefix = pm.group(1)
            
            # 提取所有 @router.xxx('/yyy') 路由
            for i, line in enumerate(lines):
                m = re.match(r'\s*@router\.(get|post|put|delete|patch)\s*\(\s*[\"\']([^\"\']+)[\"\']', line)
                if m:
                    method = m.group(1).upper()
                    sub_route = m.group(2)
                    full_route = router_prefix + sub_route if sub_route != '/' else router_prefix
                    
                    # 找下一行的summary/docstring
                    desc = ''
                    for j in range(i+1, min(i+5, len(lines))):
                        ds = re.search(r'(?:summary|description)\s*=\s*[\"\'](.+?)[\"\']', lines[j])
                        if ds:
                            desc = ds.group(1)
                            break
                        ds2 = re.search(r'\"\"\"\s*(.+?)\s*\"\"\"', lines[j])
                        if ds2:
                            desc = ds2.group(1)
                            break
                    
                    results.append({
                        'method': method,
                        'full_route': full_route,
                        'file': path,
                        'desc': desc
                    })

# 按路由排序
results.sort(key=lambda x: x['full_route'])

for r in results:
    print(f"{r['method']:6s} {r['full_route']:50s} [{r['file']}]  {r['desc']}")

print(f'\n总计: {len(results)} 条路由')
