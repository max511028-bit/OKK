# 02 · Бэкенд-API — справочник эндпоинтов

> Обновлено: 2026-07-13. Сгенерировано по `tasks/api/main.py` (колонка «строка» —
> место в коде). **Добавил/изменил эндпоинт → добавь/поправь строку здесь.**

Всего эндпоинтов: 116. Один файл `tasks/api/main.py`, FastAPI + SQLite.

## Авторизация (3 уровня)

| Уровень | Как | Проверка в коде |
|---|---|---|
| Открыто | без ничего | — (read-only данные внутреннего портала) |
| Пароль портала | `X-Auth-Token` (portal-токен) или `?password=` | `_vcs_check_password()` |
| Пароль админки | admin-токен | `_check_admin()` / аналоги |

Токены — HMAC от `auth_secret.bin`, выдаются `POST /auth/check`.
Агент обзвона авторизуется портальным паролем из `voicecall.env`.

## Обзвон (voicecall) — 🔥 ядро, детали в 04-ГОЛОСОВОЙ-АГЕНТ.md (36)

| Метод | Путь | Функция | Строка | Что делает |
|---|---|---|---|---|
| GET | `/voicecall/upload-template` | vc_upload_template | 3920 | Excel-шаблон под конкретный сценарий: Имя, Телефон, и по одной |
| POST | `/voicecall/manual-entry` | vc_manual_entry | 3945 | JSON-аналог /voicecall/upload-contacts — те же Имя/Телефон/ |
| POST | `/voicecall/preview-contacts-file` | vc_preview_contacts_file | 4093 |  |
| POST | `/voicecall/upload-contacts` | vc_upload_contacts | 4124 |  |
| GET | `/voicecall/campaigns` | vc_list_campaigns | 4190 |  |
| GET | `/voicecall/contacts` | vc_list_contacts | 4224 |  |
| POST | `/voicecall/contacts/{cid}/skip` | vc_skip_contact | 4262 |  |
| POST | `/voicecall/contacts/{cid}/retry` | vc_retry_contact | 4272 |  |
| GET | `/voicecall/contacts/{cid}/detail` | vc_contact_detail | 4285 | Полная карточка контакта для модалки на портале: всё что знаем — |
| GET | `/voicecall/contacts/{cid}/live` | vc_contact_live | 4331 | Живой транскрипт текущего звонка (пока status='calling') — агент |
| DELETE | `/voicecall/campaigns/{cid}` | vc_delete_campaign | 4339 |  |
| POST | `/voicecall/campaigns/{cid}/start-dispatch` | vc_start_dispatch | 4357 | Кнопка «Начать обзвон» на портале — просто ставит кампании флаг, |
| POST | `/voicecall/campaigns/{cid}/pause-dispatch` | vc_pause_dispatch | 4387 | «Пауза» — текущий звонок (если идёт) доигрывается до конца, но |
| POST | `/voicecall/campaigns/{cid}/resume-dispatch` | vc_resume_dispatch | 4401 | «Продолжить» после паузы — агент снова начинает забирать pending- |
| GET | `/voicecall/agent-status` | vc_agent_status | 4434 | Открытый индикатор для портала: жив ли агент обзвона на ПК. |
| GET | `/voicecall/dispatch/poll` | vc_dispatch_poll | 4451 | Агент опрашивает это раз в 15-30 сек. Атомарно забирает САМУЮ |
| POST | `/voicecall/dispatch/claim` | vc_dispatch_claim | 4493 | Атомарно забирает самый старый (сверху вниз в списке на портале) |
| POST | `/voicecall/dispatch/live` | vc_dispatch_live | 4599 | Агент шлёт сюда снимок транскрипта по ходу звонка (не только в |
| POST | `/voicecall/dispatch/result` | vc_dispatch_result | 4609 | Агент шлёт сюда результат реального звонка. Каждая попытка (даже |
| POST | `/voicecall/dispatch/recording` | vc_dispatch_recording | 4688 | Агент шлёт сюда ссылку на запись разговора отдельным (не блокирующим |
| POST | `/voicecall/dispatch/recheck-transcript` | vc_dispatch_recheck_transcript | 4716 | Агент шлёт сюда текст ПОВТОРНОГО распознавания разговора — по |
| GET | `/voicecall/campaigns/{cid}/funnel` | vc_campaign_funnel | 4808 | Воронка обзвона по этапам: загружено/отсеяно → попытки дозвона → |
| GET | `/voicecall/campaigns/{cid}/suspect-voicemails` | vc_campaign_suspect_voicemails | 4860 | Пункт 3 доработок 2026-07: конвейер пополнения базы фраз |
| GET | `/voicecall/campaigns/{cid}/export` | vc_campaign_export | 4916 | Отчёт .xlsx: Имя/Телефон/Статус/точная причина/Вердикт/причина стопа |
| POST | `/voicecall/test/start` | vc_test_start | 5033 |  |
| POST | `/voicecall/test/{sid}/answer` | vc_test_answer | 5106 | Принимает уже распознанный текст ответа, продвигает диалог, |
| GET | `/voicecall/test/summary/{validation_id}` | vc_test_get_summary | 5226 | Возвращает summary конкретного завершённого звонка. Polling-эндпоинт |
| GET | `/voicecall/tts` | vc_tts | 5367 |  |
| GET | `/voicecall/silero-status` | vc_silero_status | 5415 | Проверка что Silero-сервер на ПК пользователя доступен через туннель. |
| GET | `/voicecall/scripts` | vcs_list | 5735 | Список всех скриптов (для библиотеки и для выбора при обзвоне). |
| GET | `/voicecall/scripts/{sid}` | vcs_get | 5753 | Полный скрипт со всеми шагами. Открыто (нужно при обзвоне/тесте). |
| POST | `/voicecall/scripts` | vcs_create | 5773 | Создать новый скрипт. За паролем. |
| PUT | `/voicecall/scripts/{sid}` | vcs_update | 5800 | Обновить скрипт. За паролем. |
| POST | `/voicecall/scripts/{sid}/publish` | vcs_publish | 5827 | Опубликовать скрипт (draft → published). За паролем. |
| POST | `/voicecall/scripts/{sid}/copy` | vcs_copy | 5843 | Создать копию скрипта (новый draft). За паролем. |
| DELETE | `/voicecall/scripts/{sid}` | vcs_delete | 5869 | Удалить скрипт. За паролем. (Пока тесты — удаляем насовсем, решение #2) |

## Задачник (tasks) (12)

| Метод | Путь | Функция | Строка | Что делает |
|---|---|---|---|---|
| GET | `/tasks` | list_tasks | 595 |  |
| GET | `/tasks/trash` | list_trash | 605 | Список мягко-удалённых задач (для UI «корзина»). |
| GET | `/tasks/{tid}` | get_task | 615 |  |
| POST | `/tasks` | create_task | 624 |  |
| PUT | `/tasks/{tid}` | update_task | 641 |  |
| DELETE | `/tasks/{tid}` | delete_task | 667 | Удалить задачу. По умолчанию — мягко (deleted_at=сейчас, восстановимо 7 дней). |
| POST | `/tasks/{tid}/restore` | restore_task | 686 | Восстановить мягко-удалённую задачу. |
| POST | `/tasks/{tid}/attachments` | upload_attachment | 715 | Загружает файл и привязывает к задаче. Обновляет task.attachments. |
| GET | `/tasks/{tid}/attachments/{name}` | download_attachment | 767 | Отдаёт файл вложения. Открытый эндпоинт — портал и так за паролем. |
| DELETE | `/tasks/{tid}/attachments/{name}` | delete_attachment | 777 |  |
| POST | `/tasks/{tid}/comments` | add_comment | 801 | Добавить комментарий к задаче. Без авторизации (внутренний портал за паролем). |
| GET | `/tasks/{tid}/history` | task_history | 830 |  |

## AI-прокси (Ollama через туннель) (7)

| Метод | Путь | Функция | Строка | Что делает |
|---|---|---|---|---|
| GET | `/ai/url` | get_ai_url | 1866 | Вернуть текущий адрес Ollama (или null если оффлайн). |
| POST | `/ai/url` | set_ai_url | 1884 | Сохранить новый адрес Ollama (вызывается скриптом start-ai.ps1). |
| GET | `/ai/prompt/{dashboard}` | get_prompt | 1912 | Вернуть системный промпт для указанного дашборда (публичный). |
| GET | `/ai/health` | ai_health | 2343 | Доступность Ollama. Используется фронтом для плашки «ИИ оффлайн». |
| GET | `/ai/proxy/tags` | proxy_ai_tags | 2382 | Вернуть список моделей Ollama через прокси. |
| POST | `/ai/proxy/chat` | proxy_ai_chat | 2409 | Переслать запрос к Ollama /api/chat через прокси (non-streaming). |
| POST | `/ai/proxy/chat/stream` | proxy_ai_chat_stream | 2620 | Стриминг-прокси к Ollama /api/chat. Возвращает NDJSON (stream:true). |

## Админка (20)

| Метод | Путь | Функция | Строка | Что делает |
|---|---|---|---|---|
| POST | `/admin/tasks/purge-trash` | purge_trash | 698 | Физически удалить задачи, лежавшие в корзине больше N дней (по умолчанию 7). |
| GET | `/admin/prompts` | admin_get_prompts | 1976 | Вернуть все промпты. Принимает либо ?password=..., либо заголовок X-Auth-Token. |
| POST | `/admin/prompts` | admin_save_prompts | 1985 | Сохранить все промпты (требует пароль или токен). |
| GET | `/admin/analyst-canon` | admin_get_analyst_canon | 2000 | Вернуть всю канон-библиотеку аналитика (новые поля + legacy body для совместимос… |
| GET | `/admin/analyst-recipes` | admin_get_analyst_canon | 2001 | Вернуть всю канон-библиотеку аналитика (новые поля + legacy body для совместимос… |
| POST | `/admin/analyst-canon` | admin_save_analyst_canon | 2016 | Сохранить весь список (полная замена). Принимает {canon:[...]} или legacy {recip… |
| POST | `/admin/analyst-recipes` | admin_save_analyst_canon | 2017 | Сохранить весь список (полная замена). Принимает {canon:[...]} или legacy {recip… |
| POST | `/admin/analyst-canon/verify` | admin_verify_analyst_canon | 2036 | Пометить auto-сгенерированную запись как проверенную (или снять отметку). |
| DELETE | `/admin/analyst-canon/{entry_id}` | admin_delete_analyst_canon | 2062 | Удалить запись из канон-библиотеки. |
| GET | `/admin/ai-logs` | admin_ai_logs | 2076 |  |
| GET | `/admin/ai-logs/stats` | admin_ai_logs_stats | 2108 | Сводная статистика: всего запросов, токенов за сутки, по дашбордам, hit/miss кэш… |
| POST | `/admin/ai-smoke-run` | admin_ai_smoke_run | 2275 | Запуск smoke-теста. С localhost — без авторизации (для cron). |
| GET | `/admin/ai-test-runs` | admin_ai_test_runs | 2285 | История прогонов smoke. Группированно по run_id. |
| POST | `/admin/ai-cache/clear` | admin_clear_ai_cache | 2571 | Очистить весь кэш ИИ. Только админ. |
| GET | `/admin/ai-cache/stats` | admin_ai_cache_stats | 2582 | Статистика кэша ИИ для админки. |
| POST | `/admin/tunnel-cleanup` | admin_tunnel_cleanup | 5385 | Прибивает зомби-sshd процессы которые висят на портах туннеля |
| POST | `/admin/vosk-install` | admin_vosk_install | 5496 | Стартует скачивание Vosk-модели в фоне (не блокирует API). |
| GET | `/admin/vosk-install-log` | admin_vosk_install_log | 5526 | Лог фоновой установки Vosk. |
| POST | `/admin/vosk-upload` | admin_vosk_upload | 5538 | Принимает .zip с Vosk-моделью (vosk-model-small-ru-0.22.zip), |
| GET | `/admin/vosk-status` | admin_vosk_status | 5615 | Статус Vosk модели на VPS. |

## Рекрутер (8)

| Метод | Путь | Функция | Строка | Что делает |
|---|---|---|---|---|
| GET | `/recruiter/projects` | recruiter_projects | 2802 | Список проектов из Google Sheets (Roadmap). |
| GET | `/recruiter/tz` | recruiter_load_tz | 2813 | Загрузить ТЗ проекта из Google Sheets. |
| POST | `/recruiter/answer/stream` | recruiter_answer_stream | 2838 | Стриминг ответа на возражение кандидата. |
| POST | `/recruiter/chat/stream` | recruiter_chat_stream | 2897 | Стриминг уточняющего вопроса в диалоге рекрутера. |
| POST | `/recruiter/feedback` | recruiter_feedback | 2955 | Сохранить оценку варианта скрипта. |
| POST | `/recruiter/log` | recruiter_log | 2972 | Записать лог запроса в Google Sheets. |
| POST | `/recruiter/chat-log` | recruiter_chat_log | 2990 | Записать диалог рекрутера в Google Sheets. |
| GET | `/recruiter/status` | recruiter_status | 3001 | Проверить доступность Google API. |

## Аналитик (6)

| Метод | Путь | Функция | Строка | Что делает |
|---|---|---|---|---|
| GET | `/analyst/fetch` | analyst_fetch | 3146 | Прокси-загрузка CSV/XLSX по URL. |
| GET | `/analyst/gsheet` | analyst_gsheet | 3213 | Загрузить лист Google Sheets как JSON. |
| POST | `/analyst/projects` | analyst_projects_create | 3287 | Сохранить проект аналитика. Возвращает id + read_token для шеринг-ссылки. |
| GET | `/analyst/projects` | analyst_projects_list | 3309 | Список последних 50 проектов текущего владельца. |
| GET | `/analyst/projects/{pid}` | analyst_projects_get | 3327 | Открыть проект по id + read_token (без авторизации). Инкрементит view_count. |
| DELETE | `/analyst/projects/{pid}` | analyst_projects_delete | 3357 | Удалить проект. Только если owner совпадает или legacy (owner=NULL/'') |

## Чат с LLM (4)

| Метод | Путь | Функция | Строка | Что делает |
|---|---|---|---|---|
| GET | `/chat/list` | chat_list | 3032 | Список чатов (без сообщений), свежие сверху. |
| GET | `/chat/{chat_id}` | chat_get | 3043 | Один чат со всеми сообщениями. |
| POST | `/chat` | chat_save | 3056 | Создать новый чат или обновить существующий. |
| DELETE | `/chat/{chat_id}` | chat_delete | 3091 | Жёсткое удаление чата (без soft-delete: чаты пользовательские, простые). |

## Валидатор кандидатов (5)

| Метод | Путь | Функция | Строка | Что делает |
|---|---|---|---|---|
| POST | `/validator/result` | validator_save_result | 3440 |  |
| GET | `/validator/results` | validator_list_results | 3464 |  |
| POST | `/validator/transcribe` | validator_transcribe | 3491 |  |
| POST | `/validator/llm-classify` | validator_llm_classify | 3543 |  |
| POST | `/validator/llm-summary` | validator_llm_summary | 3613 |  |

## КП (3)

| Метод | Путь | Функция | Строка | Что делает |
|---|---|---|---|---|
| GET | `/kp` | list_kp | 884 |  |
| POST | `/kp` | save_kp | 906 |  |
| DELETE | `/kp/{kpid}` | delete_kp | 923 |  |

## ОКК (2)

| Метод | Путь | Функция | Строка | Что делает |
|---|---|---|---|---|
| GET | `/okk/files` | okk_files | 959 | Лёгкий справочник: ключ → google-id. Браузер использует чтобы построить URL gviz… |
| GET | `/okk/tabs/{key}` | okk_tabs | 965 | Список имён вкладок в одной из 5 OKK-таблиц. Кэш 5 минут. |

## Месячный отчёт (6)

| Метод | Путь | Функция | Строка | Что делает |
|---|---|---|---|---|
| GET | `/month/data` | month_get_data | 364 |  |
| PUT | `/month/data` | month_put_data | 369 |  |
| GET | `/month/norms` | month_get_norms | 376 |  |
| PUT | `/month/norms` | month_put_norms | 381 |  |
| GET | `/month/actions` | month_get_actions | 388 |  |
| PUT | `/month/actions` | month_put_actions | 393 |  |

## Продажи (2)

| Метод | Путь | Функция | Строка | Что делает |
|---|---|---|---|---|
| GET | `/sales/{collection}` | sales_get_collection | 409 |  |
| PUT | `/sales/{collection}` | sales_put_collection | 414 |  |

## Прочее (state, lists, auth, health) (5)

| Метод | Путь | Функция | Строка | Что делает |
|---|---|---|---|---|
| GET | `/state` | get_state | 548 |  |
| POST | `/health-snapshot` | save_health_snapshot | 570 | Снимок IT Health Score для тренда «к прошлой неделе» на дашборде. |
| PUT | `/lists/{name}` | update_list | 844 |  |
| GET | `/health` | health | 933 |  |
| POST | `/auth/check` | auth_check | 1948 | Проверить пароль и вернуть opaque-токен. |

## Кандидаты на ревизию (проверить в этап 4 — код-ревью)

| Эндпоинт | Подозрение |
|---|---|
| `GET/POST /ai/url` | наследие ngrok-эры (start-ai.ps1 писал сюда URL туннеля); сейчас адрес Ollama статичен (127.0.0.1:21434) — возможно, мёртвый механизм |
| `/admin/analyst-recipes` (2 шт) | легаси-алиасы `/admin/analyst-canon` — дубли той же функции |
| `/admin/vosk-*` (4 шт) | Vosk на VPS для validator/transcribe; проверить, жив ли сценарий (STT давно на ПК) |
| `POST /voicecall/contacts/{cid}/skip` | есть ли кнопка на портале? |
| `/month/*`, `/sales/*` | тонкие blob-хранилища; живы, но без авторизации на запись — проверить |

> Полная ревизия «нужен/не нужен» — отдельным решением владельца на этапе код-ревью.
