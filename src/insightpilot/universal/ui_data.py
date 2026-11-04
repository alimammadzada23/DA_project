"""Streamlit data management; analytical implementation lives in independent services."""
import json
import csv
import io
from pathlib import Path
import re
import uuid

import pandas as pd
import streamlit as st

from .connectors import database_tables, import_api, import_database, import_postgres
from .metrics import Metric, save_metric
from .relationships import inspect_relationship, join_preview, load_relationships, save_relationship, suggest_relationships

ROLES=['unassigned','date','identifier','customer','product','transaction','revenue','expense','quantity',
       'price','geography','company','category','status','duration','text','measure']


def data_hub(workspace):
    st.subheader('Import and organize related tables')
    st.write('Each table keeps its own row grain, original file and version history. Imports are local; nothing is sent to an AI provider.')
    message_key='universal-import-messages-'+str(workspace.directory)
    for level,message in st.session_state.pop(message_key,[]):
        getattr(st,level)(message)
    tabs=st.tabs(['Files','Local databases','HTTP API','PostgreSQL','Event logs'])
    with tabs[0]:
        from .import_preview import parsing_preview
        files=st.file_uploader('Dataset files',type=['csv','tsv','xlsx','parquet','json','jsonl','ndjson'],accept_multiple_files=True,key=message_key+'-files')
        delimiter=st.selectbox('Delimited text separator',[None,',','\t',';','|'],format_func=lambda v:'By extension: comma for CSV, tab for TSV' if v is None else repr(v))
        if files:
            with st.expander('Preview source parsing',expanded=True):
                for upload in files:
                    if Path(upload.name).suffix.lower() not in ('.csv','.tsv'): continue
                    sep=delimiter or ('\t' if upload.name.lower().endswith('.tsv') else ',')
                    try:
                        preview=parsing_preview(upload.getbuffer(),sep)
                        st.caption(upload.name+' · UTF-8 · selected separator '+repr(sep)+' · '+str(preview['field_count'])+' fields detected. ISO dates recognized; ambiguous dates need explicit conversion.')
                        if preview['warning']:st.warning(preview['warning'])
                        st.dataframe(pd.DataFrame(preview['rows'],columns=preview['columns']),hide_index=True,width='stretch')
                    except (UnicodeDecodeError,ValueError,csv.Error):st.warning('Preview could not parse these bytes as UTF-8 delimited text. Import checks the complete source and retains rejected bytes.')
        with st.form('universal-upload'):
            sheet=st.text_input('Excel sheet name (blank = first sheet)')
            synthetic=st.checkbox('These files are synthetic test data')
            submitted=st.form_submit_button('Import tables')
        if submitted:
            messages=[]
            if not files: messages.append(('warning','Choose at least one file.'))
            for file in files or []:
                folder=workspace.directory/'incoming'; folder.mkdir(exist_ok=True)
                path=folder/(uuid.uuid4().hex+Path(file.name).suffix.lower())
                path.write_bytes(file.getvalue())
                name=re.sub(r'[^A-Za-z0-9_]+','_',Path(file.name).stem).strip('_')[:50] or 'dataset'
                if not name[0].isalpha(): name='table_'+name
                base=name; index=2
                while name in workspace.tables(): name=base+'_'+str(index); index+=1
                try:
                    with st.spinner(f'Importing {file.name}; checking all rows…'):
                        info=workspace.add_file(path,name,sheet=sheet or 0,delimiter=delimiter,provenance={
                            'filename':file.name,'origin':'synthetic' if synthetic else 'user_upload'})
                    messages.append(('success',f'{name}: {info["rows"]:,} rows imported. {len(info["columns"])} fields detected. Review meaning and quality before defining metrics.'))
                    for warning in info.get('import_warnings',[]):messages.append(('warning',warning+' Source bytes were retained; re-import with the intended separator into a new table if needed.'))
                except Exception as exc: messages.append(('error',f'{file.name}: {exc}. Other successful imports are retained; this file did not create a partial table.'))
                finally: path.unlink(missing_ok=True)
            st.session_state[message_key]=messages
            st.rerun()
        st.caption('CSV/TSV, flat Parquet and JSON stream into DuckDB. Up to 512 MiB, 5 million rows and 150 columns. '
                   'Excel is bounded to 100 MiB/500,000 rows. JSON must be a flat array; JSON Lines uses one object per line. '
                   'Malformed input is rejected atomically, with its original file and a rejection receipt retained.')
    with tabs[1]:
        path=st.text_input('Local SQLite or DuckDB source path',key='universal-db-path')
        if path and Path(path).is_file():
            try:
                source_table=st.selectbox('Source database table',database_tables(path))
                target=st.text_input('Imported table name','database_table')
                if st.button('Import read-only database snapshot'):
                    import_database(workspace,path,source_table,target); st.rerun()
            except Exception as exc: st.error(str(exc))
        st.caption('Source data is opened read-only. The extracted table is copied into this workspace; source database files are not edited.')
    with tabs[2]:
        with st.form('universal-api'):
            url=st.text_input('HTTPS API base URL')
            name=st.text_input('Table name','api_records')
            records_key=st.text_input('Top-level record array key (blank = root array)')
            page=st.text_input('Page-number parameter','page'); size=st.text_input('Page-size parameter','per_page')
            per_page=st.number_input('Records per page',min_value=1,max_value=1000,value=100)
            token=st.text_input('Bearer-token environment variable name (optional)')
            confirmed=st.checkbox('I have verified this API’s pagination contract and am authorized to read it.')
            submitted=st.form_submit_button('Fetch configured API')
        if submitted:
            if not confirmed: st.warning('Confirm the source and pagination contract before requesting it.')
            else:
                import_api(workspace,url,name,records_key=records_key or None,page_parameter=page,
                           size_parameter=size,page_size=int(per_page),token_variable=token or None)
                st.rerun()
        st.caption('At most 50 pages / 100,000 records, with rate spacing and an empty terminal page required. '
                   'No redirects or arbitrary next-page links. Credentials are read from secure process configuration and never saved in receipts. Network policy still applies.')
    with tabs[3]:
        with st.form('universal-postgres'):
            variable=st.text_input('DSN environment variable name','INSIGHTPILOT_POSTGRES_DSN')
            schema=st.text_input('Source schema','public'); table=st.text_input('Source table')
            target=st.text_input('Target table name','postgres_records')
            submitted=st.form_submit_button('Import PostgreSQL snapshot')
        if submitted:
            try: import_postgres(workspace,schema,table,target,dsn_variable=variable)
            except ValueError: raise
            except Exception as exc:
                raise ValueError(f'PostgreSQL import could not finish ({type(exc).__name__}). Verify the configured read-only connection, schema/table, network access and source permissions.') from exc
            st.rerun()
        st.caption('Optional postgres extra and a configured reachable database are required. Server cursor, read-only transaction and statement timeout are enforced. No credentials are entered here.')
    with tabs[4]:
        st.caption('Import XES/XES.GZ using the existing streaming parser. The original log and parser database are retained. Cases and events enter the same catalog as all other datasets; review their definitions before analysis.')
        xes=st.file_uploader('XES event log',type=['xes','gz'])
        prefix=st.text_input('Event table prefix','eventlog')
        if st.button('Import event log'):
            if xes is None: st.warning('Choose an event log first.')
            else:
                from .process import import_xes
                incoming=workspace.directory/'incoming'; incoming.mkdir(exist_ok=True)
                suffix='.xes.gz' if xes.name.lower().endswith('.gz') else '.xes'
                path=incoming/(uuid.uuid4().hex+suffix); path.write_bytes(xes.getvalue())
                import_xes(workspace,path,prefix)
                path.unlink(); st.success('Imported cases and events into the shared catalog.'); st.rerun()
    tables=workspace.tables()
    if tables:
        st.subheader('Table catalog')
        st.dataframe(pd.DataFrame([{'table':k,'rows':v['rows'],'fields':len(v['columns']),
            'source':v['source']['origin'],'grain':v['grain'],'versions':len(v['versions']),'original file':v['source']['filename'],'imported at':v['created_at']} for k,v in tables.items()]),hide_index=True,width='stretch')
    if len(tables)>=2: relationship_editor(workspace)


def relationship_editor(workspace):
    st.subheader('Model relationships')
    st.caption('Keys are suggestions to investigate, not proof of entity identity. Exact text equality preserves leading zeros; NULL keys do not match.')
    st.caption('Automatic screening checks at most 50 same-name key candidates; additional relationships can be validated manually below.')
    suggestions=suggest_relationships(workspace)
    if suggestions:
        st.dataframe(pd.DataFrame([{'left':r['left'],'left key':r['left_key'],'right':r['right'],'right key':r['right_key'],
            'cardinality':r['cardinality'],'unmatched left':r['left_unmatched_rows'],'basis':r['basis']} for r in suggestions]),hide_index=True,width='stretch')
    default=suggestions[0] if suggestions else None
    names=list(workspace.tables()); left,right=st.columns(2)
    l=left.selectbox('Left fact table',names,index=names.index(default['left']) if default else 0,key='rel-left')
    r=right.selectbox('Right related table',names,index=names.index(default['right']) if default else 1,key='rel-right')
    lc,rc=workspace.columns(l),workspace.columns(r)
    lk=left.selectbox('Left key',lc,index=lc.index(default['left_key']) if default and default['left']==l else 0,key='rel-lkey-'+l)
    rk=right.selectbox('Right key',rc,index=rc.index(default['right_key']) if default and default['right']==r else 0,key='rel-rkey-'+r)
    if l==r: st.info('Select two different tables.'); return
    relation=inspect_relationship(workspace,l,lk,r,rk)
    st.write(f'**{relation["cardinality"]}** · {relation["left_rows"]:,} left rows → {relation["left_join_rows"]:,} rows after a left join')
    st.write(f'Unmatched: {relation["left_unmatched_rows"]:,} left / {relation["right_unmatched_rows"]:,} right. '
             f'Duplicate key occurrences: {relation["left_duplicate_keys"]:,} left / {relation["right_duplicate_keys"]:,} right.')
    how=st.selectbox('Join preview type',['left','inner','full'])
    st.dataframe(join_preview(workspace,relation,how),hide_index=True,width='stretch')
    acknowledged=st.checkbox('I understand and accept left-side row multiplication',disabled=relation['left_join_rows']<=relation['left_rows'])
    if st.button('Save validated relationship'):
        save_relationship(workspace,l+'_'+lk+'__'+r+'_'+rk,relation,allow_multiplication=acknowledged)
        st.success('Relationship saved with its versions and cardinality receipt. Analytical metrics still require a unique right-side key.')
    with st.expander('Relationship evidence'): st.json(relation)


def quality_editor(workspace,table,report):
    info=workspace.info(table)
    st.subheader('Understand every field')
    st.write(f'{report["rows"]:,} rows · {len(report["columns"])} fields · {report["exact_duplicate_rows"]:,} exact duplicate rows retained')
    query=st.text_input('Search the column catalog')
    catalog=[{'Field':c['column'],'Type hypothesis':c['inferred_type'],'Missing':c['missing'],
        'Distinct':c['distinct'],'Missing markers?':c['potential_missing_markers'],'Finite numbers':c['numeric_values'],'ISO dates':c['recognized_dates']}
        for c in report['columns']
        if query.lower() in c['column'].lower()]
    st.dataframe(pd.DataFrame(catalog),hide_index=True,width='stretch')
    st.info(report['semantic_notice'])
    for warning in info.get('import_warnings',[]):st.warning(warning+' Original bytes and this table remain intact; re-import with an explicit separator if appropriate.')
    col=st.selectbox('Inspect field meaning and statistics',info['columns'])
    detail=next(c for c in report['columns'] if c['column']==col)
    for hypothesis in detail['candidates']:
        st.write('**Possible '+hypothesis['role']+'** · '+hypothesis['confidence']+'. '+hypothesis['basis'])
    if detail['key_candidate']: st.caption('Values are unique and nonmissing in this version; business key identity still needs confirmation.')
    if detail['statistics']:
        st.dataframe(pd.DataFrame(detail['statistics'].items(),columns=['Statistic','Value']),hide_index=True,width='stretch')
    with st.expander('Review this field and row grain'):
        sem=info.get('semantics',{})
        with st.form('field-review-'+table+'-'+col):
            role=st.selectbox('Field role',ROLES,index=ROLES.index(info['roles'].get(col,'unassigned')))
            unit=st.text_input('Field units / currency',sem.get('units',{}).get(col,''))
            purpose=st.text_input('Timestamp purpose',sem.get('date_purposes',{}).get(col,''))
            grain=st.text_input('Reviewed row grain',info['grain'])
            basis=st.checkbox('Use this field as the shared date basis',value=sem.get('date_basis')==col)
            reviewed=st.checkbox('I confirm these field definitions and row grain')
            apply=st.form_submit_button('Approve field definition')
        if apply:
            if not reviewed: st.warning('Confirm the definitions before approval.')
            else:
                roles=info['roles']|{col:role}; units=sem.get('units',{})|{col:unit}; purposes=sem.get('date_purposes',{})|{col:purpose}
                selected=col if basis else None if sem.get('date_basis')==col else sem.get('date_basis')
                workspace.approve_semantics(table,roles,grain,units=units,date_purposes=purposes,date_basis=selected); st.rerun()
    with st.expander('Dataset understanding report',expanded=True):
        st.write(report['understanding']['type_hypothesis'])
        st.write(report['understanding']['row_grain_hypothesis'])
        st.write('Row grain: '+info['grain'])
        st.write('**Possible analyses**'); st.markdown('\n'.join('- '+v for v in report['possible_analyses']))
        st.write('**Definitions and limitations**'); st.markdown('\n'.join('- '+v for v in report['unsupported_without_definitions']))
        date_fields={c['column']:c['date_range'] for c in report['columns'] if 'date_range' in c}
        if date_fields:
            st.write('**Observed date coverage**')
            for field,(start,end) in date_fields.items(): st.write(f'{field}: {start} to {end}. Source completeness is not inferred.')
    with st.expander('Review and correct semantic roles'):
        with st.form('universal-roles-'+str(workspace.directory)+'-'+table):
            grain=st.text_input('What does one row represent?',info['grain'])
            semantics=info.get('semantics',{})
            frame=pd.DataFrame({'column':info['columns'],'role':[info['roles'].get(c,'unassigned') for c in info['columns']],
                'units':[semantics.get('units',{}).get(c,'') for c in info['columns']],
                'date purpose':[semantics.get('date_purposes',{}).get(c,'') for c in info['columns']]})
            changed=st.data_editor(frame,hide_index=True,disabled=['column'],width='stretch',
                column_config={'role':st.column_config.SelectboxColumn('Approved role',options=ROLES,required=True)})
            date_options=[None]+[c['column'] for c in report['columns'] if c['recognized_dates']]
            basis=st.selectbox('Default analytical date basis',date_options,
                index=date_options.index(semantics.get('date_basis')) if semantics.get('date_basis') in date_options else 0,
                format_func=lambda x:'No date basis selected' if x is None else x)
            st.caption('Describe each timestamp separately: observation, transaction, completion, ingestion, scheduled or another event. A trend never silently chooses the first timestamp.')
            approved=st.checkbox('I have reviewed the row grain, field definitions and units.')
            submitted=st.form_submit_button('Save reviewed definitions')
        if submitted:
            if not approved: st.warning('Review the definitions before approval.')
            else:
                workspace.approve_semantics(table,dict(zip(changed.column,changed.role)),grain,
                    units=dict(zip(changed.column,changed.units)),date_purposes=dict(zip(changed.column,changed['date purpose'])),date_basis=basis); st.rerun()
    with st.expander('Prepare data reversibly'):
        operation=st.selectbox('Preparation operation',['numeric','date','date_parts','trim','nullify','deduplicate','rename','select'])
        col=st.selectbox('Preparation column',info['columns'],key='prepare-column-'+table)
        spec={'kind':operation,'column':col}
        if operation=='numeric':
            spec['decimal']=st.selectbox('Decimal separator',['.',','])
            spec['thousands']=st.selectbox('Thousands separator',[None,',','.',' '],format_func=lambda v:'None' if v is None else repr(v))
        elif operation=='date': spec['format']=st.selectbox('Explicit date format',[None,'%d/%m/%Y','%m/%d/%Y','%Y-%m-%d','%Y'],format_func=lambda v:'ISO / UTC dates' if v is None else v)
        elif operation=='date_parts':
            for part in ('year','month','day'): spec[part]=st.selectbox('Date '+part+' column',info['columns'],key='date-part-'+part)
            spec['new_name']=st.text_input('New date column name','record_date')
        elif operation=='rename': spec['new_name']=st.text_input('New column name')
        elif operation=='select': spec['columns']=st.multiselect('Columns to retain',info['columns'],default=info['columns'])
        elif operation=='nullify': spec['value']=st.text_input('Exact literal to treat as missing')
        st.caption('A new version is created. Source values and earlier versions remain available. Invalid conversions become NULL with original values and row references in a separate error ledger. Roles must be reviewed again after preparation.')
        if st.button('Apply preparation to a new version'):
            workspace.transform(table,spec); st.rerun()
    versions=info['versions']
    version=st.selectbox('Version history / undo',list(range(len(versions))),index=next(i for i,v in enumerate(versions) if v['table']==info['current']),
        format_func=lambda i:f'{i+1} · {versions[i]["operation"]["kind"]} · {versions[i]["rows"]:,} rows · {versions[i]["errors"]:,} conversion errors')
    if st.button('Activate selected version'):
        workspace.activate(table,versions[version]['table']); st.rerun()
    errors=workspace.conversion_errors(table)
    if len(errors):
        st.warning('Conversion errors were retained, not silently discarded. Showing at most 1,000; the complete ledger remains in the workspace database.')
        st.dataframe(errors,hide_index=True,width='stretch')
    with st.expander('Full source and transformation receipt'): st.json(info)


def metric_editor(workspace,table):
    with st.expander('Define a reusable metric'):
        st.caption('Use sum("amount"), avg("duration"), median("value"), count(), count("field"), distinct("customer") '
                   ', valid("numeric field"), count_if("delay", ">", 15) and + − × /. '
                   'valid counts finite numeric inputs. Ratios aggregate their numerator and denominator separately. A zero denominator returns unavailable.')
        with st.form('universal-metric-'+str(workspace.directory)+'-'+table):
            name=st.text_input('Metric name'); formula=st.text_input('Aggregate formula','count()')
            description=st.text_input('Metric definition / interpretation')
            unit=st.text_input('Units or currency','records')
            limitations=st.text_input('Known metric limitations','Review source coverage, missing inputs, row grain and double counting.')
            denominator=st.text_input('Eligible denominator / missing-value policy','')
            currency=st.text_input('Currency code (optional)','')
            submitted=st.form_submit_button('Validate and save metric')
        if submitted:
            save_metric(workspace,table,Metric(name,formula,description,unit,limitations,denominator,currency))
            st.session_state['select-new-metric-'+str(workspace.directory)+'-'+table]=name
            st.rerun()
        st.code('quantile("value", 0.9)\nsum_if("amount", "status", "==", "eligible")\nsum("quantity" * "price")\ncount_if("outcome", "==", "Yes") / count("outcome")\nweighted_avg("rate", "eligible")',language='python')
        st.caption('Derived row arithmetic is compiled safely. Quantiles and weighted averages are recomputed from eligible rows; rates are never summed. Use an explicit eligible denominator and reviewed currency/units.')
