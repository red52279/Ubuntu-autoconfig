#!/usr/bin/env python3
"""Single feature catalog: labels, package probes and supported platforms."""
import argparse
import json

import tensorrt

ALL = ('amd64', 'arm64', 'armhf')
LTS = ('focal', 'jammy', 'noble', 'resolute')
ROS = {'focal': 'noetic', 'jammy': 'humble', 'noble': 'jazzy', 'resolute': 'lyrical'}
LOCAL_DEB_PACKAGES = {'edge': 'microsoft-edge-stable', 'vscode': 'code', 'v2rayn': 'v2rayn'}
QT5_SUITES = ('focal', 'groovy', 'hirsute', 'impish')


def qt_base_packages(suite):
    if suite in QT5_SUITES:
        return 'qtbase5-dev', 'qttools5-dev-tools', 'qtcreator'
    return 'qt6-base-dev', 'qt6-tools-dev-tools', 'qtcreator'


def storage_packages(suite):
    exfat = ('exfat-utils', 'exfat-fuse') if suite in QT5_SUITES else ('exfatprogs',)
    return ('ntfs-3g', *exfat, 'udisks2')


def spark_package_available(name, arch, suite):
    return (arch in ('amd64', 'arm64') and (name != 'edge' or arch == 'amd64')
            and (name != 'v2rayn' or suite in ('noble', 'resolute')))


def feature(label, packages=(), arches=ALL, suites=(), clean=(), reason='', kind='packages'):
    return dict(label=label, packages=list(packages), arches=list(arches), suites=list(suites),
                clean=list(clean), reason=reason, kind=kind)


FEATURES = {
    'base_tools': feature('基础命令行工具', ('ca-certificates','curl','wget','gnupg','git','jq','unzip','zip','vim','tree','rsync','python3','python3-pip','python3-venv','build-essential','pkg-config')),
    'dev_tools': feature('CMake / OpenCV / Eigen 开发库', ('cmake','ninja-build','gcc','g++','make','libssl-dev','libeigen3-dev','libopencv-dev','libzbar-dev','libaravis-dev')),
    'desktop_tools': feature('Qt 开发扩展 / Qt Creator', qt_base_packages('resolute')),
    'gedit': feature('gedit 文本编辑器', ('gedit',)),
    'clion': feature('JetBrains CLion（非商业免费）', arches=('amd64','arm64'), kind='clion'),
    'media_tools': feature('压缩与归档工具', ('zip','unzip','unrar','tree')),
    'virtualization_tools': feature('Wine Windows 兼容环境', ('wine',), clean=('wine',)),
    'language_input': feature('系统中文输入法与中文语言', kind='input'),
    'time_sync': feature('Windows 双系统时间同步', ('chrony',), kind='time'),
    'storage_tools': feature('NTFS / exFAT 磁盘工具', storage_packages('resolute'), kind='storage'),
    'gpu_driver': feature('Ubuntu 推荐显卡驱动', ('ubuntu-drivers-common',), kind='gpu'),
    'cuda': feature('CUDA 工具链', ('nvidia-cuda-toolkit',), arches=('amd64','arm64'),
                    reason='签名源提供 amd64 和 arm64 CUDA 软件包'),
    'tensorrt': feature('NVIDIA TensorRT', tensorrt.PACKAGES, arches=('amd64','arm64'), suites=tuple(tensorrt.RELEASES),
                        reason='中国签名仓库提供 Ubuntu LTS 的 amd64 和 arm64 SBSA 构建', kind='tensorrt'),
    'ros': feature('匹配系统的 ROS 桌面环境', suites=LTS, reason='ROS 桌面模块只使用与 Ubuntu LTS 对应的签名二进制包', kind='ros'),
    'rosdep': feature('ROS rosdep 与国内索引', ('python3-rosdep',), suites=LTS, reason='国内 rosdep 配置支持对应的 Ubuntu LTS', kind='rosdep'),
    'v2rayn': feature('v2rayN 官方版', arches=('amd64','arm64'), reason='无该架构的已验证构建', kind='v2rayn'),
    'chrome': feature('Google Chrome 官方稳定版', ('google-chrome-stable',), arches=('amd64','arm64'),
                      clean=('google-chrome-stable',), reason='Google Chrome 稳定版提供 amd64/arm64 deb', kind='chrome'),
    'steam': feature('Steam 安装器', ('steam-installer',), arches=('amd64',), clean=('steam-installer',), reason='Steam 安装器及其 i386 依赖仅适用于 amd64'),
    'edge': feature('Microsoft Edge 官方版', ('microsoft-edge-stable',), arches=('amd64',), clean=('microsoft-edge-stable',), reason='当前实现不提供 ARM 的 Edge 包', kind='repository'),
    'vscode': feature('VS Code 官方版', ('code',), kind='repository', clean=('code',)),
    'qq': feature('腾讯 QQ 官方包', ('linuxqq',), arches=('amd64','arm64'), clean=('linuxqq',), reason='腾讯官网仅提供本模块使用的 amd64/arm64 构建', kind='deb'),
    'wechat': feature('微信官方包', ('wechat',), arches=('amd64','arm64'), clean=('wechat',), reason='腾讯提供 amd64 和 arm64 的 Linux 安装包', kind='deb'),
    'typora': feature('Typora 中国仓库', ('typora',), arches=('amd64','arm64'), clean=('typora',), reason='官方仓库无本模块支持的 armhf 包', kind='repository'),
    'anaconda': feature('Anaconda 国内镜像', arches=('amd64','arm64'), reason='Anaconda 不提供 armhf 构建', kind='anaconda'),
    'spark_store': feature('星火应用商店', ('spark-store',), arches=('amd64','arm64'), clean=('spark-store',), reason='没有该架构的星火商店 deb', kind='deb'),
    'hikcamera': feature('海康机器人 MVS 工业相机客户端', arches=('amd64','arm64'), reason='MVS 桌面客户端安装流程支持 amd64/arm64', kind='hikcamera'),
    'system_upgrade': feature('显式执行系统升级', kind='maintenance'),
}


def feature_packages(name, arch, suite):
    if name == 'desktop_tools':
        return qt_base_packages(suite)
    if name == 'storage_tools':
        return storage_packages(suite)
    if name == 'virtualization_tools':
        return 'wine', 'wine32' if arch == 'armhf' else 'wine64'
    if name == 'ros' and suite in ROS:
        return (f'ros-{ROS[suite]}-desktop',)
    if name == 'cuda' and arch == 'arm64' and suite == 'focal':
        return ('cuda-toolkit',)
    return FEATURES[name]['packages']


def support(name, arch, suite, input_method='auto'):
    item = FEATURES[name]
    if arch not in item['arches']:
        return False, item['reason'] or '不支持当前架构'
    if item['suites'] and suite not in item['suites']:
        return False, item['reason'] or '不支持当前 Ubuntu 版本'
    if name == 'ros' and arch == 'armhf' and suite != 'focal':
        return False, '该版 ROS 2 的国内源未发布 armhf 桌面包'
    if name == 'language_input' and input_method == 'sogou' and arch == 'armhf':
        return False, '搜狗无 armhf 构建，请使用 auto/ibus 系统输入法'
    return True, ''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('format', choices=['shell', 'json', 'support', 'packages'])
    parser.add_argument('--feature', choices=list(FEATURES))
    parser.add_argument('--arch', default='amd64')
    parser.add_argument('--suite', default='resolute')
    parser.add_argument('--input-method', default='auto')
    args = parser.parse_args()
    if args.format == 'json': print(json.dumps(FEATURES, ensure_ascii=False, indent=2))
    elif args.format == 'shell':
        for key, item in FEATURES.items(): print(key + '\t' + item['label'])
    elif args.format == 'packages':
        print(*feature_packages(args.feature, args.arch, args.suite), sep='\n')
    else:
        ok, reason = support(args.feature, args.arch, args.suite, args.input_method)
        print(('supported' if ok else 'disabled') + '\t' + reason)


if __name__ == '__main__': main()
