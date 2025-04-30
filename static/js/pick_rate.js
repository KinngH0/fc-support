document.addEventListener('DOMContentLoaded', function() {
    // 팀 컬러 목록 (예시)
    const teamColors = [
        "FC 우니온 베를린",
        "FC 쾰른",
        "FSV 마인츠 05",
        "19 New Generation",
        "19 UEFA Champions League",
        "20 K LEAGUE BEST"
    ];

    const teamColorList = document.getElementById('teamColorList');
    const teamColorInput = document.getElementById('teamColor');

    // 초기 팀 컬러 목록 생성
    function initializeTeamColors() {
        teamColors.forEach(color => {
            const option = document.createElement('option');
            option.value = color;
            teamColorList.appendChild(option);
        });
    }

    // 폼 제출 처리
    document.getElementById('pickRateForm').addEventListener('submit', function(e) {
        e.preventDefault();
        
        const formData = {
            rankRange: document.getElementById('rankRange').value,
            teamColor: teamColorInput.value,
            topN: document.getElementById('topN').value
        };

        // TODO: API 호출 및 결과 처리
        console.log('Form submitted:', formData);
    });

    // 초기화
    initializeTeamColors();
}); 