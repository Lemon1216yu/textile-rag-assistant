"""In-process CPU inference, no external requests, pickle or downloadable code."""
from functools import lru_cache
import os
import threading
from pathlib import Path

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer
from .config import ROOT

lock=threading.Lock()

@lru_cache(maxsize=2)
def load(name):
    path=Path(os.getenv('LOCAL_MODELS_DIR',ROOT/'data/models'))/name
    tokenizer=Tokenizer.from_file(str(path/'tokenizer.json'))
    tokenizer.enable_truncation(max_length=512)
    pad='<pad>' if name=='bge-reranker-base' else '[PAD]'
    tokenizer.enable_padding(pad_id=tokenizer.token_to_id(pad),pad_token=pad)
    opt=ort.SessionOptions(); opt.intra_op_num_threads=4; opt.inter_op_num_threads=1
    return tokenizer,ort.InferenceSession(str(path/'onnx/model_quantized.onnx'),opt,providers=['CPUExecutionProvider'])

def infer(name,items):
    with lock:
        tokenizer,model=load(name)
        rows=tokenizer.encode_batch(items)
        attrs={'input_ids':'ids','attention_mask':'attention_mask','token_type_ids':'type_ids'}
        feeds={i.name:np.array([getattr(r,attrs[i.name]) for r in rows],dtype=np.int64) for i in model.get_inputs()}
        return model.run(None,feeds)[0]

def embeddings(texts):
    out=[]
    for i in range(0,len(texts),8):
        vectors=infer('bge-small-zh-v1.5',texts[i:i+8])[:,0,:]
        vectors/=np.maximum(np.linalg.norm(vectors,axis=1,keepdims=True),1e-12)
        out.extend(vectors.tolist())
    return out

def rerank(query,chunks):
    from .providers import ranking_passage
    scores=[]
    for i in range(0,len(chunks),2):
        pairs=[(query,ranking_passage(x)) for x in chunks[i:i+2]]
        logits=infer('bge-reranker-base',pairs).reshape(-1)
        scores.extend((1/(1+np.exp(-logits))).tolist())
    return [{**chunks[i],'rerank_score':scores[i]} for i in sorted(range(len(scores)),key=scores.__getitem__,reverse=True)]
