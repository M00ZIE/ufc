# UFC Analytics

Plataforma web **educacional** em **Python / Flask** focada **só em UFC**: lê o **site oficial** (HTML público), calcula probabilidades heurísticas e um **modelo MMA ponderado** (`mma_predict`), lista **eventos recentes** (scraping leve com cache) e oferece **apostas simuladas** com créditos fictícios (SQLite + sessão).

> **Não** é casa de apostas de verdade. Previsões e odds são aproximações para estudo; respeite os termos do site da UFC ao usar scraping ou cache de páginas.

---

## Sumário

- [Funcionalidades](#funcionalidades)
- [Stack e dependências](#stack-e-dependências)
- [Estrutura do repositório](#estrutura-do-repositório)
- [Como executar](#como-executar)
- [Interface web](#interface-web)
- [API HTTP](#api-http)
- [Análise de eventos (`ufc_event_analysis`)](#análise-de-eventos-ufc_event_analysis)
- [Modelo ponderado (`mma_predict`)](#modelo-ponderado-mma_predict)
- [Lista de eventos (`ufc_events`)](#lista-de-eventos-ufc_events)
- [Apostas demo (`betting/`)](#apostas-demo-betting)
- [Cache e arquivos locais](#cache-e-arquivos-locais)
- [Testes automatizados](#testes-automatizados)
- [Variáveis de ambiente](#variáveis-de-ambiente)
- [Ideias de extensão](#ideias-de-extensão)
- [Aviso legal](#aviso-legal)

---

## Funcionalidades

| Área | Descrição |
|------|-----------|
| **Card do evento** | Parse do HTML do evento (lutadores, divisão, fotos via proxy), probabilidades do modelo legado, métodos (KO/decisão/finalização), cenários, favorito. |
| **Modelo avançado** | Por luta: `advanced_prediction` com probabilidades ponderadas, tier de risco **SAFE / RISKY / SKIP**, componentes (striking, grappling, forma, etc.), value bet opcional. |
| **Eventos** | Lista em `ufc.com.br/events` com títulos (cards com `h3`), deduplicação, cache em disco (~2 h). |
| **Conta** | Cadastro, login (bcrypt), saldo demo, histórico de apostas. |
| **Apostas simuladas** | Odds derivadas do modelo + vig por tier; limite de stake; liquidação por admin ou chave API. |
| **Admin** | Usuários, apostas, ajuste de saldo/bloqueio, liquidação (quando `is_admin`). |

---

## Stack e dependências

| Pacote | Uso |
|--------|-----|
| **Flask** | Servidor web, templates Jinja2, sessões, JSON. |
| **requests** | HTTP para páginas UFC e imagens (proxy). |
| **beautifulsoup4** | Parse HTML dos eventos e perfis. |
| **bcrypt** | Hash de senhas (`betting/`). |

Arquivo: `requirements.txt`.

---

## Estrutura do repositório

```
ufc/
├── app.py                 # Flask: rotas principais, proxy de imagens, /api/analyze, /api/ufc/events
├── ufc_event_analysis.py # Análise completa do card (heurísticas, parse, integração mma_predict)
├── ufc_events.py         # Lista de eventos (ufc.com.br/events) + cache
├── ufc_external_context.py # Contexto externo opcional (RSS/Reddit, se configurado)
├── mma_predict/          # Modelo linear ponderado + features + batch
├── sports/                 # Registro de analisadores (apenas UFC)
│   ├── ufc_analyzer.py
│   ├── ufc_urls.py       # Allowlist de URLs de evento e imagens
│   └── ...
├── betting/              # Blueprint /api: auth, odds, apostas, admin
├── templates/            # index, login, register, bets, admin
├── static/               # CSS, JS (auth.js)
├── tests/                # unittest
└── instance/             # betting.sqlite3 (criado ao correr)
```

Pastas geradas em runtime (podem ir no `.gitignore`): `.ufc_html_cache/` (HTML de eventos e listagens).

---

## Como executar

```bash
pip install -r requirements.txt
python app.py
```

Por padrão o servidor sobe em **`http://127.0.0.1:5000`**.

---

## Interface web

| Rota | Conteúdo |
|------|----------|
| **`/`** | Home: escolha de evento (lista da API), URL manual opcional, análise do card, bloco do modelo ponderado, painel de apostas estilo casa de apostas. |
| **`/login`** | Entrar (redireciona para `next` se for path seguro). |
| **`/register`** | Cadastro (saldo inicial demo). |
| **`/bets`** | Histórico de apostas (precisa estar logado). |
| **`/admin`** | Painel admin (só `is_admin`). |

### Comportamento da home

- A análise **não** roda sozinha ao abrir a página (evita travar a tela). Escolha o evento e clique em **«Analisar»**.
- Para carregar o evento **automaticamente** ao abrir:  
  `http://127.0.0.1:5000/?autoload=1`
- Para **não** carregar: `?noload=1`

---

## API HTTP

Rotas principais (prefixos exatos abaixo).

### Núcleo da app (`app.py`)

| Método | Rota | Descrição |
|--------|------|-----------|
| GET | `/` | HTML da home. |
| GET | `/api/sports` | Lista esportes registrados (`ufc`) e o padrão. |
| GET | `/api/ufc/events` | Eventos recentes (`ok`, `events[]` com `title`, `url`). `?refresh=1` força novo fetch (ignora cache curto da lista). |
| GET | `/api/analyze` | JSON da análise. Query: `url` (evento UFC), `sport=ufc` (opcional), `refresh=1` para ignorar cache de 24 h do HTML do evento. |
| GET | `/api/proxy-image` | Proxy de imagens allowlist (parâmetro `url`). |
| GET | `/api/v1` | Descoberta da API (links úteis). |

### Apostas e auth (Blueprint `betting`, prefixo **`/api`**)

| Método | Rota | Descrição |
|--------|------|-----------|
| POST | `/api/auth/register` | Criar usuário (JSON: `email`, `password`). |
| POST | `/api/auth/login` | Login (JSON); define sessão. |
| POST | `/api/auth/logout` | Sair. |
| GET | `/api/auth/me` | Estado da sessão e dados do usuário (`balance`, `is_admin`, `blocked`, …). |
| GET | `/api/odds` | Odds agregadas para um evento: `?url=<URL do evento>`. |
| POST | `/api/bet` | Registrar aposta (JSON: `event_url`, `fight_index`, `side` red/blue, `stake`). |
| GET | `/api/bet/history` | Histórico do usuário (`?limit=`). |
| POST | `/api/bet/settle` | Liquidar vencedor da luta (admin ou `X-Settle-Key`). Body: `event_url`, `fight_index`, `winner_side`. |
| GET | `/api/admin/users` | Listar usuários (admin). |
| PATCH | `/api/admin/users/<id>` | Ajustar `balance` e/ou `blocked` (admin). |
| GET | `/api/admin/bets` | Listar apostas (`user_id`, `event_url`, `limit`). |
| POST | `/api/admin/settle` | Alias de liquidação (mesmas regras que `/api/bet/settle`). |

---

## Análise de eventos (`ufc_event_analysis`)

- Faz **fetch** do HTML do evento (URLs permitidas: domínios UFC oficiais — ver `sports/ufc_urls.py`).
- **Cache** em disco (arquivos hash por URL) com TTL configurável no analisador; parâmetro `refresh` força novo download.
- Extrai lutas, nomes, divisão, fotos, dados para heurísticas (record, posição no card quando existir).
- Produz JSON com `fights[]`, cada uma com `prob_red_pct` / `prob_blue_pct` (modelo legado), métodos, cenários, etc.
- Opcionalmente chama **`mma_predict`** por luta e preenche `advanced_prediction`.

Constante **`DEFAULT_URL`**: evento padrão quando não se passa URL (no topo de `ufc_event_analysis.py`).

---

## Modelo ponderado (`mma_predict`)

Modelo **independente** do logit legado: combina diferenças **striking**, **grappling**, **forma recente**, **físico/rank no card**, **consistência** com pesos fixos (ex.: 0,3 / 0,3 / 0,2 / 0,1 / 0,1), passa por **logística** e devolve probabilidades vermelho/azul.

- **Risco**: **SAFE**, **RISKY** ou **SKIP** (mercado fechado nas apostas da UI quando SKIP).
- Integrado em `predictor.py`; exposto no JSON como `weighted_model`, `risk`, `value_bet` opcional.

### Linha de comando (batch)

Arquivo com uma URL de evento por linha (`#` comenta a linha):

```bash
python -m mma_predict.batch_events --urls-file eventos.txt --out ufc_batch.jsonl
```

---

## Lista de eventos (`ufc_events`)

- Fonte: **`https://www.ufc.com.br/events`**.
- Parse de cards (título em **`h3`** + link `/event/...`), com fallback para links soltos.
- Usa o mesmo **`fetch_html`** que a análise (cache configurável em `fetch_events_list`).

---

## Apostas demo (`betting/`)

- **Banco de dados**: SQLite (padrão `instance/betting.sqlite3`, configurável com `BETTING_DB_PATH`).
- **Odds**: derivadas das probabilidades do modelo (ponderado quando existe) com **vig** — SAFE **1.08**, RISKY **1.12**; **SKIP** não permite aposta na regra de negócio.
- **Stake máximo**: fração do saldo (ver `betting/service.py` e `odds_math.py`).
- **Liquidação**: informa o vencedor real da luta para pagar ou perder apostas abertas.

---

## Cache e arquivos locais

| Caminho | Conteúdo |
|---------|----------|
| `.ufc_html_cache/` | HTML em cache (eventos, listagem; usado por `fetch_html` / `ufc_events`). |
| `instance/betting.sqlite3` | Usuários e apostas (sessão fica em cookie Flask, não no SQLite). |

---

## Testes automatizados

```bash
python -m unittest discover -s tests -v
```

Inclui testes de allowlist de URLs, registro de esportes, odds, `mma_predict`, parse de card, etc.

---

## Variáveis de ambiente

| Variável | Função |
|----------|--------|
| `FLASK_SECRET_KEY` | Chave secreta Flask (sessões). **Obrigatória em produção**; em dev existe um valor padrão inseguro. |
| `BETTING_DB_PATH` | Caminho alternativo para o arquivo SQLite de apostas. |
| `BETTING_ADMIN_EMAILS` | Lista separada por vírgulas: emails que recebem `is_admin=1` no **cadastro**. |
| `BETTING_SETTLE_KEY` | Se definida, permite `POST /api/bet/settle` com o header **`X-Settle-Key`** sem ser admin. |

---

## Ideias de extensão

- Outras fontes de dados (sempre respeitando termos legais).
- Treinar modelos a partir de `mma_predict/data_collection.py` ou histórico.
- Deploy com Gunicorn/uWSGI + HTTPS e `FLASK_SECRET_KEY` forte em produção.

---

## Aviso legal

Uso **educacional**. Não há garantia de acerto nas previsões; não use para aposta com dinheiro real. **UFC** e marcas relacionadas são de seus titulares — este projeto **não** é afiliado à UFC.
