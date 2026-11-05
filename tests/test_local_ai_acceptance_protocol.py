"""Protocol/real-data ingestion tests are synthetic HTTP mocks, never real Qwen."""
import importlib.util
import io
import json
from pathlib import Path
import zipfile

import pandas as pd
import pytest

from insightpilot.universal.local_ai import LocalConfig
from test_local_ai_reliability import accept, client_for, counted

spec=importlib.util.spec_from_file_location('tested_acceptance_protocol',Path(__file__).parents[1]/'scripts/local_ai_acceptance_protocol.py')
protocol=importlib.util.module_from_spec(spec);spec.loader.exec_module(protocol)


def reconstructed_plans():
    result=[]
    for case in accept.questions():
        name=case['id']
        if name in ('clarify','refuse','guard_refuse'):continue
        value=counted(limit=10)
        if name=='count':value.update(group_by=[],chart='auto',limit=None)
        elif name=='top5':value['limit']=5
        elif name in ('top3','chart','resort'):
            value={'version':1,'action':'analyze','table':'facts','operation':'aggregate','inherit':True}|({'limit':3} if name=='top3' else {'chart':'bar'} if name=='chart' else {'limit':10,'descending':True})
        elif name in ('average','median','compound'):
            value['measures']=[{'aggregation':'median' if name=='median' else 'avg','column':'age'}]
            if name=='median':value.update(operation='group_distribution',group_by=['education'])
            if name=='compound':value['measures'].append({'aggregation':'records'})
        elif name=='filter':value['filters']=[{'column':'age','operator':'gte','value':30}]
        elif name=='rows':value.update(operation='records',group_by=[],measures=[],chart='table',limit=None,filters=[{'column':'age','operator':'gt','value':40}])
        elif name=='missing':value.update(group_by=[],chart='table',filters=[{'column':'age','operator':'not_missing'}])
        elif name=='smallest':value.pop('limit');value.pop('descending')
        result.append(value)
    return result


@pytest.fixture
def synthetic_bank_csv(tmp_path):
    # Published dimensions/vocabulary only: this is NOT the UCI data itself.
    jobs=sorted(protocol.BANK_JOBS)
    frame=pd.DataFrame({c:['unknown']*45211 for c in protocol.BANK_COLUMNS})
    frame['job']=[jobs[i%len(jobs)] for i in range(len(frame))]
    path=tmp_path/'SYNTHETIC-bank-shaped.csv';frame.to_csv(path,sep=';',index=False)
    return path


def test_three_fresh_pipeline_runs_with_mocked_model_and_bank_shape(tmp_path,synthetic_bank_csv):
    calls=[]
    def factory(index):
        client,chats=client_for(reconstructed_plans()+[counted()]);calls.append(chats);return client
    code,report,path=protocol.run_series(LocalConfig(),acceptance_run=accept.run,checker=accept.check,question=accept.ORIGINAL,
        runs=3,bank_data=synthetic_bank_csv,directory=tmp_path/'series',diagnostics=True,client_factory=factory)
    assert code==0 and report['all_requested_checks_passed'] and len(report['runs'])==3
    assert all(r['synthetic_passed']==16 and r['bank_passed'] for r in report['runs'])
    assert [len(c) for c in calls]==[14,14,14]
    assert report['actual_model_test'] is False and report['full_real_acceptance_passed'] is False
    assert report['three_consecutive_real_synthetic_runs_passed'] is False
    for index in range(1,4):
        assert (path.parent/f'pass-{index}/synthetic/rejected-plans.jsonl').exists()
        bank_report=path.parent/f'pass-{index}/public-bank/report.json'
        bank=json.loads(bank_report.read_text())
        assert bank['source_rows']==45211 and bank['validated_plan']['limit']==5 and len(bank['actual']['rows'])==5
        assert not bank['actual_model_quality_measured'] and not bank['diagnostics_enabled']
        assert not list(bank_report.parent.glob('*.jsonl'))
        assert list(pd.read_csv(bank_report.parent/'public-bank-job-column.csv').columns)==['job']


def test_one_failed_pass_is_retained_and_cannot_be_overwritten_by_later_passes(tmp_path):
    outcomes=iter([False,True,True])
    def stub(config,**options):
        passed=next(outcomes);directory=options['directory'];directory.mkdir(parents=True)
        result={'status':'completed','cases_run':16,'passed':16 if passed else 15,'actual_model_quality_measured':False}
        path=directory/'report.json';path.write_text(json.dumps(result))
        return (0 if passed else 1),result,path
    code,result,path=protocol.run_series(LocalConfig(),acceptance_run=stub,checker=accept.check,question=accept.ORIGINAL,
        directory=tmp_path/'failed-series',client_factory=lambda _:object())
    assert code==1 and result['status']=='failed' and [r['passed'] for r in result['runs']]==[False,True,True]
    assert not result['full_real_acceptance_passed'] and not result['all_requested_checks_passed']
    assert json.loads(path.read_text())['runs'][0]['synthetic_passed']==15


def test_missing_real_service_records_zero_cases_and_no_success(tmp_path):
    def unavailable(config,**options):
        directory=options['directory'];directory.mkdir(parents=True);path=directory/'report.json'
        result={'status':'model_unavailable','cases_run':0,'actual_model_quality_measured':False};path.write_text(json.dumps(result))
        return 2,result,path
    code,result,_=protocol.run_series(LocalConfig(),acceptance_run=unavailable,checker=accept.check,question=accept.ORIGINAL,directory=tmp_path/'unavailable')
    assert code==2 and len(result['runs'])==1 and result['runs'][0]['synthetic_cases_run']==0
    assert not result['three_consecutive_real_synthetic_runs_passed'] and not result['full_real_acceptance_passed']


def test_nested_bank_archives_are_read_without_extracting_paths(tmp_path,synthetic_bank_csv):
    nested=io.BytesIO()
    with zipfile.ZipFile(nested,'w',compression=zipfile.ZIP_DEFLATED) as archive:archive.writestr('../../bank-full.csv',synthetic_bank_csv.read_bytes())
    path=tmp_path/'public-download.zip'
    with zipfile.ZipFile(path,'w') as archive:archive.writestr('bank.zip',nested.getvalue())
    frame,digest=protocol.read_bank(path)
    assert len(frame)==45211 and list(frame.columns)==['job'] and len(digest)==64
    assert not (tmp_path.parent/'bank-full.csv').exists()


def test_other_or_private_dataset_shapes_are_rejected_before_any_model_request(tmp_path):
    source=tmp_path/'unrelated.csv';source.write_text('job,private\nnot-public,never-log\n')
    def forbidden(*args,**kwargs):pytest.fail('Invalid public-dataset input reached a model')
    code,report,_=protocol.run_series(LocalConfig(),acceptance_run=forbidden,checker=accept.check,question=accept.ORIGINAL,
        bank_data=source,directory=tmp_path/'invalid',client_factory=forbidden)
    assert code==2 and report['status']=='invalid_public_dataset' and not report['runs']
    assert 'never-log' not in json.dumps(report)
