(function () {
    'use strict';
    var btn = document.getElementById('ttsBtn');
    var audio = document.getElementById('ttsAudio');
    if (!btn || !audio) return;
    var stop = document.getElementById('ttsStop');
    var rate = document.getElementById('ttsRate');
    var status = document.getElementById('ttsStatus');
    var progress = document.getElementById('progressContainer');
    var fill = document.getElementById('ttsFill');
    var time = document.getElementById('ttsTime');
    var loading = false, generation = 0, timer;
    var controller = null, objectUrl = null;
    var nativeUrl = btn.dataset.audioUrl;

    function message(text, error) {
        status.textContent = text;
        status.className = 'tts-status tts-' + (error ? 'error' : 'playing');
        status.style.display = text ? 'block' : 'none';
    }
    function paint(playing) {
        btn.disabled = false;
        btn.textContent = playing ? '⏸ 暂停' : (objectUrl ? '▶ 播放语音' : '🔊 朗读食谱');
        btn.className = 'btn ' + (playing ? 'btn-primary' : 'btn-outline');
    }
    function clearLoading() {
        loading = false;
        window.clearTimeout(timer);
    }
    function reset() {
        generation++;
        clearLoading();
        if (controller) controller.abort();
        controller = null;
        audio.pause();
        audio.removeAttribute('src');
        audio.load();
        // Keep the server MP3 available to native controls, including after cancel/error.
        audio.src = nativeUrl;
        if (objectUrl) window.URL.revokeObjectURL(objectUrl);
        objectUrl = null;
        paint(false);
        stop.hidden = true;
        progress.style.display = 'none';
    }
    function failure(text) {
        reset();
        message(text, true);
    }
    function applyRate() {
        var value = parseFloat(rate.value);
        audio.playbackRate = isFinite(value) && value > 0 ? value : 1;
    }
    function prepare() {
        var current = ++generation;
        loading = true;
        controller = window.AbortController ? new window.AbortController() : null;
        stop.hidden = false;
        btn.textContent = '⏹ 取消准备';
        message('正在准备语音，完成后请点「播放语音」…');
        timer = window.setTimeout(function () {
            if (current === generation && loading) failure('语音准备超时，请点「朗读食谱」重试。');
        }, 60000);
        // 完整下载后使用本地 Blob，绕开 iOS 媒体加载器经隧道发起的多次 Range 请求。
        window.fetch(btn.dataset.audioUrl, {
            credentials: 'same-origin',
            signal: controller ? controller.signal : undefined
        }).then(function (response) {
            if (!response.ok) {
                throw new Error(response.status === 401 ? '请重新登录后播放语音。' : '语音服务暂时不可用，请稍后重试。');
            }
            if ((response.headers.get('Content-Type') || '').indexOf('audio/') !== 0) {
                throw new Error('没有取得有效音频，请刷新页面后重试。');
            }
            return response.blob();
        }).then(function (blob) {
            if (current !== generation) return;
            if (blob.size <= 1024) throw new Error('语音文件不完整，请重试。');
            objectUrl = window.URL.createObjectURL(blob);
            audio.src = objectUrl;
            audio.load();
            clearLoading();
            controller = null;
            paint(false);
            message('语音已就绪，请点「播放语音」或下方 ▶ 开始收听。');
        }).catch(function (err) {
            if (current !== generation) return;
            failure(err && err.name !== 'TypeError' && err.message
                ? err.message : '语音下载失败，请检查网络后重试。');
        });
    }
    btn.addEventListener('click', function (event) {
        // Without this script the link still opens Safari's own MP3 player.
        event.preventDefault();
        if (loading || !audio.paused) {
            if (loading) reset();
            else audio.pause();
            message('已暂停，点「朗读食谱」继续');
            return;
        }
        if (!objectUrl) {
            prepare();
            return;
        }
        var current = ++generation;
        if (audio.ended) audio.currentTime = 0;
        audio.muted = false;
        audio.volume = 1;
        applyRate();
        loading = true;
        stop.hidden = false;
        btn.textContent = '⏹ 取消等待';
        message('正在启动播放…');
        timer = window.setTimeout(function () {
            if (current === generation && loading) failure('语音加载超时，请点「朗读食谱」重试。');
        }, 10000);
        try {
            // 音频已经完整取得；这次真实点击里同步 play，不等待网络或异步回调。
            var result = audio.play();
            if (result && result.catch) result.catch(function (err) {
                if (current !== generation) return;
                if (err && err.name === 'NotAllowedError') {
                    clearLoading();
                    paint(false);
                    message('请点击下方播放器的 ▶ 播放按钮开始收听。', true);
                } else {
                    failure('音频播放失败，请重试；若持续失败，请联系管理员检查语音服务。');
                }
            });
        } catch (err) {
            failure('此浏览器无法启动播放器，请点击下方播放器重试。');
        }
    });
    audio.addEventListener('playing', function () {
        if (!audio.getAttribute('src')) return;
        if (!objectUrl && controller) {
            // Native playback can start while the custom button is preparing audio.
            // Do not let that pending download replace and stop the playing source.
            generation++;
            controller.abort();
            controller = null;
        }
        clearLoading();
        paint(true);
        stop.hidden = false;
        progress.style.display = 'flex';
        message('正在播放食谱语音');
    });
    audio.addEventListener('pause', function () {
        if (!audio.getAttribute('src') || loading || audio.ended) return;
        paint(false);
        message('已暂停，点「朗读食谱」继续');
    });
    audio.addEventListener('ended', function () {
        clearLoading();
        paint(false);
        stop.hidden = true;
        fill.style.width = '100%';
        message('朗读完成');
    });
    audio.addEventListener('error', function () {
        if (!audio.getAttribute('src')) return;
        if (!objectUrl && !loading) {
            message('页面播放未能启动，请点「打开语音单独播放」重试。', true);
            return;
        }
        failure('语音加载失败，请重试；若已退出登录，请重新登录后播放。');
    });
    audio.addEventListener('timeupdate', function () {
        var duration = audio.duration;
        if (!isFinite(duration) || duration <= 0) return;
        progress.style.display = 'flex';
        fill.style.width = Math.min(100, audio.currentTime / duration * 100).toFixed(1) + '%';
        time.textContent = Math.floor(audio.currentTime) + ' / ' + Math.ceil(duration) + ' 秒';
    });
    rate.addEventListener('change', applyRate);
    stop.addEventListener('click', function () { reset(); message('已停止'); });
    window.addEventListener('pagehide', function () { reset(); message(''); });
})();
