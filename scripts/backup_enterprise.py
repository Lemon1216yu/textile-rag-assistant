"""Offline-consistent backup. Run on the Linux Docker deployment host."""
import argparse
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',required=True,help='New directory on an encrypted backup disk')
    parser.add_argument('--project',default='zhizhi')
    args=parser.parse_args()
    out=Path(args.output).resolve()
    out.mkdir(parents=True,exist_ok=False)
    out.chmod(0o700)
    compose=['docker','compose','--project-name',args.project,'--env-file',str(ROOT/'deploy/secrets/compose.env'),'-f',str(ROOT/'deploy/compose.yml')]
    def run(*parts,**kwargs):
        return subprocess.run(compose+list(parts),check=True,**kwargs)
    # Stop all writers before taking SQL + original-document snapshots.
    run('stop','api','worker')
    try:
        with (out/'database.sql').open('wb') as stream:
            run('exec','-T','mysql','sh','-c','MYSQL_PWD="$MYSQL_PASSWORD" mysqldump -u zhizhi --single-transaction --no-tablespaces --set-gtid-purged=OFF --hex-blob zhizhi',stdout=stream)
        with (out/'documents.tar.gz').open('wb') as stream:
            run('run','--rm','--no-deps','-T','--entrypoint','tar','api','-czf','-','-C','/opt/zhizhi/data','enterprise-files',stdout=stream)
        hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in out.iterdir() if p.is_file()}
        (out/'manifest.json').write_text(json.dumps({'created':datetime.now(timezone.utc).isoformat(),'project':args.project,'sha256':hashes,'restore':'Restore to an isolated fresh project; rebuild Milvus using enterprise.cli rebuild-vectors.'},indent=2),encoding='utf-8')
        for p in out.iterdir(): p.chmod(0o600)
        print('Backup complete:',out)
    finally:
        run('start','api','worker')


if __name__=='__main__': main()
