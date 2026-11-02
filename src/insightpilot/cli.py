import argparse
import json
import hashlib
from datetime import date
from pathlib import Path
import duckdb
from .ingest import ingest, quality, prepare_analytics
from .agent import Analyst
from .analytics import QUESTIONS, discover
from .reporting import reconcile, analysis_report
from .demo import create_demo
from .dashboard import Dashboard, Filters


def main():
    parser = argparse.ArgumentParser(description='InsightPilot: evidence-led procurement analysis')
    sub = parser.add_subparsers(dest='command',required=True)
    p = sub.add_parser('ingest')
    p.add_argument('source'); p.add_argument('database'); p.add_argument('--synthetic',action='store_true')
    p.add_argument('--mapping',type=Path,help='JSON object overriding explicit case attribute mapping')
    for name in ['quality','discover','export','questions']:
        p = sub.add_parser(name); p.add_argument('database')
        if name=='export': p.add_argument('directory')
    p = sub.add_parser('ask'); p.add_argument('database'); p.add_argument('question_id',choices=list(QUESTIONS))
    p = sub.add_parser('validate-source'); p.add_argument('source'); p.add_argument('database')
    p = sub.add_parser('analyze'); p.add_argument('database'); p.add_argument('directory')
    p = sub.add_parser('chat'); p.add_argument('database'); p.add_argument('question'); p.add_argument('--audit',required=True)
    p = sub.add_parser('demo'); p.add_argument('directory')
    p = sub.add_parser('prepare'); p.add_argument('database')
    p = sub.add_parser('guide'); p.add_argument('database'); p.add_argument('question')
    p.add_argument('--category',action='append',default=[])
    p.add_argument('--start-date',type=date.fromisoformat);p.add_argument('--end-date',type=date.fromisoformat)
    args = parser.parse_args()
    if args.command=='prepare':
        if not Path(args.database).is_file():parser.error('Database does not exist; import an event log first')
        with duckdb.connect(args.database) as connection:
            connection.execute("SET TimeZone='UTC'")
            connection.execute("SET memory_limit='1GB'")
            connection.execute('SET threads=2')
            connection.execute('BEGIN TRANSACTION')
            try:
                prepared=prepare_analytics(connection)
                connection.execute('COMMIT')
            except Exception:
                connection.execute('ROLLBACK'); raise
        print(json.dumps({'prepared':prepared,'raw_data':'unchanged'},indent=2)); return
    if args.command=='guide':
        filters=Filters(categories=tuple(args.category),start_date=args.start_date,end_date=args.end_date)
        print(json.dumps(Dashboard(args.database,filters).guided_answer(args.question),default=str,indent=2)); return
    if args.command=='demo':
        print(json.dumps(create_demo(args.directory),indent=2)); return
    if args.command=='validate-source':
        result=reconcile(args.source,args.database)
        print(json.dumps(result,default=str,indent=2))
        if not result['passed']: raise SystemExit(1)
        return
    if args.command=='analyze':
        print(json.dumps(analysis_report(args.database,args.directory),indent=2)); return
    if args.command=='chat':
        try: result=Analyst(args.database).chat(args.question,audit_path=args.audit)
        except ValueError as exc: parser.exit(2,f'Live AI not run: {exc}\n')
        print(json.dumps(result,default=str,indent=2)); return
    if args.command=='ingest':
        ingest(args.source,args.database,synthetic=args.synthetic,mapping=json.loads(args.mapping.read_text()) if args.mapping else None)
    elif args.command=='ask':
        print(json.dumps(Analyst(args.database).investigate(args.question_id),default=str,indent=2)); return
    con = duckdb.connect(args.database,read_only=True)
    try:
        if args.command in ('quality','ingest'): result = quality(con)
        elif args.command=='discover': result = discover(con)
        elif args.command=='questions':
            con.close()
            result = {k:Analyst(args.database).investigate(k) for k in QUESTIONS}
        elif args.command=='export':
            directory = Path(args.directory).resolve(); directory.mkdir(parents=True,exist_ok=True)
            result = {}
            for table in ['cases','events','case_metrics','variants','transitions','transition_performance','category_performance','sequence_signals']:
                path = str(directory/(table+'.parquet')).replace("'","''")
                con.execute(f"COPY (SELECT * FROM {table}) TO '{path}' (FORMAT PARQUET)")
                result[table] = str(directory/(table+'.parquet'))
            metadata=quality(con)
            metadata['export_sha256']={}
            for name,path in result.items():
                digest=hashlib.sha256()
                with Path(path).open('rb') as stream:
                    for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
                metadata['export_sha256'][name]=digest.hexdigest()
            (directory/'manifest.json').write_text(json.dumps(metadata,default=str,indent=2)+'\n')
        print(json.dumps(result,default=str,indent=2))
    finally:
        con.close()


if __name__=='__main__': main()
