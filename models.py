# models.py
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import pytz

db = SQLAlchemy()

def obtener_hora_chile():
    cl_tz = pytz.timezone('America/Santiago')
    return datetime.now(cl_tz)

# ==============================================================================
# CONFIGURACIÓN DEL SISTEMA Y USUARIOS
# ==============================================================================

class RolAplicacion(db.Model):
    __tablename__ = 'roles_aplicacion'
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(50), unique=True, nullable=False)
    
    usuarios = db.relationship('Usuario', back_populates='rol')

class Usuario(db.Model, UserMixin):
    __tablename__ = 'usuarios'
    id = db.Column(db.Integer, primary_key=True)
    nombre_completo = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    activo = db.Column(db.Boolean, default=True)
    fecha_creacion = db.Column(db.DateTime, default=obtener_hora_chile)
    cambio_clave_requerido = db.Column(db.Boolean, default=False, nullable=False)
    
    reset_token = db.Column(db.String(32), unique=True, nullable=True)
    reset_token_expiracion = db.Column(db.DateTime, nullable=True)

    rol_id = db.Column(db.Integer, db.ForeignKey('roles_aplicacion.id'), nullable=False, index=True)
    rol = db.relationship('RolAplicacion', back_populates='usuarios')
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

# ==============================================================================
# AUDITORÍA Y LOGS
# ==============================================================================

class LogSistema(db.Model):
    __tablename__ = 'log_sistema'
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=obtener_hora_chile, index=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuarios.id', ondelete='SET NULL'), nullable=True, index=True)
    usuario_nombre = db.Column(db.String(255), nullable=True)
    accion = db.Column(db.String(255), nullable=False)
    detalles = db.Column(db.Text)
    ip_origen = db.Column(db.String(50), nullable=True)

    usuario = db.relationship('Usuario')

# ==============================================================================
# TABLAS PARAMÉTRICAS Y CATÁLOGOS
# ==============================================================================

class EstadoReceta(db.Model):
    __tablename__ = 'estados_receta'
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(50), unique=True, nullable=False)
    orden = db.Column(db.Integer, nullable=False)

class EstadoOrden(db.Model):
    __tablename__ = 'estados_orden'
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(50), unique=True, nullable=False)
    orden = db.Column(db.Integer, nullable=False)

class MetodoPago(db.Model):
    __tablename__ = 'metodos_pago'
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(50), unique=True, nullable=False)
    activo = db.Column(db.Boolean, default=True)

# ==============================================================================
# MÓDULO DE INVENTARIO
# ==============================================================================

class CategoriaProducto(db.Model):
    __tablename__ = 'categorias_productos'
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), unique=True, nullable=False)
    activo = db.Column(db.Boolean, default=True)
    
    productos = db.relationship('Producto', back_populates='categoria')

class Producto(db.Model):
    __tablename__ = 'productos'
    id = db.Column(db.Integer, primary_key=True)
    codigo = db.Column(db.String(50), nullable=True, index=True) # Ahora es opcional
    nombre = db.Column(db.String(255), nullable=False)
    precio = db.Column(db.Numeric(10, 2), nullable=False)
    stock = db.Column(db.Integer, default=0, nullable=False)
    stock_minimo = db.Column(db.Integer, default=5, nullable=False)
    activo = db.Column(db.Boolean, default=True)

    categoria_id = db.Column(db.Integer, db.ForeignKey('categorias_productos.id', ondelete='RESTRICT'), nullable=False, index=True)
    categoria = db.relationship('CategoriaProducto', back_populates='productos')

# ==============================================================================
# MÓDULO CLÍNICO (PACIENTES Y RECETAS)
# ==============================================================================

class Establecimiento(db.Model):
    __tablename__ = 'establecimientos'
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(255), unique=True, nullable=False)
    activo = db.Column(db.Boolean, default=True)
    
    pacientes = db.relationship('Paciente', back_populates='establecimiento')

class Paciente(db.Model):
    __tablename__ = 'pacientes'
    id = db.Column(db.Integer, primary_key=True)
    rut = db.Column(db.String(12), unique=True, nullable=False, index=True)
    nombre_completo = db.Column(db.String(255), nullable=False)
    telefono = db.Column(db.String(20), nullable=False)
    direccion = db.Column(db.String(255), nullable=False)
    fecha_registro = db.Column(db.DateTime, default=obtener_hora_chile)
    activo = db.Column(db.Boolean, default=True)

    establecimiento_id = db.Column(db.Integer, db.ForeignKey('establecimientos.id', ondelete='RESTRICT'), nullable=True, index=True)
    establecimiento = db.relationship('Establecimiento', back_populates='pacientes')
    
    recetas = db.relationship('RecetaOftalmica', back_populates='paciente', cascade="all, delete-orphan")
    ordenes = db.relationship('OrdenTrabajo', back_populates='paciente')

class RecetaProducto(db.Model):
    __tablename__ = 'receta_productos'
    id = db.Column(db.Integer, primary_key=True)
    cantidad = db.Column(db.Integer, nullable=False, default=1)
    precio_unitario = db.Column(db.Numeric(10, 2), nullable=False, default=0.00)
    subtotal = db.Column(db.Numeric(10, 2), nullable=False, default=0.00)
    observaciones = db.Column(db.String(255), nullable=True)

    receta_id = db.Column(db.Integer, db.ForeignKey('recetas_oftalmicas.id', ondelete='CASCADE'), nullable=False, index=True)
    producto_id = db.Column(db.Integer, db.ForeignKey('productos.id', ondelete='RESTRICT'), nullable=False)

    receta = db.relationship('RecetaOftalmica', back_populates='productos_asociados')
    producto = db.relationship('Producto')
    
class RecetaOftalmica(db.Model):
    __tablename__ = 'recetas_oftalmicas'
    id = db.Column(db.Integer, primary_key=True)
    archivo_receta = db.Column(db.String(255), nullable=False) # Nombre del archivo digitalizado
    
    # Generales
    observaciones = db.Column(db.Text, nullable=True)
    activa = db.Column(db.Boolean, default=True, nullable=False) # Indicador de vigencia
    
    # Auditoría Estándar
    fecha_registro = db.Column(db.DateTime, default=obtener_hora_chile)
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuarios.id', ondelete='RESTRICT'), nullable=False)
    modificado_por = db.Column(db.Integer, db.ForeignKey('usuarios.id', ondelete='SET NULL'), nullable=True)
    fecha_modificacion = db.Column(db.DateTime, nullable=True, onupdate=obtener_hora_chile)
    
    # Relaciones (con estados_receta)
    paciente_id = db.Column(db.Integer, db.ForeignKey('pacientes.id', ondelete='CASCADE'), nullable=False, index=True)
    estado_id = db.Column(db.Integer, db.ForeignKey('estados_receta.id', ondelete='RESTRICT'), nullable=False)

    paciente = db.relationship('Paciente', back_populates='recetas')
    estado = db.relationship('EstadoReceta')
    ordenes = db.relationship('OrdenTrabajo', back_populates='receta')
    productos_asociados = db.relationship('RecetaProducto', back_populates='receta', cascade="all, delete-orphan")

# ==============================================================================
# MÓDULO DE VENTAS / ÓRDENES DE TRABAJO
# ==============================================================================

class OrdenTrabajo(db.Model):
    """Reemplaza la antigua tabla 'ventas'"""
    __tablename__ = 'ordenes_trabajo'
    id = db.Column(db.Integer, primary_key=True)
    fecha_creacion = db.Column(db.DateTime, default=obtener_hora_chile, index=True)
    total = db.Column(db.Numeric(10, 2), nullable=False)
    observaciones = db.Column(db.Text, nullable=True)

    # Auditoría Estándar
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuarios.id', ondelete='RESTRICT'), nullable=False, index=True)
    modificado_por = db.Column(db.Integer, db.ForeignKey('usuarios.id', ondelete='SET NULL'), nullable=True)
    fecha_modificacion = db.Column(db.DateTime, nullable=True, onupdate=obtener_hora_chile)

    # Relaciones (con estados_orden)
    paciente_id = db.Column(db.Integer, db.ForeignKey('pacientes.id', ondelete='RESTRICT'), nullable=False, index=True)
    receta_id = db.Column(db.Integer, db.ForeignKey('recetas_oftalmicas.id', ondelete='SET NULL'), nullable=True)
    estado_id = db.Column(db.Integer, db.ForeignKey('estados_orden.id', ondelete='RESTRICT'), nullable=False)
    metodo_pago_id = db.Column(db.Integer, db.ForeignKey('metodos_pago.id', ondelete='RESTRICT'), nullable=True)

    paciente = db.relationship('Paciente', back_populates='ordenes')
    receta = db.relationship('RecetaOftalmica', back_populates='ordenes')
    estado = db.relationship('EstadoOrden')
    metodo_pago = db.relationship('MetodoPago')
    detalles = db.relationship('DetalleOrden', back_populates='orden', cascade="all, delete-orphan")
    
class DetalleOrden(db.Model):
    """Reemplaza la antigua tabla 'detalle_ventas'"""
    __tablename__ = 'detalles_orden'
    id = db.Column(db.Integer, primary_key=True)
    cantidad = db.Column(db.Integer, nullable=False)
    precio_unitario = db.Column(db.Numeric(10, 2), nullable=False)
    subtotal = db.Column(db.Numeric(10, 2), nullable=False)

    orden_id = db.Column(db.Integer, db.ForeignKey('ordenes_trabajo.id', ondelete='CASCADE'), nullable=False, index=True)
    producto_id = db.Column(db.Integer, db.ForeignKey('productos.id', ondelete='RESTRICT'), nullable=False, index=True)

    orden = db.relationship('OrdenTrabajo', back_populates='detalles')
    producto = db.relationship('Producto')
    
# ==============================================================================
# HISTORIAL Y TRAZABILIDAD (MÉTRICAS)
# ==============================================================================

class HistorialEstado(db.Model):
    __tablename__ = 'historial_estados'
    
    # Constantes para evitar errores tipográficos
    TIPO_RECETA = 'RECETA'
    TIPO_ORDEN = 'ORDEN'

    id = db.Column(db.Integer, primary_key=True)
    tipo_entidad = db.Column(db.String(20), nullable=False) 
    entidad_id = db.Column(db.Integer, nullable=False)
    
    # Polimórficos: Pueden apuntar a estados_receta o estados_orden
    estado_anterior_id = db.Column(db.Integer, nullable=True)
    estado_nuevo_id = db.Column(db.Integer, nullable=False)
    
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuarios.id', ondelete='SET NULL'), nullable=True)
    fecha = db.Column(db.DateTime, default=obtener_hora_chile)
    observacion = db.Column(db.String(255), nullable=True)

    usuario = db.relationship('Usuario')