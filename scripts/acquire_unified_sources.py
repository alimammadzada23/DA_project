"""Acquire pinned public evaluation inputs without replacing existing files."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path

import httpx

from validate_unified import INITIAL,ROOT,SOURCES
from reproduce_public_cases import SOURCES as OLD


def fetch(target,url,expected):
    target.parent.mkdir(parents=True,exist_ok=True)
    if not target.exists():
        response=httpx.get(url,timeout=60,follow_redirects=False);response.raise_for_status()
        if len(response.content)>100*1024*1024:raise ValueError('Source exceeds 100 MiB.')
        partial=target.with_name(target.name+'.partial-'+datetime.now(timezone.utc).strftime('%H%M%S'))
        partial.write_bytes(response.content)
        if hashlib.sha256(response.content).hexdigest()!=expected:
            raise ValueError('Source checksum mismatch; existing files preserved and rejected bytes retained at '+str(partial))
        partial.rename(target)
    actual=hashlib.sha256(target.read_bytes()).hexdigest()
    if actual!=expected:raise ValueError('Existing source differs from its receipt; not replaced: '+str(target))
    return {'path':str(target.relative_to(ROOT)),'url':url,'sha256':actual,'bytes':target.stat().st_size,'verified_at':datetime.now(timezone.utc).isoformat()}


def main():
    receipts=[]
    for name,details in INITIAL.items():receipts.append(fetch(SOURCES/name,details['url'],details['sha256']))
    for name in ['flights.csv','airlines.csv']:
        receipts.append(fetch(ROOT/'data/real-cases'/name,OLD[name]['url'],OLD[name]['sha256']))
    receipts.append(fetch(SOURCES/'planes.csv',OLD['flights.csv']['url'].replace('flights.csv','planes.csv'),'95a3cd5a64dc1c4ae0c002e6aec457084455ea5835d73368114285f79a66a65c'))
    heldout=ROOT/'docs/unified-heldout-source.json'
    if heldout.exists():
        details=json.loads(heldout.read_text())
        receipts.append(fetch(SOURCES/details['filename'],details['url'],details['sha256']))
    out=ROOT/'outputs/unified-source-acquisition.json';out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(receipts,indent=2)+'\n')
    print(json.dumps(receipts,indent=2))

if __name__=='__main__':main()
