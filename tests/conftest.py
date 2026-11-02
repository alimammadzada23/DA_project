from datetime import datetime, timezone, timedelta
import xml.etree.ElementTree as ET
import pytest
from insightpilot.ingest import ingest


# Explicitly synthetic: dates, identifiers, category labels and users are invented.
TRACES = [
 ('A','PO1','3-way synthetic',[('Create',0,'human'),('Record Invoice Receipt',1,'batch'),('Record Goods Receipt',3,'human'),('Record Goods Receipt',3,None)]),
 ('B','PO1','2-way synthetic',[('Create',0,'human'),('Record Invoice Receipt',2,'batch')]),
 ('C','PO2','consignment synthetic',[('Create',1,None)]),
 ('D','PO3',None,[('Create',0,'human'),('Change',1000,'human'),('Change',2,'human')]),
]


def write_xes(path, traces=TRACES):
    root = ET.Element('log',xmlns='http://www.xes-standard.org/')
    for case,document,category,events in traces:
        trace = ET.SubElement(root,'trace')
        for k,v in [('concept:name',case),('Purchasing Document',document),('Item','1'),('Item Category',category)]:
            if v is not None: ET.SubElement(trace,'string',key=k,value=v)
        for activity,day,user in events:
            event = ET.SubElement(trace,'event')
            ET.SubElement(event,'string',key='concept:name',value=activity)
            if day is not None:
                date = datetime(2018,1,1,tzinfo=timezone.utc)+timedelta(days=day)
                ET.SubElement(event,'date',key='time:timestamp',value=date.isoformat())
            if user is not None: ET.SubElement(event,'string',key='org:resource',value=user)
    ET.ElementTree(root).write(path,encoding='utf-8',xml_declaration=True)


@pytest.fixture
def database(tmp_path):
    source = tmp_path/'synthetic.xes'; write_xes(source)
    target = tmp_path/'fixture.duckdb'
    ingest(source,target,synthetic=True,batch_size=2)
    return target
