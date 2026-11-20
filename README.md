# Battery Tray · 2.4G 外设电量任务栏组件

在 Windows 任务栏上实时显示无线鼠标 / 键盘的电量，不依赖厂商软件常驻。

默认适配 **MCHOSE A7 V3 Ultra+**（`3837:1018`），但结构是按「换厂商只改一个文件」设计的：
找电量的逻辑集中在 `source/`，其余两层（呈现、交互）与厂商无关。

```
┌─────────────────────────────────────────────────────┐
│  ui/       ③ 交互与外观：菜单、主题、文案             │
├─────────────────────────────────────────────────────┤
│  present/  ② 呈现：托盘图标、任务栏内嵌电池条         │
├─────────────────────────────────────────────────────┤
│  source/   ① 找电量：HID 查询、帧解析  ← 换厂商改这里  │
└─────────────────────────────────────────────────────┘
```

## 特性

- **零第三方依赖**：只用 Python 标准库 + `ctypes`，不装 hidapi / pysrtay / Pillow
- **两种呈现**：托盘图标（数字，随时可见）+ 任务栏内嵌条（电池图形 + 百分比，逐像素透明）
- **即时生效**：显示方式、刷新间隔都能在右键菜单里改，不需要重启
- **休眠友好**：设备睡着时保留上次读数并变暗，不会显示 0%
- **只读**：只发查询命令，不写配置、不动固件

## 快速开始

要求 **Windows 10/11 + Python 3.10+**。

```bat
git clone <this-repo>
cd battery-tray\src
python -m battery_tray --taskbar
```

第一次跑建议带 `--debug`，控制台会打印每一帧的十六进制，方便确认协议：

```bat
python -m battery_tray --taskbar --debug
```

### 命令行参数

| 参数 | 作用 |
|---|---|
| `--once` | 读一次就退出，适合排查 |
| `--taskbar` | 启动时用任务栏内嵌模式（默认托盘） |
| `--debug` | 打印每一帧收发 |
| `--no-layered` | 任务栏窗口改用不透明绘制（分层失效时的退路） |
| `--interval N` | 覆盖刷新间隔（秒） |

### 开机自启

在启动文件夹放一个快捷方式：

```
目标    : <python>\pythonw.exe
参数    : -m battery_tray --taskbar
起始位置: <repo>\src
```

用 `pythonw.exe` 而不是 `python.exe`，否则每次开机弹黑框。

## 目录结构

```
src/battery_tray/
├─ app.py              组装：消息循环、定时器、单实例、线程交接
├─ core/               数据模型与配置
│  ├─ model.py         BatteryReading / BatterySnapshot / 设备状态机
│  └─ config.py        %APPDATA%\MchoseBattery\config.json
├─ source/             ★ ① 找电量
│  ├─ base.py          Provider 接口 + 责任链
│  ├─ mchose_a7v3.py   MCHOSE A7 V3（0x4D 输出报告）
│  ├─ standard_hid.py  标准 HID 电量字段（待实现）
│  ├─ hub_bridge.py    读官方软件（兜底，占位）
│  └─ _template.py     ★ 新厂商从这复制
├─ present/            ② 呈现
│  ├─ tray.py          通知区图标生命周期
│  ├─ taskbar.py       任务栏内嵌窗口 + 分层绘制
│  └─ icon.py          用 GDI 画托盘图标
├─ ui/                 ★ ③ 交互与外观
│  ├─ menu.py          菜单结构 + 弹出
│  ├─ theme.py         颜色、阈值、字号（换皮肤改这里）
│  └─ strings.py       文案（做多语言改这里）
└─ win32/hid.py        SetupAPI + hid.dll 封装
```

## 适配新厂商（三步）

详细版见 [`docs/adapt-new-vendor.md`](docs/adapt-new-vendor.md)。

**第 1 步：确认设备信息**

```bat
python tools\probe.py --list
```

记下 VID:PID、以及 Usage Page 落在 `0xFF00–0xFFFF` 的那个集合（通常在 MI=02 接口上）。

**第 2 步：抄官方驱动的帧**

有网页驱动的厂商，用 Chrome 打开配置页，`F12` 里 hook 一下就能看到明文帧：

```js
const o = HIDDevice.prototype.sendReport;
HIDDevice.prototype.sendReport = function (id, data) {
  console.log(id, [...new Uint8Array(data)].map(b=>b.toString(16).padStart(2,'0')).join(' '));
  return o.apply(this, arguments);
};
```

没有网页驱动，再用 USBPcap 抓桌面客户端。

**第 3 步：照着模板写 Provider**

```bat
copy src\battery_tray\source\_template.py src\battery_tray\source\myvendor.py
```

只改三处：

| 位置 | 改什么 |
|---|---|
| `VID_PIDS` | 你的接收器 VID:PID |
| `build_query()` / `QUERY` / `REPORT_ID` | 查询帧 |
| `parse_reply()` / `BATTERY_OFFSET` | 电量在第几个字节 |

最后把新类加进 `source/__init__.py` 的 `build_default_chain()`，跑 `python -m battery_tray --once` 验证。
`app.py` 不用改——它会枚举全部 HID 集合，让每个 Provider 用 `probe()` 自己认领设备。

## 已知协议速查

| 厂商 / 方案 | 传输 | 特征 | 电量位置 |
|---|---|---|---|
| Logitech HID++ 2.0 | Feature | 0x10 前缀 + 功能 ID 0x1000/0x1004 | 按功能返回结构 |
| Razer | Feature | 90 长度 + 命令类 + CRC | 应答特定字段 |
| Compx / RealTek（MCHOSE A7 V2、ATK） | Feature 0x11/0x12 | 载荷整体 `^0xFF`，cmd 0x06 | 还原后 payload[9] |
| Nordic / ATK 8K | Output 0x08 | `7D 72 02 00 01 07 01` | resp[7] |
| Airoha（耳机 dongle） | Output 0x06 / Input 0x07 | 属性 `0x0CD6` | 属性值 |
| **MCHOSE HUB 族（A7 V3）** | **Output 0x4D / Input 0x4D** | **`01 01` 头，明文不取反** | **payload[19]** |

## 常见问题

**`ModuleNotFoundError: No module named 'battery_tray'`**
`-m` 靠当前目录找包，必须先进 `src`，或者设 `PYTHONPATH=src`。

**托盘有图标但右键没反应**
可能是上一个进程留下的幽灵图标（进程已退出但没调 `NIM_DELETE`）。鼠标扫过它会消失，或重启 Explorer。

**任务栏那条是空的/看不见**
先加 `--no-layered` 对比。分层窗口在极少数环境会失败，程序会自动降级。

**读不到电量**
按 [`docs/adapt-new-vendor.md`](docs/adapt-new-vendor.md) 里的排查顺序，先用 `tools\probe.py --replay` 确认协议，再怀疑 UI。

## 许可

MIT。只做只读查询，不含任何厂商代码。
