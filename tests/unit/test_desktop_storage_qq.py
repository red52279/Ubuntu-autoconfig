import importlib.util
import hashlib
import json
import os
from types import SimpleNamespace
from pathlib import Path
import sys
from run_files import temporary_directory
import unittest
from unittest.mock import patch

LIB = Path(__file__).resolve().parents[2] / 'scripts/lib'


def module(name):
    spec = importlib.util.spec_from_file_location(name, LIB / (name + '.py'))
    loaded = importlib.util.module_from_spec(spec)
    with patch.object(sys, 'path', [str(LIB), *sys.path]):
        spec.loader.exec_module(loaded)
    return loaded


locale = module('desktop_locale')
storage = module('storage')
qq = module('qq')
input_config = module('input_config')
vendor_release = module('vendor_release')


class DesktopLocaleTests(unittest.TestCase):
    def test_chinese_preference_keeps_english_paths_and_existing_data(self):
        with temporary_directory() as tmp:
            home = Path(tmp)
            (home / 'Desktop').mkdir()
            (home / 'Desktop/user.txt').write_text('keep this data')
            (home / '桌面').mkdir()
            (home / '桌面/original.txt').write_text('do not move me')
            locale.configure_user_files(home)
            mapping = (home / '.config/user-dirs.dirs').read_text()
            self.assertIn('XDG_DESKTOP_DIR="$HOME/Desktop"', mapping)
            self.assertIn('XDG_DOWNLOAD_DIR="$HOME/Downloads"', mapping)
            self.assertEqual((home / 'Desktop/user.txt').read_text(), 'keep this data')
            self.assertTrue((home / '桌面/original.txt').exists())
            self.assertIn('enabled=False', (home / '.config/user-dirs.conf').read_text())
            self.assertIn('Hidden=true', (home / '.config/autostart/user-dirs-update-gtk.desktop').read_text())

    def test_directory_name_collision_never_overwrites_a_user_file(self):
        with temporary_directory() as tmp:
            home = Path(tmp)
            (home / 'Desktop').write_text('user file')
            with self.assertRaises(FileExistsError): locale.configure_user_files(home)
            self.assertEqual((home / 'Desktop').read_text(), 'user file')


class InputConfigTests(unittest.TestCase):
    def test_input_sources_keep_existing_layout_and_other_chinese_engine(self):
        existing = [('ibus', 'rime'), ('xkb', 'de'), ('xkb', 'us')]
        sources = input_config.input_sources(existing, 'libpinyin')
        self.assertEqual(sources, [('xkb', 'us'), ('ibus', 'rime'), ('xkb', 'de'), ('ibus', 'libpinyin')])
        self.assertEqual(input_config.input_sources(sources, 'libpinyin'), sources)

    def test_native_defaults_cover_login_new_windows_and_engines(self):
        with temporary_directory() as tmp:
            home = Path(tmp)
            settings = {
                'sources': "[('ibus', 'libpinyin'), ('xkb', 'us')]",
                'switch-input-source': '@as []',
                'preload-engines': "['rime']",
                'engines-order': "['libpinyin']",
            }
            def gsettings(schema, key, value=None):
                if value is not None:
                    settings[key] = value
                return settings[key]
            with patch.object(input_config, 'gsettings', side_effect=gsettings):
                input_config.configure_ibus(home, 'libpinyin', offline=True)
            self.assertEqual(settings['sources'], "[('xkb', 'us'), ('ibus', 'libpinyin')]")
            self.assertEqual(settings['mru-sources'], "[('xkb', 'us')]")
            self.assertEqual(settings['per-window'], 'true')
            self.assertEqual(settings['preload-engines'], "['xkb:us::eng', 'libpinyin', 'rime']")
            self.assertEqual(settings['engines-order'], "['xkb:us::eng', 'libpinyin']")
            self.assertEqual(settings['enable-by-default'], 'false')
            self.assertEqual(settings['use-global-engine'], 'true')
            unit_dir = home / '.config/systemd/user'
            unit = unit_dir / input_config.LOGIN_UNIT
            self.assertIn('Before=gnome-session-pre.target', unit.read_text())
            self.assertIn(f'ExecStart={input_config.LOGIN_COMMAND}', unit.read_text())
            self.assertEqual((unit_dir / 'gnome-session-pre.target.wants' / unit.name).resolve(), unit)

    def test_sogou_starts_inactive_for_every_new_context(self):
        with temporary_directory() as tmp:
            home = Path(tmp)
            path = home / '.config/fcitx'
            path.mkdir(parents=True)
            (path / 'config').write_text('[Program]\nDefaultInputMethodState=Active\nShareStateAmongWindow=All\n')
            (path / 'profile').write_text('[Profile]\nEnabledIMList=other-ime:True,sogoupinyin:True\n')
            with patch.object(input_config, 'configure_gnome_input') as gnome:
                input_config.configure_sogou(home, offline=True)
            gnome.assert_called_once_with(home)
            config = input_config.read_ini(path / 'config')
            profile = input_config.read_ini(path / 'profile')
            self.assertEqual(config['Program']['DefaultInputMethodState'], 'Inactive')
            self.assertEqual(config['Program']['ShareStateAmongWindow'], 'No')
            self.assertEqual(profile['Profile']['IMName'], 'sogoupinyin')
            self.assertEqual(profile['Profile']['EnabledIMList'], 'fcitx-keyboard-us:True,sogoupinyin:True,other-ime:True')

    def test_console_keyboard_prefers_us_and_preserves_options(self):
        with temporary_directory() as tmp:
            path = Path(tmp) / 'keyboard'
            path.write_text('XKBLAYOUT="de,us"\nXKBVARIANT="nodeadkeys,"\nXKBOPTIONS="ctrl:nocaps"\n')
            input_config.configure_system_keyboard(path)
            self.assertEqual(input_config.keyboard_layouts(path.read_text()), [('us', ''), ('de', 'nodeadkeys')])
            self.assertIn('XKBOPTIONS="ctrl:nocaps"', path.read_text())
            configured = path.read_bytes()
            input_config.configure_system_keyboard(path)
            self.assertEqual(path.read_bytes(), configured)

    def test_missing_english_layout_gets_usable_source(self):
        self.assertEqual(input_config.input_sources([], 'libpinyin'), [('xkb', 'us'), ('ibus', 'libpinyin')])

    def test_installer_recovers_display_without_importing_proxy_environment(self):
        settings = SimpleNamespace(stdout='DISPLAY=:77\nWAYLAND_DISPLAY=wayland-0\nHTTPS_PROXY=http://invalid\n')
        with patch.dict(os.environ, {}, clear=True), patch.object(input_config.subprocess, 'run', return_value=settings):
            self.assertTrue(input_config.import_desktop_environment())
            self.assertEqual(os.environ['DISPLAY'], ':77')
            self.assertNotIn('HTTPS_PROXY', os.environ)

    def test_hotkey_update_preserves_unrelated_settings_and_comments(self):
        text = '# user comment\n[Hotkey]\nSwitchKey=Disabled\nCandidateWordNumber=9\n[Other]\nKeep=1\n'
        result = input_config.replace_ini(text, {'Hotkey': {'SwitchKey': 'L_SHIFT', 'TriggerKey': 'CTRL_SPACE'}})
        self.assertIn('# user comment', result)
        self.assertIn('CandidateWordNumber=9', result)
        self.assertIn('[Other]\nKeep=1', result)
        self.assertIn('SwitchKey=L_SHIFT', result)
        self.assertIn('TriggerKey=CTRL_SPACE', result)
        self.assertEqual(input_config.replace_ini(result, {'Hotkey': {'SwitchKey': 'L_SHIFT', 'TriggerKey': 'CTRL_SPACE'}}), result)


class QQTests(unittest.TestCase):
    def setUp(self):
        self.url = 'https://qqdl.gtimg.cn/qqfile/QQNTV2/test_amd64.deb'

    def test_current_official_configuration_is_selected(self):
        self.assertEqual(qq.choose_url({'Linux': {'x64DownloadUrl': {'deb': self.url}}}), self.url)
        with self.assertRaises(ValueError):
            qq.choose_url({'Linux': {'x64DownloadUrl': {'deb': 'https://untrusted.invalid/a.deb'}}})

    def test_public_signing_request_matches_official_frontend(self):
        response = {'retcode': 0, 'data': {'url': self.url + '?sign=example&t=123'}}
        with patch.object(qq, 'read_json', return_value=response) as request:
            self.assertEqual(qq.signed_url(self.url), response['data']['url'])
        self.assertEqual(json.loads(request.call_args.kwargs['data']), {'url': self.url})
        self.assertEqual(json.loads(request.call_args.kwargs['request_headers']['x-oidb'])['uint32_command'], '0x9b8e')

    def test_missing_signature_or_changed_file_is_rejected(self):
        for url in (self.url, self.url.replace('test_amd64', 'different') + '?sign=x&t=1'):
            with patch.object(qq, 'read_json', return_value={'retcode': 0, 'data': {'url': url}}):
                with self.assertRaises(ValueError): qq.signed_url(self.url)


class StorageTests(unittest.TestCase):
    def test_legacy_cleanup_removes_only_managed_mounts_and_its_disk_profile(self):
        with temporary_directory() as tmp:
            root = Path(tmp)
            root_entry = 'UUID=system / ext4 defaults 0 1\n'
            manual_entry = 'UUID=custom /mnt/custom ext4 defaults 0 2\n'
            fstab = root / storage.FSTAB
            config = root / storage.STORAGE_CONFIG
            options = root / storage.UDISKS_OPTIONS
            config.parent.mkdir(parents=True)
            options.parent.mkdir()
            fstab.write_text(root_entry + storage.BEGIN + '\nUUID=old /mnt/old ntfs-3g defaults 0 0\n'
                             + storage.END + '\n' + manual_entry)
            config.write_text('{"volumes": []}')
            options.write_text(storage.LEGACY_OVERRIDE + '\n\n')
            self.assertTrue(storage.remove_legacy_storage(root))
            self.assertEqual(fstab.read_text(), root_entry + manual_entry)
            self.assertFalse(config.exists())
            self.assertFalse(options.exists())
            self.assertFalse(storage.remove_legacy_storage(root))

    def test_cleanup_preserves_a_custom_udisks_file(self):
        with temporary_directory() as tmp:
            root = Path(tmp)
            fstab = root / storage.FSTAB
            options = root / storage.UDISKS_OPTIONS
            options.parent.mkdir(parents=True)
            fstab.write_text(f'{storage.BEGIN}\n{storage.END}\n')
            custom = storage.LEGACY_OVERRIDE + '\nvfat_defaults=flush\n'
            options.write_text(custom)
            self.assertTrue(storage.remove_legacy_storage(root))
            self.assertEqual(options.read_text(), custom)

    def test_malformed_fstab_block_is_kept_in_full(self):
        with temporary_directory() as tmp:
            root = Path(tmp)
            fstab = root / storage.FSTAB
            fstab.parent.mkdir()
            original = storage.BEGIN + '\nUUID=system / ext4 defaults 0 1\n'
            fstab.write_text(original)
            with self.assertRaises(ValueError):
                storage.remove_legacy_storage(root)
            self.assertEqual(fstab.read_text(), original)


class VendorMirrorTests(unittest.TestCase):
    def test_spark_package_requires_checksum_from_verified_release(self):
        with temporary_directory() as tmp:
            directory = Path(tmp)
            package_hash = 'a' * 64
            index = directory / 'Packages'
            index.write_text('Package: code\nArchitecture: amd64\n'
                             'Filename: ./development/code/code_1_amd64.deb\n'
                             f'SHA256: {package_hash}\n')
            data = index.read_bytes()
            release = directory / 'Release'
            release.write_text(f'SHA256:\n {hashlib.sha256(data).hexdigest()} {len(data)} Packages\n')
            url, checksum = vendor_release.spark_archive('amd64', 'code', release, index)
            self.assertEqual(url, 'https://mirrors.sdu.edu.cn/spark-store/amd64-store/development/code/code_1_amd64.deb')
            self.assertEqual(checksum, package_hash)
            index.write_bytes(data.replace(b'code_1', b'code_2'))
            with self.assertRaises(ValueError):
                vendor_release.spark_archive('amd64', 'code', release, index)


if __name__ == '__main__': unittest.main()
