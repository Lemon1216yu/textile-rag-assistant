import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_env():
    file = ROOT / '.env.enterprise'
    if file.exists():
        for line in file.read_text('utf-8-sig').splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                key,value=line.split('=',1)
                os.environ.setdefault(key.strip(),value.strip().strip('"').strip("'"))


@dataclass
class Settings:
    mode: str = 'development'
    database_url: str = 'sqlite:///'+str(ROOT/'data'/'enterprise.db').replace('\\','/')
    redis_url: str = ''
    milvus_uri: str = ''
    milvus_token: str = ''
    collection: str = 'zhizhi_chunks_v1'
    llm_base: str = 'https://dashscope.aliyuncs.com/compatible-mode/v1'
    llm_model: str = 'qwen3.5-flash'
    llm_key: str = ''
    llm_temperature: float = 0.1
    llm_max_tokens: int = 1600
    model_timeout: int = 40
    llm_json_mode: bool = True
    embedding_model: str = 'text-embedding-v3'
    embedding_key: str = ''
    embedding_base: str = 'https://dashscope.aliyuncs.com/compatible-mode/v1'
    embedding_dimension: int = 1024
    rerank_model: str = 'gte-rerank-v2'
    rerank_url: str = ''
    rerank_key: str = ''
    rerank_format: str = 'dashscope'
    public_origin: str = 'http://127.0.0.1:8766'
    secure_cookie: bool = False
    upload_dir: str = str(ROOT/'data'/'enterprise-files')
    session_hours: int = 8
    memory_hours: int = 24
    context_chars: int = 18000
    local_evidence_min: float = 0.08
    max_corpus_chunks: int = 50000
    organization: str = '织知工作空间'

    @property
    def production(self):
        return self.mode=='production'

    def validate(self):
        if not 0<=self.local_evidence_min<=1: raise ValueError('LOCAL_EVIDENCE_MIN 应为 0–1')
        if self.mode not in {'development','test','production'}:
            raise ValueError('Invalid APP_ENV')
        if self.embedding_base=='local://onnx' and (self.embedding_model!='bge-small-zh-v1.5' or self.embedding_dimension!=512):
            raise ValueError('本地向量模型固定为 bge-small-zh-v1.5，维度为 512')
        if self.rerank_url=='local://onnx' and self.rerank_model!='bge-reranker-base':
            raise ValueError('本地精排固定为 bge-reranker-base')
        if self.production:
            required={'MySQL':self.database_url.startswith('mysql+pymysql://'), 'Redis':bool(self.redis_url),
                      'Milvus':bool(self.milvus_uri and self.milvus_token), 'Qwen':bool(self.llm_key),
                      'Embedding':bool(self.embedding_key or self.embedding_base=='local://onnx'), 'Rerank':bool((self.rerank_key and self.rerank_url) or self.rerank_url=='local://onnx'),
                      'HTTPS':self.public_origin.startswith('https://') and self.secure_cookie}
            missing=[k for k,v in required.items() if not v]
            if missing:
                raise ValueError('生产配置缺失：'+', '.join(missing))
        if self.rerank_format not in {'dashscope','compatible'}:
            raise ValueError('Invalid rerank format')

    @classmethod
    def from_env(cls):
        load_env()
        s=cls()
        fields={'mode':'APP_ENV','database_url':'DATABASE_URL','redis_url':'REDIS_URL','milvus_uri':'MILVUS_URI','milvus_token':'MILVUS_TOKEN','collection':'MILVUS_COLLECTION',
                'llm_base':'LLM_BASE_URL','llm_model':'LLM_MODEL','llm_key':'LLM_API_KEY','embedding_model':'EMBEDDING_MODEL','embedding_key':'EMBEDDING_API_KEY','embedding_base':'EMBEDDING_BASE_URL',
                'rerank_model':'RERANK_MODEL','rerank_url':'RERANK_URL','rerank_key':'RERANK_API_KEY','rerank_format':'RERANK_FORMAT','public_origin':'PUBLIC_ORIGIN','upload_dir':'UPLOAD_DIR','organization':'ORGANIZATION'}
        for field,env in fields.items():
            if env in os.environ:
                setattr(s,field,os.environ[env])
        s.local_evidence_min=float(os.getenv('LOCAL_EVIDENCE_MIN',s.local_evidence_min))
        s.embedding_dimension=int(os.getenv('EMBEDDING_DIMENSION',s.embedding_dimension))
        if os.getenv('LOCAL_RAG_MODELS')=='1':
            s.embedding_base=s.rerank_url='local://onnx'
            s.embedding_model='bge-small-zh-v1.5'; s.embedding_dimension=512
            s.rerank_model='bge-reranker-base'
        s.secure_cookie=os.getenv('COOKIE_SECURE','false').lower()=='true'
        from .model_settings import ModelSettings
        s=ModelSettings(s).effective()
        s.validate()
        return s
