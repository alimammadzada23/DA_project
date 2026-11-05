"""Fresh-session local-model verification; public bank data is explicitly supplied.

No downloads, model changes, real-workspace selection or raw-response logging.
Synthetic diagnostics remain confined to the original byte-verified fixture.
"""
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import uuid
import zipfile

import duckdb
import pandas as pd

from insightpilot.universal import Workspace
from insightpilot.universal.analyst_session import Conversation, analyze_question
from insightpilot.universal.context import AnalysisContext
from insightpilot.universal.engine import Engine
from insightpilot.universal.local_ai import OllamaClient
from insightpilot.universal.metrics import Metric

BANK_URL = 'https://archive.ics.uci.edu/static/public/222/bank+marketing.zip'
BANK_COLUMNS = ['age','job','marital','education','default','balance','housing','loan','contact','day','month','duration','campaign','pdays','previous','poutcome','y']
BANK_JOBS = {'admin.','blue-collar','entrepreneur','housemaid','management','retired','self-employed','services','student','technician','unemployed','unknown'}
MAX_ARCHIVE_BYTES = 20 * 1024 * 1024
MAX_CSV_BYTES = 10 * 1024 * 1024


def read_bank(path):
    """Read the classic public bank-full.csv, including nested official ZIPs."""
    path=Path(path)
    if not path.is_file() or path.stat().st_size>MAX_ARCHIVE_BYTES:raise ValueError('Supply the official public UCI Bank Marketing archive or bank-full.csv, at most 20 MiB.')
    raw=path.read_bytes()
    def member(blob,depth=0):
        if depth>2:raise ValueError('Bank archive nesting exceeds its bound.')
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            entries=archive.infolist()
            if len(entries)>50:raise ValueError('Unexpected public-bank archive contents.')
            for info in entries:
                if Path(info.filename).name=='bank-full.csv':
                    if info.file_size>MAX_CSV_BYTES:raise ValueError('Bank CSV exceeds its size bound.')
                    return archive.read(info)
            for info in entries:
                if Path(info.filename).name=='bank.zip':
                    if info.file_size>MAX_ARCHIVE_BYTES:raise ValueError('Nested bank archive exceeds its size bound.')
                    return member(archive.read(info),depth+1)
        raise ValueError('bank-full.csv was not found in the public archive. Use the classic 45,211-record dataset.')
    if zipfile.is_zipfile(io.BytesIO(raw)):raw=member(raw)
    if len(raw)>MAX_CSV_BYTES:raise ValueError('Bank CSV exceeds its size bound.')
    frame=pd.read_csv(io.BytesIO(raw),sep=';',dtype=str,keep_default_na=False,encoding='utf-8-sig')
    if list(frame.columns)!=BANK_COLUMNS or len(frame)!=45211 or set(frame.job)!=BANK_JOBS:
        raise ValueError('The file does not match classic UCI bank-full.csv: 45,211 records, 17 expected columns and 12 published job categories are required.')
    # The acceptance calculation needs only this public categorical column.
    # Other customer attributes never enter the acceptance workspace or prompt.
    return frame[['job']].copy(),hashlib.sha256(raw).hexdigest()


def write_report(path,value):
    path.write_text(json.dumps(value,default=str,indent=2,allow_nan=False)+'\n');path.chmod(0o600)


def bank_acceptance(config,jobs,source_hash,directory,*,checker,question,client=None):
    directory.mkdir(parents=True,mode=0o700)
    source=directory/'public-bank-job-column.csv';jobs.to_csv(source,index=False)
    workspace=Workspace.create(directory/'workspace')
    workspace.add_file(source,'facts',provenance={'origin':'caller_supplied_public_uci_bank_marketing','citation':BANK_URL,'original_sha256':source_hash,'retained_columns':['job']})
    workspace.approve_semantics('facts',{'job':'category'},'One public UCI Bank Marketing record; no unique-client or causal claim.')
    engine=Engine(workspace,'facts',context=AnalysisContext(None,'month','job'));metric=Metric('Records')
    with duckdb.connect() as connection:
        connection.register('original_public_jobs',jobs)
        expected=connection.execute('SELECT job,count(*) AS records FROM original_public_jobs GROUP BY job ORDER BY records DESC,job ASC NULLS LAST LIMIT 5').df()
    # Never attach synthetic diagnostics to this public, nonsynthetic dataset.
    planner=OllamaClient(config) if client is None else client
    old_observer=getattr(planner,'observer',None)
    if hasattr(planner,'observer'):planner.observer=None
    try:response=analyze_question(engine,workspace.profile('facts'),metric,question,Conversation(),mode='local',config=config,client=planner)
    finally:
        if hasattr(planner,'observer'):planner.observer=old_observer
    case={'group':'job','measures':[('records',None,'records')],'limit':5,'ordered':True,'chart':'bar'}
    passed,reason=checker(case,response,expected)
    # Failed model free text, raw plans, source rows and local paths are omitted.
    # Successful tables contain only the 5 public job labels and executed counts.
    backend=response.get('backend',{})
    report={'data':'Caller-supplied public UCI Bank Marketing, classic bank-full.csv; only job retained.',
            'source':BANK_URL,'original_csv_sha256':source_hash,'source_verification':'Published dimensions/vocabulary checked; official download/TLS is the caller provenance. No independent remote-byte verification is claimed.',
            'source_rows':len(jobs),'question':question,'passed':passed,'status':response['status'],
            'reason':reason if response['status']=='ok' else 'No executed result; inspect the failure interactively without recording model free text.',
            'actual_model_test':client is None,'actual_model_quality_measured':client is None and backend.get('model_response_received',False),
            'model_attempts':backend.get('model_attempts',0),'repair_attempted':backend.get('repair_attempted',False),
            'application_constraint_repairs':backend.get('constraint_repairs',[]),
            'validated_plan':response.get('plan') if response['status']=='ok' else None,
            'actual':None if response['status']!='ok' else {k:response['primary'][k] for k in ('columns','rows','truncated')},
            'independent_duckdb_reference':expected.to_dict('split'),'diagnostics_enabled':False}
    write_report(directory/'report.json',report)
    return report


def run_series(config,*,acceptance_run,checker,question,runs=3,bank_data=None,directory=None,diagnostics=False,client_factory=None):
    if type(runs) is not int or not 1<=runs<=5:raise ValueError('Choose 1–5 complete acceptance runs; the reliability target requires at least three.')
    root=Path(__file__).resolve().parents[1]
    directory=directory or root/'outputs/local-ai-reliability'/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:6])
    directory.mkdir(parents=True,mode=0o700)
    report={'model':config.model,'actual_model_test':client_factory is None,'requested_runs':runs,'runs':[],
            'session_policy':'Fresh fixture, workspace and conversations for each complete run; same deterministic model configuration. No statistical independence or accuracy estimate is implied.',
            'bank_requested':bank_data is not None,'all_requested_checks_passed':False,
            'three_consecutive_real_synthetic_runs_passed':False,'full_real_acceptance_passed':False,'status':'running'}
    try:bank=read_bank(bank_data) if bank_data is not None else None
    except (OSError,ValueError,zipfile.BadZipFile,UnicodeError,RuntimeError):
        report.update(status='invalid_public_dataset',reason='Public-dataset validation failed. Supply the official classic UCI Bank Marketing archive or bank-full.csv; no source values or local paths are recorded.');write_report(directory/'summary.json',report);return 2,report,directory/'summary.json'
    for index in range(runs):
        print(f'Acceptance pass {index+1}/{runs}: fresh session; '+('REAL installed model' if client_factory is None else 'EXPLICIT MOCK, not real-model evidence'),flush=True)
        planner=client_factory(index) if client_factory is not None else None
        code,single,path=acceptance_run(config,client=planner,directory=directory/f'pass-{index+1}'/'synthetic',diagnostics=diagnostics)
        item={'pass':index+1,'synthetic_status':single['status'],'synthetic_passed':single.get('passed',0),
              'contract':single.get('contract'),
              'synthetic_cases_run':single.get('cases_run',0),'synthetic_actual_model_quality_measured':single.get('actual_model_quality_measured',False),
              'synthetic_report':str(path.relative_to(directory)),'bank_passed':None,'bank_actual_model_quality_measured':False,'passed':False}
        if single['status']=='model_unavailable':
            report['runs'].append(item);report['status']='model_unavailable';write_report(directory/'summary.json',report);return 2,report,directory/'summary.json'
        if bank is not None:
            tested=bank_acceptance(config,*bank,directory/f'pass-{index+1}'/'public-bank',checker=checker,question=question,client=planner)
            item.update(bank_passed=tested['passed'],bank_actual_model_quality_measured=tested['actual_model_quality_measured'])
        item['passed']=code==0 and single.get('cases_run')==16 and single.get('passed')==16 and (bank is None or item['bank_passed'])
        report['runs'].append(item);write_report(directory/'summary.json',report)
    same_contract=all(r['contract']==report['runs'][0]['contract'] for r in report['runs'])
    all_ok=all(r['passed'] for r in report['runs']) and same_contract
    real=client_factory is None and all(r['synthetic_actual_model_quality_measured'] for r in report['runs'])
    streak=all_ok and real and runs>=3
    report.update(status='completed' if all_ok else 'failed',all_requested_checks_passed=all_ok,unchanged_contract_across_passes=same_contract,
                  three_consecutive_real_synthetic_runs_passed=streak,
                  full_real_acceptance_passed=streak and bank is not None and all(r['bank_actual_model_quality_measured'] and r['bank_passed'] for r in report['runs']))
    write_report(directory/'summary.json',report)
    print('Acceptance summary: '+str(directory/'summary.json'),flush=True)
    print('Real three-run synthetic target: '+str(report['three_consecutive_real_synthetic_runs_passed'])+'; real public-bank acceptance: '+str(report['full_real_acceptance_passed']),flush=True)
    return (0 if all_ok else 1),report,directory/'summary.json'
