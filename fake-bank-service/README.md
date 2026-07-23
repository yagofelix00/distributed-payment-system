# 🏦 Fake Bank Service — PIX Simulator

Serviço responsável por **simular o comportamento de um banco/PSP**, processando pagamentos via PIX e disparando **webhooks assinados** para sistemas integrados.

Este serviço representa o **lado externo** da integração, permitindo testar fluxos reais de pagamento **assíncronos e idempotentes**.

---

## 🎯 Responsabilidade do serviço

* Registrar cobranças recebidas do sistema principal
* Processar pagamentos PIX de forma simulada
* Disparar **webhooks assinados (HMAC)** para sistemas clientes
* Implementar **retry com exponential backoff**
* Propagar **X-Request-Id** para observabilidade cross-service
* Simular falhas e comportamento real de provedores de pagamento

---

## 🧠 Conceitos aplicados

* Webhooks assinados (HMAC + SHA-256)
* Retry automático com exponential backoff
* Idempotência por `event_id`
* Observabilidade cross-service
* Separação clara por rotas, serviços e clientes
* Simulação de integração bancária realista
* Dead Letter Queue (DLQ) para falhas definitivas de webhook


---

## 🛠️ Tecnologias

* Python 3.12
* Flask
* Requests
* Docker

---

## 📂 Estrutura do Serviço

```text
fake-bank-service/
├── app.py
├── routes/
│   └── pix.py
├── services/
│   └── webhook_dispatcher.py
├── clients/
│   └── webhook_client.py
├── security/
│   └── hmac.py
├── config.py
└── requirements.txt
```

---

### 🧩 Convenção de Camadas

O Fake Bank Service segue uma estrutura simples e explícita, simulando um
provedor de pagamento real:

- **routes/**  
  Camada HTTP. Responsável por:
  - endpoints que simulam ações bancárias
  - recebimento de comandos de pagamento

- **services/**  
  Camada de processamento:
  - lógica de envio de webhooks
  - retry + exponential backoff
  - construção de eventos (`event_id`, payload)

- **clients/**  
  Comunicação externa:
  - cliente HTTP responsável por despachar webhooks
  - isolamento de chamadas de rede

- **security/**  
  Segurança de integração:
  - geração de assinaturas HMAC
  - padronização de headers de webhook

- **audit/**  
  Logs e observabilidade para simular monitoramento bancário

---

## 📦 Variáveis de Ambiente

Use o template versionado como referência para as variáveis esperadas pelo serviço.

PowerShell:

```powershell
Copy-Item .env.example .env
```

Bash:

```bash
cp .env.example .env
```

> Execute os comandos acima dentro de `fake-bank-service/`.

Preencha `WEBHOOK_SECRET` com exatamente o mesmo valor configurado no `payment-charges-api`; esse segredo compartilhado é usado para assinar e validar webhooks PIX.

As variáveis de retry/backoff (`MAX_RETRIES`, `INITIAL_DELAY_SECONDS`, `BACKOFF_MULTIPLIER`, `MAX_DELAY_SECONDS`) e `TIMEOUT_SECONDS` são opcionais e possuem defaults no código.

`WEBHOOK_URL` existe na configuração do serviço, mas o fluxo principal de PIX atualmente exige `webhook_url` no payload da cobrança e usa esse valor para despachar o webhook. O template mantém `WEBHOOK_URL` apenas como variável já existente na configuração; esta task não altera o comportamento atual.

Importante: o Fake Bank não chama `load_dotenv()` explicitamente hoje. Ao rodar sem Docker Compose, exporte as variáveis no shell ou use uma ferramenta que carregue `.env` antes de iniciar o serviço.

Nunca versione arquivos `.env` com segredos reais.

---

## ▶️ Como rodar isoladamente

### Sem Docker

```bash
python -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Serviço disponível em:

```
http://localhost:6000
```

---

### Com Docker

Execute a partir da **raiz do projeto**:

```bash
docker compose up fake-bank-service
```

---

## 🔗 Endpoints

### Registrar cobrança

```
POST /bank/pix/charges
```

Payload:

```json
{
  "external_id": "uuid",
  "value": 100.0,
  "webhook_url": "http://payment-charges-api:5000/webhooks/pix"
}
```

Resposta:

```json
{
  "message": "Charge registered in bank"
}
```

---

### Processar pagamento PIX

```
POST /bank/pix/pay
```

Payload:

```json
{
  "external_id": "uuid"
}
```

> Este endpoint **simula o processamento bancário** e dispara o webhook automaticamente.

Resposta:

```json
{
  "message": "PIX processed by bank",
  "event_id": "evt_xxx"
}
```

---

## 🔔 Webhook disparado

### Headers enviados

```
X-Signature: sha256=...
X-Timestamp: <unix-seconds>
X-Event-Id: evt_xxx
X-Request-Id: demo-001
```

A assinatura enviada pelo fake bank autentica o timestamp literal e o body bruto:

```text
signed_message = UTF8(X-Timestamp) + "." + raw_request_body
digest = HMAC-SHA256(WEBHOOK_SECRET, signed_message)
X-Signature = "sha256=" + lowercase_hex(digest)
```

`X-Timestamp` usa Unix epoch em segundos, somente dígitos. Cada tentativa de retry gera novo `X-Timestamp` e nova `X-Signature`, mantendo o mesmo payload e `event_id`.

### Body

```json
{
  "event_id": "evt_xxx",
  "external_id": "uuid",
  "value": 100.0,
  "status": "PAID"
}
```

---

## 🔁 Retry + Backoff

* Webhooks são reenviados automaticamente em caso de falha
* Cada tentativa recalcula `X-Timestamp` e `X-Signature` para o mesmo payload
* Estratégia utilizada:

  * Exponential backoff
  * Jitter para evitar thundering herd
  * Número máximo de tentativas configurável

> Simula o comportamento de bancos e gateways reais.

---

## ☠️ Dead Letter Queue (DLQ)

Quando um webhook **falha definitivamente**, mesmo após todas as tentativas de
**retry com exponential backoff**, o evento é enviado para uma
**Dead Letter Queue (DLQ)**.

Isso garante que eventos de pagamento **nunca sejam perdidos**, permitindo
auditoria e reprocessamento manual — exatamente como em integrações reais
com bancos e gateways de pagamento.

### Quando um evento vai para a DLQ?

- Timeout persistente ao chamar o webhook
- Erros de rede repetidos
- Respostas HTTP não-2xx após todas as tentativas
- Falhas definitivas de entrega

### Onde os eventos são armazenados?

Os eventos são persistidos no Fake Bank Service em formato **JSON Lines**:

```text
fake-bank-service/dlq_data/failed_webhooks.jsonl
```

Cada evento registra:

* `event_id`
* `external_id`
* payload enviado
* último status HTTP recebido
* última exceção capturada (se houver)
* timestamp UTC
* status de replay

### Endpoints da DLQ

#### Listar eventos falhos

```http
GET /bank/dlq
```

#### Reprocessar um evento específico

```http
POST /bank/dlq/replay
```

Payload:

```json
{
  "event_id": "evt_xxx"
}
```

> O reprocessamento respeita idempotência e marca o evento como `replayed`
> após sucesso. O replay redispara o payload original e gera novos
> `X-Timestamp` e `X-Signature`; assinatura antiga não é persistida nem reutilizada.

---

## 🔐 Segurança

* Assinatura HMAC baseada em **`X-Timestamp` literal + `.` + raw body**
* Timestamp Unix em segundos para proteção contra replay
* Idempotência por `event_id`
* Headers obrigatórios validados no sistema receptor

---

## 📌 Observação importante

Este serviço **não persiste estado bancário** (intencionalmente).
Seu foco é simular **integração externa realista**, não substituir um banco real.

---

## 🧪 Status do projeto

* Retry/backoff: ✅ implementado
* Assinatura HMAC: ✅ implementada
* Dead Letter Queue (DLQ): ✅ implementada
* Replay manual de webhooks: ✅ implementado
* Integração com Payment API: ✅ completa
* Persistência bancária: ❌ intencionalmente ausente

---

### 🏁 Conclusão

O Fake Bank Service permite testar fluxos de pagamento PIX **como ocorrem em produção**, sendo uma peça essencial para validar segurança, idempotência e comportamento assíncrono da plataforma.

---

