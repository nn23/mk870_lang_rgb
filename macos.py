import time
import sys
import hid
import ctypes
import ctypes.util
from Foundation import NSRunLoop, NSDate

# --- НАСТРОЙКИ ОБОРУДОВАНИЯ ---
VID = 0x0416
PID = 0xC345
USAGE_PAGE = 0xFF1B
USAGE = 0x0091

# --- СЛОВАРЬ ЦВЕТОВ ДЛЯ РАСКЛАДОК ---
COLORS_EN = {"all": (0, 255, 255)}  # Бирюзовый
COLORS_RU = {"all": (255, 0, 128)}  # Розово-малиновый

# Карты смещений (Ваша карта из Windows)
ALL_OFFSETS = [7, 13, 16, 19, 22, 28, 31, 34, 37, 40, 43, 46, 49, 52, 55, 58, 83, 86, 89, 92, 95, 98, 101, 104, 107,
               110, 113, 116, 119]

# --- ДИНАМИЧЕСКИЙ КАРКАС ДЛЯ MAC OS (CARBON VIA CTYPES) ---
carbon_path = ctypes.util.find_library("Carbon")
carbon = ctypes.cdll.LoadLibrary(carbon_path)

carbon.TISCopyCurrentKeyboardInputSource.restype = ctypes.c_void_p
carbon.TISGetInputSourceProperty.restype = ctypes.c_void_p
carbon.TISGetInputSourceProperty.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

cf_path = ctypes.util.find_library("CoreFoundation")
cf = ctypes.cdll.LoadLibrary(cf_path)
cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_ssize_t, ctypes.c_uint32]
cf.CFStringGetCString.restype = ctypes.c_bool

kTISPropertyInputSourceID = ctypes.c_void_p.in_dll(carbon, "kTISPropertyInputSourceID")


def get_current_layout_id():
    """Нативно возвращает глобальный ID раскладки в macOS (работает в фоне)"""
    source = carbon.TISCopyCurrentKeyboardInputSource()
    if not source:
        return "unknown"

    cf_str = carbon.TISGetInputSourceProperty(source, kTISPropertyInputSourceID)
    if not cf_str:
        return "unknown"

    buffer = ctypes.create_string_buffer(256)
    kCFStringEncodingUTF8 = 0x08000100
    if cf.CFStringGetCString(cf_str, buffer, 256, kCFStringEncodingUTF8):
        return buffer.value.decode('utf-8')
    return "unknown"


# --- УПРАВЛЕНИЕ КЛАВИАТУРОЙ ---
class KeyboardController:
    def __init__(self):
        self.device = None
        self.connect()

    def connect(self):
        print("Поиск устройства FL·ESPORTS MK870...")
        target_path = None

        for info in hid.enumerate(VID, PID):
            if info['usage_page'] == USAGE_PAGE and info['usage'] == USAGE:
                target_path = info['path']
                break

        if not target_path:
            print("❌ Управляющий HID-интерфейс не найден.")
            sys.exit(1)

        try:
            self.device = hid.device()
            self.device.open_path(target_path)
            self.device.set_nonblocking(1)
            print("✅ Подключено к управляющему интерфейсу!")
        except Exception as e:
            print(f"❌ Ошибка открытия устройства: {e}")
            sys.exit(1)

    def init_custom_mode(self):
        """Включение поклавишного режима (65 байт, Report ID = 0x00)"""
        packet = [0x00] * 65
        packet[0] = 0x00  # На macOS Ventura часто требуется 0x00 как заглушка Report ID
        packet[1] = 0x01  # Оригинальный Report ID из Windows теперь на 1-й позиции
        packet[2] = 0x07  # Команда SetMode
        packet[7] = 0x0A  # WITMOD_CUSTOM_MODE_VALUE
        packet[8] = 0x04  # Макс яркость
        packet[9] = 0x02  # Скорость

        try:
            self.device.write(packet)
            time.sleep(0.015)
            print("Команда инициализации (0x00) отправлена.")
        except Exception as e:
            print(f"Ошибка инициализации режима: {e}")

    def update_leds(self, color_map):
        """Обновление цветов в RAM (65 байт, Report ID = 0x00)"""
        big_buf = [0] * 512
        default_color = color_map.get("all", (0, 0, 0))

        for offset in ALL_OFFSETS:
            r, g, b = color_map.get(offset, default_color)
            if offset + 1 < 512:
                big_buf[offset - 1] = r
                big_buf[offset] = g
                big_buf[offset + 1] = b

        try:
            for pkg_idx in range(8):
                packet = [0x00] * 65
                packet[0] = 0x00  # Заглушка Report ID для macOS
                packet[1] = 0x01  # Оригинальный Report ID
                packet[2] = 0x0F  # Команда SetLEDsData
                packet[5] = pkg_idx
                packet[6] = 0x12 if pkg_idx == 7 else 0x36

                start_buf = 64 * pkg_idx + 6
                end_buf = 64 * pkg_idx + 64

                for i, buf_idx in enumerate(range(start_buf, end_buf)):
                    if buf_idx < len(big_buf):
                        # Данные записываются со смещением +1 из-за пакет[0]=0x00
                        packet[7 + i] = big_buf[buf_idx]

                self.device.write(packet)

            time.sleep(0.015)
        except Exception as e:
            print(f"Ошибка отправки пакетов подсветки: {e}")


def main():
    controller = KeyboardController()
    # controller.init_custom_mode()

    # Отслеживаем состояние раскладки
    last_layout = None

    print("🚀 Скрипт успешно запущен в режиме фонового опроса. Нажмите Ctrl+C для выхода.")

    try:
        while True:
            current_layout = get_current_layout_id()

            # Если раскладка изменилась по сравнению с прошлым циклом
            if current_layout != last_layout:
                print(f"🔄 Смена раскладки macOS: {current_layout}")
                last_layout = current_layout

                if "Russian" in current_layout or "Ru" in current_layout:
                    controller.update_leds(COLORS_RU)
                else:
                    controller.update_leds(COLORS_EN)

            # Вместо time.sleep используем легкий нативный сон на 0.1 сек
            # для удержания консистентности потока на Mac
            fire_date = NSDate.dateWithTimeIntervalSinceNow_(0.1)
            NSRunLoop.currentRunLoop().runUntilDate_(fire_date)

    except KeyboardInterrupt:
        print("\nЗавершение работы...")
        if controller.device:
            controller.device.close()


if __name__ == "__main__":
    main()
