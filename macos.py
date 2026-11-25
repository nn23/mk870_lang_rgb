import ctypes
import ctypes.util
import time

import hid
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
ALL_OFFSETS = [13, 16, 19, 22, 28, 31, 34, 37, 40, 43, 46, 49]

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
        self.is_connected = False

    def connect(self):
        """Поиск и подключение к устройству. Возвращает True при успехе."""
        target_path = None
        for info in hid.enumerate(VID, PID):
            if info['usage_page'] == USAGE_PAGE and info['usage'] == USAGE:
                target_path = info['path']
                break

        if not target_path:
            if self.is_connected:
                print("⚠️ Клавиатура отключена от USB. Ожидание повторного подключения...")
                self.is_connected = False
                self.device = None
            return False

        try:
            if not self.is_connected:
                self.device = hid.device()
                self.device.open_path(target_path)
                self.device.set_nonblocking(1)
                print("✅ Клавиатура подключена к управляющему интерфейсу!")
                self.is_connected = True

                # Умная проверка режима и яркости перед инициализацией
                self.check_and_ensure_mode()
                return True
        except Exception as e:
            self.is_connected = False
            self.device = None
            return False
        return self.is_connected

    def check_and_ensure_mode(self):
        """Считывает текущие настройки из флеш-памяти клавиатуры.
        Инициализирует кастомный режим только если он сбит или яркость не средняя."""
        if not self.device:
            return

        # Пакет запроса состояния (GetMode = 0x06)
        read_request = [0x00] * 65
        read_request[0] = 0x00  # macOS заглушка Report ID
        read_request[1] = 0x01  # Эмуляция оригинального Report ID
        read_request[2] = 0x06  # Команда GetMode

        try:
            # Отправляем запрос
            self.device.write(read_request)
            time.sleep(0.02)  # Даем контроллеру время ответить

            # Читаем ответ (до 64 байт)
            response = self.device.read(64)

            if response and len(response) >= 8:
                # В ответе от hidapi на macOS байты сдвинуты так же, как при отправке.
                # Индексы соответствуют структуре:
                # response[2] - эхо команды, response[6] - текущий режим, response[7] - яркость
                current_mode = response[6]
                current_brightness = response[7]

                print(f"📊 Текущий статус устройства: Режим={hex(current_mode)}, Яркость={hex(current_brightness)}")

                # Проверяем, совпадает ли режим с кастомным (0x0A) и яркость со средней (0x02)
                if current_mode == 0x0A and current_brightness == 0x02:
                    print("🛡️ Настройки верны. Запись во флеш-память НЕ требуется.")
                    return
                else:
                    print("🔄 Настройки на устройстве отличаются от целевых.")
            else:
                print("⚠️ Не удалось прочитать ответ от клавиатуры. Форсируем инициализацию для безопасности.")

            # Если настройки не совпали или ответ не дошел — перезаписываем режим
            self.init_custom_mode()

        except Exception as e:
            print(f"Ошибка при проверке режима: {e}")
            # В случае сбоя чтения — подстраховываемся стандартным методом
            self.init_custom_mode()

    def init_custom_mode(self):
        """Включение поклавишного режима со СРЕДНЕЙ яркостью"""
        if not self.device:
            return
        packet = [0x00] * 65
        packet[0] = 0x00  # Заглушка Report ID для macOS
        packet[1] = 0x01  # Оригинальный Report ID
        packet[2] = 0x07  # Команда SetMode
        packet[3] = 0x0A  # WITMOD_CUSTOM_MODE_VALUE
        packet[4] = 0x02  # СРЕДНЯЯ ЯРКОСТЬ
        packet[5] = 0x02  # Скорость

        try:
            self.device.write(packet)
            time.sleep(0.015)
            print("💾 ВНИМАНИЕ: Целевой режим записан во флеш-память контроллера.")
        except Exception as e:
            print(f"Ошибка инициализации режима: {e}")
            self.is_connected = False

    def update_leds(self, color_map):
        """Обновление цветов в RAM"""
        if not self.device or not self.is_connected:
            return False

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
                packet[3] = pkg_idx
                packet[4] = 0x12 if pkg_idx == 7 else 0x36

                start_buf = 64 * pkg_idx + 6
                end_buf = 64 * pkg_idx + 64

                for i, buf_idx in enumerate(range(start_buf, end_buf)):
                    if buf_idx < len(big_buf):
                        packet[7 + i] = big_buf[buf_idx]

                self.device.write(packet)

            time.sleep(0.015)
            return True
        except Exception as e:
            print(f"⚠️ Ошибка отправки пакетов (возможно устройство отключено): {e}")
            self.is_connected = False
            return False

    def turn_off_leds(self):
        """Полное выключение подсветки"""
        if self.device and self.is_connected:
            print("🌙 Выключение подсветки перед выходом...")
            self.update_leds({})


def main():
    controller = KeyboardController()
    controller.connect()

    last_layout = None
    print("🚀 Скрипт успешно запущен в интеллектуальном режиме. Нажмите Ctrl+C для выхода.")

    try:
        while True:
            is_alive = controller.connect()

            if is_alive:
                current_layout = get_current_layout_id()

                if current_layout != last_layout:
                    # print(f"🔄 Смена раскладки macOS: {current_layout}")
                    last_layout = current_layout

                    if "Russian" in current_layout or "Ru" in current_layout:
                        controller.update_leds(COLORS_RU)
                    else:
                        controller.update_leds(COLORS_EN)
            else:
                last_layout = None

            fire_date = NSDate.dateWithTimeIntervalSinceNow_(0.1)
            NSRunLoop.currentRunLoop().runUntilDate_(fire_date)

    except KeyboardInterrupt:
        print("\nЗавершение работы...")
        try:
            controller.turn_off_leds()
        except Exception:
            pass
        if controller.device:
            controller.device.close()


if __name__ == "__main__":
    main()
