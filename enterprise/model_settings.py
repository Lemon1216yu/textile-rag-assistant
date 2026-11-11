"""Atomic encrypted model overrides shared by API and ingestion workers."""
import ipaddress
import json
import os
import secrets
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
from filelock import FileLock

GROUPS={
    'chat':('llm_base','llm_model','llm_key','llm_temperature','llm_max_tokens','model_timeout','llm_json_mode'),
    'embedding':('embedding_base','embedding_model','embedding_key','embedding_dimension'),
    'rerank':('rerank_url','rerank_model','rerank_key','rerank_format'),
}
KEYS={'chat':'llm_key','embedding':'embedding_key','rerank':'rerank_key'}
ENDPOINTS={'chat':'llm_base','embedding':'embedding_base','rerank':'rerank_url'}


def validate_endpoint(value,production):
    if not value: return
    try:
        parsed=urlsplit(value)
        if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError()
        if parsed.scheme not in {'http','https'} or (production and parsed.scheme!='https'):
            raise ValueError()
        host=parsed.hostname.lower()
        if host in {'metadata.google.internal','metadata','instance-data'}:
            raise ValueError()
        try: address=ipaddress.ip_address(host)
        except ValueError: address=None
        if address and (address.is_link_local or address.is_multicast or address.is_unspecified):
            raise ValueError()
        if any(ord(c)<32 or c.isspace() for c in value): raise ValueError()
        parsed.port
    except ValueError:
        raise ValueError('接口地址无效：请填写完整 HTTPS 地址，不含密钥、查询参数或用户信息；开发环境可用 HTTP') from None


class ModelSettings:
    def __init__(self,base):
        self.base=base
        self.directory=Path(base.upload_dir)/'.model-settings'
        self.path=self.directory/'settings.enc'

    def _cipher(self,create=False):
        configured=os.getenv('MODEL_CONFIG_ENCRYPTION_KEY')
        if configured: return Fernet(configured.encode())
        key_path=self.directory/'encryption.key'
        if create and not key_path.exists():
            with key_path.open('xb') as f: f.write(Fernet.generate_key())
            key_path.chmod(0o600)
        return Fernet(key_path.read_bytes())

    def read(self):
        if not self.path.exists(): return {'revision':'environment','overrides':{}}
        try:
            return json.loads(self._cipher().decrypt(self.path.read_bytes()))
        except (InvalidToken,ValueError,OSError):
            raise ValueError('模型配置无法解密，请管理员恢复原加密密钥；没有回退到旧密钥') from None

    def effective(self):
        return replace(self.base,**self.read()['overrides'])

    def public(self):
        record=self.read(); effective=replace(self.base,**record['overrides'])
        return {'revision':record['revision'],'source':'workspace' if record['overrides'] else 'environment',
            'groups':{group:{**{k:getattr(effective,k) for k in fields if k!=KEYS[group]},
                             'key_configured':bool(getattr(effective,KEYS[group]))}
                      for group,fields in GROUPS.items()}}

    def candidate(self,group,values,key_action='keep',api_key='',record=None):
        if group not in GROUPS: raise ValueError('未知模型用途')
        if key_action not in {'keep','replace','clear'}: raise ValueError('密钥操作无效')
        if set(values)-set(GROUPS[group]) or KEYS[group] in values: raise ValueError('包含不支持的配置字段')
        record=record or self.read()
        current=replace(self.base,**record['overrides'])
        updated=dict(values)
        for name,value in updated.items():
            if name in {'llm_temperature'}:
                if type(value) not in {int,float} or not 0<=value<=2: raise ValueError('Temperature 应在 0–2 之间')
            elif name in {'llm_max_tokens','model_timeout','embedding_dimension'}:
                low,high={'llm_max_tokens':(128,16384),'model_timeout':(5,60),'embedding_dimension':(1,8192)}[name]
                if type(value)!=int or not low<=value<=high: raise ValueError(f'{name} 应在 {low}–{high} 之间')
            elif name=='llm_json_mode':
                if type(value)!=bool: raise ValueError('JSON 模式配置无效')
            else:
                if not isinstance(value,str) or not value.strip() or len(value)>500: raise ValueError('模型名称或地址不能为空，且不能超过 500 字符')
                updated[name]=value.strip().rstrip('/') if name in ENDPOINTS.values() else value.strip()
        endpoint=ENDPOINTS[group]
        if endpoint in updated and not (group in {'embedding','rerank'} and updated[endpoint]=='local://onnx'): validate_endpoint(updated[endpoint],self.base.production)
        if updated.get('rerank_format',current.rerank_format) not in {'dashscope','compatible'}: raise ValueError('不支持的重排协议')
        if endpoint in updated and updated[endpoint]!=getattr(current,endpoint) and getattr(current,KEYS[group]) and key_action=='keep':
            raise ValueError('更换接口地址时必须重新输入密钥或清除密钥，防止旧密钥被发送到新服务')
        if key_action=='replace':
            if not api_key.strip() or len(api_key)>4096 or any(c.isspace() for c in api_key): raise ValueError('请填写有效 API Key')
            updated[KEYS[group]]=api_key
        elif key_action=='clear': updated[KEYS[group]]=''
        candidate=replace(current,**updated)
        candidate.validate()
        return candidate,updated

    def save(self,group,values,revision,key_action,api_key,guard):
        self.directory.mkdir(parents=True,exist_ok=True)
        self.directory.chmod(0o700)
        with FileLock(str(self.directory/'write.lock'),timeout=10):
            record=self.read()
            if record['revision']!=revision: raise RuntimeError('配置已被其他管理员更新，请刷新后再保存')
            candidate,updated=self.candidate(group,values,key_action,api_key,record)
            guard(replace(self.base,**record['overrides']),candidate)
            record={'revision':secrets.token_hex(16),'overrides':{**record['overrides'],**updated}}
            blob=self._cipher(create=True).encrypt(json.dumps(record).encode())
            temp=self.directory/('settings-'+secrets.token_hex(8)+'.tmp')
            try:
                with temp.open('xb') as f:
                    f.write(blob); f.flush(); os.fsync(f.fileno())
                temp.chmod(0o600)
                os.replace(temp,self.path)
            finally:
                temp.unlink(missing_ok=True)
        return self.public()
