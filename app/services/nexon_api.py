import requests
import os
from dotenv import load_dotenv

load_dotenv()

class NexonAPI:
    def __init__(self):
        self.api_key = os.getenv('NX_API_KEY')
        self.base_url = 'https://api.nexon.co.kr'
        
    def search_character(self, character_name):
        headers = {
            'Authorization': f'Bearer {self.api_key}'
        }
        
        # 여기에 실제 Nexon API 엔드포인트와 파라미터를 추가하세요
        response = requests.get(
            f'{self.base_url}/character',
            headers=headers,
            params={'character_name': character_name}
        )
        
        return response.json() 