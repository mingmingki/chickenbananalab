
(function () {
    const initialEl = document.getElementById("mini-capcut-initial-state");
    const metaEl = document.getElementById("mini-capcut-editor-meta");

    const initial = initialEl ? JSON.parse(initialEl.textContent || "{}") : {};
    const meta = metaEl ? JSON.parse(metaEl.textContent || "{}") : {};
    const urls = window.MINI_CAPCUT_URLS || {};

    const LABEL_WIDTH = window.innerWidth <= 720 ? 86 : 116;
    const TRACK_HEIGHT = 74;
    const DEFAULT_PX_PER_SEC = 120;
    const MIN_PX_PER_SEC = 35;
    const MAX_PX_PER_SEC = 460;

    function defaultTracks() {
        return [
            { id: "track_main", name: "메인 영상", type: "video", muted: false, locked: false, hidden: false },
            { id: "track_overlay_1", name: "오버레이 1", type: "overlay", muted: false, locked: false, hidden: false },
            { id: "track_overlay_2", name: "오버레이 2", type: "overlay", muted: false, locked: false, hidden: false },
            { id: "track_audio", name: "음성", type: "audio", muted: false, locked: false, hidden: false },
        ];
    }

    const state = {
        assets: Array.isArray(initial.assets) ? initial.assets : [],
        clips: Array.isArray(initial.clips) ? initial.clips : [],
        tracks: Array.isArray(initial.tracks) && initial.tracks.length ? initial.tracks : defaultTracks(),
        selectedClipId: initial.selectedClipId || null,
        copiedClip: null,
        currentTime: Number(initial.currentTime || 0),
        pxPerSec: Number(initial.pxPerSec || DEFAULT_PX_PER_SEC),
        isPlaying: false,
        playStartedAt: 0,
        playBaseTime: 0,
        animationId: null,
    };

    let projectId = meta.projectId || null;
    let playheadDragging = false;
    let previewCache = {
        mediaClipId: null,
        mediaUrl: null,
        lastVideoSeek: -999,
    };

    const $ = (id) => document.getElementById(id);

    const assetInput = $("mcAssetInput");
    const assetList = $("mcAssetList");
    const timeline = $("mcTimeline");

    const previewVideo = $("mcPreviewVideo");
    const previewImage = $("mcPreviewImage");
    const previewText = $("mcPreviewText");
    const previewPlaceholder = $("mcPreviewPlaceholder");

    const props = $("mcProps");
    const noSelection = $("mcNoSelection");

    const propName = $("mcPropName");
    const propStart = $("mcPropStart");
    const propDuration = $("mcPropDuration");
    const propSpeed = $("mcPropSpeed");
    const propVolume = $("mcPropVolume");
    const propTransition = $("mcPropTransition");
    const propText = $("mcPropText");
    const textPropBox = $("mcTextPropBox");

    const contextMenu = $("mcContextMenu");
    const currentTimeEl = $("mcCurrentTime");
    const playBtn = $("mcTimelinePlayBtn");
    const stopBtn = $("mcTimelineStopBtn");
    const previewTimeText = $("mcPreviewTimeText");

    function uid() {
        return "mc_" + Math.random().toString(16).slice(2) + Date.now().toString(16);
    }

    function getCookie(name) {
        const value = `; ${document.cookie}`;
        const parts = value.split(`; ${name}=`);
        if (parts.length === 2) return parts.pop().split(";").shift();
        return "";
    }

    function escapeHtml(value) {
        return String(value || "")
            .replaceAll("&", "&amp;")
            .replaceAll("<", "&lt;")
            .replaceAll(">", "&gt;")
            .replaceAll('"', "&quot;")
            .replaceAll("'", "&#039;");
    }

    function formatTime(sec) {
        sec = Math.max(0, Number(sec || 0));
        const m = Math.floor(sec / 60);
        const s = Math.floor(sec % 60);
        const d = Math.floor((sec - Math.floor(sec)) * 10);
        return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}.${d}`;
    }

    function snapTime(sec) {
        return Math.max(0, Math.round(Number(sec || 0) * 10) / 10);
    }

    function clamp(value, min, max) {
        return Math.max(min, Math.min(max, value));
    }

    function normalizeTrack(track, index) {
        return {
            id: track.id || uid(),
            name: track.name || `트랙 ${index + 1}`,
            type: track.type || "overlay",
            muted: Boolean(track.muted),
            locked: Boolean(track.locked),
            hidden: Boolean(track.hidden),
        };
    }

    function normalizeClip(clip) {
        const firstTrackId = state.tracks[0]?.id || "track_main";

        clip.id = clip.id || uid();
        clip.name = clip.name || "클립";
        clip.type = clip.type || "image";
        clip.trackId = clip.trackId || firstTrackId;
        clip.start = Number(clip.start || 0);
        clip.duration = Math.max(0.3, Number(clip.duration || 5));
        clip.speed = Number(clip.speed || 1);
        clip.volume = Number(clip.volume ?? 1);
        clip.transition = clip.transition || "none";
        clip.text = clip.text || "";
        clip.sourceOffset = Number(clip.sourceOffset || 0);
        clip.muted = Boolean(clip.muted);
        return clip;
    }

    function normalizeState() {
        state.tracks = state.tracks.map(normalizeTrack);
        state.clips = state.clips.map(normalizeClip);

        const validTrackIds = new Set(state.tracks.map(t => t.id));

        state.clips.forEach(clip => {
            if (!validTrackIds.has(clip.trackId)) {
                clip.trackId = state.tracks[0]?.id || "track_main";
            }
        });
    }

    function selectedClip() {
        return state.clips.find(c => c.id === state.selectedClipId) || null;
    }

    function getTrack(trackId) {
        return state.tracks.find(t => t.id === trackId) || null;
    }

    function trackIndex(trackId) {
        const idx = state.tracks.findIndex(t => t.id === trackId);
        return idx === -1 ? 0 : idx;
    }

    function totalDuration() {
        const maxClipEnd = state.clips.reduce((max, clip) => {
            return Math.max(max, Number(clip.start || 0) + Number(clip.duration || 0));
        }, 0);

        return Math.max(10, Math.ceil(maxClipEnd + 1), Math.ceil(state.currentTime + 1));
    }

    function timelineWidth() {
        return Math.ceil(totalDuration() * state.pxPerSec);
    }

    function getTimelineLane() {
        return document.querySelector(".mc-track-lane");
    }

    function timeFromLaneEvent(e, lane) {
        const rect = lane.getBoundingClientRect();
        const x = e.clientX - rect.left;
        return snapTime(x / state.pxPerSec);
    }

    function nextStart(trackId) {
        const clips = state.clips.filter(c => c.trackId === trackId);
        if (!clips.length) return state.currentTime || 0;

        return Math.max(...clips.map(c => Number(c.start || 0) + Number(c.duration || 0)));
    }

    function updateCurrentTimeLabel() {
        const txt = formatTime(state.currentTime);
        const total = formatTime(totalDuration());

        if (currentTimeEl) currentTimeEl.textContent = txt;
        if (previewTimeText) previewTimeText.textContent = `${txt} / ${total}`;
    }

    function updatePlayheadPosition() {
        const playhead = document.querySelector(".mc-playhead");
        if (playhead) {
            playhead.style.left = `${LABEL_WIDTH + state.currentTime * state.pxPerSec}px`;
            playhead.classList.toggle("is-playing", state.isPlaying);
        }

        if (playBtn) {
            playBtn.textContent = state.isPlaying ? "❚❚" : "▶";
            playBtn.classList.toggle("is-playing", state.isPlaying);
        }

        updateCurrentTimeLabel();
    }

    function ensurePlayheadVisible() {
        if (!timeline) return;

        const playheadX = LABEL_WIDTH + state.currentTime * state.pxPerSec;
        const visibleLeft = timeline.scrollLeft;
        const visibleRight = timeline.scrollLeft + timeline.clientWidth;

        if (playheadX > visibleRight - 120) {
            timeline.scrollLeft = playheadX - timeline.clientWidth + 160;
        } else if (playheadX < visibleLeft + 120) {
            timeline.scrollLeft = Math.max(0, playheadX - 160);
        }
    }

    function setCurrentTime(sec, options = {}) {
        const preview = options.preview !== false;
        const forceSeek = options.forceSeek !== false;
        const keepVisible = Boolean(options.keepVisible);
        const fromPlayback = Boolean(options.fromPlayback);

        state.currentTime = clamp(Number(sec || 0), 0, totalDuration());
        updatePlayheadPosition();

        if (preview) {
            previewAtTime(state.currentTime, {
                forceSeek,
                fromPlayback,
            });
        }

        if (keepVisible) {
            ensurePlayheadVisible();
        }
    }

    function moveTimelineShellToWideRow() {
        const editor = document.querySelector(".mc-editor");
        const shell = document.querySelector(".mc-timeline-shell");

        if (editor && shell && shell.parentElement !== editor) {
            editor.appendChild(shell);
            shell.classList.add("mc-wide-timeline");
        }
    }

    function updateSelectionClasses() {
        document.querySelectorAll(".mc-clip").forEach(el => {
            el.classList.toggle("selected", el.dataset.id === state.selectedClipId);
        });
    }

    function renderAssets() {
        if (!assetList) return;

        if (!state.assets.length) {
            assetList.innerHTML = `<div class="mc-empty">업로드된 자료가 없습니다.</div>`;
            return;
        }

        assetList.innerHTML = state.assets.map(asset => `
            <div class="mc-asset-item" data-id="${asset.id}" draggable="true">
                <strong>${escapeHtml(asset.name)}</strong>
                <span>${escapeHtml(asset.type)}</span>
            </div>
        `).join("");

        assetList.querySelectorAll(".mc-asset-item").forEach(el => {
            el.addEventListener("click", () => {
                const asset = state.assets.find(a => a.id === el.dataset.id);
                if (asset) {
                    addAssetToTimeline(asset, state.tracks[0]?.id, nextStart(state.tracks[0]?.id));
                }
            });

            el.addEventListener("dragstart", (e) => {
                el.classList.add("dragging");
                e.dataTransfer.setData("text/plain", `asset:${el.dataset.id}`);
                e.dataTransfer.effectAllowed = "copy";
            });

            el.addEventListener("dragend", () => {
                el.classList.remove("dragging");
            });
        });
    }

    function addAssetToTimeline(asset, trackId, start) {
        let finalTrackId = trackId || state.tracks[0]?.id;

        if (asset.type === "audio") {
            finalTrackId = state.tracks.find(t => t.type === "audio")?.id || finalTrackId;
        }

        const targetTrack = getTrack(finalTrackId);
        if (targetTrack && targetTrack.locked) {
            alert("잠긴 트랙에는 클립을 추가할 수 없습니다.");
            return;
        }

        const clip = normalizeClip({
            id: uid(),
            assetId: asset.id,
            type: asset.type,
            name: asset.name,
            url: asset.url,
            trackId: finalTrackId,
            start: snapTime(start ?? nextStart(finalTrackId)),
            duration: asset.type === "image" ? 5 : 5,
            speed: 1,
            volume: 1,
            transition: "none",
            text: "",
            sourceOffset: 0,
        });

        state.clips.push(clip);
        renderAll();
        selectClip(clip.id, { jump: true });
    }

    function addTextClip() {
        const overlayTracks = state.tracks.filter(t => t.type === "overlay" && !t.locked);
        const overlayTrack = overlayTracks[overlayTracks.length - 1] || state.tracks[1] || state.tracks[0];

        const clip = normalizeClip({
            id: uid(),
            type: "text",
            name: "텍스트",
            trackId: overlayTrack.id,
            start: state.currentTime || 0,
            duration: 3,
            speed: 1,
            volume: 1,
            transition: "none",
            text: "새 텍스트",
        });

        state.clips.push(clip);
        renderAll();
        selectClip(clip.id, { jump: true });
    }

    function addVoiceClip() {
        const ttsText = $("mcTtsText");
        const text = ttsText ? ttsText.value.trim() : "";

        if (!text) {
            alert("음성으로 만들 글을 먼저 입력하세요.");
            return;
        }

        const audioTrack = state.tracks.find(t => t.type === "audio" && !t.locked) || state.tracks[state.tracks.length - 1];

        const clip = normalizeClip({
            id: uid(),
            type: "voice",
            name: "음성 클립",
            trackId: audioTrack.id,
            start: state.currentTime || 0,
            duration: Math.max(3, Math.round(text.length / 6)),
            speed: 1,
            volume: 1,
            transition: "none",
            text: text,
        });

        state.clips.push(clip);
        renderAll();
        selectClip(clip.id, { jump: true });
    }

    function clipTypeClass(type) {
        if (type === "video") return "mc-clip-video";
        if (type === "image") return "mc-clip-image";
        if (type === "text") return "mc-clip-text";
        if (type === "audio") return "mc-clip-audio";
        if (type === "voice") return "mc-clip-voice";
        return "";
    }

    function renderRuler(width) {
        const marks = [];
        const duration = totalDuration();

        for (let i = 0; i <= duration; i++) {
            const major = i % 5 === 0 ? "major" : "";
            marks.push(`
                <div class="mc-ruler-mark ${major}" style="left:${i * state.pxPerSec}px;">
                    ${i % 2 === 0 ? `<span>${formatTime(i)}</span>` : ""}
                </div>
            `);
        }

        return `
            <div class="mc-ruler-row">
                <div class="mc-track-label">시간</div>
                <div class="mc-ruler-lane" style="width:${width}px;">
                    ${marks.join("")}
                </div>
            </div>
        `;
    }

    function renderTimeline() {
        if (!timeline) return;

        normalizeState();

        const width = timelineWidth();
        const rows = state.tracks.map((track, index) => {
            const clipsHtml = state.clips
                .filter(clip => clip.trackId === track.id)
                .sort((a, b) => Number(a.start || 0) - Number(b.start || 0))
                .map((clip) => {
                    const isSelected = clip.id === state.selectedClipId ? "selected" : "";
                    const left = Number(clip.start || 0) * state.pxPerSec;
                    const clipWidth = Math.max(52, Number(clip.duration || 1) * state.pxPerSec);
                    const label = clip.type === "text" ? "TEXT" : clip.type === "voice" ? "VOICE" : clip.type.toUpperCase();

                    return `
                        <div class="mc-clip ${isSelected} ${clipTypeClass(clip.type)}"
                             data-id="${clip.id}"
                             style="left:${left}px; width:${clipWidth}px;">
                            <strong>${escapeHtml(clip.name)}</strong>
                            <span>${formatTime(clip.start)} · ${Number(clip.duration || 0).toFixed(1)}s</span>
                            <em>${label} · ${clip.speed || 1}x</em>
                        </div>
                    `;
                })
                .join("");

            const rowCls = [
                track.muted ? "is-muted" : "",
                track.locked ? "is-locked" : "",
                track.hidden ? "is-hidden" : "",
            ].filter(Boolean).join(" ");

            return `
                <div class="mc-track-row ${rowCls}" data-track-id="${track.id}">
                    <div class="mc-track-label">
                        <div class="mc-track-name">${escapeHtml(track.name)}</div>
                        <div class="mc-track-actions">
                            <button type="button" class="${track.hidden ? "is-on" : ""}" data-track-action="hidden" data-track-id="${track.id}" title="트랙 숨김">👁</button>
                            <button type="button" class="${track.locked ? "is-on" : ""}" data-track-action="locked" data-track-id="${track.id}" title="트랙 잠금">🔒</button>
                            <button type="button" class="${track.muted ? "is-on" : ""}" data-track-action="muted" data-track-id="${track.id}" title="트랙 음소거">🔇</button>
                            <span class="mc-track-add-mini" data-add-after="${index}" title="아래에 오버레이 트랙 추가">＋</span>
                        </div>
                    </div>
                    <div class="mc-track-lane" data-track-id="${track.id}" style="width:${width}px;">
                        ${clipsHtml}
                    </div>
                </div>
            `;
        }).join("");

        const oldScrollLeft = timeline.scrollLeft;

        timeline.innerHTML = `
            <div class="mc-timeline-inner" style="width:${LABEL_WIDTH + width}px;">
                ${renderRuler(width)}
                <div class="mc-playhead" title="드래그해서 재생 위치 이동"></div>
                ${rows}
            </div>
        `;

        timeline.scrollLeft = oldScrollLeft;

        bindTimelineEvents();
        updatePlayheadPosition();
    }

    function bindTimelineEvents() {
        document.querySelectorAll(".mc-track-lane").forEach(lane => {
            lane.addEventListener("dragover", (e) => {
                e.preventDefault();

                const track = getTrack(lane.dataset.trackId);
                if (track && track.locked) return;

                lane.classList.add("drag-over");
            });

            lane.addEventListener("dragleave", () => {
                lane.classList.remove("drag-over");
            });

            lane.addEventListener("drop", (e) => {
                e.preventDefault();
                lane.classList.remove("drag-over");

                const data = e.dataTransfer.getData("text/plain");
                const trackId = lane.dataset.trackId;
                const targetTrack = getTrack(trackId);

                if (targetTrack && targetTrack.locked) {
                    alert("잠긴 트랙입니다.");
                    return;
                }

                const start = timeFromLaneEvent(e, lane);

                if (data.startsWith("asset:")) {
                    const assetId = data.split(":")[1];
                    const asset = state.assets.find(a => a.id === assetId);
                    if (asset) addAssetToTimeline(asset, trackId, start);
                }
            });

            lane.addEventListener("click", (e) => {
                if (e.target.closest(".mc-clip")) return;
                pauseTimeline();
                setCurrentTime(timeFromLaneEvent(e, lane), {
                    preview: true,
                    forceSeek: true,
                    keepVisible: false,
                });
            });
        });

        document.querySelectorAll(".mc-clip").forEach(el => {
            el.addEventListener("pointerdown", startClipPointerDrag);
            el.addEventListener("click", (e) => {
                e.stopPropagation();
                selectClip(el.dataset.id, { jump: true });
            });

            el.addEventListener("contextmenu", (e) => {
                e.preventDefault();
                e.stopPropagation();
                selectClip(el.dataset.id, { jump: false });
                showContextMenu(e.clientX, e.clientY);
            });
        });

        document.querySelectorAll(".mc-track-add-mini").forEach(btn => {
            btn.addEventListener("click", (e) => {
                e.stopPropagation();
                const index = Number(btn.dataset.addAfter || 0);
                addOverlayTrackAfter(index);
            });
        });

        document.querySelectorAll("[data-track-action]").forEach(btn => {
            btn.addEventListener("click", (e) => {
                e.stopPropagation();
                const track = getTrack(btn.dataset.trackId);
                const action = btn.dataset.trackAction;

                if (!track || !action) return;

                track[action] = !track[action];
                renderAll();
                previewAtTime(state.currentTime, { forceSeek: true });
            });
        });

        const playhead = document.querySelector(".mc-playhead");
        if (playhead) {
            playhead.addEventListener("pointerdown", (e) => {
                e.preventDefault();
                pauseTimeline();
                playheadDragging = true;
                playhead.setPointerCapture?.(e.pointerId);
                document.body.style.userSelect = "none";
            });
        }

        if (timeline && !timeline.dataset.wheelBound) {
            timeline.dataset.wheelBound = "1";
            timeline.addEventListener("wheel", handleTimelineWheel, { passive: false });
        }
    }

    function trackIdFromClientY(clientY, fallbackTrackId) {
        const rows = Array.from(document.querySelectorAll(".mc-track-row"));

        if (!rows.length) return fallbackTrackId;

        let best = fallbackTrackId;
        let bestDist = Infinity;

        rows.forEach(row => {
            const rect = row.getBoundingClientRect();
            const center = rect.top + rect.height / 2;
            const dist = Math.abs(clientY - center);
            const trackId = row.dataset.trackId;
            const track = getTrack(trackId);

            if (track && track.locked) return;

            if (clientY >= rect.top && clientY <= rect.bottom) {
                best = trackId;
                bestDist = -1;
                return;
            }

            if (dist < bestDist) {
                best = trackId;
                bestDist = dist;
            }
        });

        return best;
    }

    function startClipPointerDrag(e) {
        if (e.button !== 0) return;

        const el = e.currentTarget;
        const clip = state.clips.find(c => c.id === el.dataset.id);
        if (!clip) return;

        const originTrack = getTrack(clip.trackId);

        if (originTrack && originTrack.locked) {
            alert("잠긴 트랙의 클립은 이동할 수 없습니다.");
            return;
        }

        pauseTimeline();
        selectClip(clip.id, { jump: false });

        const startX = e.clientX;
        const startY = e.clientY;
        const originStart = Number(clip.start || 0);
        const originTrackIndex = trackIndex(clip.trackId);

        let moved = false;
        let latestStart = originStart;
        let latestTrackId = clip.trackId;

        el.setPointerCapture?.(e.pointerId);
        el.classList.add("is-dragging");

        function onMove(ev) {
            const dx = ev.clientX - startX;
            const dy = ev.clientY - startY;

            if (Math.abs(dx) > 2 || Math.abs(dy) > 2) {
                moved = true;
            }

            latestStart = snapTime(originStart + dx / state.pxPerSec);
            latestTrackId = trackIdFromClientY(ev.clientY, clip.trackId);

            const targetIndex = trackIndex(latestTrackId);
            const translateX = (latestStart - originStart) * state.pxPerSec;
            const translateY = (targetIndex - originTrackIndex) * TRACK_HEIGHT;

            el.style.transform = `translate(${translateX}px, ${translateY}px)`;
        }

        function onUp(ev) {
            el.releasePointerCapture?.(ev.pointerId);
            el.classList.remove("is-dragging");
            el.style.transform = "";

            el.removeEventListener("pointermove", onMove);
            el.removeEventListener("pointerup", onUp);
            el.removeEventListener("pointercancel", onUp);

            if (moved) {
                const targetTrack = getTrack(latestTrackId);

                if (targetTrack && !targetTrack.locked) {
                    clip.start = latestStart;
                    clip.trackId = latestTrackId;
                    renderAll();
                    selectClip(clip.id, { jump: false });
                    setCurrentTime(clip.start, {
                        preview: true,
                        forceSeek: true,
                        keepVisible: true,
                    });
                }
            }
        }

        el.addEventListener("pointermove", onMove);
        el.addEventListener("pointerup", onUp);
        el.addEventListener("pointercancel", onUp);
    }

    function handleTimelineWheel(e) {
        if (!timeline) return;

        if (e.ctrlKey || e.metaKey) {
            e.preventDefault();

            const oldPx = state.pxPerSec;
            const direction = e.deltaY < 0 ? 1 : -1;
            const factor = direction > 0 ? 1.15 : 0.87;

            const rect = timeline.getBoundingClientRect();
            const mouseX = e.clientX - rect.left + timeline.scrollLeft - LABEL_WIDTH;
            const focusTime = Math.max(0, mouseX / oldPx);

            state.pxPerSec = Math.max(MIN_PX_PER_SEC, Math.min(MAX_PX_PER_SEC, state.pxPerSec * factor));

            renderTimeline();

            const newMouseX = focusTime * state.pxPerSec + LABEL_WIDTH;
            timeline.scrollLeft = Math.max(0, newMouseX - (e.clientX - rect.left));
            updatePlayheadPosition();
            return;
        }

        if (Math.abs(e.deltaY) > Math.abs(e.deltaX)) {
            e.preventDefault();
            timeline.scrollLeft += e.deltaY;
        }
    }

    document.addEventListener("pointermove", (e) => {
        if (!playheadDragging) return;

        const lane = getTimelineLane();
        if (!lane) return;

        const sec = timeFromLaneEvent(e, lane);
        setCurrentTime(sec, {
            preview: true,
            forceSeek: true,
            keepVisible: true,
        });
    });

    document.addEventListener("pointerup", () => {
        if (!playheadDragging) return;
        playheadDragging = false;
        document.body.style.userSelect = "";
    });

    function selectClip(id, options = {}) {
        const jump = options.jump !== false;

        state.selectedClipId = id;
        updateSelectionClasses();
        renderProps();

        const clip = selectedClip();

        if (clip && jump) {
            setCurrentTime(Number(clip.start || 0), {
                preview: true,
                forceSeek: true,
                keepVisible: true,
            });
        } else {
            previewAtTime(state.currentTime, { forceSeek: true });
        }
    }

    function renderProps() {
        const clip = selectedClip();

        if (!clip) {
            if (props) props.style.display = "none";
            if (noSelection) noSelection.style.display = "block";
            return;
        }

        if (props) props.style.display = "flex";
        if (noSelection) noSelection.style.display = "none";

        propName.value = clip.name || "";
        propStart.value = clip.start ?? 0;
        propDuration.value = clip.duration ?? 4;
        propSpeed.value = String(clip.speed || 1);
        propVolume.value = clip.volume ?? 1;
        propTransition.value = clip.transition || "none";
        propText.value = clip.text || "";

        if (textPropBox) {
            textPropBox.style.display = (clip.type === "text" || clip.type === "voice") ? "flex" : "none";
        }
    }

    function updateSelectedFromProps() {
        const clip = selectedClip();
        if (!clip) return;

        clip.name = propName.value;
        clip.start = Number(propStart.value || 0);
        clip.duration = Math.max(0.3, Number(propDuration.value || 1));
        clip.speed = Number(propSpeed.value || 1);
        clip.volume = Number(propVolume.value ?? 1);
        clip.transition = propTransition.value || "none";
        clip.text = propText.value || "";

        state.currentTime = clip.start;

        renderAll();
        selectClip(clip.id, { jump: false });
        previewAtTime(state.currentTime, { forceSeek: true });
    }

    function hidePreviewLayers() {
        previewVideo.style.display = "none";
        previewImage.style.display = "none";
        previewText.style.display = "none";
        previewPlaceholder.style.display = "grid";
        previewText.innerHTML = "";
    }

    function activeClipsAt(time) {
        return state.clips
            .filter(clip => {
                const track = getTrack(clip.trackId);

                if (track && track.hidden) return false;

                const s = Number(clip.start || 0);
                const e = s + Number(clip.duration || 0);
                return time >= s && time <= e;
            })
            .sort((a, b) => trackIndex(a.trackId) - trackIndex(b.trackId));
    }

    function previewAtTime(time, options = {}) {
        const forceSeek = Boolean(options.forceSeek);
        const fromPlayback = Boolean(options.fromPlayback);

        hidePreviewLayers();

        const active = activeClipsAt(time);

        if (!active.length) {
            previewPlaceholder.textContent = `${formatTime(time)} 위치에 클립이 없습니다`;
            try { previewVideo.pause(); } catch (e) {}
            return;
        }

        previewPlaceholder.style.display = "none";

        const mediaClips = active.filter(c => c.type === "video" || c.type === "image");
        const textClips = active.filter(c => c.type === "text" || c.type === "voice");

        const mediaClip = mediaClips.length ? mediaClips[mediaClips.length - 1] : null;

        if (mediaClip && mediaClip.type === "video" && mediaClip.url) {
            const track = getTrack(mediaClip.trackId);
            const trackMuted = track ? track.muted : false;

            previewVideo.muted = Boolean(trackMuted || mediaClip.muted);
            previewVideo.volume = Number(mediaClip.volume ?? 1);

            const isNewVideo = previewCache.mediaClipId !== mediaClip.id || previewCache.mediaUrl !== mediaClip.url;

            if (isNewVideo) {
                previewVideo.src = mediaClip.url;
                previewCache.mediaClipId = mediaClip.id;
                previewCache.mediaUrl = mediaClip.url;
                previewCache.lastVideoSeek = -999;
            }

            previewVideo.playbackRate = Number(mediaClip.speed || 1);
            previewVideo.style.display = "block";

            const localTime = Math.max(
                0,
                Number(mediaClip.sourceOffset || 0) + (time - Number(mediaClip.start || 0)) * Number(mediaClip.speed || 1)
            );

            const shouldSeek =
                forceSeek ||
                isNewVideo ||
                !fromPlayback ||
                Math.abs((previewVideo.currentTime || 0) - localTime) > 0.45;

            const seekAndMaybePlay = () => {
                if (shouldSeek) {
                    try {
                        if (Number.isFinite(previewVideo.duration)) {
                            previewVideo.currentTime = Math.min(localTime, Math.max(0, previewVideo.duration - 0.05));
                        } else {
                            previewVideo.currentTime = localTime;
                        }
                        previewCache.lastVideoSeek = localTime;
                    } catch (e) {}
                }

                if (state.isPlaying) {
                    previewVideo.play().catch(() => {});
                } else {
                    previewVideo.pause();
                }
            };

            if (previewVideo.readyState >= 1) {
                seekAndMaybePlay();
            } else {
                previewVideo.onloadedmetadata = seekAndMaybePlay;
            }
        } else if (mediaClip && mediaClip.type === "image" && mediaClip.url) {
            try { previewVideo.pause(); } catch (e) {}

            if (previewImage.getAttribute("src") !== mediaClip.url) {
                previewImage.src = mediaClip.url;
            }

            previewImage.style.display = "block";
            previewCache.mediaClipId = mediaClip.id;
            previewCache.mediaUrl = mediaClip.url;
        } else {
            try { previewVideo.pause(); } catch (e) {}
            previewCache.mediaClipId = null;
            previewCache.mediaUrl = null;
        }

        if (textClips.length) {
            previewText.innerHTML = textClips.map(clip => {
                const track = getTrack(clip.trackId);

                if (track && track.hidden) return "";

                const label = clip.type === "voice"
                    ? "�� " + (clip.text || "음성 클립")
                    : (clip.text || "텍스트");

                return `<div class="mc-preview-text-line">${escapeHtml(label)}</div>`;
            }).join("");

            previewText.style.display = "block";
        }

        if (!mediaClip && !textClips.length) {
            previewPlaceholder.style.display = "grid";
            previewPlaceholder.textContent = `${formatTime(time)} 위치에 미리볼 클립이 없습니다`;
        }
    }

    function previewSelected() {
        const clip = selectedClip();

        if (!clip) {
            previewAtTime(state.currentTime, { forceSeek: true });
            return;
        }

        pauseTimeline();
        setCurrentTime(Number(clip.start || 0), {
            preview: true,
            forceSeek: true,
            keepVisible: true,
        });
    }

    function playTimeline() {
        if (state.isPlaying) {
            pauseTimeline();
            return;
        }

        if (state.currentTime >= totalDuration() - 0.05) {
            state.currentTime = 0;
        }

        state.isPlaying = true;
        state.playStartedAt = performance.now();
        state.playBaseTime = state.currentTime;

        updatePlayheadPosition();
        previewAtTime(state.currentTime, {
            forceSeek: true,
            fromPlayback: true,
        });

        loopPlayback();
    }

    function loopPlayback() {
        if (!state.isPlaying) return;

        const elapsed = (performance.now() - state.playStartedAt) / 1000;
        const next = state.playBaseTime + elapsed;

        if (next >= totalDuration()) {
            setCurrentTime(totalDuration(), {
                preview: true,
                forceSeek: false,
                keepVisible: true,
                fromPlayback: true,
            });
            pauseTimeline();
            return;
        }

        setCurrentTime(next, {
            preview: true,
            forceSeek: false,
            keepVisible: true,
            fromPlayback: true,
        });

        state.animationId = requestAnimationFrame(loopPlayback);
    }

    function pauseTimeline() {
        state.isPlaying = false;

        if (state.animationId) {
            cancelAnimationFrame(state.animationId);
            state.animationId = null;
        }

        try { previewVideo.pause(); } catch (e) {}

        updatePlayheadPosition();
    }

    function stopTimeline() {
        pauseTimeline();
        setCurrentTime(0, {
            preview: true,
            forceSeek: true,
            keepVisible: true,
        });
    }

    function copySelected() {
        const clip = selectedClip();
        if (!clip) return;
        state.copiedClip = JSON.parse(JSON.stringify(clip));
    }

    function cutSelected() {
        const clip = selectedClip();
        if (!clip) return;
        copySelected();
        deleteSelected();
    }

    function pasteClip() {
        if (!state.copiedClip) {
            alert("복사된 클립이 없습니다.");
            return;
        }

        const base = selectedClip();
        const newClip = JSON.parse(JSON.stringify(state.copiedClip));
        newClip.id = uid();
        newClip.name = (newClip.name || "클립") + " 복사본";

        if (base) {
            newClip.trackId = base.trackId;
            newClip.start = Number(base.start || 0) + Number(base.duration || 0);
        } else {
            newClip.trackId = state.tracks[0]?.id;
            newClip.start = state.currentTime || 0;
        }

        state.clips.push(normalizeClip(newClip));
        renderAll();
        selectClip(newClip.id, { jump: true });
    }

    function deleteSelected() {
        const clip = selectedClip();
        if (!clip) return;

        state.clips = state.clips.filter(c => c.id !== clip.id);
        state.selectedClipId = state.clips[0]?.id || null;

        renderAll();
        previewAtTime(state.currentTime, { forceSeek: true });
    }

    function splitSelected() {
        const clip = selectedClip();
        if (!clip) return;

        const s = Number(clip.start || 0);
        const e = s + Number(clip.duration || 0);
        const t = clamp(state.currentTime, s + 0.3, e - 0.3);

        if (t <= s || t >= e) {
            alert("재생바를 클립 안쪽으로 옮긴 뒤 분할하세요.");
            return;
        }

        const firstDuration = Math.round((t - s) * 10) / 10;
        const secondDuration = Math.round((e - t) * 10) / 10;

        const newClip = JSON.parse(JSON.stringify(clip));
        newClip.id = uid();
        newClip.name = (newClip.name || "클립") + " 분할";
        newClip.start = t;
        newClip.duration = Math.max(0.3, secondDuration);
        newClip.sourceOffset = Number(clip.sourceOffset || 0) + firstDuration * Number(clip.speed || 1);

        clip.duration = Math.max(0.3, firstDuration);

        state.clips.push(normalizeClip(newClip));
        renderAll();
        selectClip(newClip.id, { jump: false });
        setCurrentTime(t, {
            preview: true,
            forceSeek: true,
            keepVisible: true,
        });
    }

    function deleteLeftOfSelected() {
        const clip = selectedClip();
        if (!clip) return;

        const s = Number(clip.start || 0);
        const e = s + Number(clip.duration || 0);
        const t = state.currentTime;

        if (t <= s || t >= e) {
            alert("재생바를 클립 안쪽으로 옮긴 뒤 왼쪽 삭제를 누르세요.");
            return;
        }

        const cut = t - s;
        clip.start = snapTime(t);
        clip.duration = Math.max(0.3, e - t);
        clip.sourceOffset = Number(clip.sourceOffset || 0) + cut * Number(clip.speed || 1);

        renderAll();
        selectClip(clip.id, { jump: false });
        setCurrentTime(clip.start, {
            preview: true,
            forceSeek: true,
            keepVisible: true,
        });
    }

    function deleteRightOfSelected() {
        const clip = selectedClip();
        if (!clip) return;

        const s = Number(clip.start || 0);
        const e = s + Number(clip.duration || 0);
        const t = state.currentTime;

        if (t <= s || t >= e) {
            alert("재생바를 클립 안쪽으로 옮긴 뒤 오른쪽 삭제를 누르세요.");
            return;
        }

        clip.duration = Math.max(0.3, t - s);

        renderAll();
        selectClip(clip.id, { jump: false });
        setCurrentTime(t, {
            preview: true,
            forceSeek: true,
            keepVisible: true,
        });
    }

    function toggleSelectedTrackMuted() {
        const clip = selectedClip();
        if (!clip) return;

        const track = getTrack(clip.trackId);
        if (!track) return;

        track.muted = !track.muted;
        renderAll();
        previewAtTime(state.currentTime, { forceSeek: true });
    }

    function addTrack(where) {
        const clip = selectedClip();
        let baseIndex = clip ? trackIndex(clip.trackId) : state.tracks.length - 1;

        if (!clip && where === "above") {
            baseIndex = 0;
        }

        const insertIndex = where === "above"
            ? Math.max(0, baseIndex)
            : Math.min(state.tracks.length, baseIndex + 1);

        const newTrack = {
            id: uid(),
            name: `오버레이 ${state.tracks.filter(t => t.type === "overlay").length + 1}`,
            type: "overlay",
            muted: false,
            locked: false,
            hidden: false,
        };

        state.tracks.splice(insertIndex, 0, newTrack);
        renderAll();
    }

    function addOverlayTrackAfter(index) {
        const newTrack = {
            id: uid(),
            name: `오버레이 ${state.tracks.filter(t => t.type === "overlay").length + 1}`,
            type: "overlay",
            muted: false,
            locked: false,
            hidden: false,
        };

        state.tracks.splice(index + 1, 0, newTrack);
        renderAll();
    }

    function showContextMenu(x, y) {
        if (!contextMenu) return;
        contextMenu.style.left = x + "px";
        contextMenu.style.top = y + "px";
        contextMenu.style.display = "block";
    }

    function hideContextMenu() {
        if (!contextMenu) return;
        contextMenu.style.display = "none";
    }

    function speakText(text) {
        if (!text) return;

        if (!("speechSynthesis" in window)) {
            alert("이 브라우저에서는 음성 미리듣기를 지원하지 않습니다.");
            return;
        }

        window.speechSynthesis.cancel();

        const utterance = new SpeechSynthesisUtterance(text);
        utterance.lang = "ko-KR";
        utterance.rate = 1;
        utterance.pitch = 1;

        window.speechSynthesis.speak(utterance);
    }

    async function uploadFiles(files) {
        if (!files || !files.length) return;

        const form = new FormData();
        Array.from(files).forEach(file => {
            form.append("files", file);
        });

        const res = await fetch(urls.upload, {
            method: "POST",
            headers: {
                "X-CSRFToken": getCookie("csrftoken"),
            },
            body: form,
        });

        const data = await res.json();

        if (!data.ok) {
            alert(data.error || "업로드에 실패했습니다.");
            return;
        }

        state.assets.push(...data.files);
        renderAssets();
    }

    async function saveProject(showAlert = true) {
        const title = document.querySelector(".mc-title")?.textContent?.trim() || "치킨바나나컷 프로젝트";

        const payload = {
            post_id: meta.postId,
            project_id: projectId,
            title: title,
            data: {
                assets: state.assets,
                clips: state.clips,
                tracks: state.tracks,
                selectedClipId: state.selectedClipId,
                currentTime: state.currentTime,
                pxPerSec: state.pxPerSec,
            },
        };

        const res = await fetch(urls.save, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-CSRFToken": getCookie("csrftoken"),
            },
            body: JSON.stringify(payload),
        });

        const data = await res.json();

        if (!data.ok) {
            alert(data.error || "저장 실패");
            return false;
        }

        projectId = data.project_id;

        if (showAlert) {
            alert(data.message || "저장되었습니다.");
        }

        return true;
    }

    async function exportVideo() {
        const saved = await saveProject(false);
        if (!saved) return;

        const res = await fetch(urls.export, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-CSRFToken": getCookie("csrftoken"),
            },
            body: JSON.stringify({
                project_id: projectId,
            }),
        });

        const data = await res.json();
        alert(data.message || "영상 저장 준비 완료");
    }

    function renderAll() {
        normalizeState();
        moveTimelineShellToWideRow();
        renderAssets();
        renderTimeline();
        renderProps();
        updateCurrentTimeLabel();
    }

    if (assetInput) {
        assetInput.addEventListener("change", (e) => {
            uploadFiles(e.target.files);
            assetInput.value = "";
        });
    }

    $("mcAddTextBtn")?.addEventListener("click", addTextClip);
    $("mcAddVoiceBtn")?.addEventListener("click", addVoiceClip);
    $("mcAddTrackAboveBtn")?.addEventListener("click", () => addTrack("above"));
    $("mcAddTrackBelowBtn")?.addEventListener("click", () => addTrack("below"));

    playBtn?.addEventListener("pointerdown", (e) => {
        e.preventDefault();
        e.stopPropagation();
    });

    playBtn?.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        playTimeline();
    });

    stopBtn?.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        stopTimeline();
    });

    $("mcSpeakBtn")?.addEventListener("click", () => {
        const text = $("mcTtsText")?.value?.trim() || "";
        if (!text) {
            alert("먼저 읽을 글을 입력하세요.");
            return;
        }
        speakText(text);
    });

    $("mcPlaySelectedBtn")?.addEventListener("click", previewSelected);
    $("mcSaveBtn")?.addEventListener("click", () => saveProject(true));
    $("mcExportBtn")?.addEventListener("click", exportVideo);

    $("mcSplitBtn")?.addEventListener("click", splitSelected);
    $("mcDeleteLeftBtn")?.addEventListener("click", deleteLeftOfSelected);
    $("mcDeleteRightBtn")?.addEventListener("click", deleteRightOfSelected);
    $("mcMuteTrackBtn")?.addEventListener("click", toggleSelectedTrackMuted);

    $("mcCutBtn")?.addEventListener("click", cutSelected);
    $("mcCopyBtn")?.addEventListener("click", copySelected);
    $("mcPasteBtn")?.addEventListener("click", pasteClip);
    $("mcDeleteBtn")?.addEventListener("click", deleteSelected);

    [
        propName,
        propStart,
        propDuration,
        propSpeed,
        propVolume,
        propTransition,
        propText,
    ].forEach(input => {
        if (!input) return;
        input.addEventListener("input", updateSelectedFromProps);
        input.addEventListener("change", updateSelectedFromProps);
    });

    if (contextMenu) {
        contextMenu.addEventListener("click", (e) => {
            const btn = e.target.closest("button");
            if (!btn) return;

            const action = btn.dataset.action || "";

            if (action === "copy") copySelected();
            if (action === "cut") cutSelected();
            if (action === "paste") pasteClip();
            if (action === "delete") deleteSelected();

            if (action.startsWith("speed:")) {
                const clip = selectedClip();
                if (clip) {
                    clip.speed = Number(action.split(":")[1] || 1);
                    renderAll();
                    previewAtTime(state.currentTime, { forceSeek: true });
                }
            }

            hideContextMenu();
        });
    }

    document.addEventListener("click", (e) => {
        if (!e.target.closest("#mcContextMenu")) {
            hideContextMenu();
        }
    });

    document.addEventListener("keydown", (e) => {
        const isInput = ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName);
        if (isInput) return;

        if (e.code === "Space") {
            e.preventDefault();
            playTimeline();
            return;
        }

        if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "b") {
            e.preventDefault();
            splitSelected();
            return;
        }

        if (e.key.toLowerCase() === "q") {
            e.preventDefault();
            deleteLeftOfSelected();
            return;
        }

        if (e.key.toLowerCase() === "w") {
            e.preventDefault();
            deleteRightOfSelected();
            return;
        }

        if (e.key.toLowerCase() === "m") {
            e.preventDefault();
            toggleSelectedTrackMuted();
            return;
        }

        if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "c") {
            e.preventDefault();
            copySelected();
        }

        if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "v") {
            e.preventDefault();
            pasteClip();
        }

        if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "x") {
            e.preventDefault();
            cutSelected();
        }

        if (e.key === "Delete" || e.key === "Backspace") {
            e.preventDefault();
            deleteSelected();
        }
    });

    normalizeState();
    renderAll();
    previewAtTime(state.currentTime, { forceSeek: true });

    if (state.selectedClipId) {
        renderProps();
        updateSelectionClasses();
    }


    // MINI CAPCUT PATCH - SCALE PREVIEW SLOT
    function removeWrongSplitterPatch() {
        document.querySelectorAll(".mc-preview-size-control, #mcPreviewSizeRange, #mcPreviewTimelineSplitter").forEach((el) => {
            const box = el.closest(".mc-preview-size-control") || el;
            box.remove();
        });

        document.querySelectorAll(".mc-preview-resizable-panel").forEach((el) => {
            el.classList.remove("mc-preview-resizable-panel");
            el.style.removeProperty("width");
            el.style.removeProperty("--mc-preview-width");
            el.style.removeProperty("min-width");
            el.style.removeProperty("max-width");
            el.style.removeProperty("resize");
            el.style.removeProperty("overflow");
            el.style.removeProperty("flex");
            el.style.removeProperty("transform");
            el.style.removeProperty("transform-origin");
        });

        document.querySelectorAll(
            ".mc-preview-visual-follow-by-splitter, .mc-preview-visual-scale-by-splitter, .mc-preview-visual-round-by-splitter"
        ).forEach((el) => {
            el.classList.remove("mc-preview-visual-follow-by-splitter");
            el.classList.remove("mc-preview-visual-scale-by-splitter");
            el.classList.remove("mc-preview-visual-round-by-splitter");
            el.style.removeProperty("transform");
            el.style.removeProperty("transform-origin");
        });
    }

    function compactTrackNamesOnlyPatch() {
        const hiddenNames = ["메인 영상", "메인영상", "오버레이 1", "오버레이1", "오버레이 2", "오버레이2", "음성"];

        document.querySelectorAll(".mc-track-label, .mc-lane-label").forEach((box) => {
            Array.from(box.childNodes).forEach((node) => {
                if (node.nodeType === Node.TEXT_NODE) {
                    const text = (node.textContent || "").trim();
                    if (hiddenNames.some((name) => text === name || text.includes(name))) {
                        node.textContent = "";
                    }
                }
            });

            box.querySelectorAll("*").forEach((el) => {
                const text = (el.textContent || "").trim();
                const isButton = el.tagName === "BUTTON" || !!el.closest("button");
                const hasChild = el.children.length > 0;

                if (!isButton && !hasChild && hiddenNames.some((name) => text === name || text.includes(name))) {
                    el.style.display = "none";
                }
            });
        });
    }

    function collectTopPanesForSplitterPatch(splitter) {
        if (!splitter || !splitter.parentElement) return [];

        const parent = splitter.parentElement;
        const splitterRect = splitter.getBoundingClientRect();
        const children = Array.from(parent.children);
        let panes = [];

        for (const child of children) {
            if (child === splitter) break;

            if (
                child.id === "mcMainTimelineFullSplitter" ||
                child.id === "mcPreviewTimelineSplitter" ||
                child.classList.contains("mc-preview-size-control") ||
                child.classList.contains("mc-timeline-shell") ||
                child.tagName === "SCRIPT" ||
                child.tagName === "STYLE"
            ) {
                continue;
            }

            const style = window.getComputedStyle(child);
            if (style.display === "none" || style.visibility === "hidden") continue;

            const rect = child.getBoundingClientRect();
            if (rect.width < 120 || rect.height < 60) continue;

            const nearSplitter = rect.bottom >= splitterRect.top - 110;
            if (!nearSplitter) continue;

            panes.push(child);
        }

        if (panes.length <= 1) {
            let previous = splitter.previousElementSibling;

            while (previous && (
                previous.id === "mcMainTimelineFullSplitter" ||
                previous.id === "mcPreviewTimelineSplitter"
            )) {
                previous = previous.previousElementSibling;
            }

            if (previous) {
                const baseTop = previous.offsetTop;
                const fallback = [];

                for (const child of children) {
                    if (child === splitter) break;
                    if (child.classList.contains("mc-timeline-shell")) continue;

                    const rect = child.getBoundingClientRect();
                    if (rect.width < 120 || rect.height < 60) continue;

                    if (Math.abs(child.offsetTop - baseTop) <= 45) {
                        fallback.push(child);
                    }
                }

                if (fallback.length > panes.length) panes = fallback;
            }
        }

        return panes;
    }

    function findPreviewPartsPatch() {
        const playBtn = document.getElementById("mcTimelinePlayBtn");
        const stopBtn = document.getElementById("mcTimelineStopBtn");
        const controls = (playBtn || stopBtn)?.parentElement;

        if (!controls) return null;

        controls.classList.add("mc-preview-controls-slot-by-splitter");

        let centerPane = controls.closest(".mc-top-pane-resized-by-splitter");

        if (!centerPane) {
            let p = controls.parentElement;

            while (p && p !== document.body) {
                const r = p.getBoundingClientRect();

                if (r.width > 300 && r.height > 160) {
                    centerPane = p;
                }

                if (p.classList.contains("mc-editor") || p.classList.contains("mc-timeline-shell")) {
                    break;
                }

                p = p.parentElement;
            }
        }

        if (!centerPane) return null;

        centerPane.classList.add("mc-center-preview-pane-slot-by-splitter");

        let stage = controls.previousElementSibling;

        if (!stage || stage.getBoundingClientRect().height < 80) {
            const candidates = Array.from(centerPane.children)
                .filter((el) => el !== controls && !el.contains(controls))
                .map((el) => ({ el, rect: el.getBoundingClientRect() }))
                .filter((item) => item.rect.width > 200 && item.rect.height > 80)
                .sort((a, b) => (b.rect.width * b.rect.height) - (a.rect.width * a.rect.height));

            stage = candidates[0]?.el;
        }

        if (!stage) return null;

        stage.classList.add("mc-preview-stage-slot-host");

        let slot = stage.querySelector(":scope > .mc-preview-scale-slot");

        if (!slot) {
            slot = document.createElement("div");
            slot.className = "mc-preview-scale-slot";

            while (stage.firstChild) {
                slot.appendChild(stage.firstChild);
            }

            stage.appendChild(slot);
        }

        return { centerPane, stage, slot, controls };
    }

    function fitPreviewSlotPatch() {
        const parts = findPreviewPartsPatch();
        if (!parts) return;

        const { centerPane, stage, slot, controls } = parts;

        slot.style.transform = "scale(1)";
        slot.style.transformOrigin = "center bottom";

        stage.style.height = "";
        stage.style.maxHeight = "";

        const paneRect = centerPane.getBoundingClientRect();
        const controlsRect = controls.getBoundingClientRect();

        const availableHeight = Math.max(90, paneRect.height - controlsRect.height - 18);
        const availableWidth = Math.max(180, paneRect.width - 36);

        const slotRect = slot.getBoundingClientRect();

        const baseWidth = slot.scrollWidth || slot.offsetWidth || slotRect.width || 300;
        const baseHeight = slot.scrollHeight || slot.offsetHeight || slotRect.height || 420;

        const scaleByHeight = availableHeight / baseHeight;
        const scaleByWidth = availableWidth / baseWidth;

        const scale = Math.max(0.25, Math.min(1, scaleByHeight, scaleByWidth));
        const scaledHeight = Math.ceil(baseHeight * scale);

        slot.style.transform = `scale(${scale})`;

        stage.style.height = `${scaledHeight + 6}px`;
        stage.style.maxHeight = `${availableHeight}px`;
    }

    function bindPreviewSlotSplitterPatch() {
        const shell = document.querySelector(".mc-timeline-shell");
        if (!shell || !shell.parentNode) return;

        removeWrongSplitterPatch();

        let splitter = document.getElementById("mcMainTimelineFullSplitter");

        if (!splitter) {
            splitter = document.createElement("div");
            splitter.id = "mcMainTimelineFullSplitter";
            splitter.title = "위아래로 드래그해서 메인 화면과 타임라인 높이를 조절합니다. 더블클릭하면 기본값으로 돌아갑니다.";
            shell.parentNode.insertBefore(splitter, shell);
        }

        function ensureClassesAndSavedHeight() {
            const panes = collectTopPanesForSplitterPatch(splitter);

            const savedTop = Number(localStorage.getItem("miniCapcutTopPaneHeight") || 0);
            const savedTimeline = Number(localStorage.getItem("miniCapcutBottomTimelineHeight") || 0);

            panes.forEach((pane) => {
                pane.classList.add("mc-top-pane-resized-by-splitter");

                if (savedTop >= 180) {
                    pane.style.setProperty("--mc-top-pane-height", `${savedTop}px`);
                }
            });

            shell.classList.add("mc-timeline-pane-resized-by-splitter");

            if (savedTimeline >= 170) {
                shell.style.setProperty("--mc-timeline-pane-height", `${savedTimeline}px`);
            }

            fitPreviewSlotPatch();

            return panes;
        }

        ensureClassesAndSavedHeight();

        if (splitter.dataset.bound === "1") return;
        splitter.dataset.bound = "1";

        let dragging = false;
        let startY = 0;
        let startTopHeight = 0;
        let startTimelineHeight = 0;
        let activePanes = [];

        function clamp(value, min, max) {
            return Math.max(min, Math.min(max, value));
        }

        function applyHeights(topHeight, timelineHeight, save) {
            activePanes = activePanes.length ? activePanes : collectTopPanesForSplitterPatch(splitter);

            activePanes.forEach((pane) => {
                pane.classList.add("mc-top-pane-resized-by-splitter");
                pane.style.setProperty("--mc-top-pane-height", `${Math.round(topHeight)}px`);
            });

            shell.classList.add("mc-timeline-pane-resized-by-splitter");
            shell.style.setProperty("--mc-timeline-pane-height", `${Math.round(timelineHeight)}px`);

            if (save) {
                localStorage.setItem("miniCapcutTopPaneHeight", String(Math.round(topHeight)));
                localStorage.setItem("miniCapcutBottomTimelineHeight", String(Math.round(timelineHeight)));
            }

            fitPreviewSlotPatch();
            window.dispatchEvent(new Event("resize"));
        }

        splitter.addEventListener("pointerdown", (e) => {
            activePanes = collectTopPanesForSplitterPatch(splitter);
            if (!activePanes.length) return;

            activePanes.forEach((pane) => pane.classList.add("mc-top-pane-resized-by-splitter"));

            fitPreviewSlotPatch();

            dragging = true;
            startY = e.clientY;

            startTopHeight = Math.max(...activePanes.map((pane) => pane.getBoundingClientRect().height || 0), 300);
            startTimelineHeight = shell.getBoundingClientRect().height || 300;

            splitter.setPointerCapture?.(e.pointerId);
            document.body.classList.add("mc-main-timeline-dragging");

            e.preventDefault();
        });

        splitter.addEventListener("pointermove", (e) => {
            if (!dragging) return;

            const diff = e.clientY - startY;
            const total = startTopHeight + startTimelineHeight;

            const minTop = 180;
            const minTimeline = 170;

            let nextTop = startTopHeight + diff;
            nextTop = clamp(nextTop, minTop, total - minTimeline);

            const nextTimeline = total - nextTop;

            applyHeights(nextTop, nextTimeline, true);

            e.preventDefault();
        });

        function endDrag(e) {
            if (!dragging) return;

            dragging = false;
            splitter.releasePointerCapture?.(e.pointerId);
            document.body.classList.remove("mc-main-timeline-dragging");

            fitPreviewSlotPatch();
        }

        splitter.addEventListener("pointerup", endDrag);
        splitter.addEventListener("pointercancel", endDrag);

        splitter.addEventListener("dblclick", () => {
            localStorage.removeItem("miniCapcutTopPaneHeight");
            localStorage.removeItem("miniCapcutBottomTimelineHeight");

            const panes = collectTopPanesForSplitterPatch(splitter);

            panes.forEach((pane) => {
                pane.classList.add("mc-top-pane-resized-by-splitter");
                pane.style.removeProperty("--mc-top-pane-height");
            });

            shell.classList.add("mc-timeline-pane-resized-by-splitter");
            shell.style.removeProperty("--mc-timeline-pane-height");

            activePanes = [];

            setTimeout(() => {
                ensureClassesAndSavedHeight();
                fitPreviewSlotPatch();
            }, 50);

            window.dispatchEvent(new Event("resize"));
        });

        window.addEventListener("resize", () => {
            fitPreviewSlotPatch();
        });
    }

    function initPreviewSlotSplitterPatch() {
        removeWrongSplitterPatch();
        compactTrackNamesOnlyPatch();
        bindPreviewSlotSplitterPatch();
        fitPreviewSlotPatch();

        const timelineEl = document.getElementById("mcTimeline");
        if (timelineEl && !timelineEl.dataset.previewSlotSplitterObserver) {
            timelineEl.dataset.previewSlotSplitterObserver = "1";
            const observer = new MutationObserver(() => compactTrackNamesOnlyPatch());
            observer.observe(timelineEl, { childList: true, subtree: true });
        }
    }

    setTimeout(initPreviewSlotSplitterPatch, 0);
    setTimeout(initPreviewSlotSplitterPatch, 300);
    setTimeout(initPreviewSlotSplitterPatch, 1000);
    setTimeout(fitPreviewSlotPatch, 1500);



    // MINI CAPCUT PATCH - REMOVE PREVIEW ROUND BOX
    function removePreviewRoundBoxPatch() {
        const selectors = [
            ".mc-preview-stage-slot-host",
            ".mc-preview-stage-round-by-splitter",
            ".mc-preview-stage-follow-by-splitter",
            ".mc-preview-stage-fit-by-splitter",
            ".mc-preview-scale-slot",
            ".mc-preview-visual-round-by-splitter",
            ".mc-preview-visual-follow-by-splitter",
            ".mc-preview-visual-scale-by-splitter"
        ];

        document.querySelectorAll(selectors.join(",")).forEach((root) => {
            root.style.borderRadius = "0";
            root.style.boxShadow = "none";

            root.querySelectorAll("*").forEach((el) => {
                const style = window.getComputedStyle(el);

                const radius = Math.max(
                    parseFloat(style.borderTopLeftRadius) || 0,
                    parseFloat(style.borderTopRightRadius) || 0,
                    parseFloat(style.borderBottomLeftRadius) || 0,
                    parseFloat(style.borderBottomRightRadius) || 0
                );

                const hasShadow = style.boxShadow && style.boxShadow !== "none";

                if (radius > 0 || hasShadow) {
                    el.style.borderRadius = "0";
                    el.style.boxShadow = "none";
                }
            });
        });
    }

    setTimeout(removePreviewRoundBoxPatch, 0);
    setTimeout(removePreviewRoundBoxPatch, 300);
    setTimeout(removePreviewRoundBoxPatch, 1000);
    window.addEventListener("resize", removePreviewRoundBoxPatch);



    // MINI CAPCUT PATCH - TTS VOICE STEP 1
    function findTextVoiceBoxPatch() {
        const textareas = Array.from(document.querySelectorAll("textarea"));

        // placeholder에 음성 관련 문구가 있는 textarea 우선
        let target = textareas.find((el) => {
            const p = (el.getAttribute("placeholder") || "").trim();
            return p.includes("음성") || p.includes("미리 들을");
        });

        // 없으면 텍스트/음성 패널 안 첫 textarea
        if (!target) {
            const heading = Array.from(document.querySelectorAll("*")).find((el) => {
                const t = (el.textContent || "").trim();
                return t === "텍스트 / 음성" || t.includes("텍스트 / 음성");
            });

            if (heading) {
                const panel = heading.closest("div");
                target = panel?.querySelector("textarea") || null;
            }
        }

        return target || null;
    }

    function ensureTtsPanelPatch() {
        if (document.getElementById("mcTtsPanel")) return;

        const textarea = findTextVoiceBoxPatch();
        if (!textarea || !textarea.parentElement) return;

        const panel = document.createElement("div");
        panel.id = "mcTtsPanel";
        panel.className = "mc-tts-panel";
        panel.innerHTML = `
            <div class="mc-tts-panel-title">음성 만들기</div>

            <div class="mc-tts-row">
                <select id="mcTtsVoiceSelect">
                    <option value="">음성 불러오는 중...</option>
                </select>
            </div>

            <div class="mc-tts-row">
                <input id="mcTtsRateInput" type="number" min="0.5" max="2" step="0.1" value="1" title="속도">
                <input id="mcTtsPitchInput" type="number" min="0.5" max="2" step="0.1" value="1" title="톤">
            </div>

            <div class="mc-tts-row">
                <button type="button" class="mc-tts-btn mc-tts-blue" id="mcTtsPreviewBtn">음성 미리듣기</button>
                <button type="button" class="mc-tts-btn" id="mcTtsAddClipBtn">음성 클립 추가</button>
            </div>

            <div class="mc-tts-row">
                <button type="button" class="mc-tts-btn mc-tts-red" id="mcTtsStopBtn">음성 정지</button>
            </div>

            <div class="mc-tts-help">
                현재는 브라우저 기본 음성으로 미리듣기와 타임라인 표시를 먼저 붙인 상태입니다.
                실제 MP4 음성 합성은 다음 단계에서 서버 렌더링으로 연결합니다.
            </div>
        `;

        textarea.parentElement.insertBefore(panel, textarea.nextSibling);
    }

    function loadTtsVoicesPatch() {
        const select = document.getElementById("mcTtsVoiceSelect");
        if (!select || !("speechSynthesis" in window)) return;

        const voices = window.speechSynthesis.getVoices() || [];

        select.innerHTML = "";

        const koVoices = voices.filter((v) => String(v.lang || "").toLowerCase().startsWith("ko"));
        const enVoices = voices.filter((v) => String(v.lang || "").toLowerCase().startsWith("en"));
        const others = voices.filter((v) => !koVoices.includes(v) && !enVoices.includes(v));

        const ordered = [...koVoices, ...enVoices, ...others];

        if (!ordered.length) {
            select.innerHTML = `<option value="">사용 가능한 음성이 없습니다</option>`;
            return;
        }

        ordered.forEach((voice, index) => {
            const opt = document.createElement("option");
            opt.value = voice.name;
            opt.textContent = `${voice.name} (${voice.lang})`;
            if (index === 0) opt.selected = true;
            select.appendChild(opt);
        });
    }

    function getTtsTextPatch() {
        const textarea = findTextVoiceBoxPatch();
        return (textarea?.value || "").trim();
    }

    function speakTtsPatch() {
        if (!("speechSynthesis" in window)) {
            alert("이 브라우저에서는 음성 읽기를 지원하지 않습니다.");
            return;
        }

        const text = getTtsTextPatch();

        if (!text) {
            alert("먼저 음성으로 읽을 글을 입력해 주세요.");
            return;
        }

        window.speechSynthesis.cancel();

        const utter = new SpeechSynthesisUtterance(text);

        const select = document.getElementById("mcTtsVoiceSelect");
        const voiceName = select?.value || "";
        const voices = window.speechSynthesis.getVoices() || [];
        const selectedVoice = voices.find((v) => v.name === voiceName);

        if (selectedVoice) {
            utter.voice = selectedVoice;
            utter.lang = selectedVoice.lang || "ko-KR";
        } else {
            utter.lang = "ko-KR";
        }

        const rate = Number(document.getElementById("mcTtsRateInput")?.value || 1);
        const pitch = Number(document.getElementById("mcTtsPitchInput")?.value || 1);

        utter.rate = Math.max(0.5, Math.min(2, rate));
        utter.pitch = Math.max(0.5, Math.min(2, pitch));

        window.speechSynthesis.speak(utter);
    }

    function stopTtsPatch() {
        if ("speechSynthesis" in window) {
            window.speechSynthesis.cancel();
        }
    }

    function estimateTtsDurationPatch(text) {
        // 한국어 기준 대략 1초에 5~7글자 정도로 잡음
        const len = String(text || "").replace(/\s+/g, "").length;
        return Math.max(2.0, Math.min(30.0, Math.round((len / 5.5) * 10) / 10));
    }

    function findAudioTrackIdPatch() {
        try {
            if (typeof state !== "undefined" && state.tracks && Array.isArray(state.tracks)) {
                const audioTrack = state.tracks.find((t) => {
                    const name = String(t.name || t.label || "").toLowerCase();
                    const kind = String(t.kind || t.type || "").toLowerCase();
                    return name.includes("음성") || name.includes("audio") || kind.includes("audio");
                });

                if (audioTrack?.id) return audioTrack.id;

                const last = state.tracks[state.tracks.length - 1];
                if (last?.id) return last.id;
            }
        } catch (e) {}

        return null;
    }

    function addTtsClipToTimelinePatch() {
        const text = getTtsTextPatch();

        if (!text) {
            alert("먼저 음성으로 넣을 글을 입력해 주세요.");
            return;
        }

        const duration = estimateTtsDurationPatch(text);
        const title = text.length > 18 ? text.slice(0, 18) + "..." : text;

        try {
            if (typeof state !== "undefined" && state.tracks && Array.isArray(state.tracks)) {
                const trackId = findAudioTrackIdPatch();

                const clip = {
                    id: "tts_" + Date.now(),
                    type: "tts",
                    kind: "audio",
                    name: "TTS 음성",
                    title: title,
                    text: text,
                    start: 0,
                    duration: duration,
                    length: duration,
                    muted: false,
                    volume: 1,
                    voiceName: document.getElementById("mcTtsVoiceSelect")?.value || "",
                    rate: Number(document.getElementById("mcTtsRateInput")?.value || 1),
                    pitch: Number(document.getElementById("mcTtsPitchInput")?.value || 1)
                };

                const track = state.tracks.find((t) => t.id === trackId) || state.tracks[state.tracks.length - 1];

                if (track) {
                    if (!Array.isArray(track.clips)) track.clips = [];
                    if (!Array.isArray(track.items)) track.items = track.clips;

                    const existing = track.clips || track.items || [];

                    let maxEnd = 0;
                    existing.forEach((c) => {
                        const s = Number(c.start || 0);
                        const d = Number(c.duration || c.length || 2);
                        maxEnd = Math.max(maxEnd, s + d);
                    });

                    clip.start = Math.round(maxEnd * 10) / 10;

                    if (Array.isArray(track.clips)) track.clips.push(clip);
                    else if (Array.isArray(track.items)) track.items.push(clip);

                    if (typeof renderTimeline === "function") {
                        renderTimeline();
                    }

                    if (typeof saveProjectDebounced === "function") {
                        saveProjectDebounced();
                    }

                    alert("음성 클립을 타임라인에 추가했습니다.");
                    return;
                }
            }
        } catch (e) {
            console.warn("TTS clip state insert failed:", e);
        }

        // state 구조를 못 찾는 경우 화면에 임시 표시
        const timeline = document.getElementById("mcTimeline");
        if (timeline) {
            const temp = document.createElement("div");
            temp.className = "mc-tts-clip-badge";
            temp.textContent = "🔊 TTS 음성: " + title;
            temp.style.position = "absolute";
            temp.style.left = "150px";
            temp.style.top = "20px";
            temp.style.zIndex = "50";
            timeline.appendChild(temp);
            alert("음성 클립 임시 표시를 추가했습니다. 다음 단계에서 프로젝트 저장 구조에 정확히 연결하겠습니다.");
        }
    }

    function bindTtsButtonsPatch() {
        ensureTtsPanelPatch();
        loadTtsVoicesPatch();

        const previewBtn = document.getElementById("mcTtsPreviewBtn");
        const addBtn = document.getElementById("mcTtsAddClipBtn");
        const stopBtn = document.getElementById("mcTtsStopBtn");

        if (previewBtn && previewBtn.dataset.bound !== "1") {
            previewBtn.dataset.bound = "1";
            previewBtn.addEventListener("click", speakTtsPatch);
        }

        if (addBtn && addBtn.dataset.bound !== "1") {
            addBtn.dataset.bound = "1";
            addBtn.addEventListener("click", addTtsClipToTimelinePatch);
        }

        if (stopBtn && stopBtn.dataset.bound !== "1") {
            stopBtn.dataset.bound = "1";
            stopBtn.addEventListener("click", stopTtsPatch);
        }
    }

    function initTtsVoiceStep1Patch() {
        bindTtsButtonsPatch();

        if ("speechSynthesis" in window) {
            window.speechSynthesis.onvoiceschanged = () => {
                loadTtsVoicesPatch();
            };
        }
    }

    setTimeout(initTtsVoiceStep1Patch, 0);
    setTimeout(initTtsVoiceStep1Patch, 300);
    setTimeout(initTtsVoiceStep1Patch, 1000);



    // MINI CAPCUT PATCH - KOREAN MALE TTS PRESETS
    function ensureKoreanMalePresetPatch() {
        const panel = document.getElementById("mcTtsPanel");
        if (!panel) return;

        if (document.getElementById("mcTtsMalePresetSelect")) return;

        const voiceSelect = document.getElementById("mcTtsVoiceSelect");
        const insertAfter = voiceSelect?.closest(".mc-tts-row") || panel.querySelector(".mc-tts-panel-title");

        const box = document.createElement("div");
        box.className = "mc-tts-male-preset-box";
        box.innerHTML = `
            <select id="mcTtsMalePresetSelect">
                <option value="auto">자동 선택</option>
                <option value="ko_male_1">한국 남자 1 - 차분한톤</option>
                <option value="ko_male_2">한국 남자 2 - 뉴스톤</option>
                <option value="ko_male_3">한국 남자 3 - 낮은톤</option>
            </select>

            <div class="mc-tts-male-chip-row">
                <button type="button" class="mc-tts-male-chip" data-tts-preset="ko_male_1">남자 1</button>
                <button type="button" class="mc-tts-male-chip" data-tts-preset="ko_male_2">남자 2</button>
                <button type="button" class="mc-tts-male-chip" data-tts-preset="ko_male_3">남자 3</button>
            </div>
        `;

        insertAfter?.insertAdjacentElement("afterend", box);

        box.querySelectorAll("[data-tts-preset]").forEach((btn) => {
            btn.addEventListener("click", () => {
                const select = document.getElementById("mcTtsMalePresetSelect");
                if (select) select.value = btn.dataset.ttsPreset || "auto";
                applyKoreanMalePresetPatch();
            });
        });

        document.getElementById("mcTtsMalePresetSelect")?.addEventListener("change", applyKoreanMalePresetPatch);
    }

    function getKoreanMalePresetPatch() {
        return document.getElementById("mcTtsMalePresetSelect")?.value || "auto";
    }

    function findBestKoreanMaleVoicePatch() {
        if (!("speechSynthesis" in window)) return null;

        const voices = window.speechSynthesis.getVoices() || [];
        const koVoices = voices.filter((v) => String(v.lang || "").toLowerCase().startsWith("ko"));

        if (!koVoices.length) return null;

        const maleHints = [
            "injoon",
            "in joon",
            "in-joon",
            "hyunsu",
            "hyun su",
            "hyun-su",
            "male",
            "남성",
            "남자",
            "microsoft injoon",
            "google 한국의 남성"
        ];

        const maleVoice = koVoices.find((v) => {
            const name = String(v.name || "").toLowerCase();
            return maleHints.some((hint) => name.includes(hint));
        });

        return maleVoice || koVoices[0];
    }

    function applyKoreanMalePresetPatch() {
        const preset = getKoreanMalePresetPatch();

        const rateInput = document.getElementById("mcTtsRateInput");
        const pitchInput = document.getElementById("mcTtsPitchInput");
        const voiceSelect = document.getElementById("mcTtsVoiceSelect");

        const bestMaleVoice = findBestKoreanMaleVoicePatch();

        if (bestMaleVoice && voiceSelect) {
            const exists = Array.from(voiceSelect.options).some((opt) => opt.value === bestMaleVoice.name);

            if (!exists) {
                const opt = document.createElement("option");
                opt.value = bestMaleVoice.name;
                opt.textContent = `${bestMaleVoice.name} (${bestMaleVoice.lang})`;
                voiceSelect.appendChild(opt);
            }

            voiceSelect.value = bestMaleVoice.name;
        }

        if (preset === "ko_male_1") {
            if (rateInput) rateInput.value = "0.92";
            if (pitchInput) pitchInput.value = "0.78";
        } else if (preset === "ko_male_2") {
            if (rateInput) rateInput.value = "1.02";
            if (pitchInput) pitchInput.value = "0.86";
        } else if (preset === "ko_male_3") {
            if (rateInput) rateInput.value = "0.82";
            if (pitchInput) pitchInput.value = "0.62";
        }
    }

    function speakKoreanMaleTtsPatch() {
        if (!("speechSynthesis" in window)) {
            alert("이 브라우저에서는 음성 읽기를 지원하지 않습니다.");
            return;
        }

        const textarea = findTextVoiceBoxPatch ? findTextVoiceBoxPatch() : null;
        const text = (textarea?.value || "").trim();

        if (!text) {
            alert("먼저 음성으로 읽을 글을 입력해 주세요.");
            return;
        }

        applyKoreanMalePresetPatch();

        window.speechSynthesis.cancel();

        const utter = new SpeechSynthesisUtterance(text);

        const select = document.getElementById("mcTtsVoiceSelect");
        const voiceName = select?.value || "";
        const voices = window.speechSynthesis.getVoices() || [];

        let selectedVoice = voices.find((v) => v.name === voiceName);

        const preset = getKoreanMalePresetPatch();
        if (preset.startsWith("ko_male")) {
            selectedVoice = findBestKoreanMaleVoicePatch() || selectedVoice;
        }

        if (selectedVoice) {
            utter.voice = selectedVoice;
            utter.lang = selectedVoice.lang || "ko-KR";
        } else {
            utter.lang = "ko-KR";
        }

        const rate = Number(document.getElementById("mcTtsRateInput")?.value || 1);
        const pitch = Number(document.getElementById("mcTtsPitchInput")?.value || 1);

        utter.rate = Math.max(0.5, Math.min(2, rate));
        utter.pitch = Math.max(0.5, Math.min(2, pitch));

        window.speechSynthesis.speak(utter);
    }

    function bindKoreanMaleTtsPatch() {
        ensureKoreanMalePresetPatch();
        applyKoreanMalePresetPatch();

        const previewBtn = document.getElementById("mcTtsPreviewBtn");

        if (previewBtn && previewBtn.dataset.koreanMaleBound !== "1") {
            previewBtn.dataset.koreanMaleBound = "1";

            previewBtn.addEventListener("click", (e) => {
                e.preventDefault();
                e.stopImmediatePropagation();
                speakKoreanMaleTtsPatch();
            }, true);
        }

        const addBtn = document.getElementById("mcTtsAddClipBtn");
        if (addBtn && addBtn.dataset.koreanMaleClipBound !== "1") {
            addBtn.dataset.koreanMaleClipBound = "1";
            addBtn.addEventListener("click", () => {
                applyKoreanMalePresetPatch();
            }, true);
        }
    }

    function initKoreanMaleTtsPatch() {
        bindKoreanMaleTtsPatch();

        if ("speechSynthesis" in window) {
            window.speechSynthesis.onvoiceschanged = () => {
                if (typeof loadTtsVoicesPatch === "function") {
                    loadTtsVoicesPatch();
                }
                bindKoreanMaleTtsPatch();
            };
        }
    }

    setTimeout(initKoreanMaleTtsPatch, 0);
    setTimeout(initKoreanMaleTtsPatch, 300);
    setTimeout(initKoreanMaleTtsPatch, 1000);
    setTimeout(initKoreanMaleTtsPatch, 1800);



    // MINI CAPCUT PATCH - AUTO TRANSITION + TOP TRACK PRIORITY
    function getClipListPatch(track) {
        if (!track) return [];
        if (Array.isArray(track.clips)) return track.clips;
        if (Array.isArray(track.items)) return track.items;
        return [];
    }

    function clipStartPatch(clip) {
        return Number(clip?.start ?? clip?.startTime ?? 0) || 0;
    }

    function clipDurationPatch(clip) {
        return Number(clip?.duration ?? clip?.length ?? clip?.endTime ?? 0) || 0;
    }

    function clipEndPatch(clip) {
        return clipStartPatch(clip) + clipDurationPatch(clip);
    }

    function isVisualClipPatch(clip) {
        const type = String(clip?.type || clip?.kind || clip?.mediaType || "").toLowerCase();
        const name = String(clip?.name || clip?.title || "").toLowerCase();
        const url = String(clip?.url || clip?.src || clip?.file || "").toLowerCase();

        return (
            type.includes("video") ||
            type.includes("image") ||
            type.includes("photo") ||
            name.includes("영상") ||
            name.includes("이미지") ||
            url.endsWith(".mp4") ||
            url.endsWith(".mov") ||
            url.endsWith(".webm") ||
            url.endsWith(".png") ||
            url.endsWith(".jpg") ||
            url.endsWith(".jpeg") ||
            url.endsWith(".webp")
        );
    }

    function isAudioClipPatch(clip) {
        const type = String(clip?.type || clip?.kind || clip?.mediaType || "").toLowerCase();
        const name = String(clip?.name || clip?.title || "").toLowerCase();
        const url = String(clip?.url || clip?.src || clip?.file || "").toLowerCase();

        return (
            type.includes("audio") ||
            type.includes("tts") ||
            name.includes("음성") ||
            name.includes("audio") ||
            url.endsWith(".mp3") ||
            url.endsWith(".wav") ||
            url.endsWith(".m4a")
        );
    }

    function normalizeAutoTransitionAndPriorityPatch() {
        try {
            if (typeof state === "undefined" || !state || !Array.isArray(state.tracks)) return false;

            const tracks = state.tracks;
            const totalTracks = tracks.length;
            let changed = false;

            tracks.forEach((track, trackIndex) => {
                /*
                  화면상 위쪽 트랙 우선.
                  state.tracks 배열이 화면 위에서 아래 순서라고 보고,
                  앞에 있는 트랙일수록 높은 priority 부여.
                */
                const priority = totalTracks - trackIndex;
                track.layerPriority = priority;
                track.zIndex = priority;

                const clips = getClipListPatch(track);

                clips.forEach((clip) => {
                    if (!clip) return;

                    if (clip.layerPriority !== priority) {
                        clip.layerPriority = priority;
                        changed = true;
                    }

                    if (clip.zIndex !== priority) {
                        clip.zIndex = priority;
                        changed = true;
                    }

                    if (clip.trackPriority !== priority) {
                        clip.trackPriority = priority;
                        changed = true;
                    }

                    /*
                      전환효과 셀렉트가 이상하더라도
                      시각 클립은 기본 전환값을 fade로 보유.
                    */
                    if (isVisualClipPatch(clip)) {
                        if (!clip.transition || clip.transition === "none" || clip.transition === "없음") {
                            clip.transition = "fade";
                            changed = true;
                        }

                        if (!clip.transitionType || clip.transitionType === "none" || clip.transitionType === "없음") {
                            clip.transitionType = "fade";
                            changed = true;
                        }

                        if (!clip.transitionDuration) {
                            clip.transitionDuration = 0.35;
                            changed = true;
                        }
                    }
                });

                /*
                  같은 트랙 안에서 영상/이미지가 이어지는 경우
                  앞 클립 transitionOut, 뒤 클립 transitionIn 자동 부여.
                */
                const visualClips = clips
                    .filter(isVisualClipPatch)
                    .slice()
                    .sort((a, b) => clipStartPatch(a) - clipStartPatch(b));

                for (let i = 0; i < visualClips.length - 1; i++) {
                    const prev = visualClips[i];
                    const next = visualClips[i + 1];

                    const prevEnd = clipEndPatch(prev);
                    const nextStart = clipStartPatch(next);
                    const gap = Math.abs(nextStart - prevEnd);

                    /*
                      완전히 붙어 있거나 0.5초 이내로 가까우면 자동 전환.
                      영상 합치기 상황에서 바로 전환효과가 들어가게 하는 기준.
                    */
                    if (gap <= 0.5 || nextStart <= prevEnd) {
                        if (!prev.transitionOut || prev.transitionOut === "none" || prev.transitionOut === "없음") {
                            prev.transitionOut = "fade";
                            changed = true;
                        }

                        if (!next.transitionIn || next.transitionIn === "none" || next.transitionIn === "없음") {
                            next.transitionIn = "fade";
                            changed = true;
                        }

                        if (!prev.transitionOutDuration) {
                            prev.transitionOutDuration = 0.35;
                            changed = true;
                        }

                        if (!next.transitionInDuration) {
                            next.transitionInDuration = 0.35;
                            changed = true;
                        }

                        prev.hasAutoTransition = true;
                        next.hasAutoTransition = true;
                    }
                }
            });

            return changed;
        } catch (e) {
            console.warn("auto transition priority patch failed:", e);
            return false;
        }
    }

    function getCurrentPlayheadTimePatch() {
        try {
            if (typeof state !== "undefined") {
                const keys = ["playhead", "currentTime", "time", "previewTime"];
                for (const key of keys) {
                    const value = Number(state[key]);
                    if (Number.isFinite(value)) return value;
                }
            }

            const text = document.querySelector(".mc-time-display")?.textContent || "";
            const match = text.match(/(\d{2}):(\d{2}(?:\.\d+)?)/);
            if (match) {
                return Number(match[1]) * 60 + Number(match[2]);
            }
        } catch (e) {}

        return 0;
    }

    function getTopVisibleVisualClipPatch(time) {
        try {
            if (typeof state === "undefined" || !state || !Array.isArray(state.tracks)) return null;

            /*
              위쪽 트랙 우선:
              배열 앞쪽부터 검사해서 현재 시간에 걸리는 시각 클립을 반환.
            */
            for (const track of state.tracks) {
                if (!track) continue;
                if (track.hidden || track.visible === false) continue;

                const clips = getClipListPatch(track)
                    .filter(isVisualClipPatch)
                    .slice()
                    .sort((a, b) => clipStartPatch(a) - clipStartPatch(b));

                for (const clip of clips) {
                    const s = clipStartPatch(clip);
                    const e = clipEndPatch(clip);
                    if (time >= s && time <= e) {
                        return clip;
                    }
                }
            }
        } catch (e) {}

        return null;
    }

    function applyTopTrackPriorityToPreviewPatch() {
        /*
          기존 preview 렌더링 로직을 직접 갈아엎지는 않고,
          현재 시간 기준 위쪽 트랙 클립을 state에 기록해둠.
          기존 코드가 selected/current clip을 참고하는 경우 이 값으로 우선순위가 잡힘.
        */
        try {
            if (typeof state === "undefined" || !state) return;

            const time = getCurrentPlayheadTimePatch();
            const topClip = getTopVisibleVisualClipPatch(time);

            if (topClip) {
                state.activeVisualClip = topClip;
                state.previewClip = topClip;
                state.currentVisualClip = topClip;
            }
        } catch (e) {}
    }

    function decorateTimelineAutoTransitionPatch() {
        const timeline = document.getElementById("mcTimeline");
        if (!timeline) return;

        /*
          기존 DOM 구조를 모르기 때문에 넓게 잡음.
          data-clip-id가 있으면 clip id와 매칭, 없으면 텍스트 기반으로 최소 표시.
        */
        const clipEls = Array.from(timeline.querySelectorAll("[data-clip-id], .mc-clip, .mc-timeline-clip, .clip"));

        clipEls.forEach((el) => {
            const clipId = el.dataset?.clipId || el.getAttribute("data-id") || "";
            let clip = null;

            try {
                if (typeof state !== "undefined" && state?.tracks) {
                    for (const track of state.tracks) {
                        const found = getClipListPatch(track).find((c) => String(c.id || c.clipId || "") === String(clipId));
                        if (found) {
                            clip = found;
                            break;
                        }
                    }
                }
            } catch (e) {}

            if (clip?.zIndex) {
                el.classList.add("mc-top-priority-clip");
                el.style.setProperty("--mc-clip-z", String(clip.zIndex));
            }

            if (clip?.hasAutoTransition || clip?.transitionIn || clip?.transitionOut || clip?.transition === "fade") {
                el.classList.add("mc-auto-transition-clip");

                if (!el.querySelector(".mc-auto-transition-badge")) {
                    const badge = document.createElement("span");
                    badge.className = "mc-auto-transition-badge";
                    badge.textContent = "자동전환";
                    el.appendChild(badge);
                }
            }
        });
    }

    function setDefaultTransitionSelectPatch() {
        document.querySelectorAll('select[id*="transition"], select[name*="transition"], select[class*="transition"]').forEach((select) => {
            const current = String(select.value || "").toLowerCase();

            if (!current || current === "none" || current === "없음") {
                const options = Array.from(select.options || []);

                const fadeOption = options.find((opt) => {
                    const value = String(opt.value || "").toLowerCase();
                    const text = String(opt.textContent || "").toLowerCase();
                    return value.includes("fade") || text.includes("fade") || text.includes("페이드");
                });

                if (fadeOption) {
                    select.value = fadeOption.value;
                    select.dispatchEvent(new Event("change", { bubbles: true }));
                }
            }
        });
    }

    function runAutoTransitionPriorityPatch() {
        const changed = normalizeAutoTransitionAndPriorityPatch();

        applyTopTrackPriorityToPreviewPatch();
        setDefaultTransitionSelectPatch();

        if (changed) {
            try {
                if (typeof saveProjectDebounced === "function") {
                    saveProjectDebounced();
                }
            } catch (e) {}
        }

        setTimeout(decorateTimelineAutoTransitionPatch, 0);
    }

    /*
      renderTimeline 이후 자동 전환/우선순위 재적용.
    */
    try {
        if (typeof renderTimeline === "function" && !renderTimeline.__autoTransitionPriorityPatched) {
            const originalRenderTimeline = renderTimeline;

            renderTimeline = function(...args) {
                const result = originalRenderTimeline.apply(this, args);
                setTimeout(runAutoTransitionPriorityPatch, 0);
                return result;
            };

            renderTimeline.__autoTransitionPriorityPatched = true;
        }
    } catch (e) {
        console.warn("renderTimeline patch failed:", e);
    }

    /*
      preview 함수가 있으면 실행 전 우선순위 clip을 기록.
    */
    ["renderPreview", "updatePreview", "updatePreviewFrame", "previewAtTime", "renderCurrentFrame"].forEach((fnName) => {
        try {
            if (typeof window[fnName] === "function" && !window[fnName].__topTrackPriorityPatched) {
                const original = window[fnName];

                window[fnName] = function(...args) {
                    applyTopTrackPriorityToPreviewPatch();
                    return original.apply(this, args);
                };

                window[fnName].__topTrackPriorityPatched = true;
            }
        } catch (e) {}
    });

    function initAutoTransitionPriorityPatch() {
        runAutoTransitionPriorityPatch();

        const timeline = document.getElementById("mcTimeline");
        if (timeline && !timeline.dataset.autoTransitionPriorityObserver) {
            timeline.dataset.autoTransitionPriorityObserver = "1";

            const observer = new MutationObserver(() => {
                setTimeout(runAutoTransitionPriorityPatch, 0);
            });

            observer.observe(timeline, {
                childList: true,
                subtree: true
            });
        }
    }

    setTimeout(initAutoTransitionPriorityPatch, 0);
    setTimeout(initAutoTransitionPriorityPatch, 300);
    setTimeout(initAutoTransitionPriorityPatch, 1000);
    setInterval(() => {
        applyTopTrackPriorityToPreviewPatch();
    }, 500);

})();







/* ===== mini capcut text style panel runtime ===== */
(function(){
    function findTextTextarea(){
        const areas = Array.from(document.querySelectorAll("textarea"));
        for(const ta of areas){
            if(ta.closest(".mc-text-edit-wrap")) continue;

            let node = ta.parentElement;
            let depth = 0;

            while(node && depth < 6){
                const txt = (node.innerText || node.textContent || "");
                if(txt.includes("텍스트 내용")){
                    return ta;
                }
                node = node.parentElement;
                depth += 1;
            }
        }
        return null;
    }

    function makePanel(){
        const panel = document.createElement("div");
        panel.className = "mc-text-style-panel";
        panel.innerHTML = `
            <div class="mc-style-row">
                <label>크기</label>
                <input id="mcTextFontSize" type="number" min="10" max="120" value="36">
            </div>

            <div class="mc-style-row">
                <label>글꼴</label>
                <select id="mcTextFontFamily">
                    <option value="system">기본</option>
                    <option value="Pretendard">Pretendard</option>
                    <option value="Noto Sans KR">Noto Sans KR</option>
                    <option value="Arial">Arial</option>
                    <option value="serif">명조체</option>
                    <option value="monospace">고정폭</option>
                </select>
            </div>

            <div class="mc-style-row">
                <label>색상</label>
                <input id="mcTextColor" type="color" value="#ffffff">
            </div>

            <div class="mc-style-row">
                <label>스타일</label>
                <div class="mc-style-inline">
                    <button type="button" id="mcTextBold" class="mc-style-btn">B</button>
                    <button type="button" id="mcTextAlignLeft" class="mc-style-btn">좌</button>
                    <button type="button" id="mcTextAlignCenter" class="mc-style-btn active">중</button>
                </div>
            </div>
        `;
        return panel;
    }

    function injectTextStylePanel(){
        const ta = findTextTextarea();
        if(!ta) return;

        if(ta.closest(".mc-text-edit-wrap")) return;

        const wrap = document.createElement("div");
        wrap.className = "mc-text-edit-wrap";

        const parent = ta.parentNode;
        parent.insertBefore(wrap, ta);
        wrap.appendChild(ta);
        wrap.appendChild(makePanel());

        bindTextStyleEvents(wrap, ta);
    }

    function bindTextStyleEvents(wrap, ta){
        const fontSize = wrap.querySelector("#mcTextFontSize");
        const fontFamily = wrap.querySelector("#mcTextFontFamily");
        const textColor = wrap.querySelector("#mcTextColor");
        const boldBtn = wrap.querySelector("#mcTextBold");
        const alignLeft = wrap.querySelector("#mcTextAlignLeft");
        const alignCenter = wrap.querySelector("#mcTextAlignCenter");

        window.mcCurrentTextStyle = window.mcCurrentTextStyle || {
            fontSize:36,
            fontFamily:"system",
            color:"#ffffff",
            bold:false,
            align:"center"
        };

        function apply(){
            const s = window.mcCurrentTextStyle;

            s.fontSize = parseInt(fontSize.value || "36", 10);
            s.fontFamily = fontFamily.value || "system";
            s.color = textColor.value || "#ffffff";

            ta.style.fontSize = Math.max(12, Math.min(28, s.fontSize * 0.5)) + "px";
            ta.style.color = s.color;
            ta.style.fontWeight = s.bold ? "900" : "400";
            ta.style.textAlign = s.align;

            if(s.fontFamily === "system"){
                ta.style.fontFamily = "-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif";
            }else{
                ta.style.fontFamily = s.fontFamily;
            }

            boldBtn.classList.toggle("active", !!s.bold);
            alignLeft.classList.toggle("active", s.align === "left");
            alignCenter.classList.toggle("active", s.align === "center");

            window.dispatchEvent(new CustomEvent("mc:text-style-change", { detail:s }));
        }

        fontSize.addEventListener("input", apply);
        fontFamily.addEventListener("change", apply);
        textColor.addEventListener("input", apply);

        boldBtn.addEventListener("click", function(){
            window.mcCurrentTextStyle.bold = !window.mcCurrentTextStyle.bold;
            apply();
        });

        alignLeft.addEventListener("click", function(){
            window.mcCurrentTextStyle.align = "left";
            apply();
        });

        alignCenter.addEventListener("click", function(){
            window.mcCurrentTextStyle.align = "center";
            apply();
        });

        apply();
    }

    document.addEventListener("DOMContentLoaded", function(){
        injectTextStylePanel();

        const mo = new MutationObserver(function(){
            injectTextStylePanel();
        });

        mo.observe(document.body, {
            childList:true,
            subtree:true
        });

        window.addEventListener("click", function(){
            setTimeout(injectTextStylePanel, 50);
        });
    });
})();
