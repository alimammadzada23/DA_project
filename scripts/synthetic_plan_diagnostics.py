"""Opt-in, redacted plan capture for ONE known synthetic acceptance fixture.

No environment switch or app UI enables this. Do not use it for user datasets.
Unknown strings/keys, free text, invalid JSON and source rows are never logged.
"""
import hashlib
import json
import math
import os
from pathlib import Path

from insightpilot.universal.analyst_plan import ANALYSIS_PROPERTIES,MEASURE_SCHEMAS

FIXTURE_SHA256='2da6da19465c3ab26e46f9d5f45baf21115e3d2f95b76520a10d5600a25a726f'
KEYS=set(ANALYSIS_PROPERTIES)|{'aggregation','column','metric','quantile','numerator','denominator','kind','operator','value','message'}
WORDS={'facts','job','age','education','absent_field','Records','analyze','clarify','refuse','selected','nonmissing','condition',
    'records','count','distinct','sum','avg','median','min','max','quantile','saved','share','rate','aggregate','schema','missing','summary',
    'distribution','group_distribution','scatter','trend','compare_periods','outliers','eq','ne','gt','gte','lt','lte','is_missing','not_missing','from','through',
    'auto','table','bar','histogram','line','box','pie','group','period','m0','m1','m2','m3','day','week','month','quarter','year'}
WORDS.update('Category '+chr(65+i) for i in range(12));WORDS.update('Level '+str(i) for i in range(1,4))
CASE_IDS={'count','groups','top5','top3','chart','resort','average','median','compound','filter','rows','missing','smallest','clarify','refuse','guard_refuse'}


class SyntheticPlanDiagnostics:
    def __init__(self,engine,path,case_ids):
        self.engine=engine;self.path=Path(path);self.case_ids=set(case_ids);self.case=None;self.attempt=0
        if self.case_ids-CASE_IDS or self.path.name!='rejected-plans.jsonl' or self.path.resolve().parent!=engine.workspace.directory.parent.parent.resolve():
            raise ValueError('Diagnostics must use a built-in case and its isolated acceptance directory.')
        self.verify_engine(engine)
        # Refuse overwrite; every acceptance run has a new directory.
        fd=os.open(self.path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);os.close(fd)

    def verify_engine(self,engine):
        info=engine.workspace.info(engine.table)
        retained=engine.workspace.directory/info['source']['retained']
        if engine.table!='facts' or info['source']['origin']!='synthetic' or info['rows']!=70 or info['columns']!=['job','age','education'] or len(info['versions'])!=1 or info['current']!=info['versions'][0]['table'] or engine.info['current']!=info['current'] or info['source']['sha256']!=FIXTURE_SHA256 or retained.stat().st_size!=1694 or hashlib.sha256(retained.read_bytes()).hexdigest()!=FIXTURE_SHA256:
            raise ValueError('Diagnostics only accept the byte-verified built-in synthetic fixture. No diagnostic data was captured.')

    def begin_case(self,case):
        if case not in self.case_ids:raise ValueError('Unknown synthetic diagnostic case.')
        self.case=case;self.attempt=0

    def begin_attempt(self,attempt):
        if type(attempt) is not int or attempt not in (1,2):raise ValueError('Diagnostic attempt must be 1 or 2.')
        self.attempt=attempt

    def _write(self,event,**payload):
        self.verify_engine(self.engine)
        with self.path.open('a',encoding='utf-8') as stream:
            stream.write(json.dumps({'case':self.case,'attempt':self.attempt,'event':event,**payload},ensure_ascii=True,allow_nan=False)+'\n')

    def _project(self,value,key='',depth=0):
        if depth>12:return '<depth omitted>'
        if isinstance(value,dict):
            return {(k if k in KEYS else '<unknown key '+str(i)+'>'):self._project(v,k,depth+1) if k in KEYS else '<redacted>' for i,(k,v) in enumerate(list(value.items())[:60])}
        if isinstance(value,list):return [self._project(v,key,depth+1) for v in value[:100]]
        if value is None or isinstance(value,bool):return value
        if type(value) in (int,float):return value if math.isfinite(value) and -100<=value<=100 else '<number redacted>'
        if isinstance(value,str):return value if value in WORDS else '<text redacted>'
        return '<type omitted>'

    def capture_response(self,content):
        if not isinstance(content,str) or len(content.encode())>16_384:
            self._write('model_response',capture='oversized response omitted');return
        try:raw=json.loads(content)
        except (ValueError,RecursionError):self._write('model_response',capture='invalid JSON omitted');return
        signatures=[]
        if isinstance(raw,dict) and isinstance(raw.get('measures'),list):
            for measure in raw['measures'][:4]:
                if not isinstance(measure,dict):continue
                agg=measure.get('aggregation');schema=MEASURE_SCHEMAS.get(agg) if isinstance(agg,str) else None
                permitted=set(schema['required']) if schema else set()
                signatures.append({'aggregation':agg if schema else '<unknown>',
                    'keys_present':sorted(k for k in measure if k in KEYS),
                    'unexpected_known_keys':sorted(k for k in measure if k in KEYS and k not in permitted)})
        self._write('model_response',plan_projection=self._project(raw),measure_signatures=signatures,
                    policy='Known synthetic tokens only; no raw response, free text, prompt, rows, paths or credentials.')

    def project_plan(self,value):
        self.verify_engine(self.engine)
        return self._project(value)

    def decision(self,stage,*,reason=None):
        stages={'validated','rejected','rejected_schema','result_ok','result_clarify','result_unsupported','result_error','result_approval_required'}
        reasons={'measure_arguments','missing_operation','missing_scope','request_constraints','invalid_schema','invalid_plan','invalid_output','timeout','unavailable','request_failed'}
        self._write(stage if stage in stages else 'decision',reason=reason if reason in reasons else None)
