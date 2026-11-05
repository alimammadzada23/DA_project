"""Additive display metadata: never rename, move, combine or delete a workspace."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unicodedata
import uuid

from .store import Workspace,now

METADATA='.insightpilot-workspace.json'


def identity(workspace):return hashlib.sha256(str(workspace.directory).encode()).hexdigest()[:24]


def metadata(workspace):
    identifier=identity(workspace)
    fallback={'id':identifier,'name':'Untitled workspace · '+str(int(identifier[:8],16)%100_000).zfill(5),'description':'','purpose':'','status':'unnamed'}
    path=workspace.directory/METADATA
    if not path.exists():return fallback
    try:
        if path.stat().st_size>16_384:raise ValueError
        value=json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(value,dict) or value.get('format_version')!=1 or not isinstance(value.get('id'),str) or len(value['id'])!=24:raise ValueError
        name=valid_name(value['name']);description=valid_description(value.get('description',''))
        return {'id':value['id'],'name':name,'description':description,'purpose':valid_description(value.get('purpose','')),'status':'named'}
    except (ValueError,KeyError,TypeError,OSError):return fallback|{'status':'invalid','notice':'Display-name metadata could not be read. Data and saved analyses are still available; no file was changed.'}


def valid_name(value):
    if not isinstance(value,str) or any(unicodedata.category(c).startswith('C') for c in value):raise ValueError('Workspace names cannot contain control or hidden characters.')
    value=' '.join(unicodedata.normalize('NFKC',value).split())
    if not 1<=len(value)<=60 or value in ('.','..'):raise ValueError('Choose a workspace name of 1–60 visible characters.')
    return value


def valid_description(value):
    if not isinstance(value,str) or len(value)>240 or any(unicodedata.category(c).startswith('C') and c not in '\n\t' for c in value):raise ValueError('Descriptions may contain at most 240 ordinary characters.')
    return value.strip()


def catalog(root):
    root=Path(root)
    return [Workspace(p.parent) for p in sorted(root.glob('*/workspace.duckdb'))]


def save_name(workspace,name,description='',*,peers=(),purpose=None):
    name=valid_name(name);description=valid_description(description);old=metadata(workspace)
    if old['status']=='invalid':raise ValueError('The display metadata needs repair before renaming. The existing metadata file and all data have been preserved.')
    for peer in peers:
        if peer.directory!=workspace.directory and metadata(peer)['name'].casefold()==name.casefold():raise ValueError('Another workspace already uses that display name. Choose a distinct name; workspaces are never merged.')
    value={'format_version':1,'id':old['id'],'name':name,'description':description,'purpose':valid_description(purpose if purpose is not None else old.get('purpose','')),'updated_at':now()}
    # Atomic replacement affects only this feature's namespaced metadata file.
    with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',prefix='.workspace-display-',dir=workspace.directory,delete=False) as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2);stream.write('\n');temporary=stream.name
    os.replace(temporary,workspace.directory/METADATA)
    return metadata(workspace)


def create_named(root,name,description='',*,purpose=''):
    name=valid_name(name);description=valid_description(description);purpose=valid_description(purpose);peers=catalog(root)
    if any(metadata(p)['name'].casefold()==name.casefold() for p in peers):raise ValueError('Another workspace already uses that name. Choose a distinct name.')
    workspace=Workspace.create(Path(root)/uuid.uuid4().hex[:12])
    save_name(workspace,name,description,peers=peers,purpose=purpose)
    return workspace


def option_labels(workspaces):
    values={str(w.directory):metadata(w) for w in workspaces};counts={}
    for value in values.values():counts[value['name'].casefold()]=counts.get(value['name'].casefold(),0)+1
    return {key:value['name']+(' · '+hashlib.sha256(key.encode()).hexdigest()[:4] if counts[value['name'].casefold()]>1 else '') for key,value in values.items()}


def table_labels(workspace):
    """Stable table IDs remain values; repeated source imports get explicit context."""
    tables=workspace.tables();labels={}
    by_filename={}
    for key,info in sorted(tables.items(),key=lambda item:(item[1]['created_at'],item[0])):
        filename=info['source'].get('filename',key)
        group=by_filename.setdefault(filename,[]);group.append(key)
    for filename,names in by_filename.items():
        for index,key in enumerate(names,1):
            info=tables[key]
            title=info.get('display_name') or filename
            suffix=f' · import {index}' if len(names)>1 else ''
            labels[key]=f'{title}{suffix} · {info["rows"]:,} rows'
    return labels
