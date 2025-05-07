# FC Online 4 데이터 분석 서비스

FIFA Online 4 게임의 데이터를 수집하고 분석하는 웹 서비스입니다.

## 주요 기능

- 랭킹 데이터 수집 및 분석
- 선수 픽률 분석
- 팀 컬러 분석
- 선수 효율성 분석
- 관리자 페이지

## 기술 스택

- Python 3.11
- Flask
- SQLite
- BeautifulSoup4
- Pandas
- XlsxWriter

## 설치 방법

1. 저장소 클론
```bash
git clone [repository-url]
cd fc-support
```

2. 가상환경 생성 및 활성화
```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
venv\Scripts\activate     # Windows
```

3. 의존성 설치
```bash
pip install -r requirements.txt
```

4. 환경 변수 설정
```bash
# Windows
set FC_API_KEY=your_api_key

# Linux/Mac
export FC_API_KEY=your_api_key
```

5. 서버 실행
```bash
python app.py
```

## API 키 발급

FIFA Online 4 API 키는 [Nexon Open API](https://openapi.nexon.com/)에서 발급받을 수 있습니다.

## 라이선스

MIT License 