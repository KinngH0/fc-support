from flask import Blueprint, request, jsonify
from app.services.nexon_api import NexonAPI

api_bp = Blueprint('api', __name__)
nexon_api = NexonAPI()

@api_bp.route('/api/search', methods=['POST'])
def search():
    data = request.get_json()
    character_name = data.get('character_name')
    
    if not character_name:
        return jsonify({'error': '캐릭터 이름이 필요합니다.'}), 400
        
    result = nexon_api.search_character(character_name)
    return jsonify(result) 