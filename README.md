# MVP_TV — Sistema de Abertura de Falhas

Aplicação web responsiva que atua como cockpit de entrada para criação e triagem de solicitações de manutenção, com integração bidirecional ao SAP (via Mock de BAPI/REST).

---

## Stack

| Camada | Tecnologia |
|---|---|
| Back-end | Python 3.13 + Flask |
| Banco de dados | PostgreSQL (Supabase) |
| Armazenamento | Supabase Storage |
| Integração | Mock SAP (BAPI_ALM_NOTIF_CREATE) |

---

## Pré-requisitos

- Python 3.13+
- Conta no [Supabase](https://supabase.com)
- Git

---

## Instalação

```bash
# 1. Clone o repositório
git clone https://github.com/Devssaur/MVP_TV.git
cd MVP_TVF

# 2. Crie e ative o ambiente virtual
python -m venv venv
venv\Scripts\activate     # Windows
# source venv/bin/activate  # Linux/Mac

# 3. Instale as dependências
pip install flask supabase python-dotenv
```

---

## Configuração

Crie o arquivo `.env` na raiz do projeto:

```env
SUPABASE_URL=https://<seu-projeto>.supabase.co
SUPABASE_KEY=<sua-chave-anon>
SUPABASE_ANON_KEY=<opcional: alias da chave anon>
SUPABASE_SERVICE_KEY=<opcional: usada em operações administrativas>
```

---

## Banco de Dados

Execute os scripts na ordem abaixo no **SQL Editor** do Supabase:

| Arquivo | Descrição |
|---|---|
| `database/schema_saf.sql` | Criação das tabelas, índices e triggers |
| `database/policies_rls.sql` | Políticas de segurança RLS |
| `database/migrations/add_aprovado_usuarios.sql` | Adiciona aprovação/cadastro automático de usuário via `auth.users` |
| `database/migrations/add_usando_como_usuarios.sql` | Adiciona coluna de modo de visualização (`usando_como`) |
| `database/migrations/add_manutencao_perfil_usuarios.sql` | Habilita perfil `Manutenção` e modo `MANUTENCAO` |
| `database/seed_usuarios.sql` | Usuários de teste |

---

## Executando

```bash
python app.py
```

Acesse: `http://127.0.0.1:5000/`

---

## Rotas da API

### Autenticação — `/api/auth`

| Método | Rota | Descrição |
|---|---|---|
| POST | `/api/auth/login` | Login com email e senha |

```json
// POST /api/auth/login
{ "email": "joao@empresa.com", "senha": "senha123" }
```

---

### Solicitações — `/api/solicitacoes`

| Método | Rota | Descrição |
|---|---|---|
| POST | `/api/solicitacoes/criar` | Cria nova SAF |
| GET | `/api/solicitacoes/minhassafs/<usuario_id>` | Lista SAFs do solicitante |

```json
// POST /api/solicitacoes/criar
{
  "notificador_id": "<uuid>",
  "titulo_falha": "Bomba com vazamento",
  "descricao_longa": "Identificado vazamento no selo mecânico",
  "local_instalacao": "LI-0001",
  "equipamento": "EQ-0001",
  "prioridade": 2,
  "data_inicio_avaria": "2026-04-25",
  "hora_inicio_avaria": "14:30:00"
}
```

---

### CCM — `/api/ccm`

| Método | Rota | Descrição |
|---|---|---|
| GET | `/api/ccm/pendentes` | Lista SAFs aguardando triagem |
| POST | `/api/ccm/avaliar/<saf_id>` | Avalia uma SAF |

```json
// POST /api/ccm/avaliar/<saf_id>
// status: "Confirmado" | "Necessário Complemento" | "Cancelado"
{
  "status": "Confirmado",
  "avaliado_por_id": "<uuid-do-ccm>"
}
```

---

### Dados Mestres — `/api/dados`

| Método | Rota | Descrição |
|---|---|---|
| GET | `/api/dados/locais` | Lista locais de instalação |
| GET | `/api/dados/equipamentos/<local_id>` | Equipamentos por local |
| GET | `/api/dados/sintomas/<equipamento_id>` | Catálogo de sintomas |

---

## Endpoints por Método

### GET

- /api/solicitacoes/minhassafs/<usuario_id>
- /api/ccm/pendentes
- /api/dados/locais
- /api/dados/equipamentos/<local_id>
- /api/dados/sintomas/<equipamento_id>

### POST

- /api/auth/login
- /api/solicitacoes/criar
- /api/ccm/avaliar/<saf_id>

---

## Perfis de Acesso (RBAC)

| Perfil | Permissões |
|---|---|
| `SOLICITANTE` | Cria, edita (se devolvida) e cancela SAFs (sem ordem SAP) |
| `CCM` | Avalia SAFs: Aprovar, Devolver ou Cancelar |
| `MANUTENCAO` | Dashboard operacional com visão de SAFs aprovadas por sistema/centro |
| `ADMIN` | Acesso a logs de auditoria e configurações |

---

## Estrutura do Projeto

```
MVP_TV/
├── app.py                  # Ponto de entrada Flask
├── .env                    # Credenciais (não versionado)
├── .gitignore
├── contexto.md             # Documento de requisitos do projeto
├── database/
│   ├── schema_saf.sql      # DDL das tabelas
│   ├── policies_rls.sql    # Políticas RLS do Supabase
│   └── seed_usuarios.sql   # Dados de teste
└── routes/
    ├── auth.py             # Autenticação
    ├── solicitacoes.py     # CRUD de SAFs
    ├── ccm.py              # Triagem CCM
    └── dados_mestres.py    # Cache de dados SAP
```
