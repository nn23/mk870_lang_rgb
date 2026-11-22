import ctypes
import ctypes.util
import struct
import time
import hid

# --- ИДЕНТИФИКАТОРЫ КЛАВИАТУРЫ (Witmod / MK870) ---
VID = 0x0416
PID = 0xC345
USAGE_PAGE = 0xFF1B
USAGE = 0x91

# --- НАСТРОЙКИ ПОДСВЕТКИ ---
CUSTOM_MODE = 0x0A  # Поклавишный режим
BRIGHTNESS = 0x01  # Яркость (0x00..0x04)

# Зона индикации: "SPACE", "F_KEYS", "TOP_ROW" или "NUM_ROW"
INDICATOR_TARGET = "SPACE"

# Фоновый цвет остальных клавиш (R, G, B)
BASE_COLOR = (40, 40, 40)

# Цвета индикатора в зависимости от раскладки в macOS
LAYOUT_COLORS = {
    "ABC": (0, 120, 255),  # Английский (ABC / US) — Синий
    "US": (0, 120, 255),  # Английский (US) — Синий
    "Russian": (255, 20, 0),  # Русская — Красный
    "RussianWin": (255, 20, 0),  # Русская (ПК) — Красный
    "DEFAULT": (255, 255, 255)  # Любая другая раскладка — Белый
}

# --- КАРТА СМЕЩЕНИЙ ВСЕХ 87 КЛАВИШ GK8110 (TKL) ---
ALL_KEYS_OFFSETS = (
    7, 13, 16, 19, 22, 28, 31, 34, 37, 40, 43, 46, 49, 52, 55, 58,
    83, 86, 89, 92, 95, 98, 101, 104, 107, 110, 113, 116, 119, 135, 138, 141, 144,
    159, 162, 165, 168, 171, 174, 177, 180, 183, 186, 199, 202, 205, 211, 214, 217, 220,
    235, 241, 244, 247, 250, 263, 266, 269, 272, 275, 278, 281, 284, 287,
    311, 314, 327, 330, 333, 336, 339, 342, 345, 348, 351, 354, 363, 369,
    397, 400, 403, 415, 427, 430, 433, 436, 442, 455, 458
)

ZONES = {
    "SPACE": (415,),
    "F_KEYS": (13, 16, 19, 22, 28, 31, 34, 37, 40, 43, 46, 49),
    "TOP_ROW": (7, 13, 16, 19, 22, 28, 31, 34, 37, 40, 43, 46, 49, 52, 55, 58),
    "NUM_ROW": (86, 89, 92, 95, 98, 101, 104, 107, 110, 113),
}

# --- ПОДКЛЮЧЕНИЕ СИСТЕМНЫХ ФРЕЙМВОРКОВ MACOS (Carbon + CoreFoundation) ---
carbon_path = ctypes.util.find_library("Carbon")
cf_path = ctypes.util.find_library("CoreFoundation")

carbon = ctypes.CDLL(carbon_path)
cf = ctypes.CDLL(cf_path)

carbon.TISCopyCurrentKeyboardInputSource.restype = ctypes.c_void_p
carbon.TISGetInputSourceProperty.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
carbon.TISGetInputSourceProperty.restype = ctypes.c_void_p

cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
cf.CFStringGetCString.restype = ctypes.c_bool
cf.CFRelease.argtypes = [ctypes.c_void_p]

kTISPropertyInputSourceID = ctypes.c_void_p.in_dll(carbon, "kTISPropertyInputSourceID")
kCFStringEncodingUTF8 = 0x08000100


def get_current_layout_macos():
    """Возвращает короткое имя текущей раскладки macOS (например, 'ABC' или 'RussianWin')."""
    source = carbon.TISCopyCurrentKeyboardInputSource()
    if not source:
        return None
    try:
        cf_str = carbon.TISGetInputSourceProperty(source, kTISPropertyInputSourceID)
        if not cf_str:
            return None
        buf = ctypes.create_string_buffer(256)
        if cf.CFStringGetCString(cf_str, buf, 256, kCFStringEncodingUTF8):
            full_id = buf.value.decode("utf-8")
            # full_id выглядит как 'com.apple.keylayout.RussianWin' -> берем последнюю часть
            return full_id.split(".")[-1]
    finally:
        cf.CFRelease(source)
    return None


def connect_keyboard():
    """Ищет и открывает вендорский интерфейс MK870 в macOS."""
    try:
        for d in hid.enumerate(VID, PID):
            if d.get("usage_page") == USAGE_PAGE and d.get("usage") == USAGE:
                dev = hid.device()
                dev.open_path(d["path"])
                print("Подключено к Witmod/MK870 (macOS).")
                return dev
    except Exception:
        pass
    return None


def safe_close(dev):
    if dev:
        try:
            dev.close()
        except Exception:
            pass


def set_custom_mode(dev):
    # В macOS hidapi принимает 64 байта, начиная сразу с Report ID (0x01)
    buf = bytearray(64)
    struct.pack_into("BB", buf, 0, 0x01, 0x07)
    struct.pack_into("BBBBBB", buf, 6, CUSTOM_MODE, BRIGHTNESS, 0x02, 0, 0, 0)
    dev.write(bytes(buf))
    time.sleep(0.015)


def update_per_key_leds(dev, indicator_rgb):
    big_buf = bytearray(512)

    for offset in ALL_KEYS_OFFSETS:
        struct.pack_into("BBB", big_buf, offset - 1, *BASE_COLOR)

    target_offsets = ZONES.get(INDICATOR_TARGET, ZONES["SPACE"])
    for offset in target_offsets:
        struct.pack_into("BBB", big_buf, offset - 1, *indicator_rgb)

    for pkg_idx in range(8):
        pkt = bytearray(64)
        pkg_size = 0x12 if pkg_idx == 7 else 0x36
        struct.pack_into("BBBBBB", pkt, 0, 0x01, 0x0F, 0x00, 0x00, pkg_idx, pkg_size)

        start = 64 * pkg_idx + 6
        pkt[6:64] = big_buf[start: start + 58]

        dev.write(bytes(pkt))

    time.sleep(0.015)


def main():
    dev = None
    last_layout = None
    mode_initialized = False
    last_tick = time.monotonic()

    print(f"Мониторинг раскладки macOS запущен (индикатор: {INDICATOR_TARGET}). Нажмите Ctrl+C для выхода.")

    try:
        while True:
            now = time.monotonic()
            # Детектор выхода Mac из режима сна
            if now - last_tick > 3.0:
                print("Обнаружен выход из сна, переподключение...")
                safe_close(dev)
                dev = None
                last_layout = None
                mode_initialized = False
                time.sleep(1.5)
            last_tick = now

            if dev is None:
                dev = connect_keyboard()
                if dev is None:
                    time.sleep(1.0)
                    continue
                last_layout = None
                mode_initialized = False

            current_layout = get_current_layout_macos()

            if current_layout and current_layout != last_layout:
                rgb = LAYOUT_COLORS.get(current_layout, LAYOUT_COLORS["DEFAULT"])
                try:
                    if not mode_initialized:
                        set_custom_mode(dev)
                        mode_initialized = True

                    update_per_key_leds(dev, rgb)
                    print(f"Раскладка: {current_layout} -> Индикатор RGB{rgb}")
                    last_layout = current_layout
                except Exception as e:
                    print(f"Ошибка отправки ({e}), переподключаюсь...")
                    safe_close(dev)
                    dev = None
                    last_layout = None
                    mode_initialized = False
                    time.sleep(0.5)

            time.sleep(0.1)

    except KeyboardInterrupt:
        print("\nЗавершение работы...")
    finally:
        safe_close(dev)


if __name__ == "__main__":
    main()
