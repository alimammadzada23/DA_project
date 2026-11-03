import gzip
import json
import subprocess
import sys
import duckdb
import pytest
from insightpilot.ingest import ingest,quality
from insightpilot.agent import Analyst,propose_measure
from insightpilot.analytics import QUESTIONS,discover
from conftest import write_xes


def test_quality_and_preservation(database):
    with duckdb.connect(str(database),read_only=True) as con:
        report = quality(con)
        assert report['counts']=={'cases':4,'events':10,'purchase_documents':3,'activities':4,'users':2}
        assert report['dataset']=='synthetic fixture'
        assert report['issues']['source_order_reversals']==1
        assert report['issues']['tied_timestamp_excess']==1
        assert report['issues']['missing_resources']==2
        assert con.execute('SELECT count(*) FROM events').fetchone()[0]==10
        assert all(not r['matches'] for r in report['published_count_comparison'].values())


@pytest.mark.parametrize('key,expected',[
 ('item_volume',4),('event_volume',10),('document_volume',3),('activity_volume',4),('user_volume',2),
 ('median_span',2.5),('p90_span',700.9),('repeat_cases',2),('repeat_events',2),('variant_count',4),
 ('missing_users',2),('missing_times',0),
])
def test_independent_scalar_reference(database,key,expected):
    # Hand-derived from fixture: case spans [3,2,0,1000], 10 events, 4 variants.
    assert Analyst(database).investigate(key)['rows'][0][0]==pytest.approx(expected)


def test_remaining_reference_questions(database):
    analyst = Analyst(database)
    top = analyst.investigate('top_variants')['rows']
    assert len(top)==4 and sum(r[1] for r in top)==4
    cats = analyst.investigate('categories')['rows']
    assert {r[0]:r[2] for r in cats}=={'[unknown]':1000,'3-way synthetic':3,'2-way synthetic':2,'consignment synthetic':0}
    transitions = analyst.investigate('transitions')['rows']
    assert {(r[0],r[1]):r[3] for r in transitions}=={
      ('Create','Record Invoice Receipt'):36,('Record Invoice Receipt','Record Goods Receipt'):48,
      ('Record Goods Receipt','Record Goods Receipt'):0,('Create','Change'):48,('Change','Change'):23952}
    signals = analyst.investigate('sequence_signals')['rows']
    assert len(signals)==1 and signals[0][0]=='A' and signals[0][2]=='review_required'
    assert dict(analyst.investigate('activity_frequency')['rows'])=={'Create':4,'Record Invoice Receipt':2,'Record Goods Receipt':2,'Change':2}
    assert analyst.investigate('monthly_cases')['rows'][0][1]==4
    assert dict(analyst.investigate('resource_frequency')['rows'])=={'human':6,'batch':2,None:2}
    assert analyst.investigate('long_spans')['rows']==[('D',1000),('A',3),('B',2),('C',0)]
    assert len(QUESTIONS)==20


@pytest.mark.parametrize('sql',[
 'DELETE FROM events','CREATE TABLE bad(i INT)','SELECT 1; SELECT 2',
 "COPY events TO '/tmp/leak.csv'", "ATTACH '/tmp/a.duckdb' AS other",
 "INSTALL httpfs", "SELECT * FROM read_csv_auto('/etc/passwd')",
 "SELECT * FROM read_parquet('https://example.com/file.parquet')",
 "SELECT write_file('/tmp/leak','hello')", 'PRAGMA database_list',
])
def test_query_restrictions(database,sql):
    with pytest.raises((ValueError,duckdb.Error)):
        Analyst(database).query(sql)
    assert Analyst(database).investigate('event_volume')['rows']==[(10,)]


def test_query_cte_limit_and_evidence(database):
    a = Analyst(database)
    result = a.query('WITH x AS (SELECT * FROM events) SELECT * FROM x',limit=2)
    assert len(result['rows'])==2 and result['truncated']
    assert result['synthetic'] and len(result['source_sha256'])==64


def test_order_and_unknown_completion(database):
    with duckdb.connect(str(database),read_only=True) as c:
        assert c.execute("SELECT source_order FROM ordered_events WHERE case_id='D' ORDER BY event_rank").fetchall()==[(0,),(2,),(1,)]
        assert c.execute("SELECT source_order FROM ordered_events WHERE case_id='A' ORDER BY event_rank").fetchall()==[(0,),(1,),(2,),(3,)]
        assert c.execute('SELECT DISTINCT completion_status FROM case_metrics').fetchall()==[('unknown',)]


def test_discovery(database):
    with duckdb.connect(str(database),read_only=True) as c: result=discover(c)
    assert result['start_activities']=={'Create':4}
    assert sum(e['count'] for e in result['edges'])==6
    assert result['petri_places']>0 and result['petri_transitions']>0


@pytest.mark.parametrize('content',[
 '<notlog/>', '<log/>', '<log><trace><event/></trace></log>',
 '<log><trace><string key="concept:name" value="A"/></trace></log>',
 '<log><trace><string key="concept:name" value="A"/><event><date key="time:timestamp" value="nonsense"/></event></trace></log>',
 '<!DOCTYPE log [<!ENTITY x SYSTEM "file:///etc/passwd">]><log>&x;</log>',
])
def test_malformed_atomic(tmp_path,content):
    source=tmp_path/'bad.xes'; source.write_text(content); target=tmp_path/'bad.duckdb'
    with pytest.raises(Exception): ingest(source,target)
    assert not target.exists()
    assert source.read_text()==content


def test_gzip_and_no_overwrite(tmp_path):
    raw=tmp_path/'synthetic.xes'; write_xes(raw)
    packed=tmp_path/'synthetic.xes.gz'
    with gzip.open(packed,'wb') as f: f.write(raw.read_bytes())
    target=tmp_path/'test.duckdb'; ingest(packed,target,synthetic=True)
    with pytest.raises(ValueError): ingest(packed,target)


def test_duplicate_cases_rejected(tmp_path):
    source=tmp_path/'duplicate.xes'
    write_xes(source,[('A','PO',None,[('Create',0,None)]),('A','PO',None,[('Create',1,None)])])
    target=tmp_path/'duplicate.duckdb'
    with pytest.raises(duckdb.ConstraintException): ingest(source,target)
    assert not target.exists()


def test_duplicate_signatures_retained(tmp_path):
    source=tmp_path/'duplicate.xes'
    write_xes(source,[('A','PO',None,[('Create',0,'user'),('Create',0,'user')])])
    target=tmp_path/'duplicate.duckdb'; ingest(source,target,synthetic=True)
    with duckdb.connect(str(target),read_only=True) as c:
        report=quality(c)
        assert report['counts']['events']==2
        assert report['issues']['duplicate_signature_excess']==1


def test_offset_free_dates_rejected(tmp_path):
    source=tmp_path/'date.xes'
    source.write_text('<log><trace><string key="concept:name" value="A"/><event><date key="time:timestamp" value="2018-01-01T00:00:00"/></event></trace></log>')
    with pytest.raises(ValueError,match='timezone'): ingest(source,tmp_path/'date.duckdb')


def test_missing_times_and_activity(tmp_path):
    source=tmp_path/'missing.xes'
    source.write_text('<log><trace><string key="concept:name" value="A"/><event/></trace></log>')
    target=tmp_path/'missing.duckdb'; ingest(source,target,synthetic=True)
    with duckdb.connect(str(target),read_only=True) as c:
        report=quality(c)
        assert report['issues']['missing_activities']==1
        assert report['issues']['missing_timestamps']==1
        assert c.execute('SELECT observed_span_days FROM case_metrics').fetchone()[0] is None
        assert c.execute('SELECT repeated_event_count FROM case_metrics').fetchone()[0]==0
        with pytest.raises(ValueError): discover(c)


def test_proposal_is_nonmutating():
    p=propose_measure('Items','COUNTROWS(Cases)')
    assert p['status']=='pending human review' and len(p['proposal_id'])==64


def test_cli_export(database,tmp_path):
    result=subprocess.run([sys.executable,'-m','insightpilot.cli','export',str(database),str(tmp_path/'export')],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    paths=json.loads(result.stdout)
    with duckdb.connect() as con:
        assert con.execute('SELECT count(*) FROM read_parquet(?)',[paths['events']]).fetchone()[0]==10
