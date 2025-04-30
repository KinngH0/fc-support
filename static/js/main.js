document.addEventListener('DOMContentLoaded', function() {
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