#!/usr/bin/env python3
"""Read-only installation inventory; state checkpoints are never proof of readiness."""
import argparse
import ast
import configparser
import json
import os
from pathlib import Path
import pwd
import subprocess
from catalog import FEATURES, LOCAL_DEB_PACKAGES, ROS, support
from archive_apps import CLION_PATH, clion_version, v2rayn_desktop_ready
from qt_packages import available_qt_packages
from vendor_release import managed_typora_source
from input_config import (ENGLISH_IBUS_ENGINE, ENGLISH_LAYOUT, FCITX_ENGLISH, FCITX_PROGRAM,
                          GNOME_SOURCES, LOGIN_COMMAND, LOGIN_TARGET, LOGIN_UNIT,
                          keyboard_layouts, read_ini)
from network import check_url
from storage import legacy_storage_present

ROOT = Path(__file__).resolve().parents[2]


def output(args, timeout=10):
    try:
        result = subprocess.run(args, text=True, capture_output=True, timeout=timeout,
                                env={**os.environ, 'LC_ALL': 'C'})
        return result.stdout.strip() if result.returncode == 0 else ''
    except (OSError, subprocess.TimeoutExpired): return ''


def read(path):
    try: return Path(path).read_text()
    except OSError: return ''


def binary_arch(path):
    try:
        with Path(path).open('rb') as stream: header=stream.read(20)
    except OSError: return None
    if len(header)!=20 or not header.startswith(b'\x7fELF'): return None
    order='little' if header[5]==1 else 'big'
    return {62:'amd64',183:'arm64',40:'armhf'}.get(int.from_bytes(header[18:20],order))


def sources_ready(arch, suite):
    import re
    expected = {suite + suffix for suffix in ('','-updates','-security','-backports')}
    archive = 'ubuntu' if arch == 'amd64' else 'ubuntu-ports'
    found = set()
    for line in read('/etc/apt/sources.list').splitlines():
        match = re.match(r'^deb\s+(?:\[([^]]+)\]\s+)?(https://\S+)\s+(\S+)\s+(.+)$',line)
        if not match: continue
        options,url,codename,components = match.groups()
        try: check_url(url)
        except ValueError: return False
        architecture = re.search(r'\barch=([^ ]+)',options or '')
        if architecture and arch not in architecture[1].split(','): continue
        if not url.rstrip('/').endswith('/'+archive): continue
        if {'main','universe'}.issubset(components.split()): found.add(codename)
    if not expected.issubset(found): return False
    for path in [*Path('/etc/apt/sources.list.d').glob('*.list'),*Path('/etc/apt/sources.list.d').glob('*.sources')]:
        for line in read(path).splitlines():
            if line.lstrip().startswith('#'): continue
            for url in re.findall(r'\b[a-zA-Z][a-zA-Z0-9+.-]*://[^\s]+',line):
                try: check_url(url)
                except ValueError: return False
    return True


class Inventory:
    def __init__(self, arch=None, suite=None, user=None, input_method='auto', packages=None):
        self.host_arch = output(['dpkg', '--print-architecture'])
        self.arch = arch or self.host_arch
        self.suite = suite or dict(line.split('=',1) for line in read('/etc/os-release').splitlines() if '=' in line).get('VERSION_CODENAME','').strip('"')
        self.user = user or os.environ.get('SUDO_USER') or pwd.getpwuid(int(os.environ.get('PKEXEC_UID',os.getuid()))).pw_name
        self.home = Path(pwd.getpwnam(self.user).pw_dir)
        self.method = input_method
        if packages is None:
            rows = output(['dpkg-query','-W','-f=${binary:Package}\t${Version}\t${db:Status-Status}\n']).splitlines()
            self.packages = {row.split('\t')[0]: row.split('\t')[1] for row in rows if row.endswith('\tinstalled')}
        else: self.packages = packages

    def installed(self, package):
        return package in self.packages or package + ':' + self.arch in self.packages

    def user_command(self, *args):
        uid = pwd.getpwnam(self.user).pw_uid
        prefix = [] if os.getuid() == uid else ['sudo','-H','-u',self.user]
        return output([*prefix,'env',f'XDG_RUNTIME_DIR=/run/user/{uid}',f'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus',*args])

    def result(self, name, status, reason, packages=None):
        return dict(feature=name, label=FEATURES[name]['label'], status=status, reason=reason,
                    packages=packages if packages is not None else FEATURES[name]['packages'],
                    clean_reinstall=bool(FEATURES[name]['clean']))

    def vendor_deb_status(self, name):
        filename = f'{LOCAL_DEB_PACKAGES[name]}_{self.arch}.deb'
        relative = Path('packages.local') / filename
        if (ROOT / relative).is_file():
            return self.result(name, 'missing', f'将安装本地包 {relative}')
        if self.arch in ('amd64', 'arm64') and self.suite in ('noble', 'resolute'):
            return self.result(name, 'missing', '将从山大星火签名镜像下载')
        return self.result(name, 'disabled', f'当前无已验证的国内下载源，可提供 {relative}')

    def inspect(self, name):
        spec = FEATURES[name]
        supported, why = support(name,self.arch,self.suite,self.method)
        if not supported: return self.result(name,'disabled',why)
        if self.arch != self.host_arch:
            return self.result(name,'unknown','仅验证目标平台支持范围；安装包和配置需在目标机器检测')
        if name == 'anaconda' and not Path('/opt/anaconda3/bin/python').exists():
            return self.result(name,'missing','尚未安装 Anaconda')
        if name == 'clion' and not clion_version(CLION_PATH):
            return self.result(name, 'missing', '使用本地安装包或 JetBrains 官方中国 CDN')
        if name == 'v2rayn' and binary_arch('/opt/v2rayN/v2rayN') != self.arch:
            if os.environ.get('V2RAYN_DOMESTIC_URLS'):
                return self.result(name, 'missing', '将使用指定的国内镜像')
            return self.vendor_deb_status(name)
        if name == 'hikcamera' and binary_arch('/opt/MVS/bin/MVS') != self.arch:
            return self.result(name,'missing','缺少匹配架构的 MVS 客户端')
        if name == 'desktop_tools':
            try:
                packages = available_qt_packages(self.arch)
            except (OSError, subprocess.SubprocessError):
                return self.result(name, 'unknown', '刷新 APT 索引后检查 Qt6 开发包')
            if 'qt6-base-dev' not in packages:
                return self.result(name, 'unknown', '刷新 APT 索引后检查 Qt6 开发包')
            missing = [package for package in packages if not self.installed(package)]
            if missing:
                return self.result(name, 'missing', f'缺少 {len(missing)} 个 Qt6 开发环境软件包，例如 ' + ', '.join(missing[:5]), packages)
            return self.result(name, 'ready', 'Qt6 开发扩展和工具已就绪', packages)
        packages = list(spec['packages'])
        if name == 'ros': packages = ['ros-' + ROS[self.suite] + '-desktop']
        if name == 'ros' and self.suite == 'focal' and self.installed('ros-noetic-desktop-full'):
            packages = ['ros-noetic-desktop-full']
        if name == 'language_input':
            provider = next((p for p in ('ibus-libpinyin','ibus-pinyin','ibus-rime') if self.installed(p)), 'ibus-libpinyin')
            fallback = self.method == 'auto' and read('/etc/ubuntu-autoconfig/input-fallback').strip() == 'native-unavailable'
            self.effective_input = 'sogou' if self.method == 'sogou' or fallback else 'ibus'
            packages = ['sogoupinyin','fcitx','language-pack-gnome-zh-hans'] if self.effective_input == 'sogou' else ['ibus',provider,'language-pack-gnome-zh-hans']
        missing = [p for p in packages if not self.installed(p)]
        if missing:
            if name in ('edge', 'vscode'):
                return self.vendor_deb_status(name)
            return self.result(name,'missing','缺少: ' + ', '.join(missing),packages)
        if name == 'typora' and managed_typora_source(read('/etc/apt/sources.list.d/typora.list')):
            return self.result(name,'configure','迁移旧版 Typora APT 源并保留签名校验',packages)
        if spec['kind'] in ('packages','deb','repository'):
            return self.result(name,'ready','所需软件包已安装',packages)
        ready, reason = self.configuration(name)
        return self.result(name,'ready' if ready else 'configure',reason,packages)

    def configuration(self, name):
        if name == 'clion':
            return (os.access(CLION_PATH / 'bin/clion.sh', os.X_OK)
                    and Path('/usr/share/applications/jetbrains-clion.desktop').is_file()), '检查 CLion 和桌面入口'
        if name == 'chrome':
            flags = {'repo_add_once', 'repo_reenable_on_distupgrade'}
            settings = [line.strip() for line in read('/etc/default/google-chrome').splitlines()
                        if line.partition('=')[0].strip() in flags]
            ready = len(settings) == len(flags) and set(settings) == {flag + '="false"' for flag in flags}
            return ready, 'Google Chrome 已安装' if ready else '需关闭 Chrome 自动添加国外 APT 源'
        if name == 'language_input':
            expected = getattr(self,'effective_input','sogou' if self.method == 'sogou' else 'ibus')
            actual = read('/etc/ubuntu-autoconfig/input-method').strip()
            if actual != expected: return False, '需要配置所选输入法: ' + expected
            xinput = read(self.home / '.xinputrc')
            framework = 'fcitx' if expected == 'sogou' else 'ibus'
            if 'run_im ' + framework not in xinput: return False, '用户输入框架未启用'
            if 'LANG=zh_CN.UTF-8' not in read('/etc/locale.conf'): return False, '全局中文尚未配置'
            uid = pwd.getpwnam(self.user).pw_uid
            language = output(['busctl','--system','get-property','org.freedesktop.Accounts',f'/org/freedesktop/Accounts/User{uid}','org.freedesktop.Accounts.User','Language'])
            if language and 'zh_CN' not in language: return False, '账户语言尚未切换为中文'
            mapping = read(self.home / '.config/user-dirs.dirs')
            if 'XDG_DESKTOP_DIR="$HOME/Desktop"' not in mapping: return False, '英文目录配置不完整'
            if keyboard_layouts(read('/etc/default/keyboard'))[:1] != [('us', '')]:
                return False, '系统和登录键盘尚未设为英语'
            raw = self.user_command('gsettings', 'get', GNOME_SOURCES, 'sources')
            try:
                sources = ast.literal_eval(raw.replace('@a(ss) ', '', 1))
            except (ValueError, SyntaxError):
                return False, '无法读取桌面输入源'
            if sources[:1] != [ENGLISH_LAYOUT] or self.user_command('gsettings', 'get', GNOME_SOURCES, 'per-window') != 'true':
                return False, '新窗口尚未默认使用英语'
            unit_dir = self.home / '.config/systemd/user'
            link = unit_dir / f'{LOGIN_TARGET}.wants' / LOGIN_UNIT
            if (not link.is_symlink() or os.readlink(link) != f'../{LOGIN_UNIT}'
                    or f'ExecStart={LOGIN_COMMAND}' not in read(unit_dir / LOGIN_UNIT)):
                return False, '登录时尚未配置英语输入'
            if expected == 'ibus':
                if not any(kind == 'ibus' for kind, _ in sources): return False, '桌面未配置中文输入源'
                raw = self.user_command('gsettings', 'get', 'org.freedesktop.ibus.general', 'preload-engines')
                try:
                    engines = ast.literal_eval(raw.replace('@as ', '', 1))
                except (ValueError, SyntaxError):
                    return False, '无法读取 IBus 输入引擎'
                if (engines[:1] != [ENGLISH_IBUS_ENGINE]
                        or self.user_command('gsettings', 'get', 'org.freedesktop.ibus.general', 'enable-by-default') != 'false'
                        or self.user_command('gsettings', 'get', 'org.freedesktop.ibus.general', 'use-global-engine') != 'true'):
                    return False, 'IBus 尚未默认使用英语'
            else:
                try:
                    profile = read_ini(self.home / '.config/fcitx/profile')
                    enabled = profile.get('Profile', 'EnabledIMList', fallback='').split(',')
                    config = read_ini(self.home / '.config/fcitx/config')
                except (OSError, configparser.Error):
                    return False, '无法读取 Fcitx 输入设置'
                if 'sogoupinyin:True' not in enabled: return False, '搜狗输入源未启用'
                if enabled[0] != FCITX_ENGLISH or any(config.get('Program', key, fallback='') != value
                                                     for key, value in FCITX_PROGRAM.items()):
                    return False, 'Fcitx 尚未默认使用英语'
                libs = output(['ldd','/opt/sogoupinyin/files/bin/sogoupinyin-service'])
                if not libs or '=> not found' in libs: return False, '搜狗运行库不完整'
            return True, '英语默认输入及中文切换已配置'
        if name == 'time_sync':
            ready = ('ntp.aliyun.com' in read('/etc/chrony/chrony.conf') and
                     output(['timedatectl','show','-p','LocalRTC','--value']) == 'yes' and
                     output(['timedatectl','show','-p','Timezone','--value']) == 'Asia/Shanghai' and
                     output(['systemctl','is-active','chrony']) == 'active')
            return ready, '时间同步已配置' if ready else '需补齐国内 NTP / 时区 / RTC 设置'
        if name == 'storage_tools':
            try:
                if legacy_storage_present():
                    return False, '需清理旧版安装器的固定磁盘挂载配置'
            except OSError:
                return False, '需检查旧版安装器的磁盘配置'
            return True, '通用 NTFS / exFAT 磁盘工具已安装'
        if name == 'ros': return Path('/etc/profile.d/ros.sh').exists(),'ROS 软件包已安装；检查登录配置'
        if name == 'rosdep':
            text = read('/etc/ros/rosdep/sources.list.d/20-default.list')
            profile = read('/etc/profile.d/rosdep.sh')
            tuna = 'https://mirrors.tuna.tsinghua.edu.cn/rosdistro/'
            return (tuna + 'rosdep/base.yaml' in text and tuna + 'index-v4.yaml' in profile
                    and (self.home / '.ros/rosdep/sources.cache/index').exists()),'检查 rosdep 国内源与用户缓存'
        if name == 'gpu_driver':
            if output(['nvidia-smi','--query-gpu=driver_version','--format=csv,noheader']): return True,'现有 NVIDIA 驱动正常'
            recommended = output(['ubuntu-drivers','devices'],timeout=20)
            if not recommended: return True,'未检测到需要本模块安装的专用驱动'
            return False,'需检查推荐显卡驱动'
        if name == 'v2rayn':
            binary = Path('/opt/v2rayN/v2rayN')
            return binary_arch(binary)==self.arch and os.access(binary,os.X_OK) and v2rayn_desktop_ready(),'检查已有程序架构及重复桌面入口'
        if name == 'hikcamera':
            return (os.access('/opt/MVS/bin/MVS', os.X_OK)
                    and os.access('/opt/MVS/bin/MVS.sh', os.X_OK)
                    and Path('/usr/share/applications/MVS.desktop').is_file()), '检查 MVS 程序及桌面入口'
        if name == 'anaconda':
            binary = Path('/opt/anaconda3/bin/python')
            if not binary.exists(): return False,'尚未安装 Anaconda'
            version = output([str(binary),'-I','-c','import conda,numpy,pandas; print(conda.__version__)'],timeout=20)
            if not version: return False,'已有目录不完整，需检查恢复'
            ready = ('mirrors.nju.edu.cn/anaconda/' in read('/etc/conda/.condarc') and
                     'bin:$PATH' not in read('/etc/profile.d/anaconda.sh'))
            return ready,'Anaconda 已安装；检查国内频道和系统 Python 共存配置'
        return False,'显式维护操作，每次选中时执行'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--arch',choices=['amd64','arm64','armhf'])
    parser.add_argument('--suite')
    parser.add_argument('--user')
    parser.add_argument('--input-method',choices=['auto','ibus','sogou'],default='auto')
    parser.add_argument('--feature',choices=list(FEATURES))
    parser.add_argument('--sources-ready',action='store_true')
    parser.add_argument('--output',type=Path)
    parser.add_argument('--format',choices=['json','shell','table'],default='table')
    args = parser.parse_args()
    inventory = Inventory(args.arch,args.suite,args.user,args.input_method)
    if args.sources_ready:
        raise SystemExit(0 if sources_ready(inventory.arch,inventory.suite) else 1)
    rows = [inventory.inspect(name) for name in ([args.feature] if args.feature else FEATURES)]
    if args.output:
        args.output.write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n')
    if args.format == 'json': print(json.dumps(rows,ensure_ascii=False,indent=2))
    elif args.format == 'shell':
        for row in rows: print('\t'.join(str(row[key]).replace('\t',' ').replace('\n',' ') for key in ('feature','status','reason')))
    else:
        print('功能                      检测结果      说明')
        labels = dict(ready='已就绪',missing='未安装',configure='需配置',disabled='已禁用',unknown='待目标机检测')
        for row in rows: print(f"{row['feature']:<25} {labels[row['status']]:<10} {row['reason']}")


if __name__ == '__main__': main()
