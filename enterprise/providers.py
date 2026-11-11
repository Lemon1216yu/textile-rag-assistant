import json
import math
import re

import httpx


class ProviderError(RuntimeError): pass


def ranking_passage(chunk):
    # Specifications often put the fabric ID in the heading, outside the body.
    return '\n'.join(dict.fromkeys(s for s in (chunk.get('title',''),chunk.get('section',''),chunk['text']) if s))


class Models:
    def __init__(self,settings):
        self.s=settings

    def post(self,url,key,body,timeout=None):
        try:
            with httpx.Client(timeout=timeout or self.s.model_timeout,follow_redirects=False) as client:
                r=client.post(url,headers={'Authorization':'Bearer '+key},json=body)
                r.raise_for_status()
                return r.json()
        except (httpx.HTTPError,ValueError) as e:
            # Never return response body, credentials or document text in errors.
            code=getattr(getattr(e,'response',None),'status_code','unavailable')
            raise ProviderError(f'模型服务不可用（{code}），请联系管理员查看服务配置') from None

    def complete(self,system,payload,json_mode=False,max_tokens=None):
        if not self.s.llm_key:
            raise ProviderError('尚未配置生成模型')
        body={'model':self.s.llm_model,'messages':[{'role':'system','content':system},{'role':'user','content':json.dumps(payload,ensure_ascii=False)}],'temperature':self.s.llm_temperature,'max_tokens':max_tokens or self.s.llm_max_tokens}
        if self.s.llm_model.startswith('qwen3'):
            body['enable_thinking']=False
        if json_mode and self.s.llm_json_mode:
            body['response_format']={'type':'json_object'}
        result=self.post(self.s.llm_base.rstrip('/')+'/chat/completions',self.s.llm_key,body)
        try:
            text=result['choices'][0]['message']['content']
            if not isinstance(text,str) or not text.strip(): raise ValueError()
            return text,result.get('usage') or {}
        except (KeyError,IndexError,ValueError,TypeError):
            raise ProviderError('模型没有返回有效回答') from None

    def embed(self,texts):
        if self.s.embedding_base=='local://onnx':
            from .local_models import embeddings
            return embeddings(texts)
        if not self.s.embedding_key: return []
        vectors=[]
        for start in range(0,len(texts),8):
            batch=texts[start:start+8]
            result=self.post(self.s.embedding_base.rstrip('/')+'/embeddings',self.s.embedding_key,{'model':self.s.embedding_model,'input':batch,'dimensions':self.s.embedding_dimension},timeout=30)
            try:
                data=sorted(result['data'],key=lambda item:item['index'])
                part=[item['embedding'] for item in data]
                if len(part)!=len(batch) or any(len(v)!=self.s.embedding_dimension or not all(isinstance(x,(float,int)) and math.isfinite(x) for x in v) for v in part):
                    raise ValueError()
                vectors.extend(part)
            except (KeyError,TypeError,ValueError):
                raise ProviderError('向量模型返回格式或维度不一致') from None
        return vectors

    def rerank(self,query,chunks):
        if chunks and self.s.rerank_url=='local://onnx':
            from .local_models import rerank
            return rerank(query,chunks),True
        if not chunks or not self.s.rerank_key or not self.s.rerank_url:
            return chunks,False
        texts=[ranking_passage(c) for c in chunks]
        body={'model':self.s.rerank_model,'input':{'query':query,'documents':texts},'parameters':{'top_n':min(8,len(texts)),'return_documents':False}}
        if self.s.rerank_format=='compatible':
            body={'model':self.s.rerank_model,'query':query,'documents':texts,'top_n':min(8,len(texts))}
        response=self.post(self.s.rerank_url,self.s.rerank_key,body,timeout=30)
        try:
            results=response.get('output',response)['results']
            ordered=[]
            seen=set()
            for r in results:
                i=r['index']
                if not isinstance(i,int) or i<0 or i>=len(chunks) or i in seen: raise ValueError()
                seen.add(i)
                ordered.append({**chunks[i],'rerank_score':float(r['relevance_score'])})
            if not ordered: raise ValueError()
            return ordered,True
        except (KeyError,TypeError,ValueError):
            raise ProviderError('重排模型返回无效结果') from None


class VectorStore:
    def __init__(self,settings):
        self.s=settings
        self._client=None

    @property
    def client(self):
        if self._client is None and self.s.milvus_uri:
            from pymilvus import MilvusClient
            self._client=MilvusClient(uri=self.s.milvus_uri,token=self.s.milvus_token,timeout=15)
        return self._client

    def ensure(self):
        if not self.client: return
        if not self.client.has_collection(self.s.collection):
            self.client.create_collection(collection_name=self.s.collection,dimension=self.s.embedding_dimension,primary_field_name='id',id_type='string',max_length=48,vector_field_name='vector',metric_type='COSINE',consistency_level='Strong')

    def upsert(self,doc,chunks):
        if not self.client: return
        self.ensure()
        self.client.upsert(self.s.collection,[{'id':c['id'],'vector':c['vector'],'doc_id':doc.id,'department':doc.department,'fabric':doc.fabric} for c in chunks])

    def search(self,vector,department,fabric='',admin=False,allowed_doc_ids=None):
        if not self.client: return []
        expressions=[]
        if allowed_doc_ids is not None:
            if not allowed_doc_ids: return []
            expressions.append('doc_id in '+json.dumps(sorted(allowed_doc_ids)))
        if not admin: expressions.append('department in '+json.dumps(['共享',department],ensure_ascii=False))
        if fabric: expressions.append('fabric in '+json.dumps(['',fabric],ensure_ascii=False))
        result=self.client.search(collection_name=self.s.collection,data=[vector],filter=' and '.join(expressions),limit=40,output_fields=['doc_id'],search_params={'metric_type':'COSINE','params':{}})
        return [(str(x['id']),float(x['distance'])) for x in result[0]]
