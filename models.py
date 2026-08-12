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

class ConfiguracionSistema(db.Model):
    """Almacena parámetros globales como Días de Vigencia de Cotizaciones, IVA, etc."""
    __tablename__ = 'configuracion_sistema'
    id = db.Column(db.Integer, primary_key=True)
    clave = db.Column(db.String(50), unique=True, nullable=False, index=True)
    valor = db.Column(db.String(255), nullable=False)
    descripcion = db.Column(db.String(255), nullable=True)

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
# MÓDULO DE INVENTARIO Y KARDEX (REFACTORIZADO)
# ==============================================================================

class CategoriaProducto(db.Model):
    __tablename__ = 'categorias_productos'
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), unique=True, nullable=False)
    activo = db.Column(db.Boolean, default=True)
    
    productos = db.relationship('Producto', back_populates='categoria')
    
class Proveedor(db.Model):
    __tablename__ = 'proveedores'
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(255), nullable=False)
    activo = db.Column(db.Boolean, default=True)
    
    productos = db.relationship('Producto', back_populates='proveedor')

class Producto(db.Model):
    __tablename__ = 'productos'
    id = db.Column(db.Integer, primary_key=True)
    codigo = db.Column(db.String(50), nullable=True, index=True)
    nombre = db.Column(db.String(255), nullable=False)
    precio_compra = db.Column(db.Numeric(10, 2), nullable=False, default=0.00) # Precio Compra (Neto)
    precio = db.Column(db.Numeric(10, 2), nullable=False) # Precio Venta (IVA Aplicado)
    stock = db.Column(db.Integer, default=0, nullable=False)
    stock_minimo = db.Column(db.Integer, default=5, nullable=False)
    activo = db.Column(db.Boolean, default=True)

    categoria_id = db.Column(db.Integer, db.ForeignKey('categorias_productos.id', ondelete='RESTRICT'), nullable=False, index=True)
    proveedor_id = db.Column(db.Integer, db.ForeignKey('proveedores.id', ondelete='SET NULL'), nullable=True)
    
    categoria = db.relationship('CategoriaProducto', back_populates='productos')
    proveedor = db.relationship('Proveedor', back_populates='productos')
    
class TipoMovimientoInventario(db.Model):
    __tablename__ = 'tipos_movimiento_inventario'
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(50), unique=True, nullable=False)
    operacion = db.Column(db.Integer, nullable=False) # 1 o -1
    descripcion = db.Column(db.String(255), nullable=True)
    activo = db.Column(db.Boolean, default=True)

class MovimientoInventario(db.Model):
    __tablename__ = 'movimientos_inventario'
    id = db.Column(db.Integer, primary_key=True)
    
    # Cantidad y Costo Histórico
    cantidad = db.Column(db.Integer, nullable=False)
    costo_unitario = db.Column(db.Numeric(10, 2), nullable=False, default=0.00)
    stock_anterior = db.Column(db.Integer, nullable=False)
    stock_nuevo = db.Column(db.Integer, nullable=False)
    
    # Trazabilidad Interna (Relacional)
    referencia_tipo = db.Column(db.String(50), nullable=True, index=True)
    referencia_id = db.Column(db.Integer, nullable=True, index=True)
    
    # Trazabilidad Externa (Documental)
    documento_tipo = db.Column(db.String(50), nullable=True)
    documento_numero = db.Column(db.String(100), nullable=True, index=True)
    
    motivo = db.Column(db.String(255), nullable=True)
    fecha = db.Column(db.DateTime, default=obtener_hora_chile, index=True)
    
    # Llaves Foráneas
    producto_id = db.Column(db.Integer, db.ForeignKey('productos.id', ondelete='RESTRICT'), nullable=False, index=True)
    tipo_movimiento_id = db.Column(db.Integer, db.ForeignKey('tipos_movimiento_inventario.id', ondelete='RESTRICT'), nullable=False, index=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuarios.id', ondelete='RESTRICT'), nullable=False, index=True)
    
    # Relaciones
    producto = db.relationship('Producto')
    tipo_movimiento = db.relationship('TipoMovimientoInventario')
    usuario = db.relationship('Usuario')

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
    cotizaciones = db.relationship('Cotizacion', back_populates='paciente', cascade="all, delete-orphan")

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
    archivo_receta = db.Column(db.String(255), nullable=False)
    
    # Generales
    observaciones = db.Column(db.Text, nullable=True)
    activa = db.Column(db.Boolean, default=True, nullable=False)
    
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

class TipoOrdenTrabajo(db.Model):
    __tablename__ = 'tipos_orden_trabajo'
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(50), unique=True, nullable=False)
    descripcion = db.Column(db.String(255), nullable=True)
    activo = db.Column(db.Boolean, default=True)
    
class OrdenTrabajo(db.Model):
    __tablename__ = 'ordenes_trabajo'
    id = db.Column(db.Integer, primary_key=True)
    fecha_creacion = db.Column(db.DateTime, default=obtener_hora_chile, index=True)
    total = db.Column(db.Numeric(10, 2), nullable=False)
    
    # Llaves Foráneas Nuevas
    tipo_orden_id = db.Column(db.Integer, db.ForeignKey('tipos_orden_trabajo.id', ondelete='RESTRICT'), nullable=False)
    cotizacion_id = db.Column(db.Integer, db.ForeignKey('cotizaciones.id', ondelete='SET NULL'), nullable=True)

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
    tipo_orden = db.relationship('TipoOrdenTrabajo')
    cotizacion = db.relationship('Cotizacion', back_populates='ordenes')
    detalles = db.relationship('DetalleOrden', back_populates='orden', cascade="all, delete-orphan")
    
class DetalleOrden(db.Model):
    __tablename__ = 'detalles_orden'
    id = db.Column(db.Integer, primary_key=True)
    cantidad = db.Column(db.Integer, nullable=False)
    precio_unitario = db.Column(db.Numeric(10, 2), nullable=False)
    descuento_aplicado = db.Column(db.Numeric(10, 2), nullable=False, default=0.00) # Nuevo para soportar 100% de descuento en Resolutividad
    subtotal = db.Column(db.Numeric(10, 2), nullable=False)

    orden_id = db.Column(db.Integer, db.ForeignKey('ordenes_trabajo.id', ondelete='CASCADE'), nullable=False, index=True)
    producto_id = db.Column(db.Integer, db.ForeignKey('productos.id', ondelete='RESTRICT'), nullable=False, index=True)

    orden = db.relationship('OrdenTrabajo', back_populates='detalles')
    producto = db.relationship('Producto')

# ==============================================================================
# MÓDULO DE COTIZACIONES
# ==============================================================================

class EstadoCotizacion(db.Model):
    """Representa los estados posibles de una cotización"""
    __tablename__ = 'estados_cotizacion'
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(50), unique=True, nullable=False)

class Cotizacion(db.Model):
    __tablename__ = 'cotizaciones'
    id = db.Column(db.Integer, primary_key=True)
    
    fecha_emision = db.Column(db.DateTime, default=obtener_hora_chile)
    fecha_vencimiento = db.Column(db.DateTime, nullable=False)
    
    # Paciente estricto (eliminamos anónimo por reglas de negocio)
    paciente_id = db.Column(db.Integer, db.ForeignKey('pacientes.id', ondelete='CASCADE'), nullable=False, index=True)
    
    observaciones = db.Column(db.Text, nullable=True)
    total = db.Column(db.Numeric(10, 2), nullable=False, default=0.00)
    version_documento = db.Column(db.Integer, nullable=False, default=1)
    
    estado_id = db.Column(db.Integer, db.ForeignKey('estados_cotizacion.id', ondelete='RESTRICT'), nullable=False)
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuarios.id', ondelete='RESTRICT'), nullable=False)
    
    paciente = db.relationship('Paciente', back_populates='cotizaciones')
    estado = db.relationship('EstadoCotizacion')
    usuario = db.relationship('Usuario')
    detalles = db.relationship('DetalleCotizacion', back_populates='cotizacion', cascade='all, delete-orphan')
    ordenes = db.relationship('OrdenTrabajo', back_populates='cotizacion')

    @property
    def numero_formateado(self):
        """Genera el número correlativo al vuelo. Ej: COT-2026-0001"""
        return f"COT-{self.fecha_emision.year}-{self.id:04d}"

    @property
    def esta_vencida(self):
        """Calcula al vuelo si la cotización expiró."""
        if self.estado.nombre != 'Vigente':
            return False
        fecha_actual = obtener_hora_chile().date()
        return fecha_actual > self.fecha_vencimiento.date()

    @property
    def estado_logico(self):
        """Devuelve 'Vencida' si expiró, o el estado real (Vigente, Anulada, Convertida en Venta)."""
        if self.esta_vencida:
            return 'Vencida'
        return self.estado.nombre

class DetalleCotizacion(db.Model):
    __tablename__ = 'detalles_cotizacion'
    id = db.Column(db.Integer, primary_key=True)
    cantidad = db.Column(db.Integer, nullable=False, default=1)
    precio_unitario = db.Column(db.Numeric(10, 2), nullable=False)
    subtotal = db.Column(db.Numeric(10, 2), nullable=False)

    cotizacion_id = db.Column(db.Integer, db.ForeignKey('cotizaciones.id', ondelete='CASCADE'), nullable=False, index=True)
    producto_id = db.Column(db.Integer, db.ForeignKey('productos.id', ondelete='RESTRICT'), nullable=False)

    cotizacion = db.relationship('Cotizacion', back_populates='detalles')
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