# Локальная среда владельца

Проверено 25.10.2026. Это описание конкретного компьютера, не требование к жюри.

- Исходники: `E:/work/aegml`; основной private repo `ostrovok-hackathon-2026/aegml`.
- Отдельная WSL2-среда `AegML-Hackathon`, VHD в `C:/WSL/AegML-Hackathon`. `Ubuntu-24.04`/AEGYX не менялись.
- Нативная build-копия: `/home/aegml/projects/otravel`; секреты только в её `.env`, права 0600. `py scripts/sync_to_wsl.py` копирует код без `.env`/Git/кэшей и без удаления старых файлов.
- Docker Engine 29.9.1, Compose 5.6.1. Compose project `otravel`, сервисы `app`, `db`, volume `otravel_pgdata`. Системные сервисы Windows PostgreSQL и PEM сохранены.
- Backend: `http://localhost:8081/docs`, `/health`. Порт8080 принадлежит PEMHTTPD-x64 и не освобождался. PostgreSQL контейнера не публикуется на Windows5432.
- Рабочая БД `otravel`; отдельная тестовая `otravel_test`. При разделении прежние тестовые записи сохранены переименованием, рабочая БД создана заново. Последующие HTTP smoke создают только синтетические сессии и demo orders.

## Обычное продолжение

Из PowerShell:

```powershell
py E:\work\aegml\scripts\sync_to_wsl.py
wsl.exe -d AegML-Hackathon -u root -- sh -lc 'cd /home/aegml/projects/otravel && docker compose up --build -d'
py E:\work\aegml\scripts\run_tests.py -q
```

После изменения ключей/модели нужно пересоздать только app командой `docker compose up -d --force-recreate app` из нативной папки; env читается при старте. Полный вывод `docker compose config` содержит секреты: для проверки синтаксиса используйте `config --quiet`.

## Секреты и бесплатные запросы

`py E:\work\aegml\scripts\configure_keys.py` запускает скрытый локальный ввод. Enter пропускает поле. Запись через stdin и атомарную замену под файловой блокировкой сохраняет соседние настройки. Ключи не передаются в аргументах процесса.

OpenRouter подключён через официальный OAuth/PKCE с одноразовым localhost callback. Денежный потолок ключа $0.01 разрешён владельцем и подтверждён API; приложение ограничивает цены запросов нулём. Покупки кредитов/платные модели не разрешены. Основной ID — Gemma, явный бесплатный резерв — `nvidia/nemotron-3.5-super-vl:free`. Фактическая модель пишется в результат run, поэтому резерв не выдаётся за Gemma.

Harness ограничен максимум двумя попытками на run и 40 реальными попытками за UTC-сутки, общими для Gemini/OpenRouter; лимит хранится в PostgreSQL. Отдельный ручной `probe_llm.py` имеет максимум пять проб за локальные сутки и не меняет runtime-модель. Неизвестные/account/key/workspace/quota ограничения не обходятся fallback. Эти локальные счётчики не заменяют лимит аккаунта.

## WSL lifecycle

Для настройки запущен отдельный скрытый Windows-процесс `wsl.exe -d AegML-Hackathon -- sleep infinity`, чтобы WSL не завершалась между короткими проверками. Его PID и время создания сохранены в `%LOCALAPPDATA%/OTravel/setup/wsl-keeper.json`. Перед любым ручным завершением проверяйте одновременно PID, start time и точную командную строку; PID без проверки может быть переиспользован. Это не автозапуск Windows; переживание перезагрузки компьютера не проверено.

Нельзя использовать глобальные `wsl --shutdown`, `docker prune`, удаление volume или останавливать чужие службы. База сохраняется при пересоздании app. Внешние аккаунты и ключи не входят в Docker image или Git.
