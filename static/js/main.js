// 다크모드 관리
function initTheme() {
    // 시스템 다크모드 설정 확인
    const systemDarkMode = window.matchMedia('(prefers-color-scheme: dark)').matches;
    // localStorage에서 테마 설정 가져오기
    const savedTheme = localStorage.getItem('theme');
    
    if (savedTheme === 'dark' || (!savedTheme && systemDarkMode)) {
        document.documentElement.classList.add('dark');
    }
}

function toggleTheme() {
    const isDark = document.documentElement.classList.toggle('dark');
    localStorage.setItem('theme', isDark ? 'dark' : 'light');
}

document.addEventListener('DOMContentLoaded', function() {
    // 다크모드 초기화
    initTheme();
    
    // 다크모드 토글 버튼 이벤트 리스너
    const themeToggle = document.getElementById('theme-toggle');
    if (themeToggle) {
        themeToggle.addEventListener('click', toggleTheme);
    }

    // 검색 폼 제출 처리
    const searchForm = document.querySelector('form');
    if (searchForm) {
        searchForm.addEventListener('submit', function(e) {
            e.preventDefault();
            const searchInput = this.querySelector('input[type="text"]');
            if (searchInput.value.trim()) {
                // TODO: 실제 검색 API 호출 구현
                console.log('검색어:', searchInput.value.trim());
            }
        });
    }

    // 카드 호버 효과
    const cards = document.querySelectorAll('.bg-white.rounded-lg');
    cards.forEach(card => {
        card.classList.add('transition-all', 'hover:scale-105');
    });
}); 