"""Durable FTS5 postings. Only the worker tokenizes and indexes documents."""
import re
import sqlite3
from pathlib import Path
from contextlib import closing
import jieba

def terms(text):
    stop={'的','了','是','和','与','或','在','我','你','请','一下','什么','怎么','如何','多少','有没有','这个','那个'}
    return [x.lower() for x in jieba.lcut(text) if x not in stop and re.fullmatch(r'\w+',x)]

class LexicalIndex:
    def __init__(self,root):
        self.path=Path(root).resolve()/'.indexes'/'lexical-v1.sqlite'
    def replace(self,doc,chunks):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with closing(sqlite3.connect(self.path,timeout=30)) as db, db:
            db.execute('CREATE VIRTUAL TABLE IF NOT EXISTS corpus USING fts5(id UNINDEXED, doc UNINDEXED, terms)')
            db.execute('DELETE FROM corpus WHERE doc=?',(doc.id,))
            db.executemany('INSERT INTO corpus VALUES(?,?,?)',[(c['id'],doc.id,' '.join(terms(doc.title+' '+c['section']+' '+c['text']))) for c in chunks])
    def search(self,query,docs):
        tokens=list(dict.fromkeys(terms(query)))[:96]
        if not docs or not tokens: return []
        if not self.path.exists(): raise ValueError('关键词索引尚未创建，请运行 rebuild-bm25')
        with closing(sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True,timeout=30)) as db:
            db.execute('CREATE TEMP TABLE allowed(doc TEXT PRIMARY KEY)')
            db.executemany('INSERT INTO allowed VALUES(?)',[(x,) for x in docs])
            return [(i,-v) for i,v in db.execute('SELECT id,rank FROM corpus WHERE corpus MATCH ? AND doc IN (SELECT doc FROM allowed) ORDER BY rank,id LIMIT 30',(' OR '.join('"'+x+'"' for x in tokens),))]
