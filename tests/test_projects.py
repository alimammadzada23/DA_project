"""Explicit save/discard isolation, legacy preservation, and conflict safety."""
import hashlib
from pathlib import Path
import pytest
from insightpilot.universal.projects import ProjectSession,delete_project,digest
from insightpilot.universal.workspace_labels import catalog,metadata,create_named
from insightpilot.universal.engine import Engine
from insightpilot.universal.store import Workspace


def upload(session,tmp_path,name='facts'):
    p=tmp_path/'data.csv';p.write_text('region,amount\nNorth,10\nSouth,20\n')
    session.workspace.add_file(p,name)
    return p


def test_new_session_is_empty_and_never_enters_catalog_until_explicit_save(tmp_path):
    root=tmp_path/'projects';s=ProjectSession.start(root)
    assert not s.workspace.tables() and catalog(root)==[]
    p=upload(s,tmp_path)
    assert Engine(s.workspace,'facts').count()==2 and catalog(root)==[]
    s.workspace.save_object('view','Selection',{'meaning':'session evidence'})
    assert not catalog(root)
    saved=s.save('Revenue review')
    assert [metadata(w)['name'] for w in catalog(root)]==['Revenue review']
    assert saved.saved_objects('view')['Selection']['meaning']=='session evidence'
    assert (saved.directory/saved.info('facts')['source']['retained']).read_bytes()==p.read_bytes()


def test_discard_temporary_project_leaves_no_saved_card_and_preserves_upload(tmp_path):
    s=ProjectSession.start(tmp_path/'projects');p=upload(s,tmp_path);before=p.read_bytes()
    s.discard()
    assert not s.directory.exists() and not catalog(s.root)
    assert p.read_bytes()==before


def test_saved_edits_are_isolated_until_save_changes(tmp_path):
    root=tmp_path/'projects';s=ProjectSession.start(root);upload(s,tmp_path)
    saved=s.save('Project A');original=digest(saved.directory)
    edit=ProjectSession.start(root,saved.directory)
    edit.observe()
    edit.workspace.save_object('layout','ux.report:Draft',{'title':'Temporary edits'})
    assert edit.dirty() and not saved.saved_objects('layout')
    assert digest(saved.directory)==original
    edit.discard()
    assert digest(saved.directory)==original
    again=ProjectSession.start(root,saved.directory)
    again.workspace.save_object('layout','ux.report:Draft',{'title':'Keep these edits'})
    again.save('Project A')
    assert saved.saved_objects('layout')['ux.report:Draft']['title']=='Keep these edits'


def test_legacy_open_does_not_modify_any_original_bytes(tmp_path):
    root=tmp_path/'projects';legacy=Workspace.create(root/'legacy-id')
    p=tmp_path/'source.csv';p.write_text('x\n1\n2\n');legacy.add_file(p,'facts')
    before=digest(legacy.directory)
    session=ProjectSession.start(root,legacy.directory)
    assert digest(legacy.directory)==before
    session.workspace.transform('facts',{'kind':'trim','column':'x'})
    assert digest(legacy.directory)==before
    assert len(legacy.info('facts')['versions'])==1
    identifier=metadata(legacy)['id']
    session.save('Named legacy project')
    assert metadata(legacy)['id']==identifier


def test_delete_only_removes_selected_catalog_entry_and_keeps_recoverable_snapshot(tmp_path):
    root=tmp_path/'projects';first=ProjectSession.start(root);source=upload(first,tmp_path)
    one=first.save('One');other=ProjectSession.start(root);upload(other,tmp_path);two=other.save('Two')
    before=digest(two.directory);original_source=source.read_bytes()
    trash=delete_project(root,one.directory)
    assert not one.directory.exists() and (trash/'workspace.duckdb').exists()
    assert [metadata(w)['name'] for w in catalog(root)]==['Two']
    assert digest(two.directory)==before and source.read_bytes()==original_source


def test_two_editors_cannot_silently_overwrite_each_other(tmp_path):
    root=tmp_path/'projects';s=ProjectSession.start(root);upload(s,tmp_path);saved=s.save('Shared')
    one=ProjectSession.start(root,saved.directory);two=ProjectSession.start(root,saved.directory)
    one.workspace.save_object('view','First',{'value':1});one.save('Shared')
    two.workspace.save_object('view','Second',{'value':2})
    before=digest(saved.directory)
    with pytest.raises(ValueError,match='another session'):two.save('Shared')
    assert digest(saved.directory)==before and 'First' in saved.saved_objects('view')
    assert 'Second' not in saved.saved_objects('view')


def test_duplicate_names_and_outside_deletion_are_rejected(tmp_path):
    root=tmp_path/'projects';one=ProjectSession.start(root);one.save('Same')
    two=ProjectSession.start(root)
    with pytest.raises(ValueError,match='distinct'):two.save('same')
    outside=create_named(tmp_path/'other','Outside')
    with pytest.raises(ValueError):delete_project(root,outside.directory)
    assert outside.database.exists() and len(catalog(root))==1


def test_refresh_reopens_the_same_temporary_copy_without_saving_it(tmp_path):
    s=ProjectSession.start(tmp_path/'projects');upload(s,tmp_path)
    ui={'analysis':{'filters':[{'column':'region','operator':'eq','value':'North'}]},'drafts':{'note':'Work in progress'}}
    s.observe(ui)
    reopened=ProjectSession(s.root,s.token)
    assert reopened.state['live_ui']==ui and reopened.workspace.info('facts')['rows']==2
    assert catalog(s.root)==[]


def test_new_project_never_inherits_prior_dataset(tmp_path):
    s=ProjectSession.start(tmp_path/'projects');upload(s,tmp_path);s.save('Existing')
    new=ProjectSession.start(s.root)
    assert new.workspace.tables()=={} and new.workspace.saved_objects('view')=={}


def test_failed_snapshot_does_not_replace_saved_project(tmp_path,monkeypatch):
    import insightpilot.universal.projects as projects
    s=ProjectSession.start(tmp_path/'projects');upload(s,tmp_path);saved=s.save('Keep')
    before=digest(saved.directory)
    def failed(*args,**kwargs):raise OSError('Simulated full disk')
    monkeypatch.setattr(projects,'copy_workspace',failed)
    with pytest.raises(OSError):s.save('Keep')
    assert digest(saved.directory)==before


def test_failed_publish_restores_previous_snapshot(tmp_path,monkeypatch):
    import insightpilot.universal.projects as projects
    s=ProjectSession.start(tmp_path/'projects');upload(s,tmp_path);saved=s.save('Keep')
    before=digest(saved.directory);replace=projects.os.replace
    def fail_stage(source,target):
        if Path(source).parent.name=='.staging':raise OSError('Simulated publish failure')
        return replace(source,target)
    monkeypatch.setattr(projects.os,'replace',fail_stage)
    with pytest.raises(OSError):s.save('Keep')
    assert digest(saved.directory)==before


def test_temporary_storage_does_not_follow_symlinks(tmp_path):
    root=tmp_path/'projects';root.mkdir();outside=tmp_path/'outside';outside.mkdir()
    (root/'.sessions').symlink_to(outside,target_is_directory=True)
    with pytest.raises(ValueError,match='symbolic'):ProjectSession.start(root)
    assert list(outside.iterdir())==[]
