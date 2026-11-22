#!/usr/bin/env python3
"""Configure the chosen input framework without replacing other user settings."""
import argparse
import ast
import configparser
import os
import shlex
import signal
import sys
from pathlib import Path
import subprocess
import time
from desktop_locale import write_file

ENGLISH_LAYOUT = ('xkb', 'us')
ENGLISH_IBUS_ENGINE = 'xkb:us::eng'
GNOME_SOURCES = 'org.gnome.desktop.input-sources'
LOGIN_UNIT = 'ubuntu-autoconfig-english-input.service'
LOGIN_TARGET = 'gnome-session-pre.target'
LOGIN_COMMAND = f'/usr/bin/gsettings set {GNOME_SOURCES} mru-sources "[{ENGLISH_LAYOUT!r}]"'
FCITX_ENGLISH = 'fcitx-keyboard-us:True'
FCITX_PROGRAM = {'DefaultInputMethodState': 'Inactive', 'ShareStateAmongWindow': 'No'}


class NativeInputUnavailable(RuntimeError):
    """The package exists but the native engine cannot initialize."""


def replace_ini(text, updates):
    remaining = {section: dict(values) for section, values in updates.items()}
    lines = []
    section = None
    seen_sections = set()

    def flush():
        if section in remaining:
            lines.extend(f'{key}={value}' for key, value in remaining[section].items())
            remaining[section].clear()

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith('[') and stripped.endswith(']'):
            flush()
            section = stripped[1:-1]
            seen_sections.add(section)
            lines.append(line)
        elif section in updates and not stripped.startswith(('#', ';')) and '=' in line:
            key = line.split('=', 1)[0].strip()
            if key in updates[section]:
                if key in remaining[section]:
                    lines.append(f'{key}={remaining[section].pop(key)}')
            else:
                lines.append(line)
        else:
            lines.append(line)
    flush()
    for group, values in remaining.items():
        if group not in seen_sections:
            lines.extend(['', f'[{group}]', *(f'{key}={value}' for key, value in values.items())])
    return '\n'.join(lines).strip() + '\n'


def input_sources(existing, engine=None):
    sources = [ENGLISH_LAYOUT, *(tuple(item) for item in existing if tuple(item) != ENGLISH_LAYOUT)]
    if engine and ('ibus', engine) not in sources:
        sources.append(('ibus', engine))
    return sources


def read_ini(path):
    info = configparser.ConfigParser(interpolation=None, strict=False)
    info.read(path, encoding='utf-8')
    return info


def keyboard_layouts(text):
    keys = {}
    for line in text.splitlines():
        key, separator, value = line.partition('=')
        if separator and key.strip() in ('XKBLAYOUT', 'XKBVARIANT'):
            parts = shlex.split(value, comments=True)
            keys[key.strip()] = parts[0].split(',') if parts else []
    variants = keys.get('XKBVARIANT', [])
    return [(layout, variants[index] if index < len(variants) else '')
            for index, layout in enumerate(keys.get('XKBLAYOUT', [])) if layout]


def configure_system_keyboard(path=Path('/etc/default/keyboard')):
    original = path.read_text() if path.exists() else ''
    layouts = keyboard_layouts(original)
    if layouts[:1] == [('us', '')]:
        return
    layouts = [('us', ''), *(layout for layout in layouts if layout != ('us', ''))]
    settings = {'XKBLAYOUT': ','.join(layout for layout, _ in layouts),
                'XKBVARIANT': ','.join(variant for _, variant in layouts).rstrip(',')}
    lines = [line for line in original.splitlines() if line.partition('=')[0].strip() not in settings]
    lines.extend(f'{key}={shlex.quote(value)}' for key, value in settings.items())
    write_file(path, '\n'.join(lines) + '\n')


def configure_gnome_input(home, engine=None):
    existing = ast.literal_eval(gsettings(GNOME_SOURCES, 'sources').replace('@a(ss) ', '', 1))
    gsettings(GNOME_SOURCES, 'mru-sources', repr([ENGLISH_LAYOUT]))
    gsettings(GNOME_SOURCES, 'sources', repr(input_sources(existing, engine)))
    # GNOME starts each new window with the first source when per-window
    # input is enabled. An explicit switch stays with the existing window.
    gsettings(GNOME_SOURCES, 'per-window', 'true')

    unit_dir = home / '.config/systemd/user'
    # GNOME initializes its MRU list before desktop autostart programs run.
    # Reset it before the shell starts, even if the last session used Chinese.
    write_file(unit_dir / LOGIN_UNIT, f'''[Unit]
Description=Start GNOME with English keyboard input
Before={LOGIN_TARGET}

[Service]
Type=oneshot
ExecStart={LOGIN_COMMAND}
''')
    link = unit_dir / f'{LOGIN_TARGET}.wants' / LOGIN_UNIT
    if not link.is_symlink() or os.readlink(link) != f'../{LOGIN_UNIT}':
        link.parent.mkdir(parents=True, exist_ok=True)
        link.unlink(missing_ok=True)
        link.symlink_to(Path('..') / LOGIN_UNIT)


def disable_autostart(home, filename):
    write_file(Path(home) / '.config/autostart' / filename,
               '[Desktop Entry]\nType=Application\nName=Disabled alternative input framework\nHidden=true\n')


def configure_sogou(home, offline=False):
    home = Path(home)
    write_file(home / '.config/environment.d/90-ubuntu-autoconfig-input.conf', 'GTK_IM_MODULE=fcitx\nQT_IM_MODULE=fcitx\nXMODIFIERS=@im=fcitx\n')
    config = home / '.config/fcitx/config'
    original = config.read_text() if config.exists() else ''
    write_file(config, replace_ini(original, {'Program': FCITX_PROGRAM, 'Hotkey': {
        'TriggerKey': 'CTRL_SPACE', 'SwitchKey': 'L_SHIFT',
        'IMSwitchKey': 'True', 'IMSwitchIncludeInactive': 'True',
        'IMSwitchHotkey': 'CTRL_SHIFT',
    }}))
    profile = home / '.config/fcitx/profile'
    text = profile.read_text() if profile.exists() else '[Profile]\n'
    enabled = [FCITX_ENGLISH, 'sogoupinyin:True']
    existing = read_ini(profile).get('Profile', 'EnabledIMList', fallback='').split(',')
    enabled.extend(item for item in existing if item and item.partition(':')[0] not in ('fcitx-keyboard-us', 'sogoupinyin'))
    # Inactive contexts use the first (US keyboard) entry. Ctrl+Space
    # activates the selected Chinese engine in the current context.
    write_file(profile, replace_ini(text, {'Profile': {'IMName': 'sogoupinyin', 'EnabledIMList': ','.join(enabled)}}))
    configure_gnome_input(home)
    # Re-enable vendor services if switching back from the native framework.
    for filename in ('sogoupinyin-service.desktop', 'sogoupinyin-watchdog.desktop'):
        override = home / '.config/autostart' / filename
        if override.exists() and 'Name=Disabled alternative input framework' in override.read_text():
            override.unlink()
    if not offline and import_desktop_environment():
        subprocess.run(['fcitx-remote', '-r'], check=False)
        subprocess.run(['fcitx-remote', '-c'], check=False)


def gsettings(schema, key, value=None):
    command = ['gsettings', 'get' if value is None else 'set', schema, key]
    if value is not None:
        command.append(value)
    return subprocess.check_output(command, text=True).strip()


def import_desktop_environment():
    environment = subprocess.run(['systemctl', '--user', 'show-environment'],
                                 capture_output=True, text=True)
    for line in environment.stdout.splitlines():
        key, separator, value = line.partition('=')
        if separator and key in ('DISPLAY', 'WAYLAND_DISPLAY', 'XAUTHORITY', 'XDG_SESSION_TYPE', 'IBUS_ADDRESS'):
            os.environ.setdefault(key, value)
    return bool(os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY'))


def configure_ibus(home, engine, offline=False):
    home = Path(home)
    if not offline:
        # A systemd/root installer has no DISPLAY even when its target user is
        # logged in. IBus uses DISPLAY when locating its private session socket.
        offline = not import_desktop_environment()
    write_file(home / '.config/environment.d/90-ubuntu-autoconfig-input.conf',
               'GTK_IM_MODULE=ibus\nQT_IM_MODULE=ibus\nXMODIFIERS=@im=ibus\n')
    for filename in ('ubuntu-autoconfig-fcitx.desktop', 'sogoupinyin-service.desktop', 'sogoupinyin-watchdog.desktop'):
        disable_autostart(home, filename)
    configure_gnome_input(home, engine)
    # Keep GNOME's standard key and add the user's Ctrl+Space fallback.
    keys = ast.literal_eval(gsettings('org.gnome.desktop.wm.keybindings', 'switch-input-source').replace('@as ', '', 1))
    for key in ('<Super>space', '<Control>space'):
        if key not in keys:
            keys.append(key)
    gsettings('org.gnome.desktop.wm.keybindings', 'switch-input-source', repr(keys))
    for key in ('preload-engines', 'engines-order'):
        existing = ast.literal_eval(gsettings('org.freedesktop.ibus.general', key).replace('@as ', '', 1))
        engines = [ENGLISH_IBUS_ENGINE, engine]
        gsettings('org.freedesktop.ibus.general', key, repr(list(dict.fromkeys(engines + existing))))
    gsettings('org.freedesktop.ibus.general', 'enable-by-default', 'false')
    gsettings('org.freedesktop.ibus.general', 'use-global-engine', 'true')
    gsettings('org.freedesktop.ibus.general.hotkey', 'triggers', repr(['<Control>space']))
    gsettings('org.gnome.desktop.interface', 'gtk-im-module', 'ibus')
    if offline:
        print('Native IBus preferences saved for next login:', engine)
        return
    subprocess.run(['systemctl', '--user', 'set-environment', 'GTK_IM_MODULE=ibus',
                    'QT_IM_MODULE=ibus', 'XMODIFIERS=@im=ibus'], check=True)
    # If IBus was running before the engine was installed, refresh its registry.
    # GNOME owns this daemon; restart via IBus instead of spawning a rival one.
    listed = subprocess.run(['ibus', 'list-engine'], capture_output=True, text=True)
    if engine not in listed.stdout:
        subprocess.run(['ibus', 'restart'], check=True)
        for _ in range(30):
            listed = subprocess.run(['ibus', 'list-engine'], capture_output=True, text=True)
            if listed.returncode == 0 and engine in listed.stdout:
                break
            time.sleep(.5)
        else:
            raise NativeInputUnavailable('IBus 新中文引擎尚未注册。')
    subprocess.run(['ibus', 'engine', ENGLISH_IBUS_ENGINE], check=False)
    # Stop only this user's old framework after the new engine is ready.
    if Path('/usr/bin/fcitx-remote').exists():
        subprocess.run(['/usr/bin/fcitx-remote', '-e'], check=False)
    obsolete = {'/opt/sogoupinyin/files/bin/sogoupinyin-watchdog',
                '/opt/sogoupinyin/files/bin/sogoupinyin-service'}
    for process in Path('/proc').glob('[0-9]*'):
        try:
            if process.stat().st_uid != os.getuid():
                continue
            argv = (process / 'cmdline').read_bytes().split(b'\0')
            if argv and os.fsdecode(argv[0]) in obsolete:
                os.kill(int(process.name), signal.SIGTERM)
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
    print('Native IBus input source configured:', engine)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('framework', choices=['ibus', 'sogou', 'system-keyboard'])
    parser.add_argument('user_home', type=Path, nargs='?')
    parser.add_argument('--engine', default='libpinyin')
    parser.add_argument('--offline', action='store_true')
    args = parser.parse_args()
    if args.framework == 'system-keyboard':
        configure_system_keyboard()
        return
    if args.user_home is None:
        parser.error('user_home is required for ibus and sogou')
    if args.framework == 'ibus':
        configure_ibus(args.user_home, args.engine, args.offline)
    else:
        configure_sogou(args.user_home, args.offline)


if __name__ == '__main__':
    try:
        main()
    except NativeInputUnavailable as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(21)
