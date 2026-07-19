import os
import logging
from pathlib import Path
from flask import Flask, render_template
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
VERSION_PATH = BASE_DIR / "VERSION"

# Carrega as variaveis antes de importar modulos que dependem delas.
# override=True evita que variaveis antigas do shell mascararem valores do .env.
load_dotenv(dotenv_path=ENV_PATH, override=True)

from routes.auth import auth_bp
from routes.solicitacoes import solicitacoes_bp
from routes.dados_mestres import dados_bp
from routes.ccm import ccm_bp
from routes.admin import admin_bp

app = Flask(__name__)
app.logger.setLevel(logging.INFO)


def _load_app_version() -> str:
    env_version = os.environ.get("APP_VERSION", "").strip()
    if env_version:
        return env_version

    try:
        version = VERSION_PATH.read_text(encoding="utf-8").strip()
        return version or "0.0.0"
    except OSError:
        return "0.0.0"


APP_VERSION = _load_app_version()

# ── Contexto global (disponível em todos os templates) ───────
@app.context_processor
def inject_globals():
    dev_mode = os.environ.get('DEV_MODE', '').lower() in ('1', 'true', 'yes')
    return dict(dev_mode=dev_mode, app_version=APP_VERSION)

# ── Registro de Rotas API ──────────────────────────────────
app.register_blueprint(auth_bp,          url_prefix='/api/auth')
app.register_blueprint(solicitacoes_bp,  url_prefix='/api/solicitacoes')
app.register_blueprint(dados_bp,         url_prefix='/api/dados')
app.register_blueprint(ccm_bp,           url_prefix='/api/ccm')
app.register_blueprint(admin_bp,         url_prefix='/api/admin')

# ── Rotas de Front-end (SPA com Jinja2) ───────────────────
@app.route('/')
def index():
    return render_template('login.html')

@app.route('/login')
def login_page():
    return render_template('login.html')

# Solicitante
@app.route('/minhassafs')
def minhas_safs():
    return render_template('minhas_safs.html')

@app.route('/novasaf')
def nova_saf():
    return render_template('nova_saf.html')

@app.route('/saf/<saf_id>')
def detalhe_saf(saf_id):
    return render_template('detalhe_saf.html')

@app.route('/editar-saf/<saf_id>')
def editar_saf(saf_id):
    # Reutiliza o mesmo template; o JS detecta a rota e carrega em modo edição
    return render_template('nova_saf.html')

# CCM
@app.route('/filaccm')
def fila_ccm():
    return render_template('fila_ccm.html')

# SIC
@app.route('/chamados-sic')
def chamados_sic():
    return render_template('chamados_sic.html')

@app.route('/avaliar-saf/<saf_id>')
def avaliar_saf(saf_id):
    return render_template('avaliar_saf.html')

# Admin
@app.route('/admin')
def admin_page():
    return render_template('admin.html')

@app.route('/admin/usuarios')
def admin_usuarios():
    return render_template('admin.html')

@app.route('/admin/logs')
def admin_logs():
    return render_template('admin.html')

# Acesso negado
@app.route('/acesso-negado')
def acesso_negado():
    return render_template('login.html'), 403

if __name__ == "__main__":
    debug_mode = os.environ.get("FLASK_DEBUG", "1").lower() in ("1", "true", "yes")
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=debug_mode,
        use_reloader=debug_mode,
    )