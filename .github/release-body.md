## 国网高速服务区充电监控与计费系统

基于 **Flet** 的跨平台充电站监控与分时计费桌面应用。

### 下载

| 平台 | 文件 | 说明 |
|---|---|---|
| Windows 10 / 11（x64） | `EVCharge-v*-Windows-x64.exe` | 单文件程序，目标机器无需安装 Python |
| macOS（Apple Silicon） | `EVCharge-v*-macOS-arm64.zip` | 解压后把「充电监控.app」拖进「应用程序」 |
| macOS（Apple Silicon） | `EVCharge-v*-macOS-arm64.dmg` | 磁盘映像，打开后拖拽安装 |

> 当前 macOS 构建为 Apple Silicon（arm64）；如需 Intel（x64）版本，请提交 Issue。

### 首次运行

**Windows**：未签名程序，SmartScreen 提示时选「更多信息 → 仍要运行」。

**macOS**：未签名应用，首次打开前在终端执行：

```bash
xattr -cr "/Applications/充电监控.app"
```

若仍被拦截，到「系统设置 → 隐私与安全性」点「仍要打开」。

### 说明

- 程序数据（订单数据库、设置、日志）保存在用户可写数据目录，与程序本身分离；
- 本页产物由 GitHub Actions 在 Windows / macOS 真实环境自动构建，见 [Actions](https://github.com/KayYiny/evcharge/actions)。
