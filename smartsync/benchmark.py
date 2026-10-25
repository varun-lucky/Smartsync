"""Deterministic SmartSync micro-benchmarks; run: python -m smartsync.benchmark."""
import hashlib, json, zlib
from .llm import rule_based

CASES=[]
for ext in ('pdf','jpg','png','txt','py','md','mp4','csv','docx'):
    name='python' if ext=='py' else ext
    for q in (f'show {name} files',f'find {name}s',f'list all {name} files'):
        CASES.append((q,(f"path like '%.{ext}'",)))
for amount,unit in ((2,'KB'),(5,'MB'),(1,'GB'),(10,'KB'),(20,'MB'),(3,'GB'),(7,'MB'),(8,'KB'),(9,'GB'),(12,'MB')):
    mult={'KB':1000,'MB':1000000,'GB':1000000000}[unit]
    CASES.append((f'files larger than {amount} {unit}',(f'size > {amount*mult}',)))
for q in ('files modified in the last month','files from the past month','files modified last week','files from the past week','files modified today'):
    CASES.append((q,('mtime >',)))
for q in ('sensitive files','files containing password','files with secrets','files containing api key'):
    CASES.append((q,('sensitive=1',)))
for q in ('deleted files','show deleted items','list deleted documents','find deleted entries'):
    CASES.append((q,("state='deleted'",)))

def scheduling():
    jobs=[9,2,5,1,8,3,4,6,2,7]
    def mean_completion(order):
        t=0; done=[]
        for n in order: t+=n; done.append(t)
        return round(sum(done)/len(done),2)
    return mean_completion(jobs),mean_completion(sorted(jobs))

def block_storage():
    body=(b'SmartSync repeated block benchmark payload. '*24000)
    files=[body,body]
    unique={}
    for data in files:
        for off in range(0,len(data),1<<20):
            block=data[off:off+(1<<20)]; digest=hashlib.sha256(block).hexdigest()
            if digest not in unique: unique[digest]=len(zlib.compress(block,6))+29 # nonce + GCM tag + marker
    original=sum(map(len,files)); stored=sum(unique.values())
    return original,stored,max(0,original-stored)

def run():
    cases=CASES; passed=0
    for q,expected in cases:
        sql=rule_based(q).lower()
        passed+=all(term in sql for term in expected)
    fcfs,sjf=scheduling(); original,stored,saved=block_storage()
    return {'text_to_sql_questions':len(cases),'text_to_sql_accuracy':round(passed/len(cases),3),
      'fcfs_mean_completion_units':fcfs,'sjf_mean_completion_units':sjf,
      'storage_original_bytes':original,'storage_stored_bytes':stored,'storage_saved_bytes':saved,
      'storage_saved_percent':round(saved/original*100,2),'duplicate_files':1,
      'note':'Deterministic micro-benchmark; scheduler uses fixed service-time units and storage estimates independently compressed unique blocks, without hardware throughput claims.'}

if __name__=='__main__': print(json.dumps(run(),indent=2))
