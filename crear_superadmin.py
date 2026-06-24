# crear_superadmin.py
from app import create_app
from models import db, Usuario, RolAplicacion

app = create_app()

def crear_admin():
    with app.app_context():
        print("\n--- CREACIÓN DE SUPER ADMINISTRADOR (ÓPTICA) ---")
        
        rol_admin = RolAplicacion.query.filter_by(nombre='Admin').first()
        if not rol_admin:
            print("❌ Error: El rol 'Admin' no existe. Ejecuta los INSERTS iniciales en SQL.")
            return

        email = input("Ingresa el email del nuevo Admin: ").strip()
        
        if Usuario.query.filter_by(email=email).first():
            print(f"❌ Error: El email {email} ya está registrado.")
            return
        
        password = input("Ingresa la contraseña temporal: ").strip()
        nombre = input("Ingresa el nombre completo (Ej: Super Administrador): ").strip()

        nuevo_admin = Usuario(
            nombre_completo=nombre or "Super Administrador",
            email=email,
            rol_id=rol_admin.id,
            activo=True,
            cambio_clave_requerido=False 
        )
        
        nuevo_admin.set_password(password)
        
        try:
            db.session.add(nuevo_admin)
            db.session.commit()
            print(f"✅ ¡Éxito! Usuario {email} creado correctamente con rol de Administrador.")
        except Exception as e:
            print(f"❌ Error al guardar en la base de datos: {e}")
            db.session.rollback()

if __name__ == '__main__':
    crear_admin()