# Ubuntu 国内源自动配置

面向 Ubuntu 20.04–26.04，自动识别 amd64、arm64 和 armhf。安装器在操作前检查本机软件和配置，按系统版本与架构显示可用功能，安装后复查结果。默认配置启用基础工具、gedit、中文语言与输入法、双系统校时、NTFS 和 exFAT 支持。

## 开始使用

```bash
./install.sh --check                              # 只读检查本机
./install.sh --dry-run --cli --config configs/all.json
sudo ./install.sh                               # 在终端菜单中选择
sudo ./install.sh --cli                         # 使用 config.json
sudo ./install.sh --cli --only chrome,gedit      # 只处理指定项目
sudo ./install.sh --background                  # 选择后交给 systemd 后台运行
```

`configs/all.json` 选择全部功能，包含显式系统升级。`configs/repair.json` 是一组常用修复选项。可用 `--config` 指定配置文件，再用 `--only` 指定想单独处理的功能。用 `--list` 查看当前平台的可用项和禁用原因，用 `--check --arch arm64` 或 `--dry-run --arch armhf` 预演其他架构。

## 功能清单

| 功能 | `--only` 取值 | 内容 |
|---|---|---|
| 基础工具、编辑、归档 | `base_tools`、`gedit`、`media_tools` | Git、curl、Python、Vim、编译工具、gedit、ZIP 和 RAR 工具 |
| 开发与图形界面 | `dev_tools`、`desktop_tools`、`clion` | CMake、Ninja、OpenCV、Eigen、ZBar、Aravis、Qt6、Qt Creator 和 JetBrains CLion。Qt6 按 22.04、24.04、26.04 仓库提供情况安装 |
| 中文环境 | `language_input` | 简体中文界面、英文标准目录。登录、虚拟终端和 GNOME 新窗口默认美式英语，可切换中文 |
| 双系统与磁盘 | `time_sync`、`storage_tools` | Chrony 国内校时和 Windows 本地硬件时钟；NTFS、exFAT、UDisks 工具，外置盘按桌面文件管理器流程挂载 |
| NVIDIA 与 CUDA | `gpu_driver`、`cuda` | Ubuntu 推荐驱动和仓库中的 CUDA 工具链。适用于 amd64，驱动按硬件检测 |
| ROS | `ros`、`rosdep` | 对应 Ubuntu LTS 的 ROS 桌面和国内依赖索引。支持 amd64、arm64 |
| 浏览器和编辑器 | `chrome`、`edge`、`vscode` | Google Chrome 稳定版、Microsoft Edge、VS Code |
| 通讯与文档 | `qq`、`wechat`、`typora` | 腾讯 QQ、微信、Typora |
| 其他应用 | `v2rayn`、`anaconda`、`spark_store`、`hikcamera` | v2rayN、Anaconda、星火应用商店、海康机器人 MVS 客户端和 SDK |
| Windows 兼容与游戏 | `virtualization_tools`、`steam` | Wine、Steam 安装器及其 i386 依赖，两项均适用于 amd64 |
| 系统维护 | `system_upgrade` | 显式执行已安装软件的 APT 升级 |

ROS 映射为 Ubuntu 20.04 → Noetic、22.04 → Humble、24.04 → Jazzy、26.04 → Lyrical。Chrome、QQ、Typora、Anaconda、星火商店和 MVS 支持 amd64、arm64，Edge 和微信的安装流程支持 amd64。v2rayN 可复用匹配架构的已有程序。Ubuntu 24.04、26.04 的 Edge、VS Code 和 v2rayN 可从山东大学星火镜像补装，其他适用版本可提供本地包。MVS 配置 USB 相机访问，PCIe 采集卡驱动按具体板卡配置。

中文输入法默认优先 IBus 的 libpinyin、pinyin 或 Rime，确认系统引擎不可用时尝试搜狗。用 `--input-method ibus` 或 `--input-method sogou` 可指定后端。IBus 用 **Super+空格** 或 **Ctrl+空格** 切换，搜狗可用 **Ctrl+空格**。应用级设置在注销重登后生效。搜狗/Fcitx4 的原生 Wayland 和 GTK4 兼容性有限。

## 下载与修复

APT 和下载器直连国内镜像及厂商白名单。Ubuntu 镜像在清华、中科大、南京大学、阿里云间测速，ARM 使用国内 Ubuntu Ports。ROS 的 APT 源在清华、阿里云、中科大间回退，rosdep 索引使用清华。可用 `sudo ./install.sh --cli --only sources --reinstall` 重新测速换源。

Chrome、Edge、VS Code 和 v2rayN 使用山东大学星火镜像，校验索引签名和包的 SHA-256。可信的本地包可放入 `packages.local/`，例如 `google-chrome-stable_amd64.deb`。星火商店直连 Gitee，QQ 和微信走腾讯的官方下载。Typora 从中国 CDN 下载并核对官方签名索引，首次重新运行 Typora 项会迁移旧版安装器创建的 APT 源，之后用 `manage.sh check-updates` 和 `manage.sh update` 检查与更新。旧命令 `--only chromium` 仍可选中 Chrome。

普通运行会跳过已经就绪的功能。`--reinstall` 重检或修复现有配置。`--clean-reinstall` 需配合 `--only`，先验证替换包和卸载依赖，再重装已配置的独立应用，例如 Chrome 或 QQ。下载缓存支持断点续传。旧版安装器留下的固定磁盘挂载配置可用 `sudo ./install.sh --cli --only storage_tools` 清理。

CLion 使用 JetBrains 的统一安装包，个人学习、开源和其他非商业用途可在应用中选择免费授权。运行 `sudo ./install.sh --cli --only clion`，安装器优先复用 `packages.local/` 中匹配架构的原始归档。自动下载要求 JetBrains 官方入口将请求分配到中国 CDN，完成后用官方 SHA-256 校验。单独下载到 `packages.local/` 可运行 `python3 -B scripts/lib/archive_apps.py "$(dpkg --print-architecture)" --download-clion`。[JetBrains 中国站](https://www.jetbrains.com.cn/clion/)

本机已有 v2rayN 时，安装器复用现有程序及可用的原启动器。只需修复桌面入口时，可运行 `sudo /usr/bin/python3 -B scripts/lib/archive_apps.py "$(dpkg --print-architecture)" --configure-v2rayn`。

## 版本更新与回退

```bash
sudo ./manage.sh check-updates
sudo ./manage.sh check-updates --only clion,v2rayn,gedit
sudo ./manage.sh update --only gedit,v2rayn
sudo ./manage.sh rollback-version --dry-run
sudo ./manage.sh rollback-version --yes
sudo ./manage.sh rollback-version --run 运行ID --yes
```

检查会用国内白名单源刷新 APT 索引并比较已安装功能的版本，系统原有源配置保留。通过 deb 安装的独立应用按现有国内下载渠道或本地包检查，部分厂商需要读取下载包才提供版本信息，结果可复用下载缓存。`update` 在应用前也会检查版本。全量更新覆盖本机已安装的功能，整机软件包升级可显式指定 `--only system_upgrade`。CLion 从 JetBrains 中国站检测版本，并按检查到的版本下载或复用安装包。

每次实际更新都会记录版本和涉及的配置。APT 旧包能从缓存或签名镜像取得时会保存，CLion、MVS 和脚本管理的 Anaconda 会记录旧安装目录。回退按整次更新记录执行，先检查现有版本和依赖，确认后使用 `--yes`。旧版本若已撤下且无备份则无法回退。其他渠道安装且无法识别版本的程序会显示检查原因。

## 进度、重试与回滚

```bash
./manage.sh status
./manage.sh logs
./manage.sh summary --json
sudo ./manage.sh retry --background
sudo ./manage.sh stop
sudo ./manage.sh cleanup
sudo ./uninstall.sh --dry-run
sudo ./uninstall.sh --yes
```

安装总结记录每项状态和失败后的重试命令。正式运行的日志和总结位于 `/var/log/ubuntu-autoconfig/`，状态、源备份和回滚记录位于 `/var/lib/ubuntu-autoconfig/`。`uninstall.sh` 按普通安装记录清理当次新增的软件并恢复配置，更新版本的降级使用 `rollback-version`。

预演写入项目的 `runs/`，设置 `XDG_STATE_HOME` 时使用其中的 `ubuntu-autoconfig/runs/`。正式安装和回滚使用 `/var/lib/ubuntu-autoconfig/runs/`。每次运行创建独立的临时目录，退出时清理。断点下载保存在系统的 `runs/cache/`，可用 `sudo ./manage.sh cleanup` 清理。系统包管理器的缓存和需要原子替换的文件使用其目标文件系统。

## 仓库结构与测试

| 路径 | 用途 |
|---|---|
| `install.sh`、`manage.sh`、`uninstall.sh` | 安装、管理和回滚入口 |
| `config.json`、`configs/` | 默认、全功能和修复配置 |
| `scripts/modules/`、`scripts/lib/` | 功能模块、检测、下载、状态和回滚 |
| `tools/check.py` | 使用相同检测逻辑输出只读状态 |
| `tests/unit/`、`tests/integration/` | 离线回归测试和可手动复现的实机测试 |
| `runs/` | 预演输出及单元测试临时工作区，已加入 Git 忽略规则 |

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests/unit -v
python3 tools/check.py --format json
```

输入法集成测试在对应的 IBus 或搜狗会话运行。真实安装与回滚测试可在可还原的测试机运行 `sudo python3 -B tests/integration/verify_rollback_live.py`。
