# 💳 Payment Charges API

API responsável por **criação de cobranças** e **confirmação de pagamentos via webhook PIX**.
Este serviço **não confirma pagamentos por chamada direta** — a confirmação ocorre **exclusivamente via webhook assinado**, simulando integração real com um banco/PSP.

---

## 🎯 Responsabilidade do serviço

* Criar cobranças (`PENDING`)
* Controlar expiração via **Redis TTL**
* Receber webhooks assinados do banco
* Validar segurança, idempotência e integridade
* Atualizar cobrança para `PAID` ou `EXPIRED`
* Expor consulta de status da cobrança

---

## 🧠 Conceitos aplicados

* Webhooks assinados (**HMAC SHA-256**)
* Proteção contra replay attack (**timestamp + tolerance window**)
* **Idempotência HTTP** por `Idempotency-Key` com fingerprint da requisição (Redis)
* **Deduplicação de evento** por `event_id` (Redis)
* **Redis como fonte de verdade** para expiração
* Rate limiting em endpoints sensíveis
* Observabilidade com **X-Request-Id**
* Logs estruturados com auditoria

---

## 🛠️ Tecnologias

* Python 3.12
* Flask
* Flask SQLAlchemy
* SQLite (ambiente local)
* Redis
* Docker

---

## 📂 Estrutura do Serviço

```text
payment-charges-api/
├── app.py                    # Flask app factory / bootstrap
├── extensions.py             # Limiter, etc (extensões Flask)
├── requirements.txt
├── .env
│
├── routes/                   # Camada HTTP (controllers)
│   ├── charges.py            # POST /charges, GET /charges/{id}
│   └── webhooks.py           # POST /webhooks/pix
│
├── services/                 # Regras de negócio
│   └── charge_service.py     # Expiração, validações, helpers
│
├── db_models/                # Models SQLAlchemy (Charge, enums)
│   └── charges.py
│
├── repository/               # Banco / ORM setup
│   └── database.py           # db = SQLAlchemy()
│
├── security/                 # Segurança (camada transversal)
│   ├── auth.py               # API key (quando aplicável)
│   ├── idempotency.py        # Idempotência via Redis (Idempotency-Key + fingerprint)
│   └── webhook_signature.py  # HMAC + timestamp validation
│
├── infrastructure/           # Integrações externas (Redis etc.)
│   └── redis_client.py
│
├── audit/                    # Observabilidade e auditoria
│   ├── logger.py             # Logger com request_id
│   └── request_context.py    # Init/get request_id (X-Request-Id)
│
├── instance/                 # SQLite (database.db) e arquivos locais
│   └── database.db
│
└── logs/                     # Logs persistidos (audit.log, etc.)
    └── audit.log
```

---

### 🧩 Convenção de Camadas

Este serviço segue uma separação clara de responsabilidades:

- **routes/**  
  Camada HTTP. Responsável apenas por:
  - receber requests
  - validar payloads básicos
  - devolver responses  
  (sem regra de negócio)

- **services/**  
  Camada de domínio. Contém:
  - regras de negócio
  - validações de fluxo
  - decisões de estado (PENDING → PAID / EXPIRED)

- **security/**  
  Camada transversal de segurança:
  - validação de webhooks (HMAC + timestamp)
  - idempotência por `Idempotency-Key` e fingerprint
  - autenticação quando aplicável

- **infrastructure/**  
  Integrações externas e recursos de suporte:
  - Redis (TTL, cache, idempotência)
  - clientes externos

- **audit/**  
  Observabilidade e auditoria:
  - logging estruturado
  - correlation ID (`X-Request-Id`)

---

## 📦 Variáveis de Ambiente

Arquivo `.env`:

```env
FLASK_ENV=development
SECRET_KEY=your-secret-key

# Webhook
WEBHOOK_SECRET=super-secret-webhook-key

# Redis
REDIS_URL=redis://redis:6379/0
```

---

## ▶️ Como rodar isoladamente

### Sem Docker

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
flask run
```

A API ficará disponível em:

```
http://localhost:5000
```

---

### Com Docker (recomendado)

Execute a partir da **raiz do projeto**:

```bash
docker compose up payment-charges-api
```

---

## 🔗 Endpoints principais

### Criar cobrança

```
POST /charges
```

Payload:

```json
{
  "value": 100.0
}
```

Resposta:

```json
{
  "id": 1,
  "external_id": "uuid",
  "status": "PENDING"
}
```

---

### Consultar cobrança

```
GET /charges/{id}
```

Resposta:

```json
{
  "id": 1,
  "value": 100.0,
  "status": "PAID",
  "expires_at": "2026-01-24T12:34:56"
}
```

---

### Webhook PIX (recebido do banco)

```
POST /webhooks/pix
```

#### Headers obrigatórios

```
X-Signature: sha256=...
X-Timestamp: <unix-seconds>
Idempotency-Key: idem_xxx
X-Event-Id: evt_xxx  # opcional; event_id também deve estar no body
```

#### Body

```json
{
  "event_id": "evt_xxx",
  "external_id": "uuid",
  "value": 100.0,
  "status": "PAID"
}
```

---


### Idempotência HTTP e fingerprint

O webhook exige `Idempotency-Key` para proteger retries da mesma operação HTTP.
Para cada chave, a API armazena a resposta junto de um fingerprint SHA-256 calculado com:

- método HTTP;
- path;
- query string bruta;
- raw body da requisição.

Se a mesma `Idempotency-Key` for reutilizada com o mesmo fingerprint, a resposta armazenada é reproduzida com o mesmo body e status.
Se a mesma chave for reutilizada com outro método, path, query string ou raw body, a API rejeita a chamada sem executar a view:

```json
{
  "error": "Idempotency-Key reused with different request"
}
```

Status HTTP: `409 Conflict`.

Essa proteção é diferente da deduplicação por `event_id`: `Idempotency-Key` protege o replay HTTP de curto prazo; `event_id` identifica o evento de webhook no domínio e evita processamento duplicado.

---

## 🔐 Segurança do Webhook

* Assinatura HMAC baseada no **raw body**
* Validação de timestamp (tolerance window)
* Proteção contra retries HTTP com `Idempotency-Key` + fingerprint
* Proteção contra eventos duplicados por `event_id`
* Webhooks inválidos são rejeitados com status **401 / 400**
* Reutilização de `Idempotency-Key` com requisição diferente é rejeitada com status **409**

> Inspirado em implementações reais de provedores como **Stripe** e **Mercado Pago**.

---

## 📜 Documentação OpenAPI

* Contrato oficial da API: `openapi.yaml`
* Define endpoints, schemas, headers e erros
* Pode ser usado para Swagger UI ou geração de clientes

---

## 🧪 Status do projeto

* Testes automatizados: ⏳ pendente
* Integração com Fake Bank: ✅ completa
* Fluxo de pagamento assíncrono: ✅ funcional

---

## 📌 Observação importante

Este serviço **não expõe endpoints para “confirmar pagamento manualmente”**.
A confirmação ocorre **somente via webhook**, simulando comportamento real de sistemas financeiros.

---


