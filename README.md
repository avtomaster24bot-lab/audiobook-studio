# Аудиокнига-студия

Сервис, который превращает книгу на русском языке в многоголосую аудиокнигу: находит текст, выделяет
персонажей и их реплики, подбирает каждому свой голос, расставляет эмоции, подкладывает фоновую музыку
по сценам с автоматическим приглушением под речью (ducking), нормализует громкость до −16 LUFS
и отдаёт MP3 (320 kbps) и M4B с главами.

**Демо:** <https://avtomaster24bot-lab.github.io/audiobook-studio/> — А. П. Чехов, «Хамелеон», полностью (6:45).
Файл в репозитории: [`docs/demo/book.mp3`](docs/demo/book.mp3).

## Возможности

| Этап | Что делает | Где в коде |
|---|---|---|
| Источник | Поиск по названию/автору в Викитеке и Project Gutenberg, загрузка по ссылке (HTML-страница, оглавление, прямой EPUB/FB2/PDF/TXT), по ISBN (метаданные Open Library), загрузка файла EPUB / FB2 / TXT / DOCX / PDF (OCR через Tesseract, если установлен). Соблюдает `robots.txt` и задержку между запросами, распознаёт платные «ознакомительные фрагменты». | `app/sources/` |
| Нормализация | Кодировки (UTF-8 / CP1251 / KOI8-R), «кракозябры», тире, кавычки, пробелы; разбиение на главы. | `normalize.py`, `text/structure.py` |
| Анализ | Реплики и ремарки («— сказал Иван»), персонажи с учётом склонений (pymorphy3), роли-синонимы («надзиратель» = Очумелов), обращения, чередование собеседников, пол. Эмоции реплик и настроение сцен. Rule-based режим работает без ключей; при наличии `OPENAI_API_KEY` результат уточняет LLM. | `app/text/` |
| Голоса | 15 голосов: Silero v4 (локально, бесплатно), Microsoft Edge TTS (онлайн, бесплатно), ElevenLabs (по ключу). Голос закрепляется за персонажем на всю книгу; вариации по высоте/темпу для разных возрастов. Эмоции → темп и тон (rubberband с сохранением формант). Кэш синтеза по хэшу. | `app/tts/` |
| Музыка | 6 настроений (calm, tension, action, sadness, romance, light), бесшовные лупы, кроссфейды 3 с между сценами. | `app/audio/mixer.py`, `music/` |
| Ducking | `sidechaincompress` в ffmpeg: музыка в паузах ≈ −12 dB от голоса, под речью ≈ −20 dB и ниже. Отчёт об уровнях в каждой задаче. | `mixer.py` |
| Экспорт | Двухпроходный `loudnorm` до −16 LUFS, true peak −1.5 dBFS, MP3 320k с тегами, M4B с главами. | `app/audio/export.py` |
| Сервис | REST API (FastAPI, Swagger на `/docs`), очередь задач с прогрессом и ETA, дедупликация (идемпотентность), вебхуки, перегенерация глав, удаление данных, метрики Prometheus `/metrics`, веб-интерфейс. | `app/main.py`, `app/jobs.py`, `static/` |

## Быстрый старт

Нужны Python 3.11+ и ffmpeg (с фильтрами `rubberband`, `sidechaincompress`, `loudnorm` — есть в сборках Debian/Ubuntu).

```bash
git clone https://github.com/avtomaster24bot-lab/audiobook-studio.git
cd audiobook-studio
python3 -m venv .venv && source .venv/bin/activate
pip install torch --index-url https://download.pytorch.org/whl/cpu   # Silero, CPU-сборка
pip install -r requirements.txt
cp .env.example .env        # при желании впишите ключи
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Откройте <http://localhost:8000> — веб-интерфейс, <http://localhost:8000/docs> — документация API.
При первом запуске Silero скачает модель (~100 МБ) в `~/.cache/torch/hub`.

Docker:

```bash
docker build -t audiobook-studio .
docker run -p 8000:8000 -v $PWD/data:/data --env-file .env audiobook-studio
```

## Демо из командной строки

```bash
python scripts/demo.py "Хамелеон Чехов" --edits scripts/demo_hameleon_edits.json --out docs/demo
python scripts/verify_ducking.py data/jobs/demo-<book_id>/work docs/demo/book.mp3 --svg docs/demo/ducking.svg
```

`--edits` — пример ручной правки атрибуции и голосов (то же, что `PATCH /api/books/{id}/analysis` и
экран «Правка атрибуции» в интерфейсе). `--preview` — озвучить только первые ~40 секунд.

### Результаты демо («Хамелеон», 908 слов, 6:45)

| Метрика | Значение |
|---|---|
| Музыка под речью относительно голоса (медиана) | −21.7 dB (5-й перцентиль −8.2 dB) |
| Музыка в паузах относительно голоса | −12.5 dB |
| Глубина ducking (пауза → речь) | 9.3 dB |
| Доля кадров речи, где музыка громче голоса | 0.13 % |
| Итоговая громкость | −16.0 LUFS, true peak −1.5 dBFS, LRA 4.0 LU |
| Атрибуция реплик без LLM | 30 / 36 (83 %), 6 поправлено вручную |
| Время генерации (CPU, 4 потока) | ≈ 1.5 мин на 6.7 мин аудио; 47 с повторно (кэш TTS) |

Разборчивость проверена распознаванием речи (Whisper) первых 40 секунд итогового MP3 поверх музыки:
текст распознаётся дословно.

## API (основное)

```bash
# 1. Найти и разметить книгу (без озвучки) — посмотреть персонажей и голоса
curl -X POST localhost:8000/api/books/resolve -F query="Хамелеон Чехов"

# 2. Поправить атрибуцию/голоса (необязательно)
curl -X PATCH localhost:8000/api/books/<book_id>/analysis -H 'Content-Type: application/json' \
     -d '{"speakers":{"0:24":"Голос из толпы"},"voices":{"Очумелов":"silero-aidar-low"}}'

# 3. Запустить озвучку (или сразу по query / url / isbn)
curl -X POST localhost:8000/api/jobs -H 'Content-Type: application/json' \
     -d '{"book_id":"<book_id>","settings":{"preview":false,"formats":["mp3","m4b"],"music":true}}'

# Загрузить файл
curl -X POST localhost:8000/api/jobs/upload -F file=@book.fb2 -F 'settings={"preview":true}'

# 4. Статус и результат
curl localhost:8000/api/jobs/<job_id>
curl -O localhost:8000/api/jobs/<job_id>/files/book.mp3
```

Также: `GET /api/voices`, `GET /api/voices/{id}/preview`, `GET /api/music`, `POST /api/jobs/{id}/regenerate`,
`DELETE /api/jobs/{id}`, `GET /api/health`, `GET /metrics`. Параметр `webhook_url` — POST по завершении задачи.

## Настройки

Все параметры — переменные окружения, см. [`.env.example`](.env.example):
`OPENAI_API_KEY` / `OPENAI_BASE_URL` / `OPENAI_MODEL` (любой OpenAI-совместимый API, включая локальные Ollama/vLLM),
`TTS_ENGINES`, `NARRATOR_VOICE`, `ELEVENLABS_API_KEY` + `ELEVENLABS_VOICES`, `TARGET_LUFS`, `MUSIC_GAIN_DB`,
`MP3_BITRATE`, `CRAWL_DELAY_SEC`, `MAX_WORDS`, `WORKERS`.

## Тесты

```bash
pytest -q
```

20 тестов: нормализация и кодировки, главы и реплики, атрибуция (в т.ч. базовая точность на «Хамелеоне»),
FB2/EPUB/TXT-парсеры, каталог голосов, сайдчейн-ducking на синтетическом сигнале. CI — GitHub Actions.

## Ограничения

- **Атрибуция без LLM** — эвристики; на сложных сценах с толпой точность ~80–85 %. Для ≥90 % задайте `OPENAI_API_KEY`
  (или локальную модель через `OPENAI_BASE_URL`) и/или правьте реплики в интерфейсе.
- **Голоса.** Silero v4 даёт 5 базовых русских голосов (2 мужских, 3 женских); остальные — их варианты по высоте и темпу,
  поэтому в больших составах персонажи могут звучать похоже. Edge TTS требует интернета и иногда недоступен —
  тогда реплика синтезируется Silero (с предупреждением). ElevenLabs — платно, по ключу.
- **Эмоции** передаются темпом, высотой и громкостью, а не «актёрской игрой» — открытые русские TTS не умеют
  управлять интонацией напрямую.
- **Источники.** Только легальные открытые источники (Викитека, Project Gutenberg, публичные страницы).
  Платные и защищённые книги не скачиваются — загрузите свой файл. OCR PDF — только при установленных
  `tesseract` (+`rus`) и `pdftoppm`.
- **Производительность.** В демо генерация шла примерно в 4 раза быстрее реального времени (CPU, рассказчик — Edge TTS);
  длинный роман на чистом Silero/CPU займёт часы — для больших книг нужен GPU или несколько воркеров (`WORKERS`).
- **Музыка** — процедурные лупы (CC0); для «студийного» звучания положите свои треки `music/<настроение>.mp3`.

## Лицензия

Код — MIT. Музыка в `music/` сгенерирована `scripts/generate_music.py` и передана в общественное достояние (CC0).
Текст «Хамелеона» (1884) — общественное достояние.
