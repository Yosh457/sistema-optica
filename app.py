# app.py
import os
from dotenv import load_dotenv
from flask import Flask, redirect, url_for, flash, render_template
from flask_wtf.csrf import CSRFError

# Importamos extensiones y modelos
from extensions import login_manager, csrf
from models import db, Usuario

def create_app():
    # Inicializa Flask
    app = Flask(__name__)
    # Habilitar extensión 'do' para Jinja2
    app.jinja_env.add_extension('jinja2.ext.do')
    # Cargar variables de entorno
    load_dotenv()

    # --- CONFIGURACIÓN DE SEGURIDAD ---
    app.config['SECRET_KEY'] = os.getenv('SECRET_KEY')
    if not app.config['SECRET_KEY']:
        raise RuntimeError("Error crítico: No se ha configurado SECRET_KEY en el archivo .env")
    
    # --- CONFIGURACIÓN DE BASE DE DATOS ---
    db_host = os.getenv('MYSQL_HOST')
    db_port = os.getenv('MYSQL_PORT')
    db_user = os.getenv('MYSQL_USER')
    db_pass = os.getenv('MYSQL_PASSWORD')
    db_name = os.getenv('MYSQL_DB')

    if not all([db_host, db_port, db_user, db_pass, db_name]):
        raise RuntimeError("Error crítico: Configuración de base de datos incompleta en el archivo .env")
    
    app.config['SQLALCHEMY_DATABASE_URI'] = f'mysql+pymysql://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['MAX_CONTENT_LENGTH'] = 32 * 1024 * 1024 # Límite de 32MB
    
    # Configuración de Pool para estabilidad
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_pre_ping": True,
        "pool_recycle": 280
    }

    # --- INICIALIZACIÓN DE EXTENSIONES ---
    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)

    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Acceso restringido. Inicie sesión.'
    login_manager.login_message_category = 'warning'

    # --- REGISTRO DE BLUEPRINTS ---
    from blueprints.auth import auth_bp
    app.register_blueprint(auth_bp)
    
    from blueprints.admin import admin_bp
    app.register_blueprint(admin_bp)
    
    from blueprints.dashboard import dashboard_bp
    app.register_blueprint(dashboard_bp)
    
    from blueprints.inventario import inventario_bp
    app.register_blueprint(inventario_bp)
    
    from blueprints.clinica import clinica_bp
    app.register_blueprint(clinica_bp)

    # Ruta raíz redirige al login
    @app.route('/')
    def index():
        from flask_login import current_user
        if current_user.is_authenticated:
            return redirect(url_for('dashboard.index'))
        return redirect(url_for('auth.login'))
    
    # --- ERRORES Y CACHÉ ---
    @app.errorhandler(CSRFError)
    def handle_csrf_error(e):
        flash('La sesión expiró. Ingrese nuevamente.', 'warning')
        return redirect(url_for('auth.login'))
    
    @app.after_request
    def add_header(response):
        """Desactiva el caché para evitar problemas al volver atrás en el navegador"""
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, post-check=0, pre-check=0, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '-1'
        return response

    # MANEJO DE ERRORES PERSONALIZADO
    @app.errorhandler(404)
    def page_not_found(e):
        return render_template('errors/404.html'), 404

    @app.errorhandler(403)
    def access_denied(e):
        return render_template('errors/403.html'), 403

    @app.errorhandler(500)
    def internal_server_error(e):
        return render_template('errors/500.html'), 500
    
    return app

# Loader de usuario para Flask-Login
@login_manager.user_loader
def load_user(user_id):
    return Usuario.query.get(int(user_id))

if __name__ == '__main__':
    app = create_app()
    with app.app_context():
        # Crea las tablas según models.py si no existen
        try:
            db.create_all()
            print("✅ Sistema Óptica inicializado. Tablas verificadas.")
        except Exception as e:
            print(f"❌ Error al conectar con BD: {e}")
            
    app.run(debug=True)