"""Deterministic PBIP candidate generation. Run explicitly; never invoked by the LLM."""
import json
from pathlib import Path

ROOT = Path(__file__).parent
BASE = 'https://developer.microsoft.com/json-schemas/fabric/'
CASE_MEASURES = {
    'Items':'COALESCE(COUNTROWS(Cases), 0)',
    'Purchase Documents':'COALESCE(COUNTROWS(FILTER(VALUES(Cases[purchase_document]), NOT ISBLANK(Cases[purchase_document]))), 0)',
    'Median Observed Days':'MEDIAN(Cases[observed_span_days])',
    'P90 Observed Days':'PERCENTILEX.INC(FILTER(Cases, NOT ISBLANK(Cases[observed_span_days])), Cases[observed_span_days], 0.9)',
    'Repeat Cases':'COALESCE(COUNTROWS(FILTER(Cases, Cases[repeated_event_count] > 0)), 0)',
    'Repeat Rate':'DIVIDE([Repeat Cases], [Items])',
}
WAIT_MEASURES = {
    'Transition Occurrences':'COALESCE(COUNTROWS(TransitionEvents), 0)',
    'Median Wait Hours':'MEDIAN(TransitionEvents[waiting_hours])',
    'P90 Wait Hours':'PERCENTILEX.INC(TransitionEvents, TransitionEvents[waiting_hours], 0.9)',
}


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
    write(report/'definition/version.json',{'$schema':schema('item/report/definition/versionMetadata/1.0.0'),'version':'2.0.0'})
    pages = [
      ('Executive','Executive Overview', [('Cases',['Items','Purchase Documents','Median Observed Days','P90 Observed Days','Repeat Cases','Repeat Rate'],True),('Cases',['matching_category','Items','Median Observed Days','Repeat Rate'],False)]),
      ('Performance','Process Performance',[('Cases',['variant','Items','Median Observed Days','P90 Observed Days'],False),('TransitionEvents',['from_activity','to_activity','Transition Occurrences','Median Wait Hours','P90 Wait Hours'],False)]),
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
                is_measure=measure or (table=='Cases' and field in CASE_MEASURES) or (table=='TransitionEvents' and field in WAIT_MEASURES)
                expression = {'Measure' if is_measure else 'Column':{'Expression':{'SourceRef':{'Entity':table}},'Property':field}}
                projections.append({'field':expression,'queryRef':table+'.'+field})
            write(page/f'visuals/visual{i}/visual.json',{
              '$schema':schema('item/report/definition/visualContainer/2.4.0'), 'name':name+str(i),
              'position':{'x':32,'y':40+i*320,'z':i,'height':280,'width':1216,'tabOrder':i},
              'visual':{'visualType':'tableEx','query':{'queryState':{'Values':{'projections':projections}}}}})
    def projection(property, measure=False):
        return {'field':{'Measure' if measure else 'Column':{'Expression':{'SourceRef':{'Entity':'Cases'}},'Property':property}},'queryRef':'Cases.'+property}
    executive=report/'definition/pages/Executive'
    for name,role,type_,fields,position in [
        ('trend','Category','lineChart',[projection('Start Month'),projection('Items',True)],{'x':32,'y':450,'z':2,'height':240,'width':1216,'tabOrder':2}),
        ('categoryFilter','Values','slicer',[projection('matching_category')],{'x':850,'y':210,'z':3,'height':220,'width':398,'tabOrder':3})]:
        query={'Category':{'projections':[fields[0]]},'Y':{'projections':[fields[1]]}} if type_=='lineChart' else {role:{'projections':fields}}
        write(executive/f'visuals/{name}/visual.json',{'$schema':schema('item/report/definition/visualContainer/2.4.0'),'name':name,'position':position,'visual':{'visualType':type_,'query':{'queryState':query}}})
    for name,position in [('visual0',{'x':32,'y':40,'z':0,'height':140,'width':1216,'tabOrder':0}),('visual1',{'x':32,'y':210,'z':1,'height':220,'width':780,'tabOrder':1})]:
        path=executive/f'visuals/{name}/visual.json';obj=json.loads(path.read_text());obj['position']=position;write(path,obj)
    performance=report/'definition/pages/Performance'
    for name,position in [('visual0',{'x':32,'y':30,'z':0,'height':210,'width':1216,'tabOrder':0}),('visual1',{'x':32,'y':260,'z':1,'height':210,'width':780,'tabOrder':1})]:
        path=performance/f'visuals/{name}/visual.json';obj=json.loads(path.read_text());obj['position']=position;write(path,obj)
    wait_fields=[]
    for property,kind in [('Waiting Band','Column'),('Transition Occurrences','Measure')]:
        wait_fields.append({'field':{kind:{'Expression':{'SourceRef':{'Entity':'TransitionEvents'}},'Property':property}},'queryRef':'TransitionEvents.'+property})
    write(performance/'visuals/waitDistribution/visual.json',{
        '$schema':schema('item/report/definition/visualContainer/2.4.0'),'name':'waitDistribution',
        'position':{'x':32,'y':490,'z':2,'height':200,'width':780,'tabOrder':2},
        'visual':{'visualType':'clusteredColumnChart','query':{'queryState':{'Category':{'projections':[wait_fields[0]]},'Y':{'projections':[wait_fields[1]]}}}}})
    for page in ['Performance','Exceptions']:
        y=260 if page=='Performance' else 350
        write(report/f'definition/pages/{page}/visuals/categoryFilter/visual.json',{
            '$schema':schema('item/report/definition/visualContainer/2.4.0'),'name':'categoryFilter',
            'position':{'x':850,'y':y,'z':3,'height':210,'width':398,'tabOrder':3},
            'visual':{'visualType':'slicer','query':{'queryState':{'Values':{'projections':[projection('matching_category')]}}}}})
        if page=='Exceptions':
            path=report/'definition/pages/Exceptions/visuals/visual1/visual.json'
            obj=json.loads(path.read_text());obj['position']['width']=780;write(path,obj)
    write(model/'definition/database.tmdl','database\n\tcompatibilityLevel: 1601\n')
    write(model/'definition/model.tmdl','model Model\n\tculture: en-US\n\tdefaultPowerBIDataSourceVersion: powerBI_V3\n\nref table Cases\nref table Transitions\nref table TransitionEvents\nref table Signals\nref table Opportunities\nref relationship TransitionCase\nref relationship SignalCase\n')
    write(model/'definition/relationships.tmdl','relationship TransitionCase\n\tfromColumn: TransitionEvents.case_id\n\ttoColumn: Cases.case_id\n\tcrossFilteringBehavior: oneDirection\n\nrelationship SignalCase\n\tfromColumn: Signals.case_id\n\ttoColumn: Cases.case_id\n\tcrossFilteringBehavior: oneDirection\n')
    write(model/'definition/expressions.tmdl','expression DataFolder = "C:\\InsightPilot\\exports" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]\n')
    tables = {
      'Cases':('case_metrics',{'case_id':'string','purchase_document':'string','matching_category':'string','first_event':'dateTime','variant':'string','observed_span_days':'double','repeated_event_count':'int64','missing_timestamps':'int64','missing_activities':'int64','completion_status':'string'}),
      'Transitions':('transition_performance',{'from_activity':'string','to_activity':'string','occurrences':'int64','median_wait_hours':'double','p90_wait_hours':'double'}),
      'TransitionEvents':('transitions',{'case_id':'string','from_activity':'string','to_activity':'string','waiting_hours':'double'}),
      'Signals':('sequence_signals',{'case_id':'string','matching_category':'string','classification':'string','reason':'string'}),
    }
    measures = CASE_MEASURES
    for name,(source,columns) in tables.items():
        content='table '+name+'\n'
        if name=='Cases':
            for measure,expression in measures.items(): content+=f"\n\tmeasure '{measure}' = {expression}\n"
        if name=='TransitionEvents':
            for measure,expression in WAIT_MEASURES.items(): content+=f"\n\tmeasure '{measure}' = {expression}\n"
        for column,type_ in columns.items():
            content+=f'\n\tcolumn {column}\n\t\tdataType: {type_}\n\t\tsummarizeBy: none\n\t\tsourceColumn: {column}\n'
        if name=='Cases':
            content+="\n\tcolumn 'Start Month' = IF(ISBLANK(Cases[first_event]), BLANK(), DATE(YEAR(Cases[first_event]), MONTH(Cases[first_event]), 1))\n\t\tdataType: dateTime\n\t\tformatString: yyyy-MM\n\t\tsummarizeBy: none\n"
        if name=='TransitionEvents':
            content+="\n\tcolumn 'Waiting Band' = SWITCH(TRUE(), TransitionEvents[waiting_hours] < 1, \"<1 hour\", TransitionEvents[waiting_hours] < 24, \"1-24 hours\", TransitionEvents[waiting_hours] < 168, \"1-7 days\", \"7+ days\")\n\t\tdataType: string\n\t\tsortByColumn: 'Waiting Band Sort'\n\t\tsummarizeBy: none\n\n\tcolumn 'Waiting Band Sort' = SWITCH(TRUE(), TransitionEvents[waiting_hours] < 1, 1, TransitionEvents[waiting_hours] < 24, 2, TransitionEvents[waiting_hours] < 168, 3, 4)\n\t\tdataType: int64\n\t\tsummarizeBy: none\n"
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
    write(ROOT/'measures.json',measures|WAIT_MEASURES)


if __name__=='__main__': generate()
