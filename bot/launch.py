"""One command starts an HTTPS tunnel and the shop. No authentication bypass."""
import argparse
import os
from pathlib import Path
import platform
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from dotenv import set_key
from bot.config import ROOT,load_settings

URL_PATTERN=re.compile(r'https://[a-z0-9-]+\.trycloudflare\.com\b')


def tunnel_binary():
    installed=shutil.which('cloudflared')
    if installed:
        return Path(installed)
    if sys.platform!='win32' or platform.machine().lower() not in {'amd64','x86_64'}:
        raise RuntimeError('Установите cloudflared для вашей ОС и повторите запуск.')
    target=ROOT/'data'/'tools'/'cloudflared.exe'
    if not target.exists():
        target.parent.mkdir(parents=True,exist_ok=True)
        partial=target.with_suffix('.download')
        print('Загружается cloudflared из официального репозитория Cloudflare…',flush=True)
        try:
            with urllib.request.urlopen('https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe',timeout=60) as response:
                with partial.open('wb') as out:
                    shutil.copyfileobj(response,out)
            partial.replace(target)
        except Exception as exc:
            partial.unlink(missing_ok=True)
            raise RuntimeError('Не удалось скачать cloudflared. Проверьте интернет или установите cloudflared вручную.') from exc
    return target


def wait_url(proc,timeout=90):
    lines=queue.Queue()
    def reader():
        for line in proc.stdout:
            lines.put(line)
        lines.put(None)
    threading.Thread(target=reader,daemon=True).start()
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        try:
            line=lines.get(timeout=min(1,max(.01,deadline-time.monotonic())))
        except queue.Empty:
            continue
        if line is None:
            raise RuntimeError('HTTPS-туннель остановился. Проверьте доступность Cloudflare из вашей сети.')
        match=URL_PATTERN.search(line)
        if match:
            return match.group()
    raise RuntimeError('Cloudflare не выдал HTTPS-адрес за 90 секунд. Попробуйте другую сеть или собственный HTTPS-прокси.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare',action='store_true',help='Only download and check cloudflared')
    args=parser.parse_args()
    if args.prepare:
        binary=tunnel_binary()
        subprocess.run([str(binary),'--version'],check=True)
        return
    settings=load_settings()
    if not settings.manager_ids:
        print('Получите свой числовой Telegram ID командой /id в боте.')
        value=input('Telegram ID менеджера: ').strip()
        if not value.isdigit() or int(value)<=0:
            raise RuntimeError('Нужен положительный числовой ID. Чужого человека нельзя назначать менеджером.')
        set_key(str(ROOT/'.env'),'MANAGER_IDS',value,quote_mode='never')
        managers=value
    else:
        managers=','.join(map(str,settings.manager_ids))
    host='127.0.0.1' if settings.web_host=='0.0.0.0' else settings.web_host
    with socket.socket() as probe:
        if probe.connect_ex((host,settings.web_port))==0:
            raise RuntimeError(f'Порт {settings.web_port} занят. Остановите прежний запуск бота (Ctrl+C) и повторите.')
    binary=tunnel_binary()
    tunnel=None; bot=None
    try:
        tunnel=subprocess.Popen([str(binary),'tunnel','--url',f'http://{host}:{settings.web_port}',
                                 '--no-autoupdate','--protocol','http2'],
                                stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                                text=True,encoding='utf-8',errors='replace',
                                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        url=wait_url(tunnel)
        set_key(str(ROOT/'.env'),'MINI_APP_URL',url,quote_mode='never')
        env=dict(os.environ,MINI_APP_URL=url,MANAGER_IDS=managers)
        print(f'Mini App: {url}\nВ Telegram отправьте /start → «Финансы · Mini App» → «Открыть Mini App».',flush=True)
        bot=subprocess.Popen([sys.executable,'-m','bot'],cwd=ROOT,env=env)
        while bot.poll() is None:
            if tunnel.poll() is not None:
                raise RuntimeError('HTTPS-туннель отключился. Повторите запуск для получения нового адреса.')
            time.sleep(.5)
        if bot.returncode:
            raise RuntimeError('Бот остановился с ошибкой. Проверьте сообщение выше.')
    except KeyboardInterrupt:
        print('\nБот и HTTPS-туннель остановлены.')
    finally:
        for child in (bot,tunnel):
            if child and child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()


if __name__=='__main__':
    try:
        main()
    except (RuntimeError,OSError) as exc:
        print(f'Ошибка запуска: {exc}',file=sys.stderr)
        sys.exit(1)
