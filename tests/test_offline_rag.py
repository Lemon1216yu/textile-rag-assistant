from types import SimpleNamespace

from enterprise.lexical import LexicalIndex


def test_saved_postings_respect_acl_and_replace_old_chunks(tmp_path):
    index=LexicalIndex(tmp_path)
    public=SimpleNamespace(id='public',title='标准')
    private=SimpleNamespace(id='private',title='研发')
    def row(i,text): return dict(id=i,section='要求',text=text)
    index.replace(private,[row('secret','内部 色牢度')])
    index.replace(public,[row('old','耐洗 色牢度')])
    reader=LexicalIndex(tmp_path)
    assert [i for i,_ in reader.search('色牢度',['public'])]==['old']
    index.replace(public,[row('new','耐磨 强度')])
    assert reader.search('色牢度',['public'])==[]
    assert reader.search('强度',['public'])[0][0]=='new'
