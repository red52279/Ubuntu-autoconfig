#!/usr/bin/env python3
"""Set Chinese user locale preferences while retaining English XDG directory paths."""
import os
from pathlib import Path
import sys
import tempfile

DIRECTORIES = {
    'DESKTOP': 'Desktop', 'DOWNLOAD': 'Downloads', 'TEMPLATES': 'Templates',
    'PUBLICSHARE': 'Public', 'DOCUMENTS': 'Documents', 'MUSIC': 'Music',
    'PICTURES': 'Pictures', 'VIDEOS': 'Videos',
}


def write_file(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = (path.stat().st_mode & 0o777) if path.exists() else 0o644
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def configure_user_files(home):
    home = Path(home)
    # Create missing standard directories; do not rename or move user data.
    for directory in DIRECTORIES.values():
        (home / directory).mkdir(exist_ok=True)
    config = home / '.config'
    mapping = '# Managed by ubuntu-autoconfig: keep standard directory paths in English.\n'
    mapping += ''.join(f'XDG_{key}_DIR="$HOME/{name}"\n' for key, name in DIRECTORIES.items())
    write_file(config / 'user-dirs.dirs', mapping)
    write_file(config / 'user-dirs.locale', 'zh_CN\n')
    write_file(config / 'user-dirs.conf', 'enabled=False\nfilename_encoding=UTF-8\n')
    write_file(config / 'autostart/user-dirs-update-gtk.desktop',
               '[Desktop Entry]\nType=Application\nName=Keep English user directory names\nHidden=true\n')


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('Usage: desktop_locale.py USER_HOME')
    configure_user_files(sys.argv[1])
