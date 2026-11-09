(function () {
  'use strict';

  function csrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute('content') : '';
  }

  // ---- chat -------------------------------------------------------------
  function addChatLine(box, className, text, id) {
    var line = document.createElement('div');
    line.className = className;
    line.textContent = text; // textContent never interprets HTML, so AI output cannot inject markup
    if (id) { line.id = id; }
    box.appendChild(line);
    box.scrollTop = box.scrollHeight;
    return line;
  }

  async function sendChat(meetingId) {
    var input = document.getElementById('q');
    var box = document.getElementById('chat');
    var button = document.getElementById('send');
    if (!input || !box) { return; }
    var question = input.value.trim();
    if (!question) { return; }

    addChatLine(box, 'user', question);
    input.value = '';
    input.disabled = true;
    if (button) { button.disabled = true; }
    var pending = addChatLine(box, 'bot', 'Thinking…', 'think');

    try {
      var response = await fetch('/chat/' + encodeURIComponent(meetingId), {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
        body: JSON.stringify({ message: question })
      });
      var data = {};
      try { data = await response.json(); } catch (parseError) { data = {}; }
      pending.remove();
      if (response.ok && data.answer) {
        addChatLine(box, 'bot', String(data.answer));
      } else {
        addChatLine(box, 'bot', String(data.error || 'The assistant could not answer right now.'));
      }
    } catch (networkError) {
      pending.remove();
      addChatLine(box, 'bot', 'Assistant unavailable. Check your connection and try again.');
    } finally {
      input.disabled = false;
      if (button) { button.disabled = false; }
      input.focus();
    }
  }

  function initChat() {
    var box = document.getElementById('chat');
    var input = document.getElementById('q');
    var button = document.getElementById('send');
    if (!box || !input || !button) { return; }
    box.scrollTop = box.scrollHeight;
    var meetingId = box.getAttribute('data-meeting-id');
    button.addEventListener('click', function () { sendChat(meetingId); });
    input.addEventListener('keydown', function (event) {
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        sendChat(meetingId);
      }
    });
  }

  // ---- new meeting form -------------------------------------------------
  function initMeetingForm() {
    var form = document.getElementById('meeting-form');
    if (!form) { return; }
    var fileInput = form.querySelector('input[type="file"]');
    var dropTitle = form.querySelector('.drop b');
    var maxMb = Number(form.getAttribute('data-max-audio-mb')) || 100;
    var originalTitle = dropTitle ? dropTitle.textContent : '';

    if (fileInput) {
      fileInput.addEventListener('change', function () {
        var file = fileInput.files && fileInput.files[0];
        if (!file) { if (dropTitle) { dropTitle.textContent = originalTitle; } return; }
        if (file.size > maxMb * 1024 * 1024) {
          alert('That file is larger than the ' + maxMb + ' MB limit.');
          fileInput.value = '';
          if (dropTitle) { dropTitle.textContent = originalTitle; }
          return;
        }
        if (dropTitle) { dropTitle.textContent = file.name; }
      });
    }

    form.addEventListener('submit', function () {
      var submit = form.querySelector('button[type="submit"]');
      if (submit && !submit.disabled) {
        submit.disabled = true;
        submit.textContent = (fileInput && fileInput.files && fileInput.files.length)
          ? 'Transcribing and analyzing… this can take a minute'
          : 'Analyzing…';
      }
    });
  }

  // ---- misc -------------------------------------------------------------
  function initAutoSubmitSelects() {
    document.querySelectorAll('select[data-autosubmit]').forEach(function (select) {
      select.addEventListener('change', function () { select.form.submit(); });
    });
  }

  function renderChart(canvasId, config) {
    var canvas = document.getElementById(canvasId);
    if (!canvas) { return null; }
    if (typeof window.Chart === 'undefined') {
      var note = document.createElement('p');
      note.className = 'empty';
      note.textContent = 'The chart could not be loaded.';
      canvas.replaceWith(note);
      return null;
    }
    return new window.Chart(canvas, config);
  }

  document.addEventListener('DOMContentLoaded', function () {
    initChat();
    initMeetingForm();
    initAutoSubmitSelects();
  });

  window.NEXUS = { renderChart: renderChart, sendChat: sendChat };
})();
