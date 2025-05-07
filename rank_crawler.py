import requests
import pandas as pd
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any
import logging
from datetime import datetime
from tqdm import tqdm
import backoff
import sqlite3

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('crawler.log'),
        logging.StreamHandler()
    ]
)

# API 요청 제한 설정
REQUEST_DELAY = 0.1  # 초당 10개 요청으로 제한
MAX_RETRIES = 3
RETRY_DELAY = 1  # 재시도 간격 (초)

def init_database():
    """데이터베이스 초기화 및 테이블 생성 (하이브리드 구조)"""
    conn = sqlite3.connect('players.db')
    c = conn.cursor()
    
    # 유저 기본 정보 테이블
    c.execute('''
        CREATE TABLE IF NOT EXISTS user_info (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nickname TEXT,
            rank INTEGER,
            team_color TEXT,
            formation TEXT,
            value INTEGER,
            score REAL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(nickname, formation, score)
        )
    ''')
    
    # 유저별 포지션별 선수 정보 테이블
    c.execute('''
        CREATE TABLE IF NOT EXISTS user_players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            position TEXT,
            player_name TEXT,
            season TEXT,
            grade TEXT,
            reinforce INTEGER,
            FOREIGN KEY (user_id) REFERENCES user_info(id),
            UNIQUE(user_id, position)
        )
    ''')
    
    # 인덱스 추가 (통계/조회 성능 향상)
    c.execute('CREATE INDEX IF NOT EXISTS idx_user_players_position ON user_players(position)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_user_players_player_name ON user_players(player_name)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_user_info_team_color ON user_info(team_color)')
    conn.commit()
    conn.close()

def update_team_color_stats(conn):
    """팀컬러 통계 업데이트"""
    c = conn.cursor()
    
    # 팀컬러별 통계 계산
    c.execute('''
        INSERT OR REPLACE INTO team_color_stats
        SELECT 
            team_color,
            COUNT(*) as total_users,
            AVG(rank) as avg_rank,
            AVG(score) as avg_score,
            AVG(value) as avg_value,
            MIN(rank) as min_rank,
            MAX(rank) as max_rank,
            MIN(score) as min_score,
            MAX(score) as max_score,
            (SELECT nickname FROM user_rankings u2 WHERE u2.team_color = u1.team_color ORDER BY rank ASC LIMIT 1) as min_ranker,
            (SELECT nickname FROM user_rankings u2 WHERE u2.team_color = u1.team_color ORDER BY rank DESC LIMIT 1) as max_ranker,
            CURRENT_TIMESTAMP
        FROM user_rankings u1
        GROUP BY team_color
    ''')
    
    # 팀컬러별 포메이션 통계 계산
    c.execute('''
        INSERT OR REPLACE INTO team_color_formations
        SELECT 
            team_color,
            formation,
            COUNT(*) as count,
            ROUND(COUNT(*) * 100.0 / (SELECT COUNT(*) FROM user_rankings u2 WHERE u2.team_color = u1.team_color), 1) as percentage,
            CURRENT_TIMESTAMP
        FROM user_rankings u1
        GROUP BY team_color, formation
    ''')
    
    conn.commit()

def update_player_pick_stats(conn):
    """선수 픽률 통계 업데이트"""
    c = conn.cursor()
    
    # 선수별 통계 계산
    c.execute('''
        INSERT OR REPLACE INTO player_pick_stats
        SELECT 
            p.id as player_id,
            COUNT(DISTINCT pu.nickname) as total_users,
            ROUND(COUNT(DISTINCT pu.nickname) * 100.0 / (SELECT COUNT(DISTINCT nickname) FROM user_rankings), 1) as percentage,
            AVG(u.rank) as avg_rank,
            AVG(u.score) as avg_score,
            AVG(u.value) as avg_value,
            MIN(u.rank) as min_rank,
            MAX(u.rank) as max_rank,
            MIN(u.score) as min_score,
            MAX(u.score) as max_score,
            (SELECT nickname FROM user_rankings u2 
             JOIN player_usage pu2 ON u2.nickname = pu2.nickname 
             WHERE pu2.player_id = p.id 
             ORDER BY u2.rank ASC LIMIT 1) as min_ranker,
            (SELECT nickname FROM user_rankings u2 
             JOIN player_usage pu2 ON u2.nickname = pu2.nickname 
             WHERE pu2.player_id = p.id 
             ORDER BY u2.rank DESC LIMIT 1) as max_ranker,
            CURRENT_TIMESTAMP
        FROM players p
        JOIN player_usage pu ON p.id = pu.player_id
        JOIN user_rankings u ON pu.nickname = u.nickname
        GROUP BY p.id
    ''')
    
    # 선수별 포메이션 통계 계산
    c.execute('''
        INSERT OR REPLACE INTO player_formations
        SELECT 
            p.id as player_id,
            u.formation,
            COUNT(*) as count,
            ROUND(COUNT(*) * 100.0 / (
                SELECT COUNT(*) 
                FROM player_usage pu2 
                WHERE pu2.player_id = p.id
            ), 1) as percentage,
            CURRENT_TIMESTAMP
        FROM players p
        JOIN player_usage pu ON p.id = pu.player_id
        JOIN user_rankings u ON pu.nickname = u.nickname
        GROUP BY p.id, u.formation
    ''')
    
    conn.commit()

def make_api_request(url: str, headers: Dict[str, str], params: Dict[str, Any] = None) -> Dict:
    """API 요청을 보내고 응답을 반환하는 함수"""
    for attempt in range(MAX_RETRIES):
        try:
            response = requests.get(url, headers=headers, params=params, timeout=10)
            response.raise_for_status()
            time.sleep(REQUEST_DELAY)
            return response.json()
        except requests.exceptions.RequestException as e:
            if attempt < MAX_RETRIES - 1:
                logging.warning(f"API 요청 실패 (시도 {attempt + 1}/{MAX_RETRIES}): {url}, 에러: {str(e)}")
                time.sleep(RETRY_DELAY * (attempt + 1))
                continue
            logging.error(f"API 요청 실패: {url}, 에러: {str(e)}")
            raise

@backoff.on_exception(backoff.expo, 
                     (requests.exceptions.RequestException, 
                      requests.exceptions.Timeout,
                      requests.exceptions.ConnectionError),
                     max_tries=3)
def fetch_rank_page(page: int, headers: Dict[str, str]) -> List[Dict]:
    """랭킹 페이지 데이터를 가져오는 함수"""
    base_url = "https://api.nexon.co.kr/fifaonline4/v1.0/rankers/division"
    params = {
        "matchtype": 50,
        "offset": (page - 1) * 20,
        "limit": 20
    }
    
    try:
        data = make_api_request(base_url, headers, params)
        return data.get('rankers', [])
    except Exception as e:
        logging.error(f"랭킹 페이지 {page} 수집 실패: {str(e)}")
        return []

@backoff.on_exception(backoff.expo, 
                     (requests.exceptions.RequestException, 
                      requests.exceptions.Timeout,
                      requests.exceptions.ConnectionError),
                     max_tries=3)
def fetch_user_data(accessid: str, headers: Dict[str, str]) -> Dict:
    """사용자 데이터를 가져오는 함수"""
    url = f"https://api.nexon.co.kr/fifaonline4/v1.0/users/{accessid}"
    
    try:
        data = make_api_request(url, headers)
        return data
    except Exception as e:
        logging.error(f"사용자 데이터 수집 실패 (accessid: {accessid}): {str(e)}")
        return {}

@backoff.on_exception(backoff.expo, 
                     (requests.exceptions.RequestException, 
                      requests.exceptions.Timeout,
                      requests.exceptions.ConnectionError),
                     max_tries=3)
def fetch_player_data(accessid: str, headers: Dict[str, str]) -> List[Dict]:
    """선수 데이터를 가져오는 함수"""
    url = f"https://api.nexon.co.kr/fifaonline4/v1.0/users/{accessid}/players"
    
    try:
        data = make_api_request(url, headers)
        return data.get('players', [])
    except Exception as e:
        logging.error(f"선수 데이터 수집 실패 (accessid: {accessid}): {str(e)}")
        return []

def parse_rank_pages(total_users: int = 10000, headers: Dict[str, str] = None) -> List[Dict]:
    """랭킹 페이지를 병렬로 수집하는 함수"""
    if headers is None:
        headers = {
            "Authorization": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhY2Nlc3NJRCI6IjE2ODg5MjM5MjgiLCJpYXQiOjE2NzM0Njg0NzgsInRlbmFudElkIjoiMSJ9.8tGcGJA9H6bgdwmgkQ0Xh4Hx4Hx4Hx4H"
        }
    
    total_pages = (total_users + 19) // 20  # 20명씩 한 페이지
    all_rankers = []
    
    # 병렬 처리 수를 줄여서 API 부하 감소
    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_page = {executor.submit(fetch_rank_page, page, headers): page 
                         for page in range(1, total_pages + 1)}
        
        for future in tqdm(as_completed(future_to_page), total=total_pages, desc="랭킹 데이터 수집"):
            page = future_to_page[future]
            try:
                rankers = future.result()
                if rankers:
                    all_rankers.extend(rankers)
            except Exception as e:
                logging.error(f"페이지 {page} 처리 실패: {str(e)}")
    
    return all_rankers

def save_to_database(rank_data: List[Dict], player_data: List[Dict]):
    """데이터를 데이터베이스에 저장하는 함수 (하이브리드 구조)"""
    conn = sqlite3.connect('players.db')
    c = conn.cursor()
    try:
        user_id_map = {}
        # 유저 기본 정보 저장
        for rank in rank_data:
            c.execute('''
                INSERT OR IGNORE INTO user_info 
                (nickname, rank, team_color, formation, value, score)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                rank['nickname'],
                rank['rank'],
                rank['team_color'],
                rank['formation'],
                rank['value'],
                rank['score']
            ))
            # user_id 조회 및 매핑
            c.execute('''SELECT id FROM user_info WHERE nickname=? AND formation=? AND score=?''',
                      (rank['nickname'], rank['formation'], rank['score']))
            user_id = c.fetchone()[0]
            user_id_map[rank['nickname']] = user_id
        # 유저별 포지션별 선수 정보 저장
        for player in player_data:
            user_id = user_id_map.get(player['nickname'])
            if user_id is None:
                continue
            c.execute('''
                INSERT OR REPLACE INTO user_players 
                (user_id, position, player_name, season, grade, reinforce)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                user_id,
                player['position'],
                player['name'],
                player['season'],
                player['grade'],
                player['reinforce']
            ))
        conn.commit()
        logging.info("데이터베이스 저장 완료 (하이브리드 구조)")
    except Exception as e:
        conn.rollback()
        logging.error(f"데이터베이스 저장 실패: {str(e)}")
        raise
    finally:
        conn.close()

def process_user_data(rankers: List[Dict], headers: Dict[str, str]) -> tuple[List[Dict], List[Dict]]:
    """사용자 데이터를 병렬로 처리하는 함수"""
    rank_data = []
    player_data = []
    
    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_user = {executor.submit(fetch_user_data, ranker['accessid'], headers): ranker 
                         for ranker in rankers}
        
        for future in tqdm(as_completed(future_to_user), total=len(rankers), desc="사용자 데이터 수집"):
            ranker = future_to_user[future]
            try:
                user_data = future.result()
                if user_data:
                    # 랭킹 정보 저장
                    rank_data.append({
                        'nickname': ranker['nickname'],
                        'rank': ranker['rank'],
                        'team_color': user_data.get('teamColor', ''),
                        'formation': user_data.get('formation', ''),
                        'value': user_data.get('value', 0),
                        'score': user_data.get('score', 0)
                    })
                    
                    # 선수 데이터 수집
                    players = fetch_player_data(ranker['accessid'], headers)
                    for player in players:
                        player_data.append({
                            'nickname': ranker['nickname'],
                            'name': player.get('name', ''),
                            'season': player.get('season', ''),
                            'grade': player.get('grade', ''),
                            'position': player.get('position', ''),
                            'reinforce': player.get('reinforce', 0)
                        })
                        
            except Exception as e:
                logging.error(f"사용자 {ranker['nickname']} 데이터 처리 실패: {str(e)}")
    
    return rank_data, player_data

def main():
    """메인 실행 함수"""
    start_time = time.time()
    
    # 데이터베이스 초기화
    init_database()
    
    # API 키 설정
    headers = {
        "Authorization": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhY2Nlc3NJRCI6IjE2ODg5MjM5MjgiLCJpYXQiOjE2NzM0Njg0NzgsInRlbmFudElkIjoiMSJ9.8tGcGJA9H6bgdwmgkQ0Xh4Hx4Hx4Hx4H"
    }
    
    try:
        # 랭킹 데이터 수집
        logging.info("랭킹 데이터 수집 시작")
        rankers = parse_rank_pages(10000, headers)
        
        # 사용자 데이터 처리
        logging.info("사용자 데이터 처리 시작")
        rank_data, player_data = process_user_data(rankers, headers)
        
        # 데이터베이스 저장
        save_to_database(rank_data, player_data)
        
        end_time = time.time()
        execution_time = end_time - start_time
        logging.info(f"데이터 수집 완료. 소요 시간: {execution_time:.2f}초")
        
    except Exception as e:
        logging.error(f"데이터 수집 중 오류 발생: {str(e)}")
        raise

if __name__ == "__main__":
    main() 