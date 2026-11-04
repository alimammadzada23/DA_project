"""Reproducible synthetic scale benchmark. Each size runs in a fresh process."""
import argparse
import json
from pathlib import Path
import platform
import resource
import subprocess
import sys
import tempfile
import time

import duckdb

from insightpilot.universal import Workspace
from insightpilot.universal.engine import Engine
from insightpilot.universal.metrics import Metric


def measure(rows):
    with tempfile.TemporaryDirectory(prefix='insightpilot-benchmark-') as directory:
        root=Path(directory); source=root/'SYNTHETIC-scale.csv'
        with duckdb.connect(config={'threads':'2'}) as con:
            con.sql('''SELECT i AS transaction_id,'SYNTHETIC-'||(i%25) AS category,
                'SYNTHETIC-C'||(i%1000) AS customer_id,
                (DATE '2020-01-01'+(i%1825)::INTEGER)::VARCHAR AS date,
                (i%100+1)::DOUBLE AS amount,(i%5+1) AS quantity FROM range(?) t(i)''',
                params=[rows]).write_csv(str(source),header=True)
        workspace=Workspace.create(root/'workspace')
        started=time.perf_counter(); workspace.add_file(source,'observations',provenance={'origin':'synthetic'})
        ingest=time.perf_counter()-started
        engine=Engine(workspace,'observations'); metric=Metric('Amount','sum("amount")','Synthetic reference sum','synthetic units')
        started=time.perf_counter(); report=workspace.profile('observations'); profile_time=time.perf_counter()-started
        expected=(rows//100)*5050 + sum(range(1,rows%100+1))
        latencies=[]
        for _ in range(3):
            started=time.perf_counter(); result=engine.metric(metric); latencies.append(time.perf_counter()-started)
            assert result['rows'][0][:2]==(expected,rows)
        started=time.perf_counter()
        assert engine.count()==rows
        grouped=engine.groups('category',metric,top=100); trend=engine.trend('date',metric)
        assert sum(r[1] for r in grouped['rows'])==expected
        assert sum(r[1] for r in trend['rows'])==expected
        overview_time=time.perf_counter()-started
        assert report['rows']==rows and len(report['columns'])==6
        return {'rows':rows,'source_bytes':source.stat().st_size,'import_seconds':round(ingest,4),
            'full_profile_seconds':round(profile_time,4),'metric_query_seconds':list(map(lambda v:round(v,4),latencies)),
            'overview_calculations_seconds':round(overview_time,4),
            'peak_process_rss_mib':round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/(1024 if platform.system()=='Linux' else 1024**2),1),
            'reference_sum':expected,'verified':True}


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--rows',type=int)
    parser.add_argument('--output',type=Path,default=Path('docs/next-generation-benchmarks.json'))
    args=parser.parse_args()
    if args.rows:
        print(json.dumps(measure(args.rows))); return
    results=[]
    for size in (10_000,100_000,1_000_000):
        result=subprocess.run([sys.executable,__file__,'--rows',str(size)],capture_output=True,text=True)
        if result.returncode:
            sys.stderr.write(result.stderr); raise SystemExit(result.returncode)
        results.append(json.loads(result.stdout))
    report={'scope':'Synthetic generated CSV, Linux only; not real-data throughput or native Mac performance.',
        'python':platform.python_version(),'duckdb':duckdb.__version__,'platform':platform.platform(),
        'settings':{'threads':2,'duckdb_memory_limit':'768MB','fresh_process_per_size':True},
        'method':'Fresh process, generated source and OS caches not cleared; import/profile without application cache; three new-connection aggregate queries; overview includes count/group/trend. '
                 'RSS includes generation, Python and native libraries. Overview timings are server calculations, not browser response.',
        'results':results}
    args.output.parent.mkdir(parents=True,exist_ok=True); args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__': main()
