"""Cloud storage adapters. MinIO support is optional (pip install minio)."""
import os

class LocalCloud:
    def __init__(self, root): self.root=root; os.makedirs(root, exist_ok=True)
    def upload(self, key, data):
        path=os.path.join(self.root,key); os.makedirs(os.path.dirname(path),exist_ok=True)
        with open(path+'.tmp','wb') as f: f.write(data)
        os.replace(path+'.tmp',path)
    def download(self,key):
        with open(os.path.join(self.root,key),'rb') as f: return f.read()
    def delete(self,key): os.remove(os.path.join(self.root,key))
    def list(self): return [f for f in os.listdir(self.root) if not f.endswith('.tmp')]

class MinioCloud:
    def __init__(self, endpoint, access_key, secret_key, bucket, secure=True):
        from minio import Minio
        self.client=Minio(endpoint,access_key=access_key,secret_key=secret_key,secure=secure); self.bucket=bucket
        if not self.client.bucket_exists(bucket): self.client.make_bucket(bucket)
    def upload(self,key,data):
        from io import BytesIO
        self.client.put_object(self.bucket,key,BytesIO(data),len(data))
    def download(self,key):
        r=self.client.get_object(self.bucket,key)
        try: return r.read()
        finally: r.close(); r.release_conn()
    def delete(self,key): self.client.remove_object(self.bucket,key)
    def list(self): return [o.object_name for o in self.client.list_objects(self.bucket)]

def create_cloud():
    if os.getenv('SS_STORAGE','local').lower()=='minio':
        return MinioCloud(os.getenv('SS_MINIO_ENDPOINT','localhost:9000'),os.environ['SS_MINIO_ACCESS_KEY'],os.environ['SS_MINIO_SECRET_KEY'],os.getenv('SS_MINIO_BUCKET','smartsync'),os.getenv('SS_MINIO_SECURE','true').lower()=='true')
    return LocalCloud(os.getenv('SS_CLOUD','cloud_store'))
