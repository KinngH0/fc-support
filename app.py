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
from collections import OrderedDict

app = Flask(__name__)
API_KEY = os.getenv('FC_API_KEY')

# 캐시 설정
rank_cache = {}
match_cache = {}
cache_timer = None

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
    retries = Retry(total=5, backoff_factor=0.1, status_forcelist=[429, 500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retries, pool_connections=100, pool_maxsize=100)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

def get_session():
    global session
    if not session:
        session = create_session()
    return session

# 메타데이터 캐싱
session = create_session()
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
    
    # 병렬 처리를 위한 함수
    def fetch_page(page):
        try:
            # 캐시 키에 팀컬러 필터 포함
            cache_key = f"{page}_{normalized_filter}"
            cached_data = get_cached_rank_data(page, normalized_filter)
            if cached_data:
                return cached_data

            url = f'https://fconline.nexon.com/datacenter/rank_inner?rt=manager&n4pageno={page}'
            res = session.get(url, timeout=5)
            if res.status_code != 200:
                return []

            soup = BeautifulSoup(res.text, 'html.parser')
            trs = soup.select('.tbody .tr')
            page_results = []
            rank = (page - 1) * 20 + 1
            
            for tr in trs:
                try:
                    name_tag = tr.select_one('.rank_coach .name')
                    team_tag = tr.select_one('.td.team_color .name .inner') or tr.select_one('.td.team_color .name')
                    formation_tag = tr.select_one('.td.formation')
                    value_tag = tr.select_one('span.price')
                    score_tag = tr.select_one('.td.rank_r_win_point')
                    
                    if not all([name_tag, team_tag]):
                        rank += 1
                        continue

                    nickname = name_tag.text.strip()
                    team_color = re.sub(r'\(.*?\)', '', team_tag.text.strip()).replace(" ", "").lower()
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
                            
                    if normalized_filter == "all" or normalized_filter in team_color:
                        page_results.append((nickname, rank, formation, value, score))
                    rank += 1
                except:
                    rank += 1
                    continue

            if page_results:
                rank_cache[cache_key] = page_results
            return page_results

        except:
            return []

    # 병렬 처리 최적화 - 큰 배치로 처리
    batch_size = 50  # 배치 크기 증가
    for batch_start in range(start_page, end_page + 1, batch_size):
        batch_end = min(batch_start + batch_size, end_page + 1)
        with ThreadPoolExecutor(max_workers=50) as executor:
            futures = [executor.submit(fetch_page, page) for page in range(batch_start, batch_end)]
            for future in as_completed(futures):
                try:
                    results = future.result()
                    all_results.extend(results)
                except:
                    continue

    return all_results

def get_cached_match_data(nickname):
    """캐시된 매치 데이터 조회"""
    if nickname in match_cache:
        data = match_cache[nickname]
        if datetime.now() < get_next_hour():
            return data
        del match_cache[nickname]
    return None

def fetch_user_data(nickname):
    # 캐시 키에 팀컬러 필터 포함
    cache_key = f"{nickname}_{datetime.now().hour}"
    cached_data = get_cached_match_data(cache_key)
    if cached_data:
        return cached_data

    try:
        headers = {"x-nxopen-api-key": API_KEY}
        ouid_res = session.get(f"https://open.api.nexon.com/fconline/v1/id?nickname={nickname}", headers=headers, timeout=5)
        if ouid_res.status_code != 200:
            return []
        ouid = ouid_res.json().get("ouid")
        if not ouid:
            return []

        match_res = session.get(f"https://open.api.nexon.com/fconline/v1/user/match?matchtype=52&ouid={ouid}&offset=0&limit=1", headers=headers, timeout=5)
        if match_res.status_code != 200 or not match_res.json():
            return []
        match_id = match_res.json()[0]

        detail_res = session.get(f"https://open.api.nexon.com/fconline/v1/match-detail?matchid={match_id}", headers=headers, timeout=5)
        if detail_res.status_code != 200:
            return []
        match_data = detail_res.json()

        results = []
        for info in match_data.get("matchInfo", []):
            if info.get("ouid") == ouid:
                for p in info.get("player", []):
                    if p.get("spPosition") == 28:  # 감독
                        continue
                    sp_id = p.get("spId")
                    grade = p.get("spGrade")
                    position = position_map.get(p.get("spPosition"), f"pos{p.get('spPosition')}")
                    season_id = int(str(sp_id)[:3]) if sp_id else 0
                    name = spid_map.get(sp_id, f"(Unknown:{sp_id})")
                    season_name = season_map.get(season_id, f"{season_id}")
                    results.append({
                        "nickname": nickname,
                        "position": position,
                        "name": name,
                        "season": season_name,
                        "grade": grade,
                    })

        # 결과 캐시 저장
        if results:
            match_cache[cache_key] = results
        return results
    except:
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

@app.route('/api/search', methods=['POST'])
def search():
    if not API_KEY:
        return jsonify({"error": "API 키가 설정되지 않았습니다."}), 500

    try:
        data = request.json
        if not data:
            return jsonify({"error": "요청 데이터가 없습니다."}), 400

        rank_limit = int(data.get('rankRange', 1000))
        team_color = data.get('teamColor', 'all')
        top_n = int(data.get('topN', 5))

        if not spid_data:
            load_metadata()

        normalized_filter = team_color.replace(" ", "").lower()
        
        # 랭커 수집 - 한 번에 전체 페이지 처리
        pages = (rank_limit - 1) // 20 + 1
        ranked_users = parse_rank_pages(1, pages, normalized_filter)
        
        # 랭킹 범위로 필터링
        ranked_users = [u for u in ranked_users if u[1] <= rank_limit][:rank_limit]

        if not ranked_users:
            return jsonify({"error": "조건에 맞는 사용자가 없습니다."}), 404

        # 팀컬러 필터링
        filtered_users = [u for u in ranked_users if normalized_filter == "all" or normalized_filter in u[2]]
        unique_users = len(filtered_users)

        # 유저별 경기 데이터 수집 - 병렬 처리 최적화
        player_records = []
        batch_size = 50  # 배치 크기 증가
        
        for i in range(0, len(filtered_users), batch_size):
            batch = filtered_users[i:i + batch_size]
            with ThreadPoolExecutor(max_workers=50) as executor:
                futures = [executor.submit(fetch_user_data, u[0]) for u in batch]
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
        top_user, top_rank, _, _, _ = min(filtered_users, key=lambda x: x[1])

        # 포메이션 정보
        formation_dict = {}
        teams_value = []
        for nickname, rank, formation, value, _ in filtered_users:
            formation_dict.setdefault(formation, []).append(nickname)
            teams_value.append(value)

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

        # 점수 정보 계산
        ranks = [r[1] for r in filtered_users]
        scores = [r[4] for r in filtered_users]
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

        # 구단 가치 정보
        if teams_value:
            avg_val = round(sum(teams_value) / len(teams_value))
            min_val = min(teams_value)
            max_val = max(teams_value)
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
        # 랭킹 범위가 1이면 해당 유저의 데이터만 사용
        if rank_limit == 1:
            df = df[df["nickname"] == top_user]

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
                .reset_index(drop=True)
            )

            positions[group_name] = []
            for i, row_data in top_players.iterrows():
                percentage = round(row_data["count"] / unique_users * 100)
                user_list = row_data["users"]
                display_users = ", ".join(user_list[:3])
                if len(user_list) > 3:
                    display_users += f" 외 {len(user_list) - 3}명"

                positions[group_name].append({
                    "rank": i + 1,
                    "name": row_data["name"],
                    "season": row_data["season"],
                    "grade": row_data["grade"],
                    "percent": percentage,
                    "count": row_data["count"],
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

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000, debug=True) 