/* R3 社区：点赞 / 收藏 的即时切换。
 *
 * 设计原则（适老化）
 * - 点一下就有反馈，不刷新页面、不弹窗打断；
 * - 按钮 56px 高，状态用颜色 + 图标双重表达（不只靠颜色，色弱也看得懂）；
 * - 请求失败时给一句大白话，而不是沉默。
 */
(function () {
    'use strict';

    var toastTimer = null;

    function toast(msg, ok) {
        if (!msg) return;
        var el = document.getElementById('community-toast');
        if (!el) {
            el = document.createElement('div');
            el.id = 'community-toast';
            el.className = 'community-toast';
            el.setAttribute('role', 'status');
            document.body.appendChild(el);
        }
        el.textContent = msg;
        el.className = 'community-toast show' + (ok ? '' : ' is-error');
        if (toastTimer) clearTimeout(toastTimer);
        toastTimer = setTimeout(function () { el.className = 'community-toast'; }, 2200);
    }

    function apply(btn, data) {
        var on = !!data.active;
        btn.classList.toggle('is-on', on);
        btn.setAttribute('aria-pressed', on ? 'true' : 'false');
        var count = btn.querySelector('.action-count');
        if (count && typeof data.like_count === 'number') {
            count.textContent = data.like_count;
        }
    }

    document.addEventListener('click', function (ev) {
        var btn = ev.target.closest ? ev.target.closest('.btn-action') : null;
        if (!btn) return;
        ev.preventDefault();
        if (btn.disabled) return;

        var id = btn.getAttribute('data-post-id');
        var kind = btn.getAttribute('data-kind');
        if (!id || !kind) return;

        btn.disabled = true;
        fetch('/api/community/post/' + id + '/' + kind, {
            method: 'POST',
            headers: { 'X-Requested-With': 'fetch' },
            credentials: 'same-origin'
        })
            .then(function (res) {
                return res.json().catch(function () { return {}; })
                    .then(function (data) { return { ok: res.ok, data: data }; });
            })
            .then(function (r) {
                btn.disabled = false;
                if (!r.ok) {
                    toast(r.data.message || '没能完成，请稍后再试', false);
                    return;
                }
                apply(btn, r.data);
                toast(r.data.message || '', true);
            })
            .catch(function () {
                btn.disabled = false;
                toast('网络不太好，请稍后再试', false);
            });
    });
})();
