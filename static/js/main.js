document.getElementById('searchForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    
    const characterName = document.getElementById('characterName').value;
    const resultDiv = document.getElementById('result');
    
    try {
        const response = await fetch('/api/search', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ character_name: characterName })
        });
        
        const data = await response.json();
        resultDiv.innerHTML = `<pre>${JSON.stringify(data, null, 2)}</pre>`;
    } catch (error) {
        resultDiv.innerHTML = `<p style="color: red;">에러가 발생했습니다: ${error.message}</p>`;
    }
}); 