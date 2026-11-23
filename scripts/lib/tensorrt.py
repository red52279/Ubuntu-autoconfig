"""TensorRT packages and NVIDIA's Ubuntu/CUDA release mapping."""
import re
import sys
from pathlib import Path

PACKAGES = ('libnvinfer-dev', 'libnvinfer-plugin-dev', 'libnvonnxparsers-dev',
            'libnvinfer-bin', 'python3-libnvinfer')
SOURCE = '/etc/apt/sources.list.d/ubuntu-autoconfig-tensorrt.list'
KEY_PREFIX = '/usr/share/keyrings/ubuntu-autoconfig-tensorrt'
PREFERENCES = '/etc/apt/preferences.d/ubuntu-autoconfig-tensorrt'
HOST = 'developer.download.nvidia.cn'
RELEASES = {'focal': '2004', 'jammy': '2204', 'noble': '2404', 'resolute': '2604'}
NVIDIA_FEATURES = ('gpu_driver', 'cuda', 'tensorrt')
JETPACK_REASON = 'Jetson 使用与板卡匹配的 NVIDIA JetPack'


def signing_fingerprint(suite):
    if suite == 'resolute':
        return '14BAFBC7562AD710CA04E69905FBB6DA60DF8A40'
    return 'EB693B3035CD5710E231E123A4B469963BF863CC'


def keyring(suite):
    return f'{KEY_PREFIX}-{signing_fingerprint(suite)[-8:].lower()}.gpg'


def is_jetson():
    if Path('/etc/nv_tegra_release').is_file():
        return True
    try:
        return b'nvidia,tegra' in Path('/proc/device-tree/compatible').read_bytes()
    except OSError:
        return False


def repository(suite, arch='amd64'):
    platform = {'amd64': 'x86_64', 'arm64': 'sbsa'}[arch]
    return f'https://{HOST}/compute/cuda/repos/ubuntu{RELEASES[suite]}/{platform}'


def source_line(suite, arch='amd64'):
    return f'deb [arch={arch} signed-by={keyring(suite)}] {repository(suite, arch)}/ /'


def is_package(name):
    name = name.partition(':')[0]
    return name in ('tensorrt', 'tensorrt-dev', 'tensorrt-libs') or name.startswith(
        ('libnvinfer', 'libnvonnxparsers', 'python3-libnvinfer'))


def runtime_packages(version):
    cuda = re.search(r'\+cuda(\d+)\.(\d+)$', version)
    if not cuda:
        return ()
    variant = f'{cuda[1]}-{cuda[2]}'
    return f'cuda-cudart-{variant}', f'libcublas-{variant}'


if __name__ == '__main__':
    mode = sys.argv[1]
    if mode == 'repository':
        suite, arch = sys.argv[2:]
        print(repository(suite, arch), source_line(suite, arch), SOURCE, keyring(suite), signing_fingerprint(suite),
              PREFERENCES, HOST, sep='\n')
    elif mode == 'packages':
        version = sys.argv[2]
        runtime = runtime_packages(version)
        if not runtime:
            raise SystemExit('当前索引没有带 CUDA 版本的 NVIDIA TensorRT 安装包。')
        print(*(f'{name}={version}' for name in PACKAGES), *runtime, sep='\n')
    elif mode == 'jetson':
        raise SystemExit(not is_jetson())
