import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import app

urls = [
    '/api/dados/subsistemas?sistema_id=1',
    '/api/dados/opcoes-formulario?tipo=serie',
    '/api/dados/opcoes-formulario?tipo=trecho&linha=11',
    '/api/dados/opcoes-formulario?tipo=via&linha=11',
    '/api/dados/opcoes-formulario?tipo=tag&sistema_id=1',
]

out = []
with app.test_client() as client:
    for u in urls:
        r = client.get(u)
        b = r.get_json(silent=True) or {}
        out.append({'url': u, 'status': r.status_code, 'erro': b.get('erro')})

print(json.dumps(out, ensure_ascii=False, indent=2))
