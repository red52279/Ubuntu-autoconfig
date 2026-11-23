import io
import hashlib
import json
from pathlib import Path
import sys
import tarfile
from types import SimpleNamespace
import unittest
from unittest.mock import ANY, patch

from run_files import temporary_directory

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts/lib'))
import archive_apps
import updates
import vendor_release


class ArchiveTests(unittest.TestCase):
    def test_clion_download_requires_matching_chinese_cdn_and_leaves_apt_policy_unchanged(self):
        import network
        url = 'https://download.jetbrains.com/cpp/CLion-2026.3.0.1.tar.gz'
        with self.assertRaises(ValueError):
            network.check_url(url)
        with patch.object(archive_apps, 'open_direct') as request:
            archive_apps.open_clion_download(url)
            checks = request.call_args.kwargs
            checks['request_check'](url)
            checks['redirect_check'](
                url.replace('download.jetbrains.com', 'download-cdn.clf.jetbrains.com.cn') + '?Signature=test')
            for invalid in (url, url.replace('download.jetbrains.com', 'download-cdn.jetbrains.com'),
                            'https://download-cdn.clf.jetbrains.com.cn/cpp/CLion-2026.3.2.tar.gz'):
                with self.assertRaises(ValueError):
                    checks['redirect_check'](invalid)

    def test_clion_install_downloads_the_pinned_version_when_no_local_archive_exists(self):
        with temporary_directory() as directory:
            root = Path(directory)
            destination = root / 'clion'
            with patch.object(archive_apps, 'download_clion') as fetch, \
                    patch.object(archive_apps, 'unpack_clion') as unpack, \
                    patch.object(archive_apps, 'clion_latest') as latest, \
                    patch.object(archive_apps.tempfile, 'tempdir', str(root)):
                archive_apps.install_clion('amd64', destination, root, version='2026.3.0.1')
                fetch.assert_called_once_with('amd64', '2026.3.0.1', ANY, ANY)
                self.assertEqual(fetch.call_args.args[2].name, 'CLion-2026.3.0.1.tar.gz')
                unpack.assert_called_once_with('amd64', destination, '2026.3.0.1', fetch.call_args.args[2])
                latest.assert_not_called()

    def test_clion_installs_matching_original_archive_and_preserves_previous_on_error(self):
        with temporary_directory() as directory:
            root = Path(directory)
            archive = root / 'CLion-2026.3.1.tar.gz'
            product = dict(productCode='CL', version='2026.3.1', launch=[dict(os='Linux', arch='amd64')])
            with tarfile.open(archive, 'w:gz') as tar:
                for name, data in (('product-info.json', json.dumps(product)), ('bin/clion.sh', '#!/bin/sh\n'),
                                   ('jbr/legal/java.base/LICENSE', 'license')):
                    entry = tarfile.TarInfo('clion/' + name)
                    entry.size = len(data)
                    entry.mode = 0o755
                    tar.addfile(entry, io.BytesIO(data.encode()))
                link = tarfile.TarInfo('clion/jbr/legal/java.naming/LICENSE')
                link.type = tarfile.SYMTYPE
                link.linkname = '../java.base/LICENSE'
                tar.addfile(link)
                for _ in range(2):
                    header = tarfile.TarInfo('clion/jbr/legal')
                    header.type = tarfile.DIRTYPE
                    header.mode = 0o755
                    tar.addfile(header)
            installed = root / 'opt-clion'
            archive_apps.install_clion('amd64', installed, root)
            self.assertEqual(archive_apps.clion_version(installed), '2026.3.1')
            self.assertEqual((installed / 'jbr/legal/java.naming/LICENSE').read_text(), 'license')
            archive.rename(root / 'CLion-2026.3.2.tar.gz')
            with self.assertRaisesRegex(ValueError, '版本'):
                archive_apps.install_clion('amd64', installed, root)
            self.assertEqual(archive_apps.clion_version(installed), '2026.3.1')

    def test_clion_rejects_extraction_through_an_archive_link(self):
        with temporary_directory() as directory:
            archive = Path(directory) / 'archive.tar.gz'
            with tarfile.open(archive, 'w:gz') as tar:
                link = tarfile.TarInfo('clion/lib/link')
                link.type = tarfile.SYMTYPE
                link.linkname = '../bin'
                tar.addfile(link)
                tar.addfile(tarfile.TarInfo('clion/lib/link/file'), io.BytesIO(b''))
            with self.assertRaisesRegex(ValueError, '链接或普通文件下写入'):
                archive_apps.clion_archive_links(archive)

    def test_v2rayn_removes_only_the_installers_duplicate_launcher(self):
        with temporary_directory() as directory:
            root = Path(directory)
            native = root / 'v2rayN.desktop'
            managed = root / 'v2rayn.desktop'
            native.write_text('[Desktop Entry]\nExec=/opt/v2rayN/v2rayN\n')
            managed.write_text(archive_apps.V2RAYN_DESKTOP)
            with patch.object(archive_apps.shutil, 'which', side_effect=lambda file: file):
                self.assertFalse(archive_apps.v2rayn_desktop_ready(root))
                archive_apps.configure_v2rayn_desktop(root)
                self.assertTrue(archive_apps.v2rayn_desktop_ready(root))
                self.assertFalse(managed.exists())
                self.assertTrue(native.is_file())
                managed.write_text('[Desktop Entry]\nName=Custom\nExec=/opt/v2rayN/v2rayN\n')
                archive_apps.configure_v2rayn_desktop(root)
                self.assertIn('Name=Custom', managed.read_text())


class VersionTests(unittest.TestCase):
    def test_typora_uses_latest_signed_package_and_recognizes_only_legacy_source(self):
        with temporary_directory() as directory:
            root = Path(directory)
            index, release = root / 'Packages', root / 'Release'
            lines = [
                f'Package: typora\nVersion: {version}-1\nArchitecture: {arch}\n'
                f'Filename: linux/typora_{version}_{arch}.deb\nSHA256: {"a" * 64}\n'
                for version, arch in (('1.9.1', 'amd64'), ('1.14.9', 'amd64'), ('1.20.1', 'arm64'))
            ]
            data = '\n'.join(lines).encode()
            index.write_bytes(data)
            release.write_text(f'SHA256:\n {hashlib.sha256(data).hexdigest()} {len(data)} Packages\n')
            url, _ = vendor_release.typora_archive('amd64', release, index)
            self.assertEqual(url, 'https://downloads.typoraio.cn/linux/typora_1.14.9_amd64.deb')
        legacy = 'deb [arch=amd64 signed-by=/usr/share/keyrings/typora-archive-keyring.gpg] https://downloads.typoraio.cn linux/'
        self.assertTrue(vendor_release.managed_typora_source(legacy))
        self.assertFalse(vendor_release.managed_typora_source(legacy + '\n# Custom configuration'))

    def test_update_sources_keep_domestic_stanzas_and_skip_foreign_stanzas(self):
        domestic = 'Types: deb\nURIs: https://mirrors.aliyun.com/ubuntu\nSuites: noble\nComponents: main\n'
        foreign = 'Types: deb\nURIs: https://packages.microsoft.com/repos/code\nSuites: stable\nComponents: main\n'
        self.assertEqual(updates.approved_source_text(domestic + '\n' + foreign, deb822=True),
                         (domestic + '\n', True))
        lines = 'deb https://packages.microsoft.com/repos/code stable main\n'
        lines += 'deb https://mirrors.aliyun.com/ubuntu noble main\n'
        self.assertEqual(updates.approved_source_text(lines), (lines.splitlines(keepends=True)[1], True))

    def test_candidates_compare_versions_and_include_multiarch_packages(self):
        installed = {'libexample:amd64': '1', 'steam-libs:i386': '2'}
        simulation = 'Inst libexample [1] (2 Debian [amd64])\nInst steam-libs:i386 [2] (3 Ubuntu [i386])'
        self.assertEqual(updates.planned_installs(simulation, installed, 'amd64'),
                         {'libexample:amd64': '2', 'steam-libs:i386': '3'})
        self.assertEqual(updates.installed_targets('steam', installed, 'noble'), {'steam-libs:i386'})

    def test_qt_version_targets_include_dev_extensions_and_base_tools(self):
        installed = {'qt6-base-dev': '6.10.2', 'qtcreator': '18.0.2',
                     'qt6-multimedia-dev:amd64': '6.10.2',
                     'libqt6multimedia6:amd64': '6.10.2', 'qt6-style-kvantum': '1.1'}
        rows = ('qt6-base-dev\tqt6-base\thttps://www.qt.io/developers/\tinstalled\n'
                'qtcreator\tqtcreator\t\tinstalled\n'
                'qt6-multimedia-dev:amd64\tqt6-multimedia\thttps://www.qt.io/developers/\tinstalled\n'
                'libqt6multimedia6:amd64\tqt6-multimedia\thttps://www.qt.io/developers/\tinstalled\n'
                'qt6-style-kvantum\tqt6-style-kvantum\thttps://github.com/tsujan/Kvantum\tinstalled\n')
        with patch('qt_packages.subprocess.check_output', return_value=rows):
            targets = updates.installed_targets('desktop_tools', installed, 'resolute')
        self.assertEqual(targets, {'qt6-base-dev', 'qtcreator', 'qt6-multimedia-dev:amd64'})

    def test_rollback_refuses_solver_changes_outside_the_record(self):
        receipt = SimpleNamespace(data={'version_update': True, 'packages_before': {'gedit': '1'},
                                        'packages_after': {'gedit': '2'}},
                                  plan=lambda _: {'restore_files': [], 'preserve_modified_files': [],
                                                  'preserve_runtime_changes': []})
        with temporary_directory() as work, patch.object(updates, 'packages', return_value={'gedit': '2'}), \
                patch.object(updates, 'apt', return_value='Inst other (5 Ubuntu [amd64])'), \
                patch.object(updates, 'apt_apply') as apply:
            with self.assertRaisesRegex(ValueError, '计划外'):
                updates.rollback_versions(receipt, True, Path(work), 'amd64')
            apply.assert_not_called()

    def test_rollback_uses_recorded_versions_and_verifies_result(self):
        receipt = SimpleNamespace(data={'version_update': True, 'packages_before': {'gedit': '1'},
                                        'packages_after': {'gedit': '2'}},
                                  plan=lambda _: {'restore_files': [], 'preserve_modified_files': [],
                                                  'preserve_runtime_changes': []}, finish=lambda _: None)
        with temporary_directory() as work, \
                patch.object(updates, 'packages', side_effect=[{'gedit': '2'}, {'gedit': '1'}]), \
                patch.object(updates, 'apt', return_value='Inst gedit [2] (1 Ubuntu [amd64])') as apt, \
                patch.object(updates, 'apt_apply') as apply:
            updates.rollback_versions(receipt, True, Path(work), 'amd64')
            self.assertIn('gedit=1', apt.call_args.args)
            self.assertIn('gedit=1', apply.call_args.args[0])
