import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
from run_files import temporary_directory
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('journal', ROOT / 'scripts/lib/journal.py')
journal = importlib.util.module_from_spec(spec)
spec.loader.exec_module(journal)


class RollbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = temporary_directory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.j = journal.Journal(self.root / 'run')
        with patch.object(journal, 'packages', return_value={'existing': '1'}), patch.object(journal, 'query', return_value=''):
            self.j.begin('root')

    def checkpoint(self):
        with patch.object(journal, 'packages', return_value={'existing': '1', 'new-app': '2'}):
            self.j.checkpoint('test', '0')

    def test_removes_only_run_additions_not_preexisting_or_later_packages(self):
        self.checkpoint()
        plan = self.j.plan({'existing': '1', 'new-app': '2', 'later-app': '1'})
        self.assertEqual(plan['remove_packages'], ['new-app'])
        self.assertEqual(self.j.plan({'existing': '1', 'new-app': '3'})['remove_packages'], [])

    def test_preserves_user_edits_after_install(self):
        path = self.root / 'settings'
        path.write_text('before')
        self.j.capture(path)
        path.write_text('installer')
        self.checkpoint()
        path.write_text('user-edit')
        plan = self.j.plan({})
        self.assertEqual(plan['preserve_modified_files'], [str(path)])
        self.assertEqual(plan['restore_files'], [])

    def test_restores_actual_file_and_symlink_and_removes_new_artifact(self):
        file = self.root / 'config'
        file.write_text('original')
        link = self.root / 'link'
        link.symlink_to(file)
        added = self.root / 'new-directory'
        for path in (file, link, added): self.j.capture(path)
        file.write_text('changed')
        link.unlink()
        link.symlink_to('other')
        added.mkdir()
        (added / 'owned-file').write_text('new')
        with patch.object(journal, 'packages', return_value={'existing': '1'}):
            self.j.checkpoint('test', '0')
            with patch.object(journal, 'STATE', self.root / 'state'):
                self.j.rollback(True)
        self.assertEqual(file.read_text(), 'original')
        self.assertEqual(os.readlink(link), str(file))
        self.assertFalse(added.exists())

    def test_uncertain_crash_changes_are_preserved(self):
        path = self.root / 'config'
        path.write_text('before')
        self.j.capture(path)
        self.j.data['files'][str(path)]['uncertain'] = True
        path.write_text('possibly-user-data')
        self.assertEqual(self.j.plan({})['preserve_modified_files'], [str(path)])

    def test_upgrade_limitations_reported(self):
        self.j.data['packages_after'] = {'existing': '2'}
        self.assertEqual(self.j.plan({'existing': '2'})['upgraded_packages_not_downgraded'], ['existing'])

    def test_runtime_changes_after_install_are_preserved(self):
        self.j.data['runtime_after'] = {'timezone': 'Asia/Shanghai', 'chrony_active': 'active'}
        with patch.object(journal, 'query', side_effect=['Europe/London', None]):
            plan = self.j.plan({})
        self.assertEqual(plan['preserve_runtime_changes'], ['timezone', 'chrony_active'])

    def test_parent_snapshot_covers_child(self):
        parent = self.root / 'sources'
        parent.mkdir()
        child = parent / 'custom.list'
        child.write_text('original')
        self.j.capture(parent)
        self.j.capture(child)
        self.assertEqual(len(self.j.data['files']), 1)


class InterruptTests(unittest.TestCase):
    def _check_signal_recovery(self, stop_child=False):
        with temporary_directory() as tmp:
            work = Path(tmp)
            script = work / 'run.sh'
            script.write_text(r'''set -Eeuo pipefail
source "$ROOT/scripts/lib/common.sh"
source "$ROOT/scripts/lib/state.sh"
source "$ROOT/scripts/lib/lifecycle.sh"
source "$ROOT/scripts/lib/planning.sh"
SCRIPT_DIR=$ROOT
STATE_DIR=$WORK
SUCCESS_LOG=$WORK/success
ERROR_LOG=$WORK/errors
CONFIG_FILE=$ROOT/config.json
INPUT_METHOD=auto
LOG_FILE=$WORK/install.log
LOG_DIR=$WORK
SUMMARY_EVENTS=$WORK/events.jsonl
FEATURES=(slow)
declare -A FEATURE_LABEL=([slow]=slow) ENABLED=([slow]=1)
mkdir "$WORK/runs"
setup_run_temp "$WORK/runs"
trap summary_on_exit EXIT
FAILURES=()
SKIPPED=()
FORCE=0
DRY_RUN=0
trap 'interrupt_run TERM' TERM
slow() { echo "$BASHPID" > "$WORK/started"; sleep 15; touch "$WORK/continued"; }
run_step slow slow slow
''')
            env = dict(os.environ, ROOT=str(ROOT), WORK=tmp)
            with (work / 'output').open('w') as output:
                process = subprocess.Popen(['bash', str(script)], env=env, stdout=output, stderr=output)
                try:
                    for _ in range(100):
                        if (work / 'started').exists(): break
                        time.sleep(.02)
                    self.assertTrue((work / 'started').exists())
                    if stop_child:
                        os.killpg(int((work / 'started').read_text()), signal.SIGSTOP)
                    process.send_signal(signal.SIGTERM)
                    self.assertEqual(process.wait(timeout=5), 130)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait()
            self.assertFalse((work / 'state').exists())
            self.assertFalse((work / 'continued').exists())
            self.assertFalse(list((work / 'runs').glob('tmp.*')))
            self.assertEqual((work / 'failed-step').read_text().strip(), 'slow')
            report=json.loads((work / 'install.summary.json').read_text())
            self.assertEqual(report['exit_code'],130)
            self.assertEqual(report['counts']['interrupted'],1)


    def test_sigterm_stops_entire_step_and_never_marks_success(self):
        self._check_signal_recovery()

    def test_stopped_job_is_continued_to_process_termination(self):
        self._check_signal_recovery(stop_child=True)


if __name__ == '__main__': unittest.main()
