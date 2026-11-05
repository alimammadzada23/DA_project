"""Non-destructive display metadata and explicit delimiter feedback."""
import hashlib
from pathlib import Path
import pytest
from streamlit.testing.v1 import AppTest

from insightpilot.universal import Workspace
from insightpilot.universal.engine import Engine
from insightpilot.universal.metrics import Metric,save_metric
from insightpilot.universal.workspace_labels import metadata,identity,save_name,create_named,catalog,table_labels,METADATA
from insightpilot.universal.import_preview import parsing_preview

APP=str(Path(__file__).parents[1]/'app.py')


def test_existing_fallback_is_stable_and_never_shows_storage_path(tmp_path):
    w=Workspace.create(tmp_path/'a918f5152c7')
    first=metadata(w);second=metadata(Workspace(w.directory))
    assert first==second and first['name'].startswith('Untitled workspace')
    assert str(tmp_path) not in first['name'] and 'a918f5152c7' not in first['name']
    assert not (w.directory/METADATA).exists()


def test_renaming_changes_only_display_metadata_and_preserves_database(tmp_path):
    w=Workspace.create(tmp_path/'opaque-id');source=tmp_path/'input.csv';source.write_text('x\n10\n20\n')
    w.add_file(source,'facts');save_metric(w,'facts',Metric('Amount','sum("x")'))
    w.save_object('view','keep',{'version':w.info('facts')['current']})
    original=w.info('facts');digest=hashlib.sha256(w.database.read_bytes()).hexdigest();identifier=metadata(w)['id'];directory=w.directory
    save_name(w,'Operations Review','Quarterly planning')
    assert w.directory==directory and metadata(w)['id']==identifier
    assert hashlib.sha256(w.database.read_bytes()).hexdigest()==digest
    assert w.info('facts')==original and w.saved_objects('view')['keep']['version']==original['current']
    assert Engine(w,'facts').count()==2
    reopened=Workspace(directory);assert metadata(reopened)['name']=='Operations Review' and metadata(reopened)['description']=='Quarterly planning'


def test_named_creation_and_duplicate_names_preserve_existing_workspaces(tmp_path):
    first=create_named(tmp_path,'My Research','Observations')
    assert first.directory.name!='My Research' and metadata(first)['name']=='My Research'
    before={p.name for p in tmp_path.iterdir()}
    with pytest.raises(ValueError,match='already'):create_named(tmp_path,'my research')
    assert {p.name for p in tmp_path.iterdir()}==before
    second=create_named(tmp_path,'Second review')
    with pytest.raises(ValueError,match='already'):save_name(second,'MY RESEARCH',peers=catalog(tmp_path))
    assert metadata(first)['name']=='My Research' and metadata(second)['name']=='Second review'


@pytest.mark.parametrize('name',['','  ','x'*61,'a\nb','a\x00b','a\u202eb','..'])
def test_invalid_display_names_do_not_modify_metadata(tmp_path,name):
    w=Workspace.create(tmp_path/'w')
    with pytest.raises(ValueError):save_name(w,name)
    assert not (w.directory/METADATA).exists()


def test_unreadable_metadata_never_blocks_analytics_or_overwrites_file(tmp_path):
    w=Workspace.create(tmp_path/'w');p=w.directory/METADATA;p.write_text('opaque existing bytes')
    assert metadata(w)['status']=='invalid'
    with pytest.raises(ValueError):save_name(w,'Do not overwrite')
    assert p.read_text()=='opaque existing bytes' and w.tables()=={}


def test_repeated_filename_imports_get_clear_context_without_combining(tmp_path):
    w=Workspace.create(tmp_path/'w');source=tmp_path/'observations.csv';source.write_text('x\n1\n2\n')
    w.add_file(source,'records');w.add_file(source,'records_2');labels=table_labels(w)
    assert set(labels)=={'records','records_2'}
    assert 'import 1' in labels['records'] and 'import 2' in labels['records_2']
    assert 'observations.csv' in labels['records_2'] and Engine(w,'records').count()==2 and Engine(w,'records_2').count()==2


def test_delimiter_mismatch_warns_without_changing_imported_bytes(tmp_path):
    content=b'person;value;group\nA;10;X\nB;20;Y\nC;30;X\n'
    preview=parsing_preview(content,',')
    assert preview['field_count']==1 and preview['suggestions'][0]['delimiter']==';' and preview['warning']
    corrected=parsing_preview(content,';');assert corrected['field_count']==3 and corrected['warning'] is None
    w=Workspace.create(tmp_path/'w');p=tmp_path/'source.csv';p.write_bytes(content)
    wrong=w.add_file(p,'first',delimiter=',');right=w.add_file(p,'second',delimiter=';')
    assert wrong['rows']==right['rows']==3 and len(wrong['columns'])==1 and len(right['columns'])==3
    assert wrong['import_warnings'] and not right['import_warnings']
    assert p.read_bytes()==content
    assert (w.directory/wrong['source']['retained']).read_bytes()==content
    assert (w.directory/right['source']['retained']).read_bytes()==content


@pytest.mark.parametrize('content',[b'notes\nhello\nworld\n',b'notes\n"one;two"\n"three;four"\n',b'"header;with;punctuation"\n"one;two;three"\n"four;five;six"\n'])
def test_legitimate_single_column_is_not_reinterpreted(content):
    p=parsing_preview(content,',');assert p['field_count']==1 and p['warning'] is None and p['delimiter']==','


def test_empty_preview_has_no_fields_and_leaves_import_validation_in_charge():
    p=parsing_preview(b'',',')
    assert p['field_count']==0 and p['rows']==[] and p['warning'] is None


def test_friendly_names_survive_actual_streamlit_reruns(tmp_path,monkeypatch):
    root=tmp_path/'data/universal';w=create_named(root,'Initial name')
    monkeypatch.chdir(tmp_path);monkeypatch.delenv('INSIGHTPILOT_UNIVERSAL_WORKSPACE',raising=False)
    monkeypatch.setenv('INSIGHTPILOT_WORKSPACE_ROOT',str(root))
    app=AppTest.from_file(APP).run(timeout=30)
    box=next(s for s in app.selectbox if s.label=='Active project')
    assert 'Initial name' in box.options and not any('data/universal' in str(v) for v in box.options)
    next(t for t in app.text_input if t.label=='Project display name').set_value('Renamed review')
    next(b for b in app.button if b.label=='Save project name').click().run(timeout=30)
    assert not app.exception and metadata(w)['name']=='Renamed review'
    renamed=next(s for s in app.selectbox if s.label=='Active project')
    assert renamed.value==str(w.directory) and renamed.key!=box.key
    assert 'Renamed review' in renamed.options
    fresh=AppTest.from_file(APP).run(timeout=30)
    assert 'Renamed review' in next(s for s in fresh.selectbox if s.label=='Active project').options
    assert w.directory.exists()
