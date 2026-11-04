"""Trusted finite plan execution using the existing Engine/Metric/SQLLab layers."""
from dataclasses import asdict
from datetime import date
import json

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from ..datasets import quote
from . import advanced, visuals
from .analyst_plan import PlanError, ClarificationRequired, ValidatedPlan, validate_plan
from .engine import Engine,Filter,frame_of
from .metrics import Metric,load_metrics
from .profile import numeric_expression,date_expression,profile as full_profile
from .sql_lab import SQLLab
from .story import format_value
from .report import configuration


def profile_for_engine(engine):
    with engine.workspace.connect() as con:return full_profile(con,engine.info['current'])


def question_filters(plan):
    result=[]
    for f in plan.get('filters',[]):
        op=f['operator'];value=f.get('value')
        if op in ('eq','ne') and type(value) in (int,float):op += '_numeric'
        result.append(Filter(f['column'],op,value))
    return result


def conditional_sql(engine,condition):
    name=condition['column'];col=engine.column(name);op=condition['operator'];value=condition.get('value')
    if op in ('is_missing','not_missing'):
        predicate=f"({col} IS NULL OR trim(CAST({col} AS VARCHAR))='')"
        return ('NOT '+predicate if op=='not_missing' else predicate),[]
    if op in ('from','through'):return f'{date_expression(name)} '+('>=' if op=='from' else '<=')+' ?::DATE',[date.fromisoformat(value)]
    if value is None:return col+(' IS NULL' if op=='eq' else ' IS NOT NULL'),[]
    if type(value) in (int,float):left=numeric_expression(name)
    else:left=f'CAST({col} AS VARCHAR)';value=str(value).lower() if type(value) is bool else value
    return left+' '+{'eq':'=','ne':'<>','gt':'>','gte':'>=','lt':'<','lte':'<='}[op]+' ?',[value]


def measure_expression(engine,spec,selected,*,total):
    agg=spec['aggregation'];column=spec.get('column');params=[];parts=[]
    units=engine.info.get('semantics',{}).get('units',{})
    if agg=='saved':
        definitions={m.name:m for m in load_metrics(engine.workspace,engine.table)}|{selected.name:selected,'Records':Metric('Records')}
        metric=definitions[spec['metric']]
    elif agg=='share':
        return f'count(*)::DOUBLE/NULLIF({int(total)},0)',[],{'label':'Share of selected records','units':'proportion (0–1)',
            'definition':'Group records / all selected records before top-N or follow-up cohort restriction; missing categories are included.',
            'denominator':total,'numerator':'group source rows','missing_policy':'Missing group values form an explicit category.'},[]
    elif agg=='rate':
        num,np_=conditional_sql(engine,spec['numerator'])
        denominator=spec['denominator'];kind=denominator['kind']
        if kind=='selected':d='count(*)';dp=[];eligible='true';missing='All selected source rows, including missing outcomes.'
        elif kind=='nonmissing':
            d=f"count(NULLIF(trim(CAST({engine.column(denominator['column'])} AS VARCHAR)),''))";dp=[];missing='NULL and blank denominator values excluded; other unknown codes remain eligible unless explicitly filtered.'
            eligible=f"({engine.column(denominator['column'])} IS NOT NULL AND trim(CAST({engine.column(denominator['column'])} AS VARCHAR))<>'')"
        else:
            predicate,dp=conditional_sql(engine,denominator['condition']);d='count(*) FILTER (WHERE '+predicate+')'
            eligible=predicate
            missing='Only rows matching the explicit denominator condition; unknown/inapplicable values are not guessed.'
        n='count(*) FILTER (WHERE ('+num+') AND ('+eligible+'))'
        expr=f'({n})::DOUBLE/NULLIF(({d}),0)'
        params=[*np_,*dp,*dp]
        definition={'label':'Approved conditional share','units':'proportion (0–1)','numerator':spec['numerator'],'denominator':denominator,
                    'missing_policy':missing,'definition':'Numerator = eligible rows satisfying the requested condition; divide by the eligible denominator. Zero denominator is unavailable. Sample share is not population risk.'}
        return expr,params,definition,[(n,[*np_,*dp],'numerator'),(d,dp,'denominator')]
    else:
        if agg=='records':metric=Metric('Records')
        else:
            formula='quantile('+json.dumps(column)+','+str(spec['quantile'])+')' if agg=='quantile' else agg+'('+json.dumps(column)+')'
            label={'avg':'Mean','sum':'Sum','median':'Median','min':'Minimum','max':'Maximum','distinct':'Distinct values','count':'Nonmissing values','quantile':'Quantile'}[agg]+' of '+column
            metric=Metric(label,formula,'Explicit question-specific aggregate; original row grain and reviewed units remain authoritative.',
                'records' if agg in ('distinct','count') else units.get(column) or 'units unconfirmed')
    expression,required=metric.compile(engine.info['columns'],engine.info['roles'])
    definition=metric.dictionary(engine.info)
    definition.update(label=metric.name,missing_policy='Finite numeric inputs only for numeric aggregates; NULL and blank excluded for count(field); count() includes every selected row.')
    for component,formula in metric.components.items():
        expr,_=Metric(component,formula).compile(engine.info['columns'],engine.info['roles'],allow_scalar=True)
        parts.append((expr,[],component))
    if column and agg not in ('count','distinct'):parts.append(('count('+numeric_expression(column)+')',[],'eligible'))
    return expression,params,definition,parts


def source_sql(engine,lab,data):
    source=quote(engine.table)
    if not data['relationship']:return 'SELECT * FROM '+source
    rel=lab.relationships.get(data['relationship'])
    if not rel:raise PlanError('The relationship changed or is no longer valid. Revalidate it in Data Hub.')
    columns=engine.workspace.columns(rel['right'])
    projection='f.*,'+','.join('d.'+quote(c)+' AS '+quote(rel['right']+'.'+c) for c in columns)
    return f'''SELECT {projection} FROM {source} f LEFT JOIN {quote(rel['right'])} d ON
        CAST(f.{quote(rel['left_key'])} AS VARCHAR)=CAST(d.{quote(rel['right_key'])} AS VARCHAR)'''


def cohort_clause(groups,cohort):
    if not cohort:return '',[]
    if not groups or len(cohort)>100 or any(len(row)!=len(groups) for row in cohort):raise PlanError('Invalid follow-up cohort.')
    params=[];terms=[]
    for row in cohort:
        parts=[]
        for group,value in zip(groups,row):parts.append(quote(group)+' IS NOT DISTINCT FROM ?');params.append(value)
        terms.append('('+' AND '.join(parts)+')')
    return ' WHERE ('+' OR '.join(terms)+')',params


def numeric_statistics(engine,column):
    value=numeric_expression(column)
    return SQLLab(engine).execute(f"""SELECT count(*) AS records,count(x) AS valid,avg(x) AS mean,
        stddev_samp(x) AS sd,min(x) AS minimum,quantile_cont(x,.25) AS q1,median(x) AS median,
        quantile_cont(x,.75) AS q3,max(x) AS maximum,skewness(x) AS sample_skewness
        FROM (SELECT {value} AS x FROM {quote(engine.table)}) AS values_in_scope""")


def execute_plan(engine,profile,validated,selected_metric,*,approved=False,cohort=()):
    # A caller cannot bypass validation by constructing or mutating a dataclass.
    validated=validate_plan(engine,profile,validated.dictionary() if isinstance(validated,ValidatedPlan) else validated,selected_metric)
    data=validated.data
    if data['action']!='analyze':return {'status':'clarify' if data['action']=='clarify' else 'unsupported','answer':data['message'],'plan':data,'evidence':[],'figures':[],'definitions':[]}
    if validated.approval_required and not approved:
        return {'status':'approval_required','answer':'Confirm the proposed numerator and eligible denominator before calculating this new proportion. Shared filters will remain active.','plan':data,'evidence':[],'figures':[],'definitions':[]}
    scoped=Engine(engine.workspace,engine.table,[*engine.filters,*question_filters(data)],version=engine.info['current'],context=engine.context)
    scoped.info['roles']=engine.info['roles'];scoped.info['semantics']=engine.info.get('semantics',{});scoped.info['grain']=engine.info['grain']
    lab=SQLLab(scoped);source=source_sql(scoped,lab,data);op=data['operation'];definitions=[];evidence=[];extra=[]
    groups=data['group_by'];fields=data['fields'];total=scoped.count()
    where,cohort_params=cohort_clause(groups,cohort)
    if op in ('aggregate','trend'):
        grouping=[quote(c)+' AS '+quote('group_'+str(i)) for i,c in enumerate(groups)] if op=='aggregate' else [f"date_trunc('{data['grain']}',{date_expression(data['date_basis'])})::DATE AS period"]
        clauses=list(grouping);params=[]
        for index,spec in enumerate(data['measures']):
            expr,p,definition,parts=measure_expression(scoped,spec,selected_metric,total=total)
            clauses.append(expr+' AS '+quote('m'+str(index)));params.extend(p);definitions.append(definition)
            for subexpr,subparams,label in parts:clauses.append(subexpr+' AS '+quote(label+'_'+str(index)));params.extend(subparams)
        clauses.append('count(*) AS records')
        if op=='trend':where=' WHERE '+date_expression(data['date_basis'])+' IS NOT NULL';cohort_params=[]
        group_sql=' GROUP BY '+','.join(str(i+1) for i in range(len(grouping))) if grouping else ''
        sort='period ASC' if op=='trend' else ('group_0' if data['sort_by']=='group' and groups else data['sort_by'] if data['sort_by'] not in ('group','period') else 'records')+(' DESC' if data['descending'] else ' ASC')+' NULLS LAST'
        if op!='trend' and groups:sort+=','+','.join(quote('group_'+str(i))+' ASC NULLS LAST' for i in range(len(groups)))
        # All text here is generated from validated choices/quoted identifiers;
        # data values remain bound parameters and still pass the SQL Lab AST scope.
        limit=1000 if op=='trend' else data['limit']
        query='SELECT '+','.join(clauses)+' FROM ('+source+') AS scoped'+where+group_sql+' ORDER BY '+sort+f' LIMIT {limit+1}'
        primary=lab.execute(query,size=limit,parameters=[*params,*cohort_params])
        if op=='trend':primary['method']['time_grain']=data['grain']
        if primary['truncated']:extra.append('More groups/periods exist than are displayed. Top-N output is not the complete population; no Other group is invented.')
        if any(m['aggregation']=='share' for m in data['measures']):extra.append('Shares use the entire selected row denominator before top-N; displayed shares are not renormalized to 100%.')
    elif op in ('schema','missing'):
        columns=fields or scoped.info['columns'];parts=[]
        for i,column in enumerate(columns):parts.append(f"count(*) FILTER (WHERE {quote(column)} IS NULL OR trim(CAST({quote(column)} AS VARCHAR))='') AS {quote('missing_'+str(i))}")
        calculation=lab.execute('SELECT count(*) AS records,'+','.join(parts)+' FROM '+quote(scoped.table))
        evidence.append(calculation);counts=calculation['rows'][0];catalog={c['column']:c for c in profile['columns']}
        rows=[]
        for i,column in enumerate(columns):
            missing=counts[i+1]
            if op=='missing' and missing==0:continue
            rows.append((column,catalog[column]['inferred_type'],scoped.info['roles'].get(column,'unassigned'),missing,counts[0]))
        primary=calculation|{'columns':['field','type_hypothesis','reviewed_role','missing_rows','selected_rows'],'rows':rows,'truncated':False}
        extra.append('Type hypotheses describe the full immutable version; missing counts respect the active filters. NULL/blank are missing; literal NA-like markers require explicit preparation.')
    elif op=='summary':
        primary=numeric_statistics(scoped,fields[0]);definitions=[{'label':'Summary of '+fields[0],'units':scoped.info.get('semantics',{}).get('units',{}).get(fields[0]) or 'units unconfirmed'}]
    elif op=='distribution':
        primary=scoped.distribution(fields[0],data['bins']);definitions=[{'label':'Distribution of '+fields[0],'units':scoped.info.get('semantics',{}).get('units',{}).get(fields[0]) or 'units unconfirmed'}]
        evidence.append(numeric_statistics(scoped,fields[0]));extra.append('Equal-width finite-value bins; missing/nonfinite values excluded. Exact maximum can occupy its own boundary bin.')
    elif op=='group_distribution':
        value=numeric_expression(fields[0]);group=quote(groups[0])
        primary=lab.execute(f'''SELECT {group} AS group_0,count({value}) AS valid,min({value}) AS minimum,
            quantile_cont({value},.25) AS q1,median({value}) AS median,quantile_cont({value},.75) AS q3,max({value}) AS maximum,
            count(*) AS records FROM {quote(scoped.table)} GROUP BY 1 ORDER BY records DESC,group_0 ASC NULLS LAST LIMIT {data['limit']+1}''',size=data['limit'])
        definitions=[{'label':fields[0]+' by '+groups[0],'units':scoped.info.get('semantics',{}).get('units',{}).get(fields[0]) or 'units unconfirmed'}]
        extra.append('Full-data quartiles; whiskers show observed min/max, not Tukey fences. This is descriptive, not a significance test.')
    elif op=='scatter':
        primary=scoped.scatter(*fields);extra.append('Scatter displays the first 2,000 selected records in stable source order, not a representative random sample.')
    elif op=='outliers':
        result=advanced.outliers(scoped,fields[0]);primary=result['evidence']
        # Existing method returns actual flagged records and full-scope summary.
        if 'data' in result:primary=primary|{'columns':list(result['data'].columns),'rows':list(result['data'].itertuples(index=False,name=None))}
        extra.append(result.get('limitations','IQR screening is descriptive; extreme values are not automatically errors.'))
    else:
        spec=data['measures'][0];expr,p,definition,_=measure_expression(scoped,spec,selected_metric,total=total)
        if p or spec['aggregation'] in ('share','rate') or spec['aggregation']=='saved' and not next((m for m in [selected_metric,*load_metrics(scoped.workspace,scoped.table)] if m.name==spec.get('metric')),Metric('Records')).additive:
            raise ClarificationRequired('Period contribution analysis requires an additive count/sum metric; averages and rates cannot be decomposed as additive totals.')
        if spec['aggregation']=='saved':metric=next((m for m in [selected_metric,Metric('Records'),*load_metrics(scoped.workspace,scoped.table)] if m.name==spec['metric']))
        elif spec['aggregation']=='records':metric=Metric('Records')
        else:metric=Metric(definition['label'],spec['aggregation']+'('+json.dumps(spec['column'])+')',units=definition['units'])
        if not metric.additive:raise ClarificationRequired('Use an additive count/sum for period contributions.')
        primary=scoped.compare_periods(groups[0],metric,data['date_basis'],data['periods'][:2],data['periods'][2:]);definitions=[definition]
    # Metadata-derived tables retain the underlying executed-query evidence too.
    evidence=[primary,*evidence]
    primary['method']={'plan':data,'measure_definitions':definitions,'execution':primary.get('method'),
        'selected_sidebar_metric':asdict(selected_metric),'question_specific_filters':data['filters'],
        'followup_cohort_rows':len(cohort),'full_selected_rows_before_cohort':total}
    answer=summarize(primary,data,definitions,total)
    if op=='distribution' and len(evidence)>1:
        stats=dict(zip(evidence[1]['columns'],evidence[1]['rows'][0]))
        answer+=' Sample skewness: '+format_value(stats['sample_skewness'])+' (finite observations; undefined for insufficient/constant data). '
    if cohort and 'records' in primary['columns']:
        contributing=sum(r[primary['columns'].index('records')] for r in primary['rows'])
        answer+=f' Follow-up restricted to {len(cohort)} previously displayed groups; {contributing:,} rows contribute to these groups.'
    return {'status':'ok','answer':answer,'plan':data,'definitions':definitions,
        'primary':primary,'evidence':evidence,'figures':figures(primary,data,definitions,scoped),
        'limitations':list(dict.fromkeys(['Observed records and model-proposed intent do not establish causal effects or population representativeness.',*extra])),
        'selected_rows':total,'question_filters':data['filters'],'cohort':[list(r[:len(groups)]) for r in primary['rows']] if op=='aggregate' and groups else [],
        'grouping':groups,'engine_manifest':configuration(engine,selected_metric)}


def summarize(result,plan,definitions,total):
    rows=result['rows'];cols=result['columns'];op=plan['operation']
    if not rows:return f'No eligible results for {total:,} selected rows. Review the current filters and missing inputs.'
    if op=='aggregate':
        first=dict(zip(cols,rows[0]));items=[]
        if plan['group_by']:
            group=' / '.join('Missing' if first.get('group_'+str(i)) is None else str(first['group_'+str(i)]) for i in range(len(plan['group_by'])))
            items.append(group+' ranks first in the requested ordering')
        for i,definition in enumerate(definitions):items.append(definition['label']+': '+format_value(first.get('m'+str(i)))+' '+definition['units'])
        return '; '.join(items)+f'. Calculated from {total:,} selected rows'+(f'; {len(rows):,} groups displayed.' if plan['group_by'] else '.')
    if op=='missing':return f'{len(rows):,} fields contain NULL or blank values within {total:,} selected rows.'
    if op=='schema':return f'{len(rows):,} available fields; metadata and missing counts for {total:,} selected rows.'
    if op=='summary':
        values=dict(zip(cols,rows[0]));return f'{plan["fields"][0]}: mean {format_value(values["mean"])}, median {format_value(values["median"])}, range {format_value(values["minimum"])} to {format_value(values["maximum"])}; {values["valid"]:,} finite of {total:,} selected rows. Sample skewness {format_value(values.get("sample_skewness"))}; descriptive, not a significance test.'
    if op=='trend':return f'{definitions[0]["label"]} across {len(rows):,} observed {plan["grain"]} periods; date basis {plan["date_basis"]}. Edge periods may be incomplete; gaps are not zero.'
    if op=='distribution':return f'{sum(r[cols.index("records")] for r in rows):,} finite observations contribute to the distribution of {plan["fields"][0]}; {total:,} selected source rows.'
    return f'{op.replace("_"," ").capitalize()}: {len(rows):,} calculated rows from {total:,} selected source rows. Inspect the table and evidence; differences are descriptive.'


def figures(result,plan,definitions,engine):
    if plan['chart']=='table' or not result['rows']:return []
    op=plan['operation'];data=frame_of(result);fig=[]
    if op=='aggregate' and plan['group_by']:
        category=data['group_0'].map(lambda v:'Missing' if pd.isna(v) else str(v))
        if len(plan['group_by'])==2:category=category+' / '+data['group_1'].map(lambda v:'Missing' if pd.isna(v) else str(v))
        for i,definition in enumerate(definitions):
            # One axis per unit/measure; no misleading dual-scale overlay.
            item={'columns':['category','value','records'],'rows':list(zip(category,data['m'+str(i)],data['records']))}
            if plan['chart']=='pie':
                total_displayed=int(data.records.sum())
                expected=result['method']['full_selected_rows_before_cohort']
                if len(data)>6 or total_displayed!=expected:raise ClarificationRequired('A pie chart would omit categories. Use a bar chart or request a complete composition with at most six groups.')
                figure=visuals.style(px.pie(names=category,values=data['m'+str(i)],color_discrete_sequence=visuals.COLORS),definition['label']+' · complete selected composition')
            else:figure=visuals.ranked(item,definition['label']+' by '+' / '.join(plan['group_by']),definition['units'])
            fig.append(figure)
    elif op=='distribution':fig=[visuals.histogram(result,definitions[0]['label'])]
    elif op=='trend':
        if len(data)<2:return []
        item=result|{'columns':['period','value','records'],'rows':[(r[data.columns.get_loc('period')],r[data.columns.get_loc('m0')],r[data.columns.get_loc('records')]) for r in result['rows']], 'method':{'time_grain':plan['grain']}}
        fig=[visuals.trend(item,definitions[0]['label']+' by '+plan['grain']+' · '+plan['date_basis'],definitions[0]['units'])]
    elif op=='scatter':
        units=engine.info.get('semantics',{}).get('units',{});x,y=plan['fields']
        fig=[visuals.style(px.scatter(data,x='x',y='y',hover_data=['_row_id'],opacity=.6,color_discrete_sequence=[visuals.TEAL]),y+' against '+x,x+' · '+(units.get(x) or 'units unconfirmed'),y+' · '+(units.get(y) or 'units unconfirmed'))]
    elif op=='group_distribution':
        box=go.Figure(go.Box(q1=data.q1,median=data['median'],q3=data.q3,lowerfence=data.minimum,upperfence=data.maximum,x=data.group_0.map(str),name=plan['fields'][0],marker_color=visuals.TEAL))
        fig=[visuals.style(box,definitions[0]['label'],plan['group_by'][0],definitions[0]['units'])]
    elif op=='compare_periods':fig=[visuals.contribution(result,'Observed segment contributions',definitions[0]['units'])]
    return [f.to_json() for f in fig]
