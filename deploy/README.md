# Деплой на VPS

При push в `main` workflow `deploy.yml` запускает backend и web проверки, собирает два Docker-образа в GitHub Actions, публикует их в GHCR и по SSH обновляет Compose-проект на VPS. Образы привязаны к SHA коммита. На VPS сборка не выполняется.

## Настройка сервера

- Пользователь `campus-deploy` состоит в группе `docker` и владеет `/opt/campus`.
- Приватный SSH-ключ находится в секрете репозитория `VPS_SSH_KEY`; на VPS лежит только публичный ключ пользователя.
- `deploy/known_hosts` закрепляет SSH host key VPS.
- Compose хранит Postgres в отдельном Docker volume. API и Postgres не публикуют порты. Web слушает только `127.0.0.1:8088`; nginx проксирует `max.fblrkus.ru` на этот порт.

## Секреты GitHub Actions

| Secret | Назначение |
| --- | --- |
| `VPS_HOST` | IP VPS |
| `VPS_SSH_KEY` | SSH-ключ пользователя `campus-deploy` |
| `MAX_BOT_TOKEN` | Токен бота MAX для API и бота |
| `POSTGRES_PASSWORD` | Пароль БД. После первого запуска менять его нужно вместе с паролем роли в Postgres. |

## Переменные GitHub Actions

| Variable | Назначение |
| --- | --- |
| `MAX_BOT_USERNAME` | Текущее имя бота MAX, соответствующее `MAX_BOT_TOKEN` |

Перед запуском deploy задайте repository variable `MAX_BOT_USERNAME` в **Settings → Secrets and variables → Actions → Variables**. Workflow завершится с понятной ошибкой, если переменная не задана или содержит перенос строки.

Workflow передаёт настройки и теги образов через SSH в `/opt/campus/.env` с правами `0600`. Значения записываются через аргументы `printf` и stdin SSH, без подстановки имени бота в shell-команду. `PUBLIC_WEB_URL` остаётся заданным в workflow; при изменении домена обновите его там.

## Проверка и обслуживание

```sh
ssh campus-deploy@153.80.244.205
cd /opt/campus
docker compose --env-file .env -f compose.prod.yaml ps
docker compose --env-file .env -f compose.prod.yaml logs --tail=100 api bot web
cat deployed-sha
cat deployed-run
```

`seed.log` доступен только пользователю деплоя и содержит приглашение организатора, созданное seed. Деплой повторно выполняет миграции и идемпотентный seed, затем ждёт готовности сервисов. Повторный запуск доступен через `workflow_dispatch` на `main`.

DNS-запись `max.fblrkus.ru` указывает на VPS. Nginx проксирует HTTPS на `127.0.0.1:8088`; сертификат Let's Encrypt продлевается Certbot. После деплоя проверьте `https://max.fblrkus.ru` и `curl http://127.0.0.1:8088/` на VPS.
