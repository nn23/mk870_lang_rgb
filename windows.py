import ctypes
from ctypes import wintypes
import struct
import time
import pywinusb.hid as hid

# --- ИДЕНТИФИКАТОРЫ КЛАВИАТУРЫ ---
VID = 0x0416
PID = 0xC345
USAGE_PAGE = 0xFF1B
USAGE = 0x91

# --- НАСТРОЙКИ ПОДСВЕТКИ ---
CUSTOM_MODE = 0x0A  # WITMOD_CUSTOM_MODE_VALUE (поклавишный режим)
BRIGHTNESS = 0x01  # Яркость (0x00..0x04)

# Выберите зону индикации: "SPACE" (только пробел), "F_KEYS" (F1-F12) или "TOP_ROW" (весь верхний ряд)
INDICATOR_TARGET = "F_KEYS"

# Фоновый цвет для всех остальных клавиш (например, мягкий белый или (0, 0, 0) чтобы выключить)
BASE_COLOR = (245, 239, 237)
# BASE_COLOR = (80, 60, 40)

# Цвета для зоны-индикатора в зависимости от раскладки (R, G, B)
LAYOUT_COLORS = {
    0x0409: (243, 183, 0),  # Английский (США)
    # 0x0409: (0, 120, 255),  # Английский (США) — Синий
    # 0x0419: (0, 129, 167),  # Русский
    0x0419: (255, 20, 0),  # Русский — Красный
    "DEFAULT": (255, 255, 255)  # Любой другой язык — Белый
}

# --- КАРТА СМЕЩЕНИЙ КЛАВИШ GK8110 (TKL 87 клавиш) ИЗ RGBController_WitmodKeyboard.cpp ---
ALL_KEYS_OFFSETS = [
    7, 13, 16, 19, 22, 28, 31, 34, 37, 40, 43, 46, 49, 52, 55, 58,
    83, 86, 89, 92, 95, 98, 101, 104, 107, 110, 113, 116, 119, 135, 138, 141, 144,
    159, 162, 165, 168, 171, 174, 177, 180, 183, 186, 199, 202, 205, 211, 214, 217, 220,
    235, 241, 244, 247, 250, 263, 266, 269, 272, 275, 278, 281, 284, 287,
    311, 314, 327, 330, 333, 336, 339, 342, 345, 348, 351, 354, 363, 369,
    397, 400, 403, 415, 427, 430, 433, 436, 442, 455, 458
]

ZONES = {
    # Только пробел (обязательна запятая после 415, чтобы Python считал это списком/кортежем)
    "SPACE": (415,),

    # Только клавиши F1 - F12
    "F_KEYS": (13, 16, 19, 22, 28, 31, 34, 37, 40, 43, 46, 49),

    # Весь верхний ряд (ESC, F1-F12, PrtSc, ScrLk, Pause)
    "TOP_ROW": (7, 13, 16, 19, 22, 28, 31, 34, 37, 40, 43, 46, 49, 52, 55, 58),

    # Цифровой ряд (1 - 0)
    "NUM_ROW": (86, 89, 92, 95, 98, 101, 104, 107, 110, 113),
}

# --- WINAPI ДЛЯ ОПРЕДЕЛЕНИЯ РАСКЛАДКИ ОКНА ---
user32 = ctypes.WinDLL('user32', use_last_error=True)


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", wintypes.RECT),
    ]


user32.GetGUIThreadInfo.restype = wintypes.BOOL
user32.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(GUITHREADINFO)]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetKeyboardLayout.restype = wintypes.HKL
user32.GetKeyboardLayout.argtypes = [wintypes.DWORD]
user32.SystemParametersInfoW.restype = wintypes.BOOL
user32.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT]


def enable_per_window_layout():
    SPI_SETTHREADLOCALINPUTSETTINGS = 0x104F
    SPIF_SENDCHANGE = 0x02
    user32.SystemParametersInfoW(SPI_SETTHREADLOCALINPUTSETTINGS, 0, ctypes.c_void_p(1), SPIF_SENDCHANGE)


def get_current_layout():
    gui_info = GUITHREADINFO()
    gui_info.cbSize = ctypes.sizeof(GUITHREADINFO)

    if user32.GetGUIThreadInfo(0, ctypes.byref(gui_info)) and gui_info.hwndFocus:
        target_hwnd = gui_info.hwndFocus
    else:
        target_hwnd = user32.GetForegroundWindow()

    if not target_hwnd:
        return None

    thread_id = user32.GetWindowThreadProcessId(target_hwnd, None)
    hkl = user32.GetKeyboardLayout(thread_id)
    return hkl & 0xFFFF


def connect_keyboard():
    try:
        devices = hid.HidDeviceFilter(
            vendor_id=VID, product_id=PID, usage_page=USAGE_PAGE, usage=USAGE
        ).get_devices()

        for dev in devices:
            dev.open()
            out_reports = dev.find_output_reports()
            if out_reports:
                report = next(iter(out_reports))
                print("Подключено к Witmod/MK870.")
                return dev, report
            dev.close()
    except Exception:
        pass
    return None, None


def safe_close(dev):
    if dev:
        try:
            dev.close()
        except Exception:
            pass


def set_custom_mode(report):
    raw_len = len(report.get_raw_data())
    buf = bytearray(raw_len)
    struct.pack_into('BB', buf, 0, 0x01, 0x07)
    struct.pack_into('BBBBBB', buf, 6, CUSTOM_MODE, BRIGHTNESS, 0x02, 0, 0, 0)
    report.set_raw_data(list(buf))
    report.send()
    time.sleep(0.015)


def update_per_key_leds(report, indicator_rgb):
    raw_len = len(report.get_raw_data())
    big_buf = bytearray(512)

    # 1. Заполняем все клавиши фоновым цветом
    for offset in ALL_KEYS_OFFSETS:
        struct.pack_into('BBB', big_buf, offset - 1, *BASE_COLOR)

    # 2. Поверх красим целевую зону (пробел или верхний ряд) в цвет раскладки
    target_offsets = ZONES.get(INDICATOR_TARGET, ZONES["SPACE"])
    for offset in target_offsets:
        struct.pack_into('BBB', big_buf, offset - 1, *indicator_rgb)

    # 3. Отправляем 8 пакетов по 64 байта (точно как в WitmodKeyboardController::SetLEDsData)
    for pkg_idx in range(8):
        pkt = bytearray(raw_len)
        pkg_size = 0x12 if pkg_idx == 7 else 0x36
        struct.pack_into('BBBBBB', pkt, 0, 0x01, 0x0F, 0x00, 0x00, pkg_idx, pkg_size)

        start = 64 * pkg_idx + 6
        pkt[6:64] = big_buf[start: start + 58]

        report.set_raw_data(list(pkt))
        report.send()

    time.sleep(0.015)


def main():
    enable_per_window_layout()
    kb, report = None, None
    last_layout = None
    mode_initialized = False
    last_tick = time.monotonic()

    print(f"Мониторинг раскладки запущен (индикатор: {INDICATOR_TARGET}). Нажмите Ctrl+C для выхода.")

    try:
        while True:
            now = time.monotonic()
            # Детектор пробуждения ПК после сна/гибернации
            if now - last_tick > 3.0:
                print("Обнаружен выход из спящего режима, переподключение...")
                safe_close(kb)
                kb, report = None, None
                last_layout = None
                mode_initialized = False
                time.sleep(1.5)
            last_tick = now

            # Подключение / автопереподключение при обрыве связи
            if kb is None or not kb.is_plugged():
                safe_close(kb)
                kb, report = connect_keyboard()
                if kb is None:
                    time.sleep(1.0)
                    continue
                last_layout = None
                mode_initialized = False

            current_layout = get_current_layout()

            if current_layout and current_layout != last_layout:
                rgb = LAYOUT_COLORS.get(current_layout, LAYOUT_COLORS["DEFAULT"])
                try:
                    if not mode_initialized:
                        set_custom_mode(report)
                        mode_initialized = True

                    update_per_key_leds(report, rgb)
                    print(f"Раскладка: {hex(current_layout)} -> Индикатор RGB{rgb}")
                    last_layout = current_layout
                except Exception as e:
                    print(f"Ошибка отправки ({e}), переподключаюсь...")
                    safe_close(kb)
                    kb, report = None, None
                    last_layout = None
                    mode_initialized = False
                    time.sleep(0.5)

            time.sleep(0.1)

    except KeyboardInterrupt:
        print("\nЗавершение работы...")
    finally:
        safe_close(kb)


if __name__ == "__main__":
    main()
