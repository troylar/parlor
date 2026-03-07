(function(){
    // Theme stored in localStorage — non-sensitive user preference only
    var valid = ['midnight','dawn','aurora','ember'];
    var t = localStorage.getItem('anteroom_theme') || localStorage.getItem('parlor_theme');
    if (!t || valid.indexOf(t) === -1) {
        t = (window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches) ? 'dawn' : 'midnight';
    }
    document.documentElement.setAttribute('data-theme', t);
})();
