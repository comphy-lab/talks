/*
 * Test stand-in for https://www.youtube.com/iframe_api (served by a
 * Playwright route; never deployed). It implements the part of the IFrame
 * Player API the talks player uses and records every call in
 * window.__ytStub, so tests can check what the player asked for without
 * network access.
 */
(function () {
  'use strict';

  var stub = { created: [], calls: [], players: [] };
  window.__ytStub = stub;

  var PlayerState = { UNSTARTED: -1, ENDED: 0, PLAYING: 1, PAUSED: 2, BUFFERING: 3, CUED: 5 };

  function Player(target, options) {
    var self = this;
    var node = typeof target === 'string' ? document.getElementById(target) : target;
    var opts = options || {};
    var vars = opts.playerVars || {};
    var host = opts.host || 'https://www.youtube.com';
    var query = new URLSearchParams();
    Object.keys(vars).forEach(function (k) { query.set(k, String(vars[k])); });
    query.set('enablejsapi', '1');

    var frame = document.createElement('iframe');
    frame.id = node && node.id ? node.id : 'yt-stub-' + stub.players.length;
    frame.src = host + '/embed/' + encodeURIComponent(opts.videoId) + '?' + query.toString();
    frame.setAttribute('allow', 'autoplay; encrypted-media; picture-in-picture');
    frame.width = String(opts.width || 640);
    frame.height = String(opts.height || 360);
    if (node) node.replaceWith(frame);

    var state = PlayerState.UNSTARTED;
    var time = 0;
    var playingSince = null;
    var destroyed = false;
    var events = opts.events || {};

    function now() {
      if (state === PlayerState.PLAYING && playingSince !== null) {
        return time + (performance.now() - playingSince) / 1000;
      }
      return time;
    }
    function record(name, args) {
      stub.calls.push({ player: index, name: name, args: Array.prototype.slice.call(args || []) });
    }
    function setState(next) {
      time = now();
      state = next;
      playingSince = next === PlayerState.PLAYING ? performance.now() : null;
      if (typeof events.onStateChange === 'function') events.onStateChange({ data: next, target: self });
    }

    var index = stub.players.length;
    stub.players.push(self);
    stub.created.push({ host: host, videoId: opts.videoId, playerVars: vars, width: opts.width, height: opts.height });

    self.playVideo = function () { record('playVideo', arguments); setState(PlayerState.PLAYING); };
    self.pauseVideo = function () { record('pauseVideo', arguments); setState(PlayerState.PAUSED); };
    self.seekTo = function (t) { record('seekTo', arguments); time = Number(t); if (playingSince !== null) playingSince = performance.now(); };
    self.mute = function () { record('mute', arguments); self._muted = true; };
    self.unMute = function () { record('unMute', arguments); self._muted = false; };
    self.isMuted = function () { return Boolean(self._muted); };
    self.getPlayerState = function () { return state; };
    self.getCurrentTime = function () { return now(); };
    self.getIframe = function () { return frame; };
    self.destroy = function () { record('destroy', arguments); destroyed = true; frame.remove(); };

    // Test controls: simulate the end of the video, or a jump in time.
    self._fire = function (code) { setState(code); };
    self._setTime = function (t) { time = Number(t); if (playingSince !== null) playingSince = performance.now(); };
    self._destroyed = function () { return destroyed; };

    // Like the real API, report ready only when the embed page says so: a
    // blocked embed host loads an error page instead, and never answers.
    function onMessage(event) {
      if (destroyed || event.source !== frame.contentWindow || event.data !== 'yt-stub-ready') return;
      window.removeEventListener('message', onMessage);
      if (typeof events.onReady === 'function') events.onReady({ target: self });
    }
    window.addEventListener('message', onMessage);
  }

  window.YT = { Player: Player, PlayerState: PlayerState, loaded: 1 };
  setTimeout(function () {
    if (typeof window.onYouTubeIframeAPIReady === 'function') window.onYouTubeIframeAPIReady();
  }, 0);
})();
