/* 食品科普 · 联网等待界面
 *
 * 点「联网找找看」是整页 POST，浏览器会一直显示旧页面直到新页面到达。
 * 所以必须在提交之前就换成等待提示，否则用户只能对着一张不动的旧页干等
 * （老人最容易在这时候以为坏了，直接关掉）。
 *
 * 无 JS 时这段不执行，表单照常提交，退化成原来的空白等待 —— 不影响可用性。
 */
(function () {
    'use strict';

    var form = document.getElementById('fg-net-form');
    var ask = document.getElementById('fg-net-ask');
    var wait = document.getElementById('fg-net-wait');
    var slow = document.getElementById('fg-net-slow');
    if (!form || !ask || !wait) { return; }

    form.addEventListener('submit', function () {
        ask.hidden = true;
        wait.hidden = false;
        /* 焦点移进等待区：屏幕阅读器会念出 role="status" 里的内容，
           键盘用户也不会把焦点留在一个已经藏起来的按钮上。 */
        wait.focus();
        /* 12 秒还没回来就补一句，免得用户以为卡死。只在本次提交内有效。 */
        window.setTimeout(function () {
            if (slow) { slow.hidden = false; }
        }, 12000);
    });

    /* 按浏览器返回键可能从往返缓存恢复，DOM 会带着「等待中」的样子回来。 */
    window.addEventListener('pageshow', function (event) {
        if (!event.persisted) { return; }
        ask.hidden = false;
        wait.hidden = true;
        if (slow) { slow.hidden = true; }
    });
})();
