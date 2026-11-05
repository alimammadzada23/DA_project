"""User-authored investigation/report drafts over existing saved-object storage.

No inference, SQL generation or data mutation. Evidence is copied from executed
saved blocks and checked against retained source/version manifests on export.
"""
from copy import deepcopy
from datetime import datetime,timezone
from html import escape
import json

import plotly.graph_objects as go
import streamlit as st

from .engine import frame_of
from .report import configuration,html_report,replay
from .workspace_labels import identity
from .ui_workflow import go as navigate_to
from .ui_workflow import scope_filters

SECTIONS=[('executive_summary','Executive summary'),('business_context','Business context'),
          ('methodology','Data and methodology'),('interpretation','Investigation and interpretation'),
          ('recommendations','Recommendations and validation steps'),('limitations','Limitations')]


def sections_from_investigation(brief):
    """Copy classified user notes; never synthesize an executive conclusion."""
    interpretation='\n\n'.join(label+':\n'+(brief.get(key) or 'Not recorded by the analyst.') for label,key in
        [('Observed facts','observations'),('Observed associations','associations'),('Hypotheses to investigate','hypotheses')])
    return {'business_context':brief.get('question',''),'interpretation':interpretation,
            'recommendations':brief.get('actions',''),'limitations':brief.get('limitations','')}


def report_definition(title,sections,selected,blocks):
    if not isinstance(title,str) or not title.strip() or len(title)>160:raise ValueError('Choose a report title of 1–160 characters.')
    if not selected or len(selected)>20 or len(set(selected))!=len(selected):raise ValueError('Choose 1–20 distinct saved findings for the report.')
    if set(selected)-set(blocks):raise ValueError('A selected finding is no longer available. Reload the evidence library.')
    if set(sections)-{key for key,_ in SECTIONS} or any(not isinstance(v,str) or len(v)>5000 for v in sections.values()):raise ValueError('Report sections must contain at most 5,000 characters each.')
    if len(json.dumps([title,sections,selected,{name:blocks[name] for name in selected}],default=str))>4_990_000:
        raise ValueError('Selected report evidence exceeds the saved-object size limit. Choose fewer findings or narrower saved result tables.')
    return {'format_version':1,'kind':'ux.report','title':title.strip(),'sections':deepcopy(sections),
            'evidence_order':list(selected),'blocks':{name:deepcopy(blocks[name]) for name in selected},
            'updated_at':datetime.now(timezone.utc).isoformat(),'commentary_author':'Analyst supplied; not model-generated or established causal evidence.'}


def business_report(workspace,engine,metric,definition):
    # Validate shape again for a stored draft; never execute arbitrary saved SQL.
    definition=report_definition(definition['title'],definition['sections'],definition['evidence_order'],definition['blocks'])
    retained_blocks=[]
    for name in definition['evidence_order']:
        block=definition['blocks'][name];retained,retained_metric=replay(workspace,block['analysis']);item=block['evidence']
        if item.get('table')!=retained.table or item.get('version')!=retained.info['current'] or item.get('source_sha256')!=retained.info['source']['sha256']:
            raise ValueError('Saved evidence does not match its retained source/version manifest. No report was generated.')
        retained_blocks.append((name,block,retained,retained_metric))
    # The appendix belongs to the first selected saved context. Changing the
    # sidebar never inserts an unrelated live dataset into a retained report.
    base=html_report(retained_blocks[0][2],retained_blocks[0][3],title=definition['title'])
    base=base.replace('</style>','''
body {box-sizing:border-box;overflow-wrap:anywhere;}
.evidence-table {max-width:100%;max-height:420px;overflow:auto;border:1px solid #cad4df;border-radius:8px;}
.evidence-table thead {position:sticky;top:0;background:#edf3f8;}
.evidence-table:focus-visible {outline:2px solid #147d79;outline-offset:3px;}
.plotly-graph-div {max-width:100%;}
@media(max-width:700px) {body{margin:12px auto;padding:12px;}h1{font-size:1.6rem;}}
@media print {.evidence-table{max-height:none;overflow:visible;}}
</style>''',1)
    content=['<section><h2>Business analysis</h2><p class="note">Commentary and recommendations below are analyst supplied. They do not establish causality. Each finding has its own dataset, filters and immutable source version.</p>']
    for key,title in SECTIONS[:3]:
        content.append('<h2>'+escape(title)+'</h2><p>'+escape(definition['sections'].get(key,'') or 'Not provided by the analyst.').replace('\n','<br>')+'</p>')
    content.append('<h2>Key findings · saved analytical evidence</h2>')
    include_plotly=True
    for index,(name,block,retained,_) in enumerate(retained_blocks,1):
        item=block['evidence']
        content.append('<article><h3>'+str(index)+'. '+escape(name)+'</h3>')
        content.append('<p class="note">Source: '+escape(retained.info['source'].get('filename',retained.table))+' · Dataset: '+escape(retained.table)+'</p>')
        content.append('<p class="note">'+escape(scope_filters(item.get('filters',[])))+' · Date basis: '+escape(retained.context.date_basis or 'not selected')+'</p>')
        for serialized in block.get('figures',[]) or ([block['figure']] if block.get('figure') else []):
            content.append(go.Figure(json.loads(serialized)).to_html(full_html=False,include_plotlyjs=include_plotly));include_plotly=False
        content.append('<div class="evidence-table" role="region" aria-label="Saved evidence table" tabindex="0">'+frame_of(item).head(100).to_html(index=False,escape=True)+'</div>')
        if len(item.get('rows',[]))>100:content.append('<p class="note">First 100 rows of the saved result shown; full executed evidence is retained below.</p>')
        content.append('<details><summary>Calculation and reproduction manifest</summary><pre>'+escape(json.dumps({'analysis':block['analysis'],'evidence':item,'analysis_plan':block.get('analysis_plan')},default=str,indent=2))+'</pre></details></article>')
    for key,title in SECTIONS[3:]:
        content.append('<h2>'+escape(title)+'</h2><p>'+escape(definition['sections'].get(key,'') or 'Not provided by the analyst.').replace('\n','<br>')+'</p>')
    payload=json.dumps(definition,default=str).replace('&','\\u0026').replace('<','\\u003c').replace('>','\\u003e')
    content.append('<script type="application/json" id="insightpilot-report-definition">'+payload+'</script></section>')
    return base.replace('<h2>Metric overview</h2>','\n'.join(content)+'<h2>Primary saved selection · calculation appendix</h2>',1)


def investigation_brief(engine,metric):
    workspace=engine.workspace;tag=identity(workspace)
    briefs={k:v for k,v in workspace.saved_objects('layout').items() if k.startswith('ux.investigation:')}
    st.subheader('Define the problem. Build the evidence.')
    st.write('Keep the question, observations and hypotheses separate. The tools below compare recorded data; they do not establish a cause.')
    selected=st.selectbox('Saved investigation',[None]+list(briefs),format_func=lambda x:'New investigation' if x is None else x.removeprefix('ux.investigation:'))
    current=briefs.get(selected,{})
    scope=tag+'-'+str(selected)
    with st.form('ux-investigation-'+scope):
        title=st.text_input('Investigation name',current.get('title',''),placeholder='Name the observed business problem')
        question=st.text_area('Business question',current.get('question',''),placeholder='What changed, for whom, and over which scope?',max_chars=2000,height=90)
        st.caption('Current measure: '+metric.name+' · '+metric.formula+'. The active dataset, filters and date meaning will be saved with this brief.')
        a,b=st.columns(2)
        observations=a.text_area('Observed facts',current.get('observations',''),max_chars=5000,placeholder='Describe only what an executed result supports.')
        associations=b.text_area('Observed associations',current.get('associations',''),max_chars=5000,placeholder='Variables that move together; these do not establish causality.')
        hypotheses=a.text_area('Hypotheses to investigate',current.get('hypotheses',''),max_chars=5000,placeholder='Possible explanations and alternative interpretations.')
        limitations=b.text_area('Uncertainties and limitations',current.get('limitations',''),max_chars=5000,placeholder='Coverage, missing definitions, measurement changes or confounders.')
        actions=st.text_area('Possible actions and validation steps',current.get('actions',''),max_chars=5000,placeholder='What could be tested next, and what information would change the decision?')
        blocks=workspace.saved_objects('block')
        evidence=st.multiselect('Supporting saved findings',list(blocks),default=[name for name in current.get('evidence',[]) if name in blocks])
        save=st.form_submit_button('Save investigation',type='primary')
    if save:
        if not title.strip() or len(title)>80 or not question.strip():st.warning('Give the investigation a name (up to 80 characters) and a business question.')
        else:
            workspace.save_object('layout','ux.investigation:'+title.strip(),{'kind':'ux.investigation','format_version':1,'title':title.strip(),'question':question,'observations':observations,'associations':associations,'hypotheses':hypotheses,'limitations':limitations,'actions':actions,'evidence':evidence,'analysis':configuration(engine,metric),'updated_at':datetime.now(timezone.utc).isoformat()})
            st.success('Saved the question, analyst notes, evidence references and analytical scope. No causal conclusion was generated.')
    a,b,c=st.columns(3)
    if a.button('Establish evidence in Explore',width='stretch'):navigate_to('Explore')
    if b.button('Ask the local analyst',width='stretch'):navigate_to('AI Analyst')
    if c.button('Assemble a business report',width='stretch'):navigate_to('Reports')


def report_builder(engine,metric):
    workspace=engine.workspace;tag=identity(workspace)
    st.subheader('Build a defensible business report')
    st.caption('Collect executed findings, arrange them in order, and add your interpretation. Reports keep each finding’s own source, version and filters. Narrative is supplied by you.')
    drafts={k:v for k,v in workspace.saved_objects('layout').items() if k.startswith('ux.report:')}
    selected=st.selectbox('Saved report draft',[None]+list(drafts),format_func=lambda x:'Start a new report' if x is None else x.removeprefix('ux.report:'))
    draft=drafts.get(selected,{})
    if draft:st.caption('This draft uses its saved evidence snapshots. Start a new report to choose newly updated findings.')
    blocks=workspace.saved_objects('block')|draft.get('blocks',{})
    if not blocks:
        st.info('No saved evidence yet. Save a chart or table from Explore, AI Analyst or SQL Lab, then return to build your report.')
        if st.button('Explore and save a finding',type='primary'):navigate_to('Explore')
        return
    key='ux-report-'+tag+'-'+str(selected)
    briefs={k:v for k,v in workspace.saved_objects('layout').items() if k.startswith('ux.investigation:')}
    if briefs:
        source=st.selectbox('Use a saved investigation',[None]+list(briefs),format_func=lambda x:'Choose investigation notes' if x is None else x.removeprefix('ux.investigation:'),key=key+'-brief')
        st.caption('Use investigation notes to replace report context, classified interpretations, actions and limitations. Write the executive summary yourself. The saved draft changes only when you save it.')
        if st.button('Use investigation notes',disabled=source is None):
            brief=briefs[source]
            for field,value in sections_from_investigation(brief).items():st.session_state[key+'-'+field]=value
            st.session_state[key+'-chosen']=[name for name in brief.get('evidence',[]) if name in blocks]
            if not st.session_state.get(key+'-title'):st.session_state[key+'-title']=brief['title']
    title=st.text_input('Business report title',draft.get('title',''),placeholder='Give the report a clear business question',key=key+'-title')
    chosen=st.multiselect('Choose saved findings',list(blocks),default=draft.get('evidence_order',[]),max_selections=20,key=key+'-chosen')
    order=[]
    if chosen:
        st.caption('Set a unique position for each finding. Every saved table and chart keeps its own scope; different selections are never silently combined.')
        membership=tuple(chosen)
        if st.session_state.get(key+'-membership')!=membership:
            for index,name in enumerate(chosen):st.session_state[key+'-position-'+name]=index+1
            st.session_state[key+'-membership']=membership
        for index,name in enumerate(chosen):
            left,right=st.columns([1,4])
            position=left.number_input('Position · '+name,min_value=1,max_value=len(chosen),key=key+'-position-'+name)
            right.text(name)
            item=blocks[name]['evidence'];right.caption(str(item.get('table',''))+' · '+str(item.get('version',''))[-10:]+' · '+str(len(item.get('rows',[])))+' saved result rows')
            order.append((position,index,name))
    sections={}
    with st.expander('Write the report narrative',expanded=True):
        narrative_tabs=st.tabs(['Summary & context','Interpretation & actions','Methods & limitations'])
        for tab,indices in zip(narrative_tabs,[(0,1),(3,4),(2,5)]):
            with tab:
                for index in indices:
                    field,label=SECTIONS[index]
                    sections[field]=st.text_area(label,draft.get('sections',{}).get(field,''),height=90,max_chars=5000,key=key+'-'+field,placeholder='Analyst commentary — cite the supporting finding and disclose uncertainty.')
    a,b=st.columns(2)
    save=a.button('Save report draft',type='primary',width='stretch')
    build=b.button('Preview & build report',width='stretch')
    context=configuration(engine,metric);context.pop('generated_at',None)
    signature=json.dumps([title,sections,order,[(name,blocks[name]['evidence'].get('evidence_id')) for name in chosen],context],sort_keys=True,default=str)
    preview_key=key+'-preview'
    if save or build:
        try:
            if len({p for p,_,_ in order})!=len(order):raise ValueError('Give each finding a distinct position before saving or building the report.')
            definition=report_definition(title,sections,[name for _,_,name in sorted(order)],blocks)
            if save:
                if len(title)>80:raise ValueError('Use a title of at most 80 characters for a saved draft.')
                workspace.save_object('layout','ux.report:'+title.strip(),definition)
                st.success('Report draft and evidence snapshots saved locally. Existing analyses and source versions are unchanged.')
            if build:st.session_state[preview_key]={'signature':signature,'html':business_report(workspace,engine,metric,definition),'definition':definition}
        except ValueError as exc:st.warning(str(exc))
    built=st.session_state.get(preview_key)
    if built and built['signature']==signature:
        st.download_button('Download business report · HTML',built['html'],file_name='insightpilot-business-report.html',mime='text/html',on_click='ignore')
        st.download_button('Download report definition · JSON',json.dumps(built['definition'],indent=2,default=str),file_name='insightpilot-report-definition.json',mime='application/json',on_click='ignore')
        with st.expander('Preview the finished report',expanded=True):
            import streamlit.components.v1 as components
            components.html(built['html'],height=650,scrolling=True)
        st.caption('Self-contained HTML with executed evidence and reproduction manifests. Browser print-to-PDF is available; native PDF generation is not claimed.')
    elif built:st.info('The report changed. Preview & build again to refresh the export.')
