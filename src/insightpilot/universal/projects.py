"""Explicit project snapshots over the existing Workspace; never autosave a catalog entry.

Working copies live below .sessions, outside the one-level saved project catalog.
Save publishes a complete snapshot under a root lock; replaced/deleted snapshots
are retained in hidden history/trash. Sources are relative and stay project-local.
"""
from contextlib import contextmanager
from datetime import datetime,timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import uuid

from .store import Workspace
from .workspace_labels import catalog,metadata,save_name,valid_name,valid_description

SESSION='.insightpilot-session.json'
SAVED='.insightpilot-project.json'
CURRENT='project.current-analysis'


def atomic_json(path,value):
    temporary=path.with_name('.write-'+uuid.uuid4().hex)
    try:
        temporary.write_text(json.dumps(value,indent=2,default=str)+'\n',encoding='utf-8')
        os.replace(temporary,path)
    finally:
        temporary.unlink(missing_ok=True)


def files(directory):
    directory=Path(directory)
    if directory.is_symlink():raise ValueError('Project directories must not be symbolic links.')
    for p in sorted(directory.rglob('*')):
        if p.is_symlink():raise ValueError('Project snapshots do not follow symbolic links.')
        if p.is_file() and p.name!=SESSION and not p.name.startswith('.write-'):
            yield p


def signature(directory):
    return [[str(p.relative_to(directory)),p.stat().st_size,p.stat().st_mtime_ns] for p in files(directory)]


def digest(directory):
    h=hashlib.sha256()
    for p in files(directory):
        h.update(str(p.relative_to(directory)).encode());h.update(b'\0')
        with p.open('rb') as stream:
            for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


@contextmanager
def locked(root):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    with (root/'.project-lock').open('a') as stream:
        fcntl.flock(stream,fcntl.LOCK_EX)
        try:yield
        finally:fcntl.flock(stream,fcntl.LOCK_UN)


def private_storage(root,name):
    path=Path(root)/name
    if path.is_symlink():raise ValueError('Private project storage must not be a symbolic link.')
    path.mkdir(parents=True,exist_ok=True)
    return path


def saved_target(root,path):
    root=Path(root).resolve();raw=Path(path)
    if raw.is_symlink() or raw.resolve().parent!=root or not (raw/'workspace.duckdb').is_file():
        raise ValueError('Choose an existing saved project in this catalog.')
    return raw.resolve()


def saved_info(workspace):
    p=workspace.directory/SAVED
    if not p.exists():return {'legacy':True,'last_saved':None}
    try:
        value=json.loads(p.read_text())
        if not isinstance(value,dict) or value.get('format_version')!=1:raise ValueError
        return value|{'legacy':False}
    except (ValueError,OSError,TypeError):raise ValueError('Saved project metadata needs review; existing files were preserved.')


def copy_workspace(source,destination):
    # The existing Workspace read lock prevents an incomplete DB copy.
    # Source files and saved-object definitions use paths relative to the project.
    list(files(source.directory))
    with source.connect():
        if source.database.with_suffix('.duckdb.wal').exists():raise ValueError('Finish the other writer before taking a project snapshot.')
        shutil.copytree(source.directory,destination,ignore=shutil.ignore_patterns(SESSION,'*.wal','.write-*'))
    return Workspace(destination)


def delete_project(root,path):
    """Remove only a confirmed catalog entry, keeping a recoverable private snapshot."""
    with locked(root):
        target=saved_target(root,path)
        trash=private_storage(root,'.trash')
        destination=trash/uuid.uuid4().hex
        os.replace(target,destination)
        return destination


def publish_editor_drafts(workspace,ui):
    """Explicit project save includes complete current editors; incomplete work
    remains editable in CURRENT. Reuse report validation, never generate findings.
    """
    if not ui:return
    from .ui_deliverables import report_definition,SECTIONS
    drafts=ui.get('drafts',{})
    for prefix,editor in ui.get('editors',{}).items():
        if not prefix.startswith(('ux-report-{project}-','ux-investigation-input-{project}-')):continue
        def value(field,default=''):return drafts.get(prefix+field,default)
        title=value('title')
        if not isinstance(title,str) or not title.strip() or len(title)>80:continue
        if editor.get('kind')=='report':
            chosen=value('chosen',[])
            if not chosen:continue
            blocks=workspace.saved_objects('block')
            stored=workspace.saved_objects('layout').get(editor.get('selected'),{})
            blocks.update(stored.get('blocks',{}))
            positions=[value('position-'+name,index+1) for index,name in enumerate(chosen)]
            if len(set(positions))!=len(positions):continue
            order=[name for _,name in sorted(zip(positions,chosen))]
            try:definition=report_definition(title,{k:value(k) for k,_ in SECTIONS},order,blocks)
            except ValueError:continue  # Keep the unfinished editor snapshot instead.
            workspace.save_object('layout','ux.report:'+title.strip(),definition)
        elif editor.get('kind')=='investigation' and value('question').strip():
            from .report import replay
            try:replay(workspace,editor['analysis'])
            except (ValueError,KeyError):continue
            fields={field:value(field) for field in ('question','observations','associations','hypotheses','limitations','actions')}
            workspace.save_object('layout','ux.investigation:'+title.strip(),{
                'kind':'ux.investigation','format_version':1,'title':title.strip(),**fields,
                'evidence':value('evidence',[]),'analysis':editor['analysis'],
                'updated_at':datetime.now(timezone.utc).isoformat()})


class ProjectSession:
    def __init__(self,root,token):
        self.root=Path(root).resolve()
        if not isinstance(token,str) or not re.fullmatch('[a-f0-9]{32}',token):raise ValueError('Invalid temporary project session.')
        self.token=token;self.directory=self.root/'.sessions'/token
        if self.directory.is_symlink() or self.directory.resolve().parent!=self.root/'.sessions':raise ValueError('Invalid session directory.')
        self.state=json.loads((self.directory/SESSION).read_text())
        if not isinstance(self.state,dict) or self.state.get('format_version')!=1:raise ValueError('Unsupported temporary project session.')
        if self.state.get('target') and Path(self.state['target']).resolve().parent!=self.root:raise ValueError('Invalid saved project target.')
        self.workspace=Workspace(self.directory)
        if not self.workspace.database.exists():raise ValueError('Temporary project data is unavailable.')

    @classmethod
    def start(cls,root,source=None):
        root=Path(root).resolve();sessions=root/'.sessions'
        if sessions.is_symlink():raise ValueError('Temporary session storage must not be a symbolic link.')
        sessions.mkdir(parents=True,exist_ok=True)
        token=uuid.uuid4().hex;directory=sessions/token
        target=saved_target(root,source) if source and Path(source).resolve().parent==root else None
        if source:
            copy_workspace(Workspace(source),directory)
        else:Workspace.create(directory)
        state={'format_version':1,'target':str(target) if target else None,
               'base_digest':digest(target) if target else None,'baseline':signature(directory),
               'initial':True,'baseline_ui':None,'live_ui':None}
        atomic_json(directory/SESSION,state)
        return cls(root,token)

    def persist(self):atomic_json(self.directory/SESSION,self.state)

    @property
    def target(self):return Path(self.state['target']) if self.state.get('target') else None

    @property
    def is_new(self):return self.target is None

    def dirty(self,ui=None):
        if signature(self.directory)!=self.state['baseline']:return True
        current=ui if ui is not None else self.state.get('live_ui')
        before=self.state.get('baseline_ui')
        # Changing page alone is navigation, not an analytical edit.
        return {k:(current or {}).get(k) for k in ('analysis','presets','drafts')}!={k:(before or {}).get(k) for k in ('analysis','presets','drafts')}

    def has_work(self):
        return bool(self.workspace.tables() or self.dirty() or any(self.workspace.saved_objects(k) for k in ('view','block','layout','query')))

    def observe(self,ui=None):
        if self.state.get('initial'):
            # Initial default controls may initialize the *temporary* copy.
            self.state['baseline']=signature(self.directory)
            stored=self.workspace.saved_objects('layout').get(CURRENT)
            self.state['baseline_ui']=stored or ui
            self.state['initial']=False
        self.state['live_ui']=ui
        self.persist()

    def save(self,name,description='',purpose='',ui=None):
        name=valid_name(name);description=valid_description(description);purpose=valid_description(purpose)
        with locked(self.root):
            target=self.target
            if target:
                saved_target(self.root,target)
                if digest(target)!=self.state['base_digest']:
                    raise ValueError('This saved project changed in another session. Nothing was overwritten. Reopen its latest version before saving changes.')
            peers=[w for w in catalog(self.root) if w.directory!=target]
            if any(metadata(w)['name'].casefold()==name.casefold() for w in peers):raise ValueError('Another saved project has this name. Choose a distinct name; projects are never overwritten or merged.')
            if ui:
                publish_editor_drafts(self.workspace,ui)
                self.workspace.save_object('layout',CURRENT,ui)
            stage_root=private_storage(self.root,'.staging')
            stage=stage_root/uuid.uuid4().hex
            backup=None
            try:
                copied=copy_workspace(self.workspace,stage)
                save_name(copied,name,description,purpose=purpose,peers=peers)
                # Staging paths must never become the stable project identity.
                labels_path=stage/'.insightpilot-workspace.json'
                labels=json.loads(labels_path.read_text())
                labels['id']=metadata(Workspace(target) if target else self.workspace)['id']
                atomic_json(labels_path,labels)
                record={'format_version':1,'last_saved':datetime.now(timezone.utc).isoformat(),'revision':uuid.uuid4().hex}
                atomic_json(stage/SAVED,record)
                if target:
                    history=private_storage(self.root,'.history')
                    backup=history/uuid.uuid4().hex;os.replace(target,backup)
                else:target=self.root/uuid.uuid4().hex
                try:os.replace(stage,target)
                except Exception:
                    if backup:os.replace(backup,target)
                    raise
                # Keep editing the same working directory and preserve all widget IDs.
                for filename in ('.insightpilot-workspace.json',SAVED):shutil.copy2(target/filename,self.directory/filename)
                self.state.update(target=str(target),base_digest=digest(target),baseline=signature(self.directory),
                                  baseline_ui=ui,live_ui=ui,initial=False)
                self.persist()
                return Workspace(target)
            finally:
                if stage.exists():shutil.rmtree(stage)

    def discard(self):
        # Only this managed temporary copy is retired. Never delete the saved target.
        if self.directory.is_symlink():raise ValueError('Refusing to discard a symbolic link.')
        shutil.rmtree(self.directory)
