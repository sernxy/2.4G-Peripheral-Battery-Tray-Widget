# 适配一家新厂商

这份文档是「换一个品牌的鼠标」时唯一需要看的东西。
前提：`present/`、`ui/`、`win32/` 三层都不用动。

---

## 0. 先判断难度

| 情况 | 预计耗时 | 做法 |
|---|---|---|
| 已有开源实现支持该型号 | 0.5 天 | 抄偏移，直接写 Provider |
| 厂商有网页驱动（WebHID） | 1 天 | hook 出真实帧，回放验证 |
| 只有桌面客户端 | 2–3 天 | USBPcap 抓包，两次不同电量对比 |
| 私有加密握手 | 不建议 | 成本高于收益，改用「读官方软件」兜底 |

---

## 1. 确认设备身份

```bat
python tools\probe.py --list
```

要记下四件事：

1. **VID:PID**（接收器一个、有线一个，可能不同）
2. **MI 号**：通常厂商集合在 `MI=02`（第三个 USB 接口）
3. **Usage Page**：落在 `0xFF00–0xFFFF` 的那个就是厂商集合
4. **集合数量**：一台鼠标会枚举 4–5 个，别抓错

示例输出：

```
 3837:1018  0xff01  0x0001   2  MCHOSE A7 V3 Ultra+  ← 厂商集合
 3837:1018  0x0001  0x0002   0  MCHOSE A7 V3 Ultra+
```

---

## 2. 先找现成实现

按顺序试，任意一个能读出电量，协议就不用自己摸了：

| 工具 | 用法 |
|---|---|
| [HaloBattery](https://github.com/HeyOkay/HaloBattery) | Windows 直接跑，看 `providers/` |
| [BatteryHub](https://github.com/krisgfx9/Wireless-Device-Battery-Monitor) | `BatteryHub.exe --dump` 会打印所有 HID 集合与描述符 |
| [EZ2.4G](https://github.com/Ap0LoG1zE/EZ2.4G) | `python my_devices.py --list` |

---

## 3. 抄官方驱动的帧（最关键的一步）

### 有网页驱动（WebHID）

Chrome 打开配置页 → `F12` → Console，**在连接设备之前**粘贴：

```js
window.__log = [];
const hex = d => [...new Uint8Array(d)].map(b => b.toString(16).padStart(2,'0')).join(' ');

for (const m of ['sendReport', 'sendFeatureReport']) {
  const o = HIDDevice.prototype[m];
  HIDDevice.prototype[m] = function (id, data) {
    window.__log.push(`SEND ${m} id=${id} ${hex(data)}`);
    return o.apply(this, arguments);
  };
}

const oOpen = HIDDevice.prototype.open;
HIDDevice.prototype.open = function () {
  this.addEventListener('inputreport', e =>
    window.__log.push(`INPUT id=${e.reportId} ${hex(e.data.buffer)}`));
  return oOpen.apply(this, arguments);
};

const oRecv = HIDDevice.prototype.receiveFeatureReport;
HIDDevice.prototype.receiveFeatureReport = function (id) {
  return oRecv.apply(this, arguments).then(r => {
    window.__log.push(`RECV id=${id} ${hex(r.buffer ?? new Uint8Array(r))}`);
    return r;
  });
};
```

然后点页面上的「连接设备」，让它显示一次电量，最后导出：

```js
copy(window.__log.join('\n'))
```

### 只有桌面客户端（USBPcap）

装 [USBPcap](https://desowin.org/usbpcap/)，Wireshark 里抓 `USBPcap1/2/3`，
过滤器填 `usb.idVendor == 0x你的VID`，然后让客户端显示一次电量。

重点看：**`URB_CONTROL`（特征报告）还是 `URB_INTERRUPT`（输出/输入报告）**。

---

## 4. 回放验证

`tools/probe.py` 提供了几种现成的探测模式：

| 模式 | 用途 |
|---|---|
| `--list` | 列 HID 集合 |
| `--dump VID:PID --report 0x11` | 发一帧特征报告查询 |
| `--g3 VID:PID` | 按 Output+Input 模型发一帧 |
| `--scan-ids VID:PID` | 探测哪些 Report ID 存在（纯读） |
| `--replay VID:PID` | 回放 MCHOSE 0x4D 查询 |

如果是新厂商，最快的办法是**临时加一个 `--replay-xxx`**，把第 3 步抓到的查询帧硬编码进去，
发一次看应答里哪个字节等于界面显示的电量。

---

## 5. 写 Provider

```bat
copy src\battery_tray\source\_template.py src\battery_tray\source\myvendor.py
```

只改三处：

```python
VID_PIDS = frozenset({(0x1234, 0x5678)})

REPORT_ID = 0x08
QUERY = bytes([0x7D, 0x72, 0x02, 0x00, 0x01, 0x07, 0x01])

BATTERY_OFFSET = 6      # 按 payload 算（不含 Report ID）
CHARGING_OFFSET = 7
```

三个校验缺一不可：

```python
if raw[0] != REPORT_ID: return None          # Report ID 对不对
if payload[0] != 0x01:  return None          # 帧头对不对
if not (0 <= level <= 100): return None      # 电量范围对不对
```

> 少了第一、二条，很容易把「VID」「固件版本」当成电量读出来。

---

## 6. 注册到责任链

`src/battery_tray/source/__init__.py`：

```python
from .myvendor import MyVendorProvider

def build_default_chain(verbose=False):
    chain = ProviderChain([
        MchoseHidProvider(),
        MyVendorProvider(),        # ← 加这一行
        StandardHidProvider(),
        HubBridgeProvider(),
    ])
```

`priority` 数字越小越先试。自己的 Provider 建议放 10–30，兜底的放 50 以上。

> **不需要改 `app.py`**。`discover_targets()` 会枚举全部 HID 集合，再让每个 Provider
> 用 `probe()` 自己认领；没有 Provider 认领的设备会被忽略。

---

## 7. 验证

```bat
cd src
python -m battery_tray --once
```

期望输出：

```
MYDEVICE: 87%  [vendor-hid] output 0x08
```

退出码含义：`0` 成功 / `2` 没找到设备 / `3` 有设备但读不出。

---

## 8. 排查表

| 现象 | 最可能原因 | 下一步 |
|---|---|---|
| 枚举不到厂商集合 | 设备在别的模式（有线/蓝牙） | 换连接方式 |
| `SetFeature` 成功但 `GetFeature` 恒失败 | 协议族错了，应答走输入报告 | 去看官方实现 |
| `err=87` | 缓冲区长度 ≠ ReportByteLength（**含** Report ID） | 用 `caps` 里的长度 |
| `err=31` | 设备没应答（休眠 / 会话没建立） | 先唤醒设备 |
| `err=5` | 被厂商软件独占，或跨权限 | 退出官方驱动再试 |
| 读到恒定值 | 读的是 VID / 固件版本，不是电量 | 换偏移；换两个电量对比 |
| 百分比总是 10 的倍数 | 固件只上报粗粒度 | 接受，别插值 |

---

## 一条经验

**连续两轮实验都没有新信息时，停止穷举，转去观察官方实现。**

本项目在「换长度、换命令、换 Report ID」上耗掉的时间，远多于后来 hook 一次 WebHID 的 15 分钟。
