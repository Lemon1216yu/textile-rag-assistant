"""Provision public reference PDFs and pinned, hash-verified ONNX weights. Never runs on requests."""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
MODELS = {
    'bge-small-zh-v1.5': ('75c43b069aac4d136ba6bc1122f995fedcfd2781', {
        'onnx/model_quantized.onnx': 'sha256:15b717c382bcb518ba457b93ea6850ede7f4f1cd8937454aa06972366cd19bcc',
        'tokenizer.json': 'blob:cdb3043fc938fc918c06e66cf704c2ba58f88747'}),
    'bge-reranker-base': ('280bcc27a84e0b898c251e06fddb25171bd9b101', {
        'onnx/model_quantized.onnx': 'sha256:dd98f3e67837d23210a6b7550c08cced4f61845b940ac45be3565840a10f3244',
        'tokenizer.json': 'sha256:48564c5c7d3fa64d85d95e65414a542385f88b0f128fd8d4163fd7a57f2be05c'})}


def download(url, path, expected=''):
    path.parent.mkdir(parents=True, exist_ok=True)
    def valid(data):
        if not expected: return data.startswith(b'%PDF-')
        kind, digest = expected.split(':')
        actual = hashlib.sha256(data).hexdigest() if kind == 'sha256' else hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
        return actual == digest
    if path.exists() and valid(path.read_bytes()): return
    with httpx.Client(proxy=os.getenv('ASSET_PROXY') or None, follow_redirects=True, timeout=120) as client:
        with client.stream('GET', url) as response:
            response.raise_for_status()
            temporary = path.with_suffix(path.suffix+'.part')
            with temporary.open('wb') as f:
                for chunk in response.iter_bytes(): f.write(chunk)
    data = temporary.read_bytes()
    if not valid(data): raise ValueError('Asset verification failed: '+path.name)
    temporary.replace(path)
    print('Verified', path.relative_to(ROOT), len(data), flush=True)


def main():
    tasks = []
    for name, (revision, files) in MODELS.items():
        for filename, digest in files.items():
            tasks.append((f'https://huggingface.co/Xenova/{name}/resolve/{revision}/{filename}', ROOT/'data/models'/name/filename, digest))
    sources = json.loads((ROOT/'examples/public_sources.json').read_text('utf-8'))
    tasks.extend((s['url'], ROOT/'data/public-pdfs'/s['filename'], 'sha256:'+s['sha256']) for s in sources)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(download, *task) for task in tasks]
        for future in futures: future.result()


if __name__ == '__main__': main()
