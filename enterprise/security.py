import hashlib
import secrets
import threading
import time
from contextlib import contextmanager

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError

PASSWORDS=PasswordHasher()
DUMMY=PASSWORDS.hash(secrets.token_urlsafe(24))


def hash_token(token): return hashlib.sha256(token.encode()).hexdigest()


def verify_password(encoded,password):
    try:
        return PASSWORDS.verify(encoded or DUMMY,password)
    except (VerificationError,InvalidHashError):
        return False


class Runtime:
    def __init__(self,settings):
        self.redis=None
        self.local_guard=threading.Lock()
        self.local_locks={}
        self.buckets={}
        if settings.redis_url:
            import redis
            self.redis=redis.Redis.from_url(settings.redis_url,decode_responses=True,socket_timeout=3)

    def rate_limit(self,key,limit=30,seconds=60):
        bucket=f'zhizhi:rate:{key}:{int(time.time())//seconds}'
        if self.redis:
            with self.redis.pipeline() as p:
                p.incr(bucket); p.expire(bucket,seconds+1)
                return p.execute()[0]<=limit
        with self.local_guard:
            now=time.time()
            self.buckets={k:v for k,v in self.buckets.items() if v[1]>now}
            count,_=self.buckets.get(bucket,(0,now+seconds))
            self.buckets[bucket]=(count+1,now+seconds)
            return count<limit

    @contextmanager
    def conversation_lock(self,sid):
        if self.redis:
            lock=self.redis.lock('zhizhi:lock:'+sid,timeout=300,blocking_timeout=1)
        else:
            with self.local_guard:
                entry=self.local_locks.setdefault(sid,{'lock':threading.Lock(),'refs':0})
                entry['refs']+=1
                lock=entry['lock']
        acquired=False
        try:
            acquired=lock.acquire(blocking=True) if self.redis else lock.acquire(timeout=1)
            if not acquired:
                raise RuntimeError('这个会话仍有请求处理中，请稍后重试')
            yield
        finally:
            if acquired:
                lock.release()
            if not self.redis:
                with self.local_guard:
                    entry['refs']-=1
                    if not entry['refs']:
                        self.local_locks.pop(sid,None)
