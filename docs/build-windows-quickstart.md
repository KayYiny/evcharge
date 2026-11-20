# 在 Windows 上构建并运行

本文记录在 Windows 上从源码运行、打包并分发本程序的过程。仓库自带一份示例数据备份
（`demo-data/示例数据备份.zip`，含 1489 条历史订单、6 个车位 `A001~A006` 与默认账号），
不需要联网生成数据。

## 0. 安装 Python

安装 **Python 3.12 或 3.13（64 位）**，安装时勾选 **Add Python to PATH**。
Python 3.15 这类预发布版本可能没有预编译的依赖轮子，能避开就避开。

```powershell
python --version
```

## 1. 解压并创建虚拟环境

把仓库解压到任意目录（路径不要带中文和空格更稳），在该目录打开 PowerShell：

```powershell
cd C:\path\to\evcharge

python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e .
```

## 2. 先运行源码版

```powershell
.\.venv\Scripts\python.exe -m evcharge
```

登录：`admin` / `admin`。
此时数据库是空的，程序数据与源码目录分开存放，导入示例数据见第 4 步。

## 3. 打包成 EXE

```powershell
# 单文件（体积小、启动稍慢）
.\scripts\build_windows.ps1

# 或者打成文件夹（启动更快）
.\scripts\build_windows.ps1 -OneDir
```

产物在 `dist\` 下：

- 单文件模式：`dist\充电监控.exe`
- OneDir 模式：`dist\充电监控\充电监控.exe`（整个文件夹一起拷贝）

首次运行会被 SmartScreen 提示「未知发布者」：点 **更多信息 → 仍要运行**。

## 4. 导入示例数据（可选）

打包后的程序使用自己的用户数据目录，**第一次启动是空库**。导入步骤：

1. 打开程序，登录 `admin` / `admin`；
2. 左侧「设置」→ 拉到最下方 **数据管理**；
3. 点 **导入备份…** → 选 `demo-data\示例数据备份.zip`；
4. 两次确认（覆盖前会自动把现有数据备份到用户数据目录的 `backups/`）；
5. 按提示**重启程序**。重启后订单页就有 1489 条历史订单，数据分析与 AI 报告都能用。

> 也可以不用备份：登录后自行「添加车位」（默认建议 `A001`），
> 或在源码目录执行
> `.\.venv\Scripts\python.exe scripts\gen_history.py --reset --reset-spots`
> 生成一份新的示例数据（生成的是本机 `data\charging.db`，与打包版的数据目录不同）。

## 5. 清理数据

设置页 → 数据管理 → **清空所有数据…**（两次确认）。
订单、车位、账号、设置与 API Key 会全部清空，管理员恢复成 `admin` / `admin`。

## 6. 常见问题

| 现象 | 处理 |
|---|---|
| `flet: 无法将"flet"识别为...` | 用 `.\scripts\build_windows.ps1`（脚本内部使用 `.venv-build` 里的完整路径），或 `.\.venv\Scripts\flet.exe pack ...` |
| 打包时提示缺依赖 | 删掉 `.venv-build` 后重跑脚本，它会重新安装 |
| EXE 启动闪退 | 在 PowerShell 里直接运行打包产物查看报错；日志在用户数据目录的 `evcharge.log` |
| 相册识别没反应 | Windows 走系统「相机」应用，拍照后点「相册识别」选图；或在设置页配置视觉模型 |
| 想换站点名或电价档 | 设置页修改，保存后对新订单生效 |
