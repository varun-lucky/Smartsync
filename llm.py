import re, json, time, urllib.request
BAD = re.compile(r'\b(insert|update|delete|drop|attach|pragma|alter|create|replace|vacuum)\b', re.I)
TABLES = {'files', 'versions', 'blobs'}
PROMPT = """Tables: files(file_id,path,size,mtime,state,sensitive) versions(version_id,file_id,hash,size,created_at) blobs(hash,orig_size,stored_size,ref_count).
size is bytes, mtime is unix seconds. Reply with ONE SQLite SELECT statement only, no prose.
Question: """

def validate(sql):
    sql = re.sub(r'^```\w*|```$', '', sql.strip()).strip().rstrip(';')
    if ';' in sql or BAD.search(sql) or not sql.lower().startswith('select'): raise ValueError('only a single SELECT is allowed')
    tabs = set(re.findall(r'(?:from|join)\s+(\w+)', sql, re.I))
    if not tabs or not tabs <= TABLES: raise ValueError('unknown table')
    return sql if re.search(r'\blimit\b', sql, re.I) else sql + ' LIMIT 100'

def ask_ollama(q, model, host='http://localhost:11434'):
    req = urllib.request.Request(host + '/api/generate', json.dumps(dict(model=model, prompt=PROMPT + q, stream=False)).encode(), {'Content-Type': 'application/json'})
    return json.load(urllib.request.urlopen(req, timeout=30))['response']

def rule_based(q):  # offline fallback so search works without the LLM
    q = q.lower(); w = []
    for ext in ('pdf', 'jpg', 'png', 'txt', 'py', 'md', 'mp4', 'csv', 'docx', 'zip'):
        aliases = {'py': r'python(?:\s+files?)?'}
        if re.search(rf'\b{ext}s?\b', q) or (ext in aliases and re.search(aliases[ext], q)): w.append(f"path like '%.{ext}'")
    m = re.search(r'(\d+(?:\.\d+)?)\s*(kb|mb|gb)', q)
    if m: w.append(f"size {'<' if re.search(r'smaller|under|less', q) else '>'} {int(float(m[1]) * {'kb': 1e3, 'mb': 1e6, 'gb': 1e9}[m[2]])}")
    days = 30 if 'month' in q else 7 if 'week' in q else 1 if 'today' in q else 0
    if days: w.append(f"mtime > {int(time.time()) - days * 86400}")
    if any(term in q for term in ('sensitive','password','secret','api key')): w.append('sensitive=1')
    if 'deleted' in q: w.append("state='deleted'")
    return "select path,size,mtime,state from files" + (' where ' + ' and '.join(w) if w else '') + ' order by mtime desc'

def nl_to_sql(q, model='llama3.2:3b'):
    try: return validate(ask_ollama(q, model)), 'ollama:' + model
    except Exception: return validate(rule_based(q)), 'rules (Ollama not reachable)'
