document.addEventListener('DOMContentLoaded', function() {
    // 팀 컬러 목록 (예시)
    const teamColors = [
        "맨체스터 시티",
        "맨체스터 유나이티드",
        "리버풀",
        "첼시",
        "아스널",
        "토트넘",
        "레알 마드리드",
        "바르셀로나",
        "아틀레티코 마드리드",
        "바이에른 뮌헨",
        "도르트문트",
        "파리 생제르맹",
        "유벤투스",
        "AC 밀란",
        "인터 밀란"
    ];

    const teamColorSelect = document.getElementById('teamColor');
    const teamColorSearch = document.getElementById('teamColorSearch');

    // 초기 팀 컬러 목록 생성
    function initializeTeamColors() {
        teamColors.forEach(color => {
            const option = document.createElement('option');
            option.value = color;
            option.textContent = color;
            teamColorSelect.appendChild(option);
        });
    }

    // 팀 컬러 필터링
    function filterTeamColors(searchText) {
        const filteredColors = teamColors.filter(color => 
            color.toLowerCase().includes(searchText.toLowerCase())
        );

        // 기존 옵션 제거 (첫 번째 '전체' 옵션 제외)
        while (teamColorSelect.options.length > 1) {
            teamColorSelect.remove(1);
        }

        // 필터링된 옵션 추가
        filteredColors.forEach(color => {
            const option = document.createElement('option');
            option.value = color;
            option.textContent = color;
            teamColorSelect.appendChild(option);
        });
    }

    // 이벤트 리스너 등록
    teamColorSearch.addEventListener('input', (e) => {
        filterTeamColors(e.target.value);
    });

    // 폼 제출 처리
    document.getElementById('pickRateForm').addEventListener('submit', function(e) {
        e.preventDefault();
        
        const formData = {
            rankRange: document.getElementById('rankRange').value,
            teamColor: document.getElementById('teamColor').value,
            topN: document.getElementById('topN').value
        };

        // TODO: API 호출 및 결과 처리
        console.log('Form submitted:', formData);
    });

    // 초기화
    initializeTeamColors();
}); 