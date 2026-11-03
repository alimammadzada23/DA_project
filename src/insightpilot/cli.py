import argparse
import json
import hashlib
from datetime import date
from pathlib import Path
import duckdb
import httpx
from .ingest import ingest, quality, prepare_analytics
from .agent import Analyst
from .analytics import QUESTIONS, discover
from .reporting import reconcile, analysis_report
from .demo import create_demo, create_portfolio_demo
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
    p = sub.add_parser('portfolio-demo');p.add_argument('directory');p.add_argument('--cases',type=int,default=1200);p.add_argument('--seed',type=int,default=2019)
    p = sub.add_parser('cfpb-download', help='Download a full official CSV period extract; end date is exclusive')
    p.add_argument('directory'); p.add_argument('--start', type=date.fromisoformat, required=True); p.add_argument('--end', type=date.fromisoformat, required=True)
    p = sub.add_parser('cfpb-import'); p.add_argument('source'); p.add_argument('database'); p.add_argument('--synthetic', action='store_true')
    p = sub.add_parser('cfpb-validate'); p.add_argument('source'); p.add_argument('database')
    p = sub.add_parser('cfpb-analyze'); p.add_argument('database'); p.add_argument('directory')
    p = sub.add_parser('cfpb-guide'); p.add_argument('database'); p.add_argument('question'); p.add_argument('--company', action='append', default=[])
    p = sub.add_parser('inspect'); p.add_argument('source'); p.add_argument('--sheet', default=0)
    p = sub.add_parser('import-table'); p.add_argument('source'); p.add_argument('database'); p.add_argument('--mapping', type=Path, required=True)
    p.add_argument('--profile', choices=['descriptive', 'transactions', 'complaints', 'event_log'], default='descriptive'); p.add_argument('--revenue', action='store_true')
    p.add_argument('--day-first', action='store_true'); p.add_argument('--assume-utc', action='store_true'); p.add_argument('--synthetic', action='store_true')
    args = parser.parse_args()
    if args.command.startswith('cfpb-'):
        from .complaints import download_extract, ingest_complaints, reconcile_complaints, export_complaints, Complaints
        from .datasets import TableFilters
        try:
            if args.command == 'cfpb-download': result = download_extract(args.directory, args.start, args.end)
            elif args.command == 'cfpb-import': result = ingest_complaints(args.source, args.database, synthetic=args.synthetic)
            elif args.command == 'cfpb-validate': result = reconcile_complaints(args.source, args.database)
            elif args.command == 'cfpb-analyze': result = export_complaints(args.database, args.directory)
            else: result = Complaints(args.database, TableFilters(companies=tuple(args.company))).guided_answer(args.question)
        except (ValueError, OSError, httpx.HTTPError) as exc: parser.exit(2, f'CFPB operation incomplete: {exc}\n')
        print(json.dumps(result, default=str, indent=2))
        if args.command == 'cfpb-validate' and not result['passed']: raise SystemExit(1)
        return
    if args.command in ('inspect', 'import-table'):
        from .datasets import inspect_table, ingest_table
        result = inspect_table(args.source, args.sheet) if args.command == 'inspect' else ingest_table(args.source, args.database,
            json.loads(args.mapping.read_text()), profile=args.profile, revenue=args.revenue, day_first=args.day_first,
            synthetic=args.synthetic, assume_utc=args.assume_utc)
        print(json.dumps(result, default=str, indent=2)); return
    if args.command=='portfolio-demo':
        print(json.dumps(create_portfolio_demo(args.directory,args.cases,args.seed),indent=2));return
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
