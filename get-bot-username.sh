#!/usr/bin/env bash
# Read the bot token from the project's .env without executing shell code.
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if ! command -v python3 >/dev/null 2>&1; then
    echo 'Нужен Python 3: команда python3 не найдена.' >&2
    exit 1
fi

exec python3 - "$project_dir" <<'PY'
import json
import re
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path


def fail(message):
    print(message, file=sys.stderr)
    sys.exit(1)


root = Path(sys.argv[1])
try:
    lines = (root / ".env").read_text(encoding="utf-8-sig").splitlines()
except OSError:
    fail("Не удалось прочитать .env в корне проекта. Скопируйте .env.example в .env.")

token = ""
for line in lines:
    match = re.match(r"^\s*(?:export\s+)?MAX_BOT_TOKEN\s*=\s*(.*?)\s*$", line)
    if match:
        value = match.group(1)
        if value.startswith(("'", '"')):
            quoted = re.fullmatch(r"(['\"])(.*?)\1\s*(?:#.*)?", value)
            if not quoted:
                fail("MAX_BOT_TOKEN должен быть записан одной строкой с закрытыми кавычками.")
            token = quoted.group(2)
        else:
            token = re.split(r"\s+#", value, maxsplit=1)[0].strip()

if not token:
    fail("Заполните MAX_BOT_TOKEN в .env.")
if any(char.isspace() for char in token):
    fail("В MAX_BOT_TOKEN обнаружены пробелы. Проверьте значение в .env.")

# Add the project's CA to system roots; certificate verification stays enabled.
context = ssl.create_default_context()
try:
    context.load_verify_locations(root / "backend/certs/russian_trusted_root_ca.pem")
except (OSError, ssl.SSLError):
    fail("Не удалось загрузить backend/certs/russian_trusted_root_ca.pem.")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


opener = urllib.request.build_opener(
    urllib.request.HTTPSHandler(context=context), NoRedirect()
)
request = urllib.request.Request(
    "https://platform-api2.max.ru/me",
    headers={"Authorization": token, "Accept": "application/json"},
)
try:
    with opener.open(request, timeout=20) as response:
        bot = json.load(response)
except urllib.error.HTTPError as exc:
    if exc.code == 401:
        fail("MAX отклонил токен (HTTP 401). Проверьте MAX_BOT_TOKEN в .env.")
    fail(f"MAX вернул HTTP {exc.code}.")
except (urllib.error.URLError, TimeoutError, OSError):
    fail("Не удалось подключиться к MAX. Проверьте сеть, системное время и сертификаты.")
except (ValueError, UnicodeError):
    fail("MAX вернул некорректный JSON.")

username = bot.get("username") if isinstance(bot, dict) else None
if not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9_\-]+", username):
    fail("MAX не вернул корректный username бота. Уточните имя у организаторов.")

print(f"MAX_BOT_USERNAME={username}")
print(f"Ссылка на бота: https://max.ru/{username}")
print("Замените строку MAX_BOT_USERNAME в .env полученным значением.")
PY
