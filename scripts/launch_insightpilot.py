"""Reuse an installed environment, own one local server, and open it when ready.

No package installation, Git update, model pull, source import or migration.
POSIX locking works on macOS and Linux; native Finder/Ollama opening is macOS-only.
"""
import argparse
import fcntl
import hashlib
import http.client
import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import webbrowser

ROOT=Path(__file__).resolve().parents[1]
MODULES=('streamlit','duckdb','pandas','plotly','numpy','scipy','httpx','jsonschema','openpyxl','pyarrow','defusedxml','streamlit_ace','pm4py')


def dependencies():
    if sys.version_info[:2]!=(3,12):
        raise RuntimeError('Use the Python 3.12 environment for this project. Run once: uv python install 3.12; uv sync --locked --python 3.12 --extra test')
    missing=[m for m in MODULES if importlib.util.find_spec(m) is None]
    if missing:raise RuntimeError('Missing installed dependencies: '+', '.join(missing)+'. Run once in this project: uv sync --locked --extra test')
    sys.path.insert(0,str(ROOT/'src'))
    # Catch broken binary/import dependencies before reporting a healthy launch.
    from insightpilot.universal import ui  # noqa: F401


def local_get(port,path):
    connection=http.client.HTTPConnection('127.0.0.1',port,timeout=.6)
    try:
        connection.request('GET',path);response=connection.getresponse()
        return response.status,response.read(262145)
    except (OSError,http.client.HTTPException):return None,b''
    finally:connection.close()


def healthy(state):
    if not isinstance(state,dict) or state.get('project')!=str(ROOT):return False
    if type(state.get('port')) is not int or not 1024<=state['port']<=65535:return False
    prefix='insightpilot-'+hashlib.sha256(str(ROOT).encode()).hexdigest()[:12]
    if state.get('prefix')!=prefix:return False
    status,body=local_get(state['port'],'/'+prefix+'/_stcore/health')
    return status==200 and body.strip()==b'ok'


def find_port(preferred):
    for port in range(preferred,min(preferred+20,65536)):
        with socket.socket() as sock:
            try:sock.bind(('127.0.0.1',port))
            except OSError:continue
            return port
    raise RuntimeError('The requested local ports are occupied. Close the other application or use --port with a free port. No existing process was stopped.')


def open_browser(url):
    print('Open InsightPilot: '+url,flush=True)
    if sys.platform=='darwin':
        result=subprocess.run(['open',url],check=False)
        if result.returncode:print('The browser did not open automatically. Open the URL above manually.')
    else:webbrowser.open(url)


def ollama_status(*,start_installed=True):
    from insightpilot.universal.local_ai import LocalConfig,OllamaClient,LocalAIError
    endpoint=os.environ.get('INSIGHTPILOT_OLLAMA_URL','http://127.0.0.1:11434')
    model=os.environ.get('INSIGHTPILOT_OLLAMA_MODEL','qwen3.5:4b')
    try:config=LocalConfig(endpoint=endpoint,model=model).validated()
    except LocalAIError as exc:
        print('Local AI configuration: '+str(exc)+' Standard analysis remains available.');return
    client=OllamaClient(config)
    try:client.inspect_model();print('Ollama reports the selected local model available: '+model+'. No inference test has run.');return
    except LocalAIError as exc:
        if exc.code!='unavailable' or not start_installed or sys.platform!='darwin' or config.endpoint!='http://127.0.0.1:11434':
            print('Local AI: '+str(exc));return
    # Opening an existing macOS app is idempotent. Never spawn `ollama serve` or
    # a second daemon. Only configure the launch environment when it is offline.
    for key,value in [('OLLAMA_NO_CLOUD','1'),('OLLAMA_HOST','127.0.0.1:11434')]:
        if subprocess.run(['launchctl','setenv',key,value],check=False).returncode:
            print('Could not configure Ollama startup. Open it manually with cloud features disabled. Standard analysis is available.');return
    result=subprocess.run(['open','-gj','-a','Ollama'],check=False,capture_output=True)
    if result.returncode:print('Ollama could not be opened. If installed, open it from Applications. Standard analysis remains available.');return
    print('Opened the installed Ollama app with a loopback listener and cloud disabled for this launch environment. Refresh Local AI models after it starts; no model was downloaded.')


def stop_owned(process):
    if process is None or process.poll() is not None:return
    # This process group was created by us, never an existing listener.
    try:os.killpg(process.pid,signal.SIGTERM)
    except ProcessLookupError:return
    try:process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:os.killpg(process.pid,signal.SIGKILL)
        except ProcessLookupError:pass
        process.wait(timeout=5)


def launch(*,port=8501,browser=True,start_ollama=True,ready_timeout=45):
    dependencies();os.chdir(ROOT)
    directory=ROOT/'outputs/launcher';directory.mkdir(parents=True,exist_ok=True)
    # No credential or question goes in this file. It is session coordination,
    # never a workspace/catalog. The kernel releases its lock even after a crash.
    with (directory/'server.lock').open('a+') as lock:
        os.chmod(lock.name,0o600)
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            for _ in range(40):
                try:lock.seek(0);state=json.load(lock)
                except (ValueError,OSError):state={}
                if healthy(state):
                    url=f"http://127.0.0.1:{state['port']}/{state['prefix']}/"
                    print('Reusing this project\'s running InsightPilot. Its original Terminal owns the server.')
                    if browser:open_browser(url)
                    else:print(url,flush=True)
                    return 0
                time.sleep(.25)
            raise RuntimeError('This project is already starting, or its launcher is still open. Check that Terminal; no duplicate server was started.')
        process=None
        try:
            if start_ollama:ollama_status()
            selected=find_port(port);prefix='insightpilot-'+hashlib.sha256(str(ROOT).encode()).hexdigest()[:12]
            env=os.environ.copy();env['PYTHONPATH']=str(ROOT/'src')+(os.pathsep+env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
            command=[sys.executable,'-m','streamlit','run',str(ROOT/'app.py'),'--server.address=127.0.0.1',
                '--server.port='+str(selected),'--server.baseUrlPath='+prefix,'--server.headless=true','--browser.gatherUsageStats=false']
            process=subprocess.Popen(command,cwd=ROOT,env=env,start_new_session=True)
            state={'project':str(ROOT),'port':selected,'prefix':prefix,'pid':process.pid}
            lock.seek(0);lock.truncate();json.dump(state,lock);lock.flush()
            deadline=time.monotonic()+ready_timeout
            while time.monotonic()<deadline:
                if process.poll() is not None:raise RuntimeError('Streamlit exited before it was ready. Read its error above. If dependencies changed, run uv sync --locked --extra test once.')
                if healthy(state):break
                time.sleep(.2)
            else:raise RuntimeError('Streamlit did not become ready. No browser was opened; the server started by this launcher will be stopped.')
            url=f'http://127.0.0.1:{selected}/{prefix}/'
            print('Ready. Press Control-C in this Terminal to stop InsightPilot. Saved workspaces and datasets stay on disk.',flush=True)
            if browser:open_browser(url)
            else:print(url,flush=True)
            return process.wait()
        except KeyboardInterrupt:
            print('\nStopping this launcher\'s InsightPilot server; saved data is retained.',flush=True);return 0
        finally:
            stop_owned(process);lock.seek(0);lock.truncate();lock.flush()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8501)
    parser.add_argument('--no-browser',action='store_true')
    parser.add_argument('--no-ollama-start',action='store_true')
    parser.add_argument('--check-local-ai',action='store_true')
    parser.add_argument('--diagnostics',action='store_true',help='Only with --check-local-ai; synthetic acceptance diagnostics, never dashboard logging.')
    parser.add_argument('--runs',type=int,default=1)
    parser.add_argument('--bank-data',type=Path)
    args=parser.parse_args()
    if not 1024<=args.port<=65535:parser.error('Choose a port between 1024 and 65535.')
    if args.diagnostics and not args.check_local_ai:parser.error('--diagnostics is restricted to the synthetic acceptance check.')
    if (args.runs!=1 or args.bank_data is not None) and not args.check_local_ai:parser.error('Acceptance run/data flags require --check-local-ai.')
    for signum in (signal.SIGTERM,signal.SIGHUP):signal.signal(signum,lambda *_:(_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        if args.check_local_ai:
            dependencies()
            from accept_local_ai import main as accept
            options=['--diagnostics'] if args.diagnostics else []
            if args.runs!=1:options+=['--runs',str(args.runs)]
            if args.bank_data is not None:options+=['--bank-data',str(args.bank_data)]
            result=accept(options)
            if sys.stdin.isatty():input('\nPress Return to close this acceptance report. ')
            return result
        return launch(port=args.port,browser=not args.no_browser,start_ollama=not args.no_ollama_start)
    except (RuntimeError,ImportError,OSError) as exc:
        print('InsightPilot could not start: '+str(exc),file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())
