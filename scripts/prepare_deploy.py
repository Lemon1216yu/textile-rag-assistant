"""Generate fresh per-install secrets. Never overwrites an existing deployment."""
import argparse
import base64
import secrets
from pathlib import Path

import yaml

ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser()
parser.add_argument('--origin',required=True,help='For example https://knowledge.company.internal:8443')
args=parser.parse_args()
if not args.origin.startswith('https://') or '\n' in args.origin: raise SystemExit('HTTPS origin required')
out=ROOT/'deploy'/'secrets'; out.mkdir(parents=True,exist_ok=True)
if (out/'compose.env').exists() or (out/'app.env').exists(): raise SystemExit('配置已存在，不覆盖原有密钥')
values={key:secrets.token_urlsafe(24) for key in ['MYSQL_PASSWORD','MYSQL_ROOT_PASSWORD','REDIS_PASSWORD','MINIO_PASSWORD','MILVUS_PASSWORD','BOOTSTRAP_PASSWORD']}
(out/'compose.env').write_text('\n'.join(f'{k}={v}' for k,v in values.items())+'\nBIND_ADDRESS=127.0.0.1\nHTTPS_PORT=8443\n',encoding='utf-8')
app={
    'MODEL_CONFIG_ENCRYPTION_KEY':base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
    'APP_ENV':'production','PUBLIC_ORIGIN':args.origin,'COOKIE_SECURE':'true','ORGANIZATION':'织知企业工作空间',
    'DATABASE_URL':f"mysql+pymysql://zhizhi:{values['MYSQL_PASSWORD']}@mysql:3306/zhizhi?charset=utf8mb4",
    'REDIS_URL':f"redis://:{values['REDIS_PASSWORD']}@redis:6379/0",
    'MILVUS_URI':'http://milvus:19530','MILVUS_TOKEN':'root:'+values['MILVUS_PASSWORD'],'MILVUS_COLLECTION':'zhizhi_chunks_v1',
    'LLM_BASE_URL':'https://dashscope.aliyuncs.com/compatible-mode/v1','LLM_MODEL':'qwen3.5-flash','LLM_API_KEY':'',
    'EMBEDDING_BASE_URL':'https://dashscope.aliyuncs.com/compatible-mode/v1','EMBEDDING_MODEL':'text-embedding-v3','EMBEDDING_DIMENSION':'1024','EMBEDDING_API_KEY':'',
    'RERANK_URL':'','RERANK_MODEL':'gte-rerank-v2','RERANK_FORMAT':'dashscope','RERANK_API_KEY':'',
    'BOOTSTRAP_EMAIL':'admin@company.internal','BOOTSTRAP_PASSWORD':values['BOOTSTRAP_PASSWORD'],
}
(out/'app.env').write_text('\n'.join(f'{k}={v}' for k,v in app.items())+'\n',encoding='utf-8')
(out/'milvus.yaml').write_text(yaml.safe_dump({'common':{'security':{'authorizationEnabled':True,'defaultRootPassword':values['MILVUS_PASSWORD']}}}),encoding='utf-8')
(out/'tls').mkdir(exist_ok=True)
for file in out.glob('*'):
    if file.is_file(): file.chmod(0o600)
print('已生成独立安装配置。请在 deploy/secrets/app.env 填入百炼配置，并在 tls 目录放置企业证书。')
