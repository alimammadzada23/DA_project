"""Friendly workspace controls over unchanged storage identities."""
from pathlib import Path
import hashlib
import os

import streamlit as st

from .store import Workspace
from .workspace_labels import catalog,metadata,option_labels,save_name,create_named


def choose_workspace():
    configured=os.environ.get('INSIGHTPILOT_UNIVERSAL_WORKSPACE')
    root=Path(os.environ.get('INSIGHTPILOT_WORKSPACE_ROOT','data/universal')).resolve()
    current=Path(configured).resolve() if configured else Path(st.session_state.get('universal-directory',root/'default')).resolve()
    existing=[] if configured else catalog(root)
    if not configured and not (current/'workspace.duckdb').is_file() and existing:
        current=existing[0].directory
    workspace=Workspace(current) if (current/'workspace.duckdb').is_file() else Workspace.create(current)
    peers=[workspace] if configured else catalog(root)
    if not peers:peers=[workspace]
    paths=[str(w.directory) for w in peers];labels=option_labels(peers)
    # Streamlit retains the combobox's visible label when only format_func's
    # output changes. Refresh that widget identity after a rename while keeping
    # the selected storage path and analytical context unchanged.
    selector_key='friendly-workspace-selector-'+hashlib.sha256(repr(sorted(labels.items())).encode()).hexdigest()[:12]
    pending=st.session_state.pop('workspace-selector-pending',None)
    if pending in paths:st.session_state[selector_key]=pending
    old=st.session_state.get(selector_key)
    if old not in paths:st.session_state.pop(selector_key,None)
    selected=st.sidebar.selectbox('Active project',paths,index=paths.index(str(current)) if str(current) in paths else 0,
        format_func=lambda path:labels[path] if metadata(Workspace(path))['status']=='named' else 'Unnamed project',key=selector_key,disabled=bool(configured))
    workspace=Workspace(selected);st.session_state['universal-directory']=selected
    item=metadata(workspace);tag=hashlib.sha256(selected.encode()).hexdigest()[:10]
    if item['description']:st.sidebar.text(item['description'])
    if not configured and st.sidebar.button('＋ New project',width='stretch'):
        st.session_state['ux-create-expanded']=True
        st.session_state['pending-view-layout']={'page':'Workspace Home','presets':{}}
        st.rerun()
    with st.sidebar.expander('Project settings'):
        with st.form('rename-friendly-workspace-'+tag):
            name=st.text_input('Project display name',item['name'])
            description=st.text_input('Description',item['description'],max_chars=240)
            purpose=st.text_input('Business purpose',item.get('purpose',''),max_chars=240)
            submitted=st.form_submit_button('Save project name')
        st.caption('Changes the name shown here; datasets and saved analyses stay in place.')
        if submitted:
            try:save_name(workspace,name,description,peers=peers,purpose=purpose);st.rerun()
            except ValueError as exc:st.warning(str(exc))
    with st.sidebar.expander('Project details'):
        st.caption('Stable identity: '+item['id'])
        st.code(str(workspace.directory),language=None)
        st.caption(f'{len(workspace.tables()):,} imported tables')
        if item.get('notice'):st.warning(item['notice'])
    return workspace
