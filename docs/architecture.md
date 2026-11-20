# 架构说明

## 1. 三层加一层底层

```
┌──────────────────────────────────────────────┐
│ ui/         ③ 交互与外观                      │
│   menu.py    菜单结构、弹出、勾选              │
│   theme.py   颜色、阈值、字号                  │
│   strings.py 文案                             │
├──────────────────────────────────────────────┤
│ present/    ② 呈现                            │
│   tray.py    托盘图标生命周期                  │
│   taskbar.py 任务栏内嵌窗口 + 分层绘制         │
│   icon.py    GDI 画图标                       │
├──────────────────────────────────────────────┤
│ source/     ① 找电量  ← 换厂商只改这里         │
│   base.py    Provider 接口 + 责任链            │
│   *.py       各厂商协议                        │
├──────────────────────────────────────────────┤
│ win32/hid.py   SetupAPI / hid.dll / 重叠 I/O   │
└──────────────────────────────────────────────┘
         core/  数据模型与配置（被所有层引用）
```

依赖方向严格单向：`app → ui / present / source → win32`，`core` 不依赖任何模块。

---

## 2. 线程模型

```
主线程                          工作线程（Poller）
  │                               │
  │  每 60s / 插拔 / 唤醒 ────────▶│ trigger()
  │                               │ 读一次电量
  │◀──── PostMessage(WM_APP_READY)│
  │  取结果 → 渲染                 │
```

两条硬约束：

1. **HID 读可能阻塞**：设备休眠时 `HidD_GetFeature` 会卡到驱动超时，主线程一卡托盘就失去响应。
2. **窗口有线程亲和性**：`SetWindowPos` / `InvalidateRect` 必须回到创建窗口的线程。

中间只传不可变的 `BatterySnapshot`，带 `seq` 单调递增，防止旧结果覆盖新结果。

---

## 3. 数据流

```
DeviceInfo（VID/PID/UsagePage）
   ↓  source.ProviderChain.read()
BatteryReading（电量、充电、原始字节）
   ↓  app._poll_once()
BatterySnapshot（+ 状态、时间戳、序号）
   ↓  app._apply()
HICON（托盘）  +  text/color（任务栏条）
```

界面永远渲染快照，不直接读设备。

---

## 4. 状态机

| 状态 | 含义 | 界面表现 |
|---|---|---|
| `OK` | 本次读到 | 正常亮度 |
| `ASLEEP` | 设备没应答，但有历史值 | 显示上次值 + 变暗 + tooltip 注明时间 |
| `STALE` | 缓存过期 | 显示上次值 + 灰色问号 |
| `UNAVAILABLE` | 从没读到过 | 显示 `--` |

**绝对不要把「读不到」当成 0%**，这是这类工具最常见的 bug。

---

## 5. 责任链

```python
class BatteryProvider(ABC):
    name: str
    priority: int
    def probe(self, device) -> bool          # 便宜：只看 VID/PID/UsagePage
    def read(self, device) -> BatteryReading | None
```

规则：

- 按 `priority` 升序尝试，第一个成功者胜出
- 连续失败 3 次 → 冷却 5 分钟，避免每轮都全量试探
- 记住上次成功的 Provider 与通道，下次优先试它

---

## 6. 任务栏窗口的生命周期

```
create()  CreateWindowExW(WS_POPUP)     ← 不能直接建 WS_CHILD（父窗口为 NULL 会失败）
attach()  SetParent(Shell_TrayWnd)
          SetWindowLongPtrW(WS_CHILD | WS_VISIBLE)
          SetWindowPos(托盘区左侧)
render()  CreateDIBSection → GDI 绘制 → 补 alpha → UpdateLayeredWindow
destroy() DestroyWindow + 释放 DIB/DC，句柄置 0
```

三个必须处理的外部事件：

| 事件 | 后果 | 处理 |
|---|---|---|
| Explorer 重启 | 窗口消失、托盘图标失效 | 监听 `TaskbarCreated`，销毁重建 + 重新 `NIM_ADD` |
| 主题 / DPI 变化 | 颜色、尺寸不对 | `WM_SETTINGCHANGE` / `WM_DPICHANGED` 重算 |
| 设备插拔 / 唤醒 | 数据不刷新 | `WM_DEVICECHANGE` / `WM_POWERBROADCAST` 触发一次读取 |

---

## 7. 扩展点

| 想做什么 | 改哪里 |
|---|---|
| 支持新厂商 | `source/` 新增一个 Provider，注册到 `source/__init__.py` |
| 换配色 / 阈值 | `ui/theme.py` |
| 改文案 / 做多语言 | `ui/strings.py` |
| 加菜单项 | `ui/menu.py` 定义结构，`app.py` 解释命令 id |
| 换显示形态（如浮窗） | `present/` 新增一个类，`app._apply_mode()` 里分支 |
