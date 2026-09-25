# blueprints/ordenes.py
from flask import Blueprint, render_template, request, redirect, url_for, flash, send_file
from flask_login import login_required, current_user
from sqlalchemy import or_
import io
from xml.sax.saxutils import escape

# Modelos e Inclusiones
from models import (db, OrdenTrabajo, DetalleOrden, Paciente, RecetaOftalmica, 
                    Producto, EstadoOrden, EstadoReceta, MetodoPago, HistorialEstado,
                    TipoOrdenTrabajo, Cotizacion, EstadoCotizacion, MovimientoInventario,
                    TipoMovimientoInventario)
from utils import registrar_log_sistema, obtener_hora_chile, admin_required
from services.inventario_service import InventarioService

# Configuración ReportLab
from reportlab.lib.pagesizes import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

ordenes_bp = Blueprint('ordenes', __name__, template_folder='../templates', url_prefix='/ordenes')

# ==============================================================================
# CONSTANTES DE NEGOCIO
# ==============================================================================
ESTADO_OT_PENDIENTE = "Pendiente de Pago"
ESTADO_OT_PAGADA = "Pagada"
ESTADO_OT_ENTREGADA = "Entregada"
ESTADO_OT_ANULADA = "Anulada"

ESTADO_RECETA_LISTA = "Lista"
ESTADO_RECETA_ANULADA = "Anulada"

ESTADO_COT_VIGENTE = "Vigente"
ESTADO_COT_CONVERTIDA = "Convertida en Venta"

# ==============================================================================
# HELPER DE VALIDACIÓN DE ESTADOS
# ==============================================================================
def get_estados_requeridos():
    """Valida la consistencia estructural de la base de datos."""
    estados = {
        'pendiente': EstadoOrden.query.filter_by(nombre=ESTADO_OT_PENDIENTE).first(),
        'pagada': EstadoOrden.query.filter_by(nombre=ESTADO_OT_PAGADA).first(),
        'entregada': EstadoOrden.query.filter_by(nombre=ESTADO_OT_ENTREGADA).first(),
        'anulada': EstadoOrden.query.filter_by(nombre=ESTADO_OT_ANULADA).first()
    }
    return estados

# ==============================================================================
# ENDPOINTS Y RUTAS DEL FLUJO
# ==============================================================================

@ordenes_bp.route('/listar')
@login_required
def listar_ordenes():
    """Muestra el historial de órdenes generadas con filtros dinámicos."""
    page = request.args.get('page', 1, type=int)
    busqueda = request.args.get('busqueda', '').strip()
    estado_filtro = request.args.get('estado_id', '')

    query = OrdenTrabajo.query.join(Paciente)

    if busqueda:
        query = query.filter(
            or_(Paciente.rut.ilike(f'%{busqueda}%'),
                Paciente.nombre_completo.ilike(f'%{busqueda}%'),
                OrdenTrabajo.id == busqueda)
        )
    
    if estado_filtro and estado_filtro.isdigit():
        query = query.filter(OrdenTrabajo.estado_id == int(estado_filtro))

    pagination = query.order_by(OrdenTrabajo.fecha_creacion.desc()).paginate(page=page, per_page=15, error_out=False)
    
    estados_ot = EstadoOrden.query.order_by(EstadoOrden.orden).all()
    metodos_pago = MetodoPago.query.filter_by(activo=True).order_by(MetodoPago.nombre).all()

    return render_template('ordenes/listar.html', 
                           pagination=pagination, 
                           busqueda=busqueda, 
                           estados_ot=estados_ot,
                           metodos_pago=metodos_pago,
                           estado_filtro=estado_filtro)

@ordenes_bp.route('/crear/buscar-paciente')
@login_required
def buscar_paciente():
    """Paso 1: Buscar paciente, recetas LISTAS obligatorias y cotizaciones vigentes."""
    busqueda = request.args.get('busqueda', '').strip()
    paciente = None
    recetas = []
    cotizaciones = []

    if busqueda:
        paciente = Paciente.query.filter((Paciente.rut == busqueda) | (Paciente.nombre_completo.ilike(f'%{busqueda}%'))).first()
        if paciente:
            estado_lista = EstadoReceta.query.filter_by(nombre=ESTADO_RECETA_LISTA).first()
            if estado_lista:
                recetas = RecetaOftalmica.query.filter_by(
                    paciente_id=paciente.id, 
                    estado_id=estado_lista.id, 
                    activa=True
                ).order_by(RecetaOftalmica.fecha_registro.desc()).all()
            
            estado_cot_vigente = EstadoCotizacion.query.filter_by(nombre=ESTADO_COT_VIGENTE).first()
            if estado_cot_vigente:
                cotizaciones_crudas = Cotizacion.query.filter_by(
                    paciente_id=paciente.id, 
                    estado_id=estado_cot_vigente.id
                ).order_by(Cotizacion.fecha_emision.desc()).all()
                # Filtramos al vuelo las que no estén vencidas
                cotizaciones = [c for c in cotizaciones_crudas if not c.esta_vencida]
        else:
            flash('Paciente no encontrado en los registros clínicos.', 'warning')

    return render_template('ordenes/seleccionar_paciente.html', 
                           paciente=paciente, 
                           recetas=recetas, 
                           cotizaciones=cotizaciones, 
                           busqueda=busqueda)

@ordenes_bp.route('/crear/formulario', methods=['GET', 'POST'])
@login_required
def formulario_orden():
    """Paso 2: Validar seguridad, compilar el carrito y generar la OT."""
    paciente_id = request.args.get('paciente_id', type=int)
    receta_id = request.args.get('receta_id', type=int)
    cotizacion_id = request.args.get('cotizacion_id', type=int)

    # 1. Validaciones Base (IDOR y Existencia)
    if not paciente_id or not receta_id:
        flash('SEGURIDAD: Toda Orden de Trabajo requiere estar vinculada a una receta médica.', 'danger')
        return redirect(url_for('ordenes.buscar_paciente'))

    paciente = Paciente.query.get_or_404(paciente_id)
    receta = RecetaOftalmica.query.get_or_404(receta_id)
    
    if receta.paciente_id != paciente.id:
        flash('SEGURIDAD: La receta médica no pertenece a este paciente.', 'danger')
        return redirect(url_for('ordenes.buscar_paciente'))
    if not receta.activa or receta.estado.nombre != ESTADO_RECETA_LISTA:
        flash('La receta seleccionada ya no está Vigente o no está Lista para cobro.', 'warning')
        return redirect(url_for('ordenes.buscar_paciente'))

    estados = get_estados_requeridos()
    if not estados['pendiente'] or not estados['pagada'] or not estados['entregada']:
        flash('Error de configuración: Faltan estados de orden en la BD.', 'danger')
        return redirect(url_for('ordenes.buscar_paciente'))

    # 2. Prevenir reutilización de receta activa
    ot_existente = OrdenTrabajo.query.filter(
        OrdenTrabajo.receta_id == receta.id,
        OrdenTrabajo.estado_id.in_([estados['pendiente'].id, estados['pagada'].id, estados['entregada'].id])
    ).first()
    if ot_existente:
        flash(f'La receta clínica seleccionada ya fue utilizada en la Orden de Trabajo #{ot_existente.id} vigente.', 'danger')
        return redirect(url_for('ordenes.buscar_paciente'))

    # 3. Lógica de Importación de Cotización (IDOR y Estado)
    cotizacion_importada = None
    precios_acordados = {} # Diccionario para respetar el precio de la cotización
    
    if cotizacion_id:
        cotizacion_importada = Cotizacion.query.get_or_404(cotizacion_id)
        if cotizacion_importada.paciente_id != paciente.id:
            flash('SEGURIDAD: La cotización seleccionada no pertenece a este paciente.', 'danger')
            return redirect(url_for('ordenes.buscar_paciente'))
        if cotizacion_importada.estado_logico != ESTADO_COT_VIGENTE:
            flash('La cotización seleccionada ya no está vigente.', 'warning')
            return redirect(url_for('ordenes.buscar_paciente'))
        
        # Guardamos los precios acordados en la cotización
        for det in cotizacion_importada.detalles:
            precios_acordados[det.producto_id] = det.precio_unitario

    productos_disponibles = Producto.query.filter_by(activo=True).order_by(Producto.nombre).all()
    tipos_orden = TipoOrdenTrabajo.query.filter_by(activo=True).all()

    if request.method == 'POST':
        tipo_orden_id = request.form.get('tipo_orden_id', type=int)
        productos_ids_brutos = request.form.getlist('producto_id[]')
        cantidades_brutas = request.form.getlist('cantidad[]')

        # 4. Validaciones Estrictas del POST
        tipo_orden = TipoOrdenTrabajo.query.filter_by(id=tipo_orden_id, activo=True).first()
        if not tipo_orden:
            flash('Error: Debe seleccionar un tipo de orden válido y activo.', 'danger')
            return redirect(request.url)

        if not productos_ids_brutos or not cantidades_brutas:
            flash('Error: Debe añadir al menos un artículo a la orden.', 'danger')
            return redirect(request.url)
            
        if len(productos_ids_brutos) != len(cantidades_brutas):
            flash('Error Crítico: Los productos y cantidades enviadas no coinciden. Intento rechazado.', 'danger')
            return redirect(request.url)

        # 5. Consolidación de Carrito (Previene productos duplicados)
        carrito_consolidado = {}
        for p_id, cant_str in zip(productos_ids_brutos, cantidades_brutas):
            if not p_id or not cant_str: continue
            try:
                p_id_int = int(p_id)
                cantidad = int(cant_str)
                if cantidad <= 0:
                    raise ValueError
            except ValueError:
                flash('Error Crítico: Formato de datos alterado. Valores deben ser enteros positivos.', 'danger')
                return redirect(request.url)

            # Suma cantidades si envían el mismo producto dos veces
            carrito_consolidado[p_id_int] = carrito_consolidado.get(p_id_int, 0) + cantidad

        es_beneficio_gratuito = tipo_orden.nombre in ['Resolutividad', 'Garantía']
        total_orden = 0
        detalles_a_guardar = []

        # 6. Procesamiento del Carrito Consolidado
        for p_id_int, cantidad in carrito_consolidado.items():
            prod = Producto.query.filter_by(id=p_id_int, activo=True).first()
            if not prod:
                flash(f'Error: Un producto seleccionado no es válido o está inactivo.', 'danger')
                return redirect(request.url)

            # Opción B (Cotización): Respetar el precio de la cotización si existe, si no usar el actual
            precio_unitario = precios_acordados.get(p_id_int, prod.precio)
            descuento_unitario = 0

            # Lógica de Beneficio: Subtotal 0, descuento_unitario absorbe el costo para trazabilidad
            if es_beneficio_gratuito:
                descuento_unitario = precio_unitario
                subtotal = 0
            else:
                subtotal = precio_unitario * cantidad

            total_orden += subtotal

            detalle = DetalleOrden(
                producto_id=prod.id,
                cantidad=cantidad,
                precio_unitario=precio_unitario,
                descuento_aplicado=descuento_unitario,
                subtotal=subtotal
            )
            detalles_a_guardar.append(detalle)

        # La OT nace en PENDIENTE DE PAGO
        nueva_orden = OrdenTrabajo(
            total=total_orden,
            estado_id=estados['pendiente'].id,
            tipo_orden_id=tipo_orden.id,
            cotizacion_id=cotizacion_id,
            usuario_id=current_user.id,
            paciente_id=paciente.id,
            receta_id=receta.id,
            fecha_creacion=obtener_hora_chile()
        )
        nueva_orden.detalles.extend(detalles_a_guardar)

        try:
            db.session.add(nueva_orden)
            
            # Cambiamos estado de la cotización si se importó
            if cotizacion_importada:
                estado_convertida = EstadoCotizacion.query.filter_by(nombre=ESTADO_COT_CONVERTIDA).first()
                if estado_convertida:
                    cotizacion_importada.estado_id = estado_convertida.id

            db.session.flush()

            historial = HistorialEstado(
                tipo_entidad=HistorialEstado.TIPO_ORDEN,
                entidad_id=nueva_orden.id,
                estado_nuevo_id=estados['pendiente'].id,
                usuario_id=current_user.id,
                fecha=obtener_hora_chile(),
                observacion=f"Generada como {tipo_orden.nombre}. Pendiente de pago."
            )
            db.session.add(historial)
            
            db.session.commit()
            registrar_log_sistema("Generación Orden", f"OT Nº {nueva_orden.id} creada ({tipo_orden.nombre}) para paciente {paciente.rut}.")
            flash(f'Orden de Trabajo Nº {nueva_orden.id} generada exitosamente. Lista para procesar pago.', 'success')
            return redirect(url_for('ordenes.listar_ordenes'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error al generar la orden: {str(e)}', 'danger')

    return render_template('ordenes/crear_orden.html', 
                           paciente=paciente, 
                           receta=receta, 
                           cotizacion=cotizacion_importada,
                           productos=productos_disponibles,
                           tipos_orden=tipos_orden)

@ordenes_bp.route('/registrar_pago/<int:id>', methods=['POST'])
@login_required
def registrar_pago_orden(id):
    """
    ÁREA DE CAJA: Recibe el pago y DESCUENTA EL STOCK vía InventarioService.
    La receta clínica se mantiene viva (Lista) hasta la Entrega.
    """
    orden = OrdenTrabajo.query.get_or_404(id)
    metodo_pago_id = request.form.get('metodo_pago_id')

    estados = get_estados_requeridos()
    if not estados['pendiente'] or not estados['pagada']:
        flash('Error estructural: Faltan estados de orden en la BD.', 'danger')
        return redirect(url_for('ordenes.listar_ordenes'))

    if orden.estado_id != estados['pendiente'].id:
        flash('Error: Solo se puede registrar el pago de órdenes en estado Pendiente de Pago.', 'danger')
        return redirect(url_for('ordenes.listar_ordenes'))

    metodo_pago = MetodoPago.query.filter_by(id=metodo_pago_id, activo=True).first()
    if not metodo_pago:
        flash('Error: El método de pago seleccionado no es válido o está inactivo.', 'danger')
        return redirect(url_for('ordenes.listar_ordenes'))

    try:
        es_beneficio = orden.tipo_orden.nombre in ['Resolutividad', 'Garantía']
        
        for d in orden.detalles:
            InventarioService.registrar_salida_orden(
                producto_id=d.producto_id,
                cantidad=d.cantidad,
                es_resolutividad=es_beneficio,
                orden_id=orden.id,
                usuario_id=current_user.id
            )

        estado_anterior = orden.estado_id
        orden.estado_id = estados['pagada'].id
        orden.metodo_pago_id = metodo_pago.id
        orden.modificado_por = current_user.id
        orden.fecha_modificacion = obtener_hora_chile()

        historial_ot = HistorialEstado(
            tipo_entidad=HistorialEstado.TIPO_ORDEN,
            entidad_id=orden.id,
            estado_anterior_id=estado_anterior,
            estado_nuevo_id=estados['pagada'].id,
            usuario_id=current_user.id,
            fecha=obtener_hora_chile(),
            observacion="Pago/Confirmación registrado. Stock descontado del Kardex."
        )
        db.session.add(historial_ot)

        db.session.commit()
        registrar_log_sistema("Confirmación Orden", f"OT Nº {orden.id} pagada. Kardex actualizado.")
        flash(f'¡Éxito! Orden Nº {orden.id} confirmada. El stock ha sido descontado oficialmente.', 'success')

    except ValueError as e:
        db.session.rollback()
        flash(f"⚠️ Operación cancelada por el Inventario: {str(e)}", 'danger')
    except Exception as e:
        db.session.rollback()
        flash(f"Error inesperado al confirmar: {str(e)}", 'danger')

    return redirect(url_for('ordenes.listar_ordenes'))

@ordenes_bp.route('/marcar_entregada/<int:id>', methods=['POST'])
@login_required
def marcar_entregada(id):
    """
    Cambia estado a Entregada. AQUÍ SE CIERRA LA RECETA CLÍNICA.
    """
    orden = OrdenTrabajo.query.get_or_404(id)
    estados = get_estados_requeridos()

    if not estados['pagada'] or not estados['entregada']:
        flash('Error estructural: Faltan estados de orden en la BD.', 'danger')
        return redirect(url_for('ordenes.listar_ordenes'))

    if orden.estado_id != estados['pagada'].id:
        flash('Solo se pueden entregar órdenes que ya hayan sido pagadas/confirmadas.', 'warning')
        return redirect(url_for('ordenes.listar_ordenes'))

    orden.estado_id = estados['entregada'].id
    orden.modificado_por = current_user.id
    orden.fecha_modificacion = obtener_hora_chile()

    db.session.add(HistorialEstado(
        tipo_entidad=HistorialEstado.TIPO_ORDEN,
        entidad_id=orden.id,
        estado_anterior_id=estados['pagada'].id,
        estado_nuevo_id=estados['entregada'].id,
        usuario_id=current_user.id,
        fecha=obtener_hora_chile(),
        observacion="El paciente retiró los lentes en sucursal."
    ))

    # Cierre de la receta clínica
    if orden.receta and orden.receta.activa:
        receta = orden.receta
        receta.activa = False
        receta.modificado_por = current_user.id
        receta.fecha_modificacion = obtener_hora_chile()

        db.session.add(HistorialEstado(
            tipo_entidad=HistorialEstado.TIPO_RECETA,
            entidad_id=receta.id,
            estado_anterior_id=receta.estado_id,
            estado_nuevo_id=receta.estado_id,
            usuario_id=current_user.id,
            fecha=obtener_hora_chile(),
            observacion=f"Receta cerrada definitivamente tras entregar la OT #{orden.id} al paciente."
        ))

    db.session.commit()
    flash(f'Orden #{orden.id} marcada como entregada al paciente.', 'success')
    return redirect(url_for('ordenes.listar_ordenes'))

@ordenes_bp.route('/anular/<int:id>', methods=['POST'])
@login_required
@admin_required
def anular_orden(id):
    """
    Solo Admin. Anula la orden. REVERSA EL STOCK recuperando el costo histórico exacto 
    desde el Movimiento original en el Kardex. Anula la Receta.
    """
    orden = OrdenTrabajo.query.get_or_404(id)
    estados = get_estados_requeridos()
    estado_receta_anulada = EstadoReceta.query.filter_by(nombre=ESTADO_RECETA_ANULADA).first()

    if not estados['anulada'] or not estado_receta_anulada:
        flash('Error estructural: Faltan estados en la BD.', 'danger')
        return redirect(url_for('ordenes.listar_ordenes'))

    if orden.estado_id == estados['anulada'].id:
        flash('La orden ya se encuentra anulada.', 'warning')
        return redirect(url_for('ordenes.listar_ordenes'))

    try:
        # REVERSIÓN ESTRICTA DE KARDEX (Solo si salió stock)
        if orden.estado_id in [estados['pagada'].id, estados['entregada'].id]:
            for d in orden.detalles:
                # Buscar el movimiento de Salida Original
                mov_salida_original = MovimientoInventario.query.join(TipoMovimientoInventario).filter(
                    MovimientoInventario.referencia_tipo == "ORDEN_TRABAJO",
                    MovimientoInventario.referencia_id == orden.id,
                    MovimientoInventario.producto_id == d.producto_id,
                    TipoMovimientoInventario.operacion == -1 # Garantiza que fue la salida
                ).first()
                
                if not mov_salida_original:
                    raise ValueError(f"No se encontró el movimiento Kardex de salida original para el producto ID {d.producto_id}. Integridad comprometida.")

                InventarioService.reversar_salida_orden(
                    producto_id=d.producto_id,
                    cantidad=d.cantidad,
                    costo_unitario_original=mov_salida_original.costo_unitario, 
                    orden_id=orden.id,
                    usuario_id=current_user.id
                )

        estado_anterior = orden.estado_id
        orden.estado_id = estados['anulada'].id
        orden.modificado_por = current_user.id
        orden.fecha_modificacion = obtener_hora_chile()

        db.session.add(HistorialEstado(
            tipo_entidad=HistorialEstado.TIPO_ORDEN,
            entidad_id=orden.id,
            estado_anterior_id=estado_anterior,
            estado_nuevo_id=estados['anulada'].id,
            usuario_id=current_user.id,
            fecha=obtener_hora_chile(),
            observacion="Orden anulada por administrador. Stock reversado histórico si correspondía."
        ))

        # Anulación de Receta
        if orden.receta:
            receta = orden.receta
            estado_anterior_receta = receta.estado_id
            
            receta.estado_id = estado_receta_anulada.id
            receta.activa = False
            receta.modificado_por = current_user.id
            receta.fecha_modificacion = obtener_hora_chile()
            
            db.session.add(HistorialEstado(
                tipo_entidad=HistorialEstado.TIPO_RECETA,
                entidad_id=receta.id,
                estado_anterior_id=estado_anterior_receta,
                estado_nuevo_id=estado_receta_anulada.id,
                usuario_id=current_user.id,
                fecha=obtener_hora_chile(),
                observacion=f"Anulada automáticamente al anular la OT #{orden.id}."
            ))

        db.session.commit()
        registrar_log_sistema("Anulación Orden", f"OT Nº {orden.id} anulada. Kardex/Historial actualizados.")
        flash(f'Orden de Trabajo Nº {orden.id} anulada con éxito.', 'info')
        
    except ValueError as e:
        db.session.rollback()
        flash(f'Error al reversar inventario: {str(e)}', 'danger')
    except Exception as e:
        db.session.rollback()
        flash(f'Error crítico al anular: {str(e)}', 'danger')

    return redirect(url_for('ordenes.listar_ordenes'))

@ordenes_bp.route('/pdf/<int:id>')
@login_required
def generar_pdf(id):
    """Genera el ticket térmico protegiendo los datos con XML escape y validando estados."""
    orden = OrdenTrabajo.query.get_or_404(id)
    estados = get_estados_requeridos()
    
    if orden.estado_id not in [estados['pagada'].id, estados['entregada'].id]:
        flash('⚠️ No se puede imprimir el comprobante de una OT que no esté Pagada o Entregada.', 'danger')
        return redirect(url_for('ordenes.listar_ordenes'))
    
    buffer = io.BytesIO()
    PAGE_WIDTH = 80 * mm
    PAGE_HEIGHT = 260 * mm
    
    doc = SimpleDocTemplate(buffer, pagesize=(PAGE_WIDTH, PAGE_HEIGHT), rightMargin=4*mm, leftMargin=4*mm, topMargin=4*mm, bottomMargin=4*mm)
    story = []
    
    title_style = ParagraphStyle('TkTitle', fontName='Helvetica-Bold', fontSize=11, alignment=1, spaceAfter=2)
    subtitle_style = ParagraphStyle('TkSub', fontName='Helvetica', fontSize=8, alignment=1, spaceAfter=4)
    bold_sec_style = ParagraphStyle('TkSec', fontName='Helvetica-Bold', fontSize=8, leading=10, spaceBefore=4, spaceAfter=4)
    cell_left = ParagraphStyle('CelLeft', fontName='Helvetica', fontSize=7.5, leading=9, alignment=0)
    cell_center = ParagraphStyle('CelCenter', fontName='Helvetica', fontSize=7.5, leading=9, alignment=1)
    cell_right = ParagraphStyle('CelRight', fontName='Helvetica', fontSize=7.5, leading=9, alignment=2)
    cell_left_b = ParagraphStyle('CelLeftB', parent=cell_left, fontName='Helvetica-Bold')
    cell_center_b = ParagraphStyle('CelCenterB', parent=cell_center, fontName='Helvetica-Bold')
    cell_right_b = ParagraphStyle('CelRightB', parent=cell_right, fontName='Helvetica-Bold')

    def agregar_linea_divisoria():
        linea = Table([['']], colWidths=[72*mm])
        linea.setStyle(TableStyle([
            ('LINEBELOW', (0,0), (-1,-1), 0.6, colors.black),
            ('BOTTOMPADDING', (0,0), (-1,-1), 0),
            ('TOPPADDING', (0,0), (-1,-1), 2),
        ]))
        story.append(linea)
        story.append(Spacer(1, 4))

    # ENCABEZADO
    story.append(Paragraph("ÓPTICA MUNICIPAL", title_style))
    story.append(Paragraph("Municipalidad de Alto Hospicio", subtitle_style))
    story.append(Spacer(1, 2))
    story.append(Paragraph(f"ORDEN DE TRABAJO Nº {orden.id}", title_style))
    story.append(Paragraph(f"Tipo: {orden.tipo_orden.nombre}", ParagraphStyle('TkSubB', fontName='Helvetica-Bold', fontSize=8, alignment=1)))
    story.append(Paragraph(f"Fecha: {orden.fecha_creacion.strftime('%d-%m-%Y %H:%M')}", subtitle_style))
    agregar_linea_divisoria()
    
    # PACIENTE (Protegido con Escape HTML)
    story.append(Paragraph("DATOS DEL PACIENTE", bold_sec_style))
    story.append(Paragraph(f"<b>RUT:</b> {escape(orden.paciente.rut)}", cell_left))
    story.append(Paragraph(f"<b>Nombre:</b> {escape(orden.paciente.nombre_completo)}", cell_left))
    agregar_linea_divisoria()
    
    # RECETA (Texto referencial)
    if orden.receta:
        story.append(Paragraph("INFORMACIÓN CLÍNICA", bold_sec_style))
        story.append(Paragraph(f"Lentes elaborados según Receta Médica ID #{orden.receta.id}.", cell_left))
        story.append(Spacer(1, 4))
        agregar_linea_divisoria()
    
    # DETALLE PRODUCTOS
    story.append(Paragraph("DETALLE DE ARTÍCULOS", bold_sec_style))
    articulos_data = [[
        Paragraph('<b>Cant</b>', cell_center_b), 
        Paragraph('<b>Insumo</b>', cell_left_b), 
        Paragraph('<b>Total</b>', cell_right_b)
    ]]
    
    suma_descuentos = 0
    for d in orden.detalles:
        suma_descuentos += (d.descuento_aplicado * d.cantidad)
        articulos_data.append([
            Paragraph(str(d.cantidad), cell_center),
            Paragraph(escape(d.producto.nombre), cell_left),
            Paragraph(f"${int(d.subtotal):,}".replace(",", "."), cell_right)
        ])
        
    t_articulos = Table(articulos_data, colWidths=[9*mm, 43*mm, 20*mm])
    t_articulos.setStyle(TableStyle([('VALIGN', (0,0), (-1,-1), 'TOP'), ('LINEBELOW', (0,0), (-1,0), 0.5, colors.black)]))
    story.append(t_articulos)
    agregar_linea_divisoria()
    
    # TOTALES
    if suma_descuentos > 0:
        val_ref = int(orden.total + suma_descuentos)
        story.append(Paragraph(f"Valor Referencial: ${val_ref:,}".replace(",", "."), ParagraphStyle('Ref', fontName='Helvetica', fontSize=8, alignment=2)))
        story.append(Paragraph(f"Beneficio Aplicado: -${int(suma_descuentos):,}".replace(",", "."), ParagraphStyle('Desc', fontName='Helvetica', fontSize=8, alignment=2, textColor=colors.red)))
        
    total_str = f"${int(orden.total):,}".replace(",", ".")
    story.append(Paragraph(f"TOTAL A PAGAR: {total_str}", ParagraphStyle('TkTotal', fontName='Helvetica-Bold', fontSize=11, alignment=2, spaceBefore=4)))
    
    if orden.metodo_pago:
        story.append(Paragraph(f"<b>PAGO:</b> {orden.metodo_pago.nombre}", ParagraphStyle('TkPago', fontName='Helvetica', fontSize=7.5, alignment=2, spaceAfter=10)))
    
    # PIE
    story.append(Paragraph("Documento interno de control de caja.", subtitle_style))
    
    doc.build(story)
    buffer.seek(0)
    return send_file(buffer, as_attachment=False, mimetype='application/pdf', download_name=f"ticket_OT_{orden.id}.pdf")