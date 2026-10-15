import urllib.request, urllib.parse, json, http.cookiejar

BASE = "http://127.0.0.1:8766"
jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
headers = {"Content-Type": "application/json"}

def post(path, body=None):
    data = json.dumps(body).encode() if body is not None else b"{}"
    req = urllib.request.Request(BASE + path, data=data, method="POST", headers=headers)
    try:
        with opener.open(req) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"ERROR": e.code, "body": e.read().decode()}

def get(path):
    req = urllib.request.Request(BASE + path)
    with opener.open(req) as r:
        return json.loads(r.read())

def delete(path):
    req = urllib.request.Request(BASE + path, method="DELETE")
    try:
        with opener.open(req) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"ERROR": e.code, "body": e.read().decode()}

# 1. 登录
r = post("/api/auth/login", {"username": "admin", "password": "admin123456"})
print(f"1. LOGIN: {r.get('user', {}).get('username', r)}")

# 2. GET workspaces
r = get("/api/workspaces")
print(f"\n2. GET workspaces: active={r['active']} count={len(r['workspaces'])}")
for ws in r['workspaces']:
    print(f"   [{ws['id'][:12]}] active={ws['active']} exists={ws['exists']} name={ws['name']}")

# 3. POST create
r = post("/api/workspaces", {"name": "Python完整测试"})
new_id = r["id"]
print(f"\n3. CREATE: id={new_id} name={r['name']}")

# 4. Activate new
r = post(f"/api/workspaces/{new_id}/activate")
print(f"\n4. ACTIVATE: {r}")

# 5. GET state — 确认 active 切换了
r = get("/api/state")
print(f"\n5. STATE active_workspace={r.get('active_workspace')} stats.assets={r['stats']['assets']}")

# 6. 切回 legacy
r = post("/api/workspaces/legacy/activate")
print(f"\n6. SWITCH TO legacy: {r}")

# 7. GET state 确认数据回到 legacy
r = get("/api/state")
print(f"\n7. STATE AFTER SWITCH: active={r.get('active_workspace')} stats.assets={r['stats']['assets']}")

# 8. DELETE 新工作空间
r = delete(f"/api/workspaces/{new_id}")
print(f"\n8. DELETE Python完整测试: {r}")

# 9. 删当前活跃的 → 应该报错
r = delete("/api/workspaces/legacy")
print(f"\n9. DELETE active (should fail): {r}")

# 10. 删最后一个 → 也应该报错
# 先确保只剩 2 个（legacy + c929d4d07bb4 + aa29a44a725a）
# 让我们清理残留
r = delete("/api/workspaces/c929d4d07bb4")
print(f"\n10a. DELETE leftover c929...: {r}")
r = delete("/api/workspaces/aa29a44a725a")
print(f"10b. DELETE leftover aa29...: {r}")

# 现在只剩 legacy — 删它（但它是 active，应该先报错）
r = delete("/api/workspaces/legacy")
print(f"10c. DELETE only workspace (active): {r}")

print("\n=== ALL API TESTS COMPLETE ===")
