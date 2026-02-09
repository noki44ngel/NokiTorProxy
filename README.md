# NokiTorProxy

Минимальный TUI-менеджер Tor proxy для Linux.

## Что нужно

- Linux
- `tor`
- Python 3
- `pip`
- Python пакет: `requests`
- Для сборки бинарника: `nuitka`

## Быстрый старт

```bash
git clone https://github.com/noki44ngel/NokiTorProxy.git
cd NokiTorProxy
pip install -r requirements.txt
python3 nokiTOR.py
```

Приложение само найдет (`tor.service`, `tor@default` или `service tor`) и запустить его через `sudo`.

## Установка зависимостей

### Arch Linux

```bash
sudo pacman -S tor python python-pip
pip install -r requirements.txt
```

### Ubuntu / Debian

```bash
sudo apt update
sudo apt install -y tor python3 python3-pip
pip3 install -r requirements.txt
```

## Сборка бинарника

```bash
pip install nuitka
./build.sh
```

Готовый файл: `build/torproxy`
