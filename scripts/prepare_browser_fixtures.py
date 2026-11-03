"""Optional browser QA fixtures. Never called by the normal application."""
from pathlib import Path
import shutil
import pandas as pd
from insightpilot.complaints import ingest_complaints
from insightpilot.datasets import ingest_table
from insightpilot.demo import create_portfolio_demo

root = Path(__file__).resolve().parents[1]
directory = root / 'data/browser-fixtures'; directory.mkdir(parents=True, exist_ok=True)
for name in ('complaints', 'sales'):
    source = directory / ('SYNTHETIC-' + name + '.csv')
    shutil.copyfile(root / 'tests/fixtures' / source.name, source)
    target = directory / (name + '.duckdb')
    if not target.exists():
        if name == 'complaints': ingest_complaints(source, target, synthetic=True)
        else: ingest_table(source, target, {'date': 'date', 'product': 'product', 'customer': 'customer', 'measure': 'amount'}, title='SYNTHETIC reference sales', synthetic=True)
uploads = directory / 'uploads'; uploads.mkdir(exist_ok=True)
frame = pd.read_csv(directory / 'SYNTHETIC-sales.csv', dtype=str, keep_default_na=False)
frame.to_csv(uploads / 'SYNTHETIC-sales.csv', index=False)
frame.to_excel(uploads / 'SYNTHETIC-sales.xlsx', index=False)
frame.to_parquet(uploads / 'SYNTHETIC-sales.parquet', index=False)
(uploads / 'SYNTHETIC-events.csv').write_text('case_id,activity,timestamp\nA,Open,2025-01-01T00:00:00Z\nA,Close,2025-01-03T00:00:00Z\nB,Open,2025-01-02T00:00:00Z\n')
demo = root / 'data/portfolio-demo-v2'
if not (demo / 'SYNTHETIC-portfolio-v2.duckdb').exists(): create_portfolio_demo(demo)
print('Prepared SYNTHETIC browser fixtures only; no genuine CFPB or BPI records.')
print('INSIGHTPILOT_DATABASE=data/portfolio-demo-v2/SYNTHETIC-portfolio-v2.duckdb INSIGHTPILOT_CFPB_DATABASE=data/browser-fixtures/complaints.duckdb INSIGHTPILOT_CUSTOM_DATABASE=data/browser-fixtures/sales.duckdb uv run --frozen streamlit run app.py')
