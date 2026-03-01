# 💳 Payment Platform — PIX Webhooks

Plataforma de pagamentos desenvolvida para **simular um fluxo real de cobranças e confirmações via PIX**, utilizando **webhooks assinados**, **Redis como fonte de verdade**, **idempotência**, **rate limit**, **observabilidade cross-service** e um **Fake Bank Service** para integração completa.

O projeto tem foco **educacional e de portfólio**, demonstrando **como sistemas de pagamento funcionam em produção**, indo além de CRUDs simples.

---

## 🚀 Visão Geral

* Tipo: **API REST**
* Domínio: **Pagamentos / PIX / Webhooks**
* Modelo: **Confirmação assíncrona via webhook**
* Cenário real: e-commerce, SaaS, marketplaces, PSPs
* Integração: **Payment API ↔ Fake Bank Service**

---

## 🏗️ Arquitetura (Visão de Produto)

```text
┌──────────────┐        Webhook (HMAC)
│ Fake Bank    │ ─────────────────────▶ │ Payment Charges API │
│ Service      │                         │                     │
└──────────────┘                         └─────────────────────┘
        ▲                                             │
        │                                             │
        └─────────── PIX Payment Flow ────────────────┘
```

### Fluxo completo

1. Cliente cria uma cobrança (`POST /payment/charges`)
2. Cobrança é registrada no Fake Bank
3. Fake Bank processa o pagamento PIX
4. Fake Bank envia **webhook assinado**
5. API valida assinatura + timestamp + idempotência
6. Cobrança é marcada como **PAID**

---

## 🧠 Conceitos de Produção Implementados

* Webhooks assinados (**HMAC SHA-256**)
* Proteção contra replay attacks (**timestamp + tolerance window**)
* Idempotência de eventos via Redis
* Redis como fonte de verdade para expiração (TTL)
* Rate limit em endpoints sensíveis
* Observabilidade cross-service (`X-Request-Id`)
* Logs estruturados e auditáveis
* Retry + exponential backoff no Fake Bank
* Separação clara por camadas e responsabilidades

> Modelo inspirado em provedores como **Stripe, Mercado Pago e OpenPix**.

---

## 🛠️ Tecnologias

* **Python 3.12**
* **Flask**
* **Flask SQLAlchemy**
* **SQLite** (ambiente local)
* **Redis**
* **Docker / Docker Compose**
* **Postman**
* **OpenAPI 3.0**

---

## 📂 Estrutura do Projeto

```text
payment-platform/
├── payment-charges-api/
│   ├── app.py
│   ├── routes/
│   │   ├── charges.py
│   │   └── webhooks.py
│   ├── services/
│   ├── repository/
│   ├── security/
│   ├── infrastructure/
│   ├── audit/
│   ├── instance/
│   └── requirements.txt
│
├── fake-bank-service/
│   ├── app.py
│   ├── routes/
│   ├── services/
│   ├── clients/
│   ├── security/
│   └── requirements.txt
│
├── openapi.yaml
├── docker-compose.yml
└── README.md
```

---

## ⚡ Quickstart (60 segundos)

### Pré-requisitos

* Docker
* Docker Compose

### Subir todo o sistema

```bash
docker compose up --build
```

Serviços disponíveis:

* Payment API → `http://localhost:5000`
* Fake Bank → `http://localhost:6000`

---

## 🔁 Fluxo Completo (Exemplo Real)

### 1️⃣ Criar cobrança

```bash
curl -X POST http://localhost:5000/payment/charges \
  -H "Content-Type: application/json" \
  -H "X-Request-Id: demo-001" \
  -d '{"value":100.0}'
```

Resposta:

```json
{
  "id": 1,
  "external_id": "uuid-gerado",
  "status": "PENDING"
}
```

---

### 2️⃣ Registrar cobrança no Fake Bank

```bash
curl -X POST http://localhost:6000/bank/pix/charges \
  -H "Content-Type: application/json" \
  -H "X-Request-Id: demo-001" \
  -d '{
    "external_id":"uuid-gerado",
    "value":100.0,
    "webhook_url":"http://payment-charges-api:5000/webhooks/pix"
  }'
```

---

### 3️⃣ Processar pagamento PIX

```bash
curl -X POST http://localhost:6000/bank/pix/pay \
  -H "Content-Type: application/json" \
  -H "X-Request-Id: demo-001" \
  -d '{"external_id":"uuid-gerado"}'
```

O Fake Bank dispara o webhook automaticamente.

---

### 4️⃣ Consultar status final

```bash
curl http://localhost:5000/payment/charges/1 \
  -H "X-Request-Id: demo-001"
```

```json
{
  "id": 1,
  "value": 100.0,
  "status": "PAID",
  "expires_at": "2026-01-24T12:34:56"
}
```

---

## 🔐 Exemplo Real de Webhook (Fake Bank → API)

### Headers

```text
X-Signature: sha256=...
X-Timestamp: 1700000000
X-Event-Id: evt_xxx
X-Request-Id: demo-001
```

### Body

```json
{
  "event_id": "evt_xxx",
  "external_id": "uuid-gerado",
  "value": 100.0,
  "status": "PAID"
}
```

---

## 📜 OpenAPI

* Payment Charges API: `payment-charges-api/openapi.yaml`
* Fake Bank Service: `fake-bank-service/openapi.yaml`
* Define endpoints, payloads, headers e erros
* Pode ser usado para:

  * Swagger UI
  * Geração de clientes
  * Integrações externas

---

## 🧪 Testes

* Testes manuais via Postman
* Cenários cobertos:

  * Webhook válido
  * Webhook duplicado (idempotência)
  * Webhook expirado
  * Assinatura inválida
  * Rate limit excedido

---

## 📌 Próximos Passos

* [ ] Métricas (Prometheus)
* [ ] Migração para PostgreSQL
* [ ] Deploy em ambiente cloud

---

## 👨‍💻 Autor

**Yago Félix**  

💼 Desenvolvedor Python — Back-end | Full Stack  
🔍 Focado em APIs, automação e sistemas distribuídos

GitHub: https://github.com/yagofelix00  
LinkedIn: https://www.linkedin.com/in/yago-felix-737011279/

---



