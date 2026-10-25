import os, shutil, time, tempfile
from smartsync.engine import Engine, detect
from smartsync.db import connect
from smartsync.llm import validate
def test_all():
    d = tempfile.mkdtemp(); w = f'{d}/w'; os.makedirs(w)
    e = Engine(w, f'{d}/c', f'{d}/db', 'pw'); e.start()
    body = b'hello ' * 50000
    open(f'{w}/a.txt', 'wb').write(body); open(f'{w}/b.txt', 'wb').write(body); open(f'{w}/k.env', 'w').write('password: x')
    time.sleep(4); s = e.stats()
    assert s['files'] == 3 and s['dup_blobs'] == 1 and s['saved'] > 0 and s['sensitive'] == 1, s
    open(f'{w}/a.txt', 'wb').write(b'v2'); time.sleep(4)
    c = connect(e.db); fid = c.execute("select file_id from files where path='a.txt'").fetchone()[0]
    vs = [r[0] for r in c.execute("select version_id from versions where file_id=? order by 1", (fid,))]; assert len(vs) == 2
    e.restore(vs[0]); assert open(f'{w}/a.txt', 'rb').read() == body
    os.rename(f'{w}/b.txt', f'{w}/b2.txt'); time.sleep(4)
    assert c.execute("select count(*) from files where path='b2.txt'").fetchone()[0] == 1 and c.execute("select count(*) from files where path='b.txt'").fetchone()[0] == 0
    assert detect('B', 'B', 'C') == 'download' and detect('B', 'L', 'C') == 'conflict'
    for bad in ('drop table files', 'select 1; select 2', 'select * from audit_log'):
        try: validate(bad); assert False
        except ValueError: pass
    e.shutdown(); print('OK', s)
if __name__ == '__main__': test_all()
