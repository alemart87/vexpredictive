// Analytics tracking script - injected on all authenticated pages
(function() {
    // Generate or retrieve session ID
    if (!sessionStorage.getItem('sid')) {
        sessionStorage.setItem('sid', 'sid_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9));
    }
    const sid = sessionStorage.getItem('sid');

    // Track page view
    fetch('/api/track/pageview', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            page_path: window.location.pathname,
            referrer: document.referrer,
            session_id: sid
        })
    }).catch(function() {});

    // Track clicks on interactive elements
    document.addEventListener('click', function(e) {
        var target = e.target.closest('a, button, .card');
        if (!target) return;

        var elementType = target.tagName.toLowerCase();
        if (target.classList.contains('card')) elementType = 'card';

        fetch('/api/track/click', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                element_type: elementType,
                element_text: (target.textContent || '').trim().substring(0, 200),
                page_path: window.location.pathname
            })
        }).catch(function() {});
    });
})();
