"""User-controlled working sessions and explicit project snapshot actions."""
from pathlib import Path
import hashlib
import json
import os

import streamlit as st

from .store import Workspace
from .projects import ProjectSession,SESSION,CURRENT,delete_project
from .workspace_labels import catalog,metadata,option_labels


def root_directory():return Path(os.environ.get('INSIGHTPILOT_WORKSPACE_ROOT','data/universal')).resolve()


def active_session():return ProjectSession(root_directory(),st.session_state['_project-token'])


def request_transition(kind,target=None,page=None):
    st.session_state['_project-transition']={'kind':kind,'target':str(target) if target else None,'page':page}
    st.rerun()


def _activate(session):
    st.session_state['_project-token']=session.token
    st.session_state['universal-directory']=str(session.directory)
    st.query_params['project_session']=session.token
    st.session_state['_project-restore']=True
    for key in ('_active-analysis','analysis-caption','ux-pending-table','explore-chart-type','explore-groupby','explore-top','_project-report-editor','_project-investigation-editor'):
        st.session_state.pop(key,None)


def _finish_transition(session):
    intent=st.session_state.pop('_project-transition')
    new=ProjectSession.start(session.root,intent['target'] if intent['kind']=='open' else None)
    session.discard()
    _activate(new)
    st.session_state['pending-view-layout']={'page':intent.get('page') or ('Data Hub' if intent['kind']=='new' else 'Workspace Home'),'presets':{}}
    st.rerun()


def _restore(session):
    if not st.session_state.pop('_project-restore',False):return
    current=session.state.get('live_ui') or session.workspace.saved_objects('layout').get(CURRENT)
    if not current:return
    analysis=current.get('analysis',{});table=analysis.get('table')
    if table not in session.workspace.tables():return
    info=session.workspace.info(table)
    if analysis.get('version')!=info['current']:return
    tag=hashlib.sha256(str(session.directory).encode()).hexdigest()[:8]
    state=tag+'-'+table+'-'+info['current']
    st.session_state['ux-pending-table']=table
    st.session_state['restored-analysis-'+state]=analysis
    from .ui_workflow import PAGES
    if current.get('page') in PAGES:st.session_state['universal-page']=current['page']
    for key,value in current.get('presets',{}).items():
        if key in ('explore-chart-type','explore-groupby','explore-top'):st.session_state[key]=value


@st.dialog('Save Project',dismissible=False)
def save_dialog(session):
    info=metadata(session.workspace)
    st.caption('Save a complete snapshot of this project: its datasets, preparation versions, saved analyses, investigations, and report drafts. Subsequent edits remain temporary until Save Changes.')
    with st.form('explicit-save-project'):
        name=st.text_input('Project name',info['name'] if info['status']=='named' else '',max_chars=60)
        purpose=st.text_input('Business purpose (optional)',info.get('purpose',''),max_chars=240)
        description=st.text_input('Project description (optional)',info['description'],max_chars=240)
        submit=st.form_submit_button('Save Project' if session.is_new else 'Save Changes',type='primary')
    if submit:
        try:
            session.save(name,description,purpose,ui=session.state.get('live_ui'))
            st.session_state.pop('_project-save-open',None)
            st.session_state['_project-picker-reset']=True
            st.rerun()
        except ValueError as exc:st.warning(str(exc))
    if st.button('Cancel',key='cancel-project-save'):
        st.session_state.pop('_project-save-open',None)
        st.session_state.pop('_project-transition',None)
        st.session_state['_project-picker-reset']=True
        st.rerun()


@st.dialog('Leave this project?',dismissible=False)
def leave_dialog(session):
    st.warning('This project has not been saved, or has unsaved changes. Choose what to do before leaving.')
    a,b,c=st.columns(3)
    if a.button('Save Project',type='primary',width='stretch',key='leave-save'):
        st.session_state['_project-save-open']=True;st.rerun()
    if b.button('Discard Project',width='stretch',key='leave-discard'):_finish_transition(session)
    if c.button('Cancel',width='stretch',key='leave-cancel'):
        st.session_state.pop('_project-transition',None)
        st.session_state['_project-picker-reset']=True
        st.rerun()


@st.dialog('Delete Project?',dismissible=False)
def delete_dialog(session,target):
    info=metadata(Workspace(target))
    st.warning('Delete “'+info['name']+'” from Saved Projects?')
    st.write('This removes its saved snapshot from the list. Original uploaded files, shared datasets, and unrelated projects are not deleted. A recoverable private copy is kept in project Trash.')
    if session.target==Path(target):st.caption('Unsaved edits in this project will also be discarded if you confirm.')
    if st.button('Confirm Delete',type='primary'):
        delete_project(session.root,target)
        st.session_state.pop('_project-delete-target',None)
        if session.target==Path(target):
            session.discard();_activate(ProjectSession.start(session.root))
            st.session_state['pending-view-layout']={'page':'Workspace Home','presets':{}}
        st.session_state['_project-picker-reset']=True
        st.rerun()
    if st.button('Cancel',key='cancel-project-delete'):
        st.session_state.pop('_project-delete-target',None);st.rerun()


def choose_workspace():
    root=root_directory();configured=os.environ.get('INSIGHTPILOT_UNIVERSAL_WORKSPACE')
    if '_project-token' not in st.session_state:
        token=st.query_params.get('project_session')
        if token:
            try:session=ProjectSession(root,token)
            except (ValueError,OSError):
                st.error('This temporary session is unavailable. Saved projects have not been changed. Remove project_session from the address to start a new session.')
                st.stop()
        elif configured and (Path(configured)/SESSION).exists():session=ProjectSession(root,Path(configured).name)
        else:session=ProjectSession.start(root,configured)
        _activate(session)
    session=active_session()
    if not session.state.get('initial') and current_ui(session.workspace) is not None:session.observe(current_ui(session.workspace))
    if st.session_state.get('_project-save-open'):
        st.session_state['_project-widget-page']=None
        save_dialog(session);st.stop()
    if st.session_state.get('_project-transition'):
        if session.has_work() and (session.is_new or session.dirty()):
            st.session_state['_project-widget-page']=None
            leave_dialog(session);st.stop()
        _finish_transition(session)
    if st.session_state.get('_project-delete-target'):
        st.session_state['_project-widget-page']=None
        delete_dialog(session,st.session_state['_project-delete-target']);st.stop()
    workspace=session.workspace
    _restore(session)
    _restore_drafts(session)
    if st.session_state.get('universal-page')=='Explore' and st.session_state.get('_project-widget-page')!='Explore':
        for key,value in (session.state.get('live_ui') or {}).get('presets',{}).items():
            if key in ('explore-chart-type','explore-groupby','explore-top'):st.session_state[key]=value
    st.session_state['_project-unsaved']=session.is_new or session.dirty()
    peers=catalog(root);labels=option_labels(peers)
    paths=[str(p.directory) for p in peers]
    current=str(session.target) if session.target and str(session.target) in paths else '__current__'
    if current=='__current__':paths.insert(0,current);labels[current]='Unsaved project'
    selector_key='project-picker-'+session.token+'-'+hashlib.sha256(repr(sorted(labels.items())).encode()).hexdigest()[:8]
    if st.session_state.pop('_project-picker-reset',False):st.session_state[selector_key]=current
    def changed():
        selection=st.session_state[selector_key]
        if selection!=current and selection!='__current__':st.session_state['_project-transition']={'kind':'open','target':selection}
    st.sidebar.selectbox('Active project',paths,index=paths.index(current),format_func=labels.get,key=selector_key,on_change=changed)
    if st.sidebar.button('＋ New project',width='stretch'):request_transition('new')
    actions=st.sidebar.container()
    st.sidebar.caption('Save only when you choose.')
    return workspace,actions


def current_ui(workspace):
    active=st.session_state.get('_active-analysis')
    if not active or active[0].workspace.directory!=workspace.directory:return None
    from .report import configuration
    engine,metric=active
    analysis=configuration(engine,metric);analysis.pop('generated_at',None)
    from .workspace_labels import identity
    tag=identity(workspace)
    drafts=dict((active_session().state.get('live_ui') or {}).get('drafts',{}))
    for key,value in st.session_state.items():
        family='Reports' if key.startswith('ux-report-'+tag+'-') else 'Investigate' if key.startswith('ux-investigation-input-'+tag+'-') else None
        if family and family==st.session_state.get('_project-widget-page') and not key.endswith('-preview'):
            if isinstance(value,(str,int,float,bool,list,tuple)):
                normalized=key.replace(tag,'{project}',1)
                if value or normalized in drafts:drafts[normalized]=value
    editors=dict((active_session().state.get('live_ui') or {}).get('editors',{}))
    for key in ('_project-report-editor','_project-investigation-editor'):
        editor=st.session_state.get(key)
        if isinstance(editor,dict) and editor.get('directory')==str(workspace.directory):
            editors[editor['prefix']]={k:v for k,v in editor.items() if k!='directory'}
    presets=dict((active_session().state.get('live_ui') or {}).get('presets',{}))
    if st.session_state.get('_project-widget-page')=='Explore':
        presets.update({key:st.session_state[key] for key in ('explore-chart-type','explore-groupby','explore-top') if key in st.session_state})
    value={'editors':editors,'drafts':drafts,'analysis':analysis,'page':st.session_state.get('universal-page','Workspace Home'),'presets':presets}
    return json.loads(json.dumps(value,default=str))


def _restore_drafts(session):
    from .workspace_labels import identity
    for key,value in (session.state.get('live_ui') or session.workspace.saved_objects('layout').get(CURRENT) or {}).get('drafts',{}).items():
        if not key.startswith(('ux-report-{project}-','ux-investigation-input-{project}-')):continue
        current=key.replace('{project}',identity(session.workspace),1)
        family='Reports' if key.startswith('ux-report-') else 'Investigate'
        if current not in st.session_state or st.session_state.get('_project-widget-page')!=family:
            st.session_state[current]=tuple(value) if current.endswith('-membership') else value


def project_actions(workspace):
    session=active_session()
    ui=current_ui(workspace)
    session.observe(ui)
    dirty=session.dirty(ui)
    st.session_state['_project-unsaved']=session.is_new or dirty
    name=metadata(workspace)
    with st.container(key='project-actions'):
        st.caption('NOT SAVED' if session.is_new else 'UNSAVED CHANGES' if dirty else 'ALL CHANGES SAVED')
        st.markdown('<span data-project-unsaved="'+('true' if session.is_new or dirty else 'false')+'" hidden></span>',unsafe_allow_html=True)
        if session.is_new:
            if st.button('Save Project',type='primary',width='stretch'):
                st.session_state['_project-save-open']=True;st.rerun()
            if st.button('Discard Project',width='stretch'):request_transition('new',page='Workspace Home')
        else:
            if st.button('Save Changes',type='primary',width='stretch'):
                session.save(name['name'],name['description'],name.get('purpose',''),ui=ui);st.rerun()
            left,right=st.columns(2)
            if left.button('Rename',width='stretch'):
                st.session_state['_project-save-open']=True;st.rerun()
            if right.button('Delete',width='stretch',key='delete-active-project'):
                st.session_state['_project-delete-target']=str(session.target);st.rerun()
