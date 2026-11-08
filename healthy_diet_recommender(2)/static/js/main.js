/**
 * 益食安康 — 前端交互脚本
 * 全局初始化 + Flash 消息自动消失 + 通用工具函数
 */

document.addEventListener('DOMContentLoaded', function () {

    // ---- Flash 消息 5 秒后自动消失 ----
    var flashMessages = document.querySelectorAll('.flash-message');
    flashMessages.forEach(function (msg) {
        setTimeout(function () {
            msg.style.opacity = '0';
            msg.style.transform = 'translateY(-8px)';
            msg.style.transition = 'opacity 0.3s, transform 0.3s';
            setTimeout(function () { msg.remove(); }, 300);
        }, 5000);
    });

    // ---- 拖拽上传初始化 ----
    var dropZones = document.querySelectorAll('.upload-zone');
    dropZones.forEach(function (zone) {
        // 点击触发文件选择
        zone.addEventListener('click', function (e) {
            if (e.target.tagName === 'BUTTON') return;
            var input = zone.querySelector('input[type="file"]');
            if (input) input.click();
        });

        // 拖拽高亮
        zone.addEventListener('dragover', function (e) {
            e.preventDefault();
            zone.classList.add('upload-zone-dragover');
        });
        zone.addEventListener('dragleave', function () {
            zone.classList.remove('upload-zone-dragover');
        });
        zone.addEventListener('drop', function (e) {
            e.preventDefault();
            zone.classList.remove('upload-zone-dragover');
            var input = zone.querySelector('input[type="file"]');
            if (input && e.dataTransfer.files.length > 0) {
                input.files = e.dataTransfer.files;
                input.dispatchEvent(new Event('change'));
            }
        });
    });

    // ---- 文件选择后显示文件名 ----
    document.querySelectorAll('input[type="file"]').forEach(function (input) {
        input.addEventListener('change', function () {
            var zone = this.closest('.upload-zone');
            if (!zone) return;
            var nameEl = zone.querySelector('.upload-filename');
            if (!nameEl) return;
            if (this.files && this.files[0]) {
                nameEl.textContent = '已选择: ' + this.files[0].name;
                nameEl.style.display = 'block';
            } else {
                nameEl.style.display = 'none';
            }
        });
    });

    // ---- 导航下拉菜单 ----
    document.querySelectorAll('.nav-dropdown').forEach(function (dd) {
        var toggle = dd.querySelector('.nav-dd-toggle');
        toggle.addEventListener('click', function (e) {
            e.preventDefault();
            var isOpen = dd.classList.contains('open');
            // 关闭其它已打开的下拉
            document.querySelectorAll('.nav-dropdown.open').forEach(function (o) {
                if (o !== dd) o.classList.remove('open');
            });
            dd.classList.toggle('open', !isOpen);
        });
        // 点击菜单项后自动收起
        dd.querySelectorAll('.nav-dd-menu a').forEach(function (a) {
            a.addEventListener('click', function () {
                dd.classList.remove('open');
            });
        });
    });
    // 点击页面其它区域关闭下拉
    document.addEventListener('click', function (e) {
        if (!e.target.closest('.nav-dropdown')) {
            document.querySelectorAll('.nav-dropdown.open').forEach(function (o) {
                o.classList.remove('open');
            });
        }
    });

    // ---- 3D 倾斜光影卡片（按压跟随 + Spring 阻尼回正） ----
    // 2026-10-12 视觉对齐：动效与外观拆开
    //   .tilt-card —— 只管「按下去跟着指针倾斜、松手弹簧回正」，所有卡片都参与；
    //   .glass     —— 只管毛玻璃外观，**只打给没有语义底色的卡片**。
    // 原因：原来 .tilt-card 自带 `background: rgba(255,255,255,.55) !important`，
    // 把 .metric-card.is-warn / .is-critical（偏高黄 / 危险红）、.risk-alert-item 的
    // 底色全部盖成同一个半透明白 —— 判档颜色是这套界面最要紧的信息，不能被装饰吃掉。
    var TILT_SELECTOR = [
        '.stat-card', '.info-card', '.recipe-card-mini', '.recipe-list-item',
        '.meal-card', '.meal-plan-form-card', '.upload-card', '.generate-card',
        '.auth-card', '.recipe-section',
        '.nutri-card', '.nutri-chart-card',
        '.metric-card', '.chart-card',
        '.meal-advice', '.risk-alert-item'
    ].join(',');

    // 语义底色的卡片不在这里：.metric-card / .nutri-card / .risk-alert-item。
    // .meal-day / .nutri-day 是分组容器不是「面」，也不加玻璃（会与里面的卡片叠成两层）。
    var GLASS_SELECTOR = [
        '.stat-card', '.info-card', '.recipe-card-mini', '.recipe-list-item',
        '.meal-card', '.meal-plan-form-card', '.upload-card', '.generate-card',
        '.auth-card', '.recipe-section',
        '.nutri-chart-card', '.chart-card', '.meal-advice'
    ].join(',');

    // 统一打上 .tilt-card 标记，CSS 只认 .tilt-card / .glass
    document.querySelectorAll(TILT_SELECTOR).forEach(function (el) {
        if (el.closest('.dashboard-redesign')) return;
        el.classList.add('tilt-card');
    });
    document.querySelectorAll(GLASS_SELECTOR).forEach(function (el) {
        if (el.closest('.dashboard-redesign')) return;
        el.classList.add('glass');
    });

    function initTiltCard(card) {
        var MAX_TILT = 16;          // 最大倾斜角度（度）
        var SPRING_K = 120;         // 弹簧刚度
        var SPRING_C = 14;          // 阻尼系数（欠阻尼 → 轻微回弹）
        var ON_OFFSET = 0.5;        // 归一化中心坐标

        var tracking = false;       // 当前是否按住跟踪
        var activePointer = null;   // 正在跟踪的 pointerId
        var x = 0, y = 0;           // 归一化偏移（-0.5 ~ 0.5，中心为 0）
        var vx = 0, vy = 0;         // 弹簧速度（单位/秒）
        var lastTick = 0;           // 上一物理帧时间戳
        var raf = null;

        function apply() {
            var px = 0.5 + x;
            var py = 0.5 + y;
            var rx = -y * MAX_TILT;
            var ry = x * MAX_TILT;
            card.style.transform =
                'perspective(900px) rotateX(' + rx.toFixed(2) + 'deg) rotateY(' + ry.toFixed(2) + 'deg) scale(1.02)';
            card.style.setProperty('--mx', (px * 100).toFixed(1) + '%');
            card.style.setProperty('--my', (py * 100).toFixed(1) + '%');
        }

        function springTick(now) {
            var dt = Math.min((now - lastTick) / 1000, 0.05);
            lastTick = now;
            // 弹簧动力学：a = -k*x - c*v
            x += vx * dt;
            vx += (-SPRING_K * x - SPRING_C * vx) * dt;
            y += vy * dt;
            vy += (-SPRING_K * y - SPRING_C * vy) * dt;

            if (Math.abs(x) < 0.003 && Math.abs(y) < 0.003 &&
                Math.abs(vx) < 0.02 && Math.abs(vy) < 0.02) {
                // 安定：清空，回到 CSS 默认态
                x = 0; y = 0; vx = 0; vy = 0;
                card.style.transform = '';
                card.style.removeProperty('--mx');
                card.style.removeProperty('--my');
                card.classList.remove('tilt-active');
                card.style.transition = '';
                raf = null;
                return;
            }
            apply();
            raf = requestAnimationFrame(springTick);
        }
        function startSpring() {
            if (raf) cancelAnimationFrame(raf);
            lastTick = performance.now();
            card.classList.add('tilt-active');
            card.style.transition = 'none'; // 由物理帧驱动，禁用 CSS 过渡避免打架
            raf = requestAnimationFrame(springTick);
        }

        function setFromEvent(e) {
            var rect = card.getBoundingClientRect();
            var px = (e.clientX - rect.left) / rect.width;
            var py = (e.clientY - rect.top) / rect.height;
            px = Math.max(0, Math.min(1, px)); // 越界钳制
            py = Math.max(0, Math.min(1, py));
            x = px - 0.5;
            y = py - 0.5;
            if (raf) { cancelAnimationFrame(raf); raf = null; }
            card.classList.add('tilt-active');
            card.style.transition = 'none';
            apply();
        }

        function stopTracking() {
            tracking = false;
            activePointer = null;
            startSpring(); // 松手：弹簧回正
        }

        card.addEventListener('pointerdown', function (e) {
            // 忽略非主按键
            if (e.button !== undefined && e.button !== 0 && e.pointerType === 'mouse') return;
            // 点到链接/按钮/输入框时不启动 tilt，让原生点击正常触发
            if (e.target.closest('a, button, input, textarea, select, label')) return;
            tracking = true;
            activePointer = e.pointerId;
            setFromEvent(e);
        });
        card.addEventListener('pointermove', function (e) {
            if (!tracking || e.pointerId !== activePointer) return;
            setFromEvent(e);
        });
        card.addEventListener('pointerup', function (e) {
            if (!tracking || e.pointerId !== activePointer) return;
            stopTracking();
        });
        card.addEventListener('pointercancel', function (e) {
            if (!tracking || e.pointerId !== activePointer) return;
            stopTracking();
        });
        card.addEventListener('pointerleave', function (e) {
            if (!tracking || e.pointerId !== activePointer) return;
            stopTracking();
        });
    }
    if ('PointerEvent' in window) {
        document.querySelectorAll('.tilt-card').forEach(initTiltCard);
    }

});
