#!/usr/bin/env python3
"""Single feature catalog: labels, package probes and supported platforms."""
import argparse
import json

ALL = ('amd64', 'arm64', 'armhf')
LTS = ('focal', 'jammy', 'noble', 'resolute')
ROS = {'focal': 'noetic', 'jammy': 'humble', 'noble': 'jazzy', 'resolute': 'lyrical'}
LOCAL_DEB_PACKAGES = {'edge': 'microsoft-edge-stable', 'vscode': 'code', 'v2rayn': 'v2rayn'}


def feature(label, packages=(), arches=ALL, suites=(), clean=(), reason='', kind='packages'):
    return dict(label=label, packages=list(packages), arches=list(arches), suites=list(suites),
                clean=list(clean), reason=reason, kind=kind)


FEATURES = {
    'base_tools': feature('基础命令行工具', ('ca-certificates','curl','wget','gnupg','git','jq','unzip','zip','vim','tree','rsync','python3','python3-pip','python3-venv','build-essential','pkg-config')),
    'dev_tools': feature('CMake / OpenCV / Eigen 开发库', ('cmake','ninja-build','gcc','g++','make','libssl-dev','libeigen3-dev','libopencv-dev','libzbar-dev','libaravis-dev')),
    'desktop_tools': feature('Qt6 / Qt Creator', ('qt6-base-dev','qt6-tools-dev-tools','qtcreator'), suites=('jammy','noble','resolute'), reason='该发行版的 Ubuntu 源不提供本模块需要的 Qt6'),
    'gedit': feature('gedit 文本编辑器', ('gedit',)),
    'clion': feature('JetBrains CLion（非商业免费）', arches=('amd64','arm64'), kind='clion'),
    'media_tools': feature('压缩与归档工具', ('zip','unzip','unrar','tree')),
    'virtualization_tools': feature('Wine Windows 兼容环境', ('wine',), arches=('amd64',), clean=('wine',), reason='本脚本的 Wine 流程仅适用于 amd64，不提供 ARM 上的 x86 转译'),
    'language_input': feature('系统中文输入法与中文语言', kind='input'),
    'time_sync': feature('Windows 双系统时间同步', ('chrony',), kind='time'),
    'storage_tools': feature('NTFS / exFAT 磁盘工具', ('ntfs-3g','exfatprogs','udisks2'), kind='storage'),
    'gpu_driver': feature('Ubuntu 推荐显卡驱动', ('ubuntu-drivers-common',), arches=('amd64',), reason='ARM 显卡驱动依赖具体板卡/厂商，本模块不安装通用 x86 驱动', kind='gpu'),
    'cuda': feature('Ubuntu CUDA 工具链', ('nvidia-cuda-toolkit',), arches=('amd64',), reason='ARM CUDA 需使用板卡/厂商 SDK，本模块不代替 JetPack 等专用方案'),
    'ros': feature('匹配系统的 ROS 桌面环境', arches=('amd64','arm64'), suites=LTS, reason='ROS 桌面模块仅支持对应 LTS 的 amd64/arm64 二进制包', kind='ros'),
    'rosdep': feature('ROS rosdep 与国内索引', ('python3-rosdep',), arches=('amd64','arm64'), suites=LTS, reason='当前 ROS 源不支持该系统/架构', kind='rosdep'),
    'v2rayn': feature('v2rayN 官方版', arches=('amd64','arm64'), reason='无该架构的已验证构建', kind='v2rayn'),
    'chrome': feature('Google Chrome 官方稳定版', ('google-chrome-stable',), arches=('amd64','arm64'),
                      clean=('google-chrome-stable',), reason='Google Chrome 稳定版提供 amd64/arm64 deb', kind='chrome'),
    'steam': feature('Steam 安装器', ('steam-installer',), arches=('amd64',), clean=('steam-installer',), reason='Steam 安装器及其 i386 依赖仅适用于 amd64'),
    'edge': feature('Microsoft Edge 官方版', ('microsoft-edge-stable',), arches=('amd64',), clean=('microsoft-edge-stable',), reason='当前实现不提供 ARM 的 Edge 包', kind='repository'),
    'vscode': feature('VS Code 官方版', ('code',), kind='repository', clean=('code',)),
    'qq': feature('腾讯 QQ 官方包', ('linuxqq',), arches=('amd64','arm64'), clean=('linuxqq',), reason='腾讯官网仅提供本模块使用的 amd64/arm64 构建', kind='deb'),
    'wechat': feature('微信官方包', ('wechat',), arches=('amd64',), clean=('wechat',), reason='当前固定官方下载入口仅验证了 amd64', kind='deb'),
    'typora': feature('Typora 中国仓库', ('typora',), arches=('amd64','arm64'), clean=('typora',), reason='官方仓库无本模块支持的 armhf 包', kind='repository'),
    'anaconda': feature('Anaconda 国内镜像', arches=('amd64','arm64'), reason='Anaconda 不提供 armhf 构建', kind='anaconda'),
    'spark_store': feature('星火应用商店', ('spark-store',), arches=('amd64','arm64'), clean=('spark-store',), reason='没有该架构的星火商店 deb', kind='deb'),
    'hikcamera': feature('海康机器人 MVS 工业相机客户端', arches=('amd64','arm64'), reason='MVS 桌面客户端安装流程支持 amd64/arm64', kind='hikcamera'),
    'system_upgrade': feature('显式执行系统升级', kind='maintenance'),
}


def support(name, arch, suite, input_method='auto'):
    item = FEATURES[name]
    if arch not in item['arches']:
        return False, item['reason'] or '不支持当前架构'
    if item['suites'] and suite not in item['suites']:
        return False, item['reason'] or '不支持当前 Ubuntu 版本'
    if name == 'language_input' and input_method == 'sogou' and arch == 'armhf':
        return False, '搜狗无 armhf 构建，请使用 auto/ibus 系统输入法'
    return True, ''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('format', choices=['shell', 'json', 'support'])
    parser.add_argument('--feature', choices=list(FEATURES))
    parser.add_argument('--arch', default='amd64')
    parser.add_argument('--suite', default='resolute')
    parser.add_argument('--input-method', default='auto')
    args = parser.parse_args()
    if args.format == 'json': print(json.dumps(FEATURES, ensure_ascii=False, indent=2))
    elif args.format == 'shell':
        for key, item in FEATURES.items(): print(key + '\t' + item['label'])
    else:
        ok, reason = support(args.feature, args.arch, args.suite, args.input_method)
        print(('supported' if ok else 'disabled') + '\t' + reason)


if __name__ == '__main__': main()
