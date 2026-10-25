import os, time
import base64, hashlib
from pydantic import BaseModel
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Depends
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import secrets
from fastapi.responses import FileResponse
from .engine import Engine
from .db import connect
from .llm import nl_to_sql, ask_ollama

E = None
_conflict_summaries = {}
_advice_cache = {}
@asynccontextmanager
async def life(app):
    global E
    E = Engine(os.getenv('SS_WATCH', 'sync_folder'), os.getenv('SS_CLOUD', 'cloud_store'), os.getenv('SS_DB', 'smartsync.db'), os.getenv('SS_PASS', 'change-me'))
    E.start(); yield; E.shutdown()
app = FastAPI(title='SmartSync', lifespan=life)
security = HTTPBasic()
def require_login(credentials: HTTPBasicCredentials = Depends(security)):
    user=os.getenv('SS_USER','admin'); password=os.getenv('SS_LOGIN_PASS',os.getenv('SS_PASS','change-me'))
    if not (secrets.compare_digest(credentials.username,user) and secrets.compare_digest(credentials.password,password)):
        raise HTTPException(401,'Login required',headers={'WWW-Authenticate':'Basic'})
    return credentials.username
rows = lambda c, sql, a=(): [dict(r) for r in c.execute(sql, a).fetchall()]

class CloudChange(BaseModel):
    path: str
    base_hash: str | None = None
    content_base64: str

@app.post('/api/cloud/changes')
def cloud_change(change: CloudChange, _user=Depends(require_login)):
    """Ingest a cloud-side revision, compare it with local/base, and open conflicts."""
    rel=change.path.replace('\\','/').strip('/')
    target=os.path.abspath(os.path.join(E.watch,rel))
    if not rel or os.path.commonpath([E.watch,target])!=E.watch: raise HTTPException(400,'invalid path')
    try: content=base64.b64decode(change.content_base64,validate=True)
    except Exception: raise HTTPException(400,'content_base64 must be valid base64')
    cloud_hash=hashlib.sha256(content).hexdigest()
    local_hash=None
    if os.path.isfile(target):
        h=hashlib.sha256()
        with open(target,'rb') as f:
            for block in iter(lambda:f.read(1<<20),b''): h.update(block)
        local_hash=h.hexdigest()
    # Stage under an ignored hidden file, then let the normal streaming/encryption
    # path persist this remote revision into the local cloud adapter.
    c=connect(E.db); known=c.execute('select 1 from versions where hash=? limit 1',(cloud_hash,)).fetchone(); c.close()
    if not known:
        stage=os.path.join(E.watch,'.remote-staging-'+cloud_hash)
        with open(stage,'wb') as f: f.write(content)
        try: E.process(stage,remote=True)
        finally:
            try: os.remove(stage)
            except OSError: pass
        c=connect(E.db); c.execute("update files set path=? where path=?",('__cloud_versions/'+cloud_hash,'.remote-staging-'+cloud_hash)); c.commit(); c.close()
    if local_hash==cloud_hash: return {'action':'none','hash':cloud_hash}
    if local_hash is None or (change.base_hash is not None and local_hash==change.base_hash):
        c=connect(E.db); v=c.execute('select version_id from versions where hash=? order by version_id desc limit 1',(cloud_hash,)).fetchone(); c.close()
        if v: E.restore(v['version_id'],target)
        return {'action':'download','hash':cloud_hash}
    if change.base_hash is not None and cloud_hash==change.base_hash:
        return {'action':'upload','local_hash':local_hash}
    c=connect(E.db); cur=c.execute('insert into conflicts(path,local_hash,cloud_hash,base_hash,created_at) values(?,?,?,?,?)',(rel,local_hash or '',cloud_hash,change.base_hash,time.time())); c.commit(); cid=cur.lastrowid; c.close()
    return {'action':'conflict','conflict_id':cid,'local_hash':local_hash,'cloud_hash':cloud_hash}

@app.get('/')
def index(_user=Depends(require_login)): return FileResponse(os.path.join(os.path.dirname(__file__), 'static', 'index.html'))
@app.get('/api/status')
def status(_user=Depends(require_login)): return E.stats()
@app.get('/api/files')
def files(_user=Depends(require_login)):
    c = connect(E.db, ro=True)
    return rows(c, "select file_id,path,size,mtime,state,sensitive,(select count(*) from versions v where v.file_id=f.file_id) versions from files f where path not like '__cloud_versions/%' order by mtime desc limit 500")
@app.get('/api/search')
def search(q: str, model: str = 'llama3.2:3b', _user=Depends(require_login)):
    try: sql, src = nl_to_sql(q, model)
    except ValueError as e: raise HTTPException(400, str(e))
    c = connect(E.db, ro=True)
    try: return dict(sql=sql, source=src, rows=rows(c, sql))
    except Exception as e: raise HTTPException(400, f'query failed: {e}')
@app.get('/api/versions/{fid}')
def versions(fid: int, _user=Depends(require_login)): return rows(connect(E.db, ro=True), "select version_id,size,created_at,hash from versions where file_id=? order by version_id desc", (fid,))
@app.post('/api/restore/{vid}')
def restore(vid: int, _user=Depends(require_login)):
    try: return dict(restored=E.restore(vid))
    except KeyError: raise HTTPException(404, 'version not found')
@app.get('/api/audit')
def audit(_user=Depends(require_login)): return rows(connect(E.db, ro=True), "select actor,action,target,ts from audit_log order by event_id desc limit 100")
@app.post('/api/settings')
def settings(policy: str = None, cpu_limit: int = None, rate_mb: int = None, _user=Depends(require_login)):
    if policy in ('fcfs', 'sjf'): E.policy = policy
    if cpu_limit: E.cpu_limit = max(10, min(100, cpu_limit))
    if rate_mb: E.bucket.rate = max(1, rate_mb) << 20
    return E.stats()
@app.post('/api/demo')  # drops sample files, incl. duplicates and a sensitive one
def demo(_user=Depends(require_login)):
    d = os.path.join(E.watch, 'demo'); os.makedirs(d, exist_ok=True)
    body = ('Quarterly notes. ' * 4000).encode()
    for n in ('report.txt', 'report-copy.txt', 'backup/report.txt'):
        p = os.path.join(d, n); os.makedirs(os.path.dirname(p), exist_ok=True); open(p, 'wb').write(body)
    open(os.path.join(d, 'photo.jpg'), 'wb').write(os.urandom(300_000))
    open(os.path.join(d, 'notes.md'), 'w').write(f'# notes {time.time()}\n')
    open(os.path.join(d, 'config.env'), 'w').write('API_KEY=sk-123\npassword: hunter2\n')
    return dict(ok=True)

@app.get('/api/conflicts')
def conflicts(_user=Depends(require_login)):
    found=rows(connect(E.db,ro=True),"select * from conflicts where resolved_at is null order by created_at desc")
    for x in found:
        fallback='Local and cloud versions both differ from the recorded base.' if x['base_hash'] else 'Local and cloud versions differ; no common base hash was supplied.'
        if x['conflict_id'] not in _conflict_summaries:
            try:
                prompt='Write one neutral sentence summarizing a file sync conflict. Do not choose a version. Path: '+x['path']+'; base known: '+str(bool(x['base_hash']))+'; local hash: '+x['local_hash']+'; cloud hash: '+x['cloud_hash']
                _conflict_summaries[x['conflict_id']]=ask_ollama(prompt,os.getenv('SS_LLM_MODEL','llama3.2:3b'))[:500]
            except Exception: _conflict_summaries[x['conflict_id']]=fallback
        x['summary']=_conflict_summaries[x['conflict_id']]
    return found

@app.post('/api/conflicts')
def register_conflict(path: str, local_hash: str, cloud_hash: str, base_hash: str = None, _user=Depends(require_login)):
    if local_hash==cloud_hash: return {'conflict':False,'state':'none'}
    c=connect(E.db); cur=c.execute('insert into conflicts(path,local_hash,cloud_hash,base_hash,created_at) values(?,?,?,?,?)',(path,local_hash,cloud_hash,base_hash,time.time())); c.commit(); c.close()
    return {'conflict':True,'conflict_id':cur.lastrowid}

@app.post('/api/conflicts/{cid}/resolve')
def resolve_conflict(cid:int, choice:str, _user=Depends(require_login)):
    if choice not in ('keep_local','keep_cloud','keep_both'): raise HTTPException(400,'choice must be keep_local, keep_cloud or keep_both')
    c=connect(E.db); r=c.execute('select * from conflicts where conflict_id=? and resolved_at is null',(cid,)).fetchone()
    if not r: c.close(); raise HTTPException(404,'open conflict not found')
    if choice in ('keep_cloud','keep_both'):
        v=c.execute('select version_id from versions where hash=? order by version_id desc limit 1',(r['cloud_hash'],)).fetchone()
        if not v: c.close(); raise HTTPException(409,'Cloud version is not present in this device database; sync/import the cloud manifest first')
        target=os.path.abspath(os.path.join(E.watch,r['path']))
        if os.path.commonpath([E.watch,target])!=E.watch: c.close(); raise HTTPException(400,'invalid conflict path')
        if choice=='keep_both' and os.path.exists(target):
            import shutil
            alt=target+'.local-conflict-'+str(cid); os.makedirs(os.path.dirname(alt),exist_ok=True); shutil.copy2(target,alt)
            s=c.execute("select size,mtime,sensitive from files where path=?",(r['path'],)).fetchone()
            if s:
                rel=os.path.relpath(alt,E.watch).replace('\\','/')
                c.execute("insert or ignore into files(path,size,mtime,state,sensitive) values(?,?,?,'synced',?)",(rel,s['size'],s['mtime'],s['sensitive']))
        E.restore(v['version_id'],target)
    elif choice=='keep_local':
        if not os.path.isfile(os.path.join(E.watch,r['path'])): c.close(); raise HTTPException(409,'local conflict version is missing')
        local=hashlib.sha256()
        with open(target,'rb') as f:
            for block in iter(lambda:f.read(1<<20),b''): local.update(block)
        h=local.hexdigest()
        c.execute('insert into cloud_outbox(path,action,content_hash,created_at) values(?,?,?,?)',(r['path'],'upload',h,time.time()))
    c.execute('update conflicts set resolved_at=?,resolution=? where conflict_id=?',(time.time(),choice,cid)); c.commit(); c.close()
    return {'resolved':cid,'choice':choice}

@app.get('/api/cloud/outbox')
def cloud_outbox(_user=Depends(require_login)):
    return rows(connect(E.db,ro=True),'select outbox_id,path,action,content_hash,created_at from cloud_outbox where acknowledged_at is null order by outbox_id')

@app.post('/api/cloud/outbox/{oid}/ack')
def acknowledge_cloud_action(oid:int,_user=Depends(require_login)):
    c=connect(E.db); cur=c.execute('update cloud_outbox set acknowledged_at=? where outbox_id=? and acknowledged_at is null',(time.time(),oid)); c.commit(); changed=cur.rowcount; c.close()
    if not changed: raise HTTPException(404,'pending cloud action not found')
    return {'acknowledged':oid}

@app.get('/api/advice')
def advice(_user=Depends(require_login)):
    c=connect(E.db,ro=True)
    items=rows(c,"select path,size,sensitive,mtime,state from files order by mtime")
    c.close(); now=time.time(); cold=[x['path'] for x in items if x['state']=='synced' and now-x['mtime']>90*86400]
    dup=E.stats()['dup_blobs']; flagged=[x['path'] for x in items if x['sensitive']]
    facts={'cold_count':len(cold),'sensitive_count':len(flagged),'duplicate_blobs':dup,'cold_examples':cold[:12]}
    cache_key=__import__('json').dumps(facts,sort_keys=True)
    if cache_key in _advice_cache: return _advice_cache[cache_key]
    summary=f"{len(cold)} files have not changed in 90+ days; {len(flagged)} files are flagged as sensitive; {dup} content blobs are shared by duplicates."
    cleanup=['Review cold files for archival or removal after confirming they are no longer needed.'] if cold else []
    source='local heuristics'
    try:
        response=ask_ollama('Summarize these SmartSync cleanup facts in two short sentences. Do not claim deletion is safe. Return JSON with summary and cleanup_advice array. Facts: '+__import__('json').dumps(facts),os.getenv('SS_LLM_MODEL','llama3.2:3b'))
        parsed=__import__('json').loads(response); summary=str(parsed.get('summary',summary))[:600]; cleanup=[str(x)[:240] for x in parsed.get('cleanup_advice',cleanup)][:5]; source='ollama'
    except Exception: pass
    result={'source':source,'summary':summary,'cleanup_advice':cleanup,'cold_files':cold[:100],'sensitive_files':flagged[:100]}
    if len(_advice_cache)>100: _advice_cache.clear()
    _advice_cache[cache_key]=result
    return result
