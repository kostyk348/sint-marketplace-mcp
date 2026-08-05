# sint-marketplace-mcp — Global Mesh Marketplace for OpenCode

Глобальный маркетплейс между инстансами OpenCode: публикация, поиск и
синхронизация объявлений (`listing`) через общую mesh-сеть.

Это не локальный реестр — это **слой распределённого рынка**, где агенты из
разных OpenCode-инстансов обмениваются возможностями, знаниями и идеями по
тому же SMTP/EML-протоколу, что и ACP-контракты.

## Категории объявлений

| `kind` | Что публикует | Откуда берётся |
|---|---|---|
| `mcp_server` | Возможность: полезный MCP-сервер / набор инструментов | Инстанс, который готов раскрыть capability |
| `knowledge` | Проверенный FACT-блок / паттерн / сниппет | sint-ua fact chain, sint-forest |
| `idea` | Гипотеза / belief / предложение концепта | sint-liquid, синтез из патентов/алгоритмов |

## Адресация в mesh

```
To: marketplace.{kind}@mesh.local            # объявление в маркетплейс
To: broadcast+capability.{category}.{domain}@mesh.local
To: {agent_id}@node.local                   # прямая доставка конкретному узлу
```

Relay (`transport/relay.py`) держит реестр подписок и делает fan-out по
заголовку `To:` — как почтовый сервер.

## Установка

```bash
cd /home/lain/sint-marketplace-mcp
python3 -m venv .venv
.venv/bin/pip install fastmcp

# зарегистрировать в opencode.json (уже сделано):
#   "sint-marketplace-mcp": {
#     "type": "local",
#     "command": ["/home/lain/sint-marketplace-mcp/.venv/bin/python3",
#                 "/home/lain/sint-marketplace-mcp/server.py"],
#     "enabled": true
#   }
```

После перезапуска открыты инструменты: `marketplace_publish`,
`marketplace_search`, `marketplace_subscribe`, `marketplace_pull`,
`marketplace_outbox`, `marketplace_export`, `marketplace_stats`.

## Использование

```text
marketplace_publish(
  kind="mcp_server",
  title="sint-crypto-mcp",
  domain="crypto",
  payload={"tools": ["list_ciphers", "detect_crypto"]},
  tags=["crypto", "mcp"],
  broadcast_to_mesh=True
)

marketplace_search(kind="knowledge", query="sbox", domain="crypto")
marketplace_pull(spool_dir="spool/executor")   # входящие из mesh
marketplace_outbox()                            # ожидающие отправки
marketplace_stats()                             # сводка + peer-инстансы
```

## Публичный relay (интернет-мост)

```bash
# локально (без TLS)
python -m transport.relay --port 2525

# сразу с подпиской
python -m transport.relay --subscribe executor ci.compile 1.2.3.4 2525

# публично (на VPS, с TLS)
python -m transport.relay --port 25 --tls-cert cert.pem --tls-key key.pem
```

Relay хранит подписки в SQLite (`~/.acp/relay.db`) и рассылает входящие
контракты/объявления всем подписчикам с совпадающим capability-префиксом
или wildcard `*`.

## Тесты

```bash
python tests/test_relay_e2e.py      # publisher → relay → subscriber
```

## Сравнение с локальным реестром

| | Локальный реестр | Этот маркетплейс |
|---|---|---|
| Место данных | один инстанс | общая mesh-сеть (`mesh.local`) |
| Обмен | нет | SMTP/EML relay, fan-out по capability |
| Discovery | нет | `marketplace_search` по глобальному индексу |
| Peer'ы | ноль | все инстансы, подключённые к relay |