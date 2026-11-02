"""Deterministic PBIP candidate generation. Run explicitly; never invoked by the LLM."""
import json
from pathlib import Path

ROOT = Path(__file__).parent
BASE = 'https://developer.microsoft.com/json-schemas/fabric/'


def write(path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,indent=2)+'\n' if isinstance(value,dict) else value)


def schema(path): return BASE+path+'/schema.json'


def generate():
    report = ROOT/'InsightPilot.Report'
    model = ROOT/'InsightPilot.SemanticModel'
    write(ROOT/'InsightPilot.pbip',{'$schema':schema('pbip/pbipProperties/1.0.0'),'version':'1.0','artifacts':[{'report':{'path':'InsightPilot.Report'}}]})
    write(report/'definition.pbir',{'$schema':schema('item/report/definitionProperties/2.0.0'),'version':'4.0','datasetReference':{'byPath':{'path':'../InsightPilot.SemanticModel'}}})
    write(model/'definition.pbism',{'$schema':schema('item/semanticModel/definitionProperties/1.0.0'),'version':'4.0','settings':{}})
    write(report/'definition/report.json',{'$schema':schema('item/report/definition/report/3.0.0'),'themeCollection':{}})
    pages = [
      ('Executive','Executive Overview', [('Cases',['Items','Purchase Documents','Median Observed Days','P90 Observed Days','Repeat Cases','Repeat Rate'],True),('Cases',['matching_category','observed_span_days'],False)]),
      ('Performance','Process Performance',[('Cases',['variant','observed_span_days'],False),('Transitions',['from_activity','to_activity','occurrences','median_wait_hours','p90_wait_hours'],False)]),
      ('Exceptions','Compliance and Exceptions',[('Signals',['case_id','matching_category','classification','reason'],False),('Cases',['case_id','matching_category','missing_timestamps','missing_activities','repeated_event_count'],False)]),
      ('Decisions','Management Decisions',[('Opportunities',['Hypothesis','Evidence status','Verification','Target KPI'],False)]),
    ]
    write(report/'definition/pages/pages.json',{'$schema':schema('item/report/definition/pagesMetadata/1.0.0'),'pageOrder':[p[0] for p in pages],'activePageName':'Executive'})
    for name,title,visuals in pages:
        page = report/'definition/pages'/name
        write(page/'page.json',{'$schema':schema('item/report/definition/page/2.0.0'),'name':name,'displayName':title,'displayOption':'FitToPage','height':720,'width':1280})
        for i,(table,fields,measure) in enumerate(visuals):
            projections=[]
            for field in fields:
                expression = {'Measure' if measure else 'Column':{'Expression':{'SourceRef':{'Entity':table}},'Property':field}}
                projections.append({'field':expression,'queryRef':table+'.'+field})
            write(page/f'visuals/visual{i}/visual.json',{
              '$schema':schema('item/report/definition/visualContainer/2.4.0'), 'name':name+str(i),
              'position':{'x':32,'y':40+i*320,'z':i,'height':280,'width':1216,'tabOrder':i},
              'visual':{'visualType':'tableEx','query':{'queryState':{'Values':{'projections':projections}}}}})
    write(model/'definition/database.tmdl','database\n\tcompatibilityLevel: 1601\n')
    write(model/'definition/model.tmdl','model Model\n\tculture: en-US\n\tdefaultPowerBIDataSourceVersion: powerBI_V3\n\nref table Cases\nref table Transitions\nref table Signals\nref table Opportunities\n')
    write(model/'definition/expressions.tmdl','expression DataFolder = "C:\\InsightPilot\\exports" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]\n')
    tables = {
      'Cases':('case_metrics',{'case_id':'string','purchase_document':'string','matching_category':'string','variant':'string','observed_span_days':'double','repeated_event_count':'int64','missing_timestamps':'int64','missing_activities':'int64','completion_status':'string'}),
      'Transitions':('transition_performance',{'from_activity':'string','to_activity':'string','occurrences':'int64','median_wait_hours':'double','p90_wait_hours':'double'}),
      'Signals':('sequence_signals',{'case_id':'string','matching_category':'string','classification':'string','reason':'string'}),
    }
    measures = {
      'Items':'COUNTROWS(Cases)',
      'Purchase Documents':'COUNTROWS(FILTER(VALUES(Cases[purchase_document]), NOT ISBLANK(Cases[purchase_document])))',
      'Median Observed Days':'MEDIAN(Cases[observed_span_days])',
      'P90 Observed Days':'PERCENTILEX.INC(FILTER(Cases, NOT ISBLANK(Cases[observed_span_days])), Cases[observed_span_days], 0.9)',
      'Repeat Cases':'COUNTROWS(FILTER(Cases, Cases[repeated_event_count] > 0))',
      'Repeat Rate':'DIVIDE([Repeat Cases], [Items])',
    }
    for name,(source,columns) in tables.items():
        content='table '+name+'\n'
        if name=='Cases':
            for measure,expression in measures.items(): content+=f"\n\tmeasure '{measure}' = {expression}\n"
        for column,type_ in columns.items():
            content+=f'\n\tcolumn {column}\n\t\tdataType: {type_}\n\t\tsummarizeBy: none\n\t\tsourceColumn: {column}\n'
        content+=f'\n\tpartition {name} = m\n\t\tmode: import\n\t\tsource =\n\t\t\tParquet.Document(File.Contents(DataFolder & "\\{source}.parquet"))\n'
        write(model/f'definition/tables/{name}.tmdl',content)
    content='table Opportunities\n'
    for c in ['Hypothesis','Evidence status','Verification','Target KPI']:
        content+=f"\n\tcolumn '{c}'\n\t\tdataType: string\n\t\tsourceColumn: {c}\n"
    content+='''
\tpartition Opportunities = m
\t\tmode: import
\t\tsource =
\t\t\t#table(type table [Hypothesis=text, #"Evidence status"=text, Verification=text, #"Target KPI"=text], {{"Targeted exception routing", "Hypothesis only; real data unavailable", "Validate flow-specific signals and operational ownership", "Time to review an exception; not observable in this log"}})
'''
    write(model/'definition/tables/Opportunities.tmdl',content)
    write(ROOT/'measures.json',measures)


if __name__=='__main__': generate()
