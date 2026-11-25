# macos
## Установка системных зависимостей
```
brew install hidapi
```

## Настройка Python-окружения
```
python3 -m venv .venv
source .venv/bin/activate
pip install hidapi pyobjc-framework-Quartz
```

## Проверка, что Python видит клавиатуру (должен вывести путь или данные)
```
python3 -c "import hid; print([i for i in hid.enumerate() if i['vendor_id'] == 0x0416])"
```