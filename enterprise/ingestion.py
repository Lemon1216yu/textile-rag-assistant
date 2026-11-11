import hashlib
import logging
import re
import time
from pathlib import Path

from sqlalchemy import delete, or_, select, update

from .db import Chunk, Document, IngestJob, IndexOptions
from .providers import Models, VectorStore

log=logging.getLogger('zhizhi.worker')


def parse_file(path):
    path=Path(path)
    suffix=path.suffix.lower()
    if suffix in {'.txt','.md'}:
        return [(1,path.read_text('utf-8-sig'))]
    if suffix=='.pdf':
        from pypdf import PdfReader
        reader=PdfReader(path)
        if reader.is_encrypted: raise ValueError('加密 PDF 无法导入')
        if len(reader.pages)>100: raise ValueError('单份 PDF 最多 100 页，请拆分文件')
        pages=[]
        for i,page in enumerate(reader.pages):
            try:
                text=page.extract_text(extraction_mode='layout') or ''
                text='\n'.join(re.sub(r'[ \t]{2,}', '  ', line.strip()) for line in text.splitlines() if line.strip())
            except KeyError:
                text=''
            if len(text.strip())<10: raise ValueError(f'第 {i+1} 页没有可提取文本，请先完成 OCR 或移除空白页')
            pages.append((i+1,text))
        return pages
    if suffix=='.docx':
        from docx import Document as Word
        import zipfile
        with zipfile.ZipFile(path) as z:
            if sum(x.file_size for x in z.infolist())>40*1024*1024:
                raise ValueError('DOCX 解压后体积超限')
        doc=Word(path)
        from docx.oxml.ns import qn
        from docx.table import Table
        from docx.text.paragraph import Paragraph
        lines=[]
        for element in doc.element.body:
            if element.tag==qn('w:p'):
                p=Paragraph(element,doc)
                lines.append(('# ' if p.style and p.style.name.startswith('Heading') else '')+p.text)
            elif element.tag==qn('w:tbl'):
                table=Table(element,doc)
                rows=[[c.text for c in r.cells] for r in table.rows]
                if rows:
                    headers=rows[0]
                    lines.extend('；'.join(f'{headers[i] if i<len(headers) else i}: {v}' for i,v in enumerate(row)) for row in rows[1:])
        return [(1,'\n'.join(lines))]  # Logical page; DOCX has no fixed pagination here.
    raise ValueError('支持 TXT、Markdown、文字 PDF 和 DOCX')


def validate_split(size,overlap):
    if not 100<=size<=480 or not 0<=overlap<=size//2:
        raise ValueError('每片长度须为 100–480 个字符；重叠须在 0 到片长的一半之间')


def split_pages(pages,title,size=400,overlap=56):
    validate_split(size,overlap)
    result=[]
    for page,text in pages:
        section=title
        buffer=[]
        def flush():
            value='\n'.join(buffer).strip()
            for start in range(0,len(value),size-overlap):
                part=value[start:start+size].strip()
                if part: result.append({'page':page,'section':section[:240],'text':part})
                if start+size>=len(value): break
            buffer.clear()
        for line in text.splitlines():
            if line.startswith('#'):
                flush(); section=line.lstrip('# ').strip()
            elif not line.strip() and sum(len(x) for x in buffer)>size:
                flush()
            else: buffer.append(line)
        flush()
    if not result or sum(len(c['text']) for c in result)<10:
        raise ValueError('文档没有足够的可索引文本')
    if len(result)>2000:
        raise ValueError('单份文档切片过多，请拆分导入')
    return result


class Ingestion:
    def __init__(self,db,settings):
        self.db=db; self.base_settings=settings; self.s=settings
        self.models=Models(settings); self.vectors=VectorStore(settings)

    def run_one(self):
        from .model_settings import ModelSettings
        self.s=ModelSettings(self.base_settings).effective()
        self.models=Models(self.s); self.vectors=VectorStore(self.s)
        now=time.time()
        with self.db.session() as s:
            exhausted=list(s.scalars(select(IngestJob).where(IngestJob.status=='processing',IngestJob.lease_until<now,IngestJob.attempts>=3)))
            for stale in exhausted:
                stale.status='failed'; stale.error='任务连续中断达到重试上限，请检查工作进程后手动重试'
                doc=s.get(Document,stale.doc_id)
                if doc and doc.status=='indexing':
                    doc.status='failed'; doc.error=stale.error; doc.updated=now
            job=s.scalar(select(IngestJob).where(or_(IngestJob.status=='queued',(IngestJob.status=='processing')&(IngestJob.lease_until<now)),IngestJob.attempts<3).order_by(IngestJob.created).limit(1))
            if not job: return False
            attempt=job.attempts
            claimed=s.execute(update(IngestJob).where(IngestJob.id==job.id,IngestJob.attempts==attempt,or_(IngestJob.status=='queued',IngestJob.lease_until<now)).values(status='processing',attempts=attempt+1,lease_until=now+900))
            if claimed.rowcount!=1: return True
            job_id=job.id; doc_id=job.doc_id
            doc=s.get(Document,doc_id)
            doc.status='indexing'; doc.updated=now
        try:
            with self.db.session() as s:
                doc=s.get(Document,doc_id)
                opts=s.get(IndexOptions,doc_id)
                size,overlap=(opts.chunk_size,opts.overlap) if opts else (400,56)
            path=Path(doc.path).resolve()
            root=Path(self.s.upload_dir).resolve()
            if not path.is_relative_to(root): raise ValueError('文件路径无效')
            if hashlib.sha256(path.read_bytes()).hexdigest()!=doc.checksum:
                raise ValueError('文件校验失败，请重新上传')
            chunks=split_pages(parse_file(path),doc.title,size,overlap)
            # Renew durable lease for every embedding batch; a dead worker is reclaimable.
            vectors=[]
            for offset in range(0,len(chunks),8):
                batch=chunks[offset:offset+8]
                part=self.models.embed([doc.title+'\n'+c['section']+'\n'+c['text'] for c in batch])
                vectors.extend(part or [[] for _ in batch])
                with self.db.session() as s:
                    job=s.get(IngestJob,job_id)
                    if job.attempts!=attempt+1: raise ValueError('任务租约已变更')
                    job.lease_until=time.time()+900
            for i,c in enumerate(chunks):
                c.update(id=f'{doc.id}-{i}',vector=vectors[i])
            if self.s.milvus_uri:
                if not vectors or not vectors[0]: raise ValueError('Milvus 索引需要有效语义向量')
                self.vectors.upsert(doc,chunks)
            from .lexical import LexicalIndex
            LexicalIndex(self.s.upload_dir).replace(doc,chunks)
            with self.db.session() as s:
                s.execute(delete(Chunk).where(Chunk.doc_id==doc_id))
                s.add_all(Chunk(doc_id=doc_id,**c) for c in chunks)
                doc=s.get(Document,doc_id)
                opts=s.get(IndexOptions,doc_id)
                if opts is None:
                    opts=IndexOptions(doc_id=doc_id,chunk_size=size,overlap=overlap); s.add(opts)
                opts.dimension=len(vectors[0]) if vectors and vectors[0] else None
                doc.chunks=len(chunks); doc.status='review'; doc.error=''; doc.updated=time.time()
                doc.embedding_model=self.s.embedding_model if (self.s.embedding_key or self.s.embedding_base=="local://onnx") else 'lexical-development'
                job=s.get(IngestJob,job_id); job.status='done'; job.error=''
            self.db.audit('worker','document.indexed',doc_id,{'chunks':len(chunks)})
        except Exception as e:
            message=str(e) if isinstance(e,ValueError) else '索引任务失败，请检查解析器、向量模型或 Milvus 服务后重试'
            with self.db.session() as s:
                doc=s.get(Document,doc_id); doc.status='failed'; doc.error=message; doc.updated=time.time()
                job=s.get(IngestJob,job_id); job.status='failed'; job.error=message
            self.db.audit('worker','document.failed',doc_id,{'error_type':type(e).__name__})
            log.warning('ingestion_failed job=%s type=%s',job_id,type(e).__name__)
        return True
