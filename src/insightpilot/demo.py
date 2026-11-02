"""Small, explicitly synthetic demonstration; never a substitute for the official log."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree.ElementTree import Element,SubElement,ElementTree
from .ingest import ingest
import random
import json


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


def create_portfolio_demo(directory, cases=1200, seed=2019):
    """Invented, reproducible patterns for UI exploration, never real business findings."""
    if not 1<=cases<=20000:raise ValueError('Portfolio demo supports 1 to 20000 items')
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    source=directory/'SYNTHETIC-portfolio-v2.xes';database=directory/'SYNTHETIC-portfolio-v2.duckdb'
    if source.exists() or database.exists():raise ValueError('Demo files already exist; choose a new directory')
    rng=random.Random(seed)
    root=Element('log',xmlns='http://www.xes-standard.org/')
    categories=['3-way / GR-based','3-way / invoice first','2-way matching','Consignment']
    for i in range(cases):
        kind=i%4
        # A purchasing document has one company/vendor; its items can have different matching flows.
        document_rng=random.Random(seed+1000003*(i//3+1))
        company=document_rng.randrange(4);vendor=document_rng.randrange(24)
        trace=SubElement(root,'trace')
        values={'concept:name':f'SYN-{i:05d}','Purchasing Document':f'SYN-PO{i//3:05d}','Item':str(i%3+1),
                'Item Category':'SYNTHETIC · '+categories[kind], 'Vendor':f'SYN-Vendor {vendor+1:02d}',
                'Company':f'SYN-Company {company+1}', 'Spend classification text':['Services','Materials','Maintenance','Logistics'][rng.randrange(4)],
                'GR-Based Inv. Verif.':str(kind==0).lower(),'Goods Receipt':str(kind!=2).lower(),
                'Item Type':'Consignment' if kind==3 else 'Standard','fixture:status':'SYNTHETIC','fixture:seed':str(seed)}
        if (i//3)%97==0:values.pop('Vendor')
        if i%157==0:values.pop('Item Category')
        for key,value in values.items():SubElement(trace,'string',key=key,value=value)
        activities=['Create Purchase Order Item']
        if rng.random()<.23:activities+=['Change Quantity']*(2 if rng.random()<.2 else 1)
        receipts=['Record Goods Receipt']*(2 if rng.random()<.24 else 1)
        if kind==0:activities+=receipts+['Record Invoice Receipt','Clear Invoice']
        elif kind==1:activities+=['Record Invoice Receipt']+receipts+['Remove Payment Block','Clear Invoice']
        elif kind==2:activities+=['Record Invoice Receipt','Clear Invoice']
        else:activities+=receipts
        cursor=datetime(2018,1,1,tzinfo=timezone.utc)+timedelta(days=rng.randrange(240),hours=rng.randrange(24))
        for j,activity in enumerate(activities):
            if j:
                days=rng.lognormvariate(.1+kind*.25,.85)*(1.7 if company==2 else 1)
                if rng.random()<.035:days*=8
                cursor+=timedelta(hours=round(days*24,2))
            event=SubElement(trace,'event')
            SubElement(event,'string',key='concept:name',value=activity)
            SubElement(event,'date',key='time:timestamp',value=cursor.isoformat())
            SubElement(event,'string',key='org:resource',value='NONE' if (i+j)%89==0 else f'SYN-Actor {(i+j)%12+1:02d}')
    ElementTree(root).write(source,encoding='utf-8',xml_declaration=True)
    ingest(source,database,synthetic=True)
    manifest={'scope':'SYNTHETIC PORTFOLIO DEMO ONLY','seed':seed,'items':cases,
              'patterns':'All timing, flow, company/vendor and repetition patterns are invented for interface demonstration; no BPI findings or savings.',
              'source':str(source),'database':str(database)}
    (directory/'demo-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return manifest
