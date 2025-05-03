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
from threading import Timer, Lock
from collections import OrderedDict
import asyncio
import aiohttp
from typing import List, Tuple, Dict, Any
import concurrent.futures
from dotenv import load_dotenv

# 환경변수 로드
load_dotenv()

app = Flask(__name__)
API_KEY = os.environ.get('FC_API_KEY')

if not API_KEY:
    raise ValueError("FC_API_KEY 환경변수가 설정되지 않았습니다. Render.com의 환경변수 설정에서 FC_API_KEY를 추가해주세요.")

# 캐시 설정
rank_cache = {}
match_cache = {}
cache_timer = None
cache_lock = Lock()

def get_next_hour():
    """다음 정각까지 남은 시간(초)을 반환"""
    now = datetime.now()
    next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return next_hour

def clear_cache():
    """캐시 초기화"""
    with cache_lock:
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

# 세션 풀 설정
session_pool = None
MAX_CONNECTIONS = 200

def create_session_pool():
    conn = aiohttp.TCPConnector(limit=MAX_CONNECTIONS, force_close=True)
    timeout = aiohttp.ClientTimeout(total=10)
    return aiohttp.ClientSession(
        connector=conn,
        timeout=timeout,
        headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
    )

def get_session_pool():
    global session_pool
    if session_pool is None:
        session_pool = create_session_pool()
    return session_pool

async def fetch_page(session, page: int, normalized_filter: str) -> List[Tuple]:
    cache_key = f"{page}_{normalized_filter}"
    
    with cache_lock:
        if cache_key in rank_cache:
            if datetime.now() < get_next_hour():
                return rank_cache[cache_key]
            del rank_cache[cache_key]

    try:
        url = f'https://fconline.nexon.com/datacenter/rank_inner?rt=manager&n4pageno={page}'
        async with session.get(url) as response:
            if response.status != 200:
                return []
            
            html = await response.text()
            soup = BeautifulSoup(html, 'html.parser')
            results = []
            rank = (page - 1) * 20 + 1

            for tr in soup.select('.tbody .tr'):
                try:
                    name = tr.select_one('.rank_coach .name').text.strip()
                    team = tr.select_one('.td.team_color .name .inner, .td.team_color .name').text.strip()
                    team = re.sub(r'\(.*?\)', '', team).replace(" ", "").lower()
                    
                    if normalized_filter != "all" and normalized_filter not in team:
                        rank += 1
                        continue

                    formation = tr.select_one('.td.formation').text.strip()
                    value_tag = tr.select_one('span.price')
                    value = 0
                    if value_tag:
                        raw = value_tag.get("alt") or value_tag.get("title") or "0"
                        value = int(raw.replace(",", "")) // 100_000_000

                    score = float(tr.select_one('.td.rank_r_win_point').text.strip())
                    results.append((name, rank, formation, value, score))
                    rank += 1
                except:
                    rank += 1
                    continue

            with cache_lock:
                rank_cache[cache_key] = results
            return results
    except:
        return []

async def fetch_all_pages(pages: int, normalized_filter: str) -> List[Tuple]:
    async with get_session_pool() as session:
        tasks = [fetch_page(session, page, normalized_filter) for page in range(1, pages + 1)]
        results = await asyncio.gather(*tasks)
        return [item for sublist in results for item in sublist]

def fetch_match_data(nickname: str) -> List[Dict]:
    cache_key = nickname
    
    with cache_lock:
        if cache_key in match_cache:
            if datetime.now() < get_next_hour():
                return match_cache[cache_key]
            del match_cache[cache_key]

    try:
        with requests.Session() as session:
            session.headers.update({'x-nxopen-api-key': API_KEY})
            
            ouid_res = session.get(f"https://open.api.nexon.com/fconline/v1/id?nickname={nickname}", timeout=5)
            if ouid_res.status_code != 200:
                return []
            ouid = ouid_res.json().get("ouid")

            match_res = session.get(f"https://open.api.nexon.com/fconline/v1/user/match?matchtype=52&ouid={ouid}&offset=0&limit=1", timeout=5)
            if match_res.status_code != 200 or not match_res.json():
                return []
            match_id = match_res.json()[0]

            detail_res = session.get(f"https://open.api.nexon.com/fconline/v1/match-detail?matchid={match_id}", timeout=5)
            if detail_res.status_code != 200:
                return []
            
            results = []
            for info in detail_res.json()["matchInfo"]:
                if info["ouid"] == ouid:
                    for p in info.get("player", []):
                        if p.get("spPosition") == 28:
                            continue
                        results.append({
                            "nickname": nickname,
                            "position": position_map.get(p["spPosition"], f"pos{p['spPosition']}"),
                            "name": spid_map.get(p["spId"], f"(Unknown:{p['spId']})"),
                            "season": season_map.get(int(str(p["spId"])[:3]), str(p["spId"])[:3]),
                            "grade": p["spGrade"]
                        })
            
            with cache_lock:
                match_cache[cache_key] = results
            return results
    except:
        return []

# 메타데이터 캐싱
session = requests.Session()
spid_data = None
season_data = None
position_data = None
spid_map = {}
season_map = {}
position_map = {}
position_groups = OrderedDict([
    ("ST", ["LS", "ST", "RS"]),
    ("CF", ["LF", "CF", "RF"]),
    ("LW", ["LW"]),
    ("RW", ["RW"]),
    ("CAM", ["CAM"]),
    ("LAM", ["LAM"]),
    ("RAM", ["RAM"]),
    ("LM", ["LM"]),
    ("RM", ["RM"]),
    ("CM", ["LCM", "CM", "RCM"]),
    ("CDM", ["LDM", "CDM", "RDM"]),
    ("LB", ["LWB", "LB"]),
    ("CB", ["LCB", "SW", "CB", "RCB"]),
    ("RB", ["RWB", "RB"]),
    ("GK", ["GK"])
])

@lru_cache(maxsize=1)
def load_metadata():
    global spid_data, season_data, position_data, spid_map, season_map, position_map
    
    def load_meta(url):
        res = session.get(url, timeout=5)
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

@app.route('/api/search', methods=['POST'])
def search():
    if not API_KEY:
        return jsonify({"error": "API 키가 설정되지 않았습니다."}), 500

    try:
        data = request.json
        rank_limit = int(data.get('rankRange', 1000))
        team_color = data.get('teamColor', 'all').replace(" ", "").lower()
        top_n = int(data.get('topN', 5))

        if not spid_data:
            load_metadata()

        # 비동기로 모든 페이지 데이터 수집
        pages = (rank_limit - 1) // 20 + 1
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        ranked_users = loop.run_until_complete(fetch_all_pages(pages, team_color))
        loop.close()

        # 랭킹 범위로 필터링
        ranked_users = [u for u in ranked_users if u[1] <= rank_limit][:rank_limit]
        
        if not ranked_users:
            return jsonify({"error": "조건에 맞는 사용자가 없습니다."}), 404

        # 매치 데이터 수집 (상위 100명만)
        unique_users = len(ranked_users)
        sample_size = min(100, unique_users)
        sampled_users = [u[0] for u in ranked_users[:sample_size]]
        
        with ThreadPoolExecutor(max_workers=50) as executor:
            player_records = []
            futures = {executor.submit(fetch_match_data, nickname): nickname for nickname in sampled_users}
            for future in as_completed(futures):
                try:
                    result = future.result()
                    if result:
                        player_records.extend(result)
                except:
                    continue

        df = pd.DataFrame(player_records)
        if df.empty:
            return jsonify({"error": "매치 데이터를 찾을 수 없습니다."}), 404

        # 기본 정보
        top_user = min(ranked_users, key=lambda x: x[1])
        
        # 포메이션 정보
        formations = {}
        for formation, users in pd.DataFrame(ranked_users).groupby(2)[0].agg(list).items():
            percent = round(len(users) / unique_users * 100)
            preview = ", ".join(users[:3])
            if len(users) > 3:
                preview += f" 외 {len(users) - 3}명"
            formations[formation] = {
                "percent": percent,
                "count": len(users),
                "users": preview
            }

        # 점수 정보
        ranks = [r[1] for r in ranked_users]
        scores = [r[4] for r in ranked_users]
        score_info = {
            "type": "table",
            "rows": [
                {"label": "평균", "value": f"{round(sum(ranks) / len(ranks))}등 ({round(sum(scores) / len(scores))}점)"},
                {"label": "최고", "value": f"{min(ranks)}등 ({round(max(scores))}점)"},
                {"label": "최저", "value": f"{max(ranks)}등 ({round(min(scores))}점)"}
            ]
        }

        # 구단 가치 정보
        values = [r[3] for r in ranked_users]
        value_info = {
            "type": "table",
            "rows": [
                {"label": "평균", "value": format_korean_currency(round(sum(values) / len(values)))},
                {"label": "최고", "value": format_korean_currency(max(values))},
                {"label": "최저", "value": format_korean_currency(min(values))}
            ]
        }

        # 포지션별 선수 픽률
        positions = OrderedDict()
        if rank_limit == 1:
            df = df[df["nickname"] == top_user[0]]

        for group_name, pos_list in position_groups.items():
            group_df = df[df["position"].isin(pos_list)]
            if group_df.empty:
                continue

            top_players = (
                group_df.groupby(["name", "season", "grade"])
                .agg(count=("nickname", "count"), users=("nickname", list))
                .reset_index()
                .sort_values(by="count", ascending=False)
                .head(top_n)
            )

            positions[group_name] = []
            for _, row in top_players.iterrows():
                percentage = round(row["count"] / unique_users * 100)
                user_list = row["users"]
                display_users = ", ".join(user_list[:3])
                if len(user_list) > 3:
                    display_users += f" 외 {len(user_list) - 3}명"

                positions[group_name].append({
                    "rank": len(positions[group_name]) + 1,
                    "name": row["name"],
                    "season": row["season"],
                    "grade": row["grade"],
                    "percent": percentage,
                    "count": row["count"],
                    "users": display_users
                })

        return jsonify({
            "teamColor": team_color,
            "uniqueUsers": unique_users,
            "topUser": top_user[0],
            "topRank": top_user[1],
            "formations": formations,
            "valueInfo": value_info,
            "scoreInfo": score_info,
            "positions": positions
        })

    except Exception as e:
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

# 앱 시작시 캐시 초기화 스케줄러 시작
with app.app_context():
    clear_cache()
    schedule_cache_clear()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000, debug=True) 