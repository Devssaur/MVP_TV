import json
import os
import sys
import uuid

from dotenv import load_dotenv
from supabase import create_client

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import app


def main() -> int:
    load_dotenv(dotenv_path=os.path.join(os.getcwd(), '.env'), override=True)
    url = os.getenv('SUPABASE_URL')
    service_key = os.getenv('SUPABASE_SERVICE_KEY') or os.getenv('SUPABASE_KEY')

    out: dict[str, object] = {'setup': {}, 'login': None, 'checks': []}
    if not url or not service_key:
        out['erro'] = 'SUPABASE_URL/SUPABASE_SERVICE_KEY ausentes no .env'
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    sb = create_client(url, service_key)

    suffix = str(uuid.uuid4())[:8]
    email = f'smoke_{suffix}@example.com'
    senha = 'SmokeTest#2026'
    nome = f'Smoke Test {suffix}'

    out['setup'] = {'email': email}

    try:
        resp = sb.auth.sign_up(
            {
                'email': email,
                'password': senha,
                'options': {
                    'data': {
                        'nome': nome,
                        'perfil': 'Solicitante',
                        'empresa': 'SMOKE',
                        'area': 'QA',
                    }
                },
            }
        )
        out['setup']['signup_ok'] = bool(getattr(resp, 'user', None))
    except Exception as e:
        out['setup']['signup_ok'] = False
        out['setup']['signup_err'] = str(e)

    try:
        q = (
            sb.table('usuarios')
            .select('id,email,aprovado,perfil,nome')
            .eq('email', email)
            .limit(1)
            .execute()
        )
        row = (q.data or [None])[0]
        if row:
            (
                sb.table('usuarios')
                .update(
                    {
                        'aprovado': True,
                        'perfil': 'Solicitante',
                        'nome': nome,
                        'empresa': 'SMOKE',
                        'area': 'QA',
                    }
                )
                .eq('id', row['id'])
                .execute()
            )
            out['setup']['usuario_row_found'] = True
            out['setup']['usuario_id'] = row['id']
        else:
            out['setup']['usuario_row_found'] = False
    except Exception as e:
        out['setup']['usuario_sync_err'] = str(e)

    with app.test_client() as client:
        lr = client.post('/api/auth/login', json={'email': email, 'senha': senha})
        lb = lr.get_json(silent=True) or {}
        out['login'] = {'status': lr.status_code, 'ok': 200 <= lr.status_code < 300, 'erro': lb.get('erro')}

        token = lb.get('access_token') if lr.status_code == 200 else None
        if token:
            headers = {'Authorization': f'Bearer {token}'}
            urls = [
                '/api/dados/subsistemas?sistema_id=1',
                '/api/dados/opcoes-formulario?tipo=serie',
                '/api/dados/opcoes-formulario?tipo=trem',
                '/api/dados/opcoes-formulario?tipo=carro',
                '/api/dados/opcoes-formulario?tipo=trecho&linha=11',
                '/api/dados/opcoes-formulario?tipo=via&linha=11',
                '/api/dados/opcoes-formulario?tipo=local&linha=11',
                '/api/dados/opcoes-formulario?tipo=tag&sistema_id=1',
            ]
            for u in urls:
                r = client.get(u, headers=headers)
                b = r.get_json(silent=True) or {}
                total = b.get('total')
                if total is None and isinstance(b.get('opcoes'), list):
                    total = len(b.get('opcoes'))
                out['checks'].append(
                    {
                        'url': u,
                        'status': r.status_code,
                        'ok': 200 <= r.status_code < 300,
                        'total': total,
                        'erro': b.get('erro'),
                    }
                )

    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
