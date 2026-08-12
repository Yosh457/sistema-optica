# services/inventario_service.py

from models import db, Producto, TipoMovimientoInventario, MovimientoInventario, obtener_hora_chile

class TiposMovimientoConstantes:
    """
    Mapeo estricto a los IDs de la tabla tipos_movimiento_inventario.
    Evita depender de los nombres en texto (Magic Strings).
    """
    INGRESO_COMPRA = 1
    VENTA_COMERCIAL = 2
    RESOLUTIVIDAD = 3
    AJUSTE_POSITIVO = 4
    AJUSTE_NEGATIVO = 5
    INGRESO_REVERSION = 6

class InventarioService:
    """
    Capa de servicio centralizada para el manejo de inventario y Kardex.
    Ningún Blueprint debe modificar Producto.stock directamente.
    Nota: Todos los métodos realizan db.session.flush(). El commit() final 
    debe realizarse en el controlador que invoca el servicio.
    """

    @staticmethod
    def _registrar_movimiento_kardex(producto_id, tipo_movimiento_id, cantidad, costo_unitario, usuario_id, 
                                     referencia_tipo=None, referencia_id=None, 
                                     documento_tipo=None, documento_numero=None, motivo=None):
        """
        MÉTODO PRIVADO.
        Motor central que calcula la matemática del stock y escribe el Kardex inmutable.
        """
        if cantidad <= 0:
            raise ValueError("La cantidad a mover debe ser estrictamente mayor a 0.")

        producto = Producto.query.get(producto_id)
        if not producto:
            raise ValueError(f"El producto ID {producto_id} no existe.")

        tipo_mov = TipoMovimientoInventario.query.get(tipo_movimiento_id)
        if not tipo_mov:
            raise ValueError("Tipo de movimiento de inventario no válido.")

        stock_anterior = producto.stock
        cambio_stock = cantidad * tipo_mov.operacion
        stock_nuevo = stock_anterior + cambio_stock

        # Regla de Oro: No permitir stock negativo
        if stock_nuevo < 0:
            raise ValueError(f"Stock insuficiente para '{producto.nombre}'. Disponible: {stock_anterior}, Solicitado: {cantidad}.")

        # 1. Actualizar el activo
        producto.stock = stock_nuevo

        # 2. Registrar la trazabilidad inmutable
        movimiento = MovimientoInventario(
            producto_id=producto.id,
            tipo_movimiento_id=tipo_mov.id,
            cantidad=cantidad,
            costo_unitario=costo_unitario, # Congelamos el costo en la historia
            stock_anterior=stock_anterior,
            stock_nuevo=stock_nuevo,
            referencia_tipo=referencia_tipo,
            referencia_id=referencia_id,
            documento_tipo=documento_tipo,
            documento_numero=documento_numero,
            motivo=motivo,
            usuario_id=usuario_id,
            fecha=obtener_hora_chile()
        )
        
        db.session.add(movimiento)
        db.session.flush()

        return movimiento

    @staticmethod
    def registrar_ingreso_compra(producto_id, cantidad, costo_unitario, proveedor_id, documento_tipo, documento_numero, usuario_id):
        """
        Registra el ingreso de mercadería desde un proveedor.
        Actualiza el 'Último Precio de Compra' del producto de forma global.
        """
        if not documento_numero or not documento_tipo:
            raise ValueError("Un ingreso por compra exige la trazabilidad de un documento externo (ej. Factura).")

        producto = Producto.query.get(producto_id)
        if producto:
            # Actualizamos la ficha del producto con la información comercial más reciente
            producto.precio_compra = costo_unitario
            producto.proveedor_id = proveedor_id
            db.session.flush()

        return InventarioService._registrar_movimiento_kardex(
            producto_id=producto_id,
            tipo_movimiento_id=TiposMovimientoConstantes.INGRESO_COMPRA,
            cantidad=cantidad,
            costo_unitario=costo_unitario, # Costo real según factura
            usuario_id=usuario_id,
            documento_tipo=documento_tipo,
            documento_numero=documento_numero
        )

    @staticmethod
    def registrar_salida_orden(producto_id, cantidad, es_resolutividad, orden_id, usuario_id):
        """
        Descuenta el stock físico tras la confirmación/pago de una Orden de Trabajo.
        Congela el costo histórico consultando el precio_compra actual del producto.
        """
        producto = Producto.query.get(producto_id)
        costo_congelado = producto.precio_compra if producto else 0.00
        
        tipo_mov_id = TiposMovimientoConstantes.RESOLUTIVIDAD if es_resolutividad else TiposMovimientoConstantes.VENTA_COMERCIAL

        return InventarioService._registrar_movimiento_kardex(
            producto_id=producto_id,
            tipo_movimiento_id=tipo_mov_id,
            cantidad=cantidad,
            costo_unitario=costo_congelado, # Congelamos lo que le costó al municipio
            usuario_id=usuario_id,
            referencia_tipo="ORDEN_TRABAJO",
            referencia_id=orden_id
        )

    @staticmethod
    def reversar_salida_orden(producto_id, cantidad, costo_unitario_original, orden_id, usuario_id):
        """
        Devuelve el stock a vitrina si una OT ya pagada/confirmada se anula.
        Utiliza el mismo costo unitario con el que salió originalmente.
        """
        return InventarioService._registrar_movimiento_kardex(
            producto_id=producto_id,
            tipo_movimiento_id=TiposMovimientoConstantes.INGRESO_REVERSION,
            cantidad=cantidad,
            costo_unitario=costo_unitario_original, # Mismo costo original para no descuadrar finanzas
            usuario_id=usuario_id,
            referencia_tipo="ORDEN_TRABAJO",
            referencia_id=orden_id,
            motivo=f"Reversión por anulación de Orden #{orden_id}"
        )

    @staticmethod
    def registrar_ajuste(producto_id, cantidad, es_positivo, motivo, usuario_id):
        """
        Correcciones manuales por mermas, daños en taller o sobrantes de inventario.
        Exige obligatoriamente un motivo justificado.
        """
        if not motivo or len(motivo.strip()) < 5:
            raise ValueError("Los ajustes de inventario requieren obligatoriamente un motivo detallado.")

        producto = Producto.query.get(producto_id)
        costo_congelado = producto.precio_compra if producto else 0.00

        tipo_mov_id = TiposMovimientoConstantes.AJUSTE_POSITIVO if es_positivo else TiposMovimientoConstantes.AJUSTE_NEGATIVO

        return InventarioService._registrar_movimiento_kardex(
            producto_id=producto_id,
            tipo_movimiento_id=tipo_mov_id,
            cantidad=cantidad,
            costo_unitario=costo_congelado,
            usuario_id=usuario_id,
            motivo=motivo.strip()
        )