"""Test and deploy only the B Chrome timeline protocol changes to EC2."""
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
BASELINE = 'b6075db'


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT)


def main():
    files = git('ls-tree', '-r', '--name-only', BASELINE, 'server', 'common').decode().splitlines()
    baseline = {name: git('show', BASELINE + ':' + name) for name in files if name.endswith('.py')}
    main_source = baseline['server/main.py'].decode()
    old = 'response.update(exam_events_v1=True, exam_control_v1=True, exam_foreground_v1=True)'
    assert main_source.count(old) == 1
    main_source = main_source.replace(old, 'response.update(exam_events_v1=True, exam_control_v1=True, exam_foreground_v1=True, exam_chrome_timeline_v1=True)')
    contract = baseline['server/contracts.py'].decode()
    current = (ROOT / 'server/contracts.py').read_text(encoding='utf-8')
    first, last = '    elif kind == "EXAM_TAB_CHANGED":', '    elif kind == "FOREGROUND_MONITOR_STATUS":'
    addition = current[current.index(first):current.index(last)]
    assert contract.count(last) == 1 and first not in contract
    contract = contract.replace(last, addition + last)
    updates = {'server/main.py': main_source.encode(), 'server/contracts.py': contract.encode()}
    with tempfile.TemporaryDirectory(prefix='cloudnet-chrome-release-') as temporary:
        stage = Path(temporary)
        for name, content in (baseline | updates).items():
            path = stage / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        (stage / 'requirements.txt').write_bytes((ROOT / 'requirements.txt').read_bytes())
        env = dict(os.environ, PYTHONPATH=os.pathsep.join((str(stage), str(ROOT))))
        subprocess.run([sys.executable, '-m', 'unittest',
                        'tests.test_a_chrome_timeline.ChromeTimelineTests.test_chrome_events_are_durable_deduplicated_and_relayed'],
                       cwd=stage, env=env, check=True)
    payload = {name: {'before': hashlib.sha256(baseline[name]).hexdigest(),
                      'after': base64.b64encode(content).decode()} for name, content in updates.items()}
    encoded = base64.b64encode(json.dumps(payload).encode()).decode()
    script = ('PAYLOAD = ' + repr(encoded) + '\n' + (ROOT / 'ops/chrome_timeline_remote.py').read_text(encoding='utf-8')).encode()
    ssh = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '-o', 'StrictHostKeyChecking=yes',
           '-o', 'HostKeyAlias=32.236.13.91', '-i', str(Path.home() / '.ssh/sheep_jae0_key.pem'),
           'ubuntu@13.238.97.144', '/usr/bin/python3', '-']
    subprocess.run(ssh, input=script, check=True)


if __name__ == '__main__':
    main()
