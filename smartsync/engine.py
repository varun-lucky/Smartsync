import os, re, time, random, hashlib, zlib, threading, psutil
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from .db import connect, init
from .storage import LocalCloud, create_cloud

SKIP = {'.jpg', '.jpeg', '.png', '.mp4', '.zip', '.gz', '.mp3', '.7z'}
SENS = re.compile(r'(AKIA[0-9A-Z]{16}|api[_-]?key\s*[:=]|password\s*[:=]|\b(?:\d[ -]?){13,16}\b)', re.I)

def sha256(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for ch in iter(lambda: f.read(1 << 20), b''): h.update(ch)
    return h.hexdigest()

def detect(base, local, cloud):
    """Three-way hash comparison (synopsis section 14, algorithm 2)."""
    if local == cloud: return 'none'
    if local == base: return 'download'
    if cloud == base: return 'upload'
    return 'conflict'

class Bucket:  # token bucket, bytes/sec
    def __init__(s, rate): s.rate = rate; s.t = rate; s.last = time.time()
    def take(s, n):
        now = time.time(); s.t = min(s.rate, s.t + (now - s.last) * s.rate); s.last = now
        if s.t < n: time.sleep((n - s.t) / s.rate); s.t = 0; s.last = time.time()
        else: s.t -= n

class Engine:
    def __init__(s, watch, cloud, db, passphrase, workers=3, policy='sjf', cpu_limit=85, rate=50 << 20):
        s.watch = os.path.abspath(watch); os.makedirs(s.watch, exist_ok=True)
        s.cloud = create_cloud() if os.getenv('SS_STORAGE','local').lower()=='minio' else LocalCloud(cloud); s.db = db; s.policy = policy; s.cpu_limit = cpu_limit
        s.bucket = Bucket(rate); s.tasks = []; s.cv = threading.Condition(); s.stop = threading.Event()
        s.active = 0; s.inflight = set(); s.nworkers = workers; s.throttled = False; s.metrics = []
        init(db); c = connect(db)
        r = c.execute("select value from settings where key='salt'").fetchone()
        if r: salt = bytes.fromhex(r[0])
        else:
            salt = os.urandom(16); c.execute("insert into settings values('salt',?)", (salt.hex(),)); c.commit()
        s.key = Scrypt(salt=salt, length=32, n=2**14, r=8, p=1).derive(passphrase.encode())  # key never leaves the PC
        c.close()

    # ---- lifecycle ----
    def start(s):
        c = connect(s.db)  # crash recovery: intents without a matching 'done'
        n = c.execute("""select count(*) from sync_log i where phase='intent' and not exists
            (select 1 from sync_log d where d.path=i.path and d.phase='done' and d.ts>=i.ts)""").fetchone()[0]
        if n: s._audit(c, 'system', 'recovery', f'{n} interrupted syncs re-queued'); c.commit()
        c.close()
        for _ in range(s.nworkers): threading.Thread(target=s._worker, daemon=True).start()
        threading.Thread(target=s._sampler, daemon=True).start()
        s.obs = Observer(); s.obs.schedule(_Handler(s), s.watch, recursive=True); s.obs.start()
        s.scan()
    def shutdown(s): s.stop.set(); s.obs.stop()
    def scan(s):
        for d, _, fs in os.walk(s.watch):
            for f in fs:
                if not f.startswith('.') and not f.endswith('.tmp'): s.submit(os.path.join(d, f))

    # ---- scheduler: FCFS or SJF with aging ----
    def submit(s, path, retries=0, delay=0.0):
        with s.cv:
            if any(t['path'] == path for t in s.tasks): return
            try: size = os.path.getsize(path)
            except OSError: size = 0
            s.tasks.append(dict(path=path, size=size, t=time.time(), retries=retries, at=time.time() + delay)); s.cv.notify()
    def _pick(s):
        now = time.time(); ready = [t for t in s.tasks if t['at'] <= now and t['path'] not in s.inflight]
        if not ready: return None
        key = (lambda t: t['t']) if s.policy == 'fcfs' else (lambda t: t['size'] / 1e6 - 5 * (now - t['t']))
        t = min(ready, key=key); s.tasks.remove(t); s.inflight.add(t['path']); return t
    def _worker(s):
        while not s.stop.is_set():
            with s.cv:
                t = s._pick()
                if not t: s.cv.wait(0.5); continue
            while psutil.cpu_percent(None) > s.cpu_limit and not s.stop.is_set(): s.throttled = True; time.sleep(1)
            s.throttled = False; s.active += 1
            try: s.process(t['path'])
            except Exception as e:  # offline / disk error -> exponential backoff with jitter
                r = t['retries'] + 1
                c = connect(s.db); s._audit(c, 'system', 'retry', f"{os.path.basename(t['path'])}: {e}"); c.commit(); c.close()
                if r <= 6: s.submit(t['path'], r, min(60, 2 ** r + random.random()))
            finally:
                s.active -= 1
                with s.cv: s.inflight.discard(t['path'])
    def _sampler(s):
        while not s.stop.is_set():
            s.metrics = (s.metrics + [dict(t=time.time(), cpu=psutil.cpu_percent(None), q=len(s.tasks) + s.active)])[-60:]; time.sleep(2)

    # ---- core sync: one transaction per file ----
    def _audit(s, c, actor, action, target): c.execute("insert into audit_log(actor,action,target,ts) values(?,?,?,?)", (actor, action, target, time.time()))
    def process(s, path, remote=False):
        rel = os.path.relpath(path, s.watch).replace('\\', '/'); c = connect(s.db)
        try:
            row = c.execute("select file_id,size,mtime,current_version_id,state from files where path=?", (rel,)).fetchone()
            if not os.path.isfile(path):
                if row and row['state'] != 'deleted':
                    c.execute("update files set state='deleted' where file_id=?", (row['file_id'],)); s._audit(c, 'watcher', 'delete', rel); c.commit()
                return
            st = os.stat(path)
            if row and row['state'] == 'synced' and row['size'] == st.st_size and row['mtime'] == st.st_mtime: return  # cheap change check
            h = sha256(path)
            if row:
                cur = c.execute("select hash from versions where version_id=?", (row['current_version_id'],)).fetchone()
                if cur and cur['hash'] == h and row['state'] != 'deleted':
                    c.execute("update files set size=?,mtime=?,state='synced' where file_id=?", (st.st_size, st.st_mtime, row['file_id'])); c.commit(); return
            elif not remote:  # rename detection: same hash, old path gone -> no re-upload
                for r in c.execute("select f.file_id,f.path from files f join versions v on v.version_id=f.current_version_id where v.hash=?", (h,)).fetchall():
                    if not os.path.exists(os.path.join(s.watch, r['path'])):
                        c.execute("update files set path=?,mtime=?,state='synced' where file_id=?", (rel, st.st_mtime, r['file_id']))
                        s._audit(c, 'watcher', 'rename', f"{r['path']} -> {rel}"); c.commit(); return
            c.execute("insert into sync_log(path,phase,ts) values(?,?,?)", (rel, 'intent', time.time())); c.commit()  # write-ahead intent
            sens = 0
            if st.st_size < (1 << 20):
                try: sens = int(bool(SENS.search(open(path, errors='ignore').read())))
                except Exception: pass
            have = c.execute("select 1 from blobs where hash=?", (h,)).fetchone(); stored = 0; block_hashes=[]
            # Fixed-size blocks keep memory bounded regardless of file size. Each
            # block is independently content-addressed, compressed, encrypted,
            # and uploaded only once across all files and versions.
            stream_hash=hashlib.sha256()
            with open(path,'rb') as src:
                while True:
                    part=src.read(1<<20)
                    if not part: break
                    stream_hash.update(part)
                    bh=hashlib.sha256(part).hexdigest(); existing=c.execute('select stored_size from blocks where hash=?',(bh,)).fetchone()
                    if existing:
                        block_hashes.append((bh,len(part),existing['stored_size'])); continue
                    payload=b'\x00'+part if os.path.splitext(path)[1].lower() in SKIP else b'\x01'+zlib.compress(part,6)
                    nonce=os.urandom(12); encrypted=nonce+AESGCM(s.key).encrypt(nonce,payload,None)
                    key='blocks/'+bh; s.bucket.take(len(encrypted)); s.cloud.upload(key,encrypted); stored+=len(encrypted)
                    block_hashes.append((bh,len(part),len(encrypted)))
            if stream_hash.hexdigest()!=h: raise IOError('file changed while it was being encrypted; retrying')
            with c:  # atomic: blob + file + version + log + audit
                if have: c.execute("update blobs set ref_count=ref_count+1 where hash=?", (h,))
                else: c.execute("insert into blobs values(?,?,?,?,1,1)", (h, 'manifest:'+h, st.st_size, stored))
                if row:
                    fid = row['file_id']; c.execute("update files set size=?,mtime=?,state='synced',sensitive=? where file_id=?", (st.st_size, st.st_mtime, sens, fid))
                else: fid = c.execute("insert into files(path,size,mtime,state,sensitive) values(?,?,?,'synced',?)", (rel, st.st_size, st.st_mtime, sens)).lastrowid
                vid = c.execute("insert into versions(file_id,hash,size,created_at,device_id) values(?,?,?,?,?)", (fid, h, st.st_size, time.time(), 'this-pc')).lastrowid
                for ordinal, (bh, part_size, encrypted_size) in enumerate(block_hashes):
                    old = c.execute('select 1 from blocks where hash=?',(bh,)).fetchone()
                    if old: c.execute('update blocks set ref_count=ref_count+1 where hash=?',(bh,))
                    else:
                        # The block was uploaded above; recover ciphertext size
                        # from the object only for a newly encountered block.
                        c.execute('insert into blocks values(?,?,?,?,1)',(bh,'blocks/'+bh,part_size,encrypted_size))
                    c.execute('insert into version_blocks values(?,?,?)',(vid,ordinal,bh))
                c.execute("update files set current_version_id=? where file_id=?", (vid, fid))
                c.execute("insert into sync_log(path,phase,ts,details) values(?,?,?,?)", (rel, 'done', time.time(), h))
                s._audit(c, 'worker', 'sync' + (' (dedup)' if have else ''), rel)
        finally: c.close()

    # ---- restore ----
    def restore(s, vid, destination=None):
        c = connect(s.db)
        r = c.execute("select v.hash,v.size,f.path from versions v join files f using(file_id) where version_id=?", (vid,)).fetchone()
        if not r: raise KeyError(vid)
        dest = destination or os.path.join(s.watch, r['path'])
        os.makedirs(os.path.dirname(dest),exist_ok=True)
        chunks=c.execute('select block_hash from version_blocks where version_id=? order by ordinal',(vid,)).fetchall()
        if not chunks and r['size']==0:
            open(dest+'.tmp','wb').close(); os.replace(dest+'.tmp',dest)
            s._audit(c, 'user', 'restore', f"{r['path']} v{vid}"); c.commit(); c.close(); return r['path']
        if chunks:
            with open(dest+'.tmp','wb') as out:
                for ch in chunks:
                    b=s.cloud.download('blocks/'+ch[0]); part=AESGCM(s.key).decrypt(b[:12],b[12:],None)
                    out.write(zlib.decompress(part[1:]) if part[0]==1 else part[1:])
            os.replace(dest+'.tmp',dest)
            s._audit(c, 'user', 'restore', f"{r['path']} v{vid}"); c.commit(); c.close(); return r['path']
        b = s.cloud.download(r['hash']); d = AESGCM(s.key).decrypt(b[:12], b[12:], None)
        d = zlib.decompress(d[1:]) if d[0] == 1 else d[1:]
        open(dest + '.tmp', 'wb').write(d); os.replace(dest + '.tmp', dest)
        s._audit(c, 'user', 'restore', f"{r['path']} v{vid}"); c.commit(); c.close(); return r['path']

    def stats(s):
        c = connect(s.db, ro=True); q = lambda x: c.execute(x).fetchone()[0]
        orig = q("select coalesce(sum(size),0) from versions"); stored = q("select coalesce(sum(stored_size),0) from blobs")
        by = {r[0]: r[1] for r in c.execute("select state,count(*) from files group by state")}
        out = dict(files=sum(by.values()), by_state=by, original=orig, stored=stored, saved=max(0, orig - stored),
                   dup_blobs=q("select count(*) from blobs where ref_count>1"), sensitive=q("select count(*) from files where sensitive=1"),
                   queue=len(s.tasks), active=s.active, throttled=s.throttled, policy=s.policy, cpu_limit=s.cpu_limit, metrics=s.metrics)
        c.close(); return out

class _Handler(FileSystemEventHandler):
    def __init__(s, e): s.e = e
    def on_any_event(s, ev):
        if ev.is_directory: return
        for p in {ev.src_path, getattr(ev, 'dest_path', '')}:
            if p and not os.path.basename(p).startswith('.') and not p.endswith('.tmp'):
                threading.Timer(1.0, s.e.submit, (p,)).start()  # debounce: let writes finish
