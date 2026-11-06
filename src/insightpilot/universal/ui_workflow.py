"""Workflow presentation over the existing workspace, profile and Engine services."""
from datetime import datetime, timezone
from html import escape
import hashlib

import pandas as pd
import streamlit as st

from .engine import Engine, frame_of
from .profile import numeric_status
from .store import Workspace
from .workspace_labels import catalog, metadata

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


PAGE_COPY = {
    'Workspace Home': ('YOUR ANALYTICAL COMMAND CENTER', 'Data Analysis', 'Turn complexity into understanding. Bring your data, follow the evidence, and make your next decision with confidence.'),
    'Data Hub': ('01 / CONNECT', 'Your data, in one place.', 'Import sources, inspect their structure, and keep every original intact.'),
    'Quality & Preparation': ('02 / PREPARE', 'Build on trusted foundations.', 'Review data quality, define field meaning, and prepare reversible versions.'),
    'Explore': ('03 / DISCOVER', 'Find the signal.', 'Compare segments, explore relationships, and save the evidence that matters.'),
    'Investigate': ('04 / UNDERSTAND', 'From a question to a finding.', 'Establish what changed. Separate observations, hypotheses, and limitations.'),
    'Reports': ('05 / COMMUNICATE', 'Make your evidence count.', 'Bring reproducible findings together into a clear, defensible report.'),
    'Overview': ('ANALYTICAL OVERVIEW', 'The bigger picture.', 'Your selected metric and data scope, brought into focus.'),
    'AI Analyst': ('LOCAL INTELLIGENCE', 'A thinking partner. Grounded in data.', 'Plan locally with Ollama and Qwen. Review calculations and limitations.'),
    'SQL Lab': ('QUERY WORKBENCH', 'Ask precisely. Verify everything.', 'Read-only SQL over your selected data, with bounded results and execution evidence.'),
    'Advanced': ('ANALYTICAL METHODS', 'Go beyond the surface.', 'Apply statistical methods with explicit assumptions and applicability checks.'),
}


def navigation(workspace):
    from .command_navigation import wheel
    item=metadata(workspace)
    st.session_state.setdefault('universal-page','Workspace Home')
    active=st.session_state['universal-page']
    if active not in PAGES:active='Workspace Home';st.session_state['universal-page']=active
    eyebrow,title,copy=PAGE_COPY[active]
    def alternative():
        with st.container(key='workflow-nav'):
            return st.radio('Analysis workflow',PAGES,key='universal-page',horizontal=True,label_visibility='collapsed')
    def hero():
        tables=workspace.tables()
        tag=hashlib.sha256(str(workspace.directory).encode()).hexdigest()[:8]
        selected=st.session_state.get('universal-table-'+tag)
        info=tables.get(selected) or next(iter(tables.values()),None)
        project=item['name'] if item['status']=='named' else 'Your next great question starts here'
        badge='PROJECT / '+project if item['status']=='named' else 'LOCAL-FIRST BUSINESS INTELLIGENCE'
        stats=(f'<div class="ip-statline"><div><strong>{len(tables):02d}</strong><span>Connected datasets</span></div>'
               f'<div><strong>{sum(t["rows"] for t in tables.values()):,}</strong><span>Source records</span></div>'
               f'<div><strong>{len(workspace.saved_objects("block")):02d}</strong><span>Saved evidence</span></div></div>') if active=='Workspace Home' else ''
        st.markdown('<div class="ip-hero"><div class="ip-path">INSIGHTPILOT &nbsp; / &nbsp; '+escape(active.upper())+'</div>'
            '<span class="ip-tag">'+escape(badge)+'</span><div class="eyebrow" style="margin-top:22px">'+eyebrow+'</div>'
            '<h1>'+escape(title)+'</h1><div class="objective">'+escape(item.get('purpose') or item['description'] or copy)+'</div>'+stats+'</div>',unsafe_allow_html=True)
        if info and active=='Workspace Home':st.caption('ACTIVE DATASET · '+info.get('display_name',info['source'].get('filename','Dataset'))+' · '+f'{info["rows"]:,} rows / {len(info["columns"])} fields')
    expanded=st.session_state.get('_hub-expanded',False)
    with st.container(key='command-header'):
        if active=='Workspace Home':
            left,right=st.columns([1.5,1],gap='large',vertical_alignment='center')
            with left:hero()
            with right:
                with st.container(key='command-wheel-expanded' if expanded else 'command-wheel'):
                    wheel(active,PAGES,presentation='home')
        else:
            hero()
            with st.container(key='command-dock-expanded' if expanded else 'command-dock'):
                wheel(active,PAGES,presentation='dock')
        with st.expander('Accessible navigation'):
            alternative()
    return st.session_state['universal-page']


def project_summary(workspace):
    tables=workspace.tables();layouts=workspace.saved_objects('layout')
    reports=sum(k.startswith('ux.report:') for k in layouts)
    briefs=sum(k.startswith('ux.investigation:') for k in layouts)
    blocks=len(workspace.saved_objects('block'));views=len(workspace.saved_objects('view'))
    stage='Report draft saved' if reports else 'Investigation saved' if briefs else 'Evidence saved' if blocks else 'Analysis saved' if views else 'Data imported' if tables else 'Ready for data'
    activity=datetime.fromtimestamp((workspace.directory/'workspace.duckdb').stat().st_mtime,timezone.utc).strftime('%d %b %Y, %H:%M UTC')
    return {'tables':tables,'reports':reports,'investigations':briefs,'blocks':blocks,'views':views,'stage':stage,'activity':activity}


def home(workspace):
    from .ui_workspaces import request_transition,root_directory
    from .projects import saved_info
    root=root_directory();peers=catalog(root)
    st.markdown('<div class="eyebrow">YOUR CURRENT SESSION</div>',unsafe_allow_html=True)
    a,b,c,d=st.columns(4)
    if a.button('Upload dataset',type='primary',width='stretch'):go('Data Hub')
    if b.button('Continue recent analysis',width='stretch'):go('Explore' if workspace.tables() else 'Data Hub')
    if c.button('Investigate a problem',width='stretch'):go('Investigate' if workspace.tables() else 'Data Hub')
    if d.button('View saved reports',width='stretch'):go('Reports')
    st.caption('Analyses and report drafts remain in this temporary working session. Save Project or Save Changes is the only way to update Saved Projects.')
    if st.button('New Project',width='stretch'):request_transition('new')
    st.subheader('Saved Projects')
    query=st.text_input('Find a project',placeholder='Search saved names or purposes')
    visible=[p for p in peers if query.casefold() in (metadata(p)['name']+' '+metadata(p)['description']+' '+metadata(p).get('purpose','')).casefold()]
    if not visible:st.info('No saved projects match. Start with an empty project, upload data, and click Save Project when you want to keep it.')
    if len(visible)>12:st.caption('Showing the first 12 matches. Search to find another saved project.')
    columns=st.columns(2)
    for index,peer in enumerate(visible[:12]):
        info=metadata(peer);summary=project_summary(peer);tag=info['id'];record=saved_info(peer)
        with columns[index%2].container(border=True,key='ux-project-'+tag):
            st.text(info['name'] if info['status']=='named' else 'Legacy unnamed project')
            st.caption(info.get('purpose') or info['description'] or 'Preserved existing project')
            st.text('Dataset: '+(', '.join(t.get('display_name') or t['source'].get('filename',name) for name,t in summary['tables'].items()) or 'No dataset'))
            st.caption('Last saved: '+(record['last_saved'] or 'unknown · preserved legacy project'))
            left,right=st.columns(2)
            if left.button('Open',key='ux-open-'+tag,width='stretch'):request_transition('open',peer.directory)
            if right.button('Delete',key='project-delete-'+tag,width='stretch'):
                st.session_state['_project-delete-target']=str(peer.directory);st.rerun()
    st.subheader('Recent work')
    objects=[]
    for kind,category in [('view','Analysis'),('block','Evidence'),('layout','Document')]:
        for name,value in workspace.saved_objects(kind).items():
            if kind=='layout' and not name.startswith(('ux.report:','ux.investigation:')):continue
            objects.append({'Name':name.removeprefix('ux.report:').removeprefix('ux.investigation:'),'Type':category,
                            'Saved at':value.get('updated_at') or ((value.get('evidence') or {}).get('executed_at') if isinstance(value.get('evidence'),dict) else None) or 'Retained in project'})
    if objects:st.dataframe(pd.DataFrame(objects[-8:][::-1]),hide_index=True,width='stretch')
    else:st.markdown('<div class="ip-empty"><h3>A clean slate. A world of possibilities.</h3><p>Import a dataset to begin. Saved analyses, findings, and reports will appear here as you work.</p></div>',unsafe_allow_html=True)
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
    rows=report['rows']
    cells=report['rows']*len(report['columns']);missing=sum(c['missing'] for c in report['columns'])
    a,b,c=st.columns(3)
    a.metric('Missing cells',f'{missing:,}',help=f'{missing/cells:.1%} of cells are NULL or blank.' if cells else 'No cells.')
    b.metric('Exact duplicate rows',f'{report["exact_duplicate_rows"]:,}',help='Retained until you explicitly choose reversible deduplication.')
    c.metric('Technically usable fields',str(len(usable)),help='Numeric descriptions or category counts; not approval of business meanings.')
    with st.expander('Field completeness · visual comparison',expanded=True):
        import plotly.graph_objects as go
        from .visuals import style
        columns=sorted(report['columns'],key=lambda c:c['missing'],reverse=True)[:30]
        if len(report['columns'])>30:st.caption('Showing the 30 fields with the most missing values. The complete field catalog remains available below.')
        rates=[(1-c['missing']/rows)*100 if rows else 0 for c in columns]
        figure=go.Figure(go.Bar(x=rates,y=[c['column'] for c in columns],orientation='h',
            marker_color=['#67e0cd' if x==100 else '#f1bc73' for x in rates],
            hovertemplate='%{y}<br>%{x:.1f}% nonmissing<extra></extra>'))
        style(figure,'Completeness by field','Nonmissing cells (%)',None)
        figure.update_xaxes(range=[0,100],ticksuffix='%')
        figure.update_layout(height=min(850,max(260,len(columns)*25+100)))
        st.plotly_chart(figure,width='stretch',theme=None,key='quality-completeness')
        st.caption('Full active version, before analytical filters. Nonmissing does not mean accurate or valid. Blank and NULL values count as missing.')
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
    st.caption('COMPARISONS · DISTRIBUTIONS · RELATIONSHIPS · TIME · COMPOSITION')
    st.caption('Start with a suggested view, or use the chart selector below. Every view keeps your metric, filters and source version.')
    if categories:recommendations.append(('Understand concentration','Composition & Pareto','Shares and cumulative contribution with a full selected denominator.',{}))
    cols=st.columns(len(recommendations[:4]))
    for col,(title,target,reason,presets) in zip(cols,recommendations[:4]):
        with col.container(border=True):
            st.write('**'+title+'**');st.caption(reason)
            if st.button('Open '+target,key='ux-recommend-'+target,width='stretch'):
                st.session_state.update(presets);st.session_state['explore-chart-type']=target;st.rerun()
