"""Portable launcher logic and shell quoting; native Finder remains Mac acceptance."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

ROOT=Path(__file__).parents[1]
spec=importlib.util.spec_from_file_location('mac_launcher_test',ROOT/'scripts/launch_insightpilot.py')
launcher=importlib.util.module_from_spec(spec);spec.loader.exec_module(launcher)


def test_shell_launchers_have_valid_syntax_and_executable_permissions():
    for name in ('Start InsightPilot.command','Check Local AI.command'):
        path=ROOT/name;assert os.access(path,os.X_OK)
        subprocess.run(['/bin/bash','-n',str(path)],check=True)


def test_moved_project_with_spaces_uses_existing_interpreter_without_uv_or_git(tmp_path):
    project=tmp_path/'Moved Project with spaces';(project/'.venv/bin').mkdir(parents=True)
    shutil.copy2(ROOT/'Start InsightPilot.command',project/'Start InsightPilot.command')
    marker=tmp_path/'invocation.txt';python=project/'.venv/bin/python'
    python.write_text('#!/bin/bash\nprintf "%s\\n" "$PWD" "$@" > "$INSIGHTPILOT_TEST_MARKER"\n');python.chmod(0o755)
    result=subprocess.run(['/bin/bash',str(project/'Start InsightPilot.command'),'--no-browser'],cwd=tmp_path,env=dict(os.environ,INSIGHTPILOT_TEST_MARKER=str(marker)),capture_output=True,text=True)
    assert result.returncode==0 and marker.read_text().splitlines()==[str(project),str(project/'scripts/launch_insightpilot.py'),'--no-browser']


def test_missing_environment_explains_one_time_setup_without_installing(tmp_path):
    command=tmp_path/'Start InsightPilot.command';shutil.copy2(ROOT/command.name,command)
    fake_tools=tmp_path/'tools';fake_tools.mkdir();marker=tmp_path/'should-not-exist'
    for name in ('uv','git','ollama'):
        path=fake_tools/name;path.write_text('#!/bin/bash\ntouch "$INSIGHTPILOT_TEST_MARKER"\n');path.chmod(0o755)
    result=subprocess.run(['/bin/bash',str(command)],env=dict(os.environ,PATH=str(fake_tools)+os.pathsep+os.environ.get('PATH',''),INSIGHTPILOT_TEST_MARKER=str(marker)),capture_output=True,text=True)
    assert result.returncode==1 and 'uv sync --locked' in result.stdout and not marker.exists()


def test_occupied_port_selects_another_without_stopping_any_process(monkeypatch):
    class Socket:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def bind(self,address):
            assert address[0]=='127.0.0.1'
            if address[1]==8501:raise OSError('occupied')
    monkeypatch.setattr(launcher.socket,'socket',lambda:Socket())
    assert launcher.find_port(8501)==8502


def test_health_check_does_not_reuse_an_unrelated_project_or_arbitrary_prefix(monkeypatch,tmp_path):
    monkeypatch.setattr(launcher,'local_get',lambda *args:pytest.fail('An untrusted URL was contacted'))
    assert not launcher.healthy({'project':str(tmp_path),'port':8501,'prefix':'other'})
    assert not launcher.healthy({'project':str(launcher.ROOT),'port':8501,'prefix':'other'})


def test_missing_dependencies_raise_clear_error_without_install(monkeypatch):
    monkeypatch.setattr(launcher.importlib.util,'find_spec',lambda name:None)
    with pytest.raises(RuntimeError,match='Missing installed dependencies'):launcher.dependencies()


def test_remote_ollama_configuration_never_contacts_or_starts_any_service(monkeypatch,capsys):
    monkeypatch.setenv('INSIGHTPILOT_OLLAMA_URL','https://example.test')
    monkeypatch.setattr(launcher.subprocess,'run',lambda *a,**k:pytest.fail('Started a service'))
    monkeypatch.setattr('insightpilot.universal.local_ai.OllamaClient.inspect_model',lambda *a:pytest.fail('Contacted a remote service'))
    launcher.ollama_status();assert 'configuration' in capsys.readouterr().out


def test_existing_local_ollama_is_not_started_again(monkeypatch):
    monkeypatch.delenv('INSIGHTPILOT_OLLAMA_URL',raising=False)
    monkeypatch.setattr('insightpilot.universal.local_ai.OllamaClient.inspect_model',lambda *a:{'status':'available'})
    monkeypatch.setattr(launcher.subprocess,'run',lambda *a,**k:pytest.fail('Duplicate Ollama startup'))
    launcher.ollama_status()


def test_offline_mac_opens_only_installed_app_with_local_cloud_disabled_environment(monkeypatch):
    from insightpilot.universal.local_ai import LocalAIError
    monkeypatch.delenv('INSIGHTPILOT_OLLAMA_URL',raising=False)
    def offline(*a):raise LocalAIError('unavailable','Offline')
    monkeypatch.setattr('insightpilot.universal.local_ai.OllamaClient.inspect_model',offline)
    monkeypatch.setattr(launcher.sys,'platform','darwin');calls=[]
    monkeypatch.setattr(launcher.subprocess,'run',lambda args,**kwargs:calls.append(args) or SimpleNamespace(returncode=0))
    launcher.ollama_status()
    assert calls==[['launchctl','setenv','OLLAMA_NO_CLOUD','1'],['launchctl','setenv','OLLAMA_HOST','127.0.0.1:11434'],['open','-gj','-a','Ollama']]


def test_owned_process_cleanup_leaves_finished_or_unowned_services_alone(monkeypatch):
    killed=[];monkeypatch.setattr(launcher.os,'killpg',lambda pid,sig:killed.append((pid,sig)))
    launcher.stop_owned(None);launcher.stop_owned(SimpleNamespace(poll=lambda:0));assert killed==[]
    process=SimpleNamespace(pid=912345,poll=lambda:None,wait=lambda **kwargs:0)
    launcher.stop_owned(process);assert killed==[(912345,launcher.signal.SIGTERM)]
