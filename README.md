# Ã°Å¸â€™Â³ Payment Platform Ã¢â‚¬â€ PIX Webhooks

Plataforma de pagamentos desenvolvida para **simular um fluxo real de cobranÃƒÂ§as e confirmaÃƒÂ§ÃƒÂµes via PIX**, utilizando **webhooks assinados**, **SQLite para persistÃƒÂªncia local e Redis como fonte de verdade para expiraÃƒÂ§ÃƒÂ£o**, **idempotÃƒÂªncia**, **rate limit**, **observabilidade cross-service** e um **Fake Bank Service** para integraÃƒÂ§ÃƒÂ£o completa.

O projeto tem foco **educacional e de portfÃƒÂ³lio**, demonstrando **como sistemas de pagamento funcionam em produÃƒÂ§ÃƒÂ£o**, indo alÃƒÂ©m de CRUDs simples.

---

## Ã°Å¸Å¡â‚¬ VisÃƒÂ£o Geral

* Tipo: **API REST**
* DomÃƒÂ­nio: **Pagamentos / PIX / Webhooks**
* Modelo: **ConfirmaÃƒÂ§ÃƒÂ£o assÃƒÂ­ncrona via webhook**
* CenÃƒÂ¡rio real: e-commerce, SaaS, marketplaces, PSPs
* IntegraÃƒÂ§ÃƒÂ£o: **Payment API Ã¢â€ â€ Fake Bank Service**

Responsabilidades:

* `payment-charges-api`: cria e consulta cobranÃƒÂ§as, recebe webhooks PIX assinados, valida HMAC/timestamp, aplica idempotÃƒÂªncia, controla expiraÃƒÂ§ÃƒÂ£o via Redis TTL e persiste estado localmente em SQLite.
* `fake-bank-service`: registra cobranÃƒÂ§as simuladas em memÃƒÂ³ria, processa o pagamento PIX fake, assina webhooks, faz retry com backoff exponencial e envia falhas definitivas para DLQ em arquivo.

---

## Ã°Å¸Ââ€”Ã¯Â¸Â Arquitetura (VisÃƒÂ£o de Produto)

```text
Ã¢â€Å’Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€Â        Webhook (HMAC)
Ã¢â€â€š Fake Bank    Ã¢â€â€š Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€“Â¶ Ã¢â€â€š Payment Charges API Ã¢â€â€š
Ã¢â€â€š Service      Ã¢â€â€š                         Ã¢â€â€š                     Ã¢â€â€š
Ã¢â€â€Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€Ëœ                         Ã¢â€â€Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€Ëœ
        Ã¢â€“Â²                                             Ã¢â€â€š
        Ã¢â€â€š                                             Ã¢â€â€š
        Ã¢â€â€Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬ PIX Payment Flow Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€Ëœ
```

### Fluxo completo

1. Cliente cria uma cobranÃƒÂ§a (`POST /payment/charges`)
2. CobranÃƒÂ§a ÃƒÂ© registrada no Fake Bank (`POST /bank/pix/charges`)
3. Fake Bank processa o pagamento PIX (`POST /bank/pix/pay`)
4. Fake Bank envia **webhook assinado**
5. API valida assinatura + timestamp + idempotÃƒÂªncia
6. CobranÃƒÂ§a ÃƒÂ© marcada como **PAID**

---

## Ã°Å¸Â§Â  Conceitos de ProduÃƒÂ§ÃƒÂ£o Implementados

* Webhooks assinados (**HMAC SHA-256**)
* ProteÃƒÂ§ÃƒÂ£o contra replay attacks (**timestamp + tolerance window**)
* IdempotÃƒÂªncia de eventos via Redis
* SQLite para persistÃƒÂªncia local e Redis como fonte de verdade para expiraÃƒÂ§ÃƒÂ£o (TTL)
* Rate limit em endpoints sensÃƒÂ­veis
* Observabilidade cross-service (`X-Request-Id`)
* Logs da Payment API com `request_id`; Fake Bank mantÃƒÂ©m logs simples com `print`
* Retry + exponential backoff no Fake Bank
* DLQ em arquivo para falhas permanentes de webhook
* SeparaÃƒÂ§ÃƒÂ£o clara por camadas e responsabilidades

## Ã°Å¸â€Â Event Deduplication

Para evitar processamento duplicado de eventos de webhook, o sistema implementa deduplicaÃƒÂ§ÃƒÂ£o server-side baseada em `event_id`.

Funcionamento:

- Cada webhook recebido exige `event_id` no payload.
- Antes de processar a cobranÃƒÂ§a, o sistema verifica o marcador definitivo no Redis:
  `webhook:event:{event_id}`.
- Se o marcador definitivo jÃƒÂ¡ existir, o evento ÃƒÂ© ignorado (HTTP 200 Ã¢â‚¬â€œ idempotent safe response).
- Se nÃƒÂ£o existir, a API adquire um lock transitÃƒÂ³rio com `SET NX EX`:
  `webhook:event:{event_id}:lock`.
- Enquanto o lock estiver ocupado por outro request, a API retorna HTTP 503 com
  `{"error": "Event processing in progress"}`, permitindo retry do provedor.
- A chave definitiva `webhook:event:{event_id}` ÃƒÂ© persistida como `processed`
  no Redis com TTL de 24 horas apenas apÃƒÂ³s a transiÃƒÂ§ÃƒÂ£o de estado bem-sucedida.
- Falhas de validaÃƒÂ§ÃƒÂ£o, mismatch de valor ou erro antes do commit nÃƒÂ£o gravam o
  marcador definitivo; o lock ÃƒÂ© liberado somente pelo request owner.

Isso protege contra:
- Retries do provedor
- Reenvio manual de webhooks
- Ataques de replay fora da janela de idempotÃƒÂªncia

> Modelo inspirado em provedores como **Stripe, Mercado Pago e OpenPix**.

## Ã°Å¸â€Â Health & Readiness

A API expÃƒÂµe dois endpoints voltados para ambientes de produÃƒÂ§ÃƒÂ£o, adequados para ambientes conteinerizados (Docker, Kubernetes, etc.):
### `/health`
Verifica apenas se o serviÃƒÂ§o estÃƒÂ¡ ativo (liveness probe).

Retorna:
```json
{ "status": "ok" }
```

### `/ready`
Executa validaÃƒÂ§ÃƒÂµes de dependÃƒÂªncias crÃƒÂ­ticas:

- Conectividade com o banco de dados (SQLAlchemy `SELECT 1`)
- Conectividade com o Redis (`PING`)

Exemplo de resposta:

```json
{
  "status": "ready",
  "database": "ok",
  "redis": "ok"
}
```

## Ã°Å¸Å¡Â¦ Rate Limiting

A API utiliza **Flask-Limiter** com armazenamento em Redis para controle de taxa de requisiÃƒÂ§ÃƒÂµes.

CaracterÃƒÂ­sticas:

- Armazenamento distribuÃƒÂ­do via Redis (nÃƒÂ£o em memÃƒÂ³ria)
- ProteÃƒÂ§ÃƒÂ£o contra abuso em endpoints sensÃƒÂ­veis
- Limite aplicado no endpoint de criaÃƒÂ§ÃƒÂ£o de cobranÃƒÂ§as (`POST /payment/charges`)
- Resposta automÃƒÂ¡tica HTTP 429 quando o limite ÃƒÂ© excedido

Essa abordagem garante controle consistente mesmo com mÃƒÂºltiplas instÃƒÂ¢ncias da aplicaÃƒÂ§ÃƒÂ£o.

## Ã°Å¸â€â€ž Continuous Integration

O projeto possui **pipeline de integraÃƒÂ§ÃƒÂ£o contÃƒÂ­nua (CI)** configurado com **GitHub Actions**.

A cada push ou pull request:

- As dependÃƒÂªncias dos dois serviÃƒÂ§os sÃƒÂ£o instaladas
- O ambiente de testes ÃƒÂ© preparado
- A suÃƒÂ­te de testes automatizados de `payment-charges-api/tests` ÃƒÂ© executada com **pytest**

Isso garante que mudanÃƒÂ§as no cÃƒÂ³digo nÃƒÂ£o quebrem comportamentos crÃƒÂ­ticos do sistema.

---

## Ã°Å¸â€ºÂ Ã¯Â¸Â Tecnologias

* **Python 3.12**
* **Flask**
* **Flask SQLAlchemy**
* **SQLite** (ambiente local)
* **Redis**
* **Docker / Docker Compose**
* **Postman**
* **OpenAPI 3.0**

---

## Ã°Å¸â€œâ€š Estrutura do Projeto

```text
payment-platform/
Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ payment-charges-api/
Ã¢â€â€š   Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ app.py
Ã¢â€â€š   Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ routes/
Ã¢â€â€š   Ã¢â€â€š   Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ charges.py
Ã¢â€â€š   Ã¢â€â€š   Ã¢â€â€Ã¢â€â‚¬Ã¢â€â‚¬ webhooks.py
Ã¢â€â€š   Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ services/
Ã¢â€â€š   Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ repository/
Ã¢â€â€š   Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ security/
Ã¢â€â€š   Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ infrastructure/
Ã¢â€â€š   Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ audit/
Ã¢â€â€š   Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ instance/
Ã¢â€â€š   Ã¢â€â€Ã¢â€â‚¬Ã¢â€â‚¬ requirements.txt
Ã¢â€â€š
Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ fake-bank-service/
Ã¢â€â€š   Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ app.py
Ã¢â€â€š   Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ routes/
Ã¢â€â€š   Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ services/
Ã¢â€â€š   Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ clients/
Ã¢â€â€š   Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ security/
Ã¢â€â€š   Ã¢â€â€Ã¢â€â‚¬Ã¢â€â‚¬ requirements.txt
Ã¢â€â€š
Ã¢â€Å“Ã¢â€â‚¬Ã¢â€â‚¬ docker-compose.yml
Ã¢â€â€Ã¢â€â‚¬Ã¢â€â‚¬ README.md
```

---

## Ã¢Å¡Â¡ Quickstart (60 segundos)

### PrÃƒÂ©-requisitos

* Docker
* Docker Compose

### Subir todo o sistema

```bash
docker compose up --build
```

ServiÃƒÂ§os disponÃƒÂ­veis:

* Payment API Ã¢â€ â€™ `http://localhost:5000`
  * `GET /health`
  * `GET /ready`
  * `POST /payment/charges`
  * `GET /payment/charges/<charge_id>`
  * `POST /webhooks/pix`
* Fake Bank Ã¢â€ â€™ `http://localhost:6000`
  * `POST /bank/pix/charges`
  * `POST /bank/pix/pay`
  * `GET /bank/dlq`
  * `POST /bank/dlq/replay`

---

## Ã°Å¸â€Â Fluxo Completo (Exemplo Real)

### 1Ã¯Â¸ÂÃ¢Æ’Â£ Criar cobranÃƒÂ§a

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

### 2Ã¯Â¸ÂÃ¢Æ’Â£ Registrar cobranÃƒÂ§a no Fake Bank

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

### 3Ã¯Â¸ÂÃ¢Æ’Â£ Processar pagamento PIX

```bash
curl -X POST http://localhost:6000/bank/pix/pay \
  -H "Content-Type: application/json" \
  -H "X-Request-Id: demo-001" \
  -d '{"external_id":"uuid-gerado"}'
```

O Fake Bank dispara o webhook automaticamente.

---

### 4Ã¯Â¸ÂÃ¢Æ’Â£ Consultar status final

```bash
curl http://localhost:5000/payment/charges/1 \
  -H "X-Request-Id: demo-001"
```

```json
{
  "id": 1,
  "value": 100.0,
  "status": "PAID"
}
```

---

## Ã°Å¸â€Â Exemplo Real de Webhook (Fake Bank Ã¢â€ â€™ API)

### Headers

```text
X-Signature: sha256=...
X-Timestamp: 1700000000
X-Event-Id: evt_xxx
X-Request-Id: demo-001
```

A assinatura autentica o timestamp literal e o corpo bruto do webhook:

```text
signed_message = UTF8(X-Timestamp) + "." + raw_request_body
digest = HMAC-SHA256(WEBHOOK_SECRET, signed_message)
X-Signature = "sha256=" + lowercase_hex(digest)
```

`X-Timestamp` usa Unix epoch em segundos, somente dÃƒÂ­gitos. Alterar o
timestamp ou qualquer byte do body invalida a assinatura.

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

## Ã°Å¸â€œÅ“ OpenAPI

* Payment Charges API: `payment-charges-api/openapi.yaml`
* Fake Bank Service: `fake-bank-service/openapi.yaml`
* Define endpoints, payloads, headers e erros dos contratos documentados
* Pode ser usado para:

  * Swagger UI
  * GeraÃƒÂ§ÃƒÂ£o de clientes
  * IntegraÃƒÂ§ÃƒÂµes externas

---

## Ã°Å¸Â§Âª Testes

Para executar as suites do monorepo com isolamento entre serviÃƒÂ§os:

```powershell
.\scripts\test_all.ps1
```

O script roda cada suite em um processo Python separado porque os serviÃƒÂ§os
possuem modulos internos com nomes top-level iguais, como `security`,
`services` e `routes`.

```bash
pytest payment-charges-api/tests -q
```

* Testes automatizados com pytest
* Testes manuais via Postman
* CenÃƒÂ¡rios cobertos:

  * Webhook vÃƒÂ¡lido
  * Webhook duplicado (idempotÃƒÂªncia)
  * Webhook expirado
  * Assinatura invÃƒÂ¡lida
  * Rate limit excedido

---

## Ã°Å¸â€œÅ’ PrÃƒÂ³ximos Passos

* [ ] MÃƒÂ©tricas (Prometheus)
* [ ] MigraÃƒÂ§ÃƒÂ£o para PostgreSQL
* [ ] Deploy em ambiente cloud

---

## Ã°Å¸â€˜Â¨Ã¢â‚¬ÂÃ°Å¸â€™Â» Autor

**Yago FÃƒÂ©lix**  

Ã°Å¸â€™Â¼ Desenvolvedor Python Ã¢â‚¬â€ Back-end | Full Stack  
Ã°Å¸â€Â Focado em APIs, automaÃƒÂ§ÃƒÂ£o e sistemas distribuÃƒÂ­dos

GitHub: https://github.com/yagofelix00  
LinkedIn: https://www.linkedin.com/in/yago-felix-737011279/

---
