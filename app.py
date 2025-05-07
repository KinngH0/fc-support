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

app = Flask(__name__)
API_KEY = os.getenv('FC_API_KEY')

# 로깅 설정
if not os.path.exists('logs'):
    os.makedirs('logs')

# 로그 파일 핸들러 설정
file_handler = RotatingFileHandler('logs/app.log', maxBytes=1024*1024, backupCount=5, encoding='utf-8')
file_handler.setFormatter(logging.Formatter(
    '%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
))
app.logger.addHandler(file_handler)
app.logger.setLevel(logging.INFO)

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
    print(f"캐시가 초기화되었습니다. - {datetime.now()}")

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
    if cache_key in rank_cache:
        data = rank_cache[cache_key]
        if datetime.now() < get_next_hour():
            return data
        del rank_cache[cache_key]
    return None

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
                rank_cache[f"{page}_{normalized_filter}"] = page_results
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
    if nickname in match_cache:
        data = match_cache[nickname]
        if datetime.now() < get_next_hour():
            return data
        del match_cache[nickname]
    return None

def fetch_user_data(nickname, max_retries=5):
    for attempt in range(max_retries):
        try:
            cached_data = get_cached_match_data(nickname)
            if cached_data:
                return cached_data
            session = get_session()
            ouid_res = session.get(f"https://open.api.nexon.com/fconline/v1/id?nickname={nickname}", timeout=3)
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
            while True:
                match_res = session.get(
                    f"https://open.api.nexon.com/fconline/v1/user/match?matchtype=52&ouid={ouid}&offset={offset}&limit={limit}&orderby=desc",
                    timeout=5
                )
                if match_res.status_code != 200:
                    break
                match_ids = match_res.json()
                if not match_ids:
                    break
                for match_id in match_ids:
                    detail_res = session.get(f"https://open.api.nexon.com/fconline/v1/match-detail?matchid={match_id}", timeout=5)
                    if detail_res.status_code != 200:
                        continue
                    match_data = detail_res.json()
                    found_player = False
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
                            # 선수 카드 정보 파싱 (포지션을 숫자 코드로 저장)
                            for player in info.get("player", []):
                                found_player = True
                                pos_num = player.get("spPosition")
                                player_cards.append({
                                    "nickname": nickname,
                                    "name": player.get("name"),
                                    "season": player.get("season"),
                                    "grade": player.get("grade"),
                                    "position": pos_num
                                })
                    if found_older_date:
                        break
                if found_older_date or len(match_ids) < limit:
                    break
                offset += limit
                time.sleep(0.01)  # API rate limit 방지용 딜레이 (속도 개선)
            if not player_cards:
                player_cards.append({
                    "nickname": nickname,
                    "name": None,
                    "season": None,
                    "grade": None,
                    "position": None
                })
            if player_cards:
                match_cache[nickname] = player_cards
            return player_cards
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(0.5)  # 재시도 전 대기
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
    c.execute('''
        CREATE TABLE IF NOT EXISTS player_table (
            nickname TEXT,
            rank INTEGER,
            team_color TEXT,
            formation TEXT,
            value INTEGER,
            score REAL,
            name TEXT,
            season TEXT,
            grade INTEGER,
            position INTEGER,
            timestamp TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# 1시간마다 전체 크롤링 & DB 갱신

def save_players_to_db(players):
    conn = sqlite3.connect('players.db')
    c = conn.cursor()
    c.execute('DELETE FROM player_table')
    c.executemany('''
        INSERT INTO player_table (nickname, rank, team_color, formation, value, score, name, season, grade, position, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', players)
    conn.commit()
    conn.close()

def save_status(progress, timestamp, row_count=None):
    status = {'progress': progress, 'timestamp': timestamp}
    if row_count is not None:
        status['row_count'] = row_count
    with open('status.json', 'w') as f:
        json.dump(status, f)

def load_status():
    try:
        with open('status.json', 'r') as f:
            return json.load(f)
    except:
        return {'progress': 0, 'timestamp': ''}

@app.route('/api/status')
def api_status():
    return jsonify(load_status())

def crawl_and_save():
    print('[DB 갱신 시작]')
    if not spid_data:
        load_metadata()
    rank_limit = 10000
    normalized_filter = 'all'
    pages = (rank_limit - 1) // 20 + 1
    ranked_users = parse_rank_pages(1, pages, normalized_filter)
    ranked_users = [u for u in ranked_users if u[1] <= rank_limit][:rank_limit]
    all_players = []
    sample_size = len(ranked_users)
    batch_size = 20  # 더 작은 배치로 실시간성 향상
    from datetime import datetime
    now = datetime.now()
    기준시각 = now.replace(minute=0, second=0, microsecond=0).strftime('%Y-%m-%d %H:%M:%S')
    total_batches = (sample_size + batch_size - 1) // batch_size
    processed_batches = 0
    save_status(0, 기준시각, 0)
    for i in range(0, sample_size, batch_size):
        batch = ranked_users[i:i+batch_size]
        batch_futures = [executor.submit(fetch_user_data, u[0]) for u in batch]
        for idx, future in enumerate(as_completed(batch_futures)):
            try:
                player_cards = future.result()
                if player_cards:
                    for card in player_cards:
                        user_info = next((u for u in batch if u[0] == card['nickname']), None)
                        if user_info:
                            all_players.append((
                                card['nickname'],
                                user_info[1],
                                user_info[2],
                                user_info[3],
                                user_info[4],
                                user_info[5],
                                card['name'],
                                card['season'],
                                card['grade'],
                                card['position'],
                                기준시각
                            ))
            except Exception as e:
                print(f'[DB 갱신 중 예외] {e}')
                continue
        processed_batches += 1
        progress = int(processed_batches / total_batches * 100)
        # 집계 중에도 DB row 수를 status에 기록
        conn = sqlite3.connect('players.db')
        c = conn.cursor()
        c.execute('SELECT COUNT(*) FROM player_table')
        row_count = c.fetchone()[0]
        conn.close()
        save_status(progress, 기준시각, row_count)
    save_players_to_db(all_players)
    print(f'[DB 갱신 완료] 총 {len(all_players)}개 선수 데이터 저장')
    conn = sqlite3.connect('players.db')
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM player_table')
    row_count = c.fetchone()[0]
    conn.close()
    save_status(100, 기준시각, row_count)

def seconds_until_next_10min():
    now = datetime.now()
    next_hour = (now + timedelta(hours=1)).replace(minute=10, second=0, microsecond=0)
    if now.minute < 10:
        next_10min = now.replace(minute=10, second=0, microsecond=0)
        if now < next_10min:
            return (next_10min - now).total_seconds()
    return (next_hour - now).total_seconds()

def start_crawling_scheduler():
    crawl_and_save()
    threading.Timer(seconds_until_next_10min(), crawl_and_save_scheduler).start()

def crawl_and_save_scheduler():
    crawl_and_save()
    threading.Timer(3600, crawl_and_save_scheduler).start()

# /api/search 등 조회 API는 DB에서만 읽도록 변경
@app.route('/api/search', methods=['POST'])
def search():
    try:
        data = request.json
        if not data:
            return jsonify({"error": "요청 데이터가 없습니다."}), 400
        rank_limit = int(data.get('rankRange', 1000))
        team_color = data.get('teamColor', 'all')
        top_n = int(data.get('topN', 5))
        conn = sqlite3.connect('players.db')
        c = conn.cursor()
        # 팀컬러 필터
        if team_color == 'all':
            c.execute('SELECT * FROM player_table WHERE rank <= ?', (rank_limit,))
        else:
            c.execute('SELECT * FROM player_table WHERE rank <= ? AND team_color = ?', (rank_limit, team_color))
        rows = c.fetchall()
        conn.close()
        if not rows:
            return jsonify({"error": "조건에 맞는 데이터가 없습니다."}), 404
        # DataFrame 변환
        df = pd.DataFrame(rows, columns=["nickname", "rank", "team_color", "formation", "value", "score", "name", "season", "grade", "position", "timestamp"])
        unique_users = df['nickname'].nunique()
        # 기본 정보
        top_row = df.sort_values('rank').iloc[0]
        top_user = top_row['nickname']
        top_rank = top_row['rank']
        team_color_val = top_row['team_color']
        formation_dict = df.groupby('formation')['nickname'].apply(list).to_dict()
        formations = {}
        for formation, users in sorted(formation_dict.items(), key=lambda item: len(item[1]), reverse=True):
            percent = round(len(users) / unique_users * 100)
            preview = ", ".join(users[:3])
            if len(users) > 3:
                preview += f" 외 {len(users) - 3}명"
            formations[formation] = {
                "percent": percent,
                "count": len(users),
                "users": preview
            }
        # 점수/구단가치 정보
        ranks = df['rank'].tolist()
        scores = df['score'].tolist()
        values = df['value'].tolist()
        avg_rank = round(sum(ranks) / len(ranks))
        avg_score = round(sum(scores) / len(scores))
        min_rank = min(ranks)
        max_rank = max(ranks)
        min_score = round(min(scores))
        max_score = round(max(scores))
        score_info = {
            "type": "table",
            "rows": [
                {"label": "평균", "value": f"{avg_rank}등 ({avg_score}점)"},
                {"label": "최고", "value": f"{min_rank}등 ({max_score}점)"},
                {"label": "최저", "value": f"{max_rank}등 ({min_score}점)"}
            ]
        }
        if values:
            avg_val = round(sum(values) / len(values))
            min_val = min(values)
            max_val = max(values)
            value_info = {
                "type": "table",
                "rows": [
                    {"label": "평균", "value": format_korean_currency(avg_val)},
                    {"label": "최고", "value": format_korean_currency(max_val)},
                    {"label": "최저", "value": format_korean_currency(min_val)}
                ]
            }
        else:
            value_info = {
                "type": "table",
                "rows": [
                    {"label": "평균", "value": "0억"},
                    {"label": "최고", "value": "0억"},
                    {"label": "최저", "value": "0억"}
                ]
            }
        # 포지션별 선수 픽률
        positions = OrderedDict()
        for group_name, pos_list in position_groups.items():
            group_df = df[df["position"].isin(pos_list)]
            if group_df.empty:
                continue
            group_df = group_df.drop_duplicates(subset=["nickname", "name", "season", "grade", "position"])
            position_total_cards = len(group_df)
            top_players = (
                group_df.groupby(["name", "season", "grade"])
                .agg(
                    users=("nickname", lambda x: list(set(x))),
                    card_count=("nickname", "count")
                )
                .reset_index()
            )
            top_players = top_players.sort_values("card_count", ascending=False)
            top_players = top_players.head(top_n).reset_index(drop=True)
            positions[group_name] = {
                "total_users": position_total_cards,
                "players": []
            }
            for i, row_data in top_players.iterrows():
                user_list = row_data["users"]
                card_count = row_data["card_count"]
                user_count = len(user_list)
                percentage = round(card_count / position_total_cards * 100) if position_total_cards else 0
                display_users = ", ".join(user_list[:3])
                if len(user_list) > 3:
                    display_users += f" 외 {len(user_list) - 3}명"
                positions[group_name]["players"].append({
                    "rank": i + 1,
                    "name": row_data["name"],
                    "season": row_data["season"],
                    "grade": row_data["grade"],
                    "percent": f"{percentage}% ({user_count}명)",
                    "count": user_count,
                    "users": display_users
                })
        return jsonify({
            "teamColor": team_color,
            "uniqueUsers": unique_users,
            "topUser": top_user,
            "topRank": top_rank,
            "formations": formations,
            "valueInfo": value_info,
            "scoreInfo": score_info,
            "positions": positions
        })
    except Exception as e:
        print(f"Error in search API: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/excel', methods=['POST'])
def generate_excel():
    try:
        # 엑셀 파일 생성 로직...
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_name = f"fc_online_report_{timestamp}.xlsx"
        temp_dir = tempfile.gettempdir()
        file_path = os.path.join(temp_dir, file_name)

        workbook = xlsxwriter.Workbook(file_path)
        worksheet = workbook.add_worksheet("리포트")
        
        # 여기에 엑셀 파일 생성 로직 추가...
        
        workbook.close()

        return jsonify({
            "message": "엑셀 파일이 생성되었습니다.",
            "file_name": file_name,
            "file_path": file_path
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/download/<path:filename>')
def download(filename):
    temp_dir = tempfile.gettempdir()
    return send_file(os.path.join(temp_dir, filename), as_attachment=True)

def crawl_rankings(rank_limit):
    base_url = "https://fconline.nexon.com/datacenter/rank_inner"
    params = {
        "rt": "manager",
        "n4pageno": 1
    }
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Cache-Control": "max-age=0",
        "Referer": "https://fconline.nexon.com/datacenter/rank"
    }
    
    rankings = []
    current_page = 1
    total_rankings = 0
    
    while total_rankings < rank_limit:
        params["n4pageno"] = current_page
        try:
            print(f"페이지 {current_page} 크롤링 시도 중...")
            response = requests.get(base_url, params=params, headers=headers, timeout=10)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            trs = soup.select('.tbody .tr')
            
            if not trs:
                print(f"페이지 {current_page}에서 데이터를 찾을 수 없습니다.")
                break
                
            for tr in trs:
                if total_rankings >= rank_limit:
                    break
                    
                try:
                    name_tag = tr.select_one('.rank_coach .name')
                    team_tag = tr.select_one('.td.team_color .name .inner') or tr.select_one('.td.team_color .name')
                    
                    if not all([name_tag, team_tag]):
                        print("이름이나 팀컬러 태그를 찾을 수 없습니다.")
                        continue

                    nickname = name_tag.text.strip()
                    team_color = re.sub(r'\(.*?\)', '', team_tag.text.strip()).replace(" ", "").lower()
                    
                    print(f"수집된 데이터: {nickname} - {team_color}")
                    
                    rankings.append({
                        'rank': total_rankings + 1,
                        'nickname': nickname,
                        'team_color': team_color
                    })
                    total_rankings += 1
                except Exception as e:
                    print(f"선수 데이터 파싱 중 오류: {str(e)}")
                    continue
            
            current_page += 1
            # time.sleep(2)  # 속도 개선을 위해 딜레이 제거
            
        except requests.exceptions.RequestException as e:
            print(f"페이지 {current_page} 요청 중 오류: {str(e)}")
            # time.sleep(5)
            continue
        except Exception as e:
            print(f"페이지 {current_page} 처리 중 오류: {str(e)}")
            break
    
    print(f"총 {len(rankings)}개의 데이터 수집 완료")
    return rankings

@app.route('/api/teamcolor', methods=['POST'])
def get_teamcolor_data():
    try:
        data = request.json
        if not data:
            return jsonify({"error": "요청 데이터가 없습니다."}), 400

        rank_limit = int(data.get('rankRange', 100))
        top_n = int(data.get('topN', 3))

        # 실제 랭킹 데이터 크롤링
        pages = (rank_limit - 1) // 20 + 1
        ranked_users = parse_rank_pages(1, pages, "all")
        ranked_users = [u for u in ranked_users if u[1] <= rank_limit][:rank_limit]

        if not ranked_users:
            return jsonify({"error": "조건에 맞는 사용자가 없습니다."}), 404

        # 팀컬러별 집계
        teamcolor_counter = Counter([u[2] for u in ranked_users if u[2].strip()])
        total_users = sum(teamcolor_counter.values())
        teamcolors = []
        for name, count in teamcolor_counter.most_common(top_n):
            # 해당 팀컬러의 등수/점수/구단주명 리스트 추출
            ranks = [u[1] for u in ranked_users if u[2] == name]
            scores = [u[5] for u in ranked_users if u[2] == name]
            users = [u[0] for u in ranked_users if u[2] == name]
            avg_rank = round(sum(ranks) / len(ranks)) if ranks else None
            min_rank = min(ranks) if ranks else None
            max_rank = max(ranks) if ranks else None
            avg_score = round(sum(scores) / len(scores)) if scores else None
            # 최고 등수(가장 낮은 숫자), 그에 해당하는 점수/구단주명
            if ranks:
                min_idx = ranks.index(min_rank)
                min_score = scores[min_idx]
                min_ranker = users[min_idx]
            else:
                min_score = None
                min_ranker = None
            # 최저 등수(가장 높은 숫자), 그에 해당하는 점수/구단주명
            if ranks:
                max_idx = ranks.index(max_rank)
                max_score = scores[max_idx]
                max_ranker = users[max_idx]
            else:
                max_score = None
                max_ranker = None
            percentage = round(count / total_users * 100, 1)
            teamcolors.append({
                'name': name,
                'count': count,
                'percentage': percentage,
                'avg_rank': avg_rank,
                'min_rank': min_rank,
                'max_rank': max_rank,
                'avg_score': avg_score,
                'min_score': min_score,
                'max_score': max_score,
                'min_ranker': min_ranker,
                'max_ranker': max_ranker,
                # 포메이션 정보 추가
                'formations': {
                    formation: {
                        'count': len([u for u in ranked_users if u[2] == name and u[3] == formation]),
                        'percentage': round(len([u for u in ranked_users if u[2] == name and u[3] == formation]) / count * 100, 1)
                    }
                    for formation in set(u[3] for u in ranked_users if u[2] == name)
                },
                # 구단가치 정보 추가
                'values': {
                    'avg': round(sum(u[4] for u in ranked_users if u[2] == name) / count) if count > 0 else 0,
                    'min': min(u[4] for u in ranked_users if u[2] == name) if count > 0 else 0,
                    'max': max(u[4] for u in ranked_users if u[2] == name) if count > 0 else 0
                }
            })

        return jsonify({
            'total_users': total_users,
            'teamcolors': teamcolors
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/top_ranker', methods=['GET'])
def get_top_ranker():
    try:
        # 항상 최신 1등 정보 크롤링
        rank_data = parse_rank_pages(1, 1, "all")
        if not rank_data or len(rank_data) == 0:
            return jsonify({"error": "랭킹 데이터가 없습니다."}), 404
        top_ranker = rank_data[0]
        return jsonify({
            "nickname": top_ranker[0],
            "teamcolor": top_ranker[2]
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/efficiency', methods=['POST'])
def get_efficiency():
    try:
        data = request.get_json()
        nickname = data.get('nickname')
        target_date = data.get('date')
        api_key = os.getenv('FC_API_KEY')

        if not all([nickname, target_date, api_key]):
            return jsonify({'error': '필수 파라미터가 누락되었습니다.'}), 400

        headers = {'x-nxopen-api-key': api_key}

        def create_session():
            session = requests.Session()
            session.headers.update(headers)
            retries = Retry(total=3, backoff_factor=0.3, status_forcelist=[429, 500, 502, 503, 504])
            adapter = HTTPAdapter(max_retries=retries)
            session.mount("https://", adapter)
            session.mount("http://", adapter)
            return session

        session = create_session()

        # 유저 식별자 조회
        res = session.get(f"https://open.api.nexon.com/fconline/v1/id?nickname={nickname}", timeout=5)
        if res.status_code != 200:
            return jsonify({'error': '유저 정보를 가져오지 못했습니다.'}), 400

        ouid = res.json().get("ouid")
        if not ouid:
            return jsonify({'error': '존재하지 않는 닉네임입니다.'}), 404

        # 날짜 파싱
        target = datetime.strptime(target_date, "%Y-%m-%d").date()

        # 변수 초기화
        played = 0
        win = 0
        draw = 0
        loss = 0

        # 페이지 단위로 경기 조회
        offset = 0
        limit = 100
        found_older_date = False
        while True:
            match_res = session.get(
                f"https://open.api.nexon.com/fconline/v1/user/match?matchtype=52&ouid={ouid}&offset={offset}&limit={limit}&orderby=desc",
                timeout=5
            )

            if match_res.status_code != 200:
                return jsonify({'error': '경기 목록 조회 실패'}), 500

            match_ids = match_res.json()
            if not match_ids:
                break

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
                        # target 날짜와 같으면 계속 집계
                        played += 1
                        match_result = info["matchDetail"]["matchResult"]
                        if match_result == "승":
                            win += 1
                        elif match_result == "무":
                            draw += 1
                        elif match_result == "패":
                            loss += 1
                if found_older_date:
                    break
            if found_older_date or len(match_ids) < limit:
                break
            offset += limit

        # 승률, FC 계산
        win_rate = round((win / played) * 100) if played > 0 else 0
        earned_fc = win * 15

        if played == 0:
            return jsonify({'error': '해당 날짜에 경기 데이터가 없습니다.'}), 404

        return jsonify({
            'nickname': nickname,
            'date': target_date,
            'total_games': played,
            'wins': win,
            'draws': draw,
            'losses': loss,
            'win_rate': win_rate,
            'earned_fc': earned_fc
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/admin')
def admin_page():
    return render_template('admin.html')

@app.route('/api/admin/data', methods=['GET'])
def admin_data():
    # 파라미터: page, page_size, sort, order, filters
    import math
    page = int(request.args.get('page', 1))
    page_size = int(request.args.get('page_size', 20))
    sort = request.args.get('sort', 'rank')
    order = request.args.get('order', 'asc')
    nickname = request.args.get('nickname', '').strip()
    team_color = request.args.get('team_color', '').strip()
    rank = request.args.get('rank', '').strip()
    position = request.args.get('position', '').strip()
    conn = sqlite3.connect('players.db')
    c = conn.cursor()
    q = 'SELECT * FROM player_table WHERE 1=1'
    params = []
    if nickname:
        q += ' AND nickname LIKE ?'
        params.append(f'%{nickname}%')
    if team_color:
        q += ' AND team_color LIKE ?'
        params.append(f'%{team_color}%')
    if rank:
        q += ' AND rank <= ?'
        params.append(int(rank))
    if position:
        q += ' AND position = ?'
        params.append(int(position))
    # 전체 개수
    c.execute(f'SELECT COUNT(*) FROM ({q})', params)
    total = c.fetchone()[0]
    # 정렬/페이징
    q += f' ORDER BY {sort} {order.upper()} LIMIT ? OFFSET ?'
    params += [page_size, (page-1)*page_size]
    c.execute(q, params)
    rows = c.fetchall()
    conn.close()
    # 컬럼명
    columns = ["nickname", "rank", "team_color", "formation", "value", "score", "name", "season", "grade", "position", "timestamp"]
    data = [dict(zip(columns, row)) for row in rows]
    return jsonify({"data": data, "total": total, "page": page, "page_size": page_size})

@app.route('/api/admin/delete', methods=['POST'])
def admin_delete():
    rowid = request.json.get('rowid')
    conn = sqlite3.connect('players.db')
    c = conn.cursor()
    c.execute('DELETE FROM player_table WHERE rowid=?', (rowid,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/admin/update', methods=['POST'])
def admin_update():
    data = request.json
    rowid = data.pop('rowid')
    keys = ', '.join([f'{k}=?' for k in data.keys()])
    values = list(data.values()) + [rowid]
    conn = sqlite3.connect('players.db')
    c = conn.cursor()
    c.execute(f'UPDATE player_table SET {keys} WHERE rowid=?', values)
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/admin/add', methods=['POST'])
def admin_add():
    data = request.json
    keys = ', '.join(data.keys())
    q = f'INSERT INTO player_table ({keys}) VALUES ({", ".join(["?"]*len(data))})'
    values = list(data.values())
    conn = sqlite3.connect('players.db')
    c = conn.cursor()
    c.execute(q, values)
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/admin/excel', methods=['GET'])
def admin_excel():
    import pandas as pd
    conn = sqlite3.connect('players.db')
    df = pd.read_sql_query('SELECT * FROM player_table', conn)
    conn.close()
    file_path = 'admin_export.xlsx'
    df.to_excel(file_path, index=False)
    return send_file(file_path, as_attachment=True)

@app.route('/api/admin/csv', methods=['GET'])
def admin_csv():
    import pandas as pd
    conn = sqlite3.connect('players.db')
    df = pd.read_sql_query('SELECT * FROM player_table', conn)
    conn.close()
    file_path = 'admin_export.csv'
    df.to_csv(file_path, index=False)
    return send_file(file_path, as_attachment=True)

@app.route('/api/admin/status')
def admin_status():
    return jsonify(load_status())

@app.route('/api/admin/log')
def admin_log():
    try:
        log_path = 'logs/app.log'
        if not os.path.exists(log_path):
            return ''
        
        with open(log_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()[-200:]  # 최근 200줄만 가져오기
        
        # 로그 레벨에 따른 색상 지정
        colored_lines = []
        for line in lines:
            if '[ERROR]' in line:
                line = f'<span style="color: #ff6b6b">{line}</span>'
            elif '[WARNING]' in line:
                line = f'<span style="color: #ffd93d">{line}</span>'
            elif '[INFO]' in line:
                line = f'<span style="color: #6bff6b">{line}</span>'
            colored_lines.append(line)
        
        return ''.join(colored_lines)
    except Exception as e:
        app.logger.error(f"로그 파일 읽기 오류: {str(e)}")
        return f"로그 파일 읽기 오류: {str(e)}"

if __name__ == '__main__':
    # 로컬 개발 환경에서는 디버그 모드로 실행
    app.run(debug=True, host='0.0.0.0', port=5000) 