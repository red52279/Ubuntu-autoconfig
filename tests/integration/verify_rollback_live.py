#!/usr/bin/env python3
"""Root-only disposable package: install, clean reinstall, then tracked rollback."""
import importlib.util
import fcntl
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True
spec=importlib.util.spec_from_file_location('journal',ROOT/'scripts/lib/journal.py')
journal=importlib.util.module_from_spec(spec)
spec.loader.exec_module(journal)
if os.geteuid()!=0: raise SystemExit('需要 root 执行专用软件包安装/卸载测试。')
lock=(journal.STATE/'install.lock').open('a')
fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
name='ubuntu-autoconfig-rollback-smoke'
config=Path('/etc/ubuntu-autoconfig-rollback-smoke.conf')
if name in journal.packages() or config.exists(): raise SystemExit('专用测试包/配置已存在，未覆盖。')
subprocess.run(['/usr/bin/python3',str(ROOT/'scripts/lib/network.py'),'audit-apt'],check=True)
run=journal.STATE/'runs'/f'{time.strftime("%Y%m%d-%H%M%S")}-{os.getpid()}'
receipt=journal.Journal(run)
receipt.begin(os.environ.get('SUDO_USER','root'))
receipt.capture(config)
receipt.prepare('rollback_smoke')
receipt.data['active_paths']=[str(config)]
receipt.save()
try:
    with tempfile.TemporaryDirectory(prefix='package-test.', dir=run.parent) as tmp:
        temp=Path(tmp)
        scratch = temp / 'tmp'
        scratch.mkdir()
        temp.chmod(0o711)
        scratch.chmod(0o1777)
        environment = {**os.environ, 'TMPDIR': str(scratch), 'TMP': str(scratch), 'TEMP': str(scratch)}
        directory=temp/'package'
        (directory/'DEBIAN').mkdir(parents=True)
        (directory/'DEBIAN/control').write_text(f'Package: {name}\nVersion: 1.0\nArchitecture: all\nMaintainer: Autoconfig local test\nDescription: Disposable installer safety fixture\n')
        (directory/f'usr/share/{name}').mkdir(parents=True)
        (directory/f'usr/share/{name}/proof').write_text('owned by this local test\n')
        deb=temp/'smoke.deb'
        subprocess.run(['dpkg-deb','--build','--root-owner-group',str(directory),str(deb)],check=True,env=environment)
        subprocess.run(['dpkg','-i',str(deb)],check=True,env=environment)
        config.write_text('owned by the rollback smoke test\n')
        # Use a fixture-only catalog. Production's list of removable apps is
        # unchanged; real network auditing and actual APT operations stay active.
        fixture=temp/'fixture-catalog'
        fixture.mkdir()
        (fixture/'catalog.py').write_text(f"FEATURES={{'smoke':{{'clean':['{name}']}}}}\n")
        script=r'''set -Eeuo pipefail
source "$1"
task_real_network=$2
task_fixture_catalog=$3
network_tool() { /usr/bin/python3 "$task_real_network" "$@"; }
NETWORK_HELPER=$task_fixture_catalog/network.py
CLEAN_REINSTALL=1
CURRENT_STEP=smoke
apt_update() { :; }
apt_install "$4"
'''
        subprocess.run(['bash','-c',script,'_',str(ROOT/'scripts/lib/common.sh'),str(ROOT/'scripts/lib/network.py'),str(fixture),str(deb)],
                       check=True,env=environment)
        assert name in journal.packages(),'Clean reinstall did not restore the package'
        assert (Path('/usr/share')/name/'proof').exists()
        print('PASS: replacement prepared before actual remove/install of disposable package',flush=True)
    receipt.checkpoint('rollback_smoke','0')
    receipt.finish('complete')
    receipt.rollback(True)
    assert name not in journal.packages()
    assert not config.exists()
except Exception:
    if name in journal.packages(): subprocess.run(['dpkg','--remove',name],check=True)
    if config.exists() and config.read_text()=='owned by the rollback smoke test\n': config.unlink()
    receipt.data['status']='test-failed-cleaned'
    receipt.save()
    raise
print(f'PASS: real installation, clean reinstall, APT removal and configuration rollback; receipt={run}')
