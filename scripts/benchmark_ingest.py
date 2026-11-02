"""Explicit synthetic scale check; does not benchmark the genuine BPI log or a Mac."""
import argparse
import json
import platform
import resource
import tempfile
import time
from pathlib import Path
import duckdb
from insightpilot.ingest import ingest

parser=argparse.ArgumentParser()
parser.add_argument('--cases',type=int,default=10000)
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args()
if args.cases<1:parser.error('--cases must be positive')
with tempfile.TemporaryDirectory(prefix='insightpilot-scale-') as temp:
    source=Path(temp)/'SYNTHETIC-scale.xes';target=Path(temp)/'scale.duckdb'
    with source.open('w') as stream:
        stream.write('<log>')
        for case in range(args.cases):
            stream.write(f'<trace><string key="concept:name" value="SYNTHETIC-{case}"/><string key="Purchasing Document" value="SYNTHETIC-PO{case//10}"/>')
            for event in range(10):
                stream.write(f'<event><string key="concept:name" value="SYNTHETIC-activity{event%3}"/><date key="time:timestamp" value="2018-01-01T{event:02d}:00:00+00:00"/></event>')
            stream.write('</trace>')
        stream.write('</log>')
    start=time.perf_counter();ingest(source,target,synthetic=True);elapsed=time.perf_counter()-start
    with duckdb.connect(str(target),read_only=True) as con:
        cases=con.execute('SELECT count(*) FROM cases').fetchone()[0]
        events=con.execute('SELECT count(*) FROM events').fetchone()[0]
    assert cases==args.cases and events==args.cases*10
    result={'scope':'SYNTHETIC SCALE CHECK ONLY; not genuine-data or Mac performance',
            'platform':platform.system()+' '+platform.machine(),'python':platform.python_version(),
            'duckdb':duckdb.__version__,'cases':cases,'events':events,'source_bytes':source.stat().st_size,
            'ingest_seconds':round(elapsed,3),'peak_process_rss_mib':round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/(1024 if platform.system()=='Linux' else 1024**2),1),
            'settings':'batch_size=10000; DuckDB threads=2 and memory_limit=1GB; RSS includes Python/pandas and native libraries'}
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
