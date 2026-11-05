"""Workflow presentation over the existing workspace, profile and Engine services."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

import pandas as pd
import streamlit as st

from .engine import Engine, frame_of
from .profile import numeric_status
from .store import Workspace
from .workspace_labels import catalog, create_named, metadata, table_labels

PRIMARY = ['Workspace Home','Data Hub','Quality & Preparation','Explore','Investigate','Reports']
TOOLS = ['Overview','AI Analyst','SQL Lab','Advanced']
PAGES = PRIMARY + TOOLS


def scope_filters(filters):
    labels={'eq':'=','ne':'≠','gt':'>','gte':'≥','lt':'<','lte':'≤','from':'from','through':'through','is_missing':'is missing','not_missing':'is not missing'}
    descriptions=[]
    for item in filters:
        column=item['column'];operator=item['operator'];value=item.get('value')
        label=labels.get(operator.removesuffix('_numeric'),operator)
        descriptions.append(column+' '+label+('' if operator in ('is_missing','not_missing') else ' '+('Missing' if value is None else str(value))))
    return '; '.join(descriptions) or 'No row filters'


def go(page, *, table=None, presets=None):
    if page not in PAGES:raise ValueError('Choose an available workspace page.')
    st.session_state['pending-view-layout']={'page':page,'presets':presets or {}}
    if table:
        st.session_state['ux-pending-table']=table
        st.session_state.pop('ux-last-import-'+st.session_state.get('universal-directory',''),None)
    st.rerun()


def navigation(workspace):
    item=metadata(workspace)
    st.title(item['name'])
    if item['description']:st.text(item['description'])
    st.session_state.setdefault('universal-page','Workspace Home')
    with st.container(key='workflow-nav'):
        page=st.radio('Analysis workflow',PAGES,key='universal-page',horizontal=True,label_visibility='collapsed')
    st.caption('Select a stage to continue. Your dataset and shared filters stay active across the workflow.')
    return page


def project_summary(workspace):
    tables=workspace.tables();layouts=workspace.saved_objects('layout')
    reports=sum(k.startswith('ux.report:') for k in layouts)
    briefs=sum(k.startswith('ux.investigation:') for k in layouts)
    blocks=len(workspace.saved_objects('block'));views=len(workspace.saved_objects('view'))
    stage='Report draft saved' if reports else 'Investigation saved' if briefs else 'Evidence saved' if blocks else 'Analysis saved' if views else 'Data imported' if tables else 'Ready for data'
    activity=datetime.fromtimestamp((workspace.directory/'workspace.duckdb').stat().st_mtime,timezone.utc).strftime('%d %b %Y, %H:%M UTC')
    return {'tables':tables,'reports':reports,'investigations':briefs,'blocks':blocks,'views':views,'stage':stage,'activity':activity}


def home(workspace):
    st.subheader('Your analytical workspace')
    st.write('Bring your data, investigate an observed problem, and build a report you can defend.')
    configured=bool(os.environ.get('INSIGHTPILOT_UNIVERSAL_WORKSPACE'))
    root=Path(os.environ.get('INSIGHTPILOT_WORKSPACE_ROOT','data/universal')).resolve()
    peers=[workspace] if configured else catalog(root)
    recent=workspace if workspace.tables() else next((p for p in sorted(peers,key=lambda p:(p.directory/'workspace.duckdb').stat().st_mtime,reverse=True) if p.tables()),None)
    a,b,c=st.columns(3)
    if a.button('Upload dataset',type='primary',width='stretch'):go('Data Hub')
    if b.button('Continue recent analysis',width='stretch'):
        if recent:
            st.session_state['universal-directory']=str(recent.directory)
            st.session_state['workspace-selector-pending']=str(recent.directory)
        go('Explore' if recent else 'Data Hub')
    if c.button('View saved reports',width='stretch'):go('Reports')
    with st.expander('Create new analysis project',expanded=not any(p.tables() for p in peers) and not configured):
        if configured:st.caption('This session is bound to an existing workspace. Its datasets and saved work remain in place.')
        else:
            with st.form('ux-create-project'):
                name=st.text_input('Project name',placeholder='Give this investigation a recognizable name')
                purpose=st.text_input('Business purpose (optional)',max_chars=240,placeholder='The question you want to understand')
                create=st.form_submit_button('Create analysis project',type='primary')
            if create:
                try:
                    created=create_named(root,name,purpose)
                    st.session_state['universal-directory']=str(created.directory)
                    st.session_state['workspace-selector-pending']=str(created.directory)
                    go('Data Hub')
                except ValueError as exc:st.warning(str(exc))
    st.subheader('Projects & saved work')
    query=st.text_input('Find a project',placeholder='Search by name or purpose')
    visible=[p for p in peers if query.casefold() in (metadata(p)['name']+' '+metadata(p)['description']).casefold()]
    if not visible:st.info('No project matches that search. Create a project or clear the search.')
    # Bounded cards; the selector still exposes every workspace.
    if len(visible)>12:st.caption('Showing the first 12 matching projects. Narrow the search or use the workspace selector for others.')
    columns=st.columns(2)
    for index,peer in enumerate(visible[:12]):
        info=metadata(peer);summary=project_summary(peer);tag=info['id']
        with columns[index%2].container(border=True,key='ux-project-'+tag):
            st.text(info['name']);st.caption(info['description'] or 'Add the business purpose in Workspace settings when you are ready.')
            st.write(f'{len(summary["tables"]):,} datasets · {summary["blocks"]:,} saved evidence · {summary["reports"]:,} report drafts')
            st.caption('Stage: '+summary['stage']+' · Activity: '+summary['activity'])
            st.text('Datasets: '+(', '.join(t.get('display_name') or t['source'].get('filename',name) for name,t in summary['tables'].items()) or 'No data imported yet'))
            left,right=st.columns(2)
            if left.button('Open project',key='ux-open-'+tag,width='stretch'):
                st.session_state['universal-directory']=str(peer.directory)
                st.session_state['workspace-selector-pending']=str(peer.directory)
                go('Data Hub' if not summary['tables'] else 'Explore')
            if right.button('Saved work',key='ux-saved-'+tag,width='stretch'):
                st.session_state['universal-directory']=str(peer.directory)
                st.session_state['workspace-selector-pending']=str(peer.directory)
                go('Reports')
    if any(t['source']['origin']=='synthetic' for t in workspace.tables().values()):
        st.warning('SYNTHETIC DATA · This project contains software-validation data; no business findings are implied.')


def active_scope(engine,metric):
    with st.container(border=True,key='active-scope'):
        st.caption('ACTIVE ANALYSIS SCOPE')
        left,right=st.columns([5,1])
        left.text(f'{engine.info["source"].get("filename",engine.table)} · {engine.count():,} selected / {engine.info["rows"]:,} version rows · {metric.name} ({metric.units})')
        if right.button('Adjust filters',width='stretch'):
            st.session_state['ux-show-filters']=True;st.rerun()
        filters=scope_filters([{'column':f.column,'operator':f.operator,'value':f.value} for f in engine.filters])
        st.caption(filters+' · Date basis: '+(engine.context.date_basis or 'not selected')+' · Version '+engine.info['current'][-10:])
        st.caption('Change the dataset, shared filters or metric in the sidebar. Each chart and saved finding retains its own explicit scope.')


def quality_findings(report,info):
    review=[];optional=[];usable=[];rows=report['rows']
    for item in report['columns']:
        name=item['column'];present=rows-item['missing']
        if item['missing']:review.append({'Field':name,'Review':'Missing values','Details':f'{item["missing"]:,} NULL/blank ({item["missing"]/rows:.1%} of rows). Meaning is not inferred.'})
        if item['potential_missing_markers']:review.append({'Field':name,'Review':'Special markers','Details':f'{item["potential_missing_markers"]:,} NA-like strings retained as literal values; review before nullifying.'})
        if 0<item['numeric_values']<present:review.append({'Field':name,'Review':'Mixed numeric/text values','Details':'Some nonblank values do not parse as finite numbers. This can be legitimate text; inspect before converting.'})
        if 0<item['recognized_dates']<present or any(c['role']=='date' for c in item['candidates']) and item['recognized_dates']<present:
            review.append({'Field':name,'Review':'Date recognition','Details':'Calendar meaning/format needs review. No time axis or date convention is assumed.'})
        if item['key_candidate'] or item['inferred_type']=='identifier':optional.append({'Field':name,'Review':'Possible identifier','Details':'Uniqueness or an identifier-like name is a technical clue, not proof of business identity.'})
        if numeric_status(item,report,info):usable.append({'Field':name,'Use':'Numeric description','Details':'Finite stored numbers; business units/meaning remain subject to review.'})
        elif item['inferred_type']=='category':usable.append({'Field':name,'Use':'Category counts','Details':'Group source records without inferring a business outcome.'})
        stats=item.get('statistics') or {}
        if stats and stats.get('p25') is not None and stats.get('p75') is not None:
            spread=stats['p75']-stats['p25']
            if spread>0 and (stats['min']<stats['p25']-1.5*spread or stats['max']>stats['p75']+1.5*spread):
                optional.append({'Field':name,'Review':'Unusual numeric range','Details':'Observed extremes lie beyond 1.5 IQR fences. These are screening signals, not confirmed errors.'})
    if report['exact_duplicate_rows']:review.insert(0,{'Field':'Whole table','Review':'Exact duplicate rows','Details':f'{report["exact_duplicate_rows"]:,} repeats retained. Confirm whether repeats are legitimate before deduplication.'})
    return review,optional,usable


def quality_overview(report,info):
    review,optional,usable=quality_findings(report,info)
    cells=report['rows']*len(report['columns']);missing=sum(c['missing'] for c in report['columns'])
    a,b,c=st.columns(3)
    a.metric('Missing cells',f'{missing:,}',help=f'{missing/cells:.1%} of cells are NULL or blank.' if cells else 'No cells.')
    b.metric('Exact duplicate rows',f'{report["exact_duplicate_rows"]:,}',help='Retained until you explicitly choose reversible deduplication.')
    c.metric('Technically usable fields',str(len(usable)),help='Numeric descriptions or category counts; not approval of business meanings.')
    tabs=st.tabs([f'Review potential issues · {len(review)}',f'Optional improvements · {len(optional)}',f'Usable fields · {len(usable)}'])
    for tab,items,empty in zip(tabs,[review,optional,usable],['No signals of these parsing/missingness issues. Source accuracy still needs review.','No additional suggestions from these checks.','Review conversions or field roles to enable further calculations.']):
        with tab:
            if items:st.dataframe(pd.DataFrame(items),hide_index=True,width='stretch')
            else:st.caption(empty)
    st.caption('Automatic checks prioritize review; they never remove rows, approve business meanings or classify statistical outliers as errors.')


@st.cache_data(max_entries=24,show_spinner=False)
def dataset_profile(directory,table,version):return Workspace(directory).profile(table)


def dataset_overview(workspace,table,report=None,*,new=False):
    info=workspace.info(table);report=report or dataset_profile(str(workspace.directory),table,info['current'])
    with st.container(border=True):
        st.subheader(('Just imported · ' if new else 'Dataset overview · ')+info['source'].get('filename',table))
        st.caption(f'{table} · {info["rows"]:,} rows · {len(info["columns"]):,} fields · {report["exact_duplicate_rows"]:,} exact duplicates retained')
        st.text('Source: '+info['source']['origin']+' · SHA-256 '+info['source']['sha256'][:16])
        for warning in info.get('import_warnings',[]):st.warning(warning)
        a,b,c=st.columns(3)
        if a.button('Explore data',key='ux-data-explore-'+table,type='primary',width='stretch'):go('Explore',table=table)
        if b.button('Review data quality',key='ux-data-quality-'+table,width='stretch'):go('Quality & Preparation',table=table)
        if c.button('Start analysis',key='ux-data-analyze-'+table,width='stretch'):go('Investigate',table=table)
        sample=Engine(workspace,table).records(0,size=10)
        st.dataframe(frame_of(sample).drop(columns=['_row_id'],errors='ignore'),hide_index=True,width='stretch')
        st.caption('First 10 records from the active dataset version, without analytical filters. Original source and previous versions are retained.')
        types=pd.DataFrame([{'Field':c['column'],'Detected type':c['inferred_type'],'Missing':c['missing'],'Missing %':round(100*c['missing']/report['rows'],1) if report['rows'] else 0} for c in report['columns']])
        with st.expander('Detected fields, missingness and provenance'):
            st.dataframe(types,hide_index=True,width='stretch');st.json(info['source'])


def explore_guidance(engine,report):
    numeric=[c['column'] for c in report['columns'] if numeric_status(c,report,engine.info)]
    categories=[c['column'] for c in report['columns'] if c['inferred_type']=='category' and 1<c['distinct']<=100]
    recommendations=[]
    if categories:recommendations.append(('Compare categories','Ranked groups','Record counts or the selected reviewed metric by an available category.',{'explore-groupby':engine.context.comparison or categories[0]}))
    if numeric:recommendations.append(('Understand a distribution','Numeric distribution','Inspect the spread and missing inputs of a verified numeric field.',{}))
    if engine.context.date_basis:recommendations.append(('Examine change over time','Calendar trend','Uses the shared, reviewed date meaning; gaps are not zeros.',{}))
    elif len(numeric)>1:recommendations.append(('Inspect a relationship','Numeric relationship','Compare paired numbers; association does not establish causation.',{}))
    if not recommendations:recommendations.append(('Inspect underlying records','Records','Start with the actual source rows; no chart or business meaning is assumed.',{}))
    st.subheader('Explore your data')
    st.caption('Start with a suggested view, then choose fields and inspect the calculation. All existing chart modes remain available below.')
    cols=st.columns(len(recommendations[:3]))
    for col,(title,target,reason,presets) in zip(cols,recommendations[:3]):
        with col.container(border=True):
            st.write('**'+title+'**');st.caption(reason)
            if st.button('Open '+target,key='ux-recommend-'+target,width='stretch'):
                st.session_state.update(presets);st.session_state['explore-chart-type']=target;st.rerun()
