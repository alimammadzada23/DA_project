import argparse
import json
from pathlib import Path
import duckdb
from .ingest import ingest, quality
from .agent import Analyst
from .analytics import QUESTIONS, discover


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
    args = parser.parse_args()
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
            for table in ['cases','events','case_metrics','variants','transition_performance','category_performance','sequence_signals']:
                path = str(directory/(table+'.parquet')).replace("'","''")
                con.execute(f"COPY (SELECT * FROM {table}) TO '{path}' (FORMAT PARQUET)")
                result[table] = str(directory/(table+'.parquet'))
        print(json.dumps(result,default=str,indent=2))
    finally:
        con.close()


if __name__=='__main__': main()
