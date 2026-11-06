"""AI capability inside the existing Analyst page, with bounded verified history."""
from dataclasses import asdict
import hashlib
import json
import os
import re
import time

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from . import assistant,visuals
from .analyst_execute import figures
from .analyst_session import Conversation,analyze_question,approve_pending,context_signature,save_answer,replay_answer,assert_current
from .analyst_plan import PlanError
from .engine import frame_of
from .local_ai import LocalConfig,OllamaClient,LocalAIError,DEFAULT_MODEL,DEFAULT_ENDPOINT
from .profile import numeric_status
from .report import configuration,csv_export,excel_export,safe_spreadsheet_frame


def plain(value):
    # Prevent imported/model/user text from embedding Markdown images or links.
    return re.sub(r'([\\`*_{}\[\]()#+.!<>|~])',r'\\\1',str(value))


def local_controls():
    endpoint=st.text_input('Local Ollama endpoint',os.environ.get('INSIGHTPILOT_OLLAMA_URL',DEFAULT_ENDPOINT),help='Only loopback addresses on this computer are permitted; no remote model or cloud fallback.')
    tag=hashlib.sha256(endpoint.encode()).hexdigest()[:12]
    cache=st.session_state.setdefault('ollama-discovery',{})
    if st.button('Refresh local models'):cache.pop(tag,None)
    cached=cache.get(tag)
    if cached is None or time.monotonic()-cached['checked']>30:
        try:
            state=OllamaClient(LocalConfig(endpoint=endpoint)).discover()
        except LocalAIError as exc:state={'status':exc.code,'message':str(exc),'models':[]}
        cached={'state':state,'checked':time.monotonic(),'inspections':{}};cache[tag]=cached
    state=cached['state'];options=[m['name'] for m in state.get('models',[])] or [DEFAULT_MODEL]
    preferred=os.environ.get('INSIGHTPILOT_OLLAMA_MODEL',DEFAULT_MODEL)
    model=st.selectbox('Local model',options,index=options.index(preferred) if preferred in options else 0,key='ollama-model-'+tag,
        help='Installed local models only. Qwen3.5 4B is the initial recommendation. The app never pulls weights.')
    timeout=st.select_slider('Maximum model wait (seconds)',[15,30,60,90,120],value=60)
    with st.expander('Local inference limits and field scope'):
        context=st.selectbox('Model context budget',[4096,8192],index=1)
        st.caption('One selected model; bounded JSON output; two-minute idle keep-alive. Memory and speed on your Mac remain unmeasured.')
    config=LocalConfig(endpoint,model,timeout,context,1024)
    available=False
    if state['status']=='available':
        if model not in cached['inspections']:
            try:cached['inspections'][model]=OllamaClient(config).inspect_model()
            except LocalAIError as exc:cached['inspections'][model]={'status':exc.code,'message':str(exc)}
        inspection=cached['inspections'][model]
        if inspection['status']=='available':
            available=True;st.success('Local AI available · '+model+' · server reports a local model; inference runs when you ask.')
        else:st.warning(inspection['message'])
    else:st.warning(state['message'])
    st.caption('Official Ollama loopback API only. No account, paid API, automatic installation, model download or external fallback. Schema and question go to the local service; source rows do not. With result explanations on, the computed aggregate table (at most 40 rows) is also read by the model on this computer.')
    return config,available


def examples(engine,profile):
    numeric=[c['column'] for c in profile['columns'] if numeric_status(c,profile,engine.info)]
    result=['What columns are available?','Which columns have missing values?']
    if engine.context.comparison:result.insert(0,'Compare the number of records across '+engine.context.comparison+'.')
    if numeric:
        result.append('Show the distribution of '+numeric[0]+'.')
        if engine.context.comparison:result.append('Compare record counts and average '+numeric[0]+' by '+engine.context.comparison+'.')
    if engine.context.date_basis:result.append('Show the selected metric over time.')
    return result[:5]


def render_result(response,entry_id,workspace,*,question=None):
    status=response['status'];text=plain(response['answer'])
    if status=='error':st.warning(text);return
    if status in ('clarify','unsupported'):st.info(text);return
    if status=='approval_required':st.info(text);st.json(response['plan']['measures']);return
    st.markdown(text)
    render_interpretation(response.get('interpretation'),entry_id)
    backend=response.get('backend',{});st.caption(backend.get('backend','Deterministic calculation')+(' · '+backend['model'] if backend.get('model') else ''))
    if backend.get('model'):st.caption('Model proposed the plan. Every number and chart below comes from executed local calculations.')
    else:st.caption('Deterministic guided analyst; no model or tokens used')
    if response.get('cache_hit'):st.caption('Reused the existing verified result in the unchanged context; no repeated inference.')
    if backend.get('repair_attempted'):st.caption('One model correction request was used; the final plan passed all checks.')
    if backend.get('constraint_repairs'):
        labels={'limit':'operation result bound','descending':'sort direction','sort_by':'ranking measure','operation':'analysis operation','inherit':'previous analysis context','chart':'requested chart'}
        st.caption('Applied and validated explicit request constraints: '+', '.join(labels.get(k,'requested constraint') for k in backend['constraint_repairs'])+'. Referenced groups retain their membership.')
    primary=response['primary']
    st.caption(primary['table']+' · version '+primary['version'][-10:]+' · source '+primary['source_sha256'][:12])
    st.caption('Applied filters: '+json.dumps(primary['filters'],default=str,ensure_ascii=False)+' · date basis: '+str(primary['context']['date_basis']))
    definitions=response.get('definitions',[])
    if any(d.get('semantic_status')=='technical_descriptive' for d in definitions):
        st.caption('Descriptive statistics of verified stored numbers. Business meaning and units remain unreviewed.')
    if definitions:
        with st.expander('Measure definitions and eligible denominators'):
            for definition in definitions:st.json(definition)
    display_names={}
    plan=response.get('plan',{})
    if plan.get('operation') in ('aggregate','trend'):
        display_names={'records':'Source rows','period':'Period · '+str(primary['context']['date_basis'])}
        display_names.update({'group_'+str(i):name for i,name in enumerate(plan.get('group_by',[]))})
        for i,definition in enumerate(definitions):
            label=definition['label'];unit=definition.get('units','')
            display_names['m'+str(i)]=label+(' ('+unit+')' if unit and label.casefold()!=unit.casefold() else '')
            for prefix,title in [('eligible_','Valid inputs'),('numerator_','Numerator'),('denominator_','Denominator')]:
                display_names[prefix+str(i)]=title+' · '+label
    st.dataframe(frame_of(primary).rename(columns=display_names),hide_index=True,width='stretch')
    for index,serialized in enumerate(response.get('figures',[])):
        fig=go.Figure(json.loads(serialized));st.plotly_chart(fig,width='stretch',theme=None,key=f'ai-figure-{entry_id}-{index}',config={'displaylogo':False})
        with st.expander('Chart data and export · '+str(index+1)):
            data=safe_spreadsheet_frame(visuals.plotted_data(fig))
            st.download_button('Plotted values CSV',data.to_csv(index=False),file_name='analyst-chart.csv',key=f'ai-chart-csv-{entry_id}-{index}',on_click='ignore')
            st.download_button('Interactive chart HTML',fig.to_html(include_plotlyjs=True),file_name='analyst-chart.html',key=f'ai-chart-html-{entry_id}-{index}',on_click='ignore')
    for limitation in response.get('limitations',[]):st.caption(limitation)
    with st.expander('Analysis evidence · '+entry_id):
        st.json({'plan':response.get('plan'),'backend':backend,'definitions':definitions,'evidence':response.get('evidence',[])})
        for item in response.get('evidence',[]):st.code(item['sql'],language='sql')
        st.download_button('Download analyst evidence',json.dumps({k:v for k,v in response.items() if k!='figures'},default=str,indent=2),file_name='analyst-evidence.json',key='ai-evidence-'+entry_id,on_click='ignore')
        st.download_button('Result table CSV',csv_export(primary),file_name='analyst-result.csv',key='ai-table-'+entry_id,on_click='ignore')
    if workspace:
        with st.expander('Save this analysis to Reports'):
            name=st.text_input('Saved analysis name',str(response.get('plan',{}).get('operation','Analysis')).replace('_',' ').capitalize()+' · '+entry_id[:4],key='ai-save-name-'+entry_id)
            include=st.checkbox('Include my question text in the saved block',key='ai-save-question-'+entry_id)
            st.caption('Saving is explicit and local. Evidence can contain selected source values; conversation history itself stays in this browser session.')
            if st.button('Save verified analysis',key='ai-save-'+entry_id):
                if name in workspace.saved_objects('block'):st.warning('A saved block already uses that name. Choose another name to preserve it.')
                else:
                    if response.get('plan',{}).get('version')==1:save_answer(workspace,name,response,question=question if include else None)
                    else:workspace.save_object('block',name,{'analysis':response['engine_manifest'],'evidence':primary,**({'figure':response['figures'][0]} if response['figures'] else {})})
                    st.success('Saved verified results, definitions and evidence in Reports.')


def _ask(question):
    # Callback runs before the next script pass, so the question box can be updated; the
    # follow-up is then planned and validated exactly like a typed question.
    st.session_state['analyst-question']=question;st.session_state['analyst-autorun']=True


def render_interpretation(interpretation,entry_id):
    """Model wording over computed values; every displayed number was matched to the result table."""
    if not interpretation:return
    if interpretation.get('status')!='ok':
        if interpretation.get('status')=='unavailable':st.caption(plain(interpretation.get('message','')))
        return
    with st.container(border=True,key='ai-interpretation-'+entry_id):
        st.markdown('**AI interpretation** · '+plain(interpretation['headline']))
        if interpretation['findings']:st.markdown('\n'.join('- '+plain(item) for item in interpretation['findings']))
        for caveat in interpretation['caveats']:st.caption('Caveat · '+plain(caveat))
        checked=interpretation.get('numbers_checked',0);removed=interpretation.get('removed',[])
        note=f'{checked} number(s) checked against the computed result.' if checked else 'No figures cited; wording only.'
        if removed:note+=f' {len(removed)} statement(s) removed: '+', '.join(sorted({r["reason"] for r in removed}))+'.'
        st.caption(note+' Observations, not causes. Local model · '+plain((interpretation.get('backend') or {}).get('model') or 'Ollama'))
        if interpretation['follow_ups']:
            st.caption('Suggested next questions · each is planned and validated before anything runs')
            columns=st.columns(len(interpretation['follow_ups']))
            for index,(column,question) in enumerate(zip(columns,interpretation['follow_ups'])):
                column.button(question,key=f'ai-follow-{entry_id}-{index}',on_click=_ask,args=(question,),width='stretch')


def render(engine,profile,metric,scope):
    st.subheader('Ask. Verify. Follow the evidence.')
    st.write('Ask about record counts, verified numeric statistics, reviewed business measures, groups, distributions and time. The model plans; InsightPilot calculates.')
    mode=st.radio('Analyst mode',['Standard analysis','Local AI Analyst'],horizontal=True,key='analyst-mode')
    config=None;available=True
    explain=False
    if mode=='Local AI Analyst':
        config,available=local_controls()
        explain=st.toggle('Explain results with the local model',value=True,key='ai-explain-results',
            help='After the exact calculation, the local model words the computed result table (aggregates only, at most 40 rows; never record previews). Every number it writes is checked against that table, and causal claims are removed.')
        with st.expander('Fields sent to the local planner'):
            field_scope=st.multiselect('Planning fields',engine.info['columns'],default=engine.info['columns'][:60],max_selections=60,key='ai-field-scope-'+scope)
            st.caption('Only field metadata, reviewed definitions and bounded intent context are sent. Full-table calculations remain in DuckDB.')
    else:
        field_scope=None;st.caption('Standard analysis · deterministic · Ollama is optional.')
    with st.expander('External planning · explicit opt-in'):
        provider=st.checkbox('Use optional external model for planning',disabled=mode!='Standard analysis')
        external=provider and mode=='Standard analysis'
        consent=st.checkbox('I authorize sending this question, column names and selected metric definition. No source records or calculated values will be sent.',disabled=not external)
        st.caption('Separate existing provider: summary/groups/trend plans only. Never used as a fallback for Local AI.')
    conversation=st.session_state.setdefault('analyst-conversation',Conversation())
    mode_key='external-optin' if external else 'local:'+hashlib.sha256(json.dumps(asdict(config),sort_keys=True).encode()).hexdigest() if config else 'standard'
    if conversation.bind(engine,metric,mode_key):st.info('The data or analytical context changed. Started a new conversation; previous results are not reused.')
    if st.button('Start new conversation'):
        conversation.clear();st.session_state.pop('analyst-current-response',None);st.session_state['analyst-question']='';st.rerun()
    st.session_state.setdefault('analyst-question','Summarize the selected metric')
    with st.expander('Suggested questions'):
        for index,question in enumerate(examples(engine,profile)):
            if st.button(question,key='ai-example-'+str(index)):
                st.session_state['analyst-question']=question
    question=st.text_input('Your analytical question',key='analyst-question',placeholder='Ask a question or follow up on the last validated analysis')
    st.caption('Shared grouping: '+str(engine.context.comparison)+' · date basis: '+str(engine.context.date_basis)+' · sidebar metric: '+metric.name+'. Explicit question-specific measures are labeled separately.')
    blocked=mode=='Local AI Analyst' and not available
    clicked=st.button('Calculate evidence-backed answer',type='primary',disabled=blocked)
    autorun=st.session_state.pop('analyst-autorun',False) and not blocked
    if clicked or autorun:
        if external:
            assert_current(engine)
            legacy=assistant.answer(engine,question,metric,dimension=engine.context.comparison,date_column=engine.context.date_basis,use_provider=True,consent=consent)
            assert_current(engine)
            result={'status':'ok','answer':legacy['answer'],'primary':legacy['evidence'],'evidence':[legacy['evidence']],
                'definitions':[metric.dictionary(engine.info)],'figures':[], 'limitations':[legacy['limitations']],
                'plan':{'external_legacy_plan':legacy['plan']},'engine_manifest':configuration(engine,metric),
                'backend':{'backend':legacy['mode'],'model':os.environ.get('INSIGHTPILOT_LLM_MODEL'),'usage':legacy['usage']}}
            if legacy['plan']['analysis']=='groups':result['figures']=[visuals.ranked(legacy['evidence'],metric.name+' by '+str(engine.context.comparison),metric.units).to_json()]
            elif legacy['plan']['analysis']=='trend':result['figures']=[visuals.trend(legacy['evidence'],metric.name+' over time',metric.units).to_json()]
            conversation.add(question,result,result['backend'])
        else:
            with st.spinner('Waiting for local model, then validating and calculating…' if config else 'Validating and calculating…'):
                result=analyze_question(engine,profile,metric,question,conversation,mode='local' if config else 'standard',config=config,field_scope=field_scope,interpret_results=explain)
            st.session_state['analyst-current-response']=result
    if conversation.pending:
        pending=conversation.pending
        with st.container(border=True):
            st.info('Approve the proposed eligible numerator and denominator before executing this new rate.')
            st.json(pending['plan']['measures'])
            confirmed=st.checkbox('I confirm this outcome definition, eligible denominator and missing-value policy',key='ai-rate-approval-'+hashlib.sha256(json.dumps(pending['plan'],sort_keys=True).encode()).hexdigest()[:12])
            if st.button('Confirm definition and calculate',disabled=not confirmed):approve_pending(engine,profile,metric,conversation);st.rerun()
    for entry in conversation.entries:
        with st.chat_message('user'):st.markdown(plain(entry['question']))
        with st.chat_message('assistant'):render_result(entry['response'],entry['id'],engine.workspace,question=entry['question'])
    saved={name:block for name,block in engine.workspace.saved_objects('block').items() if 'analysis_plan' in block}
    if saved:
        with st.expander('Replay a saved analyst calculation'):
            chosen=st.selectbox('Saved analyst result',list(saved))
            if st.button('Replay saved calculation without a model'):
                result=replay_answer(engine.workspace,saved[chosen]);st.caption('Replays the saved source/version and context. The active table version is unchanged.')
                render_result(result,'replay-'+hashlib.sha256(chosen.encode()).hexdigest()[:10],None)
    st.caption('Conversation memory is bounded to six turns in this browser session. New conversation removes it; context changes isolate workspaces and invalidate incompatible results. No prompts or rows are automatically saved to disk.')
