"""Small, explicitly synthetic demonstration; never a substitute for the official log."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree.ElementTree import Element,SubElement,ElementTree
from .ingest import ingest


def create_demo(directory):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    source=directory/'SYNTHETIC-demo.xes';database=directory/'SYNTHETIC-demo.duckdb'
    if source.exists() or database.exists():
        raise ValueError('Demo files already exist; choose a new directory to preserve existing files')
    root=Element('log',xmlns='http://www.xes-standard.org/')
    traces=[
        ('DEMO-1','DEMO-PO1','SYNTHETIC three-way',[('Create',0),('Record Invoice Receipt',2),('Record Goods Receipt',5)]),
        ('DEMO-2','DEMO-PO1','SYNTHETIC two-way',[('Create',0),('Record Invoice Receipt',1)]),
        ('DEMO-3','DEMO-PO2','SYNTHETIC three-way',[('Create',1),('Record Goods Receipt',3),('Record Goods Receipt',4),('Record Invoice Receipt',9)]),
        ('DEMO-4','DEMO-PO3','SYNTHETIC consignment',[('Create',0)]),
    ]
    for case,document,category,events in traces:
        trace=SubElement(root,'trace')
        for key,value in [('concept:name',case),('Purchasing Document',document),('Item','1'),('Item Category',category),('fixture:status','SYNTHETIC')]:
            SubElement(trace,'string',key=key,value=value)
        for activity,days in events:
            event=SubElement(trace,'event')
            SubElement(event,'string',key='concept:name',value=activity)
            SubElement(event,'date',key='time:timestamp',value=(datetime(2018,1,1,tzinfo=timezone.utc)+timedelta(days=days)).isoformat())
            SubElement(event,'string',key='org:resource',value='SYNTHETIC-resource')
    ElementTree(root).write(source,encoding='utf-8',xml_declaration=True)
    ingest(source,database,synthetic=True)
    return {'source':str(source),'database':str(database),'scope':'SYNTHETIC DEMONSTRATION ONLY'}
