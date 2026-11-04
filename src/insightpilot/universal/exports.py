"""Portable current-scope exports for spreadsheet and Power BI preparation."""
import io
import json
import zipfile
import pandas as pd

from .engine import frame_of
from .metrics import load_metrics
from .report import configuration, safe_spreadsheet_frame


def selection_package(engine,metric,max_rows=100_000):
    if engine.count()>max_rows: raise ValueError(f'The selection exceeds {max_rows:,} rows. Narrow filters before exporting; no records have been silently omitted.')
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as archive:
        frames=[]
        with archive.open('selected-records.csv','w') as target:
            for page in range(max(1,(engine.count()+999)//1000)):
                frame=safe_spreadsheet_frame(frame_of(engine.records(page,1000)))
                target.write(frame.to_csv(index=False,header=page==0).encode())
                frames.append(frame)
        excel=io.BytesIO()
        with pd.ExcelWriter(excel,engine='openpyxl') as writer:
            pd.concat(frames,ignore_index=True).to_excel(writer,index=False,sheet_name='Selected records')
        archive.writestr('selected-records.xlsx',excel.getvalue())
        archive.writestr('analysis-manifest.json',json.dumps(configuration(engine,metric),default=str,indent=2))
        definitions=[m.dictionary(engine.info) for m in [metric,*load_metrics(engine.workspace,engine.table)]]
        archive.writestr('metric-dictionary.json',json.dumps(definitions,default=str,indent=2))
        archive.writestr('PowerQuery.m','''// Set Folder to the extracted package directory in Power BI Desktop.
let
    Folder = "CHANGE_ME",
    Source = Csv.Document(File.Contents(Folder & "/selected-records.csv"),[Delimiter=",",Encoding=65001,QuoteStyle=QuoteStyle.Csv]),
    Headers = Table.PromoteHeaders(Source,[PromoteAllScalars=true])
in Headers
''')
        archive.writestr('README.txt','Selected records only. _row_id traces to the retained source. Review field types, units and ratio denominators before building DAX. Recompute non-additive measures from source rows. No PBIX or Power BI Desktop validation is claimed. CSV text starting with spreadsheet formula markers is prefixed with an apostrophe. All columns initially import as text; apply reviewed types explicitly.\n')
    return buffer.getvalue()
