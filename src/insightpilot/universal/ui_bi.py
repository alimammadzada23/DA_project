"""BI Studio: a Power BI-style workspace (model, DAX measures, report canvas) over the DuckDB engine."""
import hashlib
import json
import uuid

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from . import visuals
from .dax import DATE_COLUMNS, DATE_TABLE, DAXError, Field, Model, check, distinct_values, evaluate, finalize, parse, slicer, Compiler

STORE='bi.model:'
FORMATS=['Auto','Whole number','Decimal','Percent','Currency / unit']
VISUALS=['Card','Bar','Column','Line','Donut','Matrix']
CHEATSHEET=[
    ('Aggregations','SUM, AVERAGE, MIN, MAX, MEDIAN, COUNT, COUNTA, COUNTBLANK, COUNTROWS, DISTINCTCOUNT, DISTINCTCOUNTNOBLANK',"Total Value = SUM(Facts[amount])"),
    ('Iterators','SUMX, AVERAGEX, MINX, MAXX over the table or FILTER(...)',"Revenue = SUMX(Facts, Facts[qty] * Facts[price])"),
    ('Filter context','CALCULATE with column filters, IN {..}, FILTER, ALL, REMOVEFILTERS, ALLEXCEPT, KEEPFILTERS',"North Value = CALCULATE([Total Value], Facts[region] = \"North\")"),
    ('Percent of total','DIVIDE + CALCULATE + ALL',"% of Region = DIVIDE([Total Value], CALCULATE([Total Value], ALL(Facts[region])))"),
    ('Time intelligence',"TOTALYTD/QTD/MTD, DATESYTD, SAMEPERIODLASTYEAR, DATEADD, PREVIOUSMONTH/QUARTER/YEAR/DAY over 'Date'[Date]","Value LY = CALCULATE([Total Value], SAMEPERIODLASTYEAR('Date'[Date]))"),
    ('Logic and ranking','IF, SWITCH, AND, OR, NOT, ISBLANK, COALESCE, HASONEVALUE, SELECTEDVALUE, RANKX',"Region Rank = RANKX(ALL(Facts[region]), [Total Value])"),
    ('Calculated columns','Row expressions: arithmetic, &, IF, DIVIDE, ROUND, YEAR/MONTH/DAY, UPPER/LOWER/LEN',"Size = IF(Facts[amount] > 100, \"Large\", \"Small\")"),
]


# ------------------------------------------------------------- persistence
def stored(engine):
    value=engine.workspace.saved_objects('layout').get(STORE+engine.table) or {}
    return {'measures':list(value.get('measures',[])),'columns':list(value.get('columns',[])),'pages':dict(value.get('pages',{}))}


def persist(engine,state):
    engine.workspace.save_object('layout',STORE+engine.table,state)


def build_model(engine,state):
    measures=[];columns=[];problems=[]
    for item in state['columns']:
        try:Model.from_engine(engine,calculated=[*columns,item]);columns.append(item)
        except DAXError as exc:problems.append(f"Calculated column [{item['name']}]: {exc}")
    for item in state['measures']:
        try:parse(item['expression']);measures.append(item)
        except DAXError as exc:problems.append(f"Measure [{item['name']}]: {exc}")
    return Model.from_engine(engine,measures=measures,calculated=columns),problems


def fields(model):
    """Fields usable on visual axes and slicers, as Field objects with readable labels."""
    items=[Field(model.table,c) for c in model.columns]+[Field(model.table,c) for c in model.calculated]
    if model.date_basis:items+=[Field(DATE_TABLE,c) for c in DATE_COLUMNS]
    return items


def field_key(item):return f'{item.table}\x1f{item.column}'


def from_key(key):table,column=key.split('\x1f',1);return Field(table,column)


# --------------------------------------------------------------- formatting
def formatted(value,fmt,unit=''):
    if value is None or (isinstance(value,float) and pd.isna(value)):return '(Blank)'
    if isinstance(value,bool):return 'True' if value else 'False'
    if not isinstance(value,(int,float)):return str(value)
    if fmt=='Percent':return f'{value:.1%}'
    if fmt=='Whole number':return f'{value:,.0f}'
    if fmt=='Decimal':return f'{value:,.2f}'
    if fmt=='Currency / unit':return f'{value:,.2f} {unit}'.strip()
    return visuals.compact(value) if abs(value)>=1e4 else (f'{value:,.0f}' if float(value).is_integer() else f'{value:,.4g}')


def measure_meta(state,name):
    return next((m for m in state['measures'] if m['name']==name),{'format':'Auto','unit':''})


# ------------------------------------------------------------------- page
def bi_studio(engine,report,metric,scope,draw,evidence):
    state=stored(engine);model,problems=build_model(engine,state)
    for problem in problems:st.warning(problem+' It is kept but excluded until fixed.')
    st.caption(f"In DAX this table is **{engine.table}** — write {engine.table}[column]. "
               +("The automatic **Date** table follows the shared date basis ("+model.date_basis+")." if model.date_basis else 'Choose a date basis in the sidebar to unlock the Date table and time intelligence.')
               +' Sidebar filters always apply, like report-level filters.')
    report_tab,measures_tab,model_tab=st.tabs(['Report','Measures (DAX)','Model'])
    with report_tab:report_canvas(engine,model,state,scope,draw,evidence)
    with measures_tab:measures_view(engine,model,state)
    with model_tab:model_view(engine,model,state)


# ------------------------------------------------------------ measures tab
def templates(model,state):
    t=model.table;existing=[m['name'] for m in state['measures']]
    numeric=list(model.columns)+list(model.calculated)
    return existing,numeric,t


def measures_view(engine,model,state):
    left,right=st.columns([1.15,1],gap='large')
    with right:
        st.markdown('**Your measures**')
        if state['measures']:
            st.dataframe(pd.DataFrame([{'Measure':m['name'],'DAX':m['expression'],'Format':m.get('format','Auto')} for m in state['measures']]),
                         hide_index=True,width='stretch',height=min(420,38+35*len(state['measures'])))
            chosen=st.selectbox('Edit or delete a measure',[None]+[m['name'] for m in state['measures']],format_func=lambda x:'Choose a measure' if x is None else x,key='bi-pick-measure')
            a,b=st.columns(2)
            if chosen and a.button('Load into editor',width='stretch',key='bi-load-measure'):
                item=measure_meta(state,chosen)
                st.session_state.update({'bi-measure-name':item['name'],'bi-measure-dax':item['expression'],'bi-measure-format':item.get('format','Auto'),'bi-measure-unit':item.get('unit','')});st.rerun()
            if chosen and b.button('Delete measure',width='stretch',key='bi-delete-measure'):
                users=[m['name'] for m in state['measures'] if m['name']!=chosen and '['+chosen.casefold()+']' in m['expression'].casefold()]
                if users:st.warning('Used by '+', '.join(users)+'. Update those measures first.')
                else:
                    state['measures']=[m for m in state['measures'] if m['name']!=chosen];persist(engine,state);st.rerun()
        else:st.info('No measures yet. Start with a quick measure on the left, then build on it with CALCULATE.')
        with st.expander('DAX cheat sheet · supported functions'):
            for title,functions,example in CHEATSHEET:
                st.markdown(f'**{title}** · {functions}');st.code(example.replace('Facts',model.table),language='sql')
            st.caption('Practice subset of DAX evaluated by InsightPilot on DuckDB. Not supported: VAR/RETURN, USERELATIONSHIP, measures inside FILTER conditions, calculated tables.')
    with left:
        with st.expander('Quick measure',expanded=not state['measures']):
            quick(model,state)
        st.markdown('**Measure editor**')
        name=st.text_input('Measure name',key='bi-measure-name',placeholder='Total Value')
        dax=st.text_area('DAX expression',key='bi-measure-dax',height=150,placeholder=f'SUM({model.table}[amount])')
        a,b=st.columns(2)
        fmt=a.selectbox('Format',FORMATS,key='bi-measure-format')
        unit=b.text_input('Unit label',key='bi-measure-unit',placeholder='EUR',disabled=fmt!='Currency / unit')
        check_col,save_col=st.columns(2)
        if check_col.button('Check measure',width='stretch',key='bi-check'):
            try:
                result=check(engine,model,dax,name.strip() or 'Check')
                st.metric(name.strip() or 'Result',formatted(result['rows'][0][0],fmt,unit))
                with st.expander('How InsightPilot evaluated your DAX (generated SQL)'):st.code(result['dax_sql'],language='sql')
            except DAXError as exc:st.error(str(exc))
        if save_col.button('Save measure',type='primary',width='stretch',key='bi-save-measure'):
            try:
                check(engine,model,dax,name)
                canonical=Model.from_engine(engine)._check_name(name,'measure')
                state['measures']=[m for m in state['measures'] if m['name'].casefold()!=canonical.casefold()]+[{'name':canonical,'expression':dax.strip(),'format':fmt,'unit':unit.strip()}]
                persist(engine,state);st.success(f'Saved [{canonical}]. Use it on the Report tab or reference it as [{canonical}].')
            except DAXError as exc:st.error(str(exc))


def quick(model,state):
    t=model.table;existing=[m['name'] for m in state['measures']]
    kinds=['Sum of a column','Average of a column','Count rows','Distinct count','Filtered value (CALCULATE)','Percent of total by field']
    if existing:kinds+=['Year to date','Same period last year','Year-over-year %','Month-over-month change','Rank by field']
    kind=st.selectbox('Pattern',kinds,key='bi-quick-kind')
    columns=list(model.columns)+list(model.calculated);dax=None;name=None
    if kind in ('Sum of a column','Average of a column','Distinct count'):
        col=st.selectbox('Column',columns,key='bi-quick-col')
        fn={'Sum of a column':'SUM','Average of a column':'AVERAGE','Distinct count':'DISTINCTCOUNT'}[kind]
        dax=f'{fn}({t}[{col}])';name={'SUM':'Total ','AVERAGE':'Average ','DISTINCTCOUNT':'Distinct '}[fn]+col
    elif kind=='Count rows':dax=f'COUNTROWS({t})';name='Rows'
    elif kind=='Filtered value (CALCULATE)':
        base=st.selectbox('Base measure',existing or ['(create a measure first)'],key='bi-quick-base')
        col=st.selectbox('Filter column',columns,key='bi-quick-fcol');value=st.text_input('Equals value',key='bi-quick-value')
        if existing and value:dax=f'CALCULATE([{base}], {t}[{col}] = "{value.replace(chr(34),chr(34)*2)}")';name=f'{base} · {value}'
    elif kind=='Percent of total by field':
        base=st.selectbox('Base measure',existing or ['(create a measure first)'],key='bi-quick-base2');col=st.selectbox('Field',columns,key='bi-quick-pcol')
        if existing:dax=f'DIVIDE([{base}], CALCULATE([{base}], ALL({t}[{col}])))';name=f'% of {col} total'
    else:
        base=st.selectbox('Base measure',existing,key='bi-quick-base3')
        if not model.date_basis:st.info('Time patterns need the shared date basis (sidebar).')
        ly=f"CALCULATE([{base}], SAMEPERIODLASTYEAR('Date'[Date]))"
        if kind=='Year to date':dax=f"TOTALYTD([{base}], 'Date'[Date])";name=f'{base} YTD'
        elif kind=='Same period last year':dax=ly;name=f'{base} LY'
        elif kind=='Year-over-year %':dax=f'DIVIDE([{base}] - {ly}, {ly})';name=f'{base} YoY %'
        elif kind=='Month-over-month change':dax=f"[{base}] - CALCULATE([{base}], PREVIOUSMONTH('Date'[Date]))";name=f'{base} MoM Δ'
        elif kind=='Rank by field':
            col=st.selectbox('Rank across',columns,key='bi-quick-rcol');dax=f'RANKX(ALL({t}[{col}]), [{base}])';name=f'{base} rank by {col}'
    if dax:
        st.code(f'{name} = {dax}',language='sql')
        if st.button('Use in editor',key='bi-quick-use'):
            st.session_state.update({'bi-measure-name':name,'bi-measure-dax':dax,'bi-measure-format':'Percent' if '%' in name else 'Auto'});st.rerun()


# --------------------------------------------------------------- model tab
def model_view(engine,model,state):
    left,right=st.columns([1,1],gap='large')
    with left:
        st.markdown('**Model diagram**')
        def esc(text):return ''.join('\\'+ch if ch in '{}|<>"\\' else ch for ch in str(text))
        cols='\\l'.join([esc(c)+(' (calc)' if c in model.calculated else '') for c in [*model.columns,*model.calculated]][:24])+'\\l'
        graph=['digraph{rankdir=LR;bgcolor="transparent";node[shape=record,fontname="Helvetica",fontsize=16,color="#67E0CD",fontcolor="#E3EAF5"];edge[color="#94A5BD",fontcolor="#94A5BD",fontsize=14];',
               f'fact[label="{{{esc(model.table)} (fact)|{cols}}}"];']
        if model.date_basis:
            graph.append('date[label="{Date (automatic)|'+'\\l'.join(DATE_COLUMNS)+'\\l}"];')
            graph.append(f'date -> fact [label="1 → * on {esc(model.date_basis)}"];')
        graph.append('}')
        st.graphviz_chart('\n'.join(graph),width='stretch')
        if model.date_basis:
            span=engine.run(f"SELECT min({model.date_sql()}) AS first, max({model.date_sql()}) AS last FROM selected")
            first,last=span['rows'][0]
            st.caption(f'Date table spans {first} → {last} from {model.date_basis}. One-to-many relationship, single direction, like a marked date table.')
        else:st.info('No Date table yet. Choose a date basis in the sidebar (reviewed in Quality & Preparation).')
    with right:
        st.markdown('**Calculated columns**')
        if state['columns']:
            st.dataframe(pd.DataFrame([{'Column':c['name'],'DAX':c['expression']} for c in state['columns']]),hide_index=True,width='stretch')
            gone=st.selectbox('Remove a calculated column',[None]+[c['name'] for c in state['columns']],format_func=lambda x:'Choose a column' if x is None else x,key='bi-remove-col')
            if gone and st.button('Remove column',key='bi-remove-col-btn'):
                used=[m['name'] for m in state['measures'] if f'[{gone.casefold()}]' in m['expression'].casefold()]
                if used:st.warning('Used by '+', '.join(used)+'. Update those measures first.')
                else:state['columns']=[c for c in state['columns'] if c['name']!=gone];persist(engine,state);st.rerun()
        name=st.text_input('New column name',key='bi-col-name',placeholder='Order size')
        dax=st.text_area('Row expression',key='bi-col-dax',height=90,placeholder=f'IF({model.table}[amount] > 100, "Large", "Small")')
        a,b=st.columns(2)
        if a.button('Preview column',width='stretch',key='bi-col-preview'):
            try:
                trial=Model.from_engine(engine,calculated=[*state['columns'],{'name':name or 'Preview','expression':dax}])
                sql,_,_=trial.column_sql(model.table,name or 'Preview')
                preview=engine.run(finalize(f'SELECT {sql} AS value, count(*) AS rows FROM selected GROUP BY 1 ORDER BY rows DESC LIMIT 12',trial),limit=12)
                st.dataframe(pd.DataFrame(preview['rows'],columns=['Value','Rows']),hide_index=True,width='stretch')
            except DAXError as exc:st.error(str(exc))
        if b.button('Add column',type='primary',width='stretch',key='bi-col-add'):
            try:
                Model.from_engine(engine,calculated=[*state['columns'],{'name':name,'expression':dax}])
                state['columns']=[c for c in state['columns'] if c['name'].casefold()!=name.strip().casefold()]+[{'name':name.strip(),'expression':dax.strip()}]
                persist(engine,state);st.rerun()
            except DAXError as exc:st.error(str(exc))
        st.caption('Calculated columns are evaluated per row on the fly; the source table and its versions are never modified.')


# -------------------------------------------------------------- report tab
def report_canvas(engine,model,state,scope,draw,evidence):
    pages=state['pages'];names=list(pages)
    pending=st.session_state.pop('bi-page-pending',None)
    if pending is not None:
        if pending in names:st.session_state['bi-page']=pending
        else:st.session_state.pop('bi-page',None)
    if st.session_state.get('bi-page') not in names+['+ New page']:st.session_state.pop('bi-page',None)
    top=st.columns([2,1,1])
    current=top[0].selectbox('Report page',names+['+ New page'],key='bi-page') if names else '+ New page'
    if current=='+ New page':
        title=top[0].text_input('New page name',value='Page '+str(len(names)+1),key='bi-new-page')
        if top[1].button('Create page',type='primary',width='stretch',key='bi-create-page'):
            if not title.strip() or title.strip() in pages:st.warning('Choose a new, distinct page name.')
            else:pages[title.strip()]={'slicers':[],'visuals':[]};persist(engine,state);st.session_state['bi-page-pending']=title.strip();st.rerun()
        if not state['measures']:st.info('Create a measure on the Measures (DAX) tab first, then add visuals here.')
        return
    page=pages[current]
    if top[2].button('Delete page',width='stretch',key='bi-delete-page'):
        del pages[current];persist(engine,state);st.session_state['bi-page-pending']='';st.rerun()
    available={field_key(f):f for f in fields(model)}
    # Slicers filter every visual on the page; ALL() on that column removes them, as in Power BI.
    with st.expander('Page slicers · choose fields',expanded=False):
        chosen=st.multiselect('Slicer fields',list(available),default=[k for k in page['slicers'] if k in available],
                              format_func=lambda k:available[k].label,max_selections=4,key='bi-slicer-fields-'+current)
        if chosen!=page['slicers']:page['slicers']=chosen;persist(engine,state)
    conditions=[]
    if page['slicers']:
        columns=st.columns(len(page['slicers']))
        for column,key in zip(columns,page['slicers']):
            item=available[key]
            try:
                if item.table==DATE_TABLE and item.column=='Date':
                    span=engine.run(finalize(f'SELECT min({model.date_sql()}),max({model.date_sql()}) FROM selected',model))['rows'][0]
                    if span[0] is None:continue
                    picked=column.date_input('Date range',value=(span[0],span[1]),min_value=span[0],max_value=span[1],key='bi-slice-'+current+key)
                    if isinstance(picked,(list,tuple)) and len(picked)==2 and (picked[0]!=span[0] or picked[1]!=span[1]):
                        conditions.append(slicer(model,item.table,item.column,start=picked[0],end=picked[1]))
                else:
                    values,truncated=distinct_values(engine,model,item.table,item.column)
                    picked=column.multiselect(item.label,values,format_func=visuals.category_label,key='bi-slice-'+current+key,placeholder='All')
                    if picked:conditions.append(slicer(model,item.table,item.column,picked))
                    if truncated:column.caption('First 200 values listed.')
            except DAXError as exc:column.warning(str(exc))
    visuals_list=page['visuals']
    cards=[v for v in visuals_list if v['type']=='Card'];charts=[v for v in visuals_list if v['type']!='Card']
    if cards:
        row=st.columns(min(4,len(cards)))
        for index,visual in enumerate(cards):
            with row[index%len(row)]:render_card(engine,model,state,visual,conditions,current)
    grid=st.columns(2,gap='medium')
    for index,visual in enumerate(charts):
        with grid[index%2].container(border=True,key=f'bi-visual-{visual["id"]}'):
            render_visual(engine,model,state,visual,conditions,current,scope,draw,evidence)
    if not visuals_list:st.info('This page is empty. Add a card or chart below.')
    add_visual(engine,model,state,page,available)


def render_card(engine,model,state,visual,conditions,page):
    measure=visual['measures'][0];meta=measure_meta(state,measure)
    try:
        value=evaluate(engine,model,[measure],[],conditions)['rows'][0][0]
        delta=None
        if visual.get('compare'):
            other=evaluate(engine,model,[visual['compare']],[],conditions)['rows'][0][0]
            if isinstance(value,(int,float)) and isinstance(other,(int,float)) and other:delta=f'{(value-other)/abs(other):+.1%} vs {visual["compare"]}'
        with st.container(border=True,key=f'bi-card-{visual["id"]}'):
            st.metric(visual.get('title') or measure,formatted(value,meta.get('format','Auto'),meta.get('unit','')),delta,
                      help=f"{measure} = {model.measures.get(measure,'')}")
            with st.popover('⋯',help='Card options'):
                if st.button('Remove card',key=f'bi-rm-{visual["id"]}'):remove_visual(engine,state,page,visual)
    except DAXError as exc:st.error(f'{measure}: {exc}')


def remove_visual(engine,state,page,visual):
    state['pages'][page]['visuals']=[v for v in state['pages'][page]['visuals'] if v['id']!=visual['id']];persist(engine,state);st.rerun()


def render_visual(engine,model,state,visual,conditions,page,scope,draw,evidence):
    rows=[from_key(k) for k in visual['rows']];measures=visual['measures']
    title=visual.get('title') or (', '.join(measures)+(' by '+' & '.join(r.column for r in rows) if rows else ''))
    try:
        sort=None if visual['type']=='Line' or (rows and rows[0].table==DATE_TABLE) else measures[0]
        result=evaluate(engine,model,measures,rows,conditions,sort=sort,descending=True)
    except DAXError as exc:
        st.error(f'{title}: {exc}')
        if st.button('Remove visual',key=f'bi-rm-{visual["id"]}'):remove_visual(engine,state,page,visual)
        return
    data=pd.DataFrame(result['rows'],columns=result['columns'])
    if visual.get('top') and visual['type'] in ('Bar','Column','Donut') and len(data)>visual['top']:data=data.head(visual['top'])
    labels=[visuals.category_label(v) for v in data.iloc[:,0]] if rows else []
    meta=[measure_meta(state,m) for m in measures]
    if visual['type']=='Matrix' or not rows:
        shown=data.copy()
        for name,item in zip(measures,meta):shown[name]=shown[name].map(lambda v,f=item:formatted(v,f.get('format','Auto'),f.get('unit','')))
        for label in [r.label for r in rows]:shown[label]=shown[label].map(visuals.category_label)
        if result.get('total') and rows:
            total={r.label:'Total' if i==0 else '' for i,r in enumerate(rows)}|{name:formatted(v,item.get('format','Auto'),item.get('unit','')) for name,v,item in zip(measures,result['total'],meta)}
            shown=pd.concat([shown,pd.DataFrame([total])],ignore_index=True)
        st.markdown(f'**{title}**');st.dataframe(shown,hide_index=True,width='stretch',height=min(460,38+35*len(shown)))
    else:
        figure=go.Figure()
        for index,(name,item) in enumerate(zip(measures,meta)):
            values=data[name].tolist();color=visuals.COLORS[index%len(visuals.COLORS)]
            percent=item.get('format')=='Percent'
            text=[(f'{v:.1%}' if percent else visuals.compact(v)) if isinstance(v,(int,float)) else '' for v in values]
            if visual['type']=='Bar':figure.add_trace(go.Bar(y=labels,x=values,name=name,orientation='h',marker_color=color,text=text,textposition='outside',cliponaxis=False))
            elif visual['type']=='Column':figure.add_trace(go.Bar(x=labels,y=values,name=name,marker_color=color,text=text,textposition='outside',cliponaxis=False))
            elif visual['type']=='Line':figure.add_trace(go.Scatter(x=labels,y=values,name=name,mode='lines+markers',line={'color':color,'width':2.4},connectgaps=False))
            elif visual['type']=='Donut' and index==0:
                figure.add_trace(go.Pie(labels=labels,values=values,hole=.62,sort=False,marker={'colors':visuals.COLORS},textinfo='percent',name=name))
            if percent and visual['type']!='Donut':figure.update_yaxes(tickformat='.0%') if visual['type'] in ('Column','Line') else figure.update_xaxes(tickformat='.0%')
        visuals.style(figure,title,None if visual['type']=='Bar' else rows[0].column,None)
        figure.update_layout(height=380,margin={'l':16,'r':72 if visual['type']=='Bar' else 48,'t':64,'b':40},showlegend=len(measures)>1 or visual['type']=='Donut')
        if visual['type']=='Donut':figure.update_layout(legend={'orientation':'v','yanchor':'middle','y':.5,'xanchor':'left','x':1.02},margin={'r':120})
        if visual['type']=='Bar':figure.update_yaxes(autorange='reversed')
        if visual['type']=='Donut' and len(labels)>8:st.caption('Donuts read best with 8 or fewer categories; consider a bar chart.')
        draw(figure,f'bi-{scope}-{visual["id"]}',result=result)
    with st.expander('Visual options and DAX'):
        st.code('\n'.join(f'{m} = {model.measures.get(m,"")}' for m in measures),language='sql')
        st.caption('Generated SQL (filter context as correlated subqueries):');st.code(result['dax_sql'][:4000],language='sql')
        evidence(result,key=f'bi-{visual["id"]}')
        if st.button('Remove visual',key=f'bi-rm-{visual["id"]}'):remove_visual(engine,state,page,visual)


def add_visual(engine,model,state,page,available):
    with st.expander('Add a visual',expanded=not page['visuals']):
        if not state['measures']:st.info('Create a measure first on the Measures (DAX) tab.');return
        a,b=st.columns(2)
        kind=a.selectbox('Visual type',VISUALS,key='bi-add-type')
        names=[m['name'] for m in state['measures']]
        if kind=='Card':
            measures=[b.selectbox('Measure',names,key='bi-add-card')]
            compare=st.selectbox('Compare with (optional, shows % change)',[None]+names,format_func=lambda x:'No comparison' if x is None else x,key='bi-add-compare')
            rows=[]
        else:
            measures=b.multiselect('Values (measures)',names,default=names[:1],max_selections=4 if kind!='Donut' else 1,key='bi-add-measures')
            keys=list(available);default=next((k for k in keys if available[k].table==DATE_TABLE and available[k].column=='Month'),keys[0]) if kind=='Line' else keys[0]
            first=st.selectbox('Axis / rows',keys,index=keys.index(default),format_func=lambda k:available[k].label,key='bi-add-rows')
            rows=[first]
            if kind=='Matrix':
                second=st.selectbox('Second row level (optional)',[None]+keys,format_func=lambda k:'None' if k is None else available[k].label,key='bi-add-rows2')
                if second and second!=first:rows.append(second)
            compare=None
        title=st.text_input('Title (optional)',key='bi-add-title')
        top=st.slider('Show top N categories',3,50,12,key='bi-add-top') if kind in ('Bar','Column','Donut') else None
        if st.button('Add to page',type='primary',key='bi-add-visual'):
            if not measures or not measures[0]:st.warning('Choose at least one measure.');return
            visual={'id':uuid.uuid4().hex[:8],'type':kind,'measures':list(measures),'rows':rows,'title':title.strip(),'top':top}
            if compare:visual['compare']=compare
            state['pages'][st.session_state['bi-page']]['visuals'].append(visual);persist(engine,state);st.rerun()
