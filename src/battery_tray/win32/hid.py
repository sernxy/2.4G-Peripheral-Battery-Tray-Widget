"""Windows HID 底层：SetupAPI 枚举 + hid.dll 特征报告 + 重叠 I/O 的输入报告。

只在 Windows 上可用；import 本模块时不会碰任何系统 API，调用时才碰。

为什么这么写
------------
参考项目（2.4G-battery-viewer）踩过一个致命坑：
    「同一个 HID 句柄上并发『阻塞 ReadFile』+『WriteFile/IOCTL』会直接杀掉进程，
      连 Python 异常栈都没有。」
所以这里的 `read_input()` 用**独立句柄 + FILE_FLAG_OVERLAPPED + 事件超时 + CancelIoEx**，
并且永远不和 `set_feature()` 共用一个句柄。
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from dataclasses import dataclass

if sys.platform != "win32":
    raise ImportError("battery_tray.win32.hid 仅支持 Windows")

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
hid = ctypes.WinDLL("hid", use_last_error=True)

# --- 常量 -----------------------------------------------------------------

DIGCF_PRESENT = 0x00000002
DIGCF_DEVICEINTERFACE = 0x00000010

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
FILE_FLAG_OVERLAPPED = 0x40000000

INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value
ERROR_INSUFFICIENT_BUFFER = 122
ERROR_IO_PENDING = 997
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258

HIDP_STATUS_SUCCESS = 0x00110000

# --- 结构体 ---------------------------------------------------------------


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("InterfaceClassGuid", GUID),
        ("Flags", wintypes.DWORD),
        ("Reserved", ctypes.c_void_p),
    ]


class SP_DEVICE_INTERFACE_DETAIL_DATA_W(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("DevicePath", wintypes.WCHAR * 1),
    ]


class SP_DEVINFO_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("ClassGuid", GUID),
        ("DevInst", wintypes.DWORD),
        ("Reserved", ctypes.c_void_p),
    ]


class HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("Size", wintypes.ULONG),
        ("VendorID", wintypes.USHORT),
        ("ProductID", wintypes.USHORT),
        ("VersionNumber", wintypes.USHORT),
    ]


class HIDP_CAPS(ctypes.Structure):
    _fields_ = [
        ("Usage", wintypes.USHORT),
        ("UsagePage", wintypes.USHORT),
        ("InputReportByteLength", wintypes.USHORT),
        ("OutputReportByteLength", wintypes.USHORT),
        ("FeatureReportByteLength", wintypes.USHORT),
        ("Reserved", wintypes.USHORT * 17),
        ("NumberLinkCollectionNodes", wintypes.USHORT),
        ("NumberInputButtonCaps", wintypes.USHORT),
        ("NumberInputValueCaps", wintypes.USHORT),
        ("NumberInputDataIndices", wintypes.USHORT),
        ("NumberOutputButtonCaps", wintypes.USHORT),
        ("NumberOutputValueCaps", wintypes.USHORT),
        ("NumberOutputDataIndices", wintypes.USHORT),
        ("NumberFeatureButtonCaps", wintypes.USHORT),
        ("NumberFeatureValueCaps", wintypes.USHORT),
        ("NumberFeatureDataIndices", wintypes.USHORT),
    ]


class OVERLAPPED(ctypes.Structure):
    _fields_ = [
        ("Internal", ctypes.c_void_p),
        ("InternalHigh", ctypes.c_void_p),
        ("Offset", wintypes.DWORD),
        ("OffsetHigh", wintypes.DWORD),
        ("hEvent", wintypes.HANDLE),
    ]


# --- 函数原型 -------------------------------------------------------------

setupapi.SetupDiGetClassDevsW.restype = wintypes.HANDLE
setupapi.SetupDiGetClassDevsW.argtypes = [
    ctypes.POINTER(GUID), wintypes.LPCWSTR, wintypes.HWND, wintypes.DWORD,
]
setupapi.SetupDiEnumDeviceInterfaces.restype = wintypes.BOOL
setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
    wintypes.HANDLE, ctypes.c_void_p, ctypes.POINTER(GUID), wintypes.DWORD,
    ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
]
setupapi.SetupDiGetDeviceInterfaceDetailW.restype = wintypes.BOOL
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
    wintypes.HANDLE, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
    ctypes.POINTER(SP_DEVICE_INTERFACE_DETAIL_DATA_W), wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(SP_DEVINFO_DATA),
]
setupapi.SetupDiDestroyDeviceInfoList.restype = wintypes.BOOL
setupapi.SetupDiDestroyDeviceInfoList.argtypes = [wintypes.HANDLE]

hid.HidD_GetHidGuid.restype = None
hid.HidD_GetHidGuid.argtypes = [ctypes.POINTER(GUID)]
hid.HidD_GetAttributes.restype = wintypes.BOOLEAN
hid.HidD_GetAttributes.argtypes = [wintypes.HANDLE, ctypes.POINTER(HIDD_ATTRIBUTES)]
hid.HidD_GetPreparsedData.restype = wintypes.BOOLEAN
hid.HidD_GetPreparsedData.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
hid.HidD_FreePreparsedData.restype = wintypes.BOOLEAN
hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
hid.HidP_GetCaps.restype = wintypes.LONG
hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(HIDP_CAPS)]
hid.HidD_GetProductString.restype = wintypes.BOOLEAN
hid.HidD_GetProductString.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.ULONG]
hid.HidD_GetFeature.restype = wintypes.BOOLEAN
hid.HidD_GetFeature.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.ULONG]
hid.HidD_SetFeature.restype = wintypes.BOOLEAN
hid.HidD_SetFeature.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.ULONG]
hid.HidD_FlushQueue.restype = wintypes.BOOLEAN
hid.HidD_FlushQueue.argtypes = [wintypes.HANDLE]
hid.HidD_GetInputReport.restype = wintypes.BOOLEAN
hid.HidD_GetInputReport.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.ULONG]

kernel32.CreateFileW.restype = wintypes.HANDLE
kernel32.CreateFileW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
    wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.ReadFile.restype = wintypes.BOOL
kernel32.ReadFile.argtypes = [
    wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(OVERLAPPED),
]
kernel32.WriteFile.restype = wintypes.BOOL
kernel32.WriteFile.argtypes = [
    wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(OVERLAPPED),
]
kernel32.CreateEventW.restype = wintypes.HANDLE
kernel32.CreateEventW.argtypes = [
    ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR,
]
kernel32.WaitForSingleObject.restype = wintypes.DWORD
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.GetOverlappedResult.restype = wintypes.BOOL
kernel32.GetOverlappedResult.argtypes = [
    wintypes.HANDLE, ctypes.POINTER(OVERLAPPED),
    ctypes.POINTER(wintypes.DWORD), wintypes.BOOL,
]
kernel32.CancelIoEx.restype = wintypes.BOOL
kernel32.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(OVERLAPPED)]
kernel32.DeviceIoControl.restype = wintypes.BOOL
kernel32.DeviceIoControl.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
    ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
]

#: CTL_CODE(FILE_DEVICE_KEYBOARD=0x0B, 0x01, METHOD_NEITHER=3, FILE_ANY_ACCESS=0)
IOCTL_HID_GET_REPORT_DESCRIPTOR = 0x000B0007


# --- 数据 -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HidCaps:
    usage_page: int
    usage: int
    input_len: int
    output_len: int
    feature_len: int


class HidError(OSError):
    pass


def _last_error() -> int:
    return ctypes.get_last_error()


def enumerate_devices() -> list:
    """枚举所有 HID 顶层集合，返回 model.DeviceInfo 列表。

    注意：一台物理鼠标会返回 3–5 条；要靠 usage_page 区分「厂商集合」。
    """
    from ..core.model import DeviceInfo   # 避免循环 import

    result: list = []
    guid = GUID()
    hid.HidD_GetHidGuid(ctypes.byref(guid))

    info_set = setupapi.SetupDiGetClassDevsW(
        ctypes.byref(guid), None, None, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE
    )
    if info_set == INVALID_HANDLE_VALUE:
        raise HidError("SetupDiGetClassDevsW 失败", _last_error())

    try:
        index = 0
        while True:
            iface = SP_DEVICE_INTERFACE_DATA()
            iface.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DATA)
            ok = setupapi.SetupDiEnumDeviceInterfaces(
                info_set, None, ctypes.byref(guid), index, ctypes.byref(iface)
            )
            if not ok:
                break
            index += 1

            path = _interface_path(info_set, iface)
            if not path:
                continue
            try:
                dev = _describe(path)
            except OSError:
                continue
            if dev is not None:
                result.append(dev)
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(info_set)

    return result


def _interface_path(info_set, iface) -> str | None:
    required = wintypes.DWORD(0)
    setupapi.SetupDiGetDeviceInterfaceDetailW(
        info_set, ctypes.byref(iface), None, 0, ctypes.byref(required), None
    )
    if required.value == 0:
        return None
    buf = ctypes.create_string_buffer(required.value)
    detail = ctypes.cast(buf, ctypes.POINTER(SP_DEVICE_INTERFACE_DETAIL_DATA_W))
    detail.contents.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DETAIL_DATA_W)
    if not setupapi.SetupDiGetDeviceInterfaceDetailW(
        info_set, ctypes.byref(iface), detail, required.value, None, None
    ):
        return None
    # 注意：DevicePath 声明成 WCHAR[1]，ctypes 只会读回 1 个字符。
    # 必须按地址 + cbSize 偏移读出整段宽字符串，否则拿到的是 "\\"。
    addr = ctypes.addressof(detail.contents) + ctypes.sizeof(wintypes.DWORD)
    return ctypes.wstring_at(addr)


def _describe(path: str):
    from ..core.model import DeviceInfo

    handle = kernel32.CreateFileW(
        path, 0,                      # 只读属性：dwDesiredAccess=0 也能开系统集合
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        None, OPEN_EXISTING, 0, None,
    )
    if handle == INVALID_HANDLE_VALUE:
        return None
    try:
        attrs = HIDD_ATTRIBUTES()
        attrs.Size = ctypes.sizeof(HIDD_ATTRIBUTES)
        if not hid.HidD_GetAttributes(handle, ctypes.byref(attrs)):
            return None

        caps = _caps(handle)
        product = _product(handle)

        return DeviceInfo(
            vid=attrs.VendorID,
            pid=attrs.ProductID,
            product=product,
            interface_path=path,
            usage_page=caps.usage_page if caps else 0,
            usage=caps.usage if caps else 0,
            interface_number=_interface_number(path),
        )
    finally:
        kernel32.CloseHandle(handle)


def _caps(handle) -> HidCaps | None:
    preparsed = ctypes.c_void_p()
    if not hid.HidD_GetPreparsedData(handle, ctypes.byref(preparsed)):
        return None
    try:
        caps = HIDP_CAPS()
        if hid.HidP_GetCaps(preparsed, ctypes.byref(caps)) != HIDP_STATUS_SUCCESS:
            return None
        return HidCaps(
            usage_page=caps.UsagePage,
            usage=caps.Usage,
            input_len=caps.InputReportByteLength,
            output_len=caps.OutputReportByteLength,
            feature_len=caps.FeatureReportByteLength,
        )
    finally:
        hid.HidD_FreePreparsedData(preparsed)


def _product(handle) -> str:
    buf = ctypes.create_unicode_buffer(128)
    if hid.HidD_GetProductString(handle, buf, ctypes.sizeof(buf)):
        return buf.value
    return ""


def _interface_number(path: str) -> int:
    """从 \\\\?\\hid#vid_3837&pid_100a&mi_02#... 里抠出 2。"""
    low = path.lower()
    marker = "&mi_"
    pos = low.find(marker)
    if pos < 0:
        return -1
    tail = low[pos + len(marker): pos + len(marker) + 2]
    try:
        return int(tail, 16)
    except ValueError:
        return -1


# --- 设备句柄 -------------------------------------------------------------


class HidDevice:
    """一个打开的 HID 集合。用作上下文管理器。

    读句柄（用于 input report）与写句柄（特征报告/输出报告）分开打开，
    这是参考项目用「进程直接消失」换来的经验。
    """

    def __init__(self, path: str, writable: bool = True) -> None:
        self.path = path
        self._writable = writable
        self._write_handle: int | None = None
        self._read_handle: int | None = None
        #: open() 之后可用：集合声明的报告长度等
        self.caps: HidCaps | None = None
        #: 最近一次 Win32 调用失败的错误码，供调用方诊断
        self.last_error: int = 0

    # --- 生命周期 ---------------------------------------------------------

    def __enter__(self) -> "HidDevice":
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def open(self) -> None:
        access = GENERIC_READ | GENERIC_WRITE if self._writable else 0
        try:
            self._write_handle = self._create(access)
        except HidError:
            if access == 0:
                raise
            # 系统独占集合：退回只读句柄，特征报告通常仍可读写
            self._write_handle = self._create(0)
        self._read_handle = self._create(GENERIC_READ, FILE_FLAG_OVERLAPPED)
        self.caps = _caps(self._write_handle)

    def close(self) -> None:
        for attr in ("_read_handle", "_write_handle"):
            h = getattr(self, attr)
            if h and h != INVALID_HANDLE_VALUE:
                kernel32.CloseHandle(h)
                setattr(self, attr, None)

    def _create(self, access: int, flags: int = 0) -> int:
        h = kernel32.CreateFileW(
            self.path, access, FILE_SHARE_READ | FILE_SHARE_WRITE,
            None, OPEN_EXISTING, flags, None,
        )
        if h == INVALID_HANDLE_VALUE:
            err = _last_error()
            raise HidError(f"打开 HID 失败 err={err}（5/32 通常是厂商软件独占）", err)
        return h

    # --- 特征报告 ---------------------------------------------------------

    def get_feature(self, report_id: int, length: int) -> bytes | None:
        """length 含 Report ID 字节（即 payload + 1）。

        失败时把 Win32 错误码留在 self.last_error 里——「rx 为空」这个现象
        必须靠错误码才能区分「长度不对(87)」「设备没应答(0)」「掉线(1167)」。
        """
        if not self._write_handle:
            self.last_error = 0
            return None
        buf = ctypes.create_string_buffer(max(length, 1))
        buf[0] = bytes([report_id])
        if not hid.HidD_GetFeature(self._write_handle, buf, length):
            self.last_error = ctypes.get_last_error()
            return None
        self.last_error = 0
        return bytes(buf.raw[:length])

    def set_feature(self, data: bytes) -> bool:
        if not self._write_handle:
            self.last_error = 0
            return False
        buf = ctypes.create_string_buffer(data, len(data))
        ok = bool(hid.HidD_SetFeature(self._write_handle, buf, len(data)))
        self.last_error = 0 if ok else ctypes.get_last_error()
        return ok

    def flush(self) -> None:
        if self._write_handle:
            hid.HidD_FlushQueue(self._write_handle)

    # --- 输入报告（同步拉取）----------------------------------------------

    def get_input_report(self, report_id: int, length: int) -> bytes | None:
        """HidD_GetInputReport：直接「拉」最后一包输入报告，不等推送。

        对「应答放在输入报告里、但设备不主动推」的协议很有用。
        错误码含义：87 = 没有这个 Report ID；31 = ID 存在但设备没有数据。
        """
        if not self._write_handle:
            self.last_error = 0
            return None
        buf = ctypes.create_string_buffer(max(length, 1))
        buf[0] = bytes([report_id])
        if not hid.HidD_GetInputReport(self._write_handle, buf, length):
            self.last_error = ctypes.get_last_error()
            return None
        self.last_error = 0
        return bytes(buf.raw[:length])

    # --- 报告描述符 -------------------------------------------------------

    def get_report_descriptor(self, max_len: int = 4096) -> bytes | None:
        """取 HID 报告描述符原始字节（只读）。

        这是唯一能一次性看清「有哪些 Report ID、哪些是 Input/Output/Feature、
        每个报告多少字节」的办法。HID 没有 HidD_GetReportDescriptor 这个导出，
        要走 IOCTL_HID_GET_REPORT_DESCRIPTOR。
        """
        if not self._write_handle:
            self.last_error = 0
            return None
        buf = ctypes.create_string_buffer(max_len)
        returned = wintypes.DWORD(0)
        ok = kernel32.DeviceIoControl(
            self._write_handle, IOCTL_HID_GET_REPORT_DESCRIPTOR,
            None, 0, buf, max_len, ctypes.byref(returned), None,
        )
        if not ok:
            self.last_error = ctypes.get_last_error()
            return None
        self.last_error = 0
        return bytes(buf.raw[: returned.value])

    # --- 输入报告（重叠 I/O）----------------------------------------------

    def read_input(self, timeout_ms: int = 200) -> bytes | None:
        """读一包输入报告。超时返回 None；绝不阻塞超过 timeout_ms。"""
        if not self._read_handle:
            return None

        event = kernel32.CreateEventW(None, True, False, None)
        if not event:
            return None
        overlapped = OVERLAPPED()
        overlapped.hEvent = event
        buf = ctypes.create_string_buffer(4096)
        read = wintypes.DWORD(0)

        try:
            ok = kernel32.ReadFile(
                self._read_handle, buf, len(buf), ctypes.byref(read), ctypes.byref(overlapped)
            )
            if not ok:
                if _last_error() != ERROR_IO_PENDING:
                    return None
                if kernel32.WaitForSingleObject(event, timeout_ms) != WAIT_OBJECT_0:
                    kernel32.CancelIoEx(self._read_handle, ctypes.byref(overlapped))
                    return None
                if not kernel32.GetOverlappedResult(
                    self._read_handle, ctypes.byref(overlapped), ctypes.byref(read), False
                ):
                    return None
            return bytes(buf.raw[: read.value])
        finally:
            kernel32.CloseHandle(event)

    # --- 输出报告 ---------------------------------------------------------

    def write_output(self, data: bytes, timeout_ms: int = 500) -> bool:
        if not self._write_handle:
            self.last_error = 0
            return False
        # 简单起见复用同步句柄；输出报告很小，不会长时间阻塞
        written = wintypes.DWORD(0)
        buf = ctypes.create_string_buffer(data, len(data))
        ok = bool(kernel32.WriteFile(
            self._write_handle, buf, len(data), ctypes.byref(written), None
        ))
        self.last_error = 0 if ok else ctypes.get_last_error()
        return ok
