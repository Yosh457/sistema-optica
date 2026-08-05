# blueprints/api.py
from flask import Blueprint, request, jsonify
from flask_login import login_required
from models import Paciente

api_bp = Blueprint('api', __name__, url_prefix='/api')

@api_bp.route('/pacientes/buscar', methods=['GET'])
@login_required
def buscar_paciente():
    """
    Recibe un RUT por parámetro GET y devuelve los datos del paciente en JSON.
    Útil para autocompletar formularios mediante AJAX.
    """
    rut = request.args.get('rut', '').strip().upper()
    
    if not rut:
        return jsonify({'success': False, 'mensaje': 'Debe proporcionar un RUT válido.'}), 400
        
    paciente = Paciente.query.filter_by(rut=rut).first()
    
    if paciente:
        return jsonify({
            'success': True,
            'paciente': {
                'id': paciente.id,
                'rut': paciente.rut,
                'nombre_completo': paciente.nombre_completo,
                'telefono': paciente.telefono,
                'direccion': paciente.direccion,
                'establecimiento': paciente.establecimiento.nombre if paciente.establecimiento else 'Sin Recinto'
            }
        })
    else:
        return jsonify({'success': False, 'mensaje': 'Paciente no encontrado en el sistema.'}), 404