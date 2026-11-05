"""Shared visual system. Styling never changes data or widget behavior."""
SHELL = '''<style>
:root {--ip-bg:#080f1b;--ip-panel:#0f1b2b;--ip-border:#243746;--ip-muted:#98acbd;--ip-ink:#e6eff7;--ip-accent:#67e0cd;--ip-radius:14px;}
[data-testid="stAppViewContainer"]{background:radial-gradient(ellipse at 78% 0%,#102b35 0,transparent 43%),var(--ip-bg);color:var(--ip-ink);}
[data-testid="stHeader"]{background:transparent;}
[data-testid="stSidebar"]{background:#0b1422;border-right:1px solid var(--ip-border);min-width:250px!important;max-width:270px!important;}
[data-testid="stSidebar"] [data-testid="stVerticalBlock"]{gap:.8rem;}
.block-container{max-width:1530px;padding:1.5rem 2.5rem 4rem;}
h1{font-size:2.45rem!important;font-weight:650!important;letter-spacing:-.055em;line-height:1.14!important;}
h2{font-size:1.45rem!important;font-weight:600!important;letter-spacing:-.035em;}
h3{font-size:1.1rem!important;letter-spacing:-.02em;}
p{line-height:1.6;}[data-testid="stCaptionContainer"]{color:var(--ip-muted);}
.brand{display:flex;align-items:center;gap:11px;margin:3px 0 6px;}.brand-mark{display:grid;place-items:center;width:38px;height:38px;border:1px solid #45877f;background:#143c40;color:#8bf2dc;font-size:22px;border-radius:12px;}.brand-name{font-size:23px;font-weight:650;letter-spacing:-1px;color:#e6eff7;}
.eyebrow{color:#7cddcc;font-size:10px;letter-spacing:.2em;font-weight:650;margin:0 0 14px;text-transform:uppercase;}.ip-tag{display:inline-flex;align-items:center;gap:7px;border:1px solid #31544f;border-radius:20px;background:#14302f;color:#a3edda;padding:4px 10px;font-size:11px;}.ip-tag:before{content:'';width:5px;height:5px;border-radius:50%;background:#6ee0bf;}
.ip-hero{padding:22px 8px 8px 0;}.ip-hero h1{margin:14px 0 12px!important;padding:0!important;}.ip-hero .objective{max-width:550px;color:#a8bac8;font-size:15px;line-height:1.7;}.ip-path{font-size:11px;color:#7e94a9;letter-spacing:.04em;margin-bottom:20px;}.ip-subtle{font-size:12px;color:#8da4b8;}.ip-statline{display:flex;gap:32px;border-top:1px solid var(--ip-border);margin-top:22px;padding-top:18px;}.ip-statline strong{display:block;color:#e5f1f7;font-size:23px;letter-spacing:-.03em;}.ip-statline span{font-size:11px;color:#91a9bb;}
[data-testid="stMetric"]{background:linear-gradient(120deg,#122433,#0e1929);border:1px solid var(--ip-border);border-radius:var(--ip-radius);padding:16px 20px;min-height:100px;}
[data-testid="stMetricLabel"]{color:#9fb3c4;font-size:12px;}[data-testid="stMetricValue"]{font-size:1.8rem!important;letter-spacing:-.04em;}
[data-testid="stVerticalBlockBorderWrapper"]>div,[data-testid="stVerticalBlock"][style*="border"]{border-color:var(--ip-border)!important;border-radius:var(--ip-radius)!important;}
[data-testid="stButton"] button,[data-testid="stDownloadButton"] button{min-height:42px;border:1px solid #314556;border-radius:9px;font-weight:550;transition:border-color .15s,background .15s;}
[data-testid="stButton"] button:hover{border-color:#68d7c6;background:#163135;color:#bdf7e9;}
button[kind="primary"],button[kind="primary"] p{color:#092822!important;}
button:focus-visible,[role="radio"]:focus-visible,input:focus-visible{outline:2px solid #8cf0db!important;outline-offset:3px;}
[data-testid="stPlotlyChart"]{background:var(--ip-panel);border:1px solid var(--ip-border);border-radius:var(--ip-radius);padding:10px;}
[data-testid="stDataFrame"]{border:1px solid var(--ip-border);border-radius:10px;}
[data-testid="stFileUploader"]{border:1px dashed #426a76;border-radius:var(--ip-radius);padding:14px;background:#0e202d;}
[data-testid="stChatMessage"]{border:1px solid var(--ip-border);border-radius:var(--ip-radius);}
[data-testid="stExpander"] details{border-color:var(--ip-border);border-radius:10px;}
[data-baseweb="tab-list"]{gap:24px;border-bottom:1px solid var(--ip-border);}[data-baseweb="tab"]{font-size:13px;}
.st-key-active-scope{background:#0d202c;border-radius:12px;}.st-key-active-scope p{margin-bottom:.15rem;}
.st-key-workflow-nav [role="radiogroup"]{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:6px;width:100%;}.st-key-workflow-nav [role="radiogroup"]>label{border:1px solid var(--ip-border);border-radius:8px;padding:7px;margin:0;min-height:40px;}.st-key-workflow-nav [role="radiogroup"]>label:has(input:checked){border-color:#61d5c5;background:#123836;}.st-key-workflow-nav p{font-size:12px!important;}
.st-key-command-header{border-bottom:1px solid var(--ip-border);padding-bottom:12px;margin-bottom:12px;}
.ip-card-number{font-size:10px;letter-spacing:.15em;color:#71919d;margin:0 0 12px;}.ip-card-title{font-size:17px;font-weight:600;margin:0 0 8px;color:#dcebf3;}.ip-card-copy{color:#92aabc;font-size:12px;min-height:40px;line-height:1.6;}.ip-empty{padding:26px;border:1px dashed #36525d;border-radius:14px;background:#0c1c29;}.ip-empty h3{color:#dcebf3;margin:0 0 8px;}.ip-empty p{font-size:13px;color:#94aabb;}
[data-testid="stAppDeployButton"]{display:none;}
@media(min-width:901px){.st-key-hub-mobile{display:none;}}
@media(max-width:1100px){.block-container{padding-left:1.4rem;padding-right:1.4rem;}h1{font-size:2rem!important;}.ip-statline{gap:20px;}}
@media(max-width:900px){[data-testid="stMain"] [data-testid="stHorizontalBlock"]{flex-wrap:wrap;gap:1rem;}[data-testid="stMain"] [data-testid="stColumn"]{min-width:min(100%,260px)!important;flex:1 1 260px!important;}.st-key-command-wheel{display:none;}.st-key-command-header [data-testid="stHorizontalBlock"]{flex-direction:column;}.st-key-command-header [data-testid="stColumn"]{width:100%!important;flex:1 1 100%!important;}.st-key-command-header [data-testid="stColumn"]:has(.st-key-command-wheel){display:none;}.block-container{padding:1rem 1rem 3rem;}.ip-hero{padding-top:4px;}.st-key-workflow-nav [role="radiogroup"]{grid-template-columns:repeat(2,minmax(0,1fr));}}
@media(prefers-reduced-motion:reduce){*{transition:none!important;}}
</style>'''
