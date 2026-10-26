# План проверки чистого Docker-запуска

**Исторический план перед исполнением.** На момент создания: NOT_EXECUTED. Только план по текущим `Dockerfile`, `docker-compose.yml`, `config.py` и API. Образы/контейнеры/volumes/`.env` этим документом не создавались. Исполнять после Date-checkpoint, с заново зафиксированными SHA исходников: код ещё меняется.

Цель: проверить настоящий запуск приложения и нового PostgreSQL одной Compose-командой без личных API-ключей и hostSQL, затем DEMO→preview→simulatedconfirm и сохранение после перезапуска только собственных контейнеров. Это не проверка живых поставщиков или качества LLM.

## Изоляция до build

Источник — только `E:/work/aegml` (`/mnt/e/work/aegml` внутри выделенного AegML-Hackathon), **не** нативная C-копия работающих8081/8083/8084. Снять SHA commit+tree/Dockerfile/lock/compose/приложения перед build и после; при изменении контекста квалификация HOLD, а не PASS смешанного кода. `.dockerignore` исключает `.env*`, `.git`, `.local`, researchsources и локальные окружения; проверить его снова на зафиксированной ревизии.

Стенд:

- Compose project: `otravel-cleanstart-20261026` через явный `-p` (он имеет приоритет над `name: otravel`).
- App image: отдельный `otravel-agent:cleanstart-20261026-<shortsha>`, **никогда** `latest`/тег живого приложения.
- Порт: только `127.0.0.1:8085:8080`; до запуска проверить, что8085 свободен, не останавливать чужой процесс при конфликте.
- PG: тот же pinnedpostgresimage из основного compose, новый project-scoped `pgdata`; никакого hostport, externalvolume, старого volume или WindowsPostgreSQL.
- Docker engine: только предварительно проверенный engine выделенной AegML-среды, не Dockercontext другой машины.

Запускать DockerCLI из дочернего окружения с **allowlist**, а не копией всех `os.environ`: нужный системный PATH, отдельный пустой taskDOCKER_CONFIG, фиксированный `DOCKER_HOST=unix:///var/run/docker.sock` после проверки engine и `APP_PORT=8085`. Удалить наследованные `COMPOSE_*`, DB/API/LLMcredentials и proxy/config переменные, если они не нужны выбранному CLI. Не переприсваивать `$HOME` или `$CODEX_HOME`. Системные необходимые пути определить при preflight; не печатать исходное окружение.

Передавать `--env-file /dev/null` (в Linux) и явные `-f <E-source>/docker-compose.yml -f <taskoverride>`/`--project-directory <E-source>`. Отдельный envfile подавляет автоматическое чтение проектной `.env`, **но не убирает приоритет hostenv** — поэтому нужен allowlisted subprocess. Не выполнять `docker compose config --environment` в исходном пользовательском окружении. [Правила интерполяции](https://docs.docker.com/compose/how-tos/environment-variables/variable-interpolation/), [приоритет projectname](https://docs.docker.com/compose/how-tos/project-name/).

## Точные значения override

Будущий override хранить внутри ignored `.local/evaluation/cleanstart-20261026/`; никаких настоящих секретов. Пароль ниже — публичная синтетическая fixture только изолированного локального PG. Он должен совпадать в db/app.

```yaml
services:
  db:
    environment:
      POSTGRES_DB: otravel
      POSTGRES_USER: otravel
      POSTGRES_PASSWORD: otravel-cleanstart-fixture-20261026
  app:
    image: otravel-agent:cleanstart-20261026-REPLACE_WITH_SHORT_SHA
    environment:
      DATABASE_URL: ""
      DATABASE_HOST: db
      DATABASE_PORT: "5432"
      DATABASE_NAME: otravel
      DATABASE_USER: otravel
      DATABASE_PASSWORD: otravel-cleanstart-fixture-20261026
      LLM_PROVIDER: openrouter
      OPENROUTER_MODEL: dots-studio/dots-3-note-preview:free
      OPENROUTER_BACKUP_MODEL: ""
      OPENROUTER_API_KEY: ""
      GEMINI_API_KEY: ""
      GEMINI_FREE_TIER_CONFIRMED: "false"
      GROQ_API_KEY: ""
      GROQ_FREE_TIER_CONFIRMED: "false"
      ZAI_API_KEY: ""
      ZAI_FREE_TIER_CONFIRMED: "false"
      CLOUDFLARE_API_TOKEN: ""
      CLOUDFLARE_ACCOUNT_ID: ""
      CLOUDFLARE_FREE_TIER_CONFIRMED: "false"
      COHERE_API_KEY: ""
      COHERE_EVALUATION_CONFIRMED: "false"
      NVIDIA_API_KEY: ""
      NVIDIA_PROTOTYPING_CONFIRMED: "false"
      OPENAI_API_KEY: ""
      ANTHROPIC_API_KEY: ""
      LITEAPI_API_KEY: ""
      LITEAPI_MODE: sandbox
      DUFFEL_ACCESS_TOKEN: ""
      SERPAPI_API_KEY: ""
      SERPAPI_FREE_TIER_CONFIRMED: "false"
      HOTELBEDS_API_KEY: ""
      HOTELBEDS_API_SECRET: ""
      HOTELBEDS_TEST_CONFIRMED: "false"
      RATEHAWK_KEY_ID: ""
      RATEHAWK_API_KEY: ""
      RATEHAWK_TEST_CONFIRMED: "false"
      TRAVELPAYOUTS_API_TOKEN: ""
      LLM_REASONING_ENABLED: "false"
      CORS_ORIGINS: http://localhost:8085,http://127.0.0.1:8085
```

Этот override учитывает добавляемое сейчас forwarding ZAI/CF из основного compose. Перед исполнением сравнить **все** итоговые envfields с актуальной ревизией: новая credential не должна случайно остаться унаследованной. Новые модели ZAI/CF можно оставить фиксированными defaults основного compose: безключи+falseflags и `LLM_PROVIDER=openrouter` не активируют их. Нет произвольных endpoint overrides, `env_file`, bindmount исходников, hostnetwork, extra_hosts или внешнегоPG.

Порт не добавлять обычным вторым элементом override: Compose может сохранить оба publishedports. В этом варианте основной `${APP_PORT:-8080}` получает **APP_PORT=8085 из очищенного окружения**; override `ports` вообще отсутствует. Если вместо этого понадобится полная замена ports, использовать `!override ["127.0.0.1:8085:8080"]` только на Compose≥2.24.4 и проверить итог. [Правила merge](https://docs.docker.com/reference/compose-file/merge/).

Перед `up` получить resolvedconfig только в очищенном окружении и проверить: два services; buildcontext именноE; отдельный image tag; все credentials empty; все Free/test flagsfalse; appDBhost`db`; единственный publishedport8085loopback; уdb0publishedports; новое project-scoped хранилище. Если project/volume/tag уже существуют — сохранить факт и выбрать новый уникальный suffix, **не удалять их вслепую** ради freshPASS.

При необходимости запретить egress приложения отдельной `internal:true` defaultnetwork qualificationoverride. Это дополнительное ограничение, его включение и работоспособность hostloopbackport нужно отразить в результате; нельзя объявить его свойством базового compose. Для DEMO не вызывать даже доступный безключа Open-Meteo.

## Исполнение корнем после checkpoint

После preflight одна фактическая Compose-команда в очищенном окружении: `docker compose ... up --build -d --wait` с указанными project/files/envfile. Не подменять её ручным созданием SQLтаблиц, установкой пакетов или запуском uvicorn вне образа. Сохранить время, exitcode, immutable appimageID, PGimageID, labels/volumecreation и readiness. Начальный build требует доступности registry/PyPI; сетевой отказ отделять от дефекта приложения. Текущий store создаёт начальную schema при startup через `create_all`: это не доказательство миграции старой БД.

Проверки по настоящему HTTP на8085, без mocks:

1. `/health`:200, `database:ready`, `supplier_booking:false`. `/debug` и оба assets загружаются; при желании browser smoke без modelcalls. `/api/v1/providers/status`: OpenRouterвыбран, LLM/travelcredentials не настроены, Freeflagsfalse. Open-Meteo может показывать `configured:true`, потому что ключа не требует; это не факт его вызова.
2. `POST /api/v1/sessions` с `{}`:201; token держать только в памяти runner. GETбезtoken/с чужимtoken запрещён. Авторизованный GET возвращает созданную поездку, без DBpassword/tokenhash.
3. `POST /api/v1/sessions/{id}/demo` с уникальным `client_message_id` и фактической `expected_version`; boundedpoll `/api/v1/runs/{run_id}` до terminal. Получить планы и актуальную sessionversion. Все предложенияDEMО; опорный пример —9путешественников,2проживания,3номера в каждом. Проверить, что DEMOобработка не требовала модели/поставщика.
4. Выбрать реально возвращённый валидный `plan_id`. `POST .../checkout/preview` с `plan_id,state_version`; сохранить полученные `quote_id,quote_version` и сроки. `POST .../checkout/confirm` с этими значениями, текущей `state_version`, новым `idempotency_key`, `confirmed:true`. Ожидается один order: `is_demo:true`, `booking_created_at_supplier:false`. Повтор того же confirmkey не создаёт второй order. Никаких supplierorders/payments.
5. Снять безопасный snapshot trip/version/orders, создать отдельную pausedsession без LLM. Перезапустить **только** собственный appcontainer через те же project/files/env; дождаться health. Те же токены в памяти позволяют прочитать прежние sessions, trip/version/pause/order сохранены; повтор confirm в пределах допустимых условий не создаёт дубль.
6. Отдельно чисто перезапустить только собственный dbcontainer, затем дождаться health и повторить чтение trip/version/pause/orders. Это проверка обычного containerrestart с тем же volume, не disk-loss/PGSIGKILL/distributedrecovery.
7. Опционально отдельной session проверить явную ошибку отсутствующего LLMключа и сохранение trip при реплике, действительно дошедшей до modelstep. Успех детерминированного уточнения не доказывает этот отказ; учитывать внутреннюю попытку отдельно от фактического HTTPвызова. Свободный NLUбезключа не обещается.

## Доказательства и пределы

Отчёт — ignored `.local/evaluation/cleanstart-20261026/`: code/contextSHA до/после, resolvedconfig безсекретов, engine/project/имиджи/volume, команды безcredentials, exitcodes, HTTPassertions и результаты restart. Bearertokens/cookies/Authorization не сохранять. Основные8081/8083/8084, их code/image/volume и hostSQL не затрагиваются. Проверить их идентичность до/после без новых modelcalls.

PASS допускается только для фактически выполненного freshvolume→boot→DEMO→simulatedorder→ownrestart пути. Отдельно остаются liveAPI/LLMкачество, миграции, другойCPU/OS/архитектура, запуск без интернета при пустом imagecache и окончательный frontend. Qualificationoverride не является доказательством, что README-команда без этих настроек уже проверена на любой машине. Чистку не включать в стартовую команду; после сохранения результатов допускается убрать только подтверждённые собственные projectresources, без prune/globalWSLshutdown/удаления чужихvolumes.

## Отдельные результаты исполнения

Attempt1FAIL optionalinternalnetworkизтестовогоoverride мешалаlocalhostдоступу,приложение/dbбылиDockerhealthy; directbridgehealth200. ИсходныйFAILсохранён,этонебагосновнойbridgeконфигурации. Freshattempt2PASSordinarybridge/newvolume/samecb0source:DEMOquote/order/idempotence+ownapp/dbrestartссохранениемданных. Freshattempt3PASSnewproject/newvolume:ровноcomposeup--build--waitизархиваcb0,11checksи0model/external/toolcalls; restartнеповторялся(покрытattempt2). ВсеowncontainersубраныDOWNбез-v;volumes/imagesсохранены;old8081/8083/8084identitiesнеизменены. См.docs/verification/clean-start-20261026{,-attempt2,-attempt3}.json. Этиотчётынеподменяютдругдруга.
