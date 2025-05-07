import os
from flask import Flask, render_template, request, jsonify, send_file
from markupsafe import Markup
import requests
from bs4 import BeautifulSoup
import re
import pandas as pd
import xlsxwriter
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from datetime import datetime, timedelta
import tempfile
from functools import lru_cache
import time
from threading import Timer
from collections import OrderedDict, Counter
import atexit
import sqlite3
import threading
import json
import logging
from logging.handlers import RotatingFileHandler
import traceback
import sys

app = Flask(__name__)
API_KEY = os.getenv('FC_API_KEY')

# 캐시 설정
rank_cache = {}
match_cache = {}
cache_timer = None

# 전역 ThreadPoolExecutor 생성
executor = ThreadPoolExecutor(max_workers=400)

# 종료 시 executor 정리
@atexit.register
def cleanup():
    executor.shutdown(wait=True)

def get_next_hour():
    """다음 정각까지 남은 시간(초)을 반환"""
    now = datetime.now()
    next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return next_hour

def clear_cache():
    """캐시 초기화"""
    global rank_cache, match_cache
    rank_cache = {}
    match_cache = {}
    log_info(f"캐시가 초기화되었습니다. - {datetime.now()}")

def schedule_cache_clear():
    """다음 정각에 캐시를 초기화하도록 스케줄링"""
    global cache_timer
    
    def clear_and_reschedule():
        with app.app_context():
            clear_cache()
            schedule_cache_clear()
    
    if cache_timer:
        cache_timer.cancel()
    
    next_hour = get_next_hour()
    delay = (next_hour - datetime.now()).total_seconds()
    cache_timer = Timer(delay, clear_and_reschedule)
    cache_timer.daemon = True
    cache_timer.start()
    print(f"다음 캐시 초기화 예정: {next_hour}")

def create_session():
    session = requests.Session()
    headers = {
        'x-nxopen-api-key': API_KEY,
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    session.headers.update(headers)
    retries = Retry(
        total=3,
        backoff_factor=0.1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"]
    )
    adapter = HTTPAdapter(max_retries=retries, pool_connections=100, pool_maxsize=100)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

# 세션 풀 생성
session_pool = []
MAX_SESSIONS = 10

def get_session():
    global session_pool
    if not session_pool:
        session_pool = [create_session() for _ in range(MAX_SESSIONS)]
    return session_pool[hash(time.time()) % len(session_pool)]

# 메타데이터 캐싱
metadata_session = create_session()
spid_data = None
season_data = None
position_data = None
spid_map = {}
season_map = {}
position_map = {}
# FIFA 포지션 코드(숫자) 기반 그룹으로 변경
position_groups = OrderedDict([
    ("ST", [23, 22, 21]),      # RS, ST, LS
    ("CF", [26, 25, 24]),      # RF, CF, LF
    ("LW", [27]),              # LW
    ("RW", [28]),              # RW
    ("CAM", [18]),             # CAM
    ("LAM", [19]),             # LAM
    ("RAM", [20]),             # RAM
    ("LM", [17]),              # LM
    ("RM", [16]),              # RM
    ("CM", [14, 13, 12]),      # RCM, CM, LCM
    ("CDM", [11, 10, 9]),      # RDM, CDM, LDM
    ("LB", [7, 5]),            # LB, LWB
    ("CB", [3, 2, 4, 6]),      # CB, SW, RCB, LCB
    ("RB", [8, 15]),           # RB, RWB
    ("GK", [0])                # GK
])

@lru_cache(maxsize=1)
def load_metadata():
    global spid_data, season_data, position_data, spid_map, season_map, position_map
    
    def load_meta(url):
        res = metadata_session.get(url, timeout=5)
        return res.json() if res.status_code == 200 else []

    spid_data = load_meta("https://open.api.nexon.com/static/fconline/meta/spid.json")
    season_data = load_meta("https://open.api.nexon.com/static/fconline/meta/seasonid.json")
    position_data = load_meta("https://open.api.nexon.com/static/fconline/meta/spposition.json")

    spid_map = {item['id']: item['name'] for item in spid_data}
    season_map = {item['seasonId']: item['className'].split('(')[0].strip() for item in season_data}
    position_map = {item['spposition']: item['desc'] for item in position_data}

def format_korean_currency(value):
    조 = value // 10_000
    억 = value % 10_000
    if 조 > 0:
        return f"{조}조 {억:,}억"
    else:
        return f"{억:,}억"

def get_cached_rank_data(page, normalized_filter):
    """캐시된 랭킹 데이터 조회"""
    cache_key = f"{page}_{normalized_filter}"
    return rank_cache.get(cache_key)

def parse_rank_pages(start_page, end_page, normalized_filter):
    session = get_session()
    all_results = []
    
    def fetch_page(page):
        cached_data = get_cached_rank_data(page, normalized_filter)
        if cached_data:
            return cached_data

        try:
            url = f'https://fconline.nexon.com/datacenter/rank_inner?rt=manager&n4pageno={page}'
            res = session.get(url, timeout=3)
            soup = BeautifulSoup(res.text, 'html.parser')
            trs = soup.select('.tbody .tr')
            page_results = []
            rank = (page - 1) * 20 + 1
            
            for tr in trs:
                try:
                    name_tag = tr.select_one('.rank_coach .name')
                    team_tag = tr.select_one('.td.team_color .name .inner')
                    if not team_tag:
                        team_tag = tr.select_one('.td.team_color .name')
                    formation_tag = tr.select_one('.td.formation')
                    value_tag = tr.select_one('span.price')
                    score_tag = tr.select_one('.td.rank_r_win_point')
                    
                    if not all([name_tag, team_tag]):
                        rank += 1
                        continue

                    nickname = name_tag.text.strip()
                    team_color = re.sub(r'\(.*?\)', '', team_tag.text.strip()).strip()
                    formation = formation_tag.text.strip() if formation_tag else "-"
                    value = 0
                    if value_tag:
                        raw = value_tag.get("alt") or value_tag.get("title") or "0"
                        try:
                            value = int(raw.replace(",", "")) // 100_000_000
                        except:
                            pass
                    score = 0
                    if score_tag:
                        try:
                            score = float(score_tag.text.strip())
                        except:
                            pass
                    if normalized_filter == "all" or normalized_filter in team_color.replace(" ", "").lower():
                        page_results.append((nickname, rank, team_color, formation, value, score))
                    rank += 1
                except Exception as e:
                    print(f"크롤링 파싱 오류: {e}")
                    rank += 1
                    continue

            if page_results:
                set_cached_rank_data(start_page, normalized_filter, page_results)
                return page_results

        except Exception as e:
            print(f"⚠️ 페이지 {page} 처리 오류: {e}")
            return []

    # 병렬 처리를 위한 페이지 그룹화
    CHUNK_SIZE = 100  # 청크 크기 증가
    chunks = [(i, min(i + CHUNK_SIZE - 1, end_page)) for i in range(start_page, end_page + 1, CHUNK_SIZE)]
    
    futures = []
    for chunk_start, chunk_end in chunks:
        chunk_pages = range(chunk_start, chunk_end + 1)
        chunk_futures = [executor.submit(fetch_page, page) for page in chunk_pages]
        futures.extend(chunk_futures)
        
    for future in as_completed(futures):
        try:
            result = future.result()
            if result:
                all_results.extend(result)
        except Exception as e:
            print(f"Future 처리 중 오류 발생: {e}")
    
    return all_results

def get_cached_match_data(nickname):
    """캐시된 매치 데이터 조회"""
    return match_cache.get(nickname)

def fetch_user_data(nickname, max_retries=5):
    for attempt in range(max_retries):
        try:
            cached_data = get_cached_match_data(nickname)
            if cached_data:
                return cached_data
            session = get_session()
            ouid_res = session.get(f"https://open.api.nexon.com/fconline/v1/id?nickname={nickname}", timeout=5)
            if ouid_res.status_code != 200:
                return []
            ouid = ouid_res.json().get("ouid")
            if not ouid:
                return []
            target = datetime.now().date()
            offset = 0
            limit = 100
            found_older_date = False
            player_cards = []
            # 최근 5경기만 확인하도록 수정
                match_res = session.get(
                f"https://open.api.nexon.com/fconline/v1/user/match?matchtype=52&ouid={ouid}&offset=0&limit=5&orderby=desc",
                    timeout=5
                )
                if match_res.status_code != 200:
                return []
                match_ids = match_res.json()
                if not match_ids:
                return []
                for match_id in match_ids:
                    detail_res = session.get(f"https://open.api.nexon.com/fconline/v1/match-detail?matchid={match_id}", timeout=5)
                    if detail_res.status_code != 200:
                        continue
                    match_data = detail_res.json()
                    for info in match_data["matchInfo"]:
                        if info["ouid"] == ouid:
                            match_time_str = info.get("matchDate") or match_data.get("matchDate")
                            if not match_time_str:
                                continue
                            match_time = datetime.strptime(match_time_str[:19], "%Y-%m-%dT%H:%M:%S") + timedelta(hours=9)
                            match_time = match_time.date()
                            if match_time > target:
                                continue
                            elif match_time < target:
                                found_older_date = True
                                break
                        # 선수 카드 정보 파싱
                            for player in info.get("player", []):
                                pos_num = player.get("spPosition")
                                player_cards.append({
                                    "nickname": nickname,
                                    "name": player.get("name"),
                                    "season": player.get("season"),
                                    "grade": player.get("grade"),
                                    "position": pos_num
                                })
                if found_older_date or player_cards:
                        break
            if not player_cards:
                player_cards.append({
                    "nickname": nickname,
                    "name": None,
                    "season": None,
                    "grade": None,
                    "position": None
                })
            if player_cards:
                set_cached_match_data(nickname, player_cards)
            return player_cards
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(0.2)  # 재시도 전 대기 시간 감소
                continue
            print(f"[ERROR] {nickname} 데이터 수집 실패: {e}")
            return []

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/healthz')
def health_check():
    return "OK", 200

@app.route('/pickrate')
def pickrate():
    return render_template('pickrate.html')

@app.route('/teamcolor')
def teamcolor():
    return render_template('teamcolor.html')

@app.route('/efficiency')
def efficiency():
    return render_template('efficiency.html')

def safe_api_call(url, method="GET", params=None, data=None, timeout=5):
    try:
        session = create_session()
        if method == "GET":
            response = session.get(url, params=params, timeout=timeout)
        else:
            response = session.post(url, json=data, timeout=timeout)
        
        if response.status_code == 200:
            return response.json()
        elif response.status_code == 429:
            time.sleep(0.5)  # Rate limit 대기 시간 감소
            return safe_api_call(url, method, params, data, timeout)
        else:
            raise Exception(f"API 호출 실패: {response.status_code}")
    except requests.exceptions.RequestException as e:
        raise Exception(f"API 요청 중 오류 발생: {str(e)}")
    except ValueError as e:
        raise Exception(f"API 응답 파싱 중 오류 발생: {str(e)}")

def init_db():
    conn = sqlite3.connect('players.db')
    c = conn.cursor()
    # 두 개의 테이블 생성
    c.execute('''
        CREATE TABLE IF NOT EXISTS player_table_1 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            level INTEGER,
            team_color TEXT,
            rank INTEGER,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS player_table_2 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            level INTEGER,
            team_color TEXT,
            rank INTEGER,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS active_table (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            table_name TEXT NOT NULL,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS status (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            progress INTEGER DEFAULT 0,
            is_running INTEGER DEFAULT 0,
            target_hour TEXT,
            data_hour TEXT,
            last_update TEXT,
            row_count INTEGER DEFAULT 0
        )
    ''')
    conn.commit()
    conn.close()

def get_active_table():
    """현재 활성화된 테이블 번호를 반환"""
    try:
        with open('active_table.txt', 'r') as f:
            return int(f.read().strip())
    except:
        return 1

def set_active_table(table_num):
    """활성화된 테이블 번호를 설정"""
    with open('active_table.txt', 'w') as f:
        f.write(str(table_num))

def save_players_to_db(players):
    """선수 데이터를 DB에 저장 (이중 테이블 구조 활용)"""
    conn = sqlite3.connect('players.db')
    try:
    c = conn.cursor()
        # 현재 활성 테이블 확인
        active_table = c.execute('SELECT table_name FROM active_table WHERE id = 1').fetchone()
        if not active_table:
            # active_table이 없으면 초기화
            c.execute('INSERT INTO active_table (id, table_name) VALUES (1, "player_table_1")')
            active_table = ("player_table_1",)
    conn.commit()
        # 다음 테이블 결정
        next_table = "player_table_2" if active_table[0] == "player_table_1" else "player_table_1"
        # 트랜잭션 시작
        c.execute('BEGIN TRANSACTION')
        try:
            # 다음 테이블에 데이터 저장
            c.execute(f'DELETE FROM {next_table}')
            c.executemany(f'''
                INSERT INTO {next_table} (name, level, team_color, rank, last_updated)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ''', [(p['name'], p['level'], p['team_color'], p['rank']) for p in players])
            # 활성 테이블 업데이트
            c.execute('UPDATE active_table SET table_name = ? WHERE id = 1', (next_table,))
            # 트랜잭션 커밋
            conn.commit()
            print(f'[DB 저장 완료] {len(players)}개 선수 데이터를 {next_table}에 저장')
            except Exception as e:
            conn.rollback()
            print(f'[ERROR] DB 저장 중 오류 발생: {str(e)}')
            raise
    finally:
        conn.close()

def get_player_data():
    """현재 활성화된 테이블에서 데이터 조회"""
        conn = sqlite3.connect('players.db')
        c = conn.cursor()
    current_table = get_active_table()
    c.execute(f'SELECT * FROM player_table_{current_table}')
    rows = c.fetchall()
        conn.close()
    return rows

def save_status(progress, is_running, target_hour, data_hour, last_update, row_count=0):
    """상태를 DB와 파일에 동시에 저장 (구조 개선)"""
    try:
    conn = sqlite3.connect('players.db')
    c = conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS status (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                progress INTEGER DEFAULT 0,
                is_running INTEGER DEFAULT 0,
                target_hour TEXT,
                data_hour TEXT,
                last_update TEXT,
                row_count INTEGER DEFAULT 0
            )
        ''')
        c.execute('DELETE FROM status WHERE id = 1')
        c.execute('''
            INSERT INTO status (id, progress, is_running, target_hour, data_hour, last_update, row_count)
            VALUES (1, ?, ?, ?, ?, ?, ?)
        ''', (progress, int(is_running), target_hour, data_hour, last_update, row_count))
        conn.commit()
        status = {
            'progress': progress,
            'is_running': bool(is_running),
            'target_hour': target_hour,
            'data_hour': data_hour,
            'last_update': last_update,
            'row_count': row_count
        }
        with open('status.json', 'w') as f:
            json.dump(status, f)
    except Exception as e:
        print(f"[ERROR] 상태 저장 중 오류 발생: {str(e)}")
        raise
    finally:
    conn.close()

def load_status():
    try:
        conn = sqlite3.connect('players.db')
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS status (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            progress INTEGER DEFAULT 0,
            is_running INTEGER DEFAULT 0,
            target_hour TEXT,
            data_hour TEXT,
            last_update TEXT,
            row_count INTEGER DEFAULT 0
        )''')
        status = c.execute('SELECT * FROM status WHERE id = 1').fetchone()
        conn.close()
        if status:
            return {
                'progress': status['progress'],
                'is_running': bool(status['is_running']),
                'target_hour': status['target_hour'],
                'data_hour': status['data_hour'],
                'last_update': status['last_update'],
                'row_count': status['row_count']
            }
    except Exception as e:
        print(f"[WARN] DB에서 상태 로드 실패: {str(e)}")
    try:
        with open('status.json', 'r') as f:
            return json.load(f)
    except:
        return {
            'progress': 0,
            'is_running': False,
            'target_hour': '',
            'data_hour': '',
            'last_update': '',
            'row_count': 0
        }

@app.route('/api/status')
def api_status():
    return jsonify(load_status())

# 로깅 설정
def setup_logging():
    """로깅 설정"""
    log_formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
    log_file = 'app.log'
    
    # 파일 핸들러 (최대 10MB, 5개 백업)
    file_handler = RotatingFileHandler(log_file, maxBytes=10*1024*1024, backupCount=5)
    file_handler.setFormatter(log_formatter)
        
    # 콘솔 핸들러
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(log_formatter)
    
    # 루트 로거 설정
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

def log_error(e, context=""):
    """에러 로깅 헬퍼 함수"""
    error_msg = f"[ERROR] {context}: {str(e)}\n{traceback.format_exc()}"
    logging.error(error_msg)
    print(error_msg)  # 콘솔에도 출력

def log_info(msg):
    """정보 로깅 헬퍼 함수"""
    logging.info(msg)
    print(msg)  # 콘솔에도 출력

def crawl_and_save():
    """데이터 수집 및 저장"""
    try:
        with app.app_context():
            now = datetime.now()
            target_hour = get_recent_hour(now).strftime('%Y-%m-%d %H:00:00')
            # 이전 자료 기준 시각 불러오기
            prev_status = load_status()
            data_hour = prev_status.get('data_hour', target_hour)
            # 집계 시작 상태 저장
            save_status(0, True, target_hour, data_hour, now.strftime('%Y-%m-%d %H:%M:%S'), 0)
            # 데이터 수집 및 저장
            players = parse_rank_pages(1, 100, '')
            if players:
                save_players_to_db(players)
                clear_cache()
                log_info("데이터 집계 완료 및 캐시 초기화")
            # 집계 완료 상태 저장 (자료 기준 시각을 이번 집계 시각으로 업데이트)
            save_status(100, False, target_hour, target_hour, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), len(players) if players else 0)
    except Exception as e:
        log_error(f"데이터 수집 중 오류 발생: {str(e)}")
        save_status(0, False, target_hour, data_hour, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 0)

def process_batch(batch, 기준시각):
    """배치 단위로 선수 데이터 처리"""
    batch_players = []
    session = create_session()
    try:
        for nickname, rank, team_color in batch:
            try:
                player_cards = fetch_user_data(nickname)
                if player_cards:
                    for card in player_cards:
                        if card['name'] and card['season'] and card['grade'] and card['position']:
                            batch_players.append({
                                'name': card['name'],
                                'level': card['grade'],
                                'team_color': team_color,
                                'rank': rank
                            })
                except Exception as e:
                log_error(e, f"선수 {nickname} 처리 중 오류")
            continue
        except Exception as e:
        log_error(e, "배치 처리 중 오류")
        raise
    finally:
        session.close()
    return batch_players

def seconds_until_next_10min():
    try:
        now = datetime.now()
        next_hour = (now + timedelta(hours=1)).replace(minute=10, second=0, microsecond=0)
        if now.minute < 10:
            next_10min = now.replace(minute=10, second=0, microsecond=0)
            if now < next_10min:
                return (next_10min - now).total_seconds()
        return (next_hour - now).total_seconds()
    except Exception as e:
        print(f'[ERROR] 다음 실행 시간 계산 중 오류: {str(e)}')
        return 3600  # 오류 발생 시 1시간 후로 설정

def start_crawling_scheduler():
    def schedule_next():
        now = datetime.now()
        next_time = get_next_schedule_time(now)
        delay = (next_time - now).total_seconds()
        threading.Timer(delay, run_crawl_and_reschedule).start()
        print(f"[스케줄러] 다음 집계 예정: {next_time}")
    def run_crawl_and_reschedule():
        try:
            crawl_and_save()
    except Exception as e:
            print(f"[ERROR] 집계 스케줄러 오류: {str(e)}")
        schedule_next()
    schedule_next()

# 서버 시작 시 상태 초기화 (자료 기준 시각은 이전 집계 시각, 없으면 최근 정각)
def check_and_reset_status():
    try:
    conn = sqlite3.connect('players.db')
    c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS status (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            progress INTEGER DEFAULT 0,
            is_running INTEGER DEFAULT 0,
            target_hour TEXT,
            data_hour TEXT,
            last_update TEXT,
            row_count INTEGER DEFAULT 0
        )''')
        now = datetime.now()
        target_hour = get_recent_hour(now).strftime('%Y-%m-%d %H:00:00')
        data_hour = target_hour
        last_update = now.strftime('%Y-%m-%d %H:%M:%S')
        c.execute('DELETE FROM status WHERE id = 1')
        c.execute('''INSERT INTO status (id, progress, is_running, target_hour, data_hour, last_update, row_count)
            VALUES (1, 0, 0, ?, ?, ?, 0)''', (target_hour, data_hour, last_update))
    conn.commit()
        status = {
            'progress': 0,
            'is_running': False,
            'target_hour': target_hour,
            'data_hour': data_hour,
            'last_update': last_update,
            'row_count': 0
        }
        if os.path.exists('status.json'):
            os.remove('status.json')
        with open('status.json', 'w') as f:
            json.dump(status, f)
        print(f"[{last_update}] 서버 재시작: 상태를 최근 정각 기준으로 강제 초기화함")
    except Exception as e:
        print(f"[ERROR] 상태 초기화 중 오류 발생: {str(e)}")
    finally:
    conn.close()

def set_cached_rank_data(page, normalized_filter, data):
    """랭킹 데이터를 캐시에 저장"""
    cache_key = f"{page}_{normalized_filter}"
    rank_cache[cache_key] = data

def set_cached_match_data(nickname, data):
    """매치 데이터를 캐시에 저장"""
    match_cache[nickname] = data

def get_recent_hour(now=None):
    """가장 최근 정각 반환"""
    if not now:
        now = datetime.now()
    return now.replace(minute=0, second=0, microsecond=0)

def get_next_schedule_time(now=None):
    """다음 집계 스케줄(정각+10분) 반환"""
    if not now:
        now = datetime.now()
    next_hour = now.replace(minute=10, second=0, microsecond=0)
    if now.minute >= 10:
        next_hour = (now + timedelta(hours=1)).replace(minute=10, second=0, microsecond=0)
    return next_hour

@app.before_first_request
def start_cache_scheduler_once():
    schedule_cache_clear()

if __name__ == '__main__':
    setup_logging()  # 로깅 설정
    init_db()
    check_and_reset_status()  # 서버 시작 시 상태 확인
    # 크롤링 스케줄러를 별도 스레드에서 실행
    threading.Thread(target=start_crawling_scheduler, daemon=True).start()
    app.run(host='0.0.0.0', port=10000, debug=True) 